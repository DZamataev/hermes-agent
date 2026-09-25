"""Tests for the TUI-side kanban notification poller (issue #59890).

``kanban_create`` auto-subscribes TUI/desktop sessions with
``platform="tui"`` / ``chat_id=HERMES_SESSION_KEY``, but no component ever
read those rows back: the gateway notifier skips them (no "tui" messaging
adapter) and the TUI notification poller only watched process completions.
``last_event_id`` stayed 0 forever and no notification was ever delivered.

These tests cover the delivery half that now lives in tui_gateway/server.py:
``_collect_kanban_notifications`` (cursor claim + formatting + archive-only
unsubscribe) and ``_format_kanban_event_text``.
"""

from types import SimpleNamespace
from unittest.mock import patch

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_notify as kbn
from tui_gateway.server import (
    _collect_kanban_notifications,
    _format_kanban_event_text,
)

SESSION_KEY = "tui-session-key-1"


def _session(key: str = SESSION_KEY) -> dict:
    return {"session_key": key}


def _create_subscribed_task(*, chat_id: str = SESSION_KEY, platform: str = "tui"):
    conn = kbc.connect()
    try:
        tid = kb.create_task(conn, title="notify tui", assignee="worker")
        kbn.add_notify_sub(conn, task_id=tid, platform=platform, chat_id=chat_id)
        return tid
    finally:
        conn.close()


def _complete(tid: str, summary: str = "all done") -> None:
    conn = kbc.connect()
    try:
        kb.complete_task(conn, tid, summary=summary)
    finally:
        conn.close()


def _sub_rows(tid: str) -> list:
    conn = kbc.connect()
    try:
        return kbn.list_notify_subs(conn, task_id=tid)
    finally:
        conn.close()


