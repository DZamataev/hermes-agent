"""Persist acceptance with the same ownership snapshot as the terminal write."""
from __future__ import annotations

from hermes_cli.kanban_db_connect import write_txn
from hermes_cli.kanban_pr_acceptance import _PR, COMMIT_CONTRACTS, collect_acceptance, collect_commit_acceptance


def _snapshot(conn, task_id):
    row = conn.execute("SELECT current_run_id, status, completion_contract FROM tasks WHERE id=?", (task_id,)).fetchone()
    return tuple(row) if row else None


def prepare_acceptance(conn, task_id, expected_run_id, metadata):
    snapshot = _snapshot(conn, task_id)
    if snapshot is None:
        return False
    run_id, status, contract = snapshot
    if not contract or contract == "local-only":
        return None
    if status not in {"running", "ready", "blocked", "review"} or (expected_run_id is not None and run_id != expected_run_id):
        return False
    if contract in COMMIT_CONTRACTS:
        no_change = metadata.get("no_change") if isinstance(metadata, dict) else None
        return snapshot, collect_commit_acceptance(conn, task_id, run_id, no_change,
                                                   allow_no_change=contract == "local-commit-or-none")
    published_pr = metadata.get("published_pr") if isinstance(metadata, dict) else None
    match = _PR.fullmatch(published_pr) if isinstance(published_pr, str) else None
    # Publication binds once. Retrying cannot replace the task's PR with a green sibling.
    if match and contract == match[1]:
        with write_txn(conn):
            if _snapshot(conn, task_id) != snapshot:
                return False
            conn.execute("UPDATE tasks SET completion_contract=? WHERE id=?", (published_pr, task_id))
        snapshot = (run_id, status, published_pr)
        contract = published_pr
    return snapshot, collect_acceptance(contract, published_pr)


def record_acceptance(conn, task_id, acceptance, *, override: bool = False):
    """Called under complete_task's write_txn, before its terminal UPDATE. ``override`` (the operator's
    ``complete --override-acceptance``; the CLI refuses it to a worker) lets a failed receipt through, recorded as ``acceptance_overridden``; a card that changed
    under the check is refused either way."""
    from hermes_cli.kanban_db import _append_event
    snapshot, receipt = acceptance
    if _snapshot(conn, task_id) != snapshot:
        return False
    kind = receipt.get("event_kind", "pr_acceptance")
    _append_event(conn, task_id, kind, receipt, run_id=snapshot[0])
    if not receipt["ok"] and override:
        _append_event(conn, task_id, "acceptance_overridden",
                      {"classification": receipt["classification"], "actor": "operator"}, run_id=snapshot[0])
        return True
    if not receipt["ok"]:
        label = "Commit acceptance" if kind == "commit_acceptance" else "PR acceptance"
        parts = (receipt.get("detail") or "", receipt.get("recovery") or "")
        detail = f"{label} {receipt['classification']}: " + " ".join(p for p in parts if p)
        conn.execute("UPDATE tasks SET last_failure_error=? WHERE id=?", (detail, task_id))
    return receipt["ok"]
