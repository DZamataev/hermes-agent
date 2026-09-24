"""The dispatcher announces when a board runs out of work (``board_quiescent``).

Card events tell an orchestrator that one card finished or blocked; they never
say "nothing is left to run", so a session supervising a board had to poll it.
A dispatcher tick that finds no ``running``/``ready``/``review`` card after work
ran since the last announcement records ONE ``board_quiescent`` event per
subscribed destination, on a card that destination already follows, so every
existing delivery path (gateway notifier, desktop poller) carries it unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli import kanban_db_notify as kbn


@pytest.fixture
def conn(tmp_path, monkeypatch, all_assignees_spawnable):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    c = kbc.connect()
    try:
        yield c
    finally:
        c.close()


def _tick(conn, **kw):
    return kbd.dispatch_once(conn, spawn_fn=lambda *a, **k: None, **kw)


def _card(conn, title, *, sub=None, parents=()):
    tid = kb.create_task(conn, title=title, assignee="worker", parents=parents)
    if sub:
        kbn.add_notify_sub(conn, task_id=tid, platform=sub[0], chat_id=sub[1])
    return tid


def _quiescent(conn):
    rows = conn.execute(
        "SELECT task_id, payload FROM task_events WHERE kind = 'board_quiescent' ORDER BY id"
    ).fetchall()
    return [(r["task_id"], json.loads(r["payload"] or "{}")) for r in rows]


def test_last_running_card_finishing_announces_the_idle_board_once(conn):
    tid = _card(conn, "only card", sub=("tui", "orchestrator"))
    _tick(conn)
    assert conn.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()[0] == "running"
    assert _quiescent(conn) == []

    kb.complete_task(conn, tid, summary="done")
    _tick(conn)
    _tick(conn)

    events = _quiescent(conn)
    assert len(events) == 1
    task_id, payload = events[0]
    assert task_id == tid
    assert payload["counts"] == {"done": 1}


def test_blocked_leftovers_are_listed_and_the_new_blocked_card_is_named(conn):
    a = _card(conn, "a", sub=("telegram", "chat-1"))
    b = _card(conn, "b", sub=("telegram", "chat-1"))
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    kb.block_task(conn, b, reason="needs a decision", kind="needs_input")
    _tick(conn)

    events = _quiescent(conn)
    assert len(events) == 1, "one destination following two cards gets one announcement"
    _, payload = events[0]
    assert payload["counts"] == {"blocked": 1, "done": 1}
    assert payload["attention"] == [b]


def test_board_with_a_running_card_is_not_idle(conn):
    a = _card(conn, "a", sub=("tui", "orchestrator"))
    _card(conn, "b", sub=("tui", "orchestrator"))
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    _tick(conn)
    assert _quiescent(conn) == []


def test_every_subscribed_destination_is_told_and_unsubscribed_cards_stay_quiet(conn):
    a = _card(conn, "a", sub=("tui", "orchestrator"))
    b = _card(conn, "b", sub=("telegram", "chat-1"))
    c = _card(conn, "c")
    _tick(conn)
    for tid in (a, b, c):
        kb.complete_task(conn, tid, summary="ok")
    _tick(conn)

    assert sorted(t for t, _ in _quiescent(conn)) == sorted([a, b])


def test_new_work_after_an_announcement_rearms_it(conn):
    a = _card(conn, "a", sub=("tui", "orchestrator"))
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    _tick(conn)
    b = _card(conn, "b", sub=("tui", "orchestrator"))
    _tick(conn)
    kb.complete_task(conn, b, summary="ok")
    _tick(conn)

    assert len(_quiescent(conn)) == 2


def test_a_board_that_never_ran_anything_is_not_announced(conn):
    tid = kb.create_task(conn, title="waits on a human", assignee="worker", triage=True)
    kbn.add_notify_sub(conn, task_id=tid, platform="tui", chat_id="orchestrator")
    _tick(conn)
    assert _quiescent(conn) == []


def test_dry_run_tick_writes_nothing(conn):
    a = _card(conn, "a", sub=("tui", "orchestrator"))
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    _tick(conn, dry_run=True)
    assert _quiescent(conn) == []
