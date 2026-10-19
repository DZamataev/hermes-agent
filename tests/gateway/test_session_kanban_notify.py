"""Kanban card events reach the Desktop session that created the card, on the shared owner.

``kanban_create`` subscribes the calling session (``tools/kanban_tools.py::_resolve_notify_target``).
Desktop's local sessions run on ``gateway run``; the notifier there must wake THAT session with the
card's event as a notification turn.
"""
import asyncio
import json
import sqlite3
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.gateway.fixtures.local_recovery_probe import child_env, daemon, rpc, websocket
from tests.waiting import async_wait_until


class CardModel(BaseHTTPRequestHandler):
    """``MAKE_CARD`` calls ``kanban_create``; everything else answers ``ACK``."""

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        messages = body.get('messages', [])
        if messages:
            self.server.requests.append(body)
        last = messages[-1] if messages else {}
        message, finish = {'role': 'assistant', 'content': 'ACK'}, 'stop'
        if last.get('role') == 'user' and last.get('content') == 'MAKE_CARD':
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': 'card', 'type': 'function', 'function': {
                    'name': 'kanban_create', 'arguments': json.dumps({'title': 'probe card', 'assignee': 'ghost'})}}]}
            finish = 'tool_calls'
        if body.get('stream'):
            delta = dict(message)
            if 'tool_calls' in delta:
                delta['tool_calls'] = [dict(delta['tool_calls'][0], index=0)]
            payload = ('data: ' + json.dumps({'id': 'k', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]})
                       + '\n\ndata: [DONE]\n\n').encode()
            kind = 'text/event-stream'
        else:
            payload = json.dumps({'id': 'k', 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
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
    peer = ThreadingHTTPServer(('127.0.0.1', 0), CardModel)
    peer.requests = []
    thread = threading.Thread(target=peer.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{peer.server_port}/v1'
    (home / 'config.yaml').write_text(json.dumps({
        'gateway': {'multiplex_profiles': False},
        'model': {'provider': 'custom', 'default': 'kanban-fixture', 'base_url': url},
        'auxiliary': {'title_generation': {'enabled': False}},
        'kanban': {'dispatch_in_gateway': False}}))
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


def _admissions(home):
    with sqlite3.connect(f'file:{home / "state.db"}?mode=ro', uri=True) as db:
        return list(db.execute('SELECT request_id, status FROM session_admissions ORDER BY seq'))


def _kanban(home, sql):
    with sqlite3.connect(f'file:{home / "kanban.db"}?mode=ro', uri=True) as db:
        return list(db.execute(sql))


@pytest.mark.platforms("linux", "macos")
def test_a_card_event_wakes_the_local_session_that_created_it(tmp_path, monkeypatch):
    with owner(tmp_path) as (home, _peer, desc):
        async def probe():
            async with websocket(home, desc) as ws:
                sid = (await rpc(ws, 'session.create', request_id='orch', source='gui',
                                 toolsets=['kanban']))['result']['session_id']
                await rpc(ws, 'prompt.submit', session_id=sid, input_id='make', text='MAKE_CARD')
                await async_wait_until(lambda: (rows := _admissions(home)) and rows[-1][1] == 'terminal',
                                       timeout=40, interval=.1, what='the card turn to settle')
                subs = _kanban(home, 'SELECT task_id, platform, chat_id, delivery_mode FROM kanban_notify_subs')
                assert len(subs) == 1 and subs[0][1:] == ('local', sid, 'notify+wake'), subs
                task = subs[0][0]
                monkeypatch.setenv('HERMES_HOME', str(home))
                from hermes_cli import kanban_db as kb
                from hermes_cli import kanban_db_connect as kbc
                conn = kbc.connect()
                try:
                    assert kb.complete_task(conn, task, summary='probe finished')
                finally:
                    conn.close()
                # Before the fix the notifier re-claimed and rewound this event every tick, silently.
                await async_wait_until(lambda: len(_admissions(home)) >= 2, timeout=40, interval=.2,
                                       what='the card event to wake the session')
                await async_wait_until(lambda: all(r[1] == 'terminal' for r in _admissions(home)),
                                       timeout=40, interval=.1, what='the wake turn to settle')
                users = [m['content'] for m in (await rpc(ws, 'session.resume', session_id=sid))['result']['messages']
                         if m.get('role') == 'user']
                assert users[0] == 'MAKE_CARD' and task in users[1] and 'probe finished' in users[1], users
        asyncio.run(probe())
