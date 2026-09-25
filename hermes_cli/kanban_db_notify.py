"""Notification subscriptions consumed by the gateway kanban-notifier: per-(task, platform, chat, thread) rows with delivery metadata, unseen-event cursors and purge of stale done-task subs.

Split out of ``hermes_cli.kanban_db``; origin-resident helpers are reached
late-bound via ``_kb`` (import-cycle breaking) so monkeypatching
``kanban_db.<name>`` keeps working.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any
from typing import Iterable
from typing import Mapping
from typing import Optional
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hermes_cli.kanban_db import Event


# Notifier reaction to a terminal event: "notify" = passive adapter.send only
# (default); "notify+wake" = send AND wake the destination agent; "wake" = wake only.
_NOTIFY_DELIVERY_MODES = ("notify", "notify+wake", "wake")

_SCALAR_TYPES = (str, int, float, bool)

# Subscription primary key predicate; every per-row statement below binds
# ``(task_id, platform, chat_id, thread_id or "")`` against it.
_SUB_KEY_WHERE = "WHERE task_id = ? AND platform = ? AND chat_id = ? AND thread_id = ?"


def _sub_key(task_id: str, platform: str, chat_id: str, thread_id: Optional[str]) -> tuple:
    return (task_id, platform, chat_id, thread_id or "")


def _encode_notify_delivery_metadata(metadata: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Serialize platform send metadata stored on notification subscriptions."""
    if not isinstance(metadata, Mapping):
        return None
    clean = {
        str(key): value
        for key, value in metadata.items()
        if value is not None and isinstance(value, _SCALAR_TYPES)
    }
    if not clean:
        return None
    return json.dumps(clean, sort_keys=True, separators=(",", ":"))


def _decode_notify_delivery_metadata(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, _SCALAR_TYPES)}


def add_notify_sub(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    platform: str,
    chat_id: str,
    thread_id: Optional[str] = None,
    user_id: Optional[str] = None,
    user_id_alt: Optional[str] = None,
    chat_type: Optional[str] = None,
    notifier_profile: Optional[str] = None,
    delivery_mode: Optional[str] = None,
    delivery_metadata: Optional[Mapping[str, Any]] = None,
) -> None:
    """Register a gateway source wanting terminal-state notifications for
    ``task_id``; idempotent on (task, platform, chat, thread).

    ``user_id_alt`` (Signal UUID, Feishu union_id, ...) and ``chat_type`` are
    replayed on active wake: ``build_session_key`` prefers the alt id, so
    omitting it would key the wake into a different session. ``None`` keeps an
    existing row's value. ``delivery_mode``: ``None`` leaves an existing row
    untouched, an explicit valid value is last-write-wins, unknown falls back
    to ``"notify"``. ``delivery_metadata`` merges supplied routing anchors
    into an existing row so re-subscribing never discards them. New subs start
    caught up (``last_event_id`` =
    ``MAX(task_events.id)``) so the notifier never replays history at boot.
    """
    valid_mode = delivery_mode if delivery_mode in _NOTIFY_DELIVERY_MODES else None
    # api_server is stateless: the adapter has no send(), the wake self-post IS
    # the delivery. A plain 'notify' default would leave those subs with no
    # delivery mechanism at all. Explicit modes still win.
    insert_mode = valid_mode or ("notify+wake" if platform == "api_server" else "notify")
    key = _sub_key(task_id, platform, chat_id, thread_id)
    with _kb.write_txn(conn):
        existing = conn.execute(
            "SELECT delivery_metadata FROM kanban_notify_subs " + _SUB_KEY_WHERE,
            key,
        ).fetchone()
        existing_metadata = _decode_notify_delivery_metadata(existing["delivery_metadata"]) if existing else {}
        merged_metadata = dict(existing_metadata)
        if delivery_metadata:
            merged_metadata.update(delivery_metadata)
        metadata_json = _encode_notify_delivery_metadata(merged_metadata) if merged_metadata else None
        conn.execute(
            """
            INSERT OR IGNORE INTO kanban_notify_subs
                (task_id, platform, chat_id, thread_id, user_id, user_id_alt,
                 chat_type, notifier_profile, delivery_mode, delivery_metadata,
                 created_at, last_event_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    COALESCE((SELECT MAX(id) FROM task_events WHERE task_id = ?), 0))
            """,
            (
                *key, user_id, user_id_alt, chat_type or "dm", notifier_profile,
                insert_mode, metadata_json, int(time.time()), task_id,
            ),
        )
        # chat_type / delivery_mode are last-write-wins; delivery metadata
        # preserves existing routing fields while supplied fields overwrite them.
        # user_id, user_id_alt and notifier_profile only self-heal legacy rows lacking one.
        for column, value, fill_only in (
            ("chat_type", chat_type, False),
            ("user_id", user_id, True),
            ("user_id_alt", user_id_alt, True),
            ("notifier_profile", notifier_profile, True),
            ("delivery_mode", valid_mode, False),
            ("delivery_metadata", metadata_json, False),
        ):
            if not value:
                continue
            guard = f" AND ({column} IS NULL OR {column} = '')" if fill_only else ""
            conn.execute(
                f"UPDATE kanban_notify_subs SET {column} = ? " + _SUB_KEY_WHERE + guard,
                (value, *key),
            )


