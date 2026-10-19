import asyncio
import json
import math
import os
import re
import secrets
import time
from collections import defaultdict, deque
from html import escape
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode, urlsplit

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from starlette.routing import Mount, compile_path

from portal.accounts import normalize_cookie, normalize_email
from portal.anti_truncation import PREFIX, augment_models, eligible, inject, normalize, transform_stream
from portal.channels import split_catalogue, split_model
from portal.oauth import OAuthStates, discord_identity
from portal.quota import choose_account, reference_quota
from portal.store import DEFAULTS, Store, digest
from portal.usage import UsageMeter
from portal.workers import BrokerClient

VERSION = '0.8.2'


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Preferences(Strict):
    anti_truncation: StrictBool


class Login(Strict):
    password: str = Field(max_length=256)


class Label(Strict):
    label: str = Field(min_length=1, max_length=80)


class AccountUpdate(Strict):
    label: str | None = Field(default=None, min_length=1, max_length=80)
    enabled: bool | None = None
    plan: Literal['free','pro'] | None = None
    email: str | None = None

    @field_validator('email')
    @classmethod
    def email_format(cls, value):
        return normalize_email(value) if value is not None else value


class Import(Label):
    email: str | None = None

    @field_validator('email')
    @classmethod
    def email_format(cls, value):
        return normalize_email(value) if value is not None else value

    storage_state: dict
    locale: str = 'en-US'
    timezone: str = 'America/Los_Angeles'
    fingerprint: dict | None = None


class Settings(Strict):
    site_name: str = Field(default=DEFAULTS['site_name'], min_length=1, max_length=60)
    public_login: bool = False
    discord_client_id: str = ''
    discord_client_secret: str | None = None
    blocked_guilds: list[str] = Field(default_factory=list, max_length=100)
    discord_allowlist: list[str] = Field(default_factory=list, max_length=100)
    block_enabled: bool = False
    verification_hours: int = Field(default=24, ge=1, le=168)
    requests_per_minute: int = Field(default=20, ge=1, le=120)
    max_accounts: int = Field(default=5, ge=1, le=20)
    max_keys: int = Field(default=10, ge=1, le=30)
    max_workers: int = Field(default=6, ge=1, le=8)
    global_concurrency: int = Field(default=3, ge=1, le=4)

    @field_validator('discord_allowlist')
    @classmethod
    def validate_allowlist(cls, values):
        if any(not re.fullmatch(r'[0-9]{15,22}', value) for value in values):
            raise ValueError('白名单须填写15至22位 Discord 用户 ID，每行一个')
        return list(dict.fromkeys(values))


def validate_account(p):
    state = p.get('storage_state', {})
    cookies = state.get('cookies')
    if not isinstance(cookies, list) or len(cookies) > 300:
        raise HTTPException(422, '认证文件缺少 cookies')
    cookies = [normalize_cookie(c) for c in cookies]
    state['cookies'] = cookies
    required = {'SAPISID', '__Secure-1PAPISID', '__Secure-3PAPISID'}
    found = set()
    for cookie in cookies:
        if not isinstance(cookie, dict):
            raise HTTPException(422, 'Cookie 格式错误')
        domain = str(cookie.get('domain', '')).lstrip('.')
        if not (domain == 'google.com' or domain.endswith('.google.com')):
            raise HTTPException(422, '仅支持 Google 认证状态')
        if not isinstance(cookie.get('value'), str) or len(cookie['value']) > 20000:
            raise HTTPException(422, 'Cookie 值无效')
        expires = cookie.get('expires', -1)
        if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not math.isfinite(expires):
            raise HTTPException(422, 'Cookie 有效期格式错误')
        if not isinstance(cookie.get('name'), str) or not cookie['name'] or len(cookie['name']) > 256:
            raise HTTPException(422, 'Cookie 名称格式错误')
        applies = domain in ['google.com', 'aistudio.google.com'] and cookie.get('path', '/') == '/'
        if cookie['value'] and applies and (expires == -1 or expires > time.time()):
            found.add(cookie['name'])
    if not required <= found:
        raise HTTPException(422, '缺少有效 AI Studio 登录 Cookie，请重新导出')
    state['cookies'] = [normalize_cookie(c) for c in cookies]
    if not re.fullmatch(r'[a-zA-Z-]{2,24}', p['locale']) or not re.fullmatch(r'[a-zA-Z0-9_+/-]{1,80}', p['timezone']):
        raise HTTPException(422, '语言或时区格式无效')


