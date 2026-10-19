import asyncio

import httpx
import pytest
from fastapi import HTTPException

from portal.workers import BrokerClient, WorkerManager

IDENTITY_ERROR='登录会话与所选 Google 账号不匹配，请重新导出'


def test_worker_start_identity_error_is_safe_and_actionable(tmp_path,monkeypatch):
    real=httpx.AsyncClient
    def handler(r):
        if r.url.path=='/api/control/start':return httpx.Response(502,json={'error':{'message':'账户 PRIVATE_EMAIL: '+IDENTITY_ERROR+' SECRET_TOKEN'}})
        return httpx.Response(200,json={'token':'fixture'})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    class Manager(WorkerManager):
        async def docker(self,*a,**kw):return '127.0.0.1:9999' if a[0]=='port' else ''
    with pytest.raises(HTTPException) as e:asyncio.run(Manager(tmp_path).ensure('admin',[{'id':'fixture','payload':{'email':'own@example.com','storage_state':{'cookies':[]}}}],1))
    assert e.value.status_code==422 and e.value.detail==IDENTITY_ERROR


def test_broker_preserves_only_safe_identity_error(monkeypatch):
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(422,json={'detail':IDENTITY_ERROR})),**{k:v for k,v in kw.items() if k!='transport'}))
    with pytest.raises(HTTPException) as e:asyncio.run(BrokerClient().ensure('admin',[],1))
    assert e.value.detail==IDENTITY_ERROR
