"""``local-commit`` completion contract: ``done`` needs a clean tree and a commit made during the run.

Real SQLite, real git repos in tmp, the real dispatcher claim path (only the worker spawn is faked).
"""
import subprocess
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli.kanban_pr_acceptance import validate_contract


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch, all_assignees_spawnable):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    (path / "a.txt").write_text("a\n")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-qm", "base")
    return path


def _dispatched(repo: Path, contract: str = "local-commit") -> tuple[str, int]:
    """Create a dir-workspace card on ``repo`` and claim it through the real dispatcher."""
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="impl", assignee="impl", workspace_kind="dir",
                             workspace_path=str(repo), completion_contract=contract)
        res = kbd.dispatch_once(conn, spawn_fn=lambda _t, _w: 4242)
        assert [s[0] for s in res.spawned] == [tid]
        return tid, kb.get_task(conn, tid).current_run_id


def _complete(tid: str, run_id: int) -> bool:
    with kbc.connect_closing() as conn:
        return kb.complete_task(conn, tid, summary="did it", expected_run_id=run_id)


def _commit(repo: Path, name: str = "b.txt") -> str:
    (repo / name).write_text("b\n")
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", "work")
    return _git(repo, "rev-parse", "HEAD")


def _state(tid: str):
    with kbc.connect_closing() as conn:
        task = kb.get_task(conn, tid)
        receipts = [e.payload for e in kb.list_events(conn, tid) if e.kind == "commit_acceptance"]
        return task, receipts


def test_validate_contract_accepts_local_commit():
    assert validate_contract("local-commit") == "local-commit"


def test_dispatcher_records_the_start_head(repo):
    tid, run_id = _dispatched(repo)
    with kbc.connect_closing() as conn:
        heads = [(e.payload, e.run_id) for e in kb.list_events(conn, tid) if e.kind == "workspace_head"]
    assert heads == [({"head": _git(repo, "rev-parse", "HEAD")}, run_id)]


def test_a_commit_on_a_clean_tree_is_accepted(repo):
    tid, run_id = _dispatched(repo)
    head = _commit(repo)

    assert _complete(tid, run_id) is True
    task, receipts = _state(tid)
    assert task.status == "done"
    assert receipts[-1]["ok"] is True and receipts[-1]["head_sha"] == head


def test_an_uncommitted_file_is_refused_and_the_card_stays_running(repo):
    tid, run_id = _dispatched(repo)
    _commit(repo)
    (repo / "dirty.txt").write_text("x\n")

    assert _complete(tid, run_id) is False
    task, receipts = _state(tid)
    assert task.status == "running"
    assert "git status --porcelain" in task.last_failure_error
    assert receipts[-1]["classification"] == "dirty"


def test_a_clean_tree_without_a_new_commit_is_refused(repo):
    tid, run_id = _dispatched(repo)

    assert _complete(tid, run_id) is False
    task, receipts = _state(tid)
    assert task.status == "running"
    assert receipts[-1]["classification"] == "no_commit"
    assert "No commit since the run started" in task.last_failure_error


def test_a_run_without_a_recorded_start_head_is_refused_as_missing(repo):
    """A card claimed before the upgrade has no ``workspace_head`` event: it cannot complete."""
    tid, run_id = _dispatched(repo)
    with kbc.connect_closing() as conn, kb.write_txn(conn):
        conn.execute("DELETE FROM task_events WHERE task_id = ? AND kind = 'workspace_head'", (tid,))
    _commit(repo)

    assert _complete(tid, run_id) is False
    task, receipts = _state(tid)
    assert task.status == "running"
    assert receipts[-1]["classification"] == "missing"


def test_force_does_not_bypass_the_gate(repo):
    tid, _run_id = _dispatched(repo)
    with kbc.connect_closing() as conn:
        assert kb.complete_task(conn, tid, summary="operator", force=True) is False
        assert kb.get_task(conn, tid).status == "running"


def test_local_only_ignores_a_dirty_tree(repo):
    tid, run_id = _dispatched(repo, contract="local-only")
    (repo / "dirty.txt").write_text("x\n")

    assert _complete(tid, run_id) is True
    task, receipts = _state(tid)
    assert task.status == "done" and receipts == []
