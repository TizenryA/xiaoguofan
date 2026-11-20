"""Focused lifecycle regressions; run this file on HD only."""
import asyncio
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException

from portal import workers
from portal.proxy_cleanup import ProxyCleanup
from portal.resources import BASE_RESERVE, HIGH, LIMIT


class Resources:
    def snapshot(self):
        return {'available': False, 'memory': {
            'used_bytes': BASE_RESERVE, 'limit_bytes': LIMIT, 'high_bytes': HIGH}}

    def worker_memory(self, container_id):
        return 0


def item(used, leases=(), channel='build'):
    return {'url': 'http://worker', 'key': 'fixture', 'revision': [],
            'channel': channel, 'used': used, 'leases': set(leases)}


def mock_http(monkeypatch):
    client = httpx.AsyncClient
    monkeypatch.setattr(workers.httpx, 'AsyncClient', lambda **kwargs: client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'token': 'fixture'})),
        **kwargs))


def test_idle_pool_renews_for_thirty_minutes_and_active_request_survives(monkeypatch):
    now = [10000.0]
    monkeypatch.setattr(workers.time, 'time', lambda: now[0])
    manager = workers.WorkerManager('/tmp/unused')
    removed = []

    async def docker(*args, **kwargs):
        removed.append(args)
        return ''

    monkeypatch.setattr(manager, 'docker', docker)
    manager.items['alice'] = item(now[0])

    async def scenario():
        now[0] += 1799
        await manager.reap()
        assert 'alice' in manager.items
        acquired = await manager.ensure('alice', [], 2, channel='', lease=True)
        now[0] += 4000
        await manager.reap()
        assert 'alice' in manager.items
        with pytest.raises(HTTPException):
            await manager.stop('alice')
        await manager.release('alice', acquired['lease'])
        now[0] += 1799
        await manager.reap()
        assert 'alice' in manager.items
        now[0] += 1
        await manager.reap()
        assert 'alice' not in manager.items

    asyncio.run(scenario())
    assert removed == [('rm', '-f', 'asp-alice')]


def test_each_idle_request_renews_lease_and_empty_channel_reuses_pool(monkeypatch):
    now = [10000.0]
    monkeypatch.setattr(workers.time, 'time', lambda: now[0])
    manager = workers.WorkerManager('/tmp/unused')
    manager.items['alice'] = item(now[0])

    async def scenario():
        now[0] += 1700
        assert await manager.ensure('alice', [], 1) == {'url': 'http://worker', 'key': 'fixture'}
        now[0] += 1700
        await manager.reap()
        assert manager.items['alice']['used'] == 11700.0

    asyncio.run(scenario())


def test_distinct_uid_cold_start_and_hot_reuse_do_not_wait_for_other_start(tmp_path, monkeypatch):
    mock_http(monkeypatch)

    async def scenario():
        first_started, second_started, proceed = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Manager(workers.WorkerManager):
            async def docker(self, *args, **kwargs):
                if args[0] == 'run':
                    name = args[args.index('--name') + 1]
                    (first_started if name == 'asp-alice' else second_started).set()
                    await proceed.wait()
                    return name
                return '127.0.0.1:9999' if args[0] == 'port' else ''

        manager = Manager(tmp_path)
        manager.resources = Resources()
        manager.enforced = True
        manager.items['warm'] = item(workers.time.time())
        first = asyncio.create_task(manager.ensure('alice', [], 3, lease=True))
        await asyncio.wait_for(first_started.wait(), 1)
        second = asyncio.create_task(manager.ensure('bob', [], 3, lease=True))
        try:
            await asyncio.wait_for(second_started.wait(), 1)
            await manager.reap()
            assert manager.pending_cleanup == {'alice', 'bob'}
            warm = await asyncio.wait_for(manager.ensure('warm', [], 3, lease=True), 1)
            assert warm['lease']
            with pytest.raises(HTTPException) as error:
                await manager.ensure('overflow', [], 3)
            assert error.value.status_code == 503
            assert manager.starting == 2
        finally:
            proceed.set()
            await asyncio.gather(first, second)
        assert set(manager.items) == {'alice', 'bob', 'warm'}
        assert not manager.pending_cleanup

    asyncio.run(scenario())


