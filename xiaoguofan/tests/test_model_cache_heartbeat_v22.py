import asyncio
import json

import pytest
from portal.model_cache import ModelCache
from portal.store import Store
from portal.streaming import with_heartbeats


def test_cache_persists_and_is_owner_scoped(tmp_path):
    async def run():
        store = Store(tmp_path, 'fixture-long-password')
        cache = ModelCache(store)
        calls = []
        async def load():
            calls.append(1)
            return {'data': [{'id': 'fixture'}]}
        assert await cache.get('alice', 'v1', load) == await cache.get('alice', 'v1', load)
        restored = ModelCache(store)
        await restored.get('alice', 'v1', load)
        assert len(calls) == 1
        await restored.get('bob', 'v1', load)
        await restored.get('alice', 'v2', load)
        assert len(calls) == 3
    asyncio.run(run())


def test_stale_cache_returns_immediately_and_coalesces_refresh(tmp_path):
    async def run():
        cache = ModelCache(Store(tmp_path, 'fixture-long-password'), ttl=-1)
        cache.put('alice', 'v1', {'data': [{'id': 'old'}]})
        gate = asyncio.Event()
        calls = []
        async def load():
            calls.append(1)
            await gate.wait()
            return {'data': [{'id': 'new'}]}
        for _ in range(3):
            assert (await cache.get('alice', 'v1', load))['data'][0]['id'] == 'old'
        await asyncio.sleep(0)
        assert len(calls) == 1
        task = next(iter(cache.tasks.values()))
        gate.set()
        await task
        cache.ttl = 1800
        assert (await cache.get('alice', 'v1', load))['data'][0]['id'] == 'new'
    asyncio.run(run())


def test_heartbeat_does_not_cancel_delayed_source():
    async def run():
        gate = asyncio.Event()
        closed = []
        async def source():
            try:
                await gate.wait()
                yield b'data: answer\n\n'
            finally:
                closed.append(True)
        stream = with_heartbeats(source(), interval=.001)
        assert await anext(stream) == b': ping\n\n'
        assert await anext(stream) == b': ping\n\n'
        gate.set()
        assert await anext(stream) == b'data: answer\n\n'
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert closed == [True]
    asyncio.run(run())


def test_models_cache_bypasses_busy_generation_queue(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from portal.main import create_app
    class Broker:
        async def touch(self, uid):
            pass
        async def acquire(self, *args, **kwargs):
            raise AssertionError('cached models must not start a worker')
    app = create_app(tmp_path, 'fixture-long-password', 'https://fixture.test', Broker())
    store = app.state.store
    store.save_settings({'public_login': True})
    u = store.discord_user('111', 'Alice')
    store.add_account(u['id'], 'fixture', {})
    key = store.create_key(u['id'], 'fixture')['key']
    rows = store.all('SELECT id,payload,enabled FROM accounts WHERE user_id=? ORDER BY id', (u['id'],))
    from portal.store import digest
    app.state.model_cache.put(u['id'], digest(json.dumps(rows, sort_keys=True)), {'data': [{'id': 'gemini-fixture'}]})
    monkeypatch.setattr(app.state.scheduler, 'enqueue', lambda uid: (_ for _ in ()).throw(AssertionError('models entered generation queue')))
    with TestClient(app, base_url='https://fixture.test') as client:
        response = client.get('/v1/models', headers={'Authorization': 'Bearer '+key})
        assert response.status_code == 200
        assert response.json()['data'][0]['id'] == 'gemini-fixture'
