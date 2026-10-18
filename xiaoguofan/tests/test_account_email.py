import asyncio

import httpx
import pytest
from fastapi import HTTPException
from test_lite import setup

from portal.workers import WorkerManager, worker_payload


def test_upstream_payload_uses_email_not_remark_and_preserves_cookies():
    data={'id':'a','label':'测试1','payload':{'label':'测试1','email':'Friend@Gmail.com','storage_state':{'cookies':[{'name':'SID','value':'fixture'}]}}}
    p=worker_payload(data)
    assert p['label']=='friend@gmail.com'
    assert 'email' not in p
    assert p['storage_state']['cookies'][0] == data['payload']['storage_state']['cookies'][0] | {'expires':-1}
    assert data['payload']['label']=='测试1'


def test_existing_email_labels_work_but_missing_email_fails_before_docker(tmp_path):
    assert worker_payload({'payload':{'label':'friend@gmail.com'}})['label']=='friend@gmail.com'
    class Manager(WorkerManager):
        async def docker(self,*a,**kw):raise AssertionError('must not start Docker')
    with pytest.raises(HTTPException) as err:
        asyncio.run(Manager(tmp_path).ensure('admin',[{'id':'a','payload':{'label':'测试1'}}],1))
    assert err.value.status_code==422
    assert 'Google 邮箱' in err.value.detail


def test_email_patch_is_private_owner_scoped_and_encrypted(tmp_path):
    c,s,a,b,csrf,broker=setup(tmp_path);h={'X-CSRF-Token':csrf}
    own=s.add_account(a['id'],'测试1',{'label':'测试1','storage_state':{'cookies':[]}})
    other=s.add_account(b['id'],'other',{})
    assert c.patch('/api/accounts/'+other,json={'email':'friend@gmail.com'},headers=h).status_code==404
    for value in ['测试1','a b@gmail.com','a@b','x\ny@gmail.com','']:
        assert c.patch('/api/accounts/'+own,json={'email':value},headers=h).status_code==422
    assert c.patch('/api/accounts/'+own,json={'email':'Friend@Gmail.com'},headers=h).status_code==200
    account=s.account(a['id'],own)
    assert account['payload']['email']=='friend@gmail.com'
    assert account['label']=='测试1'
    assert account['status']=='unverified' and account['checked'] is None
    assert 'friend@gmail.com' not in s.one('SELECT payload FROM accounts WHERE id=?',(own,))['payload']
    assert c.get('/api/accounts').json()[0]['email']=='friend@gmail.com'
    assert 'friend@gmail.com' not in c.get('/api/admin/users').text


def test_worker_import_http_receives_email_not_remark(tmp_path,monkeypatch):
    sent=[]
    def handler(r):
        import json
        if r.url.path=='/api/pairing/accounts':sent.append(json.loads(r.content))
        return httpx.Response(200,json={'token':'fixture'})
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    class Manager(WorkerManager):
        async def docker(self,*args,**kw):return '127.0.0.1:9999' if args[0]=='port' else ''
    account={'id':'a','payload':{'label':'测试1','email':'friend@gmail.com','storage_state':{'cookies':[]}}}
    asyncio.run(Manager(tmp_path).ensure('admin',[account],1))
    assert sent[0]['label']=='friend@gmail.com'
    assert 'email' not in sent[0]
