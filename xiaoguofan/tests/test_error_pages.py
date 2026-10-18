from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from portal.main import create_app


@pytest.mark.parametrize('prefix',['','/lite'])
def test_browser_policy_denial_uses_user_image_and_no_session(tmp_path,monkeypatch,prefix):
    app=create_app(tmp_path,'test-password-long-enough','https://portal.test'+prefix)
    s=app.state.store
    s.save_settings({'public_login':True,'discord_client_id':'100000000000000123','discord_client_secret':'fixture'})
    async def denied(*args):
        raise PermissionError('private guild IDs must not be exposed')
    monkeypatch.setattr('portal.main.discord_identity',denied)
    with TestClient(app,base_url='https://portal.test') as c:
        r=c.get(prefix+'/auth/discord',follow_redirects=False)
        state=parse_qs(urlparse(r.headers['location']).query)['state'][0]
        r=c.get(prefix+'/auth/discord/callback',params={'state':state,'code':'fixture-secret'},headers={'Accept':'text/html'})
        assert r.status_code==403
        assert r.headers['content-type'].startswith('text/html')
        assert '你的账号脏脏的，不允许进去喵' in r.text
        assert prefix+'/static/猫娘拒绝.png' in r.text
        assert 'fixture-secret' not in r.text and 'private guild' not in r.text
        assert s.one('SELECT COUNT(*) n FROM sessions')['n']==0
        assert 'no-store' in r.headers['cache-control']
        assert c.get(prefix+'/static/猫娘拒绝.png').status_code==200
        expired=c.get(prefix+'/auth/discord/callback',headers={'Accept':'text/html'})
        assert expired.status_code==400
        assert '登录请求无效或已过期' in expired.text
        assert '你的账号脏脏的' not in expired.text
        assert c.get(prefix+'/api/me',headers={'Accept':'text/html'}).json()['detail']
        preview=c.get(prefix+'/access-denied')
        assert preview.status_code==403
        assert '你的账号脏脏的' in preview.text
