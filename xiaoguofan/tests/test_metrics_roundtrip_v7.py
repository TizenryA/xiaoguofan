"""Fake-engine fixtures, never real Google generation evidence."""
import json

from engine_fixture import FakeEngine, cookie_payload, portal


def test_ttft_and_usage_roundtrip_to_records_and_csv(tmp_path):
    fake = FakeEngine().start()
    fake.first_token_delay, fake.chunks = .05, 2
    try:
        app, s, alice, bob, c = portal(tmp_path, fake)
        s.add_account(alice['id'], 'own', cookie_payload('alice@gmail.com'))
        key = s.create_key(alice['id'], 'local')['key']
        token, _ = s.login(alice['id'])
        c.cookies.set('portal_session', token)
        with c:
            r = c.post('/v1/chat/completions', headers={'Authorization': 'Bearer '+key}, json={'model': 'gemini-4-argon', 'stream': True, 'messages': []})
            assert r.status_code == 200 and '片段1' in r.text
            assert fake.last_body['stream_options']['include_usage'] is True
            row = c.get('/api/records').json()['items'][0]
            assert row['input_tokens'] == 3 and row['output_tokens'] == 2
            assert .04 < row['first_token_seconds'] <= row['duration']
            assert '首字耗时秒' in c.get('/api/records.csv').text
            assert '片段' not in json.dumps(row)
    finally:
        fake.stop()