def _notify_profile_filter(
    notifier_profiles: Optional[Iterable[str]],
    *,
    include_unowned: bool,
) -> tuple[str, list[str]]:
    """Build an optional SQL predicate for notification profile ownership."""
    if notifier_profiles is None:
        return "", []

    profiles = sorted({str(p).strip() for p in notifier_profiles if str(p).strip()})
    clauses: list[str] = []
    params: list[str] = []
    if profiles:
        clauses.append("notifier_profile IN (" + ",".join("?" for _ in profiles) + ")")
        params.extend(profiles)
    if include_unowned:
        clauses.append("notifier_profile IS NULL OR notifier_profile = ''")
    if not clauses:
        return "0", []
    return "(" + ") OR (".join(clauses) + ")", params


def list_notify_subs(
    conn: sqlite3.Connection,
    task_id: Optional[str] = None,
    *,
    notifier_profiles: Optional[Iterable[str]] = None,
    include_unowned: bool = False,
) -> list[dict]:
    """List subscriptions, optionally restricted to notifier profile owners.

    No ``notifier_profiles`` -> all subscriptions. Gateway notifiers pass the
    profiles they own so they cannot claim another gateway's events;
    ``include_unowned`` (dispatch owner) covers legacy rows without a stamp.
    """
    owner_where, owner_params = _notify_profile_filter(
        notifier_profiles, include_unowned=include_unowned,
    )
    where: list[str] = []
    params: list[Any] = []
    if task_id is not None:
        where.append("task_id = ?")
        params.append(task_id)
    if owner_where:
        where.append(owner_where)
        params.extend(owner_params)
    sql = "SELECT * FROM kanban_notify_subs"
    if where:
        sql += " WHERE " + " AND ".join(f"({clause})" for clause in where)
    out: list[dict] = []
    for row in conn.execute(sql, params).fetchall():
        item = dict(row)
        if "delivery_metadata" in item:
            item["delivery_metadata"] = _decode_notify_delivery_metadata(item.get("delivery_metadata"))
        out.append(item)
    return out


