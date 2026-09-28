"""A running worker asks its orchestrator a question and waits for the answer in the same run.

``kanban_comment(..., await_reply_minutes=N)`` on the worker's own task records the question (a ``question`` event
the orchestrator is notified of), then holds the tool call until someone else comments on the card or N minutes pass.
The reply is the tool result, so the worker keeps its context instead of blocking and restarting cold.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
import tools.kanban_tools as kt


@pytest.fixture
def worker(tmp_path, monkeypatch):
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for var in ("HERMES_KANBAN_DB", "HERMES_KANBAN_WORKSPACES_ROOT", "HERMES_KANBAN_HOME", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(var, raising=False)
    kb._INITIALIZED_PATHS.clear()
    kt._comment_watermark.clear()
    kt._comment_poll_last_attempt = 0.0
    monkeypatch.setattr(kt, "_AWAIT_POLL_SECONDS", 0.02)
    monkeypatch.setattr(kt, "_AWAIT_MINUTE_SECONDS", 0.5)  # one "minute" of waiting = 0.5 s in tests
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="live task", assignee="worker-bot")
        kb.claim_task(conn, tid)  # a waiting worker is always on a running card under its own run
        run_id = kb.get_task(conn, tid).current_run_id
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run_id))
    monkeypatch.setenv("HERMES_PROFILE", "worker-bot")
    return tid


def _ask(tid: str, body="Which API version: v1 or v2?", minutes=1, **extra) -> dict:
    return json.loads(kt._handle_comment({"task_id": tid, "body": body, "await_reply_minutes": minutes, **extra}))


def _reply_later(tid: str, body: str, author="orchestrator", after=0.1) -> threading.Thread:
    def run():
        time.sleep(after)
        with kbc.connect_closing() as conn:
            kb.add_comment(conn, tid, author=author, body=body)
    t = threading.Thread(target=run)
    t.start()
    return t


def test_the_reply_comes_back_as_the_tool_result(worker):
    t = _reply_later(worker, "v2, the v1 endpoint is gone")
    out = _ask(worker)
    t.join()
    assert out["ok"] is True
    assert [r["body"] for r in out["replies"]] == ["v2, the v1 endpoint is gone"]
    assert out["replies"][0]["author"] == "orchestrator"


def test_the_question_is_an_event_the_orchestrator_is_told_about(worker):
    _reply_later(worker, "v2").join()  # answered before the ask even polls: still a question event
    _ask(worker)
    with kbc.connect_closing() as conn:
        (ev,) = [e for e in kb.list_events(conn, worker) if e.kind == "question"]
    assert ev.payload["body"] == "Which API version: v1 or v2?"
    assert ev.payload["await_minutes"] == 1


def test_a_reply_under_the_worker_own_profile_name_still_counts(worker):
    """Worker and orchestrator may run on one profile, so a reply can carry the worker's author name. The worker
    cannot comment while its own tool call waits, so every comment after the question is the answer."""
    t = _reply_later(worker, "v2", author="worker-bot")
    out = _ask(worker)
    t.join()
    assert [r["body"] for r in out["replies"]] == ["v2"]


def test_a_reply_that_lands_mid_wait_is_not_also_steered_in_by_the_heartbeat(worker, monkeypatch):
    """The activity heartbeat runs the live comment injector while the tool call waits; the reply must reach the
    worker once, as the tool result."""
    class Agent:
        def __init__(self):
            self.steers = []
        def steer(self, text):
            self.steers.append(text)
            return True
    agent = Agent()
    kt.inject_new_comments_from_env(agent)  # seeds the watermark, as a running worker's first tool call does
    monkeypatch.setattr(kt, "_AWAIT_POLL_SECONDS", 0.6)
    out = {}
    t = threading.Thread(target=lambda: out.update(_ask(worker)))
    t.start()
    time.sleep(0.2)  # the wait has polled once and sleeps
    with kbc.connect_closing() as conn:
        kb.add_comment(conn, worker, author="orchestrator", body="v2")
    kt._comment_poll_last_attempt = 0.0  # a heartbeat tick lands before the wait's next poll
    kt.inject_new_comments_from_env(agent)
    t.join()
    assert [r["body"] for r in out["replies"]] == ["v2"]
    assert agent.steers == []


@pytest.mark.parametrize("change", ["block", "reclaim"])
def test_a_card_taken_from_the_worker_ends_the_wait_without_a_reply(worker, monkeypatch, change):
    """A block (or a reclaim) while the worker waits hands the card to someone else; a comment written after that
    addresses the next worker and must not come back to the stale one as its answer."""
    def take_away():
        time.sleep(0.1)
        with kbc.connect_closing() as conn:
            if change == "block":
                assert kb.block_task(conn, worker, reason="operator stop")
            else:
                assert kb.reclaim_task(conn, worker)
            kb.add_comment(conn, worker, author="orchestrator", body="for the next worker: redo it with v3")
    t = threading.Thread(target=take_away)
    t.start()
    started = time.monotonic()
    out = _ask(worker, minutes=5)
    t.join()
    assert time.monotonic() - started < 1.5
    assert out["replies"] == []
    assert "no longer" in out["next"]


def test_a_card_re_dispatched_to_another_run_ends_the_wait(worker):
    """Reclaimed and claimed again while the first worker still waits: the card is running, but not under its run."""
    def rerun():
        time.sleep(0.1)
        with kbc.connect_closing() as conn:
            assert kb.reclaim_task(conn, worker)
            kb.claim_task(conn, worker)
            kb.add_comment(conn, worker, author="orchestrator", body="for the new run")
    t = threading.Thread(target=rerun)
    t.start()
    out = _ask(worker, minutes=5)
    t.join()
    assert out["replies"] == [] and "no longer" in out["next"]


def test_no_reply_in_time_says_what_to_do_next(worker):
    started = time.monotonic()
    out = _ask(worker, minutes=1)
    assert 0.4 < time.monotonic() - started < 3
    assert out["ok"] is True and out["replies"] == []
    assert "kanban_block" in out["next"]


def test_the_wait_is_capped(worker):
    t = _reply_later(worker, "v2", after=0.05)  # answered at once: a missing cap must fail the assert, not hang
    out = _ask(worker, minutes=10_000)
    t.join()
    assert out["await_minutes"] == kt._AWAIT_MAX_MINUTES


def test_the_live_injector_does_not_deliver_the_reply_a_second_time(worker):
    class Agent:
        steers: list = []
        def steer(self, text):
            self.steers.append(text)
            return True
    agent = Agent()
    kt.inject_new_comments_from_env(agent)  # seeds the watermark, as a running worker's first tool call does
    t = _reply_later(worker, "v2")
    _ask(worker)
    t.join()
    kt._comment_poll_last_attempt = 0.0
    assert kt.inject_new_comments_from_env(agent) is False
    assert agent.steers == []


def test_waiting_is_only_for_the_worker_on_its_own_card(worker):
    with kbc.connect_closing() as conn:
        other = kb.create_task(conn, title="someone else's")
    out = json.loads(kt._handle_comment({"task_id": other, "body": "?", "await_reply_minutes": 1}))
    assert out.get("ok") is not True
    assert "own task" in json.dumps(out)


def test_a_plain_comment_does_not_wait_or_ask(worker):
    started = time.monotonic()
    out = json.loads(kt._handle_comment({"task_id": worker, "body": "progress note"}))
    assert out["ok"] is True and time.monotonic() - started < 0.3
    with kbc.connect_closing() as conn:
        assert "question" not in [e.kind for e in kb.list_events(conn, worker)]


def test_an_interrupt_ends_the_wait(worker, monkeypatch):
    monkeypatch.setattr(kt, "_await_interrupted", lambda: True)
    started = time.monotonic()
    out = _ask(worker, minutes=5)
    assert time.monotonic() - started < 0.3
    assert out["replies"] == [] and "interrupted" in out["next"]


class _Agent:
    def __init__(self):
        self.steers = []

    def steer(self, text):
        self.steers.append(text)
        return True


def _tick(agent):
    """One activity-heartbeat tick of the live comment injector."""
    kt._comment_poll_last_attempt = 0.0
    return kt.inject_new_comments_from_env(agent)


def test_a_note_written_just_before_the_question_is_not_lost(worker):
    """A note the injector had not polled yet when the worker asked: the wait returns it (as a note), and it is not
    steered in twice afterwards."""
    agent = _Agent()
    _tick(agent)  # seeds the watermark, as a running worker's first tool call does
    with kbc.connect_closing() as conn:
        kb.add_comment(conn, worker, author="orchestrator", body="NOTE: also update the changelog")
    t = _reply_later(worker, "v2", after=0.2)
    out = _ask(worker)
    t.join()
    _tick(agent)
    assert [r["body"] for r in out["replies"]] == ["v2"]
    delivered = [n["body"] for n in out.get("notes", [])] + [s for s in agent.steers]
    assert sum("also update the changelog" in d for d in delivered) == 1


def test_the_workers_own_earlier_comments_are_not_returned_as_notes(worker):
    agent = _Agent()
    _tick(agent)
    with kbc.connect_closing() as conn:
        kb.add_comment(conn, worker, author="worker-bot", body="progress: half done")
    t = _reply_later(worker, "v2", after=0.2)
    out = _ask(worker)
    t.join()
    assert "notes" not in out


def test_a_worker_that_lost_its_card_is_not_fed_the_next_workers_comments(worker):
    agent = _Agent()
    _tick(agent)

    def take_away():
        time.sleep(0.1)
        with kbc.connect_closing() as conn:
            assert kb.block_task(conn, worker, reason="operator stop")
    t = threading.Thread(target=take_away)
    t.start()
    out = _ask(worker, minutes=5)
    t.join()
    assert "no longer" in out["next"]
    with kbc.connect_closing() as conn:
        kb.add_comment(conn, worker, author="orchestrator", body="for the NEXT worker: redo it with v3")
    assert _tick(agent) is False
    assert agent.steers == []


def test_a_block_comment_written_before_the_block_is_not_taken_as_the_answer(worker, monkeypatch):
    """`hermes kanban block <id> <reason>` records a `BLOCKED: …` comment; a wait polling while the command runs must
    not hand that comment to the worker as the answer — it gets "no longer yours"."""
    import argparse
    import contextlib
    import io
    from hermes_cli import kanban as kc
    from hermes_cli.kanban_parser import build_parser

    real_block = kb.block_task

    def slow_block(conn, tid, **kw):  # widen the window between the command's writes
        time.sleep(0.3)
        return real_block(conn, tid, **kw)
    monkeypatch.setattr(kb, "block_task", slow_block)

    def cli_block():
        time.sleep(0.1)
        parser = argparse.ArgumentParser()
        build_parser(parser.add_subparsers())
        env = {k: os.environ.pop(k) for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID") if k in os.environ}
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                assert kc.kanban_command(parser.parse_args(["kanban", "block", worker, "stop, wrong approach"])) == 0
        finally:
            os.environ.update(env)
    t = threading.Thread(target=cli_block)
    t.start()
    out = _ask(worker, minutes=5)
    t.join()
    assert out["replies"] == [] and "no longer" in out["next"]
    with kbc.connect_closing() as conn:
        assert any(c.body == "BLOCKED: stop, wrong approach" for c in kb.list_comments(conn, worker))


def test_the_injector_resumes_after_the_wait(worker):
    """The wait's marker is cleared when the call returns: a later operator note is steered in as usual."""
    agent = _Agent()
    _tick(agent)
    _reply_later(worker, "v2", after=0.05).join()
    _ask(worker)
    with kbc.connect_closing() as conn:
        kb.add_comment(conn, worker, author="orchestrator", body="one more thing")
    assert _tick(agent) is True
    assert "one more thing" in agent.steers[-1]


def test_a_worker_started_without_a_run_id_still_gets_its_reply(worker, monkeypatch):
    """A hand-started worker has no HERMES_KANBAN_RUN_ID: holding a running card is enough to be answered."""
    monkeypatch.delenv("HERMES_KANBAN_RUN_ID")
    t = _reply_later(worker, "v2")
    out = _ask(worker)
    t.join()
    assert [r["body"] for r in out["replies"]] == ["v2"]
