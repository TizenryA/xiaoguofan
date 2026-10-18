from fastapi.testclient import TestClient


def client(tmp_path):
    from portal.main import create_app
    return TestClient(create_app(tmp_path, 'test-password-long-enough', 'https://portal.example'), base_url='https://portal.example')


def login(c):
    r = c.post('/api/auth/admin', json={'password': 'test-password-long-enough'})
    assert r.status_code == 200
    return {'X-CSRF-Token': r.json()['csrf']}


def test_secure_settings_and_csrf(tmp_path):
    with client(tmp_path) as c:
        assert c.get('/api/admin/settings').status_code == 401
        h = login(c)
        assert c.post('/api/keys', json={'label': 'a'}).status_code == 403
        assert c.put('/api/admin/settings', headers=h, json={'block_enabled': True, 'blocked_guilds': ['123456789012345678']}).status_code == 200
        r = c.put('/api/admin/settings', headers=h, json={'discord_client_secret': 'sensitive-oauth-secret'})
        assert r.status_code == 200
        assert 'sensitive-oauth-secret' not in r.text
        assert r.json()['discord_secret_set']
        assert c.get('/auth/discord').status_code == 503
        assert c.get('/api/config').json()['discord_ready'] is False
        assert c.get('/v1/models').status_code == 401


def test_api_key_does_not_grant_control_panel_access(tmp_path):
    with client(tmp_path) as c:
        h = login(c)
        key = c.post('/api/keys', headers=h, json={'label': 'test'}).json()['key']
        c.post('/api/auth/logout', headers=h)
        assert c.get('/api/accounts', headers={'Authorization': 'Bearer '+key}).status_code == 401
        assert c.get('/v1/models', headers={'Authorization': 'Bearer '+key}).status_code == 503


def test_owner_is_not_client_controlled(tmp_path):
    with client(tmp_path) as c:
        h = login(c)
        assert c.post('/api/accounts/import', headers=h, json={'label':'x','user_id':'other','storage_state':{'cookies':[]}}).status_code == 422
        r = c.post('/api/keys', headers=h, json={'label':'x','user_id':'other'})
        assert r.status_code == 422
        assert c.get('/auth/discord/callback?code=fake&state=fake').status_code == 400
