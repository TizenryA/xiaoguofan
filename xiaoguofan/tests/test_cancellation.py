import asyncio

import pytest

from portal.main import create_app


def test_cancelled_worker_start_releases_tenant_lease(tmp_path):
    class Broker:
        async def ensure(self, *args):
            raise asyncio.CancelledError()
    app = create_app(tmp_path, 'test-password-long-enough', 'https://portal.example', Broker())
    s = app.state.store
    s.add_account('admin', 'a', {'storage_state': {'cookies': []}})
    key = s.create_key('admin', 'k')['key']
    route = next(r for r in app.routes if getattr(r,'path','') == '/v1/{rest:path}')
    from starlette.requests import Request
    async def exercise():
        for _ in range(2):
            r = Request({'type':'http', 'method':'GET','path':'/v1/models','query_string':b'',
                         'headers':[(b'authorization', ('Bearer '+key).encode())]})
            async def receive():
                return {'type':'http.request', 'body':b'', 'more_body':False}
            r._receive = receive
            try:
                await route.endpoint('models', r)
            except asyncio.CancelledError:
                continue
            pytest.fail('Cancellation was replaced by locked/busy response')
    asyncio.run(exercise())
