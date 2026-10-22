import asyncio

import httpx
import pytest
from starlette.requests import Request

from portal.main import create_app


@pytest.mark.parametrize('failure', ['disconnect_before_body', 'cancelled_release', 'close_error', 'client_init_error', 'cancel_before_body', 'disconnect_mid_body'])
def test_proxy_failure_never_leaves_account_operations_locked(tmp_path, monkeypatch, failure):
    class Broker:
        async def acquire(self, *args, **kwargs):
            return {'url': 'http://worker', 'key': 'fixture', 'lease': 'fixture'}
        async def release(self, *args):
            if failure == 'cancelled_release':
                raise asyncio.CancelledError()
        async def stop(self, *args):
            pass
    app = create_app(tmp_path, 'fixture-password-long', 'http://fixture', Broker())
    store = app.state.store
    aid = store.add_account('admin', 'fixture', {})
    key = store.create_key('admin', 'fixture')['key']
    token, csrf = store.login('admin')
    route = next(r for r in app.routes if getattr(r, 'path', '') == '/v1/{rest:path}')
    real = httpx.AsyncClient
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'fixture'
        async def aclose(self):
            if failure == 'close_error':
                raise RuntimeError('fixture close failed')
    def factory(**kw):
        if failure == 'client_init_error':
            raise RuntimeError('fixture client init failed')
        return real(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Body())), **kw)
    monkeypatch.setattr(httpx, 'AsyncClient', factory)
    async def exercise():
        scope = {'type': 'http', 'method': 'POST', 'path': '/v1/chat/completions', 'query_string': b'',
                 'headers': [(b'authorization', ('Bearer ' + key).encode())], 'asgi': {'spec_version': '2.4'}}
        async def receive():
            return {'type': 'http.request', 'body': b'{"model":"fixture"}', 'more_body': False}
        response = None
        try:
            response = await route.endpoint('chat/completions', Request(scope, receive))
        except Exception:
            assert failure == 'client_init_error'
        async def send(message):
            if failure == 'disconnect_before_body' and message['type'] == 'http.response.start':
                raise OSError('fixture disconnected before iteration')
            if failure == 'cancel_before_body' and message['type'] == 'http.response.start':
                raise asyncio.CancelledError()
            if failure == 'disconnect_mid_body' and message['type'] == 'http.response.body':
                raise OSError('fixture disconnected during body')
        try:
            if response is not None:
                await response(scope, receive, send)
        except BaseException:
            pass
        # Fresh HTTP request stands for refresh/re-login; it must not inherit a leaked API lock.
        async with real(transport=httpx.ASGITransport(app=app), base_url='http://fixture', cookies={'portal_session': token}, headers={'X-CSRF-Token': csrf}) as client:
            result = await client.patch('/api/accounts/' + aid, json={'label': 'saved'})
            assert result.status_code == 200, result.text
    asyncio.run(exercise())