def count_notify_subs(
    db_path: Optional[Path] = None,
    *,
    board: Optional[str] = None,
    notifier_profiles: Optional[Iterable[str]] = None,
    include_unowned: bool = False,
    platform: Optional[str] = None,
    chat_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    chat_ids: Optional[Iterable[str]] = None,
) -> int:
    """Count ``kanban_notify_subs`` rows via a read-only connection — the
    notifier's cheap zero-subscription early exit. Unlike :func:`connect` it
    never creates the file, runs init/migration or opens writable; WAL rows are
    still visible so a fresh sub is never missed. Missing DB / missing table
    counts as zero; platform matches case-insensitively (as notifier routing),
    chat/thread exactly (``chat_ids``: any of several). Raises :class:`sqlite3.Error` if the DB exists but is
    unreadable — callers pick their own fallback.
    """
    path = db_path if db_path is not None else _kb.kanban_db_path(board=board)
    if not path.exists():
        return 0
    owner_where, owner_params = _notify_profile_filter(
        notifier_profiles, include_unowned=include_unowned,
    )
    clauses: list[str] = []
    params: list[Any] = []
    if owner_where:
        clauses.append(f"({owner_where})")
        params.extend(owner_params)
    for clause, value in (
        ("LOWER(platform) = LOWER(?)", platform),
        ("chat_id = ?", chat_id),
        ("thread_id = ?", thread_id),
    ):
        if value is not None:
            clauses.append(clause)
            params.append(value)
    if chat_ids is not None:
        chat_list = list(chat_ids)
        if not chat_list:
            return 0
        clauses.append("chat_id IN (" + ",".join("?" * len(chat_list)) + ")")
        params.extend(chat_list)
    query = "SELECT COUNT(*) FROM kanban_notify_subs"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        try:
            row = conn.execute(query, params).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return 0
            raise
        return int(row[0]) if row else 0
    finally:
        conn.close()


def remove_notify_sub(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    platform: str,
    chat_id: str,
    thread_id: Optional[str] = None,
) -> bool:
    with _kb.write_txn(conn):
        cur = conn.execute(
            "DELETE FROM kanban_notify_subs " + _SUB_KEY_WHERE,
            _sub_key(task_id, platform, chat_id, thread_id),
        )
    return cur.rowcount > 0


def release_archived_notify_sub(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    platform: str,
    chat_id: str,
    thread_id: Optional[str] = None,
) -> bool:
    """Drop a subscription once its card is archived and the archival delivered, unless the idle-board announcement
    may still ride on it: the card took part in work the dispatcher has not yet ruled on (an orchestrator often
    archives its last card before the next tick), or an announcement landed on it after this delivery was claimed.

    The owning notifier calls it after delivering to an archived card and on every claim that found nothing new, so a
    held row is released by the first poll after the decision, carrier or not. Only the owner calls it, and only with
    none of its own deliveries for the row in flight, so a failed send can still rewind the cursor. Anything else is a
    cheap no-op (read-only check first). Returns True when removed."""
    key = _sub_key(task_id, platform, chat_id, thread_id)
    settled = ("SELECT 1 FROM kanban_notify_subs s JOIN tasks t ON t.id = s.task_id "
               "WHERE s.task_id = ? AND s.platform = ? AND s.chat_id = ? AND s.thread_id = ? AND t.status = 'archived'"
               " AND EXISTS (SELECT 1 FROM task_events a WHERE a.task_id = s.task_id AND a.kind = 'archived'"
               "             AND a.id <= s.last_event_id)")
    if not conn.execute(settled, key).fetchone():
        return False
    with _kb.write_txn(conn):
        if not conn.execute(settled, key).fetchone():
            return False
        if conn.execute(
            "SELECT 1 FROM task_events WHERE task_id = ? AND kind = 'claimed' AND id > ? LIMIT 1",
            (task_id, _quiescent_claim_mark(conn)),
        ).fetchone():
            return False
        if conn.execute(
            "SELECT 1 FROM task_events WHERE task_id = ? AND kind = 'board_quiescent' AND id > "
            "(SELECT last_event_id FROM kanban_notify_subs " + _SUB_KEY_WHERE + ") LIMIT 1",
            (task_id, *key),
        ).fetchone():
            return False
        cur = conn.execute("DELETE FROM kanban_notify_subs " + _SUB_KEY_WHERE, key)
    return cur.rowcount > 0


