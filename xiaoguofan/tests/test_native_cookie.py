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
    data={'cookie':RAW+'; SIDCC=ISOLATED-OLD','accounts_cookie':'SIDCC=ISOLATED-NEW; OSID=ISOLATED-OSID; __Secure-OSID=ISOLATED-SECURE-OSID','consent':True}
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
    cookies=saved['payload']['storage_state']['cookies']
    assert next(x for x in cookies if x['name']=='SIDCC')['value']=='ISOLATED-NEW'
    assert next(x for x in cookies if x['name']=='OSID')['domain']=='accounts.google.com'
    assert 'ISOLATED-OLD' not in str(saved)
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


ROTATING=['SIDCC','__Secure-1PSIDCC','__Secure-3PSIDCC','__Secure-1PSIDTS','__Secure-3PSIDTS','NID','AEC','__Secure-ENID']


def test_same_session_rotation_uses_accounts_snapshot_in_request_and_saved_state():
    main=RAW+'; '+'; '.join(k+'=ISOLATED-OLD' for k in ROTATING)
    accounts=RAW+'; '+'; '.join(k+'=ISOLATED-NEW' for k in ROTATING)+'; OSID=ISOLATED-OSID; __Secure-OSID=ISOLATED-SECURE-OSID; OTZ=ignored'
    def handler(req):
        sent=parse_header(req.headers['cookie'])
        assert all(sent[k]=='ISOLATED-NEW' for k in ROTATING)
        assert sent['OSID']=='ISOLATED-OSID' and sent['__Secure-OSID']=='ISOLATED-SECURE-OSID'
        assert 'OTZ' not in sent and 'UNKNOWN' not in sent
        return httpx.Response(200,content=wire())
    p,rows=asyncio.run(verify_native(main,accounts,transport=httpx.MockTransport(handler)))
    assert len(rows)==2
    cs=p['storage_state']['cookies']
    for k in ROTATING:
        assert [c['value'] for c in cs if c['name']==k]==['ISOLATED-NEW']
    for k in ['OSID','__Secure-OSID']:
        assert next(c for c in cs if c['name']==k)['domain']=='accounts.google.com'
    assert 'ISOLATED-OLD' not in str(p)


@pytest.mark.parametrize('name',['SID','__Secure-1PSID','__Secure-3PSID','HSID','SSID','APISID','SAPISID','__Secure-1PAPISID','__Secure-3PAPISID'])
def test_core_identity_conflict_still_rejected_before_network(name):
    main=parse_header(RAW);main[name]='ISOLATED-ONE'
    def never(req):raise AssertionError('conflicting identity must never reach Google')
    with pytest.raises(HTTPException) as e:
        asyncio.run(verify_native('; '.join(k+'='+v for k,v in main.items()),name+'=ISOLATED-TWO',transport=httpx.MockTransport(never)))
    assert e.value.status_code==422


def test_rotation_is_not_permission_to_accept_empty_google_result():
    with pytest.raises(HTTPException) as e:
        asyncio.run(verify_native(RAW+'; SIDCC=ISOLATED-OLD','SIDCC=ISOLATED-NEW; OSID=ISOLATED-OSID',transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b''))))
    assert 'Google 未返回' in e.value.detail or '默认账号页' in e.value.detail



def myaccount_html(email='fixture@example.com',gaia='123456789012345678901'):
    import json
    data={'oPEP7c':email,'qDCSke':gaia,'W3Yyqf':gaia,'S06Grb':gaia}
    return '<html><head><base href="https://myaccount.google.com/u/0/"><script nonce="fixture">window.WIZ_global_data = '+json.dumps(data)+';</script></head></html>'


def test_empty_list_fallback_requires_live_structured_default_identity():
    calls=[]
    def receive(req):
        calls.append(req.url.host)
        if req.url.host=='accounts.google.com':return httpx.Response(200,content=b'')
        assert str(req.url)=='https://myaccount.google.com/u/0/' and req.method=='GET'
        c=parse_header(req.headers['cookie'])
        assert c['OSID']=='ISOLATED-OSID' and c['__Secure-OSID']=='ISOLATED-SECURE-OSID'
        assert 'GAPS' not in c
        return httpx.Response(200,text=myaccount_html(),headers={'Content-Type':'text/html'})
    p,rows=asyncio.run(verify_native(RAW,'OSID=ISOLATED-OSID; __Secure-OSID=ISOLATED-SECURE-OSID; GAPS=ISOLATED-GAPS',transport=httpx.MockTransport(receive)))
    assert calls==['accounts.google.com','myaccount.google.com']
    assert rows==[{'email':'fixture@example.com','gaia_id':'123456789012345678901','authuser':0}]
    assert p['storage_state']['xiaoguofan_identity_verification']['method']=='myaccount-default-v1'
    for name in ['OSID','__Secure-OSID']:
        assert {c['domain'] for c in p['storage_state']['cookies'] if c['name']==name}=={'accounts.google.com','myaccount.google.com'}
    assert all(c['domain']=='accounts.google.com' for c in p['storage_state']['cookies'] if c['name']=='GAPS')


@pytest.mark.parametrize('status,body',[(302,''),(200,'fixture@example.com 123456789012345678901'),(200,myaccount_html().replace('"W3Yyqf": "123456789012345678901"','"W3Yyqf": "999999999999999999999"')),(200,myaccount_html().replace('https://myaccount.google.com/u/0/','https://evil.example/')),(200,myaccount_html()*2),(200,'x'*2100000)])
def test_myaccount_fallback_never_accepts_text_mismatch_redirect_or_oversize(status,body):
    def receive(req):
        if req.url.host=='accounts.google.com':return httpx.Response(200,content=b'')
        return httpx.Response(status,text=body,headers={'Content-Type':'text/html'})
    with pytest.raises(HTTPException):asyncio.run(verify_native(RAW,'OSID=ISOLATED-OSID',transport=httpx.MockTransport(receive)))



def test_signed_out_accounts_do_not_fallback_to_other_pages():
    email=b'fixture@example.com';gaia=b'123456789012345678901'
    row=bytes([26,len(email)])+email+bytes([72,0,82,len(gaia)])+gaia
    response=base64.b64encode(bytes([10,len(row)])+row)
    def receive(req):
        assert req.url.host=='accounts.google.com'
        return httpx.Response(200,content=response)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(verify_native(RAW,'OSID=ISOLATED-OSID',transport=httpx.MockTransport(receive)))
    assert '已退出' in exc.value.detail
