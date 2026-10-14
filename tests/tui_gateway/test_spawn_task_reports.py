"""A spawn-task session hands its first finished turn back to the session that offered the chip.

Child side: ``session.create`` arms ``spawn_report_to``; the first ``complete`` turn drops one
report into the profile's mailbox. Parent side: the session's poller claims a report addressed to
it (directly or through its compression lineage) and runs it as a notification turn.

The report is a NOTIFICATION: it names the side-task session and nothing else. The child's reply
stays in the child's own session, where the offering session reads it with ``session_search``.
"""

import logging
import os
import threading
import time
import types

from hermes_state import SessionDB
from tools import spawn_task_reports as reports
from tui_gateway import server


def _session(agent=None, session_key="child-key", **extra):
    return {
        "agent": agent if agent is not None else types.SimpleNamespace(session_id=session_key),
        "session_key": session_key, "history": [], "history_lock": threading.Lock(), "history_version": 0,
        "running": False, "attached_images": [], "image_counter": 0, "cols": 80, "slash_worker": None,
        "show_reasoning": False, "tool_progress_mode": "all", "pending_title": None, **extra,
    }


def _agent(session_id="child-key", reply="All done: fixed the flake."):
    class _Agent:
        _cached_system_prompt = ""

        def run_conversation(self, prompt, **kw):
            return {"final_response": reply, "messages": [{"role": "assistant", "content": reply}]}

    agent = _Agent()
    agent.session_id = session_id
    return agent


def _run_turn(monkeypatch, db, home, session, text="go"):
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_hermes_home", home)
    monkeypatch.setattr(server, "_emit", lambda *a, **kw: None)
    monkeypatch.setattr(server, "make_stream_renderer", lambda cols: None)
    monkeypatch.setattr(server, "render_message", lambda raw, cols: None)
    monkeypatch.setattr(server, "_sync_session_key_after_compress", lambda *a, **kw: None)

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None, **kw):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(server.threading, "Thread", _ImmediateThread)
    server._sessions["sid"] = session
    try:
        server.handle_request({"id": "1", "method": "prompt.submit", "params": {"session_id": "sid", "text": text}})
    finally:
        server._sessions.pop("sid", None)


def _poll(monkeypatch, db, home, session, *, submit=None):
    """One offering-session poll, with the turn itself stubbed out."""
    sent = []

    def fake_submit(rid, sid, s, text, **kw):
        sent.append({"text": text, **kw})
        return True

    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_hermes_home", home)
    monkeypatch.setattr(server, "_emit", lambda *a, **kw: None)
    monkeypatch.setattr(server, "_run_prompt_submit", submit or fake_submit)
    server._notif_poll_spawn_reports("psid", session)
    return sent


def _write(home, to_session="parent-key", child="child-key", title="T"):
    return reports.write_report(home, to_session=to_session, from_session=child, title=title)


def _claim(path):
    claimed = reports.claim_report(path)
    assert claimed is not None
    return claimed


class TestChildReportsItsFirstFinishedTurn:
    def test_first_complete_turn_leaves_one_report_for_the_offering_session(self, monkeypatch, tmp_path):
        db = SessionDB(db_path=tmp_path / "state.db")
        session = _session(agent=_agent(), pending_title="Fix flaky login", spawn_report_to="parent-key")

        _run_turn(monkeypatch, db, tmp_path, session)

        [(_path, record)] = reports.pending_reports(tmp_path)
        assert record["to"] == "parent-key"
        assert record["from"] == "child-key"
        assert record["title"] == "Fix flaky login"
        # Notification only: the child's reply is not carried here.
        assert "reply" not in record

    def test_later_turns_report_nothing(self, monkeypatch, tmp_path):
        db = SessionDB(db_path=tmp_path / "state.db")
        session = _session(agent=_agent(), spawn_report_to="parent-key")

        _run_turn(monkeypatch, db, tmp_path, session)
        _run_turn(monkeypatch, db, tmp_path, session, text="and one more thing")

        assert len(reports.pending_reports(tmp_path)) == 1

    def test_a_session_nobody_asked_about_reports_nothing(self, monkeypatch, tmp_path):
        db = SessionDB(db_path=tmp_path / "state.db")

        _run_turn(monkeypatch, db, tmp_path, _session(agent=_agent()))

        assert not reports.reports_dir(tmp_path).exists()

    def test_the_address_is_persisted_on_the_child_row(self, monkeypatch, tmp_path):
        """The dict is the fast copy; the row is the durable one — a resume rebuilds the dict
        from scratch and the report still has to go out (Б4)."""
        db = SessionDB(db_path=tmp_path / "state.db")
        monkeypatch.setattr(server, "_get_db", lambda: db)
        monkeypatch.setattr(server, "_hermes_home", tmp_path)

        assert server._ensure_session_db_row(_session(spawn_report_to="parent-key")) is True

        assert db.get_session_model_config_value("child-key", "spawn_report_to") == "parent-key"

    def test_a_resumed_child_reports_once_and_only_once(self, monkeypatch, tmp_path):
        """The app restarted while the side task ran: the resumed session's dict carries no
        address, so it is read off the row. The clear must land on BOTH copies, or the next
        restart reports the same task again."""
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("child-key", source="desktop", model_config={"spawn_report_to": "parent-key"})

        _run_turn(monkeypatch, db, tmp_path, _session(agent=_agent()))

        [(_path, record)] = reports.pending_reports(tmp_path)
        assert record["to"] == "parent-key"
        assert record["from"] == "child-key"
        assert db.get_session_model_config_value("child-key", "spawn_report_to") is None

        # Another restart, another fresh dict, one more finished turn: still one report.
        _run_turn(monkeypatch, db, tmp_path, _session(agent=_agent()), text="and one more thing")

        assert len(reports.pending_reports(tmp_path)) == 1