def create_app(directory=None, admin_password=None, origin=None, broker=None):
    directory = Path(directory or os.environ.get('PORTAL_DATA', '/data/aistudio-portal'))
    origin = (origin or os.environ.get('PORTAL_ORIGIN', 'http://127.0.0.1:8088')).rstrip('/')
    parsed_origin = urlsplit(origin)
    prefix = parsed_origin.path.rstrip('/')
    if prefix and not re.fullmatch(r'/[a-zA-Z0-9_-]+', prefix):
        raise RuntimeError('Portal prefix must be one path segment')
    host_origin = parsed_origin.scheme+'://'+parsed_origin.netloc
    cookie_path = prefix or '/'
    admin_path = '/guanlimanage' if prefix else '/admin'
    password = admin_password or os.environ.get('PORTAL_ADMIN_PASSWORD')
    if not password or len(password) < 16:
        raise RuntimeError('管理员密码必须通过受限配置提供，至少 16 位')
    store = Store(directory, password)
    workers = broker or BrokerClient(os.environ.get('PORTAL_BROKER', '/run/aistudio-portal/broker.sock'))
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    states = OAuthStates()
    limits = defaultdict(deque)
    userlocks = defaultdict(asyncio.Lock)
    active = set()
    secure = origin.startswith('https://')
    redirect_uri = origin + '/auth/discord/callback'

    def rate(key, n, seconds=60):
        now = time.monotonic()
        q = limits[key]
        while q and q[0] < now-seconds:
            q.popleft()
        if len(q) >= n:
            raise HTTPException(429, '请求过快，请稍后再试', headers={'Retry-After': str(seconds)})
        q.append(now)
        if len(limits) > 10000:
            for k in list(limits):
                if not limits[k] or limits[k][-1] < now-600:
                    limits.pop(k, None)

    def verify_access(user):
        if not user or user['disabled']:
            raise HTTPException(401, '请先登录')
        if user['role'] != 'admin':
            s = store.settings()
            if not s['public_login']:
                raise HTTPException(403, '公共访问尚未开放')
            if s['block_enabled'] and (user['policy'] != s['policy'] or time.time()-user['verified'] > s['verification_hours']*3600):
                raise HTTPException(403, '请重新通过 Discord 登录验证')
        return user

    def auth(request, admin=False, csrf=False):
        user = verify_access(store.session(request.cookies.get('portal_session', '')))
        if admin and user['role'] != 'admin':
            raise HTTPException(403, '仅管理员可访问')
        if csrf and not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), user['csrf']):
            raise HTTPException(403, '操作校验失效，请刷新重试')
        return user

    @app.middleware('http')
    async def security(request: Request, call_next):
        length = request.headers.get('content-length')
        if length and (not length.isdigit() or int(length) > 2*1024*1024):
            return JSONResponse({'detail':'请求体过大'}, 413)
        # Management accepts bounded bodies only, including chunked uploads.
        if request.method not in ['GET','HEAD']:
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > 2*1024*1024:
                    return JSONResponse({'detail':'请求体过大'}, 413)
            request._body = bytes(data)
        if request.url.path.startswith(prefix+'/api/') and request.method not in ['GET','HEAD']:
            if request.headers.get('origin') and request.headers['origin'].rstrip('/') != host_origin:
                return JSONResponse({'detail':'来源验证失败'}, 403)
        response = await call_next(request)
        response.headers.update({'X-Content-Type-Options':'nosniff', 'Referrer-Policy':'no-referrer',
                                 'X-Frame-Options':'DENY', 'Cache-Control':'no-store',
                                 'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://cdn.discordapp.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({'detail':str(exc)[:150]}, 400)

    def error_page(status, detail, denied=False):
        template = (Path(__file__).parent/'static/access-error.html').read_text()
        values = {
            'BASE': prefix, 'HOME': prefix or '/', 'VERSION': VERSION,
            'STATUS': str(status), 'MODE': 'denied' if denied else 'general',
            'HIDDEN': '' if denied else 'hidden',
            'TITLE': '你的账号脏脏的，不允许进去喵' if denied else str(detail),
            'LABEL': '猫猫的结界 · 暂不放行' if denied else '登录验证 · 暂未完成',
            'DESCRIPTION': '当前账号不符合本站访问规则，暂时无法进入。猫猫会认真守住这道门。' if denied else '这次登录验证未能完成。请返回用户页，重新发起 Discord 登录。',
            'HELP': '如果你认为这是误判，请联系站点管理员核实。' if denied else '若反复出现此提示，请稍后重试或联系管理员。',
        }
        for key, value in values.items():
            template = template.replace('{{'+key+'}}', escape(value, quote=True))
        if denied:
            template = template.replace('<h1 id="error-title">你的账号脏脏的，不允许进去喵</h1>', '<h1 id="error-title"><span>你的账号脏脏的，</span><span>不允许进去喵</span></h1>')
        if not denied:
            template = re.sub(r'<figure\b.*?</figure>', '', template, flags=re.DOTALL)
        response = HTMLResponse(template, status_code=status)
        response.delete_cookie('oauth_browser', path=cookie_path)
        return response

    @app.exception_handler(HTTPException)
    async def browser_auth_error(request, exc):
        if request.url.path.startswith(prefix+'/auth/') and 'text/html' in request.headers.get('accept',''):
            return error_page(exc.status_code, exc.detail, exc.status_code == 403 and exc.detail == '当前账号不符合本站访问规则')
        return await http_exception_handler(request, exc)

    @app.get('/access-denied', include_in_schema=False)
    def denied_preview():
        return error_page(403, '当前账号不符合本站访问规则', True)

    @app.get('/health')
    def health():
        return {'status':'ok', 'version':VERSION}

    @app.get('/api/config')
    def config():
        s = store.settings()
        return {'site_name':s['site_name'], 'version':VERSION, 'discord_ready':bool(s['public_login'] and s['discord_client_id'] and s['discord_client_secret']),
                'api_base':origin+'/v1', 'callback_url':redirect_uri}

    @app.post('/api/auth/admin')
    def admin_login(payload: Login, request: Request):
        rate('admin:'+request.client.host, 8, 300)
        hashed = store.one("SELECT value FROM metadata WHERE key='admin_hash'")['value']
        try:
            PasswordHasher().verify(hashed, payload.password)
        except (VerifyMismatchError, VerificationError):
            raise HTTPException(401, '账号或密码错误') from None
        token, csrf = store.login('admin')
        response = JSONResponse({'csrf':csrf})
        response.set_cookie('portal_session', token, max_age=86400, secure=secure, httponly=True, samesite='lax', path=cookie_path)
        store.audit('admin', 'login')
        return response

    @app.post('/api/auth/logout')
    def logout(request: Request):
        auth(request, csrf=True)
        store.run('DELETE FROM sessions WHERE hash=?', (digest(request.cookies.get('portal_session','')),))
        r = JSONResponse({'ok':True})
        r.delete_cookie('portal_session', path=cookie_path)
        return r

    @app.get('/api/me')
    def me(request: Request):
        u = auth(request)
        avatar_url = None
        if u.get('discord_id'):
            did=u['discord_id']
            avatar_url = ('https://cdn.discordapp.com/avatars/'+did+'/'+u['avatar']+'.png?size=128') if u.get('avatar') else ('https://cdn.discordapp.com/embed/avatars/'+str((int(did)>>22)%6)+'.png')
        return {k:u[k] for k in ['id','name','role','csrf']} | {'avatar_url':avatar_url}

    @app.get('/auth/discord')
    def discord_start(request: Request):
        rate('oauth:'+request.client.host, 20)
        s = store.settings()
        if not secure or not s['public_login'] or not s['discord_client_id'] or not s['discord_client_secret']:
            raise HTTPException(503, 'Discord 登录未配置或尚未开放')
        browser = secrets.token_urlsafe(32)
        state = states.issue(browser, s['policy'])
        query = urlencode({'client_id':s['discord_client_id'], 'redirect_uri':redirect_uri, 'response_type':'code',
                           'scope':'identify guilds' if s['block_enabled'] else 'identify', 'state':state, 'prompt':'consent'})
        r = RedirectResponse('https://discord.com/oauth2/authorize?'+query, 302)
        r.set_cookie('oauth_browser', browser, max_age=600, httponly=True, secure=secure, samesite='lax', path=cookie_path)
        return r

    @app.get('/auth/discord/callback')
    async def discord_callback(request: Request, code: str = '', state: str = ''):
        s = store.settings()
        if not states.consume(state, request.cookies.get('oauth_browser',''), s['policy']):
            raise HTTPException(400, '登录请求无效或已过期')
        if not s['public_login']:
            raise HTTPException(403, '公共登录已关闭')
        try:
            profile = await discord_identity(code, s, redirect_uri)
        except PermissionError:
            store.audit('anonymous', 'discord_policy_denied')
            raise HTTPException(403, '当前账号不符合本站访问规则') from None
        except Exception:
            raise HTTPException(503, 'Discord 验证失败或被限流，请稍后重新登录') from None
        if not isinstance(profile, dict) or not re.fullmatch(r'\d{15,22}', str(profile.get('id',''))):
            raise HTTPException(503, 'Discord 返回无效')
        name = profile.get('global_name') or profile.get('username') or 'Discord 用户'
        if not isinstance(name, str):
            raise HTTPException(503, 'Discord 返回无效')
        if not store.settings()['public_login']:
            raise HTTPException(403, '公共登录已关闭')
        u = store.discord_user(str(profile['id']), name, profile.get('avatar'))
        if u['disabled']:
            raise HTTPException(403, '账号已禁用')
        if store.settings()['policy'] != s['policy']:
            raise HTTPException(403, '访问规则已更新，请重新登录')
        if not store.mark_verified(u['id'], s['policy']):
            raise HTTPException(403, '访问规则已更新，请重新登录')
        if str(profile['id']) in s['discord_allowlist']:
            store.audit(u['id'], 'discord_allowlist_login')
        token, _ = store.login(u['id'])
        r = RedirectResponse(prefix or '/', 302)
        r.set_cookie('portal_session', token, max_age=86400, httponly=True, secure=secure, samesite='lax', path=cookie_path)
        r.delete_cookie('oauth_browser', path=cookie_path)
        return r

    @app.get('/api/admin/settings')
    def settings_get(request: Request):
        auth(request, admin=True)
        return store.settings(public=True) | {'callback_url':redirect_uri}

    @app.put('/api/admin/settings')
    def settings_put(payload: Settings, request: Request):
        auth(request, admin=True, csrf=True)
        p = payload.model_dump(exclude_unset=True)
        if p.get('discord_client_secret') is None:
            p.pop('discord_client_secret', None)
        if 'discord_client_id' in p and p['discord_client_id'] and not re.fullmatch(r'\d{15,22}', p['discord_client_id']):
            raise HTTPException(422, 'Client ID 格式无效')
        if any(not re.fullmatch(r'\d{15,22}', x) for x in p.get('blocked_guilds', [])):
            raise HTTPException(422, '服务器 ID 格式无效')
        if p.get('discord_client_secret') and len(p['discord_client_secret']) > 256:
            raise HTTPException(422, 'Client Secret 太长')
        merged = store.settings() | p
        if merged['block_enabled'] and not merged['blocked_guilds']:
            raise HTTPException(422, '开启社区屏蔽须填写服务器 ID')
        if merged['public_login'] and (not secure or not merged['discord_client_id'] or not merged['discord_client_secret']):
            raise HTTPException(422, '开放公共登录前须配置 Discord 和 HTTPS')
        store.save_settings(p)
        return store.settings(public=True) | {'callback_url':redirect_uri}

    @app.get('/api/admin/stats')
    def admin_stats(request: Request, days: int = Query(7,ge=1,le=30), limit: int = Query(20,ge=1,le=100), offset: int = Query(0,ge=0), q: str = Query('',max_length=100)):
        auth(request, admin=True)
        from portal.admin_stats import admin_statistics
        return admin_statistics(store,days=days,limit=limit,offset=offset,query=q.strip())

    @app.get('/api/admin/users')
    def users(request: Request):
        auth(request, admin=True)
        return store.all('SELECT id,name,role,disabled,created,verified FROM users ORDER BY created DESC')

    @app.post('/api/admin/users/{uid}/toggle')
    async def user_toggle(uid: str, request: Request):
        auth(request, admin=True, csrf=True)
        if uid == 'admin' or not store.user(uid):
            raise HTTPException(404)
        store.run('UPDATE users SET disabled=1-disabled WHERE id=?', (uid,))
        store.run('DELETE FROM sessions WHERE user_id=?', (uid,))
        async with userlocks[uid]:
            await workers.stop(uid)
        store.audit('admin', 'user_access_toggled')
        return {'ok':True}

    @app.get('/api/accounts')
    def accounts(request: Request):
        return store.accounts(auth(request)['id'])

    @app.post('/api/accounts/import', status_code=201)
    async def account_import(payload: Import, request: Request):
        u = auth(request, csrf=True)
        rate('import:'+u['id'], 6)
        p = payload.model_dump(exclude_none=True)
        validate_account(p)
        from portal.accounts import validate_selected_identity
        validate_selected_identity(p)
        async with userlocks[u['id']]:
            await workers.stop(u['id'])
            aid = store.add_account(u['id'], payload.label, p)
        return {'id':aid, 'label':payload.label, 'status':'待首次调用验证'}

    @app.patch('/api/accounts/{aid}')
    async def account_update(aid: str, payload: AccountUpdate, request: Request):
        u = auth(request, csrf=True)
        async with userlocks[u['id']]:
            account = store.account(u['id'], aid)
            if not account:
                raise HTTPException(404, '账号不存在')
            values = payload.model_dump(exclude_none=True)
            if not values:
                raise HTTPException(422, '未提供修改内容')
            await workers.stop(u['id'])
            if 'email' in values:
                data = account['payload'] | {'email':values.pop('email')}
                from portal.accounts import validate_selected_identity
                validate_selected_identity(data)
                store.run('UPDATE accounts SET payload=? WHERE user_id=? AND id=?', (store.encrypt(data),u['id'],aid))
                values.update(status='unverified',checked=None,note='邮箱已更新，请重新检查')
            store.update_account(u['id'], aid, values)
            store.audit(u['id'], 'account_updated')
        return {'ok':True}

    @app.post('/api/accounts/{aid}/check')
    async def account_check(aid: str, request: Request):
        u = auth(request, csrf=True)
        rate('check:'+u['id'], 3)
        async with userlocks[u['id']]:
            account = store.account(u['id'], aid)
            if not account:
                raise HTTPException(404, '账号不存在')
            try:
                result = await workers.check(u['id'], account, store.settings()['max_workers'])
            except HTTPException as exc:
                message = exc.detail if exc.status_code == 422 else '独立账号池未就绪，请稍后重试或重新导入账号'
                result = {'status':'error', 'models':0, 'message':message}
            except Exception:
                result = {'status':'error', 'models':0, 'message':'检查未通过：登录失效、网络故障或服务器繁忙，请稍后重试'}
            store.update_account(u['id'], aid, {'status':result['status'],'checked':time.time(),'note':result['message']})
            store.audit(u['id'], 'account_checked')
            return result

    @app.delete('/api/accounts/{aid}')
    async def account_delete(aid: str, request: Request):
        u = auth(request, csrf=True)
        async with userlocks[u['id']]:
            if not store.account(u['id'], aid):
                raise HTTPException(404, '账号不存在')
            await workers.stop(u['id'])
            store.delete_account(u['id'], aid)
        return {'ok':True}

    @app.post('/api/pool/stop')
    async def pool_stop(request: Request):
        u = auth(request, csrf=True)
        async with userlocks[u['id']]:
            await workers.stop(u['id'])
        return {'ok':True}

    @app.get('/api/keys')
    def keys(request: Request):
        return store.keys(auth(request)['id'])

    @app.post('/api/keys', status_code=201)
    def key_create(payload: Label, request: Request):
        return store.create_key(auth(request, csrf=True)['id'], payload.label)

    @app.delete('/api/keys/{kid}')
    def key_revoke(kid: str, request: Request):
        if not store.revoke_key(auth(request, csrf=True)['id'], kid):
            raise HTTPException(404)
        return {'ok':True}

    @app.get('/api/stats')
    def stats(request: Request):
        return store.stats(auth(request)['id'])

    @app.get('/api/preferences')
    def get_preferences(request: Request):
        return store.preferences(auth(request)['id'])

    @app.put('/api/preferences')
    def put_preferences(request: Request, values: Preferences):
        return store.save_preferences(auth(request, csrf=True)['id'], values.anti_truncation)

    @app.get('/api/quota')
    def quota(request: Request):
        u = auth(request)
        rows, s = store.accounts(u['id']), store.settings()
        return {'google_remaining':None, 'google_note':'当前上游未提供可靠的剩余额度，不以调用量推算',
                'accounts':{'used':len(rows),'enabled':sum(bool(a['enabled']) for a in rows),'limit':s['max_accounts']},
                'keys':{'used':len(store.keys(u['id'])),'limit':s['max_keys']},
                'requests_per_minute':s['requests_per_minute'], 'summary':store.stats(u['id'])['summary']} | reference_quota(store,u['id'])

    @app.get('/api/records')
    def records(request: Request, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=1000000)):
        return store.records(auth(request)['id'], limit, offset)

    @app.get('/api/records.csv')
    def records_csv(request: Request):
        import csv
        import io
        u = auth(request)
        data = store.records(u['id'], 10000)['items']
        out = io.StringIO(); writer = csv.writer(out)
        writer.writerow(['模型','状态','耗时秒','首字耗时秒','输入Token','输出Token','时间UTC','账号','请求类型'])
        for r in data:
            model = r['model']
            if model.lstrip().startswith(('=','+','-','@')):
                model = "'"+model
            writer.writerow([model,r['status'],r['duration'],r['first_token_seconds'],r['input_tokens'],r['output_tokens'],time.strftime('%Y-%m-%d %H:%M:%S',time.gmtime(r['created'])),
                             ("'" if (r['account_label'] or '').lstrip().startswith(('=','+','-','@')) else '')+(r['account_label'] or ('账号已删除' if r['account_id'] else '历史未归属')),
                             '生成调用' if r['generation']==1 else '查询 / 其他' if r['generation']==0 else '历史类型未记录'])
        from fastapi.responses import Response
        return Response('\ufeff'+out.getvalue(), media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename=usage-records.csv'})

    async def proxy(path, request):
        raw = request.headers.get('authorization','')
        key = raw[7:] if raw.lower().startswith('bearer ') else request.headers.get('x-api-key') or request.headers.get('x-goog-api-key') or ''
        u = store.key_user(key)
        if not u:
            raise HTTPException(401, 'API Key 无效或已撤销')
        verify_access(u)
        s = store.settings()
        rate('api:'+u['id'], s['requests_per_minute'])
        if not any(a['enabled'] for a in store.accounts(u['id'])):
            raise HTTPException(503, '未导入自己的 Google 账号，不会使用其他用户账号')
        if len(active) >= s['global_concurrency'] or userlocks[u['id']].locked():
            raise HTTPException(429, '当前请求繁忙，请稍后重试')
        body = await request.body()
        parsed = None
        try:
            parsed = json.loads(body) if body else None
        except ValueError:
            pass
        model = str(parsed.get('model','')) if isinstance(parsed,dict) else ''
        path_model = ''
        if not model and path.startswith(('/v1beta/models/', '/v1/models/')):
            path_model = path.split('/models/', 1)[1].split(':', 1)[0]
            model = path_model
        anti = model.startswith(PREFIX)
        enabled = store.preferences(u['id'])['anti_truncation']
        base_model, channel = split_model(model.removeprefix(PREFIX) if anti else model)
        if anti and (not enabled or request.method != 'POST' or path != '/v1/chat/completions' or not eligible(base_model)):
            raise HTTPException(400, '请先开启防截断，仅支持带前缀的 OpenAI 聊天模型')
        if channel and path_model:
            path = path.replace('/models/'+path_model, '/models/'+base_model, 1)
        if not anti and PREFIX in path:
            raise HTTPException(400, '防截断模型仅支持 OpenAI /v1/chat/completions')
        if len(active) >= s['global_concurrency'] or userlocks[u['id']].locked():
            raise HTTPException(429, '当前请求繁忙，请稍后重试')
        active.add(u['id'])
        await userlocks[u['id']].acquire()
        start = time.monotonic()
        aid, generation, synthetic_name, buffered = None, False, '', None
        client = httpx.AsyncClient(timeout=httpx.Timeout(330, connect=15), follow_redirects=False)
        try:
            if channel and isinstance(parsed, dict):
                parsed = dict(parsed, model=base_model)
                body = json.dumps(parsed, ensure_ascii=True).encode()
            if anti:
                parsed = dict(parsed, model=base_model)
                parsed, synthetic_name = await inject(parsed)
                body = json.dumps(parsed, ensure_ascii=True).encode()
            if request.method == 'POST' and path == '/v1/chat/completions' and isinstance(parsed,dict) and parsed.get('stream') is True:
                options=parsed.get('stream_options')
                parsed=dict(parsed,stream_options=(options if isinstance(options,dict) else {}) | {'include_usage':True})
                body=json.dumps(parsed,ensure_ascii=True).encode()
            if not model and path.startswith('/v1beta/models/'):
                model = path.split('/models/',1)[1].split(':',1)[0]
            generation = request.method=='POST' and (path in ['/v1/chat/completions','/v1/completions','/v1/messages','/v1/responses','/v1/images/generations'] or ':generateContent' in path or ':streamGenerateContent' in path)
            accounts = [store.account(u['id'], a['id']) for a in store.accounts(u['id']) if a['enabled']]
            selected=choose_account(accounts,store,u['id'],base_model or model)
            aid=selected['id'] if selected else None
            worker_args = (u['id'], [selected] if selected else [], s['max_workers'])
            worker = await workers.ensure(*worker_args, channel=channel) if channel else await workers.ensure(*worker_args)
            # User supplied cookies, upstream keys, forwarding headers and target URLs never pass through.
            headers = {'Authorization':'Bearer '+worker['key']}
            for h in ['content-type','accept','anthropic-version']:
                if h in request.headers:
                    headers[h] = request.headers[h]
            params = [(k,v) for k,v in request.query_params.multi_items() if k not in ['key','api_key']]
            req = client.build_request(request.method, worker['url']+path, params=params, headers=headers, content=body)
            response = await client.send(req, stream=True)
            catalogue = request.method == 'GET' and path == '/v1/models'
            if response.is_success and (catalogue or (anti and 'text/event-stream' not in response.headers.get('content-type',''))):
                raw_response = bytearray()
                async for part in response.aiter_bytes():
                    raw_response.extend(part)
                    if len(raw_response) > 16 * 1024 * 1024:
                        raise ValueError('防截断响应过大')
                payload = json.loads(raw_response)
                if catalogue:
                    result = split_catalogue(payload)
                    if enabled:result = augment_models(result)
                else:
                    result = await normalize(payload, synthetic_name, model)
                buffered = json.dumps(result, ensure_ascii=False).encode()
        except BaseException as exc:
            await client.aclose()
            userlocks[u['id']].release()
            active.discard(u['id'])
            status = 499 if isinstance(exc, asyncio.CancelledError) else 422 if isinstance(exc, HTTPException) and exc.status_code == 422 else 503
            store.record(u['id'], u['key_id'], model, status, time.monotonic()-start, account_id=aid, generation=generation)
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, HTTPException) and exc.status_code == 422:
                raise
            raise HTTPException(503, '自己的账号池未就绪，账号可能失效；未使用其他用户账号') from None

        async def stream():
            meter = UsageMeter(started=start)
            status = response.status_code
            try:
                async def output():
                    if buffered is not None:
                        yield buffered
                    elif anti and response.is_success and 'text/event-stream' in response.headers.get('content-type',''):
                        async for part in transform_stream(response.aiter_bytes(), synthetic_name, model):
                            yield part
                    else:
                        async for part in response.aiter_bytes():
                            yield part
                async for chunk in output():
                    meter.feed(chunk)
                    yield chunk
            except BaseException:
                status = 499
                raise
            finally:
                await response.aclose()
                await client.aclose()
                userlocks[u['id']].release()
                active.discard(u['id'])
                store.record(u['id'], u['key_id'], model, status, time.monotonic()-start, meter.finish(), account_id=aid, generation=generation, first_token_seconds=meter.first_token_seconds if generation and status < 400 else None)
        return StreamingResponse(stream(), status_code=response.status_code,
            headers={'Content-Type':response.headers.get('content-type','application/json'), 'X-Accel-Buffering':'no'})

    @app.api_route('/v1/{rest:path}', methods=['GET','POST','DELETE'])
    async def openai(rest: str, request: Request):
        return await proxy('/v1/'+rest, request)

    @app.api_route('/v1beta/{rest:path}', methods=['GET','POST','DELETE'])
    async def gemini(rest: str, request: Request):
        return await proxy('/v1beta/'+rest, request)

    static = Path(__file__).parent/'static'
    if static.exists():
        app.mount('/static', StaticFiles(directory=static), name='static')

    def page(filename):
        text = (static/filename).read_text()
        text = text.replace('<html ', '<html data-portal-base="'+prefix+'" ')
        for path in ['/static/', '/auth/', '/api/']:
            text = text.replace('"'+path, '"'+prefix+path)
        text = text.replace('href="/admin"', 'href="'+admin_path+'"')
        text = text.replace('href="/"', 'href="'+(prefix or '/')+'"')
        return HTMLResponse(text)

    @app.get('/')
    def index():
        return page('lite.html')

    @app.get('/admin')
    def admin_index():
        return page('index.html')

    if prefix:
        for route in app.routes:
            route.path = prefix+route.path
            pattern = route.path+'/{path:path}' if isinstance(route, Mount) else route.path
            route.path_regex, route.path_format, route.param_convertors = compile_path(pattern)
        app.add_api_route(prefix, index, methods=['GET'])
        app.add_api_route(admin_path, admin_index, methods=['GET'])
        app.add_api_route('/health', health, methods=['GET'])
    return app
