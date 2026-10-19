"""The spawn-task chip on the shared owner: Desktop's local sessions run on ``gateway run``.

The chip creates its session with ``title_dedupe`` (a model-proposed title must never reopen the
user's session of that name) and ``spawn_report_to`` (the offering session hears when the side task
finishes its first turn). Both were served only by the tui_gateway sidecar, so the owner refused
the create and the chip could not open a session at all.
"""
import asyncio
import json
import sqlite3

import pytest

from tests.gateway.fixtures.local_recovery_probe import rpc, websocket
from tests.gateway.test_session_busy_controls import admissions, owner, settled
from tests.waiting import async_wait_until


def _row(home, sid):
    with sqlite3.connect(f'file:{home / "state.db"}?mode=ro', uri=True) as db:
        title, config = db.execute('SELECT title, model_config FROM sessions WHERE id = ?', (sid,)).fetchone()
    return title, json.loads(config or '{}')


@pytest.mark.platforms("linux", "macos")
def test_chip_session_gets_a_fresh_deduped_title_and_reports_to_the_offering_session(tmp_path):
    with owner(tmp_path) as (home, _peer, desc):
        async def probe():
            async with websocket(home, desc) as ws:
                async def create(name, **params):
                    reply = await rpc(ws, 'session.create', request_id=name, source='gui', toolsets=[], **params)
                    assert 'result' in reply, reply
                    return reply['result']['session_id']
                parent = await create('parent')
                await rpc(ws, 'prompt.submit', session_id=parent, input_id='warm', text='PARENT_TURN')
                await settled(home)
                taken = await create('taken', title='Fix login')
                child = await create('chip', title='Fix login', title_dedupe=True, spawn_report_to=parent)
                assert child not in (parent, taken)
                title, config = _row(home, child)
                assert title == 'Fix login (2)' and config.get('spawn_report_to') == parent, (title, config)

                await rpc(ws, 'prompt.submit', session_id=child, input_id='side', text='SIDE_TASK')
                await async_wait_until(lambda: len(admissions(home)) == 3, timeout=40, interval=.05,
                                       what='the report to be admitted on the offering session')
                await settled(home)
                assert 'spawn_report_to' not in _row(home, child)[1]
                assert not list((home / 'spawn_task_reports').glob('*'))
                users = [m['content'] for m in (await rpc(ws, 'session.resume', session_id=parent))['result']['messages']
                         if m.get('role') == 'user']
                assert users[0] == 'PARENT_TURN' and users[1].startswith('[SPAWN TASK RESULT — "Fix login (2)"]'), users
                assert child in users[1]

                # A second finished turn is the user working in the side task: no second report.
                await rpc(ws, 'prompt.submit', session_id=child, input_id='more', text='MORE')
                await settled(home)
                assert len(admissions(home)) == 4

                for params in ({'title': 'x', 'title_dedupe': 'yes'}, {'spawn_report_to': 3}):
                    denied = await rpc(ws, 'session.create', request_id='bad', source='gui', toolsets=[], **params)
                    assert denied.get('error', {}).get('message') == 'invalid_params', denied
        asyncio.run(probe())
