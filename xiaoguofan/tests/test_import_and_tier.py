"""One paste box for several cookie formats, and evidence-only plan tier detection."""
import asyncio
import json
import time

import httpx
import pytest
from fastapi import HTTPException
from test_native_cookie import wire

from portal.engine import detect_tier
from portal.native_cookie import normalize_paste, parse_header, verify_native

NOW = 1_800_000_000
AUTH = {'SID': 'ISOLATED-SID', 'SAPISID': 'ISOLATED-SAP', '__Secure-1PAPISID': 'ISOLATED-1P', '__Secure-3PAPISID': 'ISOLATED-3P'}


def editor_export(extra=()):
    rows = [{'name': k, 'value': v, 'domain': '.google.com', 'path': '/', 'expirationDate': NOW+86400, 'hostOnly': False,
             'httpOnly': k == 'SID', 'secure': True, 'session': False, 'sameSite': 'no_restriction', 'storeId': '0'} for k, v in AUTH.items()]
    rows += [{'name': 'NID', 'value': 'other-site', 'domain': '.youtube.com', 'path': '/'},
             {'name': 'OLD', 'value': 'gone', 'domain': '.google.com', 'path': '/', 'expirationDate': NOW-10},
             {'name': '__Host-GAPS', 'value': 'ISOLATED-GAPS', 'domain': 'accounts.google.com', 'path': '/'}]
    return rows+list(extra)


def test_cookie_editor_json_is_reduced_to_the_google_auth_allowlist():
    header, accounts, kind = normalize_paste(json.dumps(editor_export()), now=NOW)
    assert kind == 'json'
    assert parse_header(header) == AUTH
    assert accounts == '__Host-GAPS=ISOLATED-GAPS'
    assert 'youtube' not in header and 'OLD' not in header


def test_storage_state_file_and_netscape_cookies_txt():
    state = {'email': 'ignored@example.com', 'storage_state': {'cookies': editor_export()}}
    assert normalize_paste(json.dumps(state), now=NOW)[2] == 'storage'
    txt = '# Netscape HTTP Cookie File\n'+'\n'.join(
        ('#HttpOnly_' if k == 'SID' else '')+f'.google.com\tTRUE\t/\tTRUE\t{NOW+86400}\t{k}\t{v}' for k, v in AUTH.items())
    txt += f'\n.google.com\tTRUE\t/\tTRUE\t{NOW-5}\tSIDCC\texpired\n'
    header, _, kind = normalize_paste(txt, now=NOW)
    assert kind == 'netscape' and parse_header(header) == AUTH


def test_plain_header_passes_through_untouched():
    raw = '; '.join(k+'='+v for k, v in AUTH.items())
    assert normalize_paste(raw, 'OSID=x', now=NOW) == (raw, 'OSID=x', 'header')


@pytest.mark.parametrize('raw', [
    '{"cookies": [', '[]', '{"foo": 1}', '[{"name":"SID","value":"x","domain":".youtube.com","path":"/"}]',
    json.dumps(editor_export([{'name': 'SID', 'value': 'SECOND-ACCOUNT', 'domain': '.google.com', 'path': '/'}])),
    '.google.com\tTRUE\t/\tSID\tx',
])
def test_bad_or_mixed_pastes_are_rejected_in_plain_words(raw):
    with pytest.raises(HTTPException) as e:
        normalize_paste(raw, now=NOW)
    assert e.value.status_code == 422


def test_json_paste_still_needs_google_to_confirm_the_identity():
    header, accounts, _ = normalize_paste(json.dumps(editor_export()), now=NOW)
    seen = []

    def handler(req):
        seen.append(req.url.host)
        return httpx.Response(200, content=wire())
    p, rows = asyncio.run(verify_native(header, accounts, transport=httpx.MockTransport(handler)))
    assert seen == ['accounts.google.com'] and rows[0]['email'] == 'first@example.com'
    assert 'ignored@example.com' not in str(p)


