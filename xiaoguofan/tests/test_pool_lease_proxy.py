import asyncio

import httpx
from fastapi.testclient import TestClient

from portal.main import create_app
from portal.workers import WorkerManager


def test_stream_success_releases_broker_lease(tmp_path,monkeypatch):
    real=httpx.AsyncClient;released=[]
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'data':[]})),**kw))
    class B:
        async def acquire(self,*a,**kw):return {'url':'http://worker','key':'private','lease':'lease'}
        async def release(self,uid,token):released.append((uid,token))
    app=create_app(tmp_path,'long-test-password-123','https://portal.example',B());s=app.state.store
    s.add_account('admin','x',{'storage_state':{'cookies':[]}});key=s.create_key('admin','x')['key']
    c=TestClient(app,base_url='https://portal.example')
    assert c.get('/v1/models',headers={'Authorization':'Bearer '+key}).status_code==200
    assert released==[('admin','lease')]


def test_cancelled_before_start_finishes_never_creates_busy_orphan(tmp_path,monkeypatch):
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'token':'x'})),**kw))
    class M(WorkerManager):
        async def docker(self,*a,**kw):return '127.0.0.1:9999' if a[0]=='port' else ''
    async def run():
        m=M(tmp_path);token='f'*32
        await m.release('admin',token)
        try:await m.ensure('admin',[],100,lease=token)
        except Exception:pass
        assert not m.items and m.starting==0
    asyncio.run(run())
