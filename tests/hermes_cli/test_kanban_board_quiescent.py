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
    """{(platform, chat_id[, thread]): [task_id, ...]} of the announcements written so far, resolved from the
    opaque ``to`` tag back through the board's subscriptions (the payload itself never holds a chat id)."""
    tags = {}
    for s in kbn.list_notify_subs(conn):
        key = (s["platform"], s["chat_id"])
        if s.get("thread_id"):
            key += (s["thread_id"],)
        tags[kbn.quiescent_destination_tag(conn, s)] = key
    out: dict = {}
    for task_id, payload in _quiescent(conn):
        out.setdefault(tags.get(payload["to"], ("?", payload["to"])), []).append(task_id)
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


def test_a_participant_whose_cards_are_all_archived_is_still_told(conn):
    """The orchestrator archives its finished card before the next tick: the drain is still announced to it,
    on the archived card, while that card's subscription exists."""
    a = _card(conn, "a", sub=("telegram", "X"))
    b = _card(conn, "b")
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    kb.block_task(conn, b, reason="needs a human", kind="needs_input")
    kb.archive_task(conn, a)
    _tick(conn)

    assert _addressed(conn) == {("telegram", "X"): [a]}


def test_an_archived_participant_is_told_even_after_its_archival_was_delivered(conn):
    """Notifiers poll every few seconds and the dispatcher ticks once a minute: the archival reaches the
    orchestrator first. Its subscription must outlive that delivery until the drain is decided."""
    from tui_gateway.server import _collect_kanban_notifications as poll

    a = _card(conn, "a", sub=("tui", "K"))
    b = _card(conn, "b")
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    kb.block_task(conn, b, reason="needs a human", kind="needs_input")
    kb.archive_task(conn, a)
    assert any(a in t and "done" in t for t in poll({"session_key": "K"}))
    assert len(kbn.list_notify_subs(conn, a)) == 1
    _tick(conn)

    assert _addressed(conn) == {("tui", "K"): [a]}
    shown = poll({"session_key": "K"})
    assert any("no work left" in t for t in shown)
    assert kbn.list_notify_subs(conn, a) == []


def test_an_archived_card_outside_the_decided_work_is_unsubscribed_on_delivery(conn):
    from tui_gateway.server import _collect_kanban_notifications as poll

    a = _card(conn, "a", sub=("tui", "K"))
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    _tick(conn)
    kb.archive_task(conn, a)
    poll({"session_key": "K"})

    assert kbn.list_notify_subs(conn, a) == []


def test_held_rows_that_carry_nothing_are_dropped_by_the_decision_the_carrier_by_its_delivery(conn):
    """Two archived cards of one orchestrator, both fully delivered: the decision drops the one it put nothing on;
    the carrier goes once the announcement is delivered."""
    from tui_gateway.server import _collect_kanban_notifications as poll

    a1 = _card(conn, "a1", sub=("tui", "K"))
    a2 = _card(conn, "a2", sub=("tui", "K"))
    _tick(conn)
    for tid in (a1, a2):
        kb.complete_task(conn, tid, summary="ok")
        kb.archive_task(conn, tid)
    poll({"session_key": "K"})
    _tick(conn)

    ((carrier,),) = _addressed(conn).values()
    other = a2 if carrier == a1 else a1
    assert kbn.list_notify_subs(conn, other) == []
    assert len(kbn.list_notify_subs(conn, carrier)) == 1
    shown = poll({"session_key": "K"})
    assert sum("no work left" in t for t in shown) == 1
    assert kbn.list_notify_subs(conn, carrier) == []


def test_an_archived_card_not_yet_polled_at_the_decision_keeps_its_row(conn):
    """The decision must not drop a row whose completion and archival were never delivered."""
    from tui_gateway.server import _collect_kanban_notifications as poll

    a = _card(conn, "a", sub=("tui", "K"))
    c = _card(conn, "c", sub=("tui", "K"))
    _tick(conn)
    kb.complete_task(conn, c, summary="carrier")
    kb.complete_task(conn, a, summary="THE RESULT")
    kb.archive_task(conn, a)
    kbn.advance_notify_cursor(conn, task_id=c, platform="tui", chat_id="K", thread_id="",
                              new_cursor=conn.execute("SELECT MAX(id) FROM task_events").fetchone()[0] + 10)
    _tick(conn)

    assert len(kbn.list_notify_subs(conn, a)) == 1
    assert any("THE RESULT" in t for t in poll({"session_key": "K"}))


