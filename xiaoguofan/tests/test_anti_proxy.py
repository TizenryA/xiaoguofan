import json
import subprocess
import time
from pathlib import Path

import pytest
from engine_fixture import FakeEngine, cookie_payload, portal

ANTI = Path(__file__).resolve().parents[1]/'anti'


@pytest.fixture(params=['resident', 'fallback'])
def anti_mode(request, tmp_path, monkeypatch):
    """Run against the resident anti/server.js service and against the fallback bridge."""
    if request.param == 'fallback':
        monkeypatch.delenv('XGF_ANTI_SOCKET', raising=False)
        yield request.param
        return
    sock = tmp_path/'anti.sock'
    proc = subprocess.Popen(['node', str(ANTI/'server.js'), str(sock)], stdout=subprocess.PIPE)
    for _ in range(100):
        if sock.exists():
            break
        time.sleep(.05)
    monkeypatch.setenv('XGF_ANTI_SOCKET', str(sock))
    try:
        yield request.param
    finally:
        proc.terminate()
        proc.wait(5)


def anti_upstream(calls):
    def hook(b):
        calls.append(b)
        name = next((t['function']['name'] for t in b.get('tools', []) if t['function']['name'].startswith('agw_emit_')), None)
        msg = {'role': 'assistant', 'content': 'plain fixture'}
        if name:
            msg = {'role': 'assistant', 'tool_calls': [{'id': 't', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps({'content': 'restored fixture'})}}]}
        if b.get('stream'):
            msg['tool_calls'][0]['index'] = 0
            event = {'model': b['model'], 'choices': [{'index': 0, 'delta': msg, 'finish_reason': 'tool_calls'}], 'usage': {'prompt_tokens': 2, 'completion_tokens': 4}}
            return 'text/event-stream', ('data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n').encode()
        return 'application/json', json.dumps({'model': b['model'], 'choices': [{'index': 0, 'message': msg, 'finish_reason': 'tool_calls' if name else 'stop'}],
                                               'usage': {'prompt_tokens': 2, 'completion_tokens': 4}}).encode()
    return hook


def test_alias_api_toggle_isolation_plain_and_stream_quota(tmp_path, anti_mode):
    calls = []
    fake = FakeEngine().start()
    fake.models = [{'id': 'gemini-4-argon', 'name': 'Gemini 4 Argon', 'displayName': 'Gemini 4 Argon'}, {'id': 'gemini-image', 'name': 'Gemini Image'}]
    fake.chat_hook = anti_upstream(calls)
    app, s, owner, other, c = portal(tmp_path, fake)
    own = s.add_account(owner['id'], 'own', cookie_payload('own@gmail.com'))
    s.add_account(other['id'], 'other', cookie_payload('other@gmail.com'))
    h = {'Authorization': 'Bearer '+s.create_key(owner['id'], 'test')['key']}
    h2 = {'Authorization': 'Bearer '+s.create_key(other['id'], 'test')['key']}
    token, csrf = s.login(owner['id'])
    try:
        with c:
            assert c.get('/api/preferences').status_code == 401
            c.cookies.set('portal_session', token)
            assert c.get('/api/preferences').json() == {'anti_truncation': False}
            assert c.put('/api/preferences', json={'anti_truncation': True}).status_code == 403
            r = c.put('/api/preferences', headers={'X-CSRF-Token': csrf}, json={'anti_truncation': True})
            assert r.status_code == 200, r.text
            assert c.put('/api/preferences', headers={'X-CSRF-Token': csrf}, json={'anti_truncation': 'false'}).status_code == 422
            catalogue = c.get('/v1/models', headers=h).json()['data']
            assert '[防截断]gemini-4-argon' in [r['id'] for r in catalogue]
            assert [r['name'] for r in catalogue] == ['Gemini 4 Argon', 'Gemini Image', '[防截断]Gemini 4 Argon']
            assert catalogue[-1]['displayName'] == '[防截断]Gemini 4 Argon'
            assert '[防截断]' not in c.get('/v1/models', headers=h2).text
            for stream in [False, True]:
                r = c.post('/v1/chat/completions', headers=h, json={'model': '[防截断]gemini-4-argon', 'messages': [{'role': 'user', 'content': 'fixture'}], 'stream': stream})
                assert r.status_code == 200, r.text
                assert 'restored fixture' in r.text and 'agw_emit_' not in r.text
            assert all(b['model'] == 'gemini-4-argon' for b in calls)
            r = c.post('/v1/chat/completions', headers=h, json={'model': 'gemini-4-argon', 'messages': []})
            assert 'plain fixture' in r.text
            assert not calls[-1].get('tools')
            assert c.post('/v1/responses', headers=h, json={'model': '[防截断]gemini-4-argon'}).status_code == 400
            assert c.post('/v1/chat/completions', headers=h2, json={'model': '[防截断]gemini-4-argon', 'messages': []}).status_code == 400
            assert c.post('/v1/chat/completions', headers=h, json={'model': '[防截断]gemini-image', 'messages': []}).status_code == 400
            r = c.get('/api/quota')
            assert r.json()['account_usage'][0]['models']['4a']['used'] == 3, r.text
            rows = s.all('SELECT * FROM requests WHERE generation=1 AND status=200')
            assert len(rows) == 3
            assert all(r['user_id'] == owner['id'] and r['account_id'] == own for r in rows)
            assert rows[0]['model'].startswith('[防截断]') and rows[1]['output_tokens'] == 4
            c.put('/api/preferences', headers={'X-CSRF-Token': csrf}, json={'anti_truncation': False})
            assert '[防截断]' not in c.get('/v1/models', headers=h).text
            assert c.post('/v1/chat/completions', headers=h, json={'model': '[防截断]gemini-4-argon'}).status_code == 400
        assert all(set(scope) == {'own@gmail.com'} for p, scope, _ in fake.seen if p == '/v1/chat/completions')
        from portal import anti_truncation
        assert anti_truncation.LAST_ERROR == ''
    finally:
        fake.stop()
