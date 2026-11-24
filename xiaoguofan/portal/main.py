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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from starlette.routing import Mount, compile_path

from portal import gateway
from portal.accounts import account_email, normalize_cookie, normalize_email
from portal.anti_truncation import PREFIX, augment_models, eligible, inject, normalize, transform_stream
from portal.channels import split_model
from portal.engine import SCOPE_HEADER, SERVING_HEADER, EngineClient, broker_locator
from portal.oauth import OAuthStates, discord_identity
from portal.quota import reference_quota
from portal.store import DEFAULTS, Alerts, Store, digest
from portal.usage import UsageMeter

VERSION = '1.0.0'


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


class NativeVerify(Strict):
    cookie: str = Field(min_length=1,max_length=65536,repr=False)
    accounts_cookie: str = Field(default='',max_length=65536,repr=False)
    consent: StrictBool


class NativeImport(Label):
    ticket: str = Field(min_length=20,max_length=128,repr=False)
    authuser: int = Field(ge=0,le=99,strict=True)
    consent: StrictBool


class Settings(Strict):
    site_name: str = Field(default=DEFAULTS['site_name'], min_length=1, max_length=60)
    public_login: bool = False
    discord_client_id: str = ''
    discord_client_secret: str | None = None
    blocked_guilds: list[str] = Field(default_factory=list, max_length=100)
    discord_allowlist: list[str] = Field(default_factory=list, max_length=100)
    block_enabled: bool = False
    verification_hours: int = Field(default=24, ge=1, le=720)
    requests_per_minute: int = Field(default=20, ge=1, le=120)
    max_accounts: int = Field(default=5, ge=1, le=20)
    max_keys: int = Field(default=10, ge=1, le=30)
    # Legacy (per-user container era); kept so old settings still validate, ignored since 1.0.
    max_workers: int = Field(default=6, ge=1, le=100)
    global_concurrency: int = Field(default=3, ge=1, le=100)
    engine_concurrency: int = Field(default=48, ge=1, le=512)
    user_concurrency: int = Field(default=4, ge=1, le=32)
    rate_limit_enabled: bool = False

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


class BrokerLoad:
    """Read-only resource snapshot from the root broker (admin dashboard)."""

    def __init__(self, socket):
        self.socket = socket

    async def load(self):
        async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=self.socket), base_url='http://broker', timeout=5) as c:
            r = await c.post('/load', json={})
            r.raise_for_status()
            return r.json()

    async def restart(self):
        async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=self.socket), base_url='http://broker', timeout=330) as c:
            r = await c.post('/engine/restart', json={})
            r.raise_for_status()
            return r.json()


