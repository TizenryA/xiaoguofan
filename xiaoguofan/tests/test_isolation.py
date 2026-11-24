
from fastapi.testclient import TestClient

from portal.main import create_app


def test_two_users_request_data_and_control_are_isolated(tmp_path):
    from engine_fixture import FakeEngine, cookie_payload
    fake = FakeEngine().start()
    app = create_app(tmp_path, 'test-password-long-enough', 'https://portal.example', engine=fake.client())
    s = app.state.store
    a,b = [s.discord_user(str(100000000000000000+i), name) for i,name in [(1,'Alice'),(2,'Bob')]]
    s.save_settings({'public_login':True})
    aid = s.add_account(a['id'], 'Alice 私有', cookie_payload('alice@gmail.com'))
    ka,kb = [s.create_key(u['id'], 'test')['key'] for u in [a,b]]
    tb,cb = s.login(b['id'])
    try:
        with TestClient(app, base_url='https://portal.example') as c:
            c.cookies.set('portal_session',tb)
            assert c.get('/api/accounts').json() == []
            assert c.delete('/api/accounts/'+aid,headers={'X-CSRF-Token':cb}).status_code == 404
            assert c.get('/api/admin/settings').status_code == 403
            # Bob has no account: never served by Alice's account.
            assert c.get('/v1/models',headers={'Authorization':'Bearer '+kb}).status_code == 503
            assert fake.seen == []
            r = c.post('/v1/chat/completions?key=malicious-other-key',headers={'Authorization':'Bearer '+ka,'X-API-Key':kb,'Cookie':'google=secret',
                       'X-Xiaoguofan-Account-Scope':'bob@gmail.com,alice@gmail.com'}, json={'model':'gemini-3.8-flash','messages':[]})
            assert r.status_code == 200
            # A client-supplied scope header is ignored: the portal sets the trusted one.
            assert fake.seen[-1][1] == ['alice@gmail.com']
            assert s.stats(a['id'])['summary']['requests'] == 1
            assert s.stats(b['id'])['summary']['requests'] == 0
            s.save_settings({'block_enabled':True,'blocked_guilds':['123456789012345678']})
            # Pending Discord re-verification gates the web pages, not API keys.
            assert c.post('/v1/chat/completions',headers={'Authorization':'Bearer '+ka},json={'model':'gemini-3.8-flash','messages':[]}).status_code == 200
            ta,_ = s.login(a['id'])
            c.cookies.set('portal_session',ta)
            assert c.get('/api/accounts').status_code == 403
            s.mark_verified(a['id'])
            assert c.get('/api/accounts').status_code == 200
    finally:
        fake.stop()


def test_account_validation_rejects_wrong_domain(tmp_path):
    import pytest
    from fastapi import HTTPException

    from portal.main import validate_account
    with pytest.raises(HTTPException):
        validate_account({'locale':'en-US','timezone':'UTC','storage_state':{'cookies':[{'domain':'google.com.evil.test','value':'secret'}]}})


def test_policy_rejects_malformed_guild_results():
    from portal.oauth import check_guilds
    assert not check_guilds({'block_enabled':True,'blocked_guilds':['123']}, [{'name':'missing-id'}])
