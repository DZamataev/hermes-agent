"""Every path that gives a session a new agent, a new row, or a new answer keeps the subagent pick.

Each test drives the real gateway function the adversarial review found leaking the pick (see
tests/tui_gateway/test_delegation_override.py for the config.set / persist / info contract).
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import patch

import tui_gateway.server as server
from tui_gateway.session_delegation import apply_delegation_override

PICK = {"provider": "deepseek", "model": "deepseek-v4", "reasoning_effort": "high"}


def _agent(**extra):
    return SimpleNamespace(model="parent", provider="anthropic", base_url="", api_mode="", reasoning_config=None,
                           service_tier=None, session_id="k1", _session_db=None, **extra)


def test_first_row_write_stamps_a_draft_pick():
    """A pick that arrived before the row existed (draft → create) must land in the row's model_config."""
    _, model_config = server._workdir_row_model_config({"session_key": "k1", "delegation_override": dict(PICK)})
    assert model_config["delegation_override"] == PICK
    _, model_config = server._workdir_row_model_config({"session_key": "k1"})
    assert "delegation_override" not in model_config


def test_rebuild_keeps_the_pick_on_the_new_agent():
    old = _agent(_delegation_override=dict(PICK))
    session = {"session_key": "k1", "agent": old, "delegation_override": dict(PICK)}
    new = _agent()
    with patch.object(server, "_make_agent", return_value=new), \
            patch.object(server, "_config_model_target", return_value=None), \
            patch.object(server, "_transfer_db_to_agent", return_value=False):
        server._rebuild_session_agent("s1", session)
    assert session["agent"] is new
    assert new._delegation_override == PICK


def test_agent_init_model_config_carries_the_pick_for_lazy_row_and_compression():
    """``_session_init_model_config`` seeds the agent's own row INSERT and the compression continuation row."""
    agent = _agent(_session_init_model_config={"reasoning_config": None})
    apply_delegation_override({"delegation_override": dict(PICK)}, agent)
    assert agent._session_init_model_config["delegation_override"] == PICK
    apply_delegation_override({}, agent)
    assert "delegation_override" not in agent._session_init_model_config


def _real_db(tmp_path, model_config):
    from hermes_state import SessionDB
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("k1", source="desktop", model="row-model", model_config=model_config)
    return db


def _config_set_lazy(session, db, value):
    import contextlib

    @contextlib.contextmanager
    def _db(_session):
        yield db

    with patch.dict(server._sessions, {"s1": session}), patch.object(server, "_session_db", _db), \
            patch.object(server, "_emit"):
        return server._methods["config.set"]("r", {"key": "delegation", "session_id": "s1", "value": value})


def _stored(db):
    from hermes_state_sessions import _parse_model_config
    row = db.get_session("k1")
    return row["model"], _parse_model_config(row.get("model_config"))


def test_clearing_a_pick_on_a_lazy_session_reaches_the_row(tmp_path):
    db = _real_db(tmp_path, {"delegation_override": dict(PICK), "reasoning_config": {"effort": "low"}})
    session = {"session_key": "k1", "agent": None, "delegation_override": dict(PICK)}
    resp = _config_set_lazy(session, db, "auto")
    assert resp["result"]["delegation_override"] == {}
    model, stored = _stored(db)
    assert "delegation_override" not in stored
    assert stored["reasoning_config"] == {"effort": "low"}  # the rest of the row is untouched
    assert model == "row-model"  # the model column is not the pick's to write


def test_lazy_pick_write_does_not_clobber_a_concurrent_row_writer(tmp_path):
    """The pick write must be one atomic merge: whatever another writer stored in between must survive.

    The racer runs on the first read of the row the pick write performs (if any); an atomic merge never
    reads stale state, so the racer's key is still there afterwards."""
    db = _real_db(tmp_path, {"reasoning_config": {"effort": "low"}})
    real_get = db.get_session
    raced = []

    def _get_then_race(key):
        row = real_get(key)
        if not raced:
            raced.append(True)
            db.patch_session_model_config(key, {"yolo_mode": True})
        return row

    session = {"session_key": "k1", "agent": None}
    with patch.object(db, "get_session", side_effect=_get_then_race):
        _config_set_lazy(session, db, dict(PICK))
    if not raced:  # no read at all: race the next write directly
        db.patch_session_model_config("k1", {"yolo_mode": True})
    _, stored = _stored(db)
    assert stored["delegation_override"] == PICK
    assert stored["yolo_mode"] is True
    assert stored["reasoning_config"] == {"effort": "low"}