def purge_stale_done_notify_subs(conn: sqlite3.Connection, *, max_age_days: int = 30) -> int:
    """Delete notify subs whose task sat in ``done``/``blocked``/``archived`` untouched for
    longer than ``max_age_days`` (``<= 0`` disables); returns rows deleted.

    Subs survive ``done`` because a reopened task must still notify its origin,
    which accumulates forever on never-archiving boards. ``blocked`` is
    abandoned (unlike ``backlog``/``ready``) so it reaps on the same clock, and so
    does an ``archived`` row held for an idle-board decision that never came
    (``release_archived_notify_sub``). Age
    = latest event, else ``completed_at``, else ``created_at`` — any activity,
    including a reopen, exempts the sub.

    The notifier keeps subscriptions alive through ``done`` because a completed task can be reopened (review
    corrections, continuation) and the reopened cycle must still notify its origin session. On boards that
    never archive, that retention would otherwise accumulate subscription rows forever — each one scanned
    every notifier tick. This GC bounds that: a task that has been ``done`` with no new events for the
    retention window is treated as settled and its subscriptions are purged. ``blocked`` tasks
    (circuit-breaker trips, dead workers) are reaped on the same clock — they are abandoned, not idle,
    unlike a ``backlog``/``ready`` card that is merely waiting for pickup (#100955).
    """
    try:
        days = int(max_age_days)
    except (TypeError, ValueError):
        days = 30
    if days <= 0:
        return 0
    cutoff = int(time.time()) - days * 86400
    with _kb.write_txn(conn):
        cur = conn.execute(
            "DELETE FROM kanban_notify_subs WHERE task_id IN ("
            " SELECT t.id FROM tasks t"
            " WHERE t.status IN ('done', 'blocked', 'archived')"
            " AND COALESCE("
            "  (SELECT MAX(e.created_at) FROM task_events e"
            "   WHERE e.task_id = t.id),"
            "  t.completed_at, t.created_at, 0"
            " ) < ?)",
            (cutoff,),
        )
    return int(cur.rowcount or 0)


def _notify_cursor(
    conn: sqlite3.Connection, task_id: str, platform: str, chat_id: str, thread_id: Optional[str],
) -> Optional[int]:
    """``last_event_id`` of one subscription row, or ``None`` when unsubscribed."""
    row = conn.execute(
        "SELECT last_event_id FROM kanban_notify_subs " + _SUB_KEY_WHERE,
        _sub_key(task_id, platform, chat_id, thread_id),
    ).fetchone()
    return None if row is None else int(row["last_event_id"])


def unseen_events_for_sub(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    platform: str,
    chat_id: str,
    thread_id: Optional[str] = None,
    kinds: Optional[Iterable[str]] = None,
) -> tuple[int, list[Event]]:
    """Return ``(new_cursor, events)`` with ``id > last_event_id``. The cursor
    is NOT advanced here; call :func:`advance_notify_cursor` after delivery.
    """
    cursor = _notify_cursor(conn, task_id, platform, chat_id, thread_id)
    if cursor is None:
        return 0, []
    kind_list = list(kinds) if kinds else None
    q = (
        "SELECT * FROM task_events WHERE task_id = ? AND id > ? "
        + ("AND kind IN (" + ",".join("?" * len(kind_list)) + ") " if kind_list else "")
        + "ORDER BY id ASC"
    )
    params: list[Any] = [task_id, cursor]
    if kind_list:
        params.extend(kind_list)
    rows = conn.execute(q, params).fetchall()
    out = [_kb.Event.from_row(r) for r in rows]
    max_id = max([cursor, *(int(r["id"]) for r in rows)])
    return max_id, out


def claim_unseen_events_for_sub(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    platform: str,
    chat_id: str,
    thread_id: Optional[str] = None,
    kinds: Optional[Iterable[str]] = None,
) -> tuple[int, int, list[Event]]:
    """Atomically claim unseen events for one subscription.

    Returns ``(old_cursor, new_cursor, events)``; when events are returned the
    row's ``last_event_id`` has already been advanced inside ``BEGIN IMMEDIATE``,
    so concurrent gateway watchers on the same board DB serialize on SQLite's
    writer lock and only the first claims a given event range. Callers send the
    events, then leave the cursor or call :func:`rewind_notify_cursor` on
    delivery failure.
    """
    with _kb.write_txn(conn):
        old_cursor = _notify_cursor(conn, task_id, platform, chat_id, thread_id)
        if old_cursor is None:
            return 0, 0, []
        new_cursor, events = unseen_events_for_sub(
            conn, task_id=task_id, platform=platform, chat_id=chat_id,
            thread_id=thread_id, kinds=kinds,
        )
        if not events:
            return old_cursor, old_cursor, []
        _cas_cursor(conn, _sub_key(task_id, platform, chat_id, thread_id), new_cursor, old_cursor)
        return old_cursor, new_cursor, events


