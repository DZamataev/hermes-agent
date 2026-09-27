"""``kanban.worker_fallback``: a board worker's model switch is recorded on its run, and a
profile can forbid it so a quota wall requeues the card instead of finishing it on another model.

Real board + real ``config.yaml`` in a temp HERMES_HOME; only the provider client is faked.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.error_classifier import FailoverReason
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from run_agent import AIAgent

_CHAIN = [{"provider": "openai", "model": "gpt-4o"}]


@pytest.fixture
def worker_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="worker", assignee="w")
        kb.claim_task(conn, tid)
        run_id = kb._current_run_id(conn, tid)
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run_id))
    return home, tid


def _set_policy(home: Path, value: str) -> None:
    (home / "config.yaml").write_text(f"kanban:\n  worker_fallback: {value}\n", encoding="utf-8")


def _agent():
    with (
        patch("model_tools.get_tool_definitions", return_value=[]),
        patch("model_tools.check_toolset_requirements", return_value={}),
        patch("agent.process_bootstrap.OpenAI"),
    ):
        agent = AIAgent(api_key="k", base_url="https://openrouter.ai/api/v1", quiet_mode=True,
                        skip_context_files=True, skip_memory=True, fallback_model=list(_CHAIN))
    agent.client = MagicMock()
    return agent


def _activate(agent, reason=FailoverReason.rate_limit) -> bool:
    fb_client = MagicMock()
    fb_client.base_url, fb_client.api_key = "https://api.openai.com/v1", "fb"
    with patch("agent.auxiliary_client.resolve_provider_client", return_value=(fb_client, "gpt-4o")):
        return agent._try_activate_fallback(reason=reason)


def _events(tid: str, kind: str):
    with kbc.connect_closing() as conn:
        return [e for e in kb.list_events(conn, tid) if e.kind == kind]


def test_wait_refuses_the_switch_for_a_board_worker(worker_home):
    home, tid = worker_home
    _set_policy(home, "wait")
    agent = _agent()
    primary = agent.model

    assert _activate(agent) is False
    assert agent.model == primary
    refused = _events(tid, "model_fallback_refused")
    assert len(refused) == 1
    assert refused[0].payload["model"] == primary
    assert refused[0].payload["reason"] == "rate_limit"
    assert _events(tid, "model_fallback") == []


def test_allow_switches_and_records_the_switch(worker_home):
    home, tid = worker_home
    _set_policy(home, "allow")
    agent = _agent()
    primary = agent.model

    assert _activate(agent) is True
    assert agent.model == "gpt-4o"
    switched = _events(tid, "model_fallback")
    assert len(switched) == 1
    assert switched[0].payload["from_model"] == primary
    assert switched[0].payload["to_model"] == "gpt-4o"
    assert _events(tid, "model_fallback_refused") == []


def test_wait_does_not_bind_an_interactive_session(worker_home, monkeypatch):
    home, tid = worker_home
    _set_policy(home, "wait")
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    agent = _agent()

    assert _activate(agent) is True
    assert agent.model == "gpt-4o"
    assert _events(tid, "model_fallback_refused") == []


def test_default_policy_is_allow(worker_home):
    _home, tid = worker_home
    agent = _agent()

    assert _activate(agent) is True
    assert len(_events(tid, "model_fallback")) == 1


class _RateLimitError(Exception):
    status_code = 429

    def __init__(self):
        super().__init__("Error code: 429 - rate limit exceeded")
        self.response = SimpleNamespace(headers={})
        self.body = {"error": {"message": "rate limit exceeded"}}


def test_wait_turns_a_quota_wall_into_the_rate_limit_exit(worker_home):
    """The whole point of ``wait``: a full turn against a walled primary never calls the
    fallback model, and the worker exits with the code the dispatcher requeues without charge."""
    from hermes_cli.cli_single_query import _single_query_exit_code
    from hermes_cli.kanban_db import KANBAN_RATE_LIMIT_EXIT_CODE

    home, tid = worker_home
    _set_policy(home, "wait")
    agent = _agent()
    agent._api_max_retries = 2
    primary = agent.model
    calls = []

    def walled(api_kwargs):
        calls.append(agent.model)
        raise _RateLimitError()

    fb_client = MagicMock()
    fb_client.base_url, fb_client.api_key = "https://api.openai.com/v1", "fb"
    with (
        patch.object(agent, "_interruptible_api_call", side_effect=walled),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
        patch("agent.agent_runtime_helpers.time.sleep"),
        patch("agent.auxiliary_client.resolve_provider_client", return_value=(fb_client, "gpt-4o")) as resolve,
    ):
        result = agent.run_conversation("hello")

    assert calls and set(calls) == {primary}
    resolve.assert_not_called()
    assert result.get("failure_reason") == "rate_limit"
    assert _single_query_exit_code(result) == KANBAN_RATE_LIMIT_EXIT_CODE
    assert len(_events(tid, "model_fallback_refused")) == 1