def test_create_arms_the_report_target(monkeypatch, tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda _sid: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_register_session_cwd", lambda _session: None)
    resp = server.handle_request({"id": "c", "method": "session.create", "params": {
        "cols": 96, "source": "desktop", "title": "T", "spawn_report_to": "parent-key"}})
    sid = resp["result"]["session_id"]
    try:
        assert server._sessions[sid]["spawn_report_to"] == "parent-key"
    finally:
        server._sessions.pop(sid, None)


class TestParentReceivesTheReport:
    def test_a_report_addressed_here_runs_as_a_notification_turn(self, monkeypatch, tmp_path):
        db = SessionDB(db_path=tmp_path / "state.db")
        _write(tmp_path, title="Fix flaky login")
        parent = _session(session_key="parent-key")

        [turn] = _poll(monkeypatch, db, tmp_path, parent)

        assert turn["text"].startswith(f'{reports.SPAWN_TASK_RESULT_PREFIX} — "Fix flaky login"]')
        assert 'session_search(session_id="child-key")' in turn["text"]
        assert turn["display_kind"] == "async_delegation_complete"
        assert turn["display_metadata"]["display_text"] == "Side task finished: Fix flaky login"
        # Same grouping key the async_delegation chip uses (Б8).
        assert turn["display_metadata"]["delegation_id"] == "child-key"
        assert reports.pending_reports(tmp_path) == []
        assert not list(reports.reports_dir(tmp_path).iterdir())

    def test_a_report_for_another_session_stays_put(self, monkeypatch, tmp_path):
        db = SessionDB(db_path=tmp_path / "state.db")
        _write(tmp_path, to_session="someone-else")
        parent = _session(session_key="parent-key")

        assert _poll(monkeypatch, db, tmp_path, parent) == []
        assert len(reports.pending_reports(tmp_path)) == 1
        assert parent["running"] is False

    def test_a_busy_session_gets_it_later(self, monkeypatch, tmp_path):
        db = SessionDB(db_path=tmp_path / "state.db")
        _write(tmp_path)
        parent = _session(session_key="parent-key", running=True)

        assert _poll(monkeypatch, db, tmp_path, parent) == []
        assert len(reports.pending_reports(tmp_path)) == 1

    def test_a_failed_delivery_returns_the_report(self, monkeypatch, tmp_path):
        db = SessionDB(db_path=tmp_path / "state.db")
        _write(tmp_path)
        parent = _session(session_key="parent-key")

        def broken_submit(*a, **kw):
            raise RuntimeError("boom")

        _poll(monkeypatch, db, tmp_path, parent, submit=broken_submit)

        assert len(reports.pending_reports(tmp_path)) == 1
        assert parent["running"] is False

    def test_a_refused_turn_returns_the_report(self, monkeypatch, tmp_path):
        db = SessionDB(db_path=tmp_path / "state.db")
        _write(tmp_path)
        parent = _session(session_key="parent-key")

        _poll(monkeypatch, db, tmp_path, parent, submit=lambda *a, **kw: False)

        assert len(reports.pending_reports(tmp_path)) == 1
        assert parent["running"] is False

    def test_compression_does_not_lose_the_address(self, monkeypatch, tmp_path):
        """The chip names the offering session's id at launch; compression may rotate it
        before the side task finishes. The continuation still gets the report."""
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("parent-old", source="desktop")
        db.append_message("parent-old", role="user", content="hi")
        db.end_session("parent-old", "compression")
        db.create_session("parent-new", source="desktop", parent_session_id="parent-old")
        db.append_message("parent-new", role="user", content="summary")
        _write(tmp_path, to_session="parent-old")
        parent = _session(session_key="parent-new")

        assert len(_poll(monkeypatch, db, tmp_path, parent)) == 1
        assert reports.pending_reports(tmp_path) == []


class TestTheMailboxHealsItself:
    def test_a_stale_claim_is_delivered_after_all(self, monkeypatch, tmp_path):
        """A claim is renamed before the turn runs, so an app quit in that window used to hide
        the report forever. Redelivering a duplicate is the lesser evil (Б2)."""
        db = SessionDB(db_path=tmp_path / "state.db")
        _write(tmp_path)
        [_path] = [p for p, _r in reports.pending_reports(tmp_path)]
        claimed = _claim(_path)
        assert reports.pending_reports(tmp_path) == []  # claimed, so invisible
        stale = time.time() - reports.CLAIM_STALE_SECONDS - 1
        os.utime(claimed, (stale, stale))

        [turn] = _poll(monkeypatch, db, tmp_path, _session(session_key="parent-key"))

        assert "child-key" in turn["text"]

    def test_a_fresh_claim_is_left_alone(self, tmp_path):
        _write(tmp_path)
        [_path] = [p for p, _r in reports.pending_reports(tmp_path)]
        _claim(_path)

        assert reports.pending_reports(tmp_path) == []

    def test_reports_past_the_ttl_are_pruned_in_one_log_line(self, tmp_path, caplog):
        """An address nothing resolves (the offering session was deleted, or the child ran in
        another profile) would otherwise be globbed and resolved on every poll, forever (Б3)."""
        old_json = _write(tmp_path, to_session="gone")
        claimed = _claim(_write(tmp_path, to_session="gone"))
        fresh = _write(tmp_path)
        stale = time.time() - reports.REPORTS_TTL_SECONDS - 1
        for path in (old_json, claimed):
            os.utime(path, (stale, stale))

        with caplog.at_level(logging.INFO, logger=reports.log.name):
            pending = reports.pending_reports(tmp_path)

        assert [record["to"] for _path, record in pending] == ["parent-key"]
        assert not old_json.exists() and not claimed.exists() and fresh.exists()
        lines = [r.getMessage() for r in caplog.records if r.name == reports.log.name and r.levelno == logging.INFO]
        assert len(lines) == 1 and "pruned 2" in lines[0]


class TestTheSessionPollerRunsTheMailbox:
    def test_the_notification_poller_polls_the_spawn_reports(self, monkeypatch):
        """Б7: nothing else proves the mailbox is wired into the session's poller thread —
        deleting the call would leave every other test green while the feature stayed dead.
        Drives the real loop one pass (stop event set by the poll itself)."""
        from tools import async_delegation
        from tools.process_registry import process_registry

        called = []
        stop = threading.Event()

        def fake_poll(sid, session):
            called.append(sid)
            stop.set()

        monkeypatch.setattr(server, "_notif_poll_spawn_reports", fake_poll)
        for idle in ("_notif_poll_kanban", "_poll_bot_live_delivery_guarded",
                     "_maybe_fire_tui_loop_tick", "_maybe_fire_tui_heartbeat_tick"):
            monkeypatch.setattr(server, idle, lambda *a, **kw: None)
        monkeypatch.setattr(async_delegation, "maybe_sweep_orphaned_completions", lambda *a, **kw: None)
        monkeypatch.setattr(process_registry, "restore_completions", lambda *a, **kw: None)

        session = _session(session_key="parent-key")
        server._sessions["psid"] = session
        # The loop parks on its stop event, so a missing poll would HANG this test instead of
        # failing it: a watchdog bounds the pass either way.
        watchdog = threading.Timer(3.0, stop.set)
        watchdog.start()
        try:
            server._notification_poller_scoped_loop(stop, "psid", session)
        finally:
            watchdog.cancel()
            server._sessions.pop("psid", None)

        assert called == ["psid"]


def test_the_notification_names_the_session_and_carries_no_reply():
    text = reports.format_report({"to": "p", "from": "child-key", "title": "Fix flaky login", "reply": "SECRET"})
    lines = text.split("\n")
    assert lines[0] == f'{reports.SPAWN_TASK_RESULT_PREFIX} — "Fix flaky login"]'
    assert "Side task session child-key" in lines[1]
    assert 'session_search(session_id="child-key")' in lines[2] and "notification" in lines[2]
    assert "SECRET" not in text
    assert not hasattr(reports, "REPLY_LIMIT")


def test_a_title_cannot_break_the_header_or_the_trusted_prefix():
    """The header is `[PREFIX — "<title>"]`; a quote or a `]` in the title would end it early,
    and the prefix is what the schema tells the model to trust (Б6)."""
    text = reports.format_report({"to": "p", "from": "child-key", "title": 'a "quoted] title\nwith breaks'})

    header = text.split("\n")[0]
    assert header == f'{reports.SPAWN_TASK_RESULT_PREFIX} — "a \'quoted) title with breaks"]'
    assert text.count('"]') == 1
    # An empty title still reads as a header, never as a bare prefix.
    assert reports.format_report({"to": "p", "from": "c", "title": ""}).split("\n")[0].endswith('"side task"]')
