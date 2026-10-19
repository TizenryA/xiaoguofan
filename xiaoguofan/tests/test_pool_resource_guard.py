import asyncio

import httpx
import pytest
from fastapi import HTTPException

from portal.workers import BrokerClient, WorkerManager


def test_budget_error_is_not_rewritten_as_invalid_cookie(monkeypatch):
    real=httpx.AsyncClient
    message='项目内存预算已满，请稍后重试'
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(503,json={'detail':message})),**{k:v for k,v in kw.items() if k!='transport'}))
    with pytest.raises(HTTPException) as e:asyncio.run(BrokerClient().ensure('admin',[],100))
    assert e.value.status_code==503 and e.value.detail==message


def test_production_worker_has_shared_parent_and_swap_disabled(tmp_path,monkeypatch):
    monkeypatch.setenv('PORTAL_RESOURCE_ENFORCED','1')
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'token':'x'})),**kw))
    from test_dynamic_pool import Resources
    commands=[]
    class M(WorkerManager):
        async def docker(self,*a,**kw):
            commands.append(a)
            return '127.0.0.1:9999' if a[0]=='port' else ''
    m=M(tmp_path);m.resources=Resources();asyncio.run(m.ensure('admin',[],100))
    run=next(c for c in commands if c[0]=='run')
    assert run[run.index('--cgroup-parent')+1]=='xiaoguofan.slice'
    assert run[run.index('--memory')+1]==run[run.index('--memory-swap')+1]=='650m'