def create_app(directory=None, admin_password=None, origin=None, broker=None, engine=None):
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
    alerts = Alerts(store)
    broker_socket = os.environ.get('PORTAL_BROKER', '/run/aistudio-portal/broker.sock')
    broker = broker or BrokerLoad(broker_socket)
    engine = (engine or EngineClient(broker_locator(broker_socket))).attach(store, alerts)
    trusted_proxies = gateway.parse_networks(os.environ.get('PORTAL_TRUSTED_PROXIES', ''))
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.engine = engine
    app.state.alerts = alerts
    native_slots=asyncio.Semaphore(4)

    from fastapi.exceptions import RequestValidationError
    @app.exception_handler(RequestValidationError)
    async def safe_validation(request,exc):
        # Never echo sensitive Cookie values from Pydantic's `input` field.
        if '/api/accounts/native/' in request.url.path:
            return JSONResponse({'detail':'导入参数格式无效；请检查授权、长度和账号选择'},422)
        from fastapi.exception_handlers import request_validation_exception_handler
        return await request_validation_exception_handler(request,exc)

    import hashlib
    asset_versions = {}
    static_root = Path(__file__).parent/'static'
    if static_root.exists():
        for asset in static_root.rglob('*'):
            if asset.is_file():
                asset_versions[asset.relative_to(static_root).as_posix()] = hashlib.sha256(asset.read_bytes()).hexdigest()[:12]
    states = OAuthStates()
    limits = defaultdict(deque)
    userlocks = defaultdict(asyncio.Lock)
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def account_operation(uid):
        lock=userlocks[uid]
        if lock.locked():
            raise HTTPException(409,'账号操作进行中，请稍后重试；不要重复点击检查')
        async with lock:
            yield
    from portal.scheduling import Admission, BodyBudget, BoundedBodyMiddleware
    scheduler = Admission(lambda: store.settings()['engine_concurrency'], lambda: store.settings()['user_concurrency'])
    budget = BodyBudget()
    app.state.scheduler = scheduler
    app.state.body_budget = budget
    @asynccontextmanager
    async def lifespan(app):
        await engine.start()
        try:
            yield
        finally:
            await engine.close()
    app.router.lifespan_context = lifespan
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

    def client_ip(request):
        return gateway.client_ip(request.client.host if request.client else '', request.headers, trusted_proxies)

    def verify_access(user, api=False):
        if not user or user['disabled']:
            raise HTTPException(401, '请先登录')
        if user['role'] != 'admin':
            s = store.settings()
            if not s['public_login']:
                raise HTTPException(403, '公共访问尚未开放')
            # API keys keep working while the periodic Discord re-check is pending;
            # the re-check only gates the web pages. Admins disable users explicitly.
            if not api and s['block_enabled'] and (user['policy'] != s['policy'] or time.time()-user['verified'] > s['verification_hours']*3600):
                raise HTTPException(403, '本站 Discord 权限验证已到期或规则已更新，请重新登录；Google 账号和用量记录不受影响')
            # An admin removing someone from the allowlist is an explicit revocation, not a
            # periodic re-check, so it also stops that person's keys.
            if api and s['block_enabled'] and allowlist_revoked(user, s):
                raise HTTPException(403, '本站访问权限已被管理员收回')
        return user

    def allowlist_revoked(user, s):
        discord_id = dict(user).get('discord_id')
        if not discord_id or discord_id in s['discord_allowlist']:
            return False
        return bool(store.one("SELECT 1 x FROM audit WHERE actor=? AND action='discord_allowlist_login' LIMIT 1", (user['id'],)))

    def auth(request, admin=False, csrf=False):
        user = verify_access(store.session(request.cookies.get('portal_session', '')))
        if admin and user['role'] != 'admin':
            raise HTTPException(403, '仅管理员可访问')
        if csrf and not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), user['csrf']):
            raise HTTPException(403, '操作校验失效，请刷新重试')
        return user

    @app.middleware('http')
    async def security(request: Request, call_next):
        store.run('DELETE FROM native_pending WHERE expires<?',(time.time(),))
        if request.url.path.startswith(prefix+'/api/') and request.method not in ['GET','HEAD']:
            if request.headers.get('origin') and request.headers['origin'].rstrip('/') != host_origin:
                return JSONResponse({'detail':'来源验证失败'}, 403)
        response = await call_next(request)
        static_asset = request.url.path.startswith(prefix+'/static/') and response.status_code == 200
        if static_asset and request.query_params.get('v') and request.query_params['v'] == asset_versions.get(request.url.path[len(prefix)+8:]):
            cache = 'public, max-age=31536000, immutable'
        elif static_asset:
            cache = 'public, max-age=3600'
        else:
            cache = response.headers.get('Cache-Control') if request.url.path.startswith(('/v1/', '/v1beta/', prefix+'/v1/', prefix+'/v1beta/')) and response.headers.get('Cache-Control') else 'no-store'
        response.headers.update({'X-Content-Type-Options':'nosniff', 'Referrer-Policy':'no-referrer',
                                 'X-Frame-Options':'DENY', 'Cache-Control':cache,
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
        rate('admin:'+client_ip(request), 8, 300)
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
        store.run('DELETE FROM native_pending WHERE owner=? AND session=?',(auth(request)['id'],digest(auth(request)['csrf'])))
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
        rate('oauth:'+client_ip(request), 20)
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
        token, _ = store.login(u['id'], lifetime=2592000)
        r = RedirectResponse(prefix or '/', 302)
        r.set_cookie('portal_session', token, max_age=2592000, httponly=True, secure=secure, samesite='lax', path=cookie_path)
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
        async with account_operation(uid):
            store.run('UPDATE users SET disabled=1-disabled WHERE id=?', (uid,))
            store.run('DELETE FROM sessions WHERE user_id=?', (uid,))
        engine.request_sync()
        store.audit('admin', 'user_access_toggled')
        return {'ok':True}

    @app.get('/api/accounts')
    def accounts(request: Request):
        uid = auth(request)['id']
        view = engine.account_view(uid)
        return [a | {'live': view.get(a['id'], {'state':'pending','text':'正在准备，稍等片刻','tier':''})} for a in store.accounts(uid)]

    def ensure_unowned(uid, email):
        if email and store.email_owner(email, exclude_uid=uid):
            raise HTTPException(409, '这个 Google 账号已被其他用户导入；每个账号只能属于一个人，请换一个账号')

    @app.post('/api/accounts/import', status_code=201)
    async def account_import(payload: Import, request: Request):
        u = auth(request, csrf=True)
        rate('import:'+u['id'], 6)
        p = payload.model_dump(exclude_none=True)
        validate_account(p)
        from portal.accounts import validate_selected_identity
        validate_selected_identity(p)
        async with account_operation(u['id']):
            ensure_unowned(u['id'], account_email(p))
            try:
                aid = store.add_account(u['id'], payload.label, p)
            except ValueError as exc:
                raise HTTPException(422,str(exc)) from None
        engine.request_sync()
        return {'id':aid, 'label':payload.label, 'status':'已保存，正在载入'}

    @app.post('/api/accounts/native/verify')
    async def native_verify(payload: NativeVerify, request: Request):
        u=auth(request,csrf=True)
        rate('native:'+u['id'],3)
        if not payload.consent:raise HTTPException(422,'请先确认本人授权')
        from portal.native_cookie import verify_native
        if native_slots.locked():raise HTTPException(429,'身份查询繁忙，请稍后重试')
        async with native_slots:
            try:
                p,rows=await asyncio.wait_for(verify_native(payload.cookie,payload.accounts_cookie),45)
            except TimeoutError:
                raise HTTPException(504,'Google 身份查询超时，未保存；请稍后重试') from None
        # Recheck access after network I/O before issuing a session-bound ticket.
        u=auth(request,csrf=True)
        ticket=secrets.token_urlsafe(32)
        with store.lock:
            store.run('DELETE FROM native_pending WHERE expires<? OR owner=?',(time.time(),u['id']))
            if store.one('SELECT COUNT(*) n FROM native_pending')['n']>=500:
                raise HTTPException(503,'待确认数量已满，请稍后重试')
            store.run('INSERT INTO native_pending VALUES (?,?,?,?,?)',
                      (digest(ticket),u['id'],digest(u['csrf']),time.time()+300,store.encrypt({'payload':p,'rows':rows})))
        return {'ticket':ticket,'expires_in':300,'accounts':[{'email':r['email'],'authuser':r['authuser']} for r in rows],
                'message':('Google 默认账号页身份已核对（账号列表接口为空，仅支持默认账号）；未验证模型权限。选择后确认保存。' if p['storage_state'].get('xiaoguofan_identity_verification') else 'Google 身份已核对；未验证 AI Studio 模型权限。选择账号后确认保存。')}

    @app.post('/api/accounts/native/import',status_code=201)
    async def native_import(payload: NativeImport, request: Request):
        u=auth(request,csrf=True)
        if not payload.consent:raise HTTPException(422,'请先确认本人授权')
        if not payload.label.strip():raise HTTPException(422,'请填写账号备注')
        async with account_operation(u['id']):
            row=store.one('SELECT * FROM native_pending WHERE token=? AND owner=? AND session=? AND expires>?',
                          (digest(payload.ticket),u['id'],digest(u['csrf']),time.time()))
            if not row:raise HTTPException(422,'确认已过期或不属于当前会话，请重新核对 Cookie')
            pending=store.decrypt(row['encrypted'])
            selected=[r for r in pending['rows'] if r['authuser']==payload.authuser]
            if len(selected)!=1:raise HTTPException(422,'请选择 Google 返回的账号，不能手动更换邮箱')
            p=pending['payload']|{'email':selected[0]['email'],'label':payload.label}
            p['storage_state']['xiaoguofan_identity']=selected[0]
            validate_account(p)
            from portal.accounts import validate_selected_identity
            validate_selected_identity(p)
            ensure_unowned(u['id'], account_email(p))
            auth(request,csrf=True)
            if row['expires']<=time.time():raise HTTPException(422,'确认已过期，请重新核对 Cookie')
            # Serialized one-use consumption within the owner lock; no destructive overwrites.
            with store.lock:
                if not store.one('SELECT token FROM native_pending WHERE token=? AND owner=? AND session=? AND expires>?',(digest(payload.ticket),u['id'],digest(u['csrf']),time.time())):
                    raise HTTPException(422,'确认已取消或过期，请重新核对 Cookie')
                try:
                    aid=store.add_account(u['id'],payload.label,p)
                except ValueError as exc:
                    raise HTTPException(422,str(exc)) from None
                store.run('DELETE FROM native_pending WHERE token=?',(digest(payload.ticket),))
            engine.request_sync()
            return {'id':aid,'label':payload.label,'status':'身份已核对，正在载入'}

    @app.delete('/api/accounts/native/pending')
    def native_cancel(request: Request):
        u=auth(request,csrf=True)
        store.run('DELETE FROM native_pending WHERE owner=? AND session=?',(u['id'],digest(u['csrf'])))
        return {'ok':True}

    @app.patch('/api/accounts/{aid}')
    async def account_update(aid: str, payload: AccountUpdate, request: Request):
        u = auth(request, csrf=True)
        async with account_operation(u['id']):
            account = store.account(u['id'], aid)
            if not account:
                raise HTTPException(404, '账号不存在')
            values = payload.model_dump(exclude_none=True)
            if not values:
                raise HTTPException(422, '未提供修改内容')
            if 'email' in values:
                data = account['payload'] | {'email':values.pop('email')}
                from portal.accounts import validate_selected_identity
                validate_selected_identity(data)
                ensure_unowned(u['id'], account_email(data))
                store.run('UPDATE accounts SET payload=? WHERE user_id=? AND id=?', (store.encrypt(data),u['id'],aid))
                values.update(status='unverified',checked=None,note='邮箱已更新，请重新检查')
            store.update_account(u['id'], aid, values)
            store.audit(u['id'], 'account_updated')
        engine.request_sync()
        return {'ok':True}

    @app.post('/api/accounts/{aid}/check')
    async def account_check(aid: str, request: Request):
        u = auth(request, csrf=True)
        rate('check:'+u['id'], 3)
        async with account_operation(u['id']):
            if not store.account(u['id'], aid):
                raise HTTPException(404, '账号不存在')
            try:
                await asyncio.wait_for(engine.sync(force=True), 200)
            except Exception:
                pass
            live = engine.account_view(u['id']).get(aid, {'state':'pending','text':'正在准备，稍等片刻'})
            ok = live['state'] in ('ready', 'busy', 'cooldown')
            status = 'ready' if ok else 'pending' if live['state'] == 'pending' else 'error'
            store.update_account(u['id'], aid, {'status':status,'checked':time.time(),'note':live['text']})
            store.audit(u['id'], 'account_checked')
            return {'status':status, 'message':live['text'], 'state':live['state']}

    @app.delete('/api/accounts/{aid}')
    async def account_delete(aid: str, request: Request):
        u = auth(request, csrf=True)
        async with account_operation(u['id']):
            if not store.account(u['id'], aid):
                raise HTTPException(404, '账号不存在')
            store.delete_account(u['id'], aid)
        engine.request_sync()
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
                'requests_per_minute':s['requests_per_minute'] if s.get('rate_limit_enabled') else None,
                'concurrency':s['user_concurrency'], 'summary':store.stats(u['id'])['summary']} | reference_quota(store,u['id'])

    @app.get('/api/records')
    def records(request: Request, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=1000000)):
        return store.records(auth(request)['id'], limit, offset)

    @app.get('/api/load')
    async def project_load(request: Request):
        """Short, jargon-free service status for the user home page."""
        u = auth(request)
        snap = scheduler.snapshot(u['id'])
        busy = snap['queued'] > 0
        return JSONResponse({'ok': engine.health.get('ok') is not False,
                             'message': '服务正常' if engine.health.get('ok') is not False else '服务暂时不可用，管理员已收到提醒',
                             'busy': busy, 'mine_running': snap['user_active'], 'mine_waiting': snap['user_queued'],
                             'refresh_seconds': 5}, headers={'Cache-Control':'no-store'})

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

    def account_for(uid, engine_id):
        if not engine_id:
            return None
        row = store.one('SELECT account_id FROM engine_accounts WHERE engine_id=? AND user_id=?', (engine_id.lower(), uid))
        return row['account_id'] if row else None

    def remember_responses(uid, ids):
        now = time.time()
        for rid in ids[:20]:
            store.run('INSERT OR IGNORE INTO response_owners(response_id,user_id,created) VALUES (?,?,?)', (rid, uid, now))

    async def proxy(path, request):
        raw = request.headers.get('authorization','')
        key = raw[7:] if raw.lower().startswith('bearer ') else request.headers.get('x-api-key') or request.headers.get('x-goog-api-key') or ''
        u = store.key_user(key)
        if not u:
            raise HTTPException(401, 'API Key 无效或已撤销')
        verify_access(u, api=True)
        uid = u['id']
        s = store.settings()
        if s.get('rate_limit_enabled'):
            rate('rpm:'+uid, s['requests_per_minute'])
        if gateway.blocked(path):
            raise HTTPException(400, gateway.BLOCKED_MESSAGE)
        if not any(a['enabled'] for a in store.accounts(uid)):
            raise HTTPException(503, '你还没有导入自己的 Google 账号（本站不会使用其他用户的账号）')
        kind = gateway.protocol(path, request.headers)
        enabled = store.preferences(uid)['anti_truncation']
        if request.method == 'GET' and path == '/v1/models' and kind == 'openai':
            # Site-wide public catalogue from SQLite, filtered by this user's own
            # accounts; never starts or waits for anything per user.
            try:
                payload = await engine.catalogue()
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(503, '模型列表暂时读取不到，请稍后重试') from None
            payload = engine.user_catalogue(uid, payload)
            if enabled:
                payload = augment_models(payload)
            return JSONResponse(payload, headers={'Cache-Control':'private, no-cache'})
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
        # Historical -pg/-build suffixes are still accepted; the engine routes channels itself.
        base_model, _ = split_model(model.removeprefix(PREFIX) if anti else model)
        if anti and (not enabled or request.method != 'POST' or path != '/v1/chat/completions' or not eligible(base_model)):
            raise HTTPException(400, '请先开启防截断，仅支持带前缀的 OpenAI 聊天模型')
        if path_model and base_model != path_model:
            path = path.replace('/models/'+path_model, '/models/'+base_model, 1)
        if not anti and PREFIX in path:
            raise HTTPException(400, '防截断模型仅支持 OpenAI /v1/chat/completions')
        if message := gateway.body_blocked(parsed):
            raise HTTPException(400, message)
        if path == '/v1/responses' and isinstance(parsed, dict) and parsed.get('previous_response_id'):
            previous = str(parsed['previous_response_id'])
            if not store.one('SELECT 1 FROM response_owners WHERE response_id=? AND user_id=?', (previous, uid)):
                raise HTTPException(404, '找不到上一轮对话（previous_response_id），请重新开始对话')
        generation = gateway.is_generation(request.method, path)
        wants_stream = request.method == 'POST' and ((isinstance(parsed, dict) and parsed.get('stream') is True) or ':streamGenerateContent' in path)
        # Native Gemini streaming without alt=sse is a JSON array: pass bytes straight through.
        sse = wants_stream and (':streamGenerateContent' not in path or request.query_params.get('alt') == 'sse')
        start = time.monotonic()
        meter = UsageMeter(started=start)
        scanner = gateway.ResponseIdScanner() if path == '/v1/responses' else None
        state = {'status': 499, 'account': None, 'ticket': None, 'response': None, 'client': None,
                 'synthetic': '', 'buffered': None, 'error': None, 'recorded': False, 'cleaned': False}

        def record():
            if state['recorded']:
                return
            state['recorded'] = True
            status = state['status']
            store.record(uid, u['key_id'], model, status, time.monotonic()-start, meter.finish(), account_id=state['account'],
                         generation=generation, first_token_seconds=meter.first_token_seconds if generation and status < 400 else None)
            if scanner is not None and status < 400 and scanner.ids:
                remember_responses(uid, scanner.ids)
            if status >= 500 and status != 503:
                alerts('upstream_5xx', model or path, f'最近有请求返回 {status}（{model or path}）', 'info')

        async def cleanup():
            if state['cleaned']:
                return
            state['cleaned'] = True
            import anyio
            with anyio.CancelScope(shield=True):
                for name in ('response', 'client'):
                    resource = state[name]
                    if resource is not None:
                        try:
                            with anyio.move_on_after(5):
                                await resource.aclose()
                        except Exception:
                            pass
                if state['ticket'] is not None:
                    scheduler.release(state['ticket'])
                    state['ticket'] = None
                record()

        async def prepare():
            nonlocal body, parsed
            state['ticket'] = await scheduler.acquire(uid)
            current = store.key_user(key)
            if not current or current['id'] != uid:
                raise HTTPException(401, 'API Key 无效或已撤销')
            verify_access(current, api=True)
            scope = await engine.ensure_ready(uid)
            if not scope:
                raise HTTPException(503, '你的账号还在准备中，或者都暂时不可用；请到「我的账号」查看')
            info = await engine.endpoint()
            if isinstance(parsed, dict) and base_model != model:
                parsed = dict(parsed, model=base_model)
                body = json.dumps(parsed, ensure_ascii=True).encode()
            if anti:
                parsed, state['synthetic'] = await inject(dict(parsed, model=base_model))
                body = json.dumps(parsed, ensure_ascii=True).encode()
            if request.method == 'POST' and path == '/v1/chat/completions' and isinstance(parsed,dict) and parsed.get('stream') is True:
                options = parsed.get('stream_options')
                parsed = dict(parsed, stream_options=(options if isinstance(options,dict) else {}) | {'include_usage':True})
                body = json.dumps(parsed, ensure_ascii=True).encode()
            # User cookies, user keys, forwarding headers and target URLs never pass through.
            headers = {'Authorization':'Bearer '+info['key'], SCOPE_HEADER: ','.join(scope)}
            for h in ['content-type','accept','anthropic-version','anthropic-beta']:
                if h in request.headers:
                    headers[h] = request.headers[h]
            params = [(k,v) for k,v in request.query_params.multi_items() if k not in ['key','api_key']]
            client = engine.client(info, 1800 if anti else 900)
            state['client'] = client
            response = await client.send(client.build_request(request.method, path, params=params, headers=headers, content=body), stream=True)
            state['response'] = response
            state['account'] = account_for(uid, response.headers.get(SERVING_HEADER, ''))
            if not response.is_success:
                raw_error = b''
                async for part in response.aiter_bytes():
                    raw_error += part
                    if len(raw_error) > 65536:
                        break
                status = response.status_code
                if status in (401, 403) and (b'account_scope_required' in raw_error or b'invalid_api_key' in raw_error or b'Unauthorized' in raw_error):
                    alerts('engine_auth', 'engine', '门户与生成服务之间的密钥不匹配，请重启生成服务', 'critical')
                    raise HTTPException(502, '生成服务配置异常，管理员已收到提醒')
                headers_out = {}
                if status == 429:
                    seconds = gateway.retry_seconds(response.headers, raw_error)
                    message = gateway.recovery_message(seconds)
                    if seconds:
                        headers_out['Retry-After'] = str(seconds)
                else:
                    message = gateway.upstream_message(status, raw_error)
                state['error'] = (status, message, headers_out)
                return
            catalogue = request.method == 'GET' and path == '/v1/models'
            if catalogue or (anti and 'text/event-stream' not in response.headers.get('content-type','')):
                raw_response = bytearray()
                async for part in response.aiter_bytes():
                    raw_response.extend(part)
                payload = json.loads(raw_response)
                if catalogue:
                    result = augment_models(payload) if enabled else payload
                else:
                    result = await normalize(payload, state['synthetic'], model)
                state['buffered'] = json.dumps(result, ensure_ascii=False).encode()

        def failure_status(exc):
            if isinstance(exc, HTTPException):
                return exc.status_code
            if isinstance(exc, asyncio.CancelledError):
                return 499
            if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
                return 504
            return 503

        def failure_message(exc):
            if isinstance(exc, HTTPException):
                return str(exc.detail)
            if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
                return '生成服务响应超时，请稍后重试'
            return '生成服务暂时连不上，请稍后重试'

        async def output():
            response = state['response']
            if state['buffered'] is not None:
                yield state['buffered']
            elif anti and response.is_success and 'text/event-stream' in response.headers.get('content-type',''):
                from contextlib import aclosing
                async with aclosing(transform_stream(response.aiter_bytes(), state['synthetic'], model)) as transformed:
                    async for part in transformed:
                        yield part
            else:
                async for part in response.aiter_bytes():
                    yield part

        async def body_stream():
            state['status'] = state['response'].status_code
            try:
                from contextlib import aclosing
                async with aclosing(output()) as source:
                    async for chunk in source:
                        meter.feed(chunk)
                        if scanner is not None:
                            scanner.feed(chunk)
                        yield chunk
            except BaseException:
                state['status'] = 499
                raise

        preparation = asyncio.create_task(prepare())
        disconnect = request.scope.get('portal.disconnect')
        watcher = asyncio.create_task(disconnect.wait()) if disconnect is not None else None

        async def settle(timeout=None):
            """Wait for preparation (or client disconnect). Returns True when prepared."""
            waiters = {preparation} | ({watcher} if watcher else set())
            done, _ = await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if preparation in done:
                preparation.result()
                return True
            if watcher is not None and watcher in done:
                preparation.cancel()
                await asyncio.gather(preparation, return_exceptions=True)
                raise HTTPException(499, '请求已断开')
            return False

        def error_response(status, message, headers=None):
            return JSONResponse(gateway.error_body(kind, status, message), status_code=status, headers=headers or {})

        async def stop_watcher():
            if watcher is not None and not watcher.done():
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)

        if sse:
            # Give a fast engine answer (e.g. 429 with recovery time) a real HTTP status;
            # otherwise start the event stream and keep it alive while preparing.
            try:
                ready = await settle(timeout=2.0)
            except BaseException as exc:
                state['status'] = failure_status(exc)
                await stop_watcher()
                await cleanup()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                return error_response(state['status'], failure_message(exc))
            if ready and state['error']:
                state['status'], message, extra = state['error']
                await stop_watcher()
                await cleanup()
                return error_response(state['status'], message, extra)
            from portal.proxy_response import ManagedStreamingResponse
            from portal.streaming import with_heartbeats

            async def events():
                try:
                    if not ready:
                        while not await settle(timeout=None):
                            pass
                    if state['error']:
                        state['status'], message, _ = state['error']
                        yield ('data: '+json.dumps(gateway.error_body(kind, state['status'], message), ensure_ascii=False)+'\n\n').encode()
                        return
                    from contextlib import aclosing
                    async with aclosing(body_stream()) as source:
                        async for chunk in source:
                            yield chunk
                except HTTPException as exc:
                    state['status'] = exc.status_code
                    if exc.status_code != 499:
                        yield ('data: '+json.dumps(gateway.error_body(kind, exc.status_code, str(exc.detail)), ensure_ascii=False)+'\n\n').encode()
                except (TimeoutError, httpx.HTTPError) as exc:
                    state['status'] = failure_status(exc)
                    yield ('data: '+json.dumps(gateway.error_body(kind, state['status'], failure_message(exc)), ensure_ascii=False)+'\n\n').encode()

            async def finish():
                if not preparation.done():
                    preparation.cancel()
                await asyncio.gather(preparation, return_exceptions=True)
                await stop_watcher()
                await cleanup()
            return ManagedStreamingResponse(with_heartbeats(events(), interval=30 if anti else 15), cleanup=finish,
                                            media_type='text/event-stream', headers={'X-Accel-Buffering':'no', 'Cache-Control':'no-cache'})
        try:
            await settle()
        except BaseException as exc:
            state['status'] = failure_status(exc)
            await stop_watcher()
            await cleanup()
            if isinstance(exc, asyncio.CancelledError):
                raise
            return error_response(state['status'], failure_message(exc))
        await stop_watcher()
        if state['error']:
            state['status'], message, extra = state['error']
            await cleanup()
            return error_response(state['status'], message, extra)
        from portal.proxy_response import ManagedStreamingResponse
        response = state['response']
        content_type = 'application/json' if state['buffered'] is not None else response.headers.get('content-type', 'application/json')
        return ManagedStreamingResponse(body_stream(), cleanup=cleanup, status_code=response.status_code,
                                        headers={'Content-Type': content_type, 'X-Accel-Buffering':'no', 'Cache-Control':'no-cache'})

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
        # Content-hashed URLs let browsers cache static assets for a year.
        text = re.sub(r'"/static/([^"?#]+)"', lambda m: '"/static/'+m.group(1)+('?v='+asset_versions[m.group(1)] if m.group(1) in asset_versions else '')+'"', text)
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
    app.add_middleware(BoundedBodyMiddleware, budget=budget)
    return app