def test_release_racing_the_decision_keeps_the_row_for_its_announcement(conn):
    """Notifier claims the archival, the dispatcher decides while the send is in flight and puts the announcement on
    this row, then the notifier advances and releases: the row must survive until the announcement is delivered."""
    a = _card(conn, "a", sub=("tui", "K"))
    b = _card(conn, "b")
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    kb.block_task(conn, b, reason="needs a human", kind="needs_input")
    kb.archive_task(conn, a)
    ident = dict(task_id=a, platform="tui", chat_id="K", thread_id="")
    _old, claimed, events = kbn.claim_unseen_events_for_sub(conn, **ident)
    assert events
    _tick(conn)
    assert _addressed(conn) == {("tui", "K"): [a]}
    kbn.advance_notify_cursor(conn, new_cursor=claimed, **ident)

    assert kbn.release_archived_notify_sub(conn, **ident) is False
    _old, _new, pending = kbn.claim_unseen_events_for_sub(conn, **ident)
    assert [e.kind for e in pending] == ["board_quiescent"]


def test_a_failed_send_during_the_decision_can_still_be_retried(conn):
    """The claim advances the cursor before sending; a decision landing mid-send must leave the row so the
    failed send can rewind it."""
    a = _card(conn, "a", sub=("tui", "K"))
    c = _card(conn, "c", sub=("tui", "K"))
    kbn.add_notify_sub(conn, task_id=c, platform="tui", chat_id="K", delivery_mode="notify+wake")
    _tick(conn)
    kb.complete_task(conn, c, summary="carrier")
    kb.complete_task(conn, a, summary="THE RESULT")
    kb.archive_task(conn, a)
    ident = dict(task_id=a, platform="tui", chat_id="K", thread_id="")
    old, claimed, events = kbn.claim_unseen_events_for_sub(conn, **ident)
    assert {e.kind for e in events} >= {"completed", "archived"}
    _tick(conn)
    assert _addressed(conn) == {("tui", "K"): [c]}

    assert kbn.rewind_notify_cursor(conn, claimed_cursor=claimed, old_cursor=old, **ident)
    _old, _new, again = kbn.claim_unseen_events_for_sub(conn, **ident)
    assert "completed" in {e.kind for e in again}


def test_two_dispatchers_deciding_one_drain_announce_it_once(conn):
    """The fast path runs outside the writer lock: a second dispatcher that saw the drain must re-check under it."""
    a = _card(conn, "a", sub=("tui", "K"))
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    real = kbn._newest_uncovered_claim
    seen = []

    def stale_fast_path(c, active):
        found = real(c, active)
        if not seen:  # the first (lock-free) look of dispatcher 2, taken before dispatcher 1 decides
            seen.append(found)
            kbn.announce_board_quiescent(kbc.connect())
        return found

    kbn._newest_uncovered_claim = stale_fast_path
    try:
        kbn.announce_board_quiescent(conn)
    finally:
        kbn._newest_uncovered_claim = real

    assert seen and seen[0] is not None
    assert len(_quiescent(conn)) == 1


def test_a_waking_card_carries_the_announcement_over_a_notify_only_one(conn):
    """Whether the orchestrator is woken must not depend on which of its cards finished last."""
    w = _card(conn, "w")
    kbn.add_notify_sub(conn, task_id=w, platform="telegram", chat_id="X", delivery_mode="notify+wake")
    p = _card(conn, "p", sub=("telegram", "X"))
    _tick(conn)
    kb.complete_task(conn, w, summary="first")
    kb.complete_task(conn, p, summary="last")
    _tick(conn)

    assert _addressed(conn) == {("telegram", "X"): [w]}