class TestCollectKanbanNotifications:
    def test_a_failure_that_trips_the_breaker_is_reported_once(self):
        """The breaker writes ``crashed`` and ``gave_up`` for one failure; the session is told once, by the
        ``gave_up`` that carries the error. A crash that did not trip it still reports."""
        from hermes_cli import kanban_db_dispatch as kbd

        tid = _create_subscribed_task()
        conn = kbc.connect()
        try:
            with kb.write_txn(conn):
                kb._append_event(conn, tid, "crashed", {"pid": 1})
            kbd._record_task_failure(conn, tid, error="pid 1 not alive", outcome="crashed", force_trip=True)
        finally:
            conn.close()
        texts = _collect_kanban_notifications(_session())
        assert len(texts) == 1 and "gave up" in texts[0] and "pid 1 not alive" in texts[0]

        conn = kbc.connect()
        try:
            with kb.write_txn(conn):
                kb._append_event(conn, tid, "crashed", {"pid": 2})
        finally:
            conn.close()
        texts = _collect_kanban_notifications(_session())
        assert len(texts) == 1 and "worker crashed" in texts[0]

    def test_zero_sub_board_is_never_opened_writable(self):
        conn = kbc.connect()
        conn.close()
        kb.create_board("second-board")

        with patch.object(kbc, "connect", wraps=kbc.connect) as spy_connect:
            texts = _collect_kanban_notifications(_session())

        assert texts == []
        spy_connect.assert_not_called()

    def test_done_reopen_notifies_once_per_event_until_archive(self):
        tid = _create_subscribed_task()
        _complete(tid, summary="shipped the fix")

        first = _collect_kanban_notifications(_session())

        assert len(first) == 1
        assert tid in first[0]
        assert "done" in first[0]
        assert "shipped the fix" in first[0]
        rows = _sub_rows(tid)
        assert len(rows) == 1, "done must retain the originating session"
        first_cursor = rows[0]["last_event_id"]

        # The retained subscription must not replay the completed event.
        assert _collect_kanban_notifications(_session()) == []

        conn = kbc.connect()
        try:
            with kb.write_txn(conn):
                conn.execute(
                    "UPDATE tasks SET status = 'ready' WHERE id = ?", (tid,)
                )
                kb._append_event(conn, tid, "status", {"status": "ready"})
            assert kb.complete_task(conn, tid, summary="review corrections")
        finally:
            conn.close()

        reopened = _collect_kanban_notifications(_session())

        assert len(reopened) == 2
        assert "ready" in reopened[0]
        assert "review corrections" in reopened[1]
        rows = _sub_rows(tid)
        assert len(rows) == 1
        assert rows[0]["chat_id"] == SESSION_KEY
        assert rows[0]["last_event_id"] > first_cursor
        assert _collect_kanban_notifications(_session()) == []

        conn = kbc.connect()
        try:
            assert kb.archive_task(conn, tid)
        finally:
            conn.close()

        # Archive is notification-terminal and removes the retained route.
        assert _collect_kanban_notifications(_session()) == []
        assert _sub_rows(tid) == []

    def test_matching_tui_sub_delivers_and_advances_cursor(self):
        tid = _create_subscribed_task()
        pre_cursor = _sub_rows(tid)[0]["last_event_id"]
        conn = kbc.connect()
        try:
            kb.block_task(conn, tid, reason="waiting on review")
        finally:
            conn.close()

        with patch.object(kbc, "connect", wraps=kbc.connect) as spy_connect:
            first = _collect_kanban_notifications(_session())
            second = _collect_kanban_notifications(_session())

        assert len(first) == 1
        assert "blocked" in first[0]
        assert "waiting on review" in first[0]
        assert second == []
        assert spy_connect.called
        # Blocked is not a final status -> subscription stays alive so a
        # respawned task's next terminal event still reaches the user.
        rows = _sub_rows(tid)
        assert len(rows) == 1
        assert rows[0]["last_event_id"] > pre_cursor

    def test_non_tui_subscription_does_not_open_board_writable(self):
        tid = _create_subscribed_task(platform="telegram", chat_id="chat-1")
        # New subs start caught up at creation time (issue #29905); record the
        # pre-completion cursors so we can assert they were never claimed.
        pre_cursor = _sub_rows(tid)[0]["last_event_id"]
        _complete(tid)

        with patch.object(kbc, "connect", wraps=kbc.connect) as spy_connect:
            texts = _collect_kanban_notifications(_session())

        assert texts == []
        spy_connect.assert_not_called()
        rows = _sub_rows(tid)
        assert len(rows) == 1
        assert rows[0]["last_event_id"] == pre_cursor

    def test_other_tui_session_does_not_open_board_writable(self):
        tid = _create_subscribed_task(chat_id="some-other-session")
        pre_cursor = _sub_rows(tid)[0]["last_event_id"]
        _complete(tid)

        with patch.object(kbc, "connect", wraps=kbc.connect) as spy_connect:
            texts = _collect_kanban_notifications(_session())

        assert texts == []
        spy_connect.assert_not_called()
        rows = _sub_rows(tid)
        assert len(rows) == 1
        assert rows[0]["last_event_id"] == pre_cursor

    def test_probe_error_falls_back_to_writable_delivery(self, monkeypatch):
        tid = _create_subscribed_task()
        _complete(tid, summary="fallback delivery")

        def fail_probe(*args, **kwargs):
            raise OSError("probe unavailable")

        monkeypatch.setattr(kbn, "count_notify_subs", fail_probe)
        with patch.object(kbc, "connect", wraps=kbc.connect) as spy_connect:
            texts = _collect_kanban_notifications(_session())

        assert len(texts) == 1
        assert tid in texts[0]
        spy_connect.assert_called_once()

    def test_no_session_key_is_a_noop(self):
        tid = _create_subscribed_task()
        _complete(tid)

        assert _collect_kanban_notifications({"session_key": ""}) == []
        assert _collect_kanban_notifications({"session_key": None}) == []
        assert len(_sub_rows(tid)) == 1

    def test_profile_scoped_session_reads_the_shared_board(self, tmp_path):
        """The kanban board is shared across profiles BY DESIGN (see the
        hermes_cli/kanban_db.py module docstring): ``kanban_home()`` anchors on
        ``get_default_hermes_root()``, which resolves the process env and
        ignores context-local profile overrides. A Desktop session bound to a
        non-launch profile (``session["profile_home"]``) must therefore still
        have its subscription claimed from the one shared board — the poller
        needs no per-profile home binding.
        """
        from hermes_constants import (
            reset_hermes_home_override,
            set_hermes_home_override,
        )

        tid = _create_subscribed_task()
        _complete(tid, summary="cross-profile delivery")

        other_profile_home = tmp_path / "profiles" / "reviewer"
        other_profile_home.mkdir(parents=True)
        session = {
            "session_key": SESSION_KEY,
            "profile_home": str(other_profile_home),
        }
        # Simulate the strictest case: a context-local profile override is
        # active while the poller collects (as a profile-bound RPC would set).
        token = set_hermes_home_override(str(other_profile_home))
        try:
            texts = _collect_kanban_notifications(session)
        finally:
            reset_hermes_home_override(token)

        assert len(texts) == 1
        assert tid in texts[0]
        assert "cross-profile delivery" in texts[0]
        # Completion is reversible, so the shared-board subscription remains
        # owned by this exact Desktop session until the task is archived.
        rows = _sub_rows(tid)
        assert len(rows) == 1
        assert rows[0]["chat_id"] == SESSION_KEY


