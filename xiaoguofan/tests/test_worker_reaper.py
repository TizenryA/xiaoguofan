import asyncio
import time


def test_worker_reaper_removes_only_expired_own_workers(monkeypatch):
    from portal.workers import WorkerManager
    m = WorkerManager('/tmp/not-used')
    calls = []
    async def fake_stop(uid):
        calls.append(uid)
        m.items.pop(uid, None)
    monkeypatch.setattr(m, 'stop', fake_stop)
    m.items = {'a':{'used':time.time()-1000},'b':{'used':time.time()}}
    asyncio.run(m.reap())
    assert calls == ['a']
    assert list(m.items) == ['b']
