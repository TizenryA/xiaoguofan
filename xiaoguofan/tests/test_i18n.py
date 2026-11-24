"""English UI/API text: dictionary health, coverage of every Chinese string, server-side switching."""
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from engine_fixture import FakeEngine, cookie_payload, portal
from fastapi.testclient import TestClient

from portal import gateway, i18n
from portal.main import create_app

ROOT = Path(__file__).parents[1]/'portal'
STATIC = ROOT/'static'
HAN = re.compile(r'[\u3400-\u9fff]')
# Chinese that is meant to stay in English text: the anti-cutoff model tag users see in their
# app, and the export button label inside the (Chinese-only) userscript.
KEEP = re.compile(r'\[防截断\]|小锅饭 · 导出 JSON')


def left(text):
    return HAN.findall(KEEP.sub('', text))


def test_every_english_entry_is_complete_and_keeps_its_placeholders():
    exact, templates = i18n.dictionary()
    assert len(exact) > 700 and len(templates) > 80
    for key, value in exact.items():
        assert not left(value), (key, value)
        assert value.strip() or key == '次', key
    for _, pattern, names, value in templates:
        assert not left(value), value
        used = set(re.findall(r'\{(\w+)(?:\|[^}]*)?\}', value))
        assert used <= set(names), (pattern.pattern, value)


def test_every_english_key_still_exists_in_the_chinese_source():
    exact, templates = i18n.dictionary()
    source = ''.join(p.read_text() for p in ROOT.rglob('*') if p.suffix in ('.py', '.js', '.html') and p.name != 'i18n-en.js')
    source = source.replace('&lt;', '<').replace('&gt;', '>')
    composed = {'大量失败 <50%', '每天请求次数', '每小时请求次数', '每天Token', '每小时Token'}  # built from two literals in JS
    stale = [k for k in exact if k not in source and k not in composed]
    assert not stale, stale


class Texts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
        for k, v in attrs:
            if v and k in ('placeholder', 'title', 'aria-label', 'alt') and HAN.search(v):
                self.found.append(v)

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and HAN.search(data):
            self.found.append(data)


@pytest.mark.parametrize('page', ['lite.html', 'index.html', 'access-error.html'])
def test_every_chinese_text_in_pages_has_english(page):
    parser = Texts()
    parser.feed((STATIC/page).read_text())
    missing = [t.strip() for t in parser.found if left(i18n.translate(t.replace('{{VERSION}}', '1.0.0').replace('{{TITLE}}', 'Title')))]
    assert not missing, missing


def literals(path):
    text = re.sub(r'(?<![:\'"\\])//[^\n]*', '', path.read_text())
    for m in re.finditer(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"|`((?:[^`\\]|\\.)*)`", text):
        value = next(g for g in m.groups() if g is not None)
        if m.group(3) is not None:
            n = iter(range(99))
            value = re.sub(r'\$\{(?:[^{}]|\{[^{}]*\})*\}', lambda _: '{%d}' % next(n), value)
        for piece in re.split(r'<[^>]*>', value):
            piece = piece.replace('\\n', '\n').strip()
            if HAN.search(piece):
                yield piece


# Pieces of a sentence glued together in JS; the whole sentence is a template entry instead.
FRAGMENTS = {'{0}首次 {1} · 最近 {2}{3}{4}', '恢复（', '的 Discord 头像', '/static/猫娘图标.webp', '近 7 天每天成功次数', '⏳ Google 说这个账号额度用完了，约', '）恢复。'}


@pytest.mark.parametrize('script', ['lite.js', 'app.js', 'admin-live.js', 'admin-stats.js', 'quota.js', 'load.js', 'native-cookie.js'])
def test_every_chinese_string_in_scripts_has_english(script):
    missing = [s for s in literals(STATIC/script) if s not in FRAGMENTS and left(i18n.translate(s))]
    assert not missing, missing


def server_messages():
    for path in sorted(ROOT.glob('*.py')):
        if path.name == 'i18n.py':
            continue
        for m in re.finditer(r"(?<![fA-Za-z])'((?:[^'\\\n]|\\.)*)'", path.read_text()):
            value = m.group(1)
            if HAN.search(value) and not re.search(r'\b(INSERT|UPDATE|SELECT)\b', value) and '<h1' not in value:
                yield path.name, value


# Keywords matched against engine text, prefixes completed by a template, and an operator-only startup error.
NOT_SHOWN = {'未登录', '已失效', '账号列表为空', '已退出', '账户池', '账号池', '账号载入失败：', '账号需要处理：', '，请稍后再试',
             '同步失败：', '重启失败：', '管理员密码必须通过受限配置提供，至少 16 位'}


def test_every_server_message_has_english():
    missing = [(f, s) for f, s in server_messages() if s not in NOT_SHOWN and left(i18n.translate(s))]
    assert not missing, missing


