"""Durable hand-back of a spawn-task session's result to the session that offered the chip.

A chip launched from an offer with ``notify_on_done`` creates its session with
``spawn_report_to=<offering session id>``. When that session finishes its first
turn, the gateway drops one report file here; the offering session's poller
claims it and runs it as a notification turn. A file, not the in-memory
completion queue: the offering tab may be closed (or the app restarted) by then,
and the report must wait for it.

Layout: ``<profile home>/spawn_task_reports/<id>.json``. A claim renames the
file to ``<id>.claimed`` (one claimer wins), delivery deletes it, a failed
delivery renames it back.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from utils import atomic_json_write

log = logging.getLogger(__name__)

REPORTS_DIR_NAME = "spawn_task_reports"
# Both sides rely on it: the offering session is told (tool schema) that a message
# starting with this is a notification, not the user's words.
SPAWN_TASK_RESULT_PREFIX = "[SPAWN TASK RESULT"
# A claim this old belongs to a poller that died between ``claim_report`` and
# ``finish_report`` (app quit, gateway restart): treated as pending again, so the
# report is redelivered. A duplicate notification is acceptable; a lost one is not.
CLAIM_STALE_SECONDS = 600
# ``pending_reports`` is the mailbox's only reader, so its expiry lives there: an
# address nothing resolves (the offering session was deleted, or the child ran in
# another profile) would otherwise be globbed and resolved on every poll, forever.
REPORTS_TTL_SECONDS = 30 * 24 * 3600


def reports_dir(home: Path | str) -> Path:
    return Path(home) / REPORTS_DIR_NAME


def write_report(home: Path | str, *, to_session: str, from_session: str, title: str) -> Path:
    root = reports_dir(home)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:8]}.json"
    atomic_json_write(path, {"to": to_session, "from": from_session, "title": title,
                             "created_at": time.time()}, fsync_dir=True)
    return path


def _requeue_stale_claims(root: Path, now: float) -> None:
    for claimed in sorted(root.glob("*.claimed")):
        try:
            if now - claimed.stat().st_mtime < CLAIM_STALE_SECONDS:
                continue
            os.rename(claimed, claimed.with_suffix(".json"))
        except OSError:
            continue


def _prune_expired(root: Path, now: float) -> None:
    removed = 0
    for path in root.iterdir():
        if path.name.startswith(".") or path.suffix not in (".json", ".claimed"):
            continue  # atomic_json_write's temp file, or something not ours
        try:
            if now - path.stat().st_mtime < REPORTS_TTL_SECONDS:
                continue
            path.unlink()
        except OSError:
            continue
        removed += 1
    if removed:
        log.info("pruned %d spawn-task report(s) older than %d days",
                 removed, REPORTS_TTL_SECONDS // 86400)


def pending_reports(home: Path | str) -> list[tuple[Path, dict[str, Any]]]:
    """Unclaimed reports, oldest first. Unreadable files are skipped, not deleted."""
    root = reports_dir(home)
    if not root.is_dir():
        return []
    now = time.time()
    _requeue_stale_claims(root, now)
    _prune_expired(root, now)
    out = []
    for path in sorted(root.glob("*.json")):
        if path.name.startswith("."):
            continue  # atomic_json_write's temp file
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and isinstance(record.get("to"), str) and record["to"]:
            out.append((path, record))
    return out


def claim_report(path: Path) -> Path | None:
    claimed = path.with_suffix(".claimed")
    try:
        os.rename(path, claimed)
    except OSError:
        return None  # another poller took it
    try:
        # rename keeps the report's mtime; stamp the claim so ``_requeue_stale_claims``
        # measures THIS claim's age, not the report's — a report that waited an hour in
        # the mailbox is not stale the moment a live poller claims it.
        os.utime(claimed, None)
    except OSError:
        pass  # the claim stands; only its staleness clock is off
    return claimed


def release_report(claimed: Path) -> None:
    try:
        os.rename(claimed, claimed.with_suffix(".json"))
    except OSError:
        log.warning("could not return spawn-task report %s to the queue", claimed, exc_info=True)


def finish_report(claimed: Path) -> None:
    try:
        claimed.unlink()
    except FileNotFoundError:
        pass


def format_report(record: dict[str, Any]) -> str:
    """The notification turn's text. Starts with ``SPAWN_TASK_RESULT_PREFIX``. The child's
    reply is deliberately NOT carried: the offering session reads the session itself."""
    title = _clean_title(record.get("title"))
    child = str(record.get("from") or "")
    return "\n".join((
        f'{SPAWN_TASK_RESULT_PREFIX} — "{title}"]',
        f"Side task session {child} (launched from your spawn_task chip) finished its first turn.",
        "This is a notification, not something the user wrote. Read the session with "
        f'session_search(session_id="{child}") if you need its result.',
    ))


def _clean_title(raw: Any) -> str:
    """The title sits between ``"`` and ``"]`` in the header. A quote or a bracket in it
    would end the header early, so the model would stop seeing the prefix as one unit."""
    title = " ".join(str(raw or "").split()) or "side task"
    return title.replace('"', "'").replace("]", ")")


def display_text(record: dict[str, Any]) -> str:
    title = " ".join(str(record.get("title") or "").split())
    return f"Side task finished: {title}" if title else "Side task finished"