def _compressed_lineage(tmp_path, monkeypatch, *ids: str):
    """A state.db where each id in ``ids`` is the compression continuation of the previous one."""
    import time as _time

    import tui_gateway.server as server
    from hermes_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")
    conn = db._conn
    assert conn is not None
    base = int(_time.time()) - 10_000
    for i, sid in enumerate(ids):
        db.create_session(sid, source="desktop", parent_session_id=ids[i - 1] if i else None)
        db.append_message(sid, role="user", content=f"turn in {sid}", timestamp=base + 100 * i + 10)
        conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (base + 100 * i, sid))
        if i < len(ids) - 1:
            db.end_session(sid, "compression")
            conn.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (base + 100 * i + 50, sid))
    conn.commit()
    monkeypatch.setattr(server, "_get_db", lambda: db)
    return db


class TestCompressedSessionOwnsItsSubscriptions:
    """Context compression rotates the session key; subscriptions written under an earlier key of the
    same conversation must still reach the live continuation (#91037)."""

    def test_continuation_receives_events_for_a_subscription_made_before_compression(self, tmp_path, monkeypatch):
        _compressed_lineage(tmp_path, monkeypatch, "before-compress", "after-compress")
        tid = _create_subscribed_task(chat_id="before-compress")
        conn = kbc.connect()
        try:
            kb.block_task(conn, tid, reason="needs the operator")
        finally:
            conn.close()

        texts = _collect_kanban_notifications(_session("after-compress"))

        assert len(texts) == 1 and tid in texts[0] and "needs the operator" in texts[0]
        assert _collect_kanban_notifications(_session("after-compress")) == []

    def test_subscriptions_of_an_unrelated_session_stay_unclaimed(self, tmp_path, monkeypatch):
        _compressed_lineage(tmp_path, monkeypatch, "before-compress", "after-compress")
        tid = _create_subscribed_task(chat_id="someone-else")
        pre_cursor = _sub_rows(tid)[0]["last_event_id"]
        _complete(tid)

        assert _collect_kanban_notifications(_session("after-compress")) == []
        assert _sub_rows(tid)[0]["last_event_id"] == pre_cursor

    def test_foreign_subscription_on_a_shared_board_stays_unclaimed(self, tmp_path, monkeypatch):
        _compressed_lineage(tmp_path, monkeypatch, "before-compress", "after-compress")
        own = _create_subscribed_task(chat_id="before-compress")
        foreign = _create_subscribed_task(chat_id="someone-else")
        pre_cursor = _sub_rows(foreign)[0]["last_event_id"]
        _complete(own, summary="mine")
        _complete(foreign, summary="theirs")

        texts = _collect_kanban_notifications(_session("after-compress"))

        assert len(texts) == 1 and "mine" in texts[0]
        assert _sub_rows(foreign)[0]["last_event_id"] == pre_cursor

    def test_stale_pre_compression_tab_leaves_events_to_the_live_continuation(self, tmp_path, monkeypatch):
        import tui_gateway.server as server

        _compressed_lineage(tmp_path, monkeypatch, "before-compress", "after-compress")
        tid = _create_subscribed_task(chat_id="before-compress")
        _complete(tid, summary="for the live tab")
        live = {"session_key": "after-compress"}
        monkeypatch.setitem(server._sessions, "sid-live-continuation", live)

        assert _collect_kanban_notifications(_session("before-compress")) == []
        texts = _collect_kanban_notifications(live)

        assert len(texts) == 1 and "for the live tab" in texts[0]