def test_two_profiles_in_one_group_are_told_separately(conn):
    a = _card(conn, "a")
    kbn.add_notify_sub(conn, task_id=a, platform="telegram", chat_id="G", notifier_profile="profA")
    b = _card(conn, "b")
    kbn.add_notify_sub(conn, task_id=b, platform="telegram", chat_id="G", notifier_profile="profB")
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    kb.complete_task(conn, b, summary="ok")
    _tick(conn)

    assert _addressed(conn) == {("telegram", "G"): [a, b]}


def test_another_bots_row_in_the_same_chat_never_carries_the_announcement(conn):
    """Profile B's waking row in group G must not carry the drain profile A did: B's gateway would wake B's
    orchestrator and A would hear nothing."""
    z = _card(conn, "z")
    kbn.add_notify_sub(conn, task_id=z, platform="telegram", chat_id="G", notifier_profile="profB",
                       delivery_mode="notify+wake")
    _tick(conn)
    kb.complete_task(conn, z, summary="earlier drain")
    _tick(conn)
    first = len(_quiescent(conn))
    a = _card(conn, "a")
    kbn.add_notify_sub(conn, task_id=a, platform="telegram", chat_id="G", notifier_profile="profA")
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    _tick(conn)

    assert [t for t, _ in _quiescent(conn)[first:]] == [a]


def test_a_legacy_unstamped_row_and_a_stamped_one_are_one_destination(conn):
    """Old rows have no notifier profile and are delivered by the dispatch owner, the same bot that stamped rows of
    a single-profile setup name: two announcements would ping and wake one topic twice."""
    a = _card(conn, "a", sub=("telegram", "G"))
    b = _card(conn, "b")
    kbn.add_notify_sub(conn, task_id=b, platform="telegram", chat_id="G", notifier_profile="default")
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    kb.complete_task(conn, b, summary="ok")
    _tick(conn)

    assert sum(len(v) for v in _addressed(conn).values()) == 1


def test_stamping_a_profile_later_keeps_the_announcement_addressed(conn):
    """Re-subscribing fills a NULL profile; an announcement written before that must still reach its follower."""
    a = _card(conn, "a", sub=("telegram", "G"))
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    _tick(conn)
    kbn.add_notify_sub(conn, task_id=a, platform="telegram", chat_id="G", notifier_profile="default")

    ((_, sub),) = [(s["task_id"], s) for s in kbn.list_notify_subs(conn, a)]
    assert sub["notifier_profile"] == "default"
    row = conn.execute("SELECT * FROM task_events WHERE kind = 'board_quiescent'").fetchone()
    ev = kb.Event(id=row["id"], task_id=a, kind="board_quiescent", payload=json.loads(row["payload"]),
                  created_at=row["created_at"], run_id=None)
    assert kbn.quiescent_addressed_to(conn, ev, sub)


def test_two_topics_of_one_group_are_told_separately(conn):
    a = _card(conn, "a")
    for topic in ("1", "2"):
        kbn.add_notify_sub(conn, task_id=a, platform="telegram", chat_id="G", thread_id=topic)
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    _tick(conn)

    assert _addressed(conn) == {("telegram", "G", "1"): [a], ("telegram", "G", "2"): [a]}
    one, two = (s for s in kbn.list_notify_subs(conn, a))
    ev_for = {e[1]["to"]: e for e in _quiescent(conn)}
    tag_one = kbn.quiescent_destination_tag(conn, one)
    ev = kb.Event(id=0, task_id=a, kind="board_quiescent", payload={"to": tag_one}, created_at=0, run_id=None)
    assert tag_one in ev_for
    assert kbn.quiescent_addressed_to(conn, ev, one) and not kbn.quiescent_addressed_to(conn, ev, two)


def test_the_announcement_names_no_destination(conn):
    """Events are exported and shown to workers (``kanban show``); a chat id or session key must not leak there."""
    a = _card(conn, "a")
    kbn.add_notify_sub(conn, task_id=a, platform="telegram", chat_id="-100SECRETCHAT", thread_id="77")
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    _tick(conn)

    raw = conn.execute("SELECT payload FROM task_events WHERE kind = 'board_quiescent'").fetchone()[0]
    assert "SECRETCHAT" not in raw
    import hashlib
    import hmac
    for salt in (b"", b"0"):  # the tag is keyed by the board's secret, not a bare or trivially keyed hash
        guess = hmac.new(salt, "\x1f".join(("telegram", "-100SECRETCHAT", "77")).encode(), hashlib.sha256)
        assert json.loads(raw)["to"] != guess.hexdigest()[:24]