def test_malformed_row_config_is_repaired_not_reported_as_saved(tmp_path):
    db = _real_db(tmp_path, None)
    db._execute_write(lambda c: c.execute("UPDATE sessions SET model_config = '{broken' WHERE id = 'k1'"))
    session = {"session_key": "k1", "agent": None}
    _config_set_lazy(session, db, dict(PICK))
    assert _stored(db)[1]["delegation_override"] == PICK


def test_pick_is_session_scoped_a_stale_session_id_is_not_a_global_write():
    """config.set with an unknown session_id must be 4001 (stale session), never fall through to config.yaml."""
    with patch.object(server, "_write_config_key") as write_key, patch.object(server, "_save_cfg") as save_cfg:
        resp = server._methods["config.set"]("r", {"key": "delegation", "session_id": "gone", "value": dict(PICK)})
    assert resp["error"]["code"] == 4001
    write_key.assert_not_called()
    save_cfg.assert_not_called()


def test_eager_resume_puts_the_stored_pick_on_the_agent(tmp_path, monkeypatch):
    """Resume that builds the agent immediately must arm it with the row's pick before any turn runs."""
    from hermes_state import SessionDB
    from tui_gateway.transport import bind_transport, reset_transport

    class _Socket:
        def write(self, frame):
            return True

    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("k-eager", source="desktop", model="m", model_config={"delegation_override": dict(PICK)})
    db.append_message("k-eager", "user", "hi")
    built = {}

    def _make_agent(sid, key, **kw):
        agent = _agent()
        built["agent"] = agent
        return agent

    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_make_agent", _make_agent)
    monkeypatch.setattr(server, "_make_agent_in_context", lambda *a, **k: _make_agent(*a, **k), raising=False)
    token = bind_transport(_Socket())
    try:
        resp = server.handle_request({"id": "1", "method": "session.resume", "params": {
            "session_id": "k-eager", "cols": 80, "source": "desktop", "eager_build": True}})
        assert "result" in resp, resp
        live = server._sessions[resp["result"]["session_id"]]
        assert live["agent"] is built["agent"]  # the eager branch really built it
        assert live["delegation_override"] == PICK
        assert built["agent"]._delegation_override == PICK
        server._sessions.pop(resp["result"]["session_id"], None)
    finally:
        reset_transport(token)


def test_fallback_info_reports_the_pick():
    """A warm reattach to a live lazy session answers from _fallback_session_info."""
    with patch.object(server, "_session_default_model", return_value="m"):
        info = server._fallback_session_info({"cwd": "/tmp", "delegation_override": dict(PICK)})
    assert info["delegation_override"] == PICK


def test_compute_host_frame_carries_the_pick():
    session = {"session_key": "k1", "history": [], "history_lock": threading.Lock(), "delegation_override": dict(PICK)}
    with patch.object(server, "_session_source", return_value="desktop"), \
            patch.object(server, "_session_auth_user_id", return_value=None):
        frame = server._compute_host_turn_frame("r", "s1", session, "hi")
    assert frame["delegation_override"] == PICK


def test_compute_host_child_applies_and_clears_the_frame_pick():
    from tui_gateway.compute_host import ComputeHost
    agent = _agent()
    session = {"session_key": "k1", "agent": agent}
    fake_server = SimpleNamespace(_sessions={"s1": session})
    host = ComputeHost.__new__(ComputeHost)
    host._transport = object()
    host._ensure_server_session(fake_server, {"sid": "s1", "delegation_override": dict(PICK)})
    assert agent._delegation_override == PICK
    host._ensure_server_session(fake_server, {"sid": "s1", "delegation_override": None})
    assert agent._delegation_override is None and "delegation_override" not in session
    session["delegation_override"] = dict(PICK)
    host._ensure_server_session(fake_server, {"sid": "s1"})  # an older parent sends no key: keep the pick
    assert session["delegation_override"] == PICK
