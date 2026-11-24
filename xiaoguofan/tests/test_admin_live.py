from engine_fixture import FakeEngine, cookie_payload, offline_engine, portal
from fastapi.testclient import TestClient

from portal.main import create_app

PASSWORD = 'test-password-long-enough'


def admin_client(app):
    c = TestClient(app, base_url='https://portal.example')
    csrf = c.post('/api/auth/admin', json={'password': PASSWORD}).json()['csrf']
    c.headers['X-CSRF-Token'] = csrf
    return c


def test_admin_dashboard_endpoints_are_admin_only(tmp_path):
    fake = FakeEngine().start()
    try:
        app, s, alice, bob, c = portal(tmp_path, fake)
        token, csrf = s.login(alice['id'])
        c.cookies.set('portal_session', token)
        with c:
            for path in ['/api/admin/live', '/api/admin/health-matrix', '/api/admin/alerts']:
                assert c.get(path).status_code == 403
            assert c.post('/api/admin/engine/restart', headers={'X-CSRF-Token': csrf}).status_code == 403
    finally:
        fake.stop()


def test_health_matrix_grades_and_excludes_client_cancellations(tmp_path):
    app = create_app(tmp_path, PASSWORD, 'https://portal.example', engine=offline_engine())
    s = app.state.store
    u = s.discord_user('100000000000000001', 'Alice')
    good = s.add_account(u['id'], 'good', cookie_payload('good.account@gmail.com'))
    bad = s.add_account(u['id'], 'bad', cookie_payload('bad@gmail.com'))
    s.add_account(u['id'], 'idle', cookie_payload('idle@gmail.com'))
    for status in [200]*9 + [499]*5 + [400]:
        s.record(u['id'], 'k', 'gemini-a', status, 1, account_id=good, generation=1, first_token_seconds=.5)
    for status in [429, 429, 502, 200]:
        s.record(u['id'], 'k', 'gemini-a', status, 1, account_id=bad, generation=1)
    s.record(u['id'], 'k', 'gemini-b', 200, 1, account_id=good, generation=1)
    s.record(u['id'], 'k', '', 200, 1, account_id=good, generation=0)  # model list: ignored
    s.run("INSERT INTO engine_accounts(engine_id,account_id,user_id,state,tier,active) VALUES ('good.account@gmail.com',?,?,'ready','Pro',1)", (good, u['id']))
    with admin_client(app) as c:
        d = c.get('/api/admin/health-matrix?hours=24').json()
        assert d['models'] == ['gemini-a', 'gemini-b']
        rows = {r['label']: r for r in d['rows']}
        assert rows['good']['cells']['gemini-a'] == {'n': 15, 'ok': 9, 'limited': 0, 'failed': 0, 'grade': 'good', 'ttft': .5}
        assert rows['bad']['cells']['gemini-a']['grade'] == 'bad'
        assert rows['idle']['grade'] == 'idle' and rows['idle']['state'] == 'pending'
        assert d['rows'][0]['label'] == 'bad'  # worst first
        assert rows['good']['email'] == 'goo***@gmail.com' and rows['good']['tier'] == 'Pro'
        assert 'good.account@gmail.com' not in str(d)
        used = c.get('/api/admin/health-matrix?hours=24&used=true').json()
        assert {r['label'] for r in used['rows']} == {'good', 'bad'}


def test_live_snapshot_alerts_and_dismiss(tmp_path):
    app = create_app(tmp_path, PASSWORD, 'https://portal.example', engine=offline_engine())
    s, alerts = app.state.store, app.state.alerts
    u = s.discord_user('100000000000000001', 'Alice')
    aid = s.add_account(u['id'], 'own', cookie_payload('alice@gmail.com'))
    s.record(u['id'], 'k', 'gemini-a', 200, 2, account_id=aid, generation=1, first_token_seconds=1.25)
    s.record(u['id'], 'k', 'gemini-a', 429, 0, account_id=aid, generation=1)
    alerts('engine_down', '', '生成服务无响应', 'critical')
    alerts('account_auth_required', 'alice@gmail.com', '登录失效')
    alerts('account_auth_required', 'alice@gmail.com', '登录失效')
    with admin_client(app) as c:
        live = c.get('/api/admin/live').json()
        assert live['admission']['limit'] == 48 and live['admission']['per_user'] == 4
        assert live['traffic']['n'] == 2 and live['traffic']['ok'] == 1 and live['traffic']['limited'] == 1
        assert live['traffic']['ttft'] == 1.25
        assert live['accounts']['saved'] == 1 and live['alerts_open'] == 2
        assert live['load'] is None  # no broker in tests
        opened = c.get('/api/admin/alerts').json()['open']
        assert opened[0]['level'] == 'critical' and opened[1]['count'] == 2
        assert c.post(f"/api/admin/alerts/{opened[0]['id']}/dismiss").status_code == 200
        assert c.post(f"/api/admin/alerts/{opened[0]['id']}/dismiss").status_code == 404
        assert len(c.get('/api/admin/alerts').json()['open']) == 1


def test_settings_round_trip_new_limits(tmp_path):
    app = create_app(tmp_path, PASSWORD, 'https://portal.example', engine=offline_engine())
    with admin_client(app) as c:
        r = c.put('/api/admin/settings', json={'engine_concurrency': 64, 'user_concurrency': 2, 'rate_limit_enabled': True, 'requests_per_minute': 30})
        assert r.status_code == 200, r.text
        d = c.get('/api/admin/settings').json()
        assert (d['engine_concurrency'], d['user_concurrency'], d['rate_limit_enabled']) == (64, 2, True)
        assert app.state.scheduler.snapshot()['limit'] == 64
        assert c.put('/api/admin/settings', json={'user_concurrency': 0}).status_code == 422
