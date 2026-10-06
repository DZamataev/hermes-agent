"""Session-scoped subagent route (Desktop composer "Subagents" pick) in the TUI gateway.

Contract:
1. ``config.set key=delegation`` with a session pins ``{provider, model, reasoning_effort}`` on the session AND
   the live agent (``_delegation_override``), never writes config.yaml; ``auto``/"" clears it.
2. A malformed pick (no model, unknown effort) is refused with 4002; no session is refused too (this key has no
   global meaning — global tier routes live in config.yaml's ``delegation`` block).
3. It survives a deferred build (applied when the agent attaches), persists in the row's ``model_config``, comes
   back on resume, and is reported on ``session.info``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import tui_gateway.server as server
from tui_gateway.session_delegation import delegation_override_from_model_config

PICK = {"provider": "deepseek", "model": "deepseek-v4", "reasoning_effort": "high"}


def _agent(**extra):
    return SimpleNamespace(model="parent", provider="anthropic", base_url="", api_mode="", reasoning_config=None,
                           service_tier=None, session_id="k1", **extra)


def _set(params: dict) -> dict:
    return server._methods["config.set"]("rid-1", params)


def _call(session, value, sid="s1"):
    with patch.dict(server._sessions, {sid: session}, clear=False), \
            patch.object(server, "_write_config_key") as write_key, \
            patch.object(server, "_save_cfg") as save_cfg, \
            patch.object(server, "_persist_live_session_runtime") as persist, \
            patch.object(server, "_emit"):
        resp = _set({"key": "delegation", "session_id": sid, "value": value})
    return resp, write_key, save_cfg, persist


class TestConfigSetDelegation:
    def test_pick_pins_session_and_live_agent_without_global_write(self):
        agent = _agent()
        session = {"session_key": "k1", "agent": agent}
        resp, write_key, save_cfg, persist = _call(session, dict(PICK))
        assert resp["result"]["value"] == PICK["model"] and resp["result"]["delegation_override"] == PICK
        assert session["delegation_override"] == PICK
        assert agent._delegation_override == PICK
        write_key.assert_not_called()
        save_cfg.assert_not_called()
        persist.assert_called_once_with(session)

    def test_model_only_pick_is_valid(self):
        session = {"session_key": "k1", "agent": _agent()}
        resp, *_ = _call(session, {"model": "m"})
        assert resp["result"]["delegation_override"] == {"provider": "", "model": "m", "reasoning_effort": ""}

    def test_auto_clears(self):
        agent = _agent(_delegation_override=dict(PICK))
        session = {"session_key": "k1", "agent": agent, "delegation_override": dict(PICK)}
        resp, *_ = _call(session, "auto")
        assert resp["result"]["value"] == "auto"
        assert "delegation_override" not in session
        assert agent._delegation_override is None

    def test_prebuild_session_keeps_pick_for_the_deferred_build(self):
        session = {"session_key": "k1", "agent": None}
        resp, *_ = _call(session, dict(PICK))
        assert session["delegation_override"] == PICK
        agent = _agent()
        server._attach_built_agent(session, agent)
        assert agent._delegation_override == PICK

    def test_missing_model_is_refused(self):
        session = {"session_key": "k1", "agent": _agent()}
        resp, *_ = _call(session, {"provider": "deepseek"})
        assert resp["error"]["code"] == 4002
        assert "delegation_override" not in session

    def test_unknown_effort_is_refused(self):
        session = {"session_key": "k1", "agent": _agent()}
        resp, *_ = _call(session, {"model": "m", "reasoning_effort": "turbo"})
        assert resp["error"]["code"] == 4002

    def test_without_a_session_is_refused(self):
        with patch.object(server, "_write_config_key") as write_key, patch.object(server, "_save_cfg") as save_cfg:
            resp = _set({"key": "delegation", "value": dict(PICK)})
        assert resp["error"]["code"] == 4002
        write_key.assert_not_called()
        save_cfg.assert_not_called()


class _Db:
    def __init__(self, model_config=None):
        import json
        self.row = {"model_config": json.dumps(model_config or {})}
        self.written = None

    def get_session(self, _key):
        return self.row

    def update_session_meta(self, _key, model_config_json, _model):
        import json
        self.written = json.loads(model_config_json)
        self.row["model_config"] = model_config_json


class TestPersistAndResume:
    def _persist(self, session, db):
        with patch.object(server, "_live_session_agent_db", return_value=(session["agent"], "k1", db)):
            server._persist_live_session_runtime(session)

    def test_pick_round_trips_through_the_row(self):
        db = _Db()
        session = {"session_key": "k1", "agent": _agent(), "delegation_override": dict(PICK)}
        self._persist(session, db)
        assert db.written["delegation_override"] == PICK
        assert delegation_override_from_model_config(db.written) == PICK

    def test_cleared_pick_is_dropped_from_the_row(self):
        db = _Db({"delegation_override": dict(PICK)})
        session = {"session_key": "k1", "agent": _agent()}
        self._persist(session, db)
        assert "delegation_override" not in db.written

    def test_resume_record_restores_pick(self):
        record = server._deferred_session_record(
            "k1", cols=80, cwd="/tmp", history=[], lease=None, source="desktop",
            delegation_override=delegation_override_from_model_config({"delegation_override": dict(PICK)}))
        assert record["delegation_override"] == PICK

    def test_malformed_stored_pick_is_ignored(self):
        assert delegation_override_from_model_config({"delegation_override": {"model": ""}}) is None
        assert delegation_override_from_model_config({"delegation_override": "x"}) is None
        assert delegation_override_from_model_config({}) is None


def test_session_info_reports_pick():
    agent = _agent()
    session = {"session_key": "k1", "agent": agent, "delegation_override": dict(PICK)}
    info = server._session_info(agent, session)
    assert info["delegation_override"] == PICK
    assert server._session_info(agent, {"session_key": "k1", "agent": agent})["delegation_override"] == {}


def _create(tmp_path, monkeypatch, pick):
    """``session.create`` as the Desktop client sends it, with the build/db side effects stubbed."""
    from hermes_state import SessionDB
    from tui_gateway.transport import bind_transport, reset_transport

    class _Socket:
        def write(self, frame):
            return True

    db = SessionDB(db_path=tmp_path / "state.db")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_resolve_model", lambda: "test-model")
    monkeypatch.setattr(server, "_start_agent_build", lambda *a, **k: None)
    token = bind_transport(_Socket())
    try:
        resp = server.handle_request({"id": "1", "method": "session.create", "params": {
            "cols": 80, "source": "desktop", "cwd": str(tmp_path), "delegation_override": pick}})
    finally:
        reset_transport(token)
    assert "result" in resp, resp
    sid = resp["result"]["session_id"]
    return server._sessions.pop(sid)


def test_session_create_carries_a_draft_pick(tmp_path, monkeypatch):
    """A pick made on the draft composer rides session.create, like the model/effort pick does."""
    assert _create(tmp_path, monkeypatch, dict(PICK))["delegation_override"] == PICK
    assert "delegation_override" not in _create(tmp_path, monkeypatch, {"provider": "x"})


def test_create_and_resume_answers_report_the_pick(tmp_path, monkeypatch):
    """The pill paints from the create/resume answer before any session.info; a pick must not read as Auto."""
    from hermes_state import SessionDB
    from tui_gateway.transport import bind_transport, reset_transport

    class _Socket:
        def write(self, frame):
            return True

    db = SessionDB(db_path=tmp_path / "state.db")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_resolve_model", lambda: "test-model")
    monkeypatch.setattr(server, "_start_agent_build", lambda *a, **k: None)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda *a, **k: None)
    token = bind_transport(_Socket())
    try:
        created = server.handle_request({"id": "1", "method": "session.create", "params": {
            "cols": 80, "source": "desktop", "cwd": str(tmp_path), "delegation_override": dict(PICK)}})["result"]
        assert created["info"]["delegation_override"] == PICK
        key = created["stored_session_id"]
        db.create_session(key, source="desktop", model="test-model",
                          model_config={"delegation_override": dict(PICK)})
        db.append_message(key, "user", "hi")
        server._sessions.pop(created["session_id"], None)
        resumed = server.handle_request({"id": "2", "method": "session.resume", "params": {
            "session_id": key, "cols": 80, "source": "desktop"}})
        assert "result" in resumed, resumed
        assert resumed["result"]["info"]["delegation_override"] == PICK
        server._sessions.pop(resumed["result"]["session_id"], None)
    finally:
        reset_transport(token)