def _cas_cursor(conn: sqlite3.Connection, key: tuple, new_cursor: int, expected: int) -> sqlite3.Cursor:
    """Move ``last_event_id`` only if it still equals ``expected``."""
    return conn.execute(
        "UPDATE kanban_notify_subs SET last_event_id = ? " + _SUB_KEY_WHERE + " AND last_event_id = ?",
        (int(new_cursor), *key, int(expected)),
    )


def advance_notify_cursor(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    platform: str,
    chat_id: str,
    thread_id: Optional[str] = None,
    new_cursor: int,
) -> None:
    with _kb.write_txn(conn):
        conn.execute(
            "UPDATE kanban_notify_subs SET last_event_id = ? " + _SUB_KEY_WHERE,
            (int(new_cursor), *_sub_key(task_id, platform, chat_id, thread_id)),
        )


def record_notify_ping(
    conn: sqlite3.Connection, *, task_id: str, platform: str, chat_id: str,
    thread_id: Optional[str] = None, event_id: int,
) -> None:
    """Checkpoint a sent ping independently of the retryable wake cursor."""
    with _kb.write_txn(conn):
        conn.execute(
            "UPDATE kanban_notify_subs SET last_ping_event_id = MAX(last_ping_event_id, ?) "
            + _SUB_KEY_WHERE,
            (int(event_id), *_sub_key(task_id, platform, chat_id, thread_id)),
        )


def rewind_notify_cursor(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    platform: str,
    chat_id: str,
    thread_id: Optional[str] = None,
    claimed_cursor: int,
    old_cursor: int,
) -> bool:
    """Undo a claim when delivery fails. The CAS guard only rewinds if no later
    notifier advanced the row, so retries never clobber newer progress.
    """
    with _kb.write_txn(conn):
        cur = _cas_cursor(conn, _sub_key(task_id, platform, chat_id, thread_id), old_cursor, claimed_cursor)
    return cur.rowcount > 0


# A board is working while any card is in one of these; ``todo``/``blocked``/``triage``/``scheduled`` wait on a
# parent, a human or the clock, so a board holding only those has nothing left to run on its own. ``review`` counts
# only while the dispatcher spawns reviewers (``kanban.review_dispatch``); otherwise it waits on a human too.
_QUIESCENT_MARK_KEY = "quiescent_claim_mark"
_QUIESCENT_ATTENTION_LIMIT = 20


def _board_active_statuses() -> tuple[str, ...]:
    from hermes_cli.kanban_db_dispatch import review_dispatch_enabled
    return ("running", "ready", "review") if review_dispatch_enabled() else ("running", "ready")


