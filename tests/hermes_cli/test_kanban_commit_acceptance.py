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
    assert "?? dirty.txt" in task.last_failure_error
    assert receipts[-1]["classification"] == "dirty"


def test_the_dirty_refusal_names_the_files_and_the_action_within_a_block_reason(repo):
    """A worker often forwards the refusal as its block reason, which the session sees cut at ~160 chars:
    the changed paths and "retry kanban_complete" must both fit there, however many files are dirty."""
    tid, run_id = _dispatched(repo)
    _commit(repo)
    for i in range(12):
        (repo / f"file_{i:02d}_with_a_long_enough_name.txt").write_text("x\n")

    assert _complete(tid, run_id) is False
    task, _ = _state(tid)
    head = task.last_failure_error[:160]
    assert head.startswith("Commit acceptance dirty: ?? file_00_")
    assert "retry kanban_complete" in head
    assert "  " not in head


def test_a_clean_tree_without_a_new_commit_is_refused(repo):
    tid, run_id = _dispatched(repo)

    assert _complete(tid, run_id) is False
    task, receipts = _state(tid)
    assert task.status == "running"
    assert receipts[-1]["classification"] == "no_commit"
    assert task.last_failure_error.startswith("Commit acceptance no_commit: No commit since the run started")


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


def _cli(*argv: str) -> tuple[int, str]:
    import argparse
    import contextlib
    import io
    from hermes_cli import kanban as kc
    from hermes_cli.kanban_parser import build_parser
    parser = argparse.ArgumentParser()
    build_parser(parser.add_subparsers())
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = kc.kanban_command(parser.parse_args(["kanban", *argv]))
    return rc, err.getvalue()


def _blocked_card(repo: Path) -> str:
    tid, run_id = _dispatched(repo)
    with kbc.connect_closing() as conn:
        assert kb.block_task(conn, tid, reason="no changes by design", expected_run_id=run_id)
    return tid


def test_the_cli_refusal_names_the_receipt_not_an_unknown_id(repo):
    tid = _blocked_card(repo)

    rc, err = _cli("complete", tid, "--summary", "closing")
    assert rc != 0
    assert "unknown id" not in err
    assert "no_commit" in err and "--override-acceptance" in err


def test_the_cli_force_alone_does_not_override_a_failed_receipt(repo):
    tid = _blocked_card(repo)

    rc, _ = _cli("complete", tid, "--summary", "closing", "--force")
    assert rc != 0
    with kbc.connect_closing() as conn:
        assert kb.get_task(conn, tid).status == "blocked"
        assert "acceptance_overridden" not in [e.kind for e in kb.list_events(conn, tid)]


def test_the_cli_override_acceptance_closes_a_refused_card_and_records_it(repo):
    tid = _blocked_card(repo)

    rc, _ = _cli("complete", tid, "--summary", "closing", "--override-acceptance")
    assert rc == 0
    with kbc.connect_closing() as conn:
        assert kb.get_task(conn, tid).status == "done"
        kinds = [e.kind for e in kb.list_events(conn, tid)]
    assert kinds.index("commit_acceptance") < kinds.index("acceptance_overridden") < kinds.index("completed")


NO_CHANGE_OK = "local-commit-or-none"


def _complete_no_change(tid: str, run_id, reason="review had no findings") -> bool:
    with kbc.connect_closing() as conn:
        return kb.complete_task(conn, tid, summary="nothing to fix", metadata={"no_change": reason},
                                expected_run_id=run_id)


def test_the_no_commit_refusal_points_at_no_change_where_the_contract_allows_it(repo):
    tid, run_id = _dispatched(repo, contract=NO_CHANGE_OK)
    assert _complete(tid, run_id) is False
    task, _ = _state(tid)
    assert "no_change" in task.last_failure_error
    assert "kanban_block" not in task.last_failure_error


def test_strict_local_commit_neither_accepts_nor_suggests_no_change(repo):
    """An implementation card must leave a commit: declaring no_change is no way out, and the refusal does not
    advertise one."""
    tid, run_id = _dispatched(repo)
    assert _complete_no_change(tid, run_id) is False
    task, receipts = _state(tid)
    assert receipts[-1]["classification"] == "no_commit"
    assert "no_change" not in task.last_failure_error


def test_the_new_contract_is_accepted_by_create():
    from hermes_cli.kanban_pr_acceptance import validate_contract
    assert validate_contract(NO_CHANGE_OK) == NO_CHANGE_OK


def test_a_declared_no_change_on_a_clean_tree_is_accepted_with_its_reason(repo):
    tid, run_id = _dispatched(repo, contract=NO_CHANGE_OK)

    assert _complete_no_change(tid, run_id) is True
    task, receipts = _state(tid)
    assert task.status == "done"
    assert receipts[-1]["ok"] is True
    assert receipts[-1]["classification"] == "no_change"
    assert receipts[-1]["detail"] == "review had no findings"


def test_a_declared_no_change_does_not_excuse_a_dirty_tree(repo):
    tid, run_id = _dispatched(repo, contract=NO_CHANGE_OK)
    (repo / "dirty.txt").write_text("x\n")

    assert _complete_no_change(tid, run_id) is False
    _, receipts = _state(tid)
    assert receipts[-1]["classification"] == "dirty"


@pytest.mark.parametrize("reason", ["", "   ", None, 7])
def test_no_change_needs_a_reason(repo, reason):
    tid, run_id = _dispatched(repo, contract=NO_CHANGE_OK)

    assert _complete_no_change(tid, run_id, reason=reason) is False
    _, receipts = _state(tid)
    assert receipts[-1]["classification"] == "no_commit"


def test_a_commit_with_no_change_declared_is_an_ordinary_success(repo):
    tid, run_id = _dispatched(repo, contract=NO_CHANGE_OK)
    _commit(repo)

    assert _complete_no_change(tid, run_id) is True
    _, receipts = _state(tid)
    assert receipts[-1]["classification"] == "success"


def test_the_orchestrator_closes_a_blocked_no_op_card_with_no_change(repo):
    """The worker blocked (its run ended); the operator closes the card from outside any run."""
    tid, run_id = _dispatched(repo, contract=NO_CHANGE_OK)
    with kbc.connect_closing() as conn:
        assert kb.block_task(conn, tid, reason="no changes by design", expected_run_id=run_id)

    assert _complete_no_change(tid, None) is True
    task, receipts = _state(tid)
    assert task.status == "done"
    assert receipts[-1]["classification"] == "no_change"


def test_a_blocked_card_without_no_change_still_needs_a_commit(repo):
    tid, run_id = _dispatched(repo)
    with kbc.connect_closing() as conn:
        assert kb.block_task(conn, tid, reason="stuck", expected_run_id=run_id)

    assert _complete(tid, None) is False
    _commit(repo)
    assert _complete(tid, None) is True


def test_local_only_ignores_a_dirty_tree(repo):
    tid, run_id = _dispatched(repo, contract="local-only")
    (repo / "dirty.txt").write_text("x\n")

    assert _complete(tid, run_id) is True
    task, receipts = _state(tid)
    assert task.status == "done" and receipts == []
