import asyncio

import pytest
from fastapi import HTTPException
from test_lite import setup

from portal.accounts import worker_payload


def test_upstream_payload_uses_email_not_remark_and_preserves_cookies():
    data={'id':'a','label':'测试1','payload':{'label':'测试1','email':'Friend@Gmail.com','storage_state':{'cookies':[{'name':'SID','value':'fixture'}]}}}
    p=worker_payload(data)
    assert p['label']=='friend@gmail.com'
    assert 'email' not in p
    assert p['storage_state']['cookies'][0] == data['payload']['storage_state']['cookies'][0] | {'expires':-1}
    assert data['payload']['label']=='测试1'


def test_existing_email_labels_work_but_missing_email_fails_before_engine(tmp_path):
    assert worker_payload({'payload':{'label':'friend@gmail.com'}})['label']=='friend@gmail.com'
    with pytest.raises(HTTPException) as err:
        worker_payload({'id':'a','payload':{'label':'测试1'}})
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


def test_engine_import_receives_email_not_remark(tmp_path):
    from engine_fixture import FakeEngine, cookie_payload, portal
    fake=FakeEngine().start()
    try:
        app,s,alice,bob,client=portal(tmp_path,fake)
        s.add_account(alice['id'],'测试1',cookie_payload('Friend@Gmail.com')|{'label':'测试1'})
        asyncio.run(app.state.engine.sync())
        assert fake.imports==['friend@gmail.com']
    finally:
        fake.stop()
