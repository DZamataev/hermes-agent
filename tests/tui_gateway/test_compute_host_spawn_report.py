"""Turn isolation must not lose a spawn-task child's report address (Б1).

With ``dashboard.turn_isolation: true`` the first turn of a freshly created session runs in a
compute-host child, which builds its session dict from the frame. The one-shot spawn-report arm
— and the client title that must claim the row before the auto-title runs — therefore have to
cross with the frame, or the offering session never hears back (and the row keeps its auto title).

They cross only on the turn that BUILDS the child session: the arm is cleared once the report is
written and the title is claimed once, while the parent's own copies never clear (the child owns
the turn), so re-adopting them on every frame would make the child report the same task again.
"""

from __future__ import annotations

import io
import threading
import time

from tui_gateway import server
from tui_gateway.compute_host import ComputeHost


def _session(**extra):
    base = dict(
        agent=None, agent_ready=threading.Event(), session_key="s1-key", history=[],
        history_version=0, history_lock=threading.Lock(), running=False,
        attached_images=[], image_counter=0, cols=80, slash_worker=None,
        show_reasoning=False, tool_progress_mode="all", inflight_turn=None,
        created_at=time.time(), last_active=time.time())
    base.update(extra)
    return base


def _host(monkeypatch):
    # heartbeat_secs=0: no heartbeat or ppid-guard threads, so nothing to tear down.
    host = ComputeHost(stdout=io.StringIO(), heartbeat_secs=0)
    monkeypatch.setattr(host, "_build_server_session", lambda srv, frame, sid: _session())
    return host


def test_compute_host_turn_frame_carries_the_spawn_report_arm_and_title():
    session = _session(spawn_report_to="offering-key", pending_title="Fix flaky login",
                       pending_title_dedupe=True)

    frame = server._compute_host_turn_frame("r1", "sid", session, "hello")

    assert frame["spawn_report_to"] == "offering-key"
    assert frame["pending_title"] == "Fix flaky login"
    assert frame["pending_title_dedupe"] is True
    # Copied, not popped — the in-process fallback path still reports.
    assert session["spawn_report_to"] == "offering-key"


def test_the_child_adopts_them_when_it_builds_the_session(monkeypatch):
    host = _host(monkeypatch)

    session = host._ensure_server_session(server, {
        "sid": "new-sid", "session_key": "new-key", "spawn_report_to": "offering-key",
        "pending_title": "Fix flaky login", "pending_title_dedupe": True})

    assert session["spawn_report_to"] == "offering-key"
    assert session["pending_title"] == "Fix flaky login"
    assert session["pending_title_dedupe"] is True


def test_a_live_child_session_is_not_re_armed_by_a_stale_frame(monkeypatch):
    host = _host(monkeypatch)
    session = _session(spawn_report_to=None, pending_title=None)
    server._sessions["live-sid"] = session
    try:
        host._ensure_server_session(server, {"sid": "live-sid", "spawn_report_to": "offering-key",
                                             "pending_title": "Fix flaky login"})

        assert session.get("spawn_report_to") is None
        assert session.get("pending_title") is None
    finally:
        server._sessions.pop("live-sid", None)


def test_a_finished_child_turn_disarms_the_parent_so_a_respawn_does_not_report_again(monkeypatch):
    """After a compute-host respawn the child is rebuilt from the PARENT's frame. The arm is
    one-shot create-time intent, so the parent must drop its copy once the child's turn is
    done — otherwise the rebuilt child adopts a stale arm and writes a second report (находка 1)."""
    host = _host(monkeypatch)
    parent = _session(spawn_report_to="offering-key")
    server._sessions["sid"] = parent
    monkeypatch.setattr(server, "_emit", lambda *a, **kw: None)
    monkeypatch.setattr(server, "_compute_host_session_info", lambda session: {})
    monkeypatch.setattr(server, "_drain_queued_prompt", lambda *a, **kw: None)
    monkeypatch.setattr(server, "_release_deferred_active_session_lease", lambda session: None)
    try:
        # Turn 1: the frame carries the arm; the freshly built child adopts it.
        frame = server._compute_host_turn_frame("r1", "sid", parent, "hello")
        child = host._ensure_server_session(server, {**frame, "sid": "child-sid"})
        assert child["spawn_report_to"] == "offering-key"
        # The child reports and clears its own copy; the turn settles successfully.
        child["spawn_report_to"] = None
        server._on_compute_host_turn_done("r1", "sid", parent, {"type": "turn.end"})

        assert parent.get("spawn_report_to") is None
        # Compute host respawned (its sessions are gone): the rebuilt child must not be
        # re-armed from a stale frame, or its next finished turn reports the task again.
        assert server._compute_host_turn_frame("r2", "sid", parent, "later")["spawn_report_to"] is None
    finally:
        server._sessions.pop("sid", None)
