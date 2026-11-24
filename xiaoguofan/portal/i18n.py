"""English variants of user-facing messages.

The dictionary lives in static/i18n-en.js (shared with the browser): the key is the
Chinese text, values may use {0}/{name} placeholders and {0|one|many} plurals.
Chinese stays the default; English is used when the request asks for it
(``xgf_lang`` cookie set by the language toggle, else the first Accept-Language tag).
"""
import json
import re
from functools import lru_cache
from pathlib import Path

DICT_PATH = Path(__file__).parent/'static'/'i18n-en.js'
HAN = re.compile(r'[\u3400-\u9fff]')
ALLOWED = re.compile(r'渡鸦の小锅饭|小锅饭 · 导出 JSON|小锅饭|猫佬|\[防截断\]')
PLACEHOLDER = re.compile(r'\{(\w+)\}')
FILL = re.compile(r'\{(\w+)(?:\|([^|}]*)\|([^}]*))?\}')
# JSON fields that hold user content (names, notes, model ids) are never rewritten.
SKIP_KEYS = frozenset({'label', 'email', 'name', 'model', 'site_name', 'account_label', 'key', 'id', 'avatar_url', 'user_name', 'engine_id'})


@lru_cache(maxsize=1)
def dictionary():
    text = DICT_PATH.read_text(encoding='utf-8')
    data = json.loads(text[text.index('window.XGF_EN=')+len('window.XGF_EN='):].strip().rstrip(';'))
    templates = []
    for key, value in data.get('templates', {}).items():
        names = []

        def part(piece):
            m = re.fullmatch(r'\{(\w+)\}', piece)
            if m:
                names.append(m.group(1))
                return r'([\s\S]+?)'
            return re.escape(piece)
        pattern = re.compile('^'+''.join(part(p) for p in re.split(r'(\{\w+\})', key))+'$')
        templates.append((len(PLACEHOLDER.sub('', key)), pattern, names, value))
    templates.sort(key=lambda t: -t[0])
    return data.get('exact', {}), templates


def _fill(value, values):
    def rep(m):
        name, one, many = m.groups()
        if name not in values:
            return m.group(0)
        v = values[name]
        if one is not None:
            return one if re.sub(r'[^\d.]', '', str(v)) == '1' else many
        return v
    return FILL.sub(rep, value)


_memo = {}


def translate(text, depth=0):
    """Chinese UI text -> English, or the text unchanged when there is no entry."""
    if not isinstance(text, str) or not HAN.search(text):
        return text
    if text in _memo:
        return _memo[text]
    if len(_memo) > 5000:
        _memo.clear()
    result = _memo[text] = _translate(text, depth)
    return result


def _translate(text, depth):
    exact, templates = dictionary()
    core = text.strip()
    lead = text[:len(text)-len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    if core in exact:
        return lead+exact[core]+trail
    best, best_left = None, None
    if depth < 8:
        # Most specific first; keep the first result with no Chinese left, else the one with the least.
        for _, pattern, names, value in templates:
            m = pattern.match(core)
            if not m:
                continue
            out = _fill(value, {n: translate(m.group(i+1), depth+1) for i, n in enumerate(names)})
            left = len(HAN.findall(ALLOWED.sub('', out)))
            if left == 0:
                return lead+out+trail
            if best_left is None or left < best_left:
                best, best_left = out, left
    return text if best is None else lead+best+trail


def lang_of(headers, cookies=None):
    """'en' or 'zh' for a request (headers/cookies are mappings)."""
    chosen = (cookies or {}).get('xgf_lang', '')
    if chosen in ('en', 'zh'):
        return chosen
    first = (headers.get('accept-language') or '').split(',')[0].split(';')[0].strip().lower()
    return 'en' if first.startswith('en') else 'zh'


def translate_json(value, key=None):
    if key in SKIP_KEYS:
        return value
    if isinstance(value, str):
        return translate(value)
    if isinstance(value, list):
        return [translate_json(v) for v in value]
    if isinstance(value, dict):
        return {k: translate_json(v, k) for k, v in value.items()}
    return value


def translate_html(html):
    """Translate text between tags and alt/title/aria-label attributes (server-rendered pages)."""
    html = re.sub(r'>([^<>]*[\u3400-\u9fff][^<>]*)<', lambda m: '>'+translate(m.group(1))+'<', html)
    html = re.sub(r'((?:alt|title|aria-label|placeholder)=")([^"]*[\u3400-\u9fff][^"]*)(")', lambda m: m.group(1)+translate(m.group(2))+m.group(3), html)
    return html.replace('<html lang="zh-CN"', '<html lang="en"', 1)


class I18nMiddleware:
    """Rewrites JSON messages to English for requests that ask for it.

    Portal API (``{prefix}/api/``): every JSON reply. OpenAI/Gemini/Anthropic routes: error
    replies only, so model ids and generated content are never touched. Streams pass through.
    """

    def __init__(self, app, prefix=''):
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = {k.decode('latin-1').lower(): v.decode('latin-1') for k, v in scope.get('headers', [])}
        cookies = {}
        for part in headers.get('cookie', '').split(';'):
            if '=' in part:
                k, v = part.split('=', 1)
                cookies[k.strip()] = v.strip()
        path = scope.get('path', '')
        api = path.startswith(self.prefix+'/api/')
        model_api = path.startswith(('/v1/', '/v1beta/', self.prefix+'/v1/', self.prefix+'/v1beta/')) or path in ('/v1', self.prefix+'/v1')
        if lang_of(headers, cookies) != 'en' or not (api or model_api):
            return await self.app(scope, receive, send)
        state = {'start': None, 'body': bytearray(), 'hold': False}

        async def wrapped(message):
            if message['type'] == 'http.response.start':
                ctype = dict((k.decode('latin-1').lower(), v.decode('latin-1')) for k, v in message.get('headers', [])).get('content-type', '')
                state['hold'] = ctype.startswith('application/json') and (api or message['status'] >= 400)
                if not state['hold']:
                    return await send(message)
                state['start'] = message
                return
            if message['type'] == 'http.response.body' and state['hold']:
                state['body'] += message.get('body', b'')
                if message.get('more_body'):
                    return
                body = bytes(state['body'])
                try:
                    body = json.dumps(translate_json(json.loads(body)), ensure_ascii=False).encode()
                except (ValueError, UnicodeDecodeError):
                    pass
                start = state['start']
                start['headers'] = [(k, v) for k, v in start.get('headers', []) if k.lower() != b'content-length']+[(b'content-length', str(len(body)).encode())]
                await send(start)
                return await send({'type': 'http.response.body', 'body': body})
            return await send(message)
        return await self.app(scope, receive, wrapped)