class TestBoardQuiescentReachesTheSession:
    def test_idle_board_announcement_is_delivered_with_leftovers(self):
        tid = _create_subscribed_task()
        conn = kbc.connect()
        try:
            kb._append_event(conn, tid, "board_quiescent",
                             {"counts": {"blocked": 2, "done": 5}, "attention": ["t_aaa", "t_bbb"]})
        finally:
            conn.close()

        texts = _collect_kanban_notifications(_session())

        assert len(texts) == 1
        assert "no work left" in texts[0] and "2 blocked" in texts[0] and "t_aaa" in texts[0]

    def test_board_level_line_does_not_credit_the_carrier_card(self):
        tid = _create_subscribed_task()
        conn = kbc.connect()
        try:
            kb._append_event(conn, tid, "board_quiescent", {"counts": {"done": 1}, "attention": []})
        finally:
            conn.close()

        (text,) = _collect_kanban_notifications(_session())

        assert tid not in text and "@worker" not in text and "no work left" in text

    def test_announcement_addressed_to_another_follower_is_skipped_without_wedging(self):
        tid = _create_subscribed_task()
        conn = kbc.connect()
        try:
            kb._append_event(conn, tid, "board_quiescent", {
                "counts": {"done": 1}, "attention": [], "mark": 3,
                "to": kbn.quiescent_destination_tag(conn, {"platform": "telegram", "chat_id": "chat-1"})})
            top = conn.execute("SELECT MAX(id) FROM task_events").fetchone()[0]
        finally:
            conn.close()

        assert _collect_kanban_notifications(_session()) == []
        assert _sub_rows(tid)[0]["last_event_id"] == top


