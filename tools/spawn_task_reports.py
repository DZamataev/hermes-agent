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
# Same cap as a kanban completion summary; the rest stays in the spawned session.
REPLY_LIMIT = 4000


def reports_dir(home: Path | str) -> Path:
    return Path(home) / REPORTS_DIR_NAME


def write_report(home: Path | str, *, to_session: str, from_session: str, title: str, reply: str) -> Path:
    root = reports_dir(home)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:8]}.json"
    atomic_json_write(path, {"to": to_session, "from": from_session, "title": title, "reply": reply,
                             "created_at": time.time()}, fsync_dir=True)
    return path


def pending_reports(home: Path | str) -> list[tuple[Path, dict[str, Any]]]:
    """Unclaimed reports, oldest first. Unreadable files are skipped, not deleted."""
    root = reports_dir(home)
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.glob("*.json")):
        if path.name.startswith("."):
            continue  # atomic_json_write's temp file
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
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
    """The notification turn's text. Starts with ``SPAWN_TASK_RESULT_PREFIX``."""
    title = " ".join(str(record.get("title") or "").split()) or "side task"
    child = str(record.get("from") or "")
    reply = str(record.get("reply") or "").strip()
    if len(reply) > REPLY_LIMIT:
        reply = f"{reply[:REPLY_LIMIT]}\n… (truncated; the full reply is in session {child})"
    return "\n".join((
        f'{SPAWN_TASK_RESULT_PREFIX} — "{title}"]',
        "The side task you offered with spawn_task (notify_on_done=true) finished its first turn in its "
        f"own session ({child}). This message is that notification, not something the user wrote. "
        "The session's final reply:",
        "--- REPLY ---",
        reply or "(the session finished without a text reply)",
    ))


def display_text(record: dict[str, Any]) -> str:
    title = " ".join(str(record.get("title") or "").split())
    return f"Side task finished: {title}" if title else "Side task finished"
