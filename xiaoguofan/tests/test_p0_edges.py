"""P0 regressions: real client IP, static caching, optional per-minute limit, body limits."""
import re

from engine_fixture import FakeEngine, cookie_payload, portal
from fastapi.testclient import TestClient

from portal import gateway
from portal.main import create_app

CF = '104.16.0.1'
DMIT = '69.63.193.127'


def test_client_ip_trusts_forwarding_only_from_trusted_hops():
    trusted = gateway.parse_networks(DMIT)
    # Direct client: spoofed headers ignored.
    assert gateway.client_ip('203.0.113.9', {'x-forwarded-for': '1.1.1.1', 'cf-connecting-ip': '1.1.1.1'}, trusted) == '203.0.113.9'
    # Local Caddy in front: rightmost untrusted hop wins, left-side spoofing ignored.
    assert gateway.client_ip('127.0.0.1', {'x-forwarded-for': '6.6.6.6, 198.51.100.7'}, trusted) == '198.51.100.7'
    # Caddy <- DMIT <- user.
    assert gateway.client_ip('127.0.0.1', {'x-forwarded-for': '198.51.100.7, '+DMIT}, trusted) == '198.51.100.7'
    # Caddy <- Cloudflare edge: CF-Connecting-IP believed only from a CF address.
    assert gateway.client_ip('127.0.0.1', {'x-forwarded-for': CF, 'cf-connecting-ip': '198.51.100.8'}, trusted) == '198.51.100.8'
    assert gateway.client_ip('127.0.0.1', {'x-forwarded-for': '198.51.100.9', 'cf-connecting-ip': '6.6.6.6'}, trusted) == '198.51.100.9'
    assert gateway.client_ip('127.0.0.1', {}, trusted) == '127.0.0.1'


def test_admin_login_rate_limit_is_per_real_ip(tmp_path):
    app = create_app(tmp_path, 'test-password-long-enough', 'https://portal.example')
    with TestClient(app, base_url='https://portal.example', client=('127.0.0.1', 5000)) as c:
        for _ in range(8):
            assert c.post('/api/auth/admin', json={'password': 'wrong-password-x'}, headers={'X-Forwarded-For': '198.51.100.1'}).status_code == 401
        assert c.post('/api/auth/admin', json={'password': 'wrong-password-x'}, headers={'X-Forwarded-For': '198.51.100.1'}).status_code == 429
        # A different visitor behind the same proxy is not locked out.
        assert c.post('/api/auth/admin', json={'password': 'test-password-long-enough'}, headers={'X-Forwarded-For': '198.51.100.2'}).status_code == 200


def test_static_assets_versioned_urls_get_long_cache(tmp_path):
    app = create_app(tmp_path, 'test-password-long-enough', 'https://portal.example')
    with TestClient(app, base_url='https://portal.example') as c:
        page = c.get('/').text
        url = re.search(r'/static/lite\.css\?v=([0-9a-f]{12})', page).group(0)
        assert 'immutable' in c.get(url).headers['cache-control']
        assert 'max-age=31536000' in c.get(url).headers['cache-control']
        stale = c.get('/static/lite.css?v=000000000000').headers['cache-control']
        assert 'immutable' not in stale and 'max-age=3600' in stale
        assert 'immutable' not in c.get('/static/lite.css').headers['cache-control']


def test_per_minute_limit_is_optional(tmp_path):
    fake = FakeEngine().start()
    try:
        app, s, alice, bob, c = portal(tmp_path, fake, requests_per_minute=1)
        s.add_account(alice['id'], 'own', cookie_payload('alice@gmail.com'))
        h = {'Authorization': 'Bearer '+s.create_key(alice['id'], 'k')['key']}
        body = {'model': 'fixture', 'messages': [], 'stream': False}
        with c:
            assert [c.post('/v1/chat/completions', headers=h, json=body).status_code for _ in range(3)] == [200, 200, 200]
            s.save_settings({'rate_limit_enabled': True})
            codes = [c.post('/v1/chat/completions', headers=h, json=body).status_code for _ in range(3)]
            assert 429 in codes
    finally:
        fake.stop()


def test_request_body_limits(tmp_path):
    app = create_app(tmp_path, 'test-password-long-enough', 'https://portal.example')
    with TestClient(app, base_url='https://portal.example') as c:
        csrf = c.post('/api/auth/admin', json={'password': 'test-password-long-enough'}).json()['csrf']
        big = (b'x'*400000 for _ in range(6))
        r = c.post('/api/accounts/import', content=big, headers={'X-CSRF-Token': csrf, 'Content-Type': 'application/json'})
        assert r.status_code == 413 and '太大' in r.json()['detail']
        # Model requests may carry inline images: 3 MB is fine at the body layer (401 = reached auth).
        r = c.post('/v1/chat/completions', content=b'x'*(3*1024**2), headers={'Content-Type': 'application/json'})
        assert r.status_code == 401
