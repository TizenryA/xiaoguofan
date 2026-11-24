import asyncio
import json

from engine_fixture import FakeEngine, cookie_payload, portal

from portal.streaming import with_heartbeats


def test_preparation_heartbeat_then_real_content_and_slot_cleanup(tmp_path, monkeypatch):
    fake = FakeEngine().start()

    async def run():
        gate = asyncio.Event()
        app, store, alice, bob, _ = portal(tmp_path, fake)
        store.add_account(alice['id'], 'fixture', cookie_payload('alice@gmail.com'))
        key = store.create_key(alice['id'], 'fixture')['key']
        engine = app.state.engine
        original = engine.ensure_ready

        async def slow_ready(uid, timeout=60):
            await gate.wait()
            return await original(uid, timeout)
        monkeypatch.setattr(engine, 'ensure_ready', slow_ready)
        monkeypatch.setattr('portal.streaming.with_heartbeats', lambda source, interval: with_heartbeats(source, interval=.001))
        incoming = asyncio.Queue()
        payload = json.dumps({'model': 'gemini-fixture-pg', 'stream': True, 'messages': []}).encode()
        await incoming.put({'type': 'http.request', 'body': payload, 'more_body': False})
        messages = []

        async def send(message):
            messages.append(message)
            if message.get('body') == b': ping\n\n':
                gate.set()
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'POST',
                 'scheme': 'https', 'path': '/v1/chat/completions', 'raw_path': b'/v1/chat/completions',
                 'query_string': b'', 'root_path': '', 'server': ('portal.example', 443), 'client': ('127.0.0.1', 1234),
                 'headers': [(b'host', b'portal.example'), (b'authorization', ('Bearer '+key).encode()), (b'content-type', b'application/json')]}
        await asyncio.wait_for(app(scope, incoming.get, send), 10)
        chunks = b''.join(m.get('body', b'') for m in messages)
        assert chunks.startswith(b': ping\n\n')
        assert '片段0'.encode() in chunks and b'[DONE]' in chunks
        assert app.state.scheduler.snapshot()['active'] == 0
        # The -pg suffix is stripped before reaching the engine.
        assert fake.seen[-1][0] == '/v1/chat/completions'
    try:
        asyncio.run(run())
    finally:
        fake.stop()