def test_cleanup_failure_is_retryable_for_same_uid(tmp_path, monkeypatch):
    mock_http(monkeypatch)

    class Manager(workers.WorkerManager):
        fail = True

        async def docker(self, *args, **kwargs):
            if self.fail and args[0] in ('run', 'rm'):
                raise RuntimeError('temporary docker failure')
            return '127.0.0.1:9999' if args[0] == 'port' else ''

    manager = Manager(tmp_path)
    manager.resources = Resources()

    async def scenario():
        with pytest.raises(HTTPException):
            await manager.ensure('alice', [], 1)
        assert 'alice' in manager.pending_cleanup
        manager.fail = False
        assert (await manager.ensure('alice', [], 1))['url']
        assert not manager.pending_cleanup

    asyncio.run(scenario())


def test_proxy_cleanup_releases_scheduler_after_close_and_release_failures():
    calls = []

    class Broker:
        async def release(self, uid, token):
            calls.append(('release', uid, token))
            raise RuntimeError('temporary broker failure')

    class Response:
        async def aclose(self):
            raise RuntimeError('temporary close failure')

    class Client:
        async def aclose(self):
            calls.append(('client-close',))

    scheduler, record = Mock(), Mock()
    cleanup = ProxyCleanup(Broker(), 'alice', scheduler, 'ticket', record)
    cleanup.response, cleanup.client = Response(), Client()
    cleanup.worker = {'lease': 'fixture'}

    async def scenario():
        await cleanup()
        await cleanup()

    asyncio.run(scenario())
    assert ('client-close',) in calls
    assert ('release', 'alice', 'fixture') in calls
    scheduler.finish.assert_called_once_with('ticket', safe=True)
    record.assert_called_once()


def test_default_pool_uses_playground_and_long_startup_settings(tmp_path, monkeypatch):
    mock_http(monkeypatch)
    commands = []

    class Manager(workers.WorkerManager):
        async def docker(self, *args, **kwargs):
            commands.append(args)
            return '127.0.0.1:9999' if args[0] == 'port' else ''

    manager = Manager(tmp_path)
    asyncio.run(manager.ensure('alice', [], 1))
    command = next(args for args in commands if args[0] == 'run')
    assert 'UPSTREAM_CHANNELS=playground' in command
    assert 'WAA_BACKEND=spidermonkey' in command
    assert 'INIT_TIMEOUT=5m' in command
    assert 'REQUEST_TIMEOUT=30m' in command
    assert workers.STARTUP_TIMEOUT == 300


def test_broker_release_survives_waiter_cancellation():
    async def scenario():
        entered, recovered = asyncio.Event(), asyncio.Event()

        class Broker(workers.BrokerClient):
            async def call(self, path, payload):
                entered.set()
                await recovered.wait()
                assert path == '/release'
                return {'ok': True}

        broker = Broker()
        caller = asyncio.create_task(broker.release('alice', 'fixture'))
        await entered.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert not broker.release_tasks[('alice', 'fixture')].done()
        recovered.set()
        assert await broker.release('alice', 'fixture') == {'ok': True}
        await asyncio.sleep(0)
        assert not broker.release_tasks

    asyncio.run(scenario())


def test_broker_release_retries_a_temporary_failure(monkeypatch):
    sleeps = []

    async def no_wait(delay):
        sleeps.append(delay)

    monkeypatch.setattr(workers.asyncio, 'sleep', no_wait)

    class Broker(workers.BrokerClient):
        calls = 0

        async def call(self, path, payload):
            self.calls += 1
            if self.calls == 1:
                raise httpx.ConnectError('temporary broker outage')
            return {'ok': True}

    broker = Broker()
    assert asyncio.run(broker.release('alice', 'fixture')) == {'ok': True}
    assert broker.calls == 2
    assert sleeps == [1]


def test_same_uid_start_is_serial_and_other_uid_stop_remains_available(tmp_path, monkeypatch):
    mock_http(monkeypatch)

    async def scenario():
        entered, proceed = asyncio.Event(), asyncio.Event()
        runs = []

        class Manager(workers.WorkerManager):
            async def docker(self, *args, **kwargs):
                if args[0] == 'run':
                    runs.append(args)
                    entered.set()
                    await proceed.wait()
                return '127.0.0.1:9999' if args[0] == 'port' else ''

        manager = Manager(tmp_path)
        manager.items['warm'] = item(workers.time.time())
        first = asyncio.create_task(manager.ensure('alice', [], 2))
        await asyncio.wait_for(entered.wait(), 1)
        second = asyncio.create_task(manager.ensure('alice', [], 2))
        try:
            await asyncio.wait_for(manager.stop('warm'), 1)
            assert not second.done()
        finally:
            proceed.set()
            results = await asyncio.gather(first, second)
        assert len(runs) == 1
        assert results[0] == results[1]

    asyncio.run(scenario())
