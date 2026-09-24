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


def _addressed(conn):
    """{(platform, chat_id): [task_id, ...]} of the announcements written so far."""
    out: dict = {}
    for task_id, payload in _quiescent(conn):
        to = payload["to"]
        out.setdefault((to["platform"], to["chat_id"]), []).append(task_id)
    return out


def test_each_destination_is_addressed_once_even_when_it_shares_a_card(conn):
    """X follows a and b, Y follows only a: every destination gets exactly one announcement, not one per card."""
    a = _card(conn, "a", sub=("telegram", "X"))
    kbn.add_notify_sub(conn, task_id=a, platform="telegram", chat_id="Y")
    b = _card(conn, "b", sub=("telegram", "X"))
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    kb.complete_task(conn, b, summary="ok")
    _tick(conn)

    addressed = _addressed(conn)
    assert sorted(addressed) == [("telegram", "X"), ("telegram", "Y")]
    assert all(len(cards) == 1 for cards in addressed.values())


def test_announcement_lands_on_the_most_recently_active_card_the_destination_follows(conn):
    a = _card(conn, "a", sub=("telegram", "X"))
    b = _card(conn, "b", sub=("telegram", "X"))
    _tick(conn)
    kb.complete_task(conn, a, summary="first")
    kb.complete_task(conn, b, summary="last")
    _tick(conn)

    assert _addressed(conn) == {("telegram", "X"): [b]}


def test_destinations_that_took_no_part_in_the_work_are_not_told(conn):
    """A session that followed a card finished before the previous announcement is not woken by later work."""
    old = _card(conn, "old", sub=("tui", "last-week"))
    _tick(conn)
    kb.complete_task(conn, old, summary="ok")
    _tick(conn)
    assert list(_addressed(conn)) == [("tui", "last-week")]

    new = _card(conn, "new", sub=("tui", "today"))
    _tick(conn)
    kb.complete_task(conn, new, summary="ok")
    _tick(conn)

    assert _addressed(conn) == {("tui", "last-week"): [old], ("tui", "today"): [new]}


def test_archived_cards_never_carry_an_announcement(conn):
    a = _card(conn, "a", sub=("telegram", "X"))
    b = _card(conn, "b", sub=("telegram", "X"))
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    kb.complete_task(conn, b, summary="ok")
    kb.archive_task(conn, b)
    _tick(conn)

    assert _addressed(conn) == {("telegram", "X"): [a]}


def test_event_gc_does_not_rearm_a_finished_announcement(conn):
    a = _card(conn, "a", sub=("tui", "orchestrator"))
    b = _card(conn, "b", sub=("tui", "orchestrator"))
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    kb.block_task(conn, b, reason="needs a human", kind="needs_input")
    _tick(conn)
    assert len(_quiescent(conn)) == 1

    kb.gc_events(conn, older_than_seconds=0)
    _tick(conn)
    _tick(conn)

    assert len(_quiescent(conn)) == 1


def test_event_gc_does_not_rearm_a_finished_announcement_even_when_it_prunes_the_carrier(conn):
    a = _card(conn, "a", sub=("tui", "orchestrator"))
    b = _card(conn, "b", sub=("tui", "orchestrator"))
    _tick(conn)
    kb.block_task(conn, b, reason="needs a human", kind="needs_input")
    kb.complete_task(conn, a, summary="ok")
    _tick(conn)
    assert [t for t, _ in _quiescent(conn)] == [a], "the carrier is the card with the newest event: done card a"

    conn.execute("UPDATE task_events SET created_at = created_at - 3600")
    conn.commit()
    assert kb.gc_events(conn, older_than_seconds=60) > 0
    assert _quiescent(conn) == [], "the announcement on done card a was pruned"
    _tick(conn)
    _tick(conn)

    assert _quiescent(conn) == []


def test_a_subscription_added_after_an_unwatched_drain_is_not_told_about_it(conn):
    a = _card(conn, "a")
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    _tick(conn)
    late = kb.create_task(conn, title="decide later", assignee="worker", triage=True)
    kbn.add_notify_sub(conn, task_id=late, platform="tui", chat_id="orchestrator")
    _tick(conn)

    assert _quiescent(conn) == []


def test_history_from_before_the_feature_is_not_announced(conn):
    """A board upgraded mid-life has old claims but no marker yet; migration seeds it, so the first idle tick is silent."""
    a = _card(conn, "a", sub=("tui", "orchestrator"))
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    conn.execute("DROP TABLE kanban_board_state")
    conn.commit()
    kb.init_db()
    _tick(conn)

    assert _quiescent(conn) == []


def test_review_cards_wait_on_a_human_when_review_dispatch_is_off(conn, monkeypatch):
    import hermes_cli.config as cfgmod
    monkeypatch.setattr(cfgmod, "load_config", lambda *a, **k: {"kanban": {"review_dispatch": False}})
    a = _card(conn, "a", sub=("tui", "orchestrator"))
    _tick(conn)
    assert kb.request_review(conn, a, summary="please look", force=True)
    _tick(conn)

    assert [p["counts"] for _, p in _quiescent(conn)] == [{"review": 1}]
