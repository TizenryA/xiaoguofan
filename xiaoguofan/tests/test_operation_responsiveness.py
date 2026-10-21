import asyncio

import httpx
import pytest
from fastapi import HTTPException

from portal.workers import WorkerManager, create_broker


def test_broker_stop_does_not_wait_behind_other_startup(tmp_path,monkeypatch):
    import portal.workers as w
    m=WorkerManager(tmp_path)
    monkeypatch.setattr(w,'WorkerManager',lambda *_:m)
    async def run():
        async with m.lock:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_broker()),base_url='http://fixture') as c:
                response=await asyncio.wait_for(c.post('/stop',json={'uid':'admin'}),.25)
                assert response.status_code==409
                assert '稍后' in response.json()['detail']
    asyncio.run(run())


def test_startup_busy_does_not_queue_another_tenant(tmp_path):
    async def run():
        m=WorkerManager(tmp_path)
        async with m.lock:
            with pytest.raises(HTTPException) as exc:
                await asyncio.wait_for(m.ensure('fixture',[],100),.25)
            assert exc.value.status_code==503
    asyncio.run(run())



def test_startup_timeout_releases_global_lock_and_cleans_container(tmp_path,monkeypatch):
    import portal.workers as w
    monkeypatch.setattr(w,'STARTUP_TIMEOUT',.05,raising=False)
    real=httpx.AsyncClient
    async def hang(req):
        await asyncio.Event().wait()
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(hang),**kw))
    removed=[]
    class Manager(WorkerManager):
        async def docker(self,*args,**kw):
            if args[0]=='rm':removed.append(args[-1]);return ''
            return '127.0.0.1:9999' if args[0]=='port' else 'fixture'
    async def run():
        m=Manager(tmp_path)
        with pytest.raises(HTTPException) as exc:
            await asyncio.wait_for(m.ensure('fixture',[],100),.5)
        assert exc.value.status_code==503
        assert not m.lock.locked() and m.starting==0 and not m.items
        assert removed[-1]=='asp-fixture'
    asyncio.run(run())



def test_portal_edit_and_delete_fail_fast_while_own_check_runs(tmp_path):
    from portal.main import create_app
    class SlowBroker:
        def __init__(self):self.entered=asyncio.Event();self.release=asyncio.Event()
        async def check(self,*args):
            self.entered.set();await self.release.wait()
            return {'status':'catalog_ok','models':1,'message':'fixture'}
        async def stop(self,*args):pass
    async def run():
        broker=SlowBroker();app=create_app(tmp_path,'isolated-password-long','http://fixture',broker)
        s=app.state.store;s.save_settings({'public_login':True})
        user=s.discord_user('100000000000000001','fixture');token,csrf=s.login(user['id'])
        aid=s.add_account(user['id'],'fixture',{})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture',cookies={'portal_session':token},headers={'X-CSRF-Token':csrf}) as c:
            running=asyncio.create_task(c.post('/api/accounts/'+aid+'/check'))
            await asyncio.wait_for(broker.entered.wait(),1)
            for method,body in [('PATCH',{'plan':'pro'}),('DELETE',None)]:
                r=await asyncio.wait_for(c.request(method,'/api/accounts/'+aid,json=body),.25)
                assert r.status_code==409 and '进行中' in r.json()['detail']
            broker.release.set();assert (await running).status_code==200
            r=await c.patch('/api/accounts/'+aid,json={'plan':'pro','label':'saved'})
            assert r.status_code==200 and s.account(user['id'],aid)['plan']=='pro'
            assert (await c.delete('/api/accounts/'+aid)).status_code==200
    asyncio.run(run())
