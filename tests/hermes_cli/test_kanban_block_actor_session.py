"""`hermes kanban block` run from a session records that session as the actor, so its own block does not wake it."""
import argparse
import json
import contextlib
import contextvars
import io
from pathlib import Path

import pytest

from hermes_cli import kanban as kc
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli.kanban_parser import build_parser


@pytest.fixture
def card(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    kb.init_db()
    with kbc.connect_closing() as conn:
        return kb.create_task(conn, title="fix", assignee="worker")


def _block(tid: str) -> dict:
    parser = argparse.ArgumentParser()
    build_parser(parser.add_subparsers())
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert kc.kanban_command(parser.parse_args(["kanban", "block", tid, "holding while closing"])) == 0
    with kbc.connect_closing() as conn:
        return [e.payload for e in kb.list_events(conn, tid) if e.kind == "blocked"][-1]


def test_a_block_from_a_session_names_it(card, monkeypatch):
    monkeypatch.setenv("HERMES_SESSION_KEY", "20260928_010203_abcdef")
    assert _block(card)["actor_session"] == "20260928_010203_abcdef"


def test_a_block_outside_a_session_names_nobody(card, monkeypatch):
    monkeypatch.delenv("HERMES_SESSION_KEY", raising=False)
    assert "actor_session" not in _block(card)


def test_a_block_from_a_messenger_chat_names_the_chat(card, monkeypatch):
    """A terminal child of a gateway turn carries the chat, not a desktop session key: the gateway matches it."""
    from hermes_cli.kanban_db_notify import chat_tag
    monkeypatch.delenv("HERMES_SESSION_KEY", raising=False)
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_CHAT_ID", "-100123")
    monkeypatch.setenv("HERMES_SESSION_THREAD_ID", "9")
    assert _block(card)["actor_chat"] == chat_tag("telegram", "-100123", "9")


def test_a_block_outside_a_chat_names_no_chat(card, monkeypatch):
    for var in ("HERMES_SESSION_PLATFORM", "HERMES_SESSION_CHAT_ID", "HERMES_SESSION_THREAD_ID"):
        monkeypatch.delenv(var, raising=False)
    assert "actor_chat" not in _block(card)


@pytest.fixture
def telegram_topic():
    """A gateway turn in a Telegram forum topic: the chat lives in the session ContextVars, not in os.environ."""
    from gateway.session_context import clear_session_vars, set_session_vars
    tokens = set_session_vars(platform="telegram", chat_id="-100G", chat_type="group", thread_id="7",
                              session_key="agent:main:telegram:group:-100G:7")
    yield
    clear_session_vars(tokens)


def _last_block(tid: str) -> dict:
    with kbc.connect_closing() as conn:
        return [e.payload for e in kb.list_events(conn, tid) if e.kind == "blocked"][-1]


def test_the_block_tool_of_an_orchestrator_agent_names_its_chat(card, monkeypatch, telegram_topic):
    import tools.kanban_tools as kt
    from hermes_cli.kanban_db_notify import chat_tag
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    kt._handle_block({"task_id": card, "reason": "holding while closing"})
    payload = _last_block(card)
    assert payload.get("actor_chat") == chat_tag("telegram", "-100G", "7")
    assert payload.get("actor_session") == "agent:main:telegram:group:-100G:7"


def test_a_worker_blocking_its_own_card_names_no_actor(card, monkeypatch, telegram_topic):
    """A worker's block is news for the orchestrator: it must never be filtered as the orchestrator's own."""
    import tools.kanban_tools as kt
    with kbc.connect_closing() as conn:
        kb.claim_task(conn, card)
        monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(kb.get_task(conn, card).current_run_id))
    monkeypatch.setenv("HERMES_KANBAN_TASK", card)
    monkeypatch.setattr(kt, "_is_dispatcher_owned_worker", lambda: True)
    kt._handle_block({"task_id": card, "reason": "need credentials"})
    payload = _last_block(card)
    assert "actor_chat" not in payload and "actor_session" not in payload


def test_a_nested_agent_blocking_names_no_actor(card, monkeypatch):
    """`hermes chat -q …` started from a session's terminal inherits that session's HERMES_SESSION_* in its env and
    binds no session of its own. Its block is news for the parent session, which subscribed to the card."""
    import tools.kanban_tools as kt
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.setenv("HERMES_SESSION_KEY", "desktop-session-S")
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_CHAT_ID", "-100G")
    out = json.loads(contextvars.Context().run(kt._handle_block, {"task_id": card, "reason": "need a decision"}))
    assert out.get("ok"), out
    payload = _last_block(card)
    assert "actor_session" not in payload and "actor_chat" not in payload


def test_a_cli_block_whose_writes_straddle_a_second_still_shows_as_stuck(card, monkeypatch):
    """`hermes kanban block <id> <reason>` also records a `BLOCKED: reason` comment. The stuck-in-blocked diagnostic
    reads any comment later than the block as a human response; the block's own reason comment must not count."""
    from hermes_cli import kanban_diagnostics as kd

    clock = iter(range(1_900_000_000, 1_900_000_000 + 10_000))  # every timestamp read is one second later
    monkeypatch.setattr(kb.time, "time", lambda: float(next(clock)))
    _block(card)
    with kbc.connect_closing() as conn:
        task = kb.get_task(conn, card)
        events = kb.list_events(conn, card)
        assert any(c.body == "BLOCKED: holding while closing" for c in kb.list_comments(conn, card))
    stuck = kd._rule_stuck_in_blocked(task, events, [], 1_900_000_000 + 48 * 3600, {})
    assert [d.kind for d in stuck] == ["stuck_in_blocked"]


def test_a_failed_block_records_no_reason_comment(card):
    _block(card)
    parser = argparse.ArgumentParser()
    build_parser(parser.add_subparsers())
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert kc.kanban_command(parser.parse_args(["kanban", "block", card, "again"])) != 0  # already blocked
    with kbc.connect_closing() as conn:
        assert [c.body for c in kb.list_comments(conn, card)] == ["BLOCKED: holding while closing"]