def _quiescent_claim_mark(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM kanban_board_state WHERE key = ?", (_QUIESCENT_MARK_KEY,)).fetchone()
    return int(row[0]) if row else 0


def _newest_uncovered_claim(conn: sqlite3.Connection, active: tuple[str, ...]) -> Optional[tuple[int, int]]:
    """``(mark, newest claimed event id)`` when the board is idle and a card was claimed past the mark, else None.
    Both lookups are indexed (``tasks.status``, a rowid range over events newer than the mark)."""
    marks = ",".join("?" * len(active))
    if conn.execute(f"SELECT 1 FROM tasks WHERE status IN ({marks}) LIMIT 1", active).fetchone():
        return None
    mark = _quiescent_claim_mark(conn)
    newest = conn.execute(
        "SELECT MAX(id) FROM task_events WHERE id > ? AND kind = 'claimed'", (mark,)).fetchone()[0]
    return (mark, int(newest)) if newest else None


_QUIESCENT_SALT_KEY = "quiescent_tag_salt"
_WAKING_MODES = ("notify+wake", "wake")


def _quiescent_salt(conn: sqlite3.Connection, *, create: bool = False) -> bytes:
    """Board-local secret keying destination tags (a bare hash of a numeric chat id is trivially reversed). Created by
    the first announcement; export strips it with the subscriptions."""
    row = conn.execute("SELECT value FROM kanban_board_state WHERE key = ?", (_QUIESCENT_SALT_KEY,)).fetchone()
    if row is None:
        if not create:
            return b""
        import secrets
        conn.execute("INSERT OR IGNORE INTO kanban_board_state (key, value) VALUES (?, ?)",
                     (_QUIESCENT_SALT_KEY, secrets.randbits(62)))
        row = conn.execute("SELECT value FROM kanban_board_state WHERE key = ?", (_QUIESCENT_SALT_KEY,)).fetchone()
    return str(int(row[0])).encode()


def _destination(sub: Mapping[str, Any]) -> tuple[str, str, str]:
    """The chat/topic a subscription reports to. The notifier profile is not part of it: legacy rows carry NULL and
    get stamped on re-subscribe, and a NULL row is delivered by the dispatch owner, so it is not a recipient of its
    own. Profiles split a destination only when several are stamped on it (see ``announce_board_quiescent``)."""
    return (str(sub.get("platform") or "").lower(), str(sub.get("chat_id") or ""), str(sub.get("thread_id") or ""))


def _profile(sub: Mapping[str, Any]) -> str:
    return str(sub.get("notifier_profile") or "").strip()


def quiescent_destination_tag(conn: sqlite3.Connection, sub: Mapping[str, Any]) -> str:
    """Opaque tag of a subscription's chat/topic on this board. Events are exported and shown to workers, so the
    announcement carries this instead of a chat id or session key."""
    import hashlib
    import hmac
    msg = "\x1f".join(_destination(sub)).encode()
    return hmac.new(_quiescent_salt(conn), msg, hashlib.sha256).hexdigest()[:24]


def announce_board_quiescent(conn: sqlite3.Connection) -> list[str]:
    """Record one ``board_quiescent`` event per destination that took part in the work that just drained.

    Card events say "this card finished/blocked" but never "nothing is left to run", so a session supervising a board
    had to poll it. Fires when no card is active and a card was claimed past the board's claim mark
    (``kanban_board_state``, out of reach of event GC); every decision advances the mark, so a board that never ran
    anything stays silent and new work re-arms it.

    Recipients are the destinations (chat/topic, see ``_destination``) with a subscription on a card claimed since
    the mark; one on which several stamped notifier profiles took part is told once per profile (two bots in one
    group each run their own orchestrator). Each gets one event on a card it follows, addressed by an opaque tag
    (``payload["to"]``): a card whose subscription wakes the agent if any (so being woken never depends on which card
    finished last), then a live one over an archived one, then the most recently active. A card holds one row per
    chat/topic, so the tag picks exactly one follower of the carrier; a destination that is not split is carried only
    by an unstamped row or one of a participating profile (another bot's row would wake the wrong orchestrator).
    Notifiers hold an archived card's row past its archival while it took part in undecided work
    (``release_archived_notify_sub``), so an orchestrator that archived its last card is still told. The decision
    deletes nothing: a held row it put nothing on may still be mid-delivery (the claim advances the cursor before the
    send, and a failed send rewinds it), so the owning notifier releases it on its next empty claim. The gateway
    notifier and the desktop poller carry the event with no new subscription type; other followers of the carrier
    skip it. Returns the carrier task ids.
    """
    active = _board_active_statuses()
    if _newest_uncovered_claim(conn, active) is None:  # read-only fast path outside the writer lock
        return []
    with _kb.write_txn(conn):
        found = _newest_uncovered_claim(conn, active)  # re-check: another dispatcher may have decided meanwhile
        if found is None:
            return []
        mark, newest = found
        conn.execute("INSERT OR REPLACE INTO kanban_board_state (key, value) VALUES (?, ?)",
                     (_QUIESCENT_MARK_KEY, newest))
        subs = [dict(r) for r in conn.execute(
            "SELECT s.platform, s.chat_id, s.thread_id, s.notifier_profile, s.delivery_mode, s.task_id,"
            " t.status = 'archived' AS archived,"
            " (SELECT COALESCE(MAX(e.id), 0) FROM task_events e WHERE e.task_id = s.task_id) AS latest,"
            " EXISTS (SELECT 1 FROM task_events c WHERE c.task_id = s.task_id AND c.kind = 'claimed'"
            "         AND c.id > ? AND c.id <= ?) AS took_part"
            " FROM kanban_notify_subs s JOIN tasks t ON t.id = s.task_id", (mark, newest))]
        profiles: dict = {}
        for s in subs:
            if s["took_part"]:
                profiles.setdefault(_destination(s), set()).add(_profile(s))
        recipients = {(dest, prof) for dest, profs in profiles.items()
                      for prof in (sorted(profs - {""}) or [""])}
        split = {dest for dest, profs in profiles.items() if len(profs - {""}) > 1}
        best: dict = {}
        for s in subs:
            dest, prof = _destination(s), _profile(s)
            if dest in split:
                recipient = (dest, prof)  # an unstamped row cannot say which of the bots it belongs to
            elif prof and prof not in profiles.get(dest, ()):
                continue  # another bot's row in the same chat: its gateway would wake the wrong orchestrator
            else:
                recipient = next((r for r in recipients if r[0] == dest), None)
            if recipient not in recipients:
                continue
            rank = ((s["delivery_mode"] or "notify") in _WAKING_MODES, not s["archived"], s["latest"])
            if recipient not in best or rank > best[recipient][0]:
                best[recipient] = (rank, s)
        if best:
            _append_board_quiescent(conn, best, active, newest)
    return sorted({s["task_id"] for _rank, s in best.values()})


def _append_board_quiescent(conn: sqlite3.Connection, best: Mapping, active: tuple[str, ...], newest: int) -> None:
    """Write one announcement per recipient (``best``: recipient → (rank, carrier sub)). Caller holds write_txn."""
    waiting = ("blocked", "triage", "scheduled") + (() if "review" in active else ("review",))
    counts = {r[0]: r[1] for r in conn.execute(
        "SELECT status, COUNT(*) FROM tasks WHERE status != 'archived' GROUP BY status ORDER BY status")}
    attention = [r[0] for r in conn.execute(
        f"SELECT id FROM tasks WHERE status IN ({','.join('?' * len(waiting))})"
        " ORDER BY priority DESC, created_at LIMIT ?", (*waiting, _QUIESCENT_ATTENTION_LIMIT))]
    _quiescent_salt(conn, create=True)
    for _recipient, (_rank, s) in sorted(best.items()):
        _kb._append_event(conn, s["task_id"], "board_quiescent", {
            "counts": counts, "attention": attention, "mark": newest,
            "to": quiescent_destination_tag(conn, s),
        })


def quiescent_addressed_to(conn: sqlite3.Connection, ev: Any, sub: Mapping[str, Any]) -> bool:
    """False for a ``board_quiescent`` event addressed to a different destination than ``sub`` on this board: it
    rides on a card several destinations may follow, and only its addressee is told. Everything else is True."""
    if getattr(ev, "kind", "") != "board_quiescent":
        return True
    to = (getattr(ev, "payload", None) or {}).get("to")
    if not isinstance(to, str) or not to:
        return True
    import hmac
    return hmac.compare_digest(to, quiescent_destination_tag(conn, sub))


def describe_board_quiescent(payload: Mapping[str, Any]) -> str:
    """One-line human summary of a ``board_quiescent`` payload, shared by every notifier."""
    raw_counts = payload.get("counts")
    counts: Mapping[str, Any] = raw_counts if isinstance(raw_counts, Mapping) else {}
    tally = ", ".join(f"{int(n)} {status}" for status, n in counts.items() if isinstance(n, int))
    attention = [str(t) for t in (payload.get("attention") or []) if t][:_QUIESCENT_ATTENTION_LIMIT]
    text = "board has no work left to run" + (f" ({tally})" if tally else "")
    if attention:
        text += "; needs attention: " + ", ".join(attention)
    return text


# Late-bound origin namespace (see module docstring); imported LAST so this
# module is fully populated before ``kanban_db`` imports from it.
from hermes_cli import kanban_db as _kb  # noqa: E402