def test_templates_plurals_and_untranslated_text():
    assert i18n.translate('近 30 天 1 次请求') == '1 request in the last 30 days'
    assert i18n.translate('近 30 天 12 次请求') == '12 requests in the last 30 days'
    assert i18n.translate('账号载入失败：Google 登录已失效，请重新登录后导入') == 'Account failed to load: Google sign-in has expired; sign in again, then import'
    assert i18n.translate('成功 26 次，额度用完 7 次，已取消 5 次') == 'Succeeded 26, Out of quota 7, Cancelled 5'
    assert i18n.translate('主力号') == '主力号', 'user content without an entry is left alone'
    assert i18n.translate_json({'label': '可用', 'text': '可用', 'items': ['已停用']}) == {'label': '可用', 'text': 'Ready', 'items': ['Paused']}


def test_language_choice():
    assert i18n.lang_of({}) == 'zh'
    assert i18n.lang_of({'accept-language': 'en-US,en;q=0.9'}) == 'en'
    assert i18n.lang_of({'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8'}) == 'zh'
    assert i18n.lang_of({'accept-language': 'en-US'}, {'xgf_lang': 'zh'}) == 'zh'
    assert i18n.lang_of({'accept-language': 'zh-CN'}, {'xgf_lang': 'en'}) == 'en'


def test_recovery_message_english_uses_utc():
    now = 1795510200  # 2026-11-24 08:50 UTC = 16:50 Beijing
    zh = gateway.recovery_message(1800, now)
    en = gateway.recovery_message(1800, now, lang='en')
    assert '北京时间 11-24 17:20' in zh
    assert en.endswith('Available again at 09:20 UTC on Nov 24 (in about 30 min).') and not HAN.search(en)
    assert gateway.recovery_message(0, now, lang='en').endswith('Please try again later.')


def test_api_errors_follow_accept_language(tmp_path):
    app = create_app(tmp_path, 'test-password-long-enough', 'https://aistudio.tizenty.xyz/lite')
    with TestClient(app, base_url='https://aistudio.tizenty.xyz') as c:
        assert c.get('/lite/api/me').json()['detail'] == '请先登录'
        assert c.get('/lite/api/me', headers={'Accept-Language': 'en-US,en;q=0.9'}).json()['detail'] == 'Please sign in first'
        assert c.get('/lite/api/me', cookies={'xgf_lang': 'en'}).json()['detail'] == 'Please sign in first'
        bad = {'Authorization': 'Bearer sk-nope', 'Accept-Language': 'en'}
        assert c.get('/lite/v1/models', headers=bad).json()['detail'] == 'The API key is invalid or revoked'
        assert c.get('/lite/v1/models', headers={'Authorization': 'Bearer sk-nope'}).json()['detail'] == 'API Key 无效或已撤销'
        cfg = c.get('/lite/api/config', headers={'Accept-Language': 'en'}).json()
        assert [r['badge'] for r in cfg['routes'] if r.get('badge')] == ['Recommended in China']
        page = c.get('/lite/access-denied', headers={'Accept-Language': 'en'}).text
        assert '<html lang="en"' in page and 'you can\'t come in, meow' in page.replace('&#x27;', "'")


def test_all_accounts_exhausted_in_english_keeps_retry_after(tmp_path):
    fake = FakeEngine().start()
    try:
        app, s, alice, bob, c = portal(tmp_path, fake)
        s.add_account(alice['id'], 'a@gmail.com', cookie_payload('a@gmail.com'))
        key = s.create_key(alice['id'], 'k')['key']
        fake.quota = {'a@gmail.com': 0}
        with c:
            for _ in range(100):
                if 'a@gmail.com' in fake.accounts:
                    break
                import time
                time.sleep(.05)
            r = c.post('/v1/chat/completions', json={'model': 'gemini-3-pro', 'messages': [{'role': 'user', 'content': 'hi'}]},
                       headers={'Authorization': 'Bearer '+key, 'Accept-Language': 'en'})
    finally:
        fake.stop()
    assert r.status_code == 429 and int(r.headers['retry-after']) > 0
    msg = r.json()['error']['message']
    assert 'Available again at' in msg and 'UTC' in msg and not HAN.search(msg)


def test_csv_header_in_english(tmp_path):
    app = create_app(tmp_path, 'test-password-long-enough', 'https://aistudio.tizenty.xyz/lite')
    s = app.state.store
    s.save_settings({'public_login': True})
    u = s.discord_user('100000000000000001', 'Alice')
    s.mark_verified(u['id'])
    token, _ = s.login(u['id'])
    with TestClient(app, base_url='https://aistudio.tizenty.xyz') as c:
        c.cookies.set('portal_session', token)
        zh = c.get('/lite/api/records.csv').text
        en = c.get('/lite/api/records.csv', cookies={'xgf_lang': 'en'}).text
    assert zh.lstrip('\ufeff').startswith('模型,状态')
    assert en.lstrip('\ufeff').startswith('Model,Status,Duration (s)')
