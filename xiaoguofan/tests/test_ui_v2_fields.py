"""API fields behind the v2 user UI: recovery times and the records summary/filter."""
import asyncio
import time

import pytest
from engine_fixture import FakeEngine, cookie_payload, portal

from portal.engine import _epoch, parse_cooldowns


def test_epoch_parses_engine_timestamps():
    assert _epoch('2026-01-01T00:00:00Z') == 1767225600
    assert abs(_epoch('2026-01-01T08:00:00.123456789+08:00') - 1767225600.123456) < 1e-3
    assert _epoch('0001-01-01T00:00:00Z') is None and _epoch('') is None and _epoch(None) is None


def test_parse_cooldowns_keeps_future_entries_per_account():
    now = 1767225600
    items = [
        {'account_id': 'A@gmail.com', 'model_id': 'gemini-4-argon', 'until': '2026-01-01T00:30:00Z', 'reason': 'quota exhausted (429)'},
        {'account_id': 'a@gmail.com', 'model_id': '', 'until': '2026-01-01T00:10:00Z', 'reason': 'auth'},
        {'account_id': 'b@gmail.com', 'model_id': 'x', 'until': '2025-12-31T23:00:00Z', 'reason': 'quota'},
    ]
    out = parse_cooldowns(items, now)
    assert list(out) == ['a@gmail.com']
    assert [c['until'] for c in out['a@gmail.com']] == [now+600, now+1800]
    assert out['a@gmail.com'][1] == {'model': 'gemini-4-argon', 'until': now+1800, 'reason': 'quota'}


@pytest.fixture
def fake():
    engine = FakeEngine().start()
    yield engine
    engine.stop()


def login(c, s, user):
    token, _ = s.login(user['id'])
    c.cookies.set('portal_session', token)


def test_account_live_view_reports_recovery_time(tmp_path, fake):
    app, s, alice, bob, c = portal(tmp_path, fake)
    s.add_account(alice['id'], '主力', cookie_payload('alice1@gmail.com'))
    asyncio.run(app.state.engine.sync())
    until = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time()+1800))
    fake.cooldowns = [{'account_id': 'alice1@gmail.com', 'model_id': 'gemini-4-argon', 'until': until, 'reason': 'quota'}]
    asyncio.run(app.state.engine.refresh_status())
    with c:
        login(c, s, alice)
        live = c.get('/api/accounts').json()[0]['live']
    assert live['state'] == 'ready' and live['cooling'][0]['model'] == 'gemini-4-argon'
    assert 1700 < live['recover_at']-time.time() <= 1800
    with c:
        login(c, s, bob)
        assert c.get('/api/accounts').json() == []


def test_records_summary_and_status_filter(tmp_path, fake):
    app, s, alice, bob, c = portal(tmp_path, fake)
    for status in (200, 200, 200, 429, 502, 499):
        s.record(alice['id'], 'k', 'gemini-3.8-flash', status, 2.0, {'input_tokens': 10, 'output_tokens': 20}, generation=1, first_token_seconds=1.0)
    s.record(bob['id'], 'k', 'gemini-3.8-flash', 200, 1.0, generation=1)
    with c:
        login(c, s, alice)
        body = c.get('/api/records').json()
        today = body['summary']['today']
        assert (today['n'], today['ok'], today['limited'], today['failed'], today['cancelled']) == (6, 3, 1, 1, 1)
        assert today['success_rate'] == 60.0  # cancelled requests don't count against the rate
        assert today['input_tokens'] == 60 and today['output_tokens'] == 120
        assert {r['status'] for r in c.get('/api/records?status=limited').json()['items']} == {429}
        assert {r['status'] for r in c.get('/api/records?status=failed').json()['items']} == {502}
        assert len(c.get('/api/records?status=ok').json()['items']) == 3
        assert c.get('/api/records?status=bogus').status_code == 422
