"""OAuth protocol simulation only: no real Discord account/token is used."""
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from portal.main import create_app

BLOCK='100000000000000099'
USER='100000000000000001'

def fixture(tmp_path, monkeypatch, mode='ok'):
    app=create_app(tmp_path,'test-password-long-enough','https://portal.example')
    s=app.state.store
    s.save_settings({'public_login':True,'discord_client_id':'100000000000000123','discord_client_secret':'fixture-not-real','block_enabled':True,'blocked_guilds':[BLOCK]})
    calls=[]
    def upstream(req):
        calls.append(req.url.path)
        if req.url.path.endswith('/revoke'):return httpx.Response(200,json={})
        if req.url.path.endswith('/token'):
            if mode=='token_fail':return httpx.Response(400,json={'error':'invalid_grant'})
            return httpx.Response(200,json={'access_token':'fixture-only','scope':'identify guilds'})
        if req.url.path.endswith('/guilds'):
            if mode=='limited':return httpx.Response(429,json={'retry_after':1})
            if mode=='malformed':return httpx.Response(200,json={'error':'not-a-list'})
            if mode=='blocked':return httpx.Response(200,json=[{'id':BLOCK}])
            if mode=='pagination' and not req.url.params.get('after'):
                return httpx.Response(200,json=[{'id':str(200000000000000000+i)} for i in range(200)])
            if mode=='pagination':return httpx.Response(200,json=[{'id':BLOCK}])
            return httpx.Response(200,json=[])
        if mode=='policy_race':s.save_settings({'blocked_guilds':['100000000000000088']})
        if mode=='close_race':s.save_settings({'public_login':False})
        if mode=='bad_profile':return httpx.Response(200,json={'id':USER,'global_name':['invalid-name']})
        return httpx.Response(200,json={'id':USER,'username':'Alice'})
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda *a,**kw:real(*a,**(kw|{'transport':httpx.MockTransport(upstream)})))
    c=TestClient(app,base_url='https://portal.example')
    return c,s,calls

def start(c):
    r=c.get('/auth/discord',follow_redirects=False)
    assert r.status_code==302
    q=parse_qs(urlparse(r.headers['location']).query)
    assert q['scope']==['identify guilds']
    assert q['redirect_uri']==['https://portal.example/auth/discord/callback']
    assert 'Secure' in r.headers['set-cookie'] and 'HttpOnly' in r.headers['set-cookie']
    return q['state'][0]

def test_callback_success_and_replay_refused(tmp_path,monkeypatch):
    c,s,calls=fixture(tmp_path,monkeypatch)
    state=start(c);r=c.get('/auth/discord/callback',params={'state':state,'code':'fixture'},follow_redirects=False)
    assert r.status_code==302 and r.headers['location']=='/'
    assert c.get('/api/me').json()['name']=='Alice'
    assert c.get('/api/admin/settings').status_code==403
    assert c.get('/auth/discord/callback',params={'state':state,'code':'fixture'}).status_code==400
    assert calls.count('/api/v10/oauth2/token')==1
    assert calls[-1].endswith('/revoke')

@pytest.mark.parametrize('mode,status',[('blocked',403),('pagination',403),('malformed',503),('limited',503),('token_fail',503),('policy_race',403)])
def test_oauth_negative_paths_do_not_create_sessions(tmp_path,monkeypatch,mode,status):
    c,s,calls=fixture(tmp_path,monkeypatch,mode)
    state=start(c)
    assert c.get('/auth/discord/callback',params={'state':state,'code':'fixture'},follow_redirects=False).status_code==status
    assert s.one('SELECT COUNT(*) n FROM sessions')['n']==0
    if mode!='token_fail':assert calls[-1].endswith('/revoke')

def test_closed_login_during_exchange_must_not_issue_session(tmp_path,monkeypatch):
    c,s,calls=fixture(tmp_path,monkeypatch,'close_race');state=start(c)
    assert c.get('/auth/discord/callback',params={'state':state,'code':'fixture'},follow_redirects=False).status_code==403
    assert s.one('SELECT COUNT(*) n FROM sessions')['n']==0

def test_malformed_profile_is_controlled_error(tmp_path,monkeypatch):
    c,s,calls=fixture(tmp_path,monkeypatch,'bad_profile');state=start(c)
    assert c.get('/auth/discord/callback',params={'state':state,'code':'fixture'},follow_redirects=False).status_code==503
    assert s.one('SELECT COUNT(*) n FROM sessions')['n']==0

def test_state_wrong_browser_expiry_and_policy_change(tmp_path,monkeypatch):
    c,s,calls=fixture(tmp_path,monkeypatch);state=start(c)
    other=TestClient(c.app,base_url='https://portal.example')
    assert other.get('/auth/discord/callback',params={'state':state,'code':'fixture'}).status_code==400
    s.save_settings({'blocked_guilds':[]})
    assert c.get('/auth/discord/callback',params={'state':state,'code':'fixture'}).status_code==400
    state=start(c)
    import portal.oauth
    clock=portal.oauth.time.time
    with monkeypatch.context() as m:
        m.setattr(portal.oauth.time,'time',lambda:clock()+601)
        assert c.get('/auth/discord/callback',params={'state':state,'code':'fixture'}).status_code==400
    assert calls==[]
