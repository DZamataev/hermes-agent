"""`hermes kanban block` run from a session records that session as the actor, so its own block does not wake it."""
import argparse
import contextlib
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


def test_a_block_from_the_chat_slash_command_names_the_chat(card, monkeypatch, telegram_topic):
    """`/kanban block` runs in the gateway process, whose os.environ has no session vars."""
    from hermes_cli.kanban import run_slash
    from hermes_cli.kanban_db_notify import chat_tag
    for var in ("HERMES_SESSION_PLATFORM", "HERMES_SESSION_CHAT_ID", "HERMES_SESSION_THREAD_ID", "HERMES_SESSION_KEY"):
        monkeypatch.delenv(var, raising=False)
    run_slash(f"block {card} holding while closing")
    assert _last_block(card).get("actor_chat") == chat_tag("telegram", "-100G", "7")


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
