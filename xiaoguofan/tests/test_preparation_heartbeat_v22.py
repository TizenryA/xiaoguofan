import asyncio
import json

import httpx

from portal.main import create_app
from portal.streaming import with_heartbeats


def test_preparation_heartbeat_then_real_content_and_lease_cleanup(tmp_path, monkeypatch):
    async def run():
        gate = asyncio.Event()
        released = []
        class Broker:
            async def acquire(self, *args, **kwargs):
                await gate.wait()
                return {'url': 'http://fixture', 'key': 'fixture', 'lease': 'lease'}
            async def release(self, uid, lease):
                released.append(lease)
        app = create_app(tmp_path, 'fixture-long-password', 'https://fixture.test', Broker())
        store = app.state.store
        store.save_settings({'public_login': True})
        u = store.discord_user('111', 'Alice')
        store.add_account(u['id'], 'fixture', {})
        key = store.create_key(u['id'], 'fixture')['key']
        original_client = httpx.AsyncClient
        body = b'data: {"choices":[{"delta":{"reasoning_content":"think","content":"answer"}}]}\n\ndata: [DONE]\n\n'
        transport = httpx.MockTransport(lambda request: httpx.Response(200, headers={'Content-Type': 'text/event-stream'}, content=body))
        monkeypatch.setattr('portal.main.httpx.AsyncClient', lambda **kwargs: original_client(transport=transport, **kwargs))
        monkeypatch.setattr('portal.streaming.with_heartbeats', lambda source, interval: with_heartbeats(source, interval=.001))
        incoming = asyncio.Queue()
        payload = json.dumps({'model': 'gemini-fixture-pg', 'stream': True, 'messages': []}).encode()
        await incoming.put({'type': 'http.request', 'body': payload, 'more_body': False})
        messages = []
        async def send(message):
            messages.append(message)
            if message.get('body') == b': ping\n\n':
                assert not gate.is_set() or any(m.get('body') == b': ping\n\n' for m in messages[:-1])
                gate.set()
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'POST',
                 'scheme': 'https', 'path': '/v1/chat/completions', 'raw_path': b'/v1/chat/completions',
                 'query_string': b'', 'root_path': '', 'server': ('fixture.test', 443), 'client': ('127.0.0.1', 1234),
                 'headers': [(b'host', b'fixture.test'), (b'authorization', ('Bearer '+key).encode()), (b'content-type', b'application/json')]}
        await asyncio.wait_for(app(scope, incoming.get, send), 5)
        chunks = b''.join(m.get('body', b'') for m in messages)
        assert chunks.startswith(b': ping\n\n')
        assert b'"reasoning_content":"think"' in chunks and b'"content":"answer"' in chunks
        assert released == ['lease']
        assert app.state.scheduler.snapshot()['active'] == 0
    asyncio.run(run())