def test_a_card_waiting_for_its_reviewer_keeps_the_board_busy(conn):
    """With review dispatch on (the default) a review card is about to get a reviewer: the board is not idle."""
    a = _card(conn, "a", sub=("tui", "orchestrator"))
    _tick(conn)
    assert kb.request_review(conn, a, summary="please look", force=True)
    kbn.announce_board_quiescent(conn)

    assert _quiescent(conn) == []


def test_scheduled_cards_are_listed_for_attention(conn):
    """Nothing un-schedules a card automatically; it waits for someone to re-gate it."""
    a = _card(conn, "a", sub=("tui", "orchestrator"))
    s = _card(conn, "later")
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    assert kb.schedule_task(conn, s, reason="after the release")
    _tick(conn)

    ((_, payload),) = _quiescent(conn)
    assert payload["attention"] == [s]


def test_triage_cards_are_listed_for_attention(conn):
    a = _card(conn, "a", sub=("tui", "orchestrator"))
    t = kb.create_task(conn, title="needs sorting", assignee="worker", triage=True)
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    _tick(conn)

    ((_, payload),) = _quiescent(conn)
    assert payload["attention"] == [t]


def test_a_restart_between_the_drain_and_the_next_tick_keeps_the_announcement(conn):
    """Schema init runs on every process start; it must not re-seed the mark over a drain not yet announced."""
    a = _card(conn, "a", sub=("tui", "orchestrator"))
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    kb.init_db()
    _tick(conn)

    assert len(_quiescent(conn)) == 1


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


def test_release_never_drops_a_live_card_an_undelivered_archival_or_a_delivery_in_flight(conn):
    """Release must only drop a row whose card is archived and whose archival was delivered, with no claim of
    anyone's still being sent (the claim moves the cursor before the send; a failed send rewinds it)."""
    live = _card(conn, "live", sub=("tui", "K"))
    gone = _card(conn, "gone", sub=("tui", "K"))
    _tick(conn)
    _tick(conn)  # decide the drain so the claim mark does not hold the rows on its own
    for tid in (live, gone):
        kb.complete_task(conn, tid, summary="ok")
    kb.archive_task(conn, gone)
    kbn.announce_board_quiescent(conn)
    ident = dict(platform="tui", chat_id="K", thread_id="")

    assert kbn.release_archived_notify_sub(conn, task_id=live, **ident) is False
    assert kbn.release_archived_notify_sub(conn, task_id=gone, **ident) is False
    assert len(kbn.list_notify_subs(conn, live)) == 1 and len(kbn.list_notify_subs(conn, gone)) == 1

    claimed = {tid: kbn.claim_unseen_events_for_sub(conn, task_id=tid, **ident) for tid in (live, gone)}
    assert kbn.release_archived_notify_sub(conn, task_id=gone, **ident) is False  # claimed, not yet sent

    for tid, (old, new, _events) in claimed.items():
        kbn.advance_notify_cursor(conn, task_id=tid, new_cursor=new, **ident)
    assert kbn.release_archived_notify_sub(conn, task_id=live, **ident) is False
    assert kbn.release_archived_notify_sub(conn, task_id=gone, **ident) is True


def test_a_second_notifier_cannot_release_a_row_the_first_is_still_delivering(conn):
    """Two gateways on one board (``--force``, a multiplexer next to a profile gateway): the second one's empty
    claim or post-delivery release must leave a row the first has claimed and not yet delivered."""
    a = _card(conn, "a", sub=("telegram", "X"))
    b = _card(conn, "b")
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    kb.block_task(conn, b, reason="needs a human", kind="needs_input")
    kb.archive_task(conn, a)
    _tick(conn)
    ident = dict(task_id=a, platform="telegram", chat_id="X", thread_id="")
    old, claimed, events = kbn.claim_unseen_events_for_sub(conn, **ident)  # notifier 1, sending...
    assert {"completed", "board_quiescent", "archived"} <= {e.kind for e in events}
    assert kbn.claim_unseen_events_for_sub(conn, **ident)[2] == []  # notifier 2: nothing new

    assert kbn.release_archived_notify_sub(conn, **ident) is False
    kbn.announce_board_quiescent(conn)
    assert kbn.rewind_notify_cursor(conn, claimed_cursor=claimed, old_cursor=old, **ident)  # notifier 1's send failed


