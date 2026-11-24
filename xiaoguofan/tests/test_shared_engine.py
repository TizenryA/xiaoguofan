"""Shared engine: isolation, streaming, first token, 429 switching (fake engine, no Google)."""
import json
import time

import httpx
import pytest
from engine_fixture import FakeEngine, Served, cookie_payload, portal


@pytest.fixture
def fake():
    engine = FakeEngine().start()
    yield engine
    engine.stop()


def add(s, user, email, plan='free'):
    aid = s.add_account(user['id'], email, cookie_payload(email))
    if plan != 'free':
        s.update_account(user['id'], aid, {'plan': plan})
    return aid


def key(s, user):
    return {'Authorization': 'Bearer '+s.create_key(user['id'], 'k')['key']}


def message(r):
    body = r.json()
    if 'detail' in body:
        return body['detail']
    return body['error']['message']


def chat(c, headers, stream=False, model='gemini-3.8-flash'):
    return c.post('/v1/chat/completions', headers=headers,
                  json={'model': model, 'stream': stream, 'messages': [{'role': 'user', 'content': 'hi'}]})


def test_requests_only_ever_use_the_callers_own_accounts(tmp_path, fake):
    app, s, alice, bob, c = portal(tmp_path, fake)
    a1 = add(s, alice, 'alice1@gmail.com')
    add(s, alice, 'alice2@gmail.com')
    add(s, bob, 'bob@gmail.com')
    ka, kb = key(s, alice), key(s, bob)
    with c:
        for _ in range(3):
            assert chat(c, ka).status_code == 200
            assert chat(c, kb).status_code == 200
    scopes = [(scope, account) for path, scope, account in fake.seen if path == '/v1/chat/completions']
    alice_scope = {'alice1@gmail.com', 'alice2@gmail.com'}
    for scope, account in scopes:
        assert set(scope) in (alice_scope, {'bob@gmail.com'})
        assert account in scope
    # Records are attributed to the caller's own portal account id.
    rows = s.all('SELECT user_id,account_id FROM requests WHERE generation=1')
    assert {r['user_id'] for r in rows} == {alice['id'], bob['id']}
    assert all(r['account_id'] in [a['id'] for a in s.accounts(r['user_id'])] for r in rows)
    assert any(r['account_id'] == a1 for r in rows)


def test_a_google_account_never_serves_two_users(tmp_path, fake):
    app, s, alice, bob, c = portal(tmp_path, fake)
    add(s, alice, 'shared@gmail.com')
    # Legacy data: the same email was imported by Bob before this release.
    add(s, bob, 'shared@gmail.com')
    kb = key(s, bob)
    with c:
        r = chat(c, kb)
        assert r.status_code == 503 and '准备中' in message(r)
        token, csrf = s.login(bob['id'])
        c.cookies.set('portal_session', token)
        live = c.get('/api/accounts').json()[0]['live']
        assert live['state'] == 'conflict' and '其他用户' in live['text']
        # New imports of someone else's email are refused outright.
        p = cookie_payload('shared@gmail.com') | {'label': '我的'}
        r = c.post('/api/accounts/import', headers={'X-CSRF-Token': csrf}, json=p)
        assert r.status_code == 409
    assert all('bob' not in ','.join(scope) for _, scope, _ in fake.seen)
    assert s.one("SELECT COUNT(*) n FROM alerts WHERE kind='duplicate_account' AND resolved IS NULL")['n'] == 1


def test_429_switches_to_the_same_users_next_account_then_reports_recovery_time(tmp_path, fake):
    app, s, alice, bob, c = portal(tmp_path, fake)
    add(s, alice, 'alice1@gmail.com')
    add(s, alice, 'alice2@gmail.com')
    add(s, bob, 'bob@gmail.com')
    ka = key(s, alice)
    fake.quota = {'alice1@gmail.com': 0, 'alice2@gmail.com': 1}
    with c:
        r = chat(c, ka)
        assert r.status_code == 200
        assert fake.seen[-1][2] == 'alice2@gmail.com'
        started = time.monotonic()
        r = chat(c, ka)
        elapsed = time.monotonic()-started
    assert elapsed < 2, 'all-cooling must be reported immediately, not queued'
    assert r.status_code == 429
    assert int(r.headers['retry-after']) > 500
    message = r.json()['error']['message']
    assert '北京时间' in message and '分钟后' in message and '不会借用别人的账号' in message
    assert 'bob@gmail.com' not in fake.cooling and all(k[0] != 'bob@gmail.com' for k in fake.cooling)


def test_429_on_a_stream_is_a_real_http_status(tmp_path, fake):
    app, s, alice, bob, c = portal(tmp_path, fake)
    add(s, alice, 'alice1@gmail.com')
    fake.quota = {'alice1@gmail.com': 0}
    with c:
        r = chat(c, key(s, alice), stream=True)
    assert r.status_code == 429 and 'retry-after' in r.headers
    assert r.json()['error']['type'] == 'rate_limit_exceeded'


