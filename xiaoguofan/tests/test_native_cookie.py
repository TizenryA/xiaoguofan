import asyncio
import base64

import httpx
import pytest
from fastapi import HTTPException
from test_lite import setup

from portal.native_cookie import parse_header, verify_native
from portal.workers import worker_payload

RAW='SID=ISOLATED-SID; SAPISID=ISOLATED-SAP; __Secure-1PAPISID=ISOLATED-1P; __Secure-3PAPISID=ISOLATED-3P; UNKNOWN=not-forwarded'


def wire():
    result=b''
    for email,gid in [('first@example.com','111'),('second@example.com','222')]:
        row=bytes([26,len(email)])+email.encode()+bytes([72,1,82,len(gid)])+gid.encode()
        result+=bytes([10,len(row)])+row
    return base64.b64encode(result)


def test_parser_no_injection_duplicates_or_unknown_metadata():
    assert parse_header('Cookie: A=a=b; B=2')=={'A':'a=b','B':'2'}
    for raw in ['A=one\r\nX=two','A=1; A=2','A=1; HttpOnly','A=1; Domain=google.com','A=1; Path=/','bad name=x','A="quoted"','A=1; Secure=x','x'*70000]:
        with pytest.raises(HTTPException):parse_header(raw)


def test_live_identity_uses_fixed_endpoint_and_bounded_cookie_scope():
    def handler(req):
        assert req.url.host=='accounts.google.com'
        assert 'UNKNOWN' not in req.headers['cookie']
        assert req.headers['cookie'].startswith('SID=ISOLATED-SID')
        return httpx.Response(200,content=wire())
    p,rows=asyncio.run(verify_native(RAW,'',transport=httpx.MockTransport(handler)))
    assert [r['email'] for r in rows]==['first@example.com','second@example.com']
    assert p['storage_state']['origins']==[]
    assert all('httpOnly' not in c and 'sameSite' not in c and 'expires' not in c for c in p['storage_state']['cookies'])
    assert 'UNKNOWN' not in str(p)
    assert p['storage_state']['xiaoguofan_cookie_source']['attributes_known'] is False
    p.update(email=rows[1]['email'],label='fixture')
    p['storage_state']['xiaoguofan_identity']=rows[1]
    assert worker_payload({'payload':p})['label']=='second@example.com'


@pytest.mark.parametrize('status,body',[(200,b''),(302,b''),(200,b'not-base64'),(200,b'x'*100001)])
def test_invalid_google_response_fails_closed(status,body):
    with pytest.raises(HTTPException):asyncio.run(verify_native(RAW,'',transport=httpx.MockTransport(lambda r:httpx.Response(status,content=body))))


def test_two_step_encrypted_owner_bound_one_use_import(tmp_path,monkeypatch):
    import portal.native_cookie as native
    async def verify(raw,accounts):return await verify_native(raw,accounts,transport=httpx.MockTransport(lambda r:httpx.Response(200,content=wire())))
    monkeypatch.setattr(native,'verify_native',verify)
    c,s,a,b,csrf,broker=setup(tmp_path);h={'X-CSRF-Token':csrf}
    data={'cookie':RAW,'accounts_cookie':'','consent':True}
    assert c.post('/api/accounts/native/verify',json=data).status_code==403
    assert c.post('/api/accounts/native/verify',json=data|{'consent':False},headers=h).status_code==422
    r=c.post('/api/accounts/native/verify',json=data,headers=h);assert r.status_code==200,r.text
    result=r.json();assert RAW not in r.text and 'ISOLATED' not in r.text
    assert s.accounts(a['id'])==[]
    payload={'ticket':result['ticket'],'authuser':1,'label':'手机专用','consent':True}
    other,othercsrf=s.login(b['id']);old=c.cookies.get('portal_session');c.cookies.set('portal_session',other)
    assert c.post('/api/accounts/native/import',json=payload,headers={'X-CSRF-Token':othercsrf}).status_code==422
    c.cookies.set('portal_session',old)
    assert c.post('/api/accounts/native/import',json=payload|{'authuser':99},headers=h).status_code==422
    r=c.post('/api/accounts/native/import',json=payload,headers=h);assert r.status_code==201,r.text
    saved=s.account(a['id'],r.json()['id']);assert saved['payload']['email']=='second@example.com'
    assert saved['payload']['storage_state']['xiaoguofan_identity']['gaia_id']=='222'
    assert 'ISOLATED' not in s.one('SELECT payload FROM accounts WHERE id=?',(saved['id'],))['payload']
    assert c.post('/api/accounts/native/import',json=payload,headers=h).status_code==422
    assert s.accounts(b['id'])==[]


def test_validation_never_echoes_secrets_and_pending_expire(tmp_path,monkeypatch):
    import portal.native_cookie as native
    async def verify(raw,accounts):return await verify_native(raw,accounts,transport=httpx.MockTransport(lambda r:httpx.Response(200,content=wire())))
    monkeypatch.setattr(native,'verify_native',verify)
    c,s,a,b,csrf,broker=setup(tmp_path);h={'X-CSRF-Token':csrf}
    r=c.post('/api/accounts/native/verify',json={'cookie':'ISOLATED-SECRET','consent':'ISOLATED-SECRET'},headers=h)
    assert r.status_code==422 and 'ISOLATED-SECRET' not in r.text
    result=c.post('/api/accounts/native/verify',json={'cookie':RAW,'consent':True},headers=h).json()
    pending=s.one('SELECT * FROM native_pending');assert 'ISOLATED' not in pending['encrypted']
    s.run('UPDATE native_pending SET expires=0')
    r=c.post('/api/accounts/native/import',json={'ticket':result['ticket'],'authuser':0,'label':'fixture','consent':True},headers=h)
    assert r.status_code==422 and s.accounts(a['id'])==[]
    assert s.one('SELECT COUNT(*) n FROM native_pending')['n']==0


def test_accounts_scope_and_conflicting_sessions():
    def handler(req):
        assert 'GAPS=ISOLATED-GAPS' in req.headers['cookie']
        return httpx.Response(200,content=wire())
    p,_=asyncio.run(verify_native(RAW,'GAPS=ISOLATED-GAPS; unrelated=drop',transport=httpx.MockTransport(handler)))
    gap=next(c for c in p['storage_state']['cookies'] if c['name']=='GAPS')
    assert gap['domain']=='accounts.google.com'
    with pytest.raises(HTTPException):asyncio.run(verify_native(RAW,'SID=OTHER',transport=httpx.MockTransport(handler)))


def test_cancel_only_current_owner(tmp_path,monkeypatch):
    import portal.native_cookie as native
    async def verify(raw,accounts):return await verify_native(raw,accounts,transport=httpx.MockTransport(lambda r:httpx.Response(200,content=wire())))
    monkeypatch.setattr(native,'verify_native',verify)
    c,s,a,b,csrf,broker=setup(tmp_path);h={'X-CSRF-Token':csrf}
    c.post('/api/accounts/native/verify',json={'cookie':RAW,'consent':True},headers=h)
    assert c.delete('/api/accounts/native/pending',headers=h).status_code==200
    assert not s.one('SELECT * FROM native_pending')