def test_a_legacy_unstamped_row_never_carries_a_drain_a_stamped_profile_did(conn):
    """An unstamped row is delivered by whichever process owns the dispatcher, possibly another bot."""
    old = _card(conn, "old")
    kbn.add_notify_sub(conn, task_id=old, platform="telegram", chat_id="G", delivery_mode="notify+wake")
    _tick(conn)
    kb.complete_task(conn, old, summary="earlier drain")
    _tick(conn)
    first = len(_quiescent(conn))
    a = _card(conn, "a")
    kbn.add_notify_sub(conn, task_id=a, platform="telegram", chat_id="G", notifier_profile="profA")
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    _tick(conn)

    assert [t for t, _ in _quiescent(conn)[first:]] == [a]


def test_held_rows_wait_while_the_board_is_busy_and_the_purge_reaps_them_without_a_dispatcher(conn):
    """No dispatcher (``dispatch_in_gateway: false`` and no daemon) never advances the mark; the purge still
    reaps held archived rows once they go stale."""
    import time as _time

    a = _card(conn, "a", sub=("tui", "K"))
    kb.claim_task(conn, a)
    kb.complete_task(conn, a, summary="ok")
    kb.archive_task(conn, a)
    ident = dict(task_id=a, platform="tui", chat_id="K", thread_id="")
    _o, new, _e = kbn.claim_unseen_events_for_sub(conn, **ident)
    kbn.advance_notify_cursor(conn, new_cursor=new, **ident)
    assert kbn.release_archived_notify_sub(conn, **ident) is False  # claimed past a mark nobody advances

    past = int(_time.time()) - 45 * 86400
    conn.execute("UPDATE task_events SET created_at = ? WHERE task_id = ?", (past, a))
    conn.commit()
    assert kbn.purge_stale_done_notify_subs(conn, max_age_days=30) == 1


def test_an_announcement_in_flight_on_an_already_told_row_blocks_its_release(conn):
    """The archival was delivered long ago; the announcement is claimed and still being sent. Neither the other
    notifier nor the decision may drop the row, or a failed send has nothing to rewind onto."""
    a = _card(conn, "a", sub=("tui", "K"))
    b = _card(conn, "b")
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    kb.archive_task(conn, a)
    ident = dict(task_id=a, platform="tui", chat_id="K", thread_id="")
    _o, told, _e = kbn.claim_unseen_events_for_sub(conn, **ident)
    kbn.advance_notify_cursor(conn, new_cursor=told, **ident)  # completion + archival delivered, row held
    kb.block_task(conn, b, reason="needs a human", kind="needs_input")
    _tick(conn)
    old, claimed, events = kbn.claim_unseen_events_for_sub(conn, **ident)
    assert [e.kind for e in events] == ["board_quiescent"]

    assert kbn.release_archived_notify_sub(conn, **ident) is False
    kbn.announce_board_quiescent(conn)
    assert kbn.rewind_notify_cursor(conn, claimed_cursor=claimed, old_cursor=old, **ident)


def test_the_decision_keeps_a_row_whose_archival_is_not_delivered_yet(conn):
    """The completion was delivered, the archival not yet: the decision passing this row by must not drop it,
    or the orchestrator never learns the card was archived."""
    a = _card(conn, "a", sub=("tui", "K"))
    c = _card(conn, "c", sub=("tui", "K"))
    _tick(conn)
    kb.complete_task(conn, a, summary="ok")
    ident = dict(task_id=a, platform="tui", chat_id="K", thread_id="")
    _o, told, _e = kbn.claim_unseen_events_for_sub(conn, **ident)
    kbn.advance_notify_cursor(conn, new_cursor=told, **ident)
    kb.complete_task(conn, c, summary="carrier")
    kb.archive_task(conn, a)
    _tick(conn)

    assert [t for t, _ in _quiescent(conn)] == [c]
    assert len(kbn.list_notify_subs(conn, a)) == 1
