"""`/kanban block` sent from a chat through the real gateway message path records that chat as the actor, so the
chat is not told about its own block. Built-in slash commands run outside an agent turn, where the session
ContextVars are not bound unless the handler binds them."""
import asyncio
from datetime import datetime

import pytest

from gateway.config import Platform
from gateway.platforms.event import MessageEvent
from gateway.session import SessionEntry, SessionSource, build_session_key
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_notify as kbn
from tests.gateway.test_status_command import _make_runner


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path))
    for var in ("HERMES_KANBAN_BOARD", "HERMES_KANBAN_DB", "HERMES_KANBAN_TASK"):
        monkeypatch.delenv(var, raising=False)
    kb.init_db()
    assert str(kb.kanban_db_path()).startswith(str(tmp_path))


def _topic_source():
    return SessionSource(platform=Platform.TELEGRAM, user_id="u1", chat_id="-100G", user_name="op",
                         chat_type="group", thread_id="7")


def _block_from_chat(monkeypatch, *, process_env: dict) -> tuple:
    for var in ("HERMES_SESSION_PLATFORM", "HERMES_SESSION_CHAT_ID", "HERMES_SESSION_THREAD_ID",
                "HERMES_SESSION_KEY", "HERMES_SESSION_CHAT_TYPE"):
        monkeypatch.delenv(var, raising=False)
    for k, v in process_env.items():
        monkeypatch.setenv(k, v)
    source = _topic_source()
    entry = SessionEntry(session_key=build_session_key(source), session_id="s1", created_at=datetime.now(),
                         updated_at=datetime.now(), platform=Platform.TELEGRAM, chat_type="group")
    runner = _make_runner(entry)
    del runner._set_session_env  # the real binding, not the helper's stub
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="c", assignee="w")
        kbn.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="-100G", chat_type="group",
                           thread_id="7", delivery_mode="notify+wake")
    asyncio.run(runner._handle_message(MessageEvent(text=f"/kanban block {tid} holding while closing",
                                                    source=source, message_id="m1")))
    with kbc.connect_closing() as conn:
        task = kb.get_task(conn, tid)
        event = [e for e in kb.list_events(conn, tid) if e.kind == "blocked"][-1]
        sub = kbn.list_notify_subs(conn, tid)[0]
        told = kbn.relevant_to(kb, conn, event, task, chat=kbn.sub_chat_tag(sub)) is not None
    return event.payload, told


def test_a_block_from_the_chat_is_not_reported_back_to_it(board, monkeypatch):
    payload, told = _block_from_chat(monkeypatch, process_env={})
    assert payload.get("actor_chat") == kbn.chat_tag("telegram", "-100G", "7")
    assert told is False


def test_the_gateway_process_env_does_not_name_another_chat(board, monkeypatch):
    """A gateway started from a Hermes terminal carries that session's HERMES_SESSION_* in os.environ."""
    payload, _ = _block_from_chat(monkeypatch, process_env={"HERMES_SESSION_PLATFORM": "telegram",
                                                            "HERMES_SESSION_CHAT_ID": "999"})
    assert payload.get("actor_chat") == kbn.chat_tag("telegram", "-100G", "7")
