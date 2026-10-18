from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

import portal.main
from portal.main import create_app


def test_oauth_callback_keeps_discord_avatar_and_user_permissions(tmp_path,monkeypatch):
    app=create_app(tmp_path,'fixture-long-password','https://portal.test/lite');s=app.state.store
    s.save_settings({'public_login':True,'discord_client_id':'100000000000000123','discord_client_secret':'fixture-only'})
    async def identity(*args):return {'id':'100000000000000001','username':'Alice','avatar':'b'*32}
    monkeypatch.setattr(portal.main,'discord_identity',identity)
    c=TestClient(app,base_url='https://portal.test')
    state=parse_qs(urlsplit(c.get('/lite/auth/discord',follow_redirects=False).headers['location']).query)['state'][0]
    assert c.get('/lite/auth/discord/callback',params={'state':state,'code':'fixture'},follow_redirects=False).status_code==302
    me=c.get('/lite/api/me').json();assert me['role']=='user' and '/avatars/100000000000000001/' in me['avatar_url']
    assert c.get('/lite/api/admin/users').status_code==403
    assert 'https://cdn.discordapp.com' in c.get('/lite').headers['content-security-policy']
