"""The spawn-task chip's hand-back on the shared owner (main's sidecar: tui_gateway/prompt_turn.py and
tui_gateway/session_notifications.py::_notif_poll_spawn_reports).

A chip session created with ``spawn_report_to`` (gateway/session_controls.py) drops one report into the
profile's mailbox (tools/spawn_task_reports.py) after its first finished turn; the owner then admits every
report addressed to one of its local sessions as a notification turn on that session's FIFO. A report for
a session this owner does not serve (a sidecar's, or a deleted one) stays for its own poller or the TTL.
"""
import asyncio
import logging
from functools import partial

from gateway.config import Platform

logger = logging.getLogger(__name__)


async def after_local_turn(runner, *, session_entry, source, final_response):
    """Post-turn hook (gateway/run_goals.py::_run_post_turn_hooks) of a finished local turn."""
    from gateway.session_authorities import active_authority
    from hermes_constants import get_hermes_home
    authority = active_authority(runner)
    if authority is None or getattr(source, 'platform', None) != Platform.LOCAL:
        return
    home = get_hermes_home()
    await _report(authority, home, source.chat_id)
    await deliver_reports(runner, authority, home)


async def _report(authority, home, sid):
    """Once only: later turns are the user working in the side task. A failed write stays armed, so
    the next finished turn retries."""
    from gateway.session_runtime_workers import tracked_write
    from tools.spawn_task_reports import write_report
    db = authority.db
    target = await asyncio.to_thread(db.get_session_model_config_value, sid, 'spawn_report_to')
    if not target:
        return
    try:
        title = await asyncio.to_thread(db.get_session_title, sid)
        await asyncio.to_thread(write_report, home, to_session=target, from_session=sid, title=title or '')
    except Exception:  # health: allow BLE001 -- stays armed for the next finished turn; traceback logged
        logger.warning('spawn-task report for %s not written; retried after the next turn', sid, exc_info=True)
        return
    await tracked_write(authority, partial(db.patch_session_model_config, sid, {'spawn_report_to': None}))


async def deliver_reports(runner, authority, home):
    from gateway.session_automation import admit_route_wake
    from gateway.session_local_recovery import restore_local_session
    from tools.spawn_task_reports import (claim_report, display_text, finish_report, format_report,
                                          pending_reports, release_report)
    for path, record in await asyncio.to_thread(pending_reports, home):
        # The offering session's local id: it stays the session's id across compression.
        sid = record['to']
        try:
            ref = restore_local_session(authority, sid)
        except Exception:  # health: allow BLE001 -- not this owner's session: left for its own poller
            logger.debug('spawn-task report for %s left in the mailbox', sid, exc_info=True)
            continue
        if (claimed := claim_report(path)) is None:
            continue  # another poller took it
        live = authority.sessions[ref.session_id]
        try:
            admitted = await admit_route_wake(
                runner, runner._delivery_adapter_for(live.source), live.source, format_report(record), 'spawn-report',
                {'display_kind': 'async_delegation_complete', 'display_text': display_text(record)})
        except Exception:  # health: allow BLE001 -- the report goes back to the mailbox; traceback logged
            logger.warning('spawn-task report for %s not admitted; kept for a retry', sid, exc_info=True)
            admitted = False
        (finish_report if admitted else release_report)(claimed)
