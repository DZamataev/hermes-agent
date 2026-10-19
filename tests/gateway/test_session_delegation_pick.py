"""The composer's Subagents pick on the shared owner: Desktop's local sessions run on ``gateway run``.

``config.set key=delegation`` was served only by the tui_gateway sidecar; the canonical adapter refused it
before the wire, so a local session's pick silently stayed Auto. The owner now stores it on the
conversation's row, reports it in ``session.info`` and ``delegate_task`` routes every child to it.
"""
import asyncio
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.gateway.fixtures.local_recovery_probe import child_env, daemon, rpc, websocket
from tests.gateway.test_session_busy_controls import admissions, settled

SUB_MODEL = 'picked-subagent-model'


class DelegatingModel(BaseHTTPRequestHandler):
    """``DELEGATE`` calls ``delegate_task``; everything else (the child included) answers ``ACK``."""

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        messages = body.get('messages', [])
        if messages:
            self.server.requests.append(body)
        last = messages[-1] if messages else {}
        message, finish = {'role': 'assistant', 'content': 'ACK'}, 'stop'
        if last.get('role') == 'user' and last.get('content') == 'DELEGATE':
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': 'sub', 'type': 'function', 'function': {
                    'name': 'delegate_task', 'arguments': json.dumps({'goal': 'say ACK'})}}]}
            finish = 'tool_calls'
        if body.get('stream'):
            delta = dict(message)
            if 'tool_calls' in delta:
                delta['tool_calls'] = [dict(delta['tool_calls'][0], index=0)]
            payload = ('data: ' + json.dumps({'id': 'd', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]})
                       + '\n\ndata: [DONE]\n\n').encode()
            kind = 'text/event-stream'
        else:
            payload = json.dumps({'id': 'd', 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                                  'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}).encode()
            kind = 'application/json'
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@contextmanager
def owner(tmp_path):
    root = Path(__file__).resolve().parents[2]
    home, user = tmp_path / 'state', tmp_path / 'user'
    home.mkdir(mode=0o700)
    user.mkdir()
    peer = ThreadingHTTPServer(('127.0.0.1', 0), DelegatingModel)
    peer.requests = []
    thread = threading.Thread(target=peer.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{peer.server_port}/v1'
    (home / 'config.yaml').write_text(json.dumps({
        'gateway': {'multiplex_profiles': False},
        'model': {'provider': 'custom', 'default': 'parent-model', 'base_url': url},
        'auxiliary': {'title_generation': {'enabled': False}}}))
    env = child_env()
    env.update(HOME=str(user), USERPROFILE=str(user), HERMES_HOME=str(home), PYTHONPATH=str(root),
               OPENAI_API_KEY='loopback-only', OPENAI_BASE_URL=url, PYTHONUNBUFFERED='1')
    try:
        with daemon(root, home, env, barrier=False) as (_, desc):
            yield home, peer, desc
    finally:
        peer.shutdown()
        peer.server_close()
        thread.join(timeout=5)


@pytest.mark.platforms("linux", "macos")
def test_the_subagents_pick_of_a_local_session_routes_its_children(tmp_path):
    with owner(tmp_path) as (home, peer, desc):
        async def probe():
            async with websocket(home, desc) as ws:
                sid = (await rpc(ws, 'session.create', request_id='pick', source='gui',
                                 toolsets=['delegation']))['result']['session_id']
                picked = await rpc(ws, 'config.set', session_id=sid, key='delegation',
                                   value={'provider': '', 'model': SUB_MODEL, 'reasoning_effort': ''})
                assert picked.get('result', {}).get('value') == SUB_MODEL, picked
                info = (await rpc(ws, 'session.info', session_id=sid))['result']
                assert info.get('delegation_override', {}).get('model') == SUB_MODEL, info

                await rpc(ws, 'prompt.submit', session_id=sid, input_id='go', text='DELEGATE')
                await settled(home)
                models = [r.get('model') for r in peer.requests]
                assert models[0] == 'parent-model' and SUB_MODEL in models, models

                cleared = await rpc(ws, 'config.set', session_id=sid, key='delegation', value='auto')
                assert cleared.get('result', {}).get('value') == 'auto', cleared
                assert not (await rpc(ws, 'session.info', session_id=sid))['result'].get('delegation_override')
                peer.requests.clear()
                await rpc(ws, 'prompt.submit', session_id=sid, input_id='again', text='DELEGATE')
                await settled(home)
                assert SUB_MODEL not in [r.get('model') for r in peer.requests]
                assert len(admissions(home)) == 2

                bad = await rpc(ws, 'config.set', session_id=sid, key='delegation', value={'model': 5})
                assert bad.get('error', {}).get('message') == 'invalid_params', bad
        asyncio.run(probe())


def test_an_unstamped_agent_reads_the_pick_across_a_compression_rotation(tmp_path):
    """The owner's agent carries no ``_delegation_override``: it reads the conversation's row, and a
    rotation carries the pick onto the continuation (a sidecar-stamped Auto still means Auto)."""
    from types import SimpleNamespace

    from hermes_state import SessionDB
    from tools.delegate_tool_routes import _session_route
    pick = {'provider': '', 'model': SUB_MODEL, 'reasoning_effort': ''}
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        db.create_session('root', source='tui')
        db.update_conversation_model_config_value('root', 'delegation_override', lambda _old: pick)
        assert db.try_acquire_compression_lock('root', 'holder')
        db.publish_compression_child(parent_session_id='root', child_session_id='tip', source='tui',
                                     messages=[{'role': 'user', 'content': 'go'}], model='m',
                                     compression_lock_holder='holder')
        assert _session_route(SimpleNamespace(session_id='tip', _session_db=db))['model'] == SUB_MODEL
        assert _session_route(SimpleNamespace(session_id='root', _session_db=db))['model'] == SUB_MODEL
        assert _session_route(SimpleNamespace(session_id='tip', _session_db=db, _delegation_override=None)) is None
