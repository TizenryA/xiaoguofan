import asyncio

import httpx

from portal.workers import WorkerManager


def test_readonly_worker_redirects_user_cache_to_owned_tmpfs(tmp_path, monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'token':'fixture'})), **kw))
    commands = []
    class Manager(WorkerManager):
        async def docker(self, *args, **kwargs):
            commands.append(args)
            return '127.0.0.1:9999' if args[0] == 'port' else ''
    asyncio.run(Manager(tmp_path).ensure('admin', [], 1))
    run = next(c for c in commands if c[0] == 'run')
    assert '--read-only' in run
    assert 'XDG_CACHE_HOME=/data/.cache' in run
    assert '/data:rw,size=160m' in run
    assert '--privileged' not in run
    assert not any('/root' in arg for arg in run)
