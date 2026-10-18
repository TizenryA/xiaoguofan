"""Allowlist uses Discord-verified ID, never username or client input."""
import pytest
from fastapi.testclient import TestClient
from test_oauth_flow import USER, fixture, start

from portal.main import create_app


def test_allowlisted_blocked_member_can_login_but_has_no_admin_access(tmp_path,monkeypatch):
    c,s,calls=fixture(tmp_path,monkeypatch,'blocked')
    s.save_settings({'discord_allowlist':[USER]})
    state=start(c)
    r=c.get('/auth/discord/callback',params={'state':state,'code':'fixture'},follow_redirects=False)
    assert r.status_code==302
    assert c.get('/api/me').status_code==200
    assert c.get('/api/me').json()['role']=='user'
    assert c.get('/api/accounts').json()==[]
    assert c.get('/api/admin/settings').status_code==403
    assert not any(p.endswith('/guilds') for p in calls)
    assert calls[-1].endswith('/revoke')
    assert s.one("SELECT COUNT(*) n FROM audit WHERE action='discord_allowlist_login'")['n']==1
    other=s.discord_user('100000000000000002','Bob')
    aid=s.add_account(other['id'],'Bobs private',{})
    assert c.delete('/api/accounts/'+aid,headers={'X-CSRF-Token':c.get('/api/me').json()['csrf']}).status_code==404


def test_revocation_blocks_existing_session_and_key_until_reverification(tmp_path,monkeypatch):
    c,s,calls=fixture(tmp_path,monkeypatch,'blocked')
    s.save_settings({'discord_allowlist':[USER]})
    state=start(c)
    assert c.get('/auth/discord/callback',params={'state':state,'code':'fixture'},follow_redirects=False).status_code==302
    me=c.get('/api/me').json()
    key=c.post('/api/keys',json={'label':'friend'},headers={'X-CSRF-Token':me['csrf']}).json()['key']
    s.save_settings({'discord_allowlist':[]})
    assert c.get('/api/me').status_code==403
    assert c.get('/v1/models',headers={'Authorization':'Bearer '+key}).status_code==403
    state=start(c)
    assert c.get('/auth/discord/callback',params={'state':state,'code':'fixture'},follow_redirects=False).status_code==403


def test_allowlist_does_not_override_disabled_or_closed_login(tmp_path,monkeypatch):
    c,s,_=fixture(tmp_path,monkeypatch,'blocked')
    s.save_settings({'discord_allowlist':[USER]})
    u=s.discord_user(USER,'friend')
    s.run('UPDATE users SET disabled=1 WHERE id=?',(u['id'],))
    state=start(c)
    assert c.get('/auth/discord/callback',params={'state':state,'code':'fixture'},follow_redirects=False).status_code==403
    assert s.one('SELECT COUNT(*) n FROM sessions')['n']==0
    s.save_settings({'public_login':False})
    assert c.get('/auth/discord',follow_redirects=False).status_code==503


@pytest.mark.parametrize('invalid',[['friend'],['123'],['100000000000000001 OR 1=1'],[100000000000000001],['１２３４５６７８９０１２３４５６７８'],['1'*23]])
def test_admin_allowlist_validates_discord_ids(tmp_path,invalid):
    app=create_app(tmp_path,'test-password-long-enough','https://portal.test')
    with TestClient(app,base_url='https://portal.test') as c:
        csrf=c.post('/api/auth/admin',json={'password':'test-password-long-enough'}).json()['csrf']
        assert c.put('/api/admin/settings',json={'discord_allowlist':invalid},headers={'X-CSRF-Token':csrf}).status_code==422


def test_admin_only_allowlist_edit_and_no_public_id_leak(tmp_path):
    app=create_app(tmp_path,'test-password-long-enough','https://portal.test')
    s=app.state.store
    with TestClient(app,base_url='https://portal.test') as c:
        csrf=c.post('/api/auth/admin',json={'password':'test-password-long-enough'}).json()['csrf']
        before=s.settings()['policy']
        assert c.put('/api/admin/settings',json={'discord_allowlist':[USER,USER]},headers={'X-CSRF-Token':csrf}).status_code==200
        assert s.settings()['discord_allowlist']==[USER]
        assert s.settings()['policy']>before
        assert c.get('/api/admin/settings').json()['discord_allowlist']==[USER]
        assert USER not in c.get('/api/config').text
        assert 'name="discord_allowlist"' in c.get('/admin').text
        s.save_settings({'public_login':True})
        u=s.discord_user(USER,'friend');s.mark_verified(u['id']);token,_=s.login(u['id'])
        c.cookies.set('portal_session',token)
        assert c.put('/api/admin/settings',json={'discord_allowlist':[]},headers={'X-CSRF-Token':csrf}).status_code==403
