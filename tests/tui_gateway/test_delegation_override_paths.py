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


class _Db:
    def __init__(self, model_config):
        import json
        self.row = {"model_config": json.dumps(model_config)}

    def get_session(self, _key):
        return self.row

    def update_session_meta(self, _key, model_config_json, _model):
        self.row["model_config"] = model_config_json


def test_clearing_a_pick_on_a_lazy_session_reaches_the_row():
    import contextlib
    import json
    db = _Db({"delegation_override": dict(PICK), "reasoning_config": {"effort": "low"}})
    session = {"session_key": "k1", "agent": None, "delegation_override": dict(PICK)}

    @contextlib.contextmanager
    def _db(_session):
        yield db

    with patch.dict(server._sessions, {"s1": session}), patch.object(server, "_session_db", _db), \
            patch.object(server, "_emit"):
        resp = server._methods["config.set"]("r", {"key": "delegation", "session_id": "s1", "value": "auto"})
    assert resp["result"]["delegation_override"] == {}
    stored = json.loads(db.row["model_config"])
    assert "delegation_override" not in stored
    assert stored["reasoning_config"] == {"effort": "low"}  # the rest of the row is untouched


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
