"""Exercise actual encoder-style JSON HTTP response through proxy and SQLite."""
import json

from engine_fixture import FakeEngine, cookie_payload, portal


def test_json_with_trailing_newline_usage_reaches_records(tmp_path):
    fake = FakeEngine().start()
    raw = (json.dumps({'choices': [{'message': {'content': 'fixture'}}], 'usage': {'prompt_tokens': 123, 'completion_tokens': 45}})+'\n').encode()
    fake.chat_hook = lambda body: ('application/json', raw)
    try:
        app, s, alice, bob, c = portal(tmp_path, fake)
        s.add_account(alice['id'], 'own', cookie_payload('alice@gmail.com'))
        key = s.create_key(alice['id'], 'fixture')['key']
        with c:
            r = c.post('/v1/chat/completions', headers={'Authorization': 'Bearer '+key}, json={'model': 'fixture', 'stream': False, 'messages': []})
            assert r.status_code == 200
            assert r.content.endswith(b'\n')
            row = s.records(alice['id'])['items'][0]
            assert row['input_tokens'] == 123 and row['output_tokens'] == 45
            assert row['first_token_seconds'] is not None
    finally:
        fake.stop()