def test_streaming_first_token_is_forwarded_immediately(tmp_path, fake):
    app, s, alice, bob, c = portal(tmp_path, fake)
    add(s, alice, 'alice1@gmail.com')
    fake.first_token_delay, fake.chunk_delay, fake.chunks = 0.05, 0.6, 3
    ka = key(s, alice)
    with Served(app) as base, httpx.Client(base_url=base, timeout=30) as http:
        # Warm-up loads the account into the engine (first use only).
        assert http.post('/v1/chat/completions', headers=ka, json={'model': 'gemini-3.8-flash', 'messages': []}).status_code == 200
        started = time.monotonic()
        arrivals = []
        with http.stream('POST', '/v1/chat/completions', headers=ka,
                         json={'model': 'gemini-3.8-flash', 'stream': True, 'messages': [{'role': 'user', 'content': 'hi'}]}) as r:
            assert r.status_code == 200
            assert r.headers['content-type'].startswith('text/event-stream')
            assert r.headers.get('x-accel-buffering') == 'no'
            for line in r.iter_lines():
                if line.startswith('data: {') and '片段' in line:
                    arrivals.append(time.monotonic()-started)
    assert len(arrivals) == 3
    assert arrivals[0] < 0.5, f'first token took {arrivals[0]:.2f}s'
    assert arrivals[-1]-arrivals[0] > 1.0, 'chunks must not be buffered until the end'
    row = s.one('SELECT status,first_token_seconds,input_tokens,output_tokens FROM requests ORDER BY id DESC LIMIT 1')
    assert row['status'] == 200 and row['first_token_seconds'] < 0.5
    assert row['input_tokens'] == 3 and row['output_tokens'] == 3


def test_native_gemini_stream_without_sse_is_passed_through_unbuffered(tmp_path, fake):
    app, s, alice, bob, c = portal(tmp_path, fake)
    add(s, alice, 'alice1@gmail.com')
    fake.first_token_delay, fake.chunk_delay, fake.chunks = 0.0, 0.5, 3
    ka = key(s, alice)
    with Served(app) as base, httpx.Client(base_url=base, timeout=30) as http:
        assert http.post('/v1/chat/completions', headers=ka, json={'model': 'gemini-3.8-flash', 'messages': []}).status_code == 200
        started = time.monotonic()
        arrivals, raw = [], b''
        with http.stream('POST', '/v1beta/models/gemini-3.8-flash:streamGenerateContent', headers=ka,
                         json={'contents': [{'parts': [{'text': 'hi'}]}]}) as r:
            assert r.status_code == 200
            assert r.headers['content-type'].startswith('application/json')
            for chunk in r.iter_raw():
                raw += chunk
                arrivals.append(time.monotonic()-started)
    value = json.loads(raw)
    assert [p['candidates'][0]['content']['parts'][0]['text'] for p in value] == ['part0', 'part1', 'part2']
    assert b': ping' not in raw
    assert arrivals[0] < 0.4 and arrivals[-1] > 0.9


def test_models_come_from_the_site_catalogue_filtered_by_tier(tmp_path, fake):
    app, s, alice, bob, c = portal(tmp_path, fake)
    add(s, alice, 'alice1@gmail.com')
    add(s, bob, 'bob@gmail.com', plan='pro')
    ka, kb = key(s, alice), key(s, bob)
    with c:
        free = c.get('/v1/models', headers=ka).json()['data']
        pro = c.get('/v1/models', headers=kb).json()['data']
        catalogue_calls = sum(1 for p, *_ in fake.seen if p == '/v1/models')
        for _ in range(5):
            assert c.get('/v1/models', headers=ka).status_code == 200
    assert [m['id'] for m in free] == ['gemini-3.8-flash']
    assert [m['id'] for m in pro] == ['gemini-3.8-flash', 'gemini-4-argon']
    assert all('channels' not in m and not m['id'].endswith(('-pg', '-build')) for m in free+pro)
    # One site-wide fetch, then SQLite: no per-user startup and no generation call.
    assert catalogue_calls == 1 and sum(1 for p, *_ in fake.seen if p == '/v1/models') == 1
    assert not any(p != '/v1/models' for p, *_ in fake.seen)


def test_engine_reported_models_narrow_the_catalogue(tmp_path, fake):
    app, s, alice, bob, c = portal(tmp_path, fake)
    add(s, alice, 'alice1@gmail.com')
    ka = key(s, alice)
    with c:
        assert c.get('/v1/models', headers=ka).status_code == 200
        fake.accounts['alice1@gmail.com']['models'] = ['models/gemini-4-argon']
        import asyncio
        asyncio.run(app.state.engine.refresh_status())
        ids = [m['id'] for m in c.get('/v1/models', headers=ka).json()['data']]
    assert ids == ['gemini-4-argon']


def test_legacy_channel_suffix_is_accepted_and_stripped(tmp_path, fake):
    app, s, alice, bob, c = portal(tmp_path, fake)
    add(s, alice, 'alice1@gmail.com')
    with c:
        r = chat(c, key(s, alice), model='gemini-3.8-flash-pg')
    assert r.status_code == 200 and r.json()['model'] == 'gemini-3.8-flash'


