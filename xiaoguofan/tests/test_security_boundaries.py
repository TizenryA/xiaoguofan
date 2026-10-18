import pytest
from fastapi.testclient import TestClient

from portal.main import create_app


def test_disabled_user_denied_login_callback(tmp_path,monkeypatch):
    from tests.test_oauth_flow import USER, fixture, start
    c,s,calls=fixture(tmp_path,monkeypatch)
    u=s.discord_user(USER,'Disabled')
    s.run('UPDATE users SET disabled=1 WHERE id=?',(u['id'],))
    state=start(c)
    assert c.get('/auth/discord/callback',params={'state':state,'code':'fixture'},follow_redirects=False).status_code==403
    assert s.one('SELECT COUNT(*) n FROM sessions')['n']==0


def test_csrf_origin_and_chunked_size_boundaries(tmp_path):
    app=create_app(tmp_path,'test-password-long-enough','https://portal.example')
    with TestClient(app,base_url='https://portal.example') as c:
        r=c.post('/api/auth/admin',json={'password':'test-password-long-enough'})
        csrf=r.json()['csrf']
        assert c.post('/api/keys',json={'label':'x'}).status_code==403
        assert c.post('/api/keys',json={'label':'x'},headers={'X-CSRF-Token':csrf,'Origin':'https://evil.example'}).status_code==403
        chunks=(b'x'*400000 for _ in range(6))
        assert c.post('/api/accounts/import',content=chunks,headers={'X-CSRF-Token':csrf,'Content-Type':'application/json'}).status_code==413
        assert app.state.store.keys('admin')==[]


def test_revoked_key_and_disabled_user_cannot_call(tmp_path):
    app=create_app(tmp_path,'test-password-long-enough','https://portal.example')
    s=app.state.store;s.save_settings({'public_login':True})
    u=s.discord_user('100000000000000001','test');s.add_account(u['id'],'own',{})
    key=s.create_key(u['id'],'key')
    with TestClient(app,base_url='https://portal.example') as c:
        h={'Authorization':'Bearer '+key['key']}
        s.run('UPDATE users SET disabled=1 WHERE id=?',(u['id'],))
        assert c.get('/v1/models',headers=h).status_code==401
        s.run('UPDATE users SET disabled=0 WHERE id=?',(u['id'],))
        assert s.revoke_key(u['id'],key['id'])
        assert c.get('/v1/models',headers=h).status_code==401


@pytest.mark.parametrize('value',[float('inf'),float('nan'),True])
def test_cookie_nonfinite_expiration_rejected(value):
    from fastapi import HTTPException

    from portal.main import validate_account
    p={'locale':'en-US','timezone':'UTC','storage_state':{'cookies':[
        {'domain':'.google.com','path':'/','name':n,'value':'fixture-only','expires':value}
        for n in ['SAPISID','__Secure-1PAPISID','__Secure-3PAPISID']]}}
    with pytest.raises(HTTPException):validate_account(p)
