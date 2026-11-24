import asyncio

import httpx
import pytest
from engine_fixture import FakeEngine, cookie_payload, portal


@pytest.mark.parametrize('failure', ['engine_down', 'disconnect_before_body', 'cancel_before_body', 'disconnect_mid_body', 'ok'])
def test_proxy_failure_never_leaves_slots_or_account_operations_locked(tmp_path, failure):
    fake = FakeEngine().start()
    app, store, alice, bob, _ = portal(tmp_path, fake)
    aid = store.add_account(alice['id'], 'fixture', cookie_payload('alice@gmail.com'))
    key = store.create_key(alice['id'], 'fixture')['key']
    token, csrf = store.login(alice['id'])
    if failure == 'engine_down':
        fake.stop()

    async def exercise():
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'POST', 'scheme': 'https',
                 'path': '/v1/chat/completions', 'raw_path': b'/v1/chat/completions', 'query_string': b'', 'root_path': '',
                 'server': ('portal.example', 443), 'client': ('127.0.0.1', 1),
                 'headers': [(b'host', b'portal.example'), (b'authorization', ('Bearer '+key).encode()), (b'content-type', b'application/json')]}
        sent = []

        async def receive():
            if not sent:
                sent.append(1)
                return {'type': 'http.request', 'body': b'{"model":"fixture","messages":[]}', 'more_body': False}
            await asyncio.sleep(3600)

        async def send(message):
            if failure == 'disconnect_before_body' and message['type'] == 'http.response.start':
                raise OSError('fixture disconnected before iteration')
            if failure == 'cancel_before_body' and message['type'] == 'http.response.start':
                raise asyncio.CancelledError()
            if failure == 'disconnect_mid_body' and message['type'] == 'http.response.body':
                raise OSError('fixture disconnected during body')
        try:
            await asyncio.wait_for(app(scope, receive, send), 30)
        except BaseException:
            pass
        assert app.state.scheduler.snapshot()['active'] == 0
        assert app.state.scheduler.snapshot()['queued'] == 0
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://portal.example',
                                     cookies={'portal_session': token}, headers={'X-CSRF-Token': csrf}) as client:
            result = await client.patch('/api/accounts/'+aid, json={'label': 'saved'})
            assert result.status_code == 200, result.text
    try:
        asyncio.run(exercise())
        assert store.one('SELECT COUNT(*) n FROM requests')['n'] == 1
    finally:
        fake.stop()
