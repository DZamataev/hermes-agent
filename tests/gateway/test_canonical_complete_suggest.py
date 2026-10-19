"""The composer ghost on the shared owner: Desktop's local turns run here, so the owner computes the
suggestion when a turn completes and answers ``complete.suggest``. Without it the canonical
connection refused the verb and the ghost never appeared."""
import asyncio
import json

import pytest

from tests.gateway.test_session_busy_controls import owner, settled
from tests.gateway.fixtures.local_recovery_probe import rpc, websocket

_MARK = 'Composer prediction request'


@pytest.mark.platforms("linux", "macos")
def test_a_completed_local_turn_gets_a_prediction_the_composer_reads(tmp_path):
    with owner(tmp_path) as (home, peer, desc):
        async def probe():
            async with websocket(home, desc) as ws:
                sid = (await rpc(ws, 'session.create', request_id='ghost', source='tui', toolsets=[]))['result']['session_id']
                # Nothing completed yet: an empty answer, never a model request on demand.
                assert (await rpc(ws, 'complete.suggest', session_id=sid))['result'] == {'candidates': []}
                # A prediction needs a conversation to continue: two exchanges.
                for n in ('one', 'two'):
                    await rpc(ws, 'prompt.submit', session_id=sid, input_id=n, text=f'TURN_{n}')
                    await settled(home)
                reply = await rpc(ws, 'complete.suggest', session_id=sid)
                assert 'error' not in reply, reply
                predictions = [r for r in peer.requests if _MARK in json.dumps(r['messages'])]
                assert len(predictions) == 1, [r['messages'][-1] for r in peer.requests]
                # The fork replays the conversation, so it rides the turn's warm cache.
                assert 'TURN_two' in json.dumps(predictions[0]['messages'])
                denied = await rpc(ws, 'complete.suggest', session_id=sid, extra='x')
                assert denied.get('error', {}).get('message') == 'invalid_params', denied
        asyncio.run(probe())