def test_previous_response_id_only_continues_own_conversations(tmp_path, fake):
    app, s, alice, bob, c = portal(tmp_path, fake)
    add(s, alice, 'alice1@gmail.com')
    add(s, bob, 'bob@gmail.com')
    ka, kb = key(s, alice), key(s, bob)
    with c:
        first = c.post('/v1/responses', headers=ka, json={'model': 'gemini-3.8-flash', 'input': 'hi'}).json()['id']
        ok = c.post('/v1/responses', headers=ka, json={'model': 'gemini-3.8-flash', 'input': 'go on', 'previous_response_id': first})
        stolen = c.post('/v1/responses', headers=kb, json={'model': 'gemini-3.8-flash', 'input': 'x', 'previous_response_id': first})
        fetched = c.get('/v1/responses/'+first, headers=kb)
    assert ok.status_code == 200
    assert stolen.status_code == 404
    assert fetched.status_code == 400
    assert sum(1 for p, scope, _ in fake.seen if p == '/v1/responses' and 'bob@gmail.com' in scope) == 0


@pytest.mark.parametrize('path,method,body', [
    ('/v1/files', 'GET', None),
    ('/v1beta/files/abc', 'GET', None),
    ('/v1beta/cachedContents', 'POST', {'model': 'x'}),
    ('/v1/videos', 'POST', {'model': 'veo'}),
    ('/v1beta/operations/op1', 'GET', None),
    ('/v1/interactions', 'POST', {}),
    ('/v1beta/models/veo:predictLongRunning', 'POST', {}),
])
def test_engine_wide_object_endpoints_are_refused(tmp_path, fake, path, method, body):
    app, s, alice, bob, c = portal(tmp_path, fake)
    add(s, alice, 'alice1@gmail.com')
    with c:
        r = c.request(method, path, headers=key(s, alice), json=body)
    assert r.status_code == 400 and '暂不支持' in message(r)
    assert fake.seen == []


def test_cached_content_reference_is_refused(tmp_path, fake):
    app, s, alice, bob, c = portal(tmp_path, fake)
    add(s, alice, 'alice1@gmail.com')
    with c:
        r = c.post('/v1beta/models/gemini-3.8-flash:generateContent', headers=key(s, alice),
                   json={'contents': [], 'cachedContent': 'cachedContents/other-users-cache'})
    assert r.status_code == 400 and fake.seen == []


def test_disabled_and_deleted_accounts_leave_the_engine(tmp_path, fake):
    app, s, alice, bob, c = portal(tmp_path, fake)
    a1 = add(s, alice, 'alice1@gmail.com')
    a2 = add(s, alice, 'alice2@gmail.com')
    ka = key(s, alice)
    import asyncio
    with c:
        assert chat(c, ka).status_code == 200
        s.update_account(alice['id'], a1, {'enabled': 0})
        asyncio.run(app.state.engine.sync())
        assert 'alice1@gmail.com' not in fake.accounts
        assert chat(c, ka).status_code == 200
        assert fake.seen[-1][1] == ['alice2@gmail.com']
        s.delete_account(alice['id'], a2)
        asyncio.run(app.state.engine.sync())
        assert fake.accounts == {}
        assert chat(c, ka).status_code == 503


def test_expired_credentials_are_shown_in_plain_words_and_alerted(tmp_path, fake):
    app, s, alice, bob, c = portal(tmp_path, fake)
    add(s, alice, 'expired@gmail.com')
    with c:
        r = chat(c, key(s, alice))
        assert r.status_code == 503
        token, _ = s.login(alice['id'])
        c.cookies.set('portal_session', token)
        live = c.get('/api/accounts').json()[0]['live']
    assert live['state'] == 'error' and '重新登录' in live['text']
    assert s.one("SELECT COUNT(*) n FROM alerts WHERE kind='account_load_failed'")['n'] == 1


def test_engine_down_is_an_admin_alert_not_a_user_detail(tmp_path, fake):
    app, s, alice, bob, c = portal(tmp_path, fake)
    add(s, alice, 'alice1@gmail.com')
    fake.stop()
    import asyncio
    asyncio.run(app.state.engine.refresh_status())
    assert s.one("SELECT level FROM alerts WHERE kind='engine_down' AND resolved IS NULL")['level'] == 'critical'
    token, _ = s.login(alice['id'])
    c.cookies.set('portal_session', token)
    with c:
        load = c.get('/api/load').json()
    assert load['ok'] is False and '管理员' in load['message']
    assert set(load) == {'ok', 'message', 'busy', 'mine_running', 'mine_waiting', 'refresh_seconds'}


def test_engine_requires_scope_header_is_respected_by_fixture(fake):
    import httpx
    from engine_fixture import KEY
    r = httpx.post(fake.url+'/v1/chat/completions', headers={'Authorization': 'Bearer '+KEY}, json={'model': 'x', 'messages': []})
    assert r.status_code == 403