class TestLineageDelivery:
    def test_card_followed_under_two_keys_of_one_conversation_is_delivered_once(self, tmp_path, monkeypatch):
        """kanban_create auto-subscribes the current key next to an inherited ancestor-key row; one event, one line."""
        _compressed_lineage(tmp_path, monkeypatch, "before-compress", "after-compress")
        tid = _create_subscribed_task(chat_id="before-compress")
        conn = kbc.connect()
        try:
            kbn.add_notify_sub(conn, task_id=tid, platform="tui", chat_id="after-compress")
        finally:
            conn.close()
        _complete(tid, summary="delivered once")

        texts = _collect_kanban_notifications(_session("after-compress"))

        assert len(texts) == 1 and "delivered once" in texts[0]

    def test_idle_board_announced_to_two_keys_of_one_conversation_is_shown_once(self, tmp_path, monkeypatch):
        _compressed_lineage(tmp_path, monkeypatch, "before-compress", "after-compress")
        old = _create_subscribed_task(chat_id="before-compress")
        new = _create_subscribed_task(chat_id="after-compress")
        conn = kbc.connect()
        try:
            for tid, key in ((old, "before-compress"), (new, "after-compress")):
                kb._append_event(conn, tid, "board_quiescent", {
                    "counts": {"done": 2}, "attention": [], "mark": 7,
                    "to": kbn.quiescent_destination_tag(conn, {"platform": "tui", "chat_id": key})})
        finally:
            conn.close()

        texts = _collect_kanban_notifications(_session("after-compress"))

        assert len(texts) == 1 and "no work left" in texts[0]

    def test_two_separate_idle_board_announcements_are_both_shown(self, tmp_path, monkeypatch):
        """Deduplication is per drain (``mark``), not per kind: a later drain in the same poll still gets through."""
        _compressed_lineage(tmp_path, monkeypatch, "before-compress", "after-compress")
        tid = _create_subscribed_task(chat_id="after-compress")
        conn = kbc.connect()
        try:
            for mark in (7, 9):
                kb._append_event(conn, tid, "board_quiescent", {
                    "counts": {"done": mark}, "attention": [], "mark": mark,
                    "to": kbn.quiescent_destination_tag(conn, {"platform": "tui", "chat_id": "after-compress"})})
        finally:
            conn.close()

        texts = _collect_kanban_notifications(_session("after-compress"))

        assert len(texts) == 2

    def test_a_second_compression_keeps_every_earlier_key(self, tmp_path, monkeypatch):
        """The cached lineage belongs to one key: after the key rotates again, both ancestors and the new key count."""
        _compressed_lineage(tmp_path, monkeypatch, "k1", "k2", "k3")
        session = _session("k2")
        assert _collect_kanban_notifications(session) == []
        tids = [_create_subscribed_task(chat_id=key) for key in ("k1", "k2", "k3")]
        for n, tid in enumerate(tids):
            _complete(tid, summary=f"from key {n + 1}")

        session["session_key"] = "k3"
        texts = _collect_kanban_notifications(session)

        assert sorted(t.split("\n")[-1] for t in texts) == ["from key 1", "from key 2", "from key 3"]

    def test_an_ancestor_tab_does_not_take_its_continuations_subscriptions(self, tmp_path, monkeypatch):
        """Ancestors only: a stale pre-compression tab must not claim what the conversation subscribed later."""
        _compressed_lineage(tmp_path, monkeypatch, "before-compress", "after-compress")
        tid = _create_subscribed_task(chat_id="after-compress")
        pre_cursor = _sub_rows(tid)[0]["last_event_id"]
        _complete(tid)

        assert _collect_kanban_notifications(_session("before-compress")) == []
        assert _sub_rows(tid)[0]["last_event_id"] == pre_cursor

    def test_a_dispatcher_announcement_reaches_the_session_end_to_end(self, tmp_path, monkeypatch):
        """dispatch_once writes the announcement; the poller of the subscribed session shows it, a second
        session following the same card does not."""
        from hermes_cli import kanban_db_dispatch as kbd
        from hermes_cli import profiles

        monkeypatch.setattr(profiles, "profile_exists", lambda name: True)
        _compressed_lineage(tmp_path, monkeypatch, "orchestrator")
        mine = _create_subscribed_task(chat_id="orchestrator")
        other = _create_subscribed_task(chat_id="someone-else")
        conn = kbc.connect()
        try:
            kbn.add_notify_sub(conn, task_id=other, platform="tui", chat_id="orchestrator")
            kbn.add_notify_sub(conn, task_id=mine, platform="tui", chat_id="someone-else")
            kbd.dispatch_once(conn, spawn_fn=lambda *a, **k: None)
            for tid in (mine, other):
                kb.complete_task(conn, tid, summary="ok")
            kbd.dispatch_once(conn, spawn_fn=lambda *a, **k: None)
        finally:
            conn.close()

        for key in ("orchestrator", "someone-else"):
            quiescent = [t for t in _collect_kanban_notifications(_session(key)) if "no work left" in t]
            assert len(quiescent) == 1, (key, quiescent)

    def test_failed_lineage_lookup_is_retried_on_the_next_poll(self, tmp_path, monkeypatch):
        import tui_gateway.server as server

        db = _compressed_lineage(tmp_path, monkeypatch, "before-compress", "after-compress")
        tid = _create_subscribed_task(chat_id="before-compress")
        _complete(tid, summary="after recovery")
        session = _session("after-compress")

        def _broken():
            raise RuntimeError("state.db unavailable")

        monkeypatch.setattr(server, "_get_db", _broken)
        assert _collect_kanban_notifications(session) == []
        monkeypatch.setattr(server, "_get_db", lambda: db)
        texts = _collect_kanban_notifications(session)

        assert len(texts) == 1 and "after recovery" in texts[0]