def test_paste_endpoint_reports_detected_format(tmp_path, monkeypatch):
    from test_lite import setup
    c, s, a, b, h, _ = setup(tmp_path)

    async def fake_verify(cookie, accounts='', **kw):
        assert parse_header(cookie) == AUTH
        return {'storage_state': {'cookies': [], 'origins': []}}, [{'email': 'first@example.com', 'gaia_id': '1', 'authuser': 0}]
    monkeypatch.setattr('portal.native_cookie.verify_native', fake_verify)
    export = editor_export()
    for row in export:
        row.pop('expirationDate', None)
    r = c.post('/api/accounts/native/verify', json={'cookie': json.dumps(export), 'consent': True}, headers={'X-CSRF-Token': h})
    assert r.status_code == 200, r.text
    assert r.json()['source'].startswith('Cookie 导出 JSON') and r.json()['accounts'][0]['email'] == 'first@example.com'


CATALOGUE = {'data': [{'id': 'gemini-3.8-flash'}, {'id': 'gemini-4-argon', 'access_modes': [3, 4]},
                      {'id': 'gemini-ultra-x', 'access_modes': [4]}, {'id': 'open-model', 'access_modes': [1, 3]}]}


def test_tier_from_google_benefit_answer():
    for raw, tier in [('Free', 'free'), ('Pro', 'pro'), ('Ultra', 'ultra'), ('Plus', 'plus')]:
        t = detect_tier(raw, [], CATALOGUE)
        assert t['tier'] == tier and t['source'] == 'google'
    assert detect_tier('Free', ['models/gemini-4-argon'], CATALOGUE)['tier'] == 'free'  # Google's answer wins


def test_tier_from_paid_only_models_is_a_lower_bound_and_never_free():
    assert detect_tier('Unknown', ['models/gemini-4-argon'], CATALOGUE) == {'tier': 'paid', 'label': '付费（Pro 或以上）', 'source': 'models'}
    assert detect_tier('', ['gemini-ultra-x', 'gemini-3.8-flash'], CATALOGUE)['tier'] == 'ultra'
    for models in ([], ['gemini-3.8-flash', 'open-model'], ['unlisted-model']):
        assert detect_tier('Unknown', models, CATALOGUE) == {'tier': 'unknown', 'label': '未识别', 'source': ''}
    assert detect_tier('Unknown', ['gemini-4-argon'], None)['tier'] == 'unknown'


def test_accounts_api_shows_tier(tmp_path):
    from engine_fixture import FakeEngine, cookie_payload, portal
    fake = FakeEngine().start()
    try:
        app, s, alice, bob, c = portal(tmp_path, fake)
        s.add_account(alice['id'], 'one', cookie_payload('one@gmail.com'))
        s.add_account(alice['id'], 'two', cookie_payload('two@gmail.com'))
        asyncio.run(app.state.engine.sync())
        fake.accounts['one@gmail.com']['benefit_tier'] = 'Pro'
        fake.accounts['two@gmail.com']['benefit_tier'] = 'Unknown'
        asyncio.run(app.state.engine.refresh_status())
        token, _ = s.login(alice['id'])
        c.cookies.set('portal_session', token)
        with c:
            tiers = {a['label']: a['live']['plan_tier'] for a in c.get('/api/accounts').json()}
        assert tiers['one'] == {'tier': 'pro', 'label': 'Pro', 'source': 'google'}
        assert tiers['two']['label'] == '未识别'
    finally:
        fake.stop()


def test_records_summary_has_token_series(tmp_path):
    from test_lite import setup
    c, s, a, b, h, _ = setup(tmp_path)
    s.record(a['id'], 'k', 'gemini-3.8-flash', 200, 1.0, {'input_tokens': 5, 'output_tokens': 7}, generation=1)
    s.record(a['id'], 'k', 'gemini-3.8-flash', 200, 1.0, {'input_tokens': 1, 'output_tokens': 1}, generation=1)
    s.run('UPDATE requests SET created=? WHERE id=(SELECT MIN(id) FROM requests)', (time.time()-2*86400,))
    summary = c.get('/api/records').json()['summary']
    week, today = summary['week']['series'], summary['today']['series']
    assert len(week) == 7 and [w['input_tokens'] for w in week][-3] == 5 and week[-1]['output_tokens'] == 1
    assert 1 <= len(today) <= 24 and sum(x['n'] for x in today) == 1
    assert all(week[i+1]['start']-week[i]['start'] == 86400 for i in range(6))
