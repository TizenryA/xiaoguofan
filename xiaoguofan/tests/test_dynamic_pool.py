import asyncio
import time

import httpx
import pytest
from fastapi import HTTPException

from portal.resources import BASE_RESERVE, HIGH, LIMIT, WORKER_BYTES, ProjectResources
from portal.workers import WorkerManager


class Resources:
    used = BASE_RESERVE
    def snapshot(self):return {'available':True,'memory':{'used_bytes':self.used,'limit_bytes':LIMIT,'high_bytes':HIGH,'swap_bytes':0},'cpu':{'percent':0,'cores':4},'timestamp':time.time()}
    def worker_memory(self, name):return 0


def test_project_cgroup_metrics_and_fail_closed(tmp_path):
    r=ProjectResources(tmp_path)
    assert r.snapshot()['available'] is False
    for name,value in {'memory.current':1234,'memory.max':LIMIT,'memory.high':HIGH,'memory.swap.current':0,'cpu.stat':'usage_usec 100\n'}.items():(tmp_path/name).write_text(str(value))
    d=r.snapshot();assert d['available'] and d['memory']['used_bytes']==1234
    assert 'root' not in d and 'path' not in d
    (tmp_path/'memory.max').write_text('max');assert not r.snapshot()['available']


def test_dynamic_lru_never_evicts_busy_or_ttl_active(tmp_path,monkeypatch):
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'token':'x'})),**kw))
    class M(WorkerManager):
        async def docker(self,*a,**kw):return '127.0.0.1:9999' if a[0]=='port' else 'fixture'
    async def run():
        m=M(tmp_path);m.resources=Resources()
        a=await m.ensure('a',[],100,lease=True)
        await m.ensure('b',[],100)
        await m.ensure('c',[],100)
        assert 'a' in m.items and 'c' in m.items and 'b' not in m.items
        assert m.evictions==1
        assert sum(WORKER_BYTES for _ in m.items)+BASE_RESERVE < LIMIT
        m.items['a']['used']=0
        await m.reap();assert 'a' in m.items
        await m.release('a',a['lease'])
        m.items['a']['used']=0
        await m.reap();assert 'a' not in m.items
    asyncio.run(run())


def test_dynamic_budget_rejects_when_all_busy(tmp_path,monkeypatch):
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'token':'x'})),**kw))
    class M(WorkerManager):
        async def docker(self,*a,**kw):return '127.0.0.1:9999' if a[0]=='port' else 'fixture'
    async def run():
        m=M(tmp_path);m.resources=Resources()
        await m.ensure('a',[],100,lease=True);await m.ensure('b',[],100,lease=True)
        with pytest.raises(HTTPException) as e:await m.ensure('c',[],100,lease=True)
        assert e.value.status_code==503 and len(m.items)==2
        with pytest.raises(HTTPException):await m.stop('a')
        assert 'a' in m.items
    asyncio.run(run())


def test_worker_browser_home_is_on_existing_private_writable_tmpfs(tmp_path,monkeypatch):
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'token':'x'})),**kw))
    commands=[]
    class M(WorkerManager):
        async def docker(self,*a,**kw):
            commands.append(a)
            return '127.0.0.1:9999' if a[0]=='port' else 'fixture'
    asyncio.run(M(tmp_path).ensure('fixture',[],2))
    run=next(a for a in commands if a[0]=='run')
    assert 'HOME=/data' in run
    assert '--read-only' in run
    assert '/data:rw,size=160m' in run
    assert '650m' in run and '160' in run
