from fastapi.testclient import TestClient

from portal.main import create_app


def test_subpath_oauth_cookie_and_callback_return_to_lite(tmp_path,monkeypatch):
    from urllib.parse import parse_qs, urlparse

    import portal.main
    app=create_app(tmp_path,'test-password-long-enough','https://aistudio.tizenty.xyz/lite')
    s=app.state.store
    s.save_settings({'public_login':True,'discord_client_id':'123456789012345678','discord_client_secret':'fixture'})
    async def identity(*args):
        return {'id':'123456789012345679','username':'Fixture'}
    monkeypatch.setattr(portal.main,'discord_identity',identity)
    with TestClient(app,base_url='https://aistudio.tizenty.xyz',follow_redirects=False) as c:
        r=c.get('/lite/auth/discord')
        assert 'Path=/lite' in r.headers['set-cookie']
        q=parse_qs(urlparse(r.headers['location']).query)
        assert q['redirect_uri']==['https://aistudio.tizenty.xyz/lite/auth/discord/callback']
        r=c.get('/lite/auth/discord/callback',params={'state':q['state'][0],'code':'fixture'})
        assert r.status_code==302
        assert r.headers['location']=='/lite'
        assert all('Path=/lite' in h for h in r.headers.get_list('set-cookie'))
        assert c.get('/lite/api/me').status_code==200
        assert c.get('/lite/v1/models',headers={'Authorization':'Bearer invalid'}).status_code==401
