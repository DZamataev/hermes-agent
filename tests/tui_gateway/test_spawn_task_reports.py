"""A spawn-task session hands its first finished turn back to the session that offered the chip.

Child side: ``session.create`` arms ``spawn_report_to``; the first ``complete`` turn drops one
report into the profile's mailbox. Parent side: the session's poller claims a report addressed to
it (directly or through its compression lineage) and runs it as a notification turn.
"""

import threading
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


class TestChildReportsItsFirstFinishedTurn:
    def test_first_complete_turn_leaves_one_report_for_the_offering_session(self, monkeypatch, tmp_path):
        db = SessionDB(db_path=tmp_path / "state.db")
        session = _session(agent=_agent(), pending_title="Fix flaky login", spawn_report_to="parent-key")

        _run_turn(monkeypatch, db, tmp_path, session)

        [(_path, record)] = reports.pending_reports(tmp_path)
        assert record["to"] == "parent-key"
        assert record["from"] == "child-key"
        assert record["title"] == "Fix flaky login"
        assert record["reply"] == "All done: fixed the flake."

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
    def _poll(self, monkeypatch, db, home, session, *, submit=None):
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

    def test_a_report_addressed_here_runs_as_a_notification_turn(self, monkeypatch, tmp_path):
        db = SessionDB(db_path=tmp_path / "state.db")
        reports.write_report(tmp_path, to_session="parent-key", from_session="child-key",
                             title="Fix flaky login", reply="All done.")
        parent = _session(session_key="parent-key")

        [turn] = self._poll(monkeypatch, db, tmp_path, parent)

        assert turn["text"].startswith(f'{reports.SPAWN_TASK_RESULT_PREFIX} — "Fix flaky login"]')
        assert "All done." in turn["text"] and "child-key" in turn["text"]
        assert turn["display_kind"] == "async_delegation_complete"
        assert turn["display_metadata"]["display_text"] == "Side task finished: Fix flaky login"
        assert reports.pending_reports(tmp_path) == []
        assert not list(reports.reports_dir(tmp_path).iterdir())

    def test_a_report_for_another_session_stays_put(self, monkeypatch, tmp_path):
        db = SessionDB(db_path=tmp_path / "state.db")
        reports.write_report(tmp_path, to_session="someone-else", from_session="c", title="T", reply="r")
        parent = _session(session_key="parent-key")

        assert self._poll(monkeypatch, db, tmp_path, parent) == []
        assert len(reports.pending_reports(tmp_path)) == 1
        assert parent["running"] is False

    def test_a_busy_session_gets_it_later(self, monkeypatch, tmp_path):
        db = SessionDB(db_path=tmp_path / "state.db")
        reports.write_report(tmp_path, to_session="parent-key", from_session="c", title="T", reply="r")
        parent = _session(session_key="parent-key", running=True)

        assert self._poll(monkeypatch, db, tmp_path, parent) == []
        assert len(reports.pending_reports(tmp_path)) == 1

    def test_a_failed_delivery_returns_the_report(self, monkeypatch, tmp_path):
        db = SessionDB(db_path=tmp_path / "state.db")
        reports.write_report(tmp_path, to_session="parent-key", from_session="c", title="T", reply="r")
        parent = _session(session_key="parent-key")

        def broken_submit(*a, **kw):
            raise RuntimeError("boom")

        self._poll(monkeypatch, db, tmp_path, parent, submit=broken_submit)

        assert len(reports.pending_reports(tmp_path)) == 1
        assert parent["running"] is False

    def test_a_refused_turn_returns_the_report(self, monkeypatch, tmp_path):
        db = SessionDB(db_path=tmp_path / "state.db")
        reports.write_report(tmp_path, to_session="parent-key", from_session="c", title="T", reply="r")
        parent = _session(session_key="parent-key")

        self._poll(monkeypatch, db, tmp_path, parent, submit=lambda *a, **kw: False)

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
        reports.write_report(tmp_path, to_session="parent-old", from_session="c", title="T", reply="r")
        parent = _session(session_key="parent-new")

        assert len(self._poll(monkeypatch, db, tmp_path, parent)) == 1
        assert reports.pending_reports(tmp_path) == []


def test_a_long_reply_is_capped_and_points_at_the_session():
    text = reports.format_report({"to": "p", "from": "child-key", "title": "T", "reply": "x" * 10_000})

    assert len(text) < reports.REPLY_LIMIT + 1000
    assert "truncated" in text and "child-key" in text