class TestFormatKanbanEventText:
    SUB = {"task_id": "t_abc123"}
    TASK = SimpleNamespace(title="build the thing", assignee="worker", result=None)

    def test_silent_kinds_return_none(self):
        for kind in ("archived", "unblocked"):
            ev = SimpleNamespace(kind=kind, payload={})
            assert _format_kanban_event_text(self.SUB, self.TASK, ev, "main") is None


    def test_timed_out_with_bad_payload_does_not_raise(self):
        ev = SimpleNamespace(kind="timed_out", payload={"limit_seconds": "not-a-number"})
        text = _format_kanban_event_text(self.SUB, self.TASK, ev, "")
        assert "timed out" in text


class TestNotificationPollerLoopKanbanWiring:
    """Drive a real TUI subscription through ``_notification_poller_loop``.

    Covers the wiring above ``_collect_kanban_notifications``: status.update
    emission, agent-turn dispatch when the session is idle, and the
    busy-session pending buffer that flushes once the session goes idle.
    """

    def _start_poller(self, session: dict, monkeypatch):
        import threading
        import tui_gateway.server as server

        emits: list = []
        submits: list = []
        monkeypatch.setattr(server, "_KANBAN_POLL_SECONDS", 0.01)
        monkeypatch.setattr(
            server, "_emit", lambda event, sid, payload=None: emits.append((event, payload))
        )
        monkeypatch.setattr(
            server,
            "_run_prompt_submit",
            lambda rid, sid, sess, text: submits.append(text),
        )
        stop = threading.Event()
        thread = threading.Thread(
            target=server._notification_poller_loop,
            args=(stop, "sid-poller-test", session),
            daemon=True,
        )
        thread.start()
        return stop, thread, emits, submits

    @staticmethod
    def _wait_for(predicate, timeout: float = 5.0) -> bool:
        import time as _time

        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            if predicate():
                return True
            _time.sleep(0.02)
        return False

    def _poller_session(self, *, running: bool = False) -> dict:
        import threading

        return {
            "session_key": SESSION_KEY,
            "history_lock": threading.Lock(),
            "running": running,
        }

    def test_idle_session_gets_status_update_and_agent_turn(self, monkeypatch):
        tid = _create_subscribed_task()
        _complete(tid, summary="poller e2e done")
        session = self._poller_session(running=False)

        stop, thread, emits, submits = self._start_poller(session, monkeypatch)
        try:
            assert self._wait_for(lambda: submits), "agent turn was never dispatched"
        finally:
            stop.set()
            thread.join(timeout=5)

        status_texts = [p["text"] for e, p in emits if e == "status.update" and p]
        assert any(tid in t for t in status_texts), status_texts
        assert any(e == "message.start" for e, _ in emits)
        assert any(tid in text for text in submits), submits
        assert session["running"] is True  # poller claimed the turn
        assert not session.get("_kanban_pending")

    def test_busy_session_buffers_then_flushes_when_idle(self, monkeypatch):
        tid = _create_subscribed_task()
        _complete(tid, summary="buffered while busy")
        session = self._poller_session(running=True)

        stop, thread, emits, submits = self._start_poller(session, monkeypatch)
        try:
            # Busy: the status line appears and the event is buffered, but no
            # agent turn is dispatched while another turn is running.
            assert self._wait_for(
                lambda: any(e == "status.update" for e, _ in emits)
                and session.get("_kanban_pending")
            )
            assert not submits

            with session["history_lock"]:
                session["running"] = False

            assert self._wait_for(lambda: submits), "pending batch never flushed"
        finally:
            stop.set()
            thread.join(timeout=5)

        assert any(tid in text for text in submits), submits
        assert session["_kanban_pending"] == []
        assert session["running"] is True
