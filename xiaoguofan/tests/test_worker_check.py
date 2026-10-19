import asyncio

import httpx
import pytest

from portal.workers import BrokerClient, WorkerManager


def test_check_releases_worker_when_catalog_request_fails(monkeypatch):
    calls=[]
    class Broker(BrokerClient):
        async def ensure(self,*args,**kwargs):return {'url':'http://worker','key':'k','lease':'lease1'}
        async def release(self,uid,lease):calls.append((uid,lease))
    async def fail(*args,**kwargs):raise httpx.ConnectError('network')
    monkeypatch.setattr(httpx.AsyncClient,'get',fail)
    with pytest.raises(httpx.ConnectError):asyncio.run(Broker().check('alice',{},1))
    assert calls==[('alice','lease1')]


def test_worker_cancelled_start_removes_created_container(tmp_path):
    class Manager(WorkerManager):
        async def docker(self,*args,**kwargs):
            if args[0]=='run':raise asyncio.CancelledError()
            self.calls.append(args)
            return ''
    m=Manager(tmp_path);m.calls=[]
    async def run():
        with pytest.raises(asyncio.CancelledError):await m.ensure('admin',[],1)
    asyncio.run(run())
    assert len([c for c in m.calls if c[0]=='rm'])==2
