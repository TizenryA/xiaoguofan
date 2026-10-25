import asyncio

import httpx
import pytest
from fastapi import HTTPException

from portal.workers import BrokerClient, WorkerManager


def test_check_rebuilds_only_own_idle_pool_before_reading_catalog(monkeypatch):
    calls=[]
    class Broker(BrokerClient):
        async def stop(self, uid):calls.append(('stop',uid))
        async def acquire(self, uid,*a):
            assert calls==[('stop',uid)]
            calls.append(('acquire',uid))
            return {'url':'http://worker','key':'fixture','lease':'fixture'}
        async def release(self,uid,token):calls.append(('release',uid))
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'data':[{'id':'fixture'}]})),**kw))
    assert asyncio.run(Broker().check('owner',{},1))['status']=='catalog_ok'
    assert calls[-1]==('release','owner')


def test_start_401_is_not_discord_login_and_is_sanitized(tmp_path,monkeypatch):
    real=httpx.AsyncClient
    def handler(r):
        if r.url.path=='/api/control/start':return httpx.Response(401,json={'detail':'PRIVATE_EMAIL SECRET_TOKEN'})
        return httpx.Response(200,json={'token':'fixture'})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    class Manager(WorkerManager):
        async def docker(self,*a,**kw):return '127.0.0.1:9999' if a[0]=='port' else ''
    with pytest.raises(HTTPException) as e:
        asyncio.run(Manager(tmp_path).ensure('admin',[{'id':'fixture','payload':{'email':'own@example.com'}}],1))
    assert e.value.status_code==503
    assert '认证' in e.value.detail and 'Discord' in e.value.detail
    assert 'PRIVATE' not in e.value.detail and 'SECRET' not in e.value.detail


def test_proxy_worker_401_is_502_not_portal_auth_failure(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient

    from portal.main import create_app
    class Broker:
        async def ensure(self,*a):return {'url':'http://worker','key':'fixture'}
    app=create_app(tmp_path,'fixture-password-long','https://portal.example',Broker())
    s=app.state.store;s.add_account('admin','own',{'email':'own@example.com'})
    key=s.create_key('admin','key')['key']
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(401,json={'error':'PRIVATE_SECRET'})),**kw))
    with TestClient(app,base_url='https://portal.example') as c:
        response=c.get('/v1/models',headers={'Authorization':'Bearer '+key})
        assert response.status_code==502
        assert 'Discord' in response.text and 'PRIVATE' not in response.text
        assert c.get('/v1/models',headers={'Authorization':'Bearer invalid'}).status_code==401


def test_check_will_not_rebuild_busy_pool():
    class Broker(BrokerClient):
        async def stop(self,uid):raise HTTPException(409,'账号池正在处理请求，请结束后重试')
        async def acquire(self,*a):pytest.fail('must not rebuild a leased worker')
    with pytest.raises(HTTPException) as exc:
        asyncio.run(Broker().check('owner',{},1))
    assert exc.value.status_code==409
