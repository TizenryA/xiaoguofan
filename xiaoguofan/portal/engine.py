"""Client for the single shared generation engine.

One long-lived engine (aistudio2api) serves every user. Isolation is enforced
by the engine itself: every proxied request carries the trusted header
``X-Xiaoguofan-Account-Scope`` listing only the requesting user's own accounts
(engine started with ``XGF_ACCOUNT_SCOPE=required`` rejects requests without
it). Accounts are never shared between users: a Google email already owned by
one user is never loaded for another.
"""
import asyncio
import contextlib
import copy
import json
import logging
import time

import httpx
from fastapi import HTTPException

from portal.accounts import account_email, worker_payload
from portal.store import digest

SCOPE_HEADER = 'X-Xiaoguofan-Account-Scope'
SERVING_HEADER = 'x-xiaoguofan-account'
PUBLIC_CATALOGUE = '__public__'
CATALOGUE_TTL = 1800
SYNC_INTERVAL = 300
STATUS_INTERVAL = 20
TERMS_REQUIRED = '请先在 Google AI Studio 官网自行阅读并同意条款，再重试；本站不会代为确认'

STATE_TEXT = {
    'ready': '可用', 'busy': '可用（正在处理请求）', 'cooldown': '额度暂时用完，会自动恢复',
    'auth_required': 'Google 登录已失效，请重新导入', 'restricted': 'Google 限制了此账号使用 AI Studio',
    'unavailable': '暂时连不上，稍后自动重试', 'disabled': '已停用', 'pending': '正在准备，稍等片刻',
    'error': '没能载入', 'conflict': '这个 Google 账号已被其他用户导入，不能重复使用',
}
PAID_TIERS = {'pro', 'ultra', 'plus'}
log = logging.getLogger(__name__)


def human_state(state, message=''):
    text = STATE_TEXT.get(state, '状态未知')
    if state in ('error', 'conflict') and message:
        return message
    return text


def static_locator(info):
    async def locate():
        return dict(info)
    return locate


def broker_locator(socket):
    async def locate():
        async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=socket), base_url='http://broker', timeout=330) as c:
            r = await c.post('/engine', json={})
            if r.status_code >= 400:
                raise HTTPException(503, '生成服务正在启动，请稍后重试')
            return r.json()
    return locate


def engine_payload(row):
    """The account payload sent to the engine (label = Google email)."""
    data = worker_payload(row)
    data.pop('renewal_token', None)
    return data


def payload_fingerprint(payload):
    return digest(json.dumps(payload, sort_keys=True, ensure_ascii=True))


class EngineClient:
    def __init__(self, locate, transport=None, background=True):
        self.locate = locate
        self.transport = transport
        self.background = background
        self.store = None
        self.info = None
        self.sync_lock = asyncio.Lock()
        self.dirty = asyncio.Event()
        self.tasks = []
        self.last_sync = 0.0
        self.last_status = 0.0
        self.health = {'ok': None, 'checked': None, 'message': ''}
        self.catalogue_task = None
        self.alert = None

    # ---- wiring -------------------------------------------------------
    def attach(self, store, alert=None):
        self.store = store
        self.alert = alert or (lambda *a, **k: None)
        return self

    async def endpoint(self, refresh=False):
        if self.info is None or refresh:
            self.info = await self.locate()
        return self.info

    def client(self, info, timeout=30.0):
        return httpx.AsyncClient(base_url=info['url'], transport=self.transport,
                                 timeout=httpx.Timeout(timeout, connect=10), follow_redirects=False)

    @contextlib.asynccontextmanager
    async def admin(self, timeout=120.0):
        info = await self.endpoint()
        async with self.client(info, timeout) as c:
            r = await c.post('/api/auth/login', json={'username': 'admin', 'password': info['admin_password']})
            if r.status_code >= 400:
                # Engine was recreated with new credentials: ask the broker again once.
                info = await self.endpoint(refresh=True)
                c.base_url = info['url']
                r = await c.post('/api/auth/login', json={'username': 'admin', 'password': info['admin_password']})
                r.raise_for_status()
            yield c

    async def start(self):
        if not self.background:
            return
        self.tasks = [asyncio.create_task(self._sync_loop()), asyncio.create_task(self._status_loop())]

    async def close(self):
        for task in self.tasks:
            task.cancel()
        if self.catalogue_task:
            self.catalogue_task.cancel()
        await asyncio.gather(*self.tasks, *( [self.catalogue_task] if self.catalogue_task else []), return_exceptions=True)

    def request_sync(self):
        self.dirty.set()

    async def _sync_loop(self):
        while True:
            try:
                await self.sync()
            except Exception:
                log.warning('engine account sync failed', exc_info=False)
            try:
                await asyncio.wait_for(self.dirty.wait(), SYNC_INTERVAL)
            except TimeoutError:
                pass
            await asyncio.sleep(0.2)  # coalesce bursts of account edits

    async def _status_loop(self):
        while True:
            try:
                await self.refresh_status()
            except Exception:
                pass
            await asyncio.sleep(STATUS_INTERVAL)

    # ---- ownership ----------------------------------------------------
    def plan(self):
        """Decide which portal account owns each Google email (earliest import wins)."""
        rows = self.store.all('SELECT a.id,a.user_id,a.enabled,a.payload,a.created,a.label,a.plan,u.disabled user_disabled '
                              'FROM accounts a JOIN users u ON u.id=a.user_id ORDER BY a.created,a.id')
        owners, conflicts, missing = {}, [], []
        for row in rows:
            payload = self.store.decrypt(row.pop('payload'))
            email = account_email(payload)
            if not email:
                missing.append(row)
                continue
            owner = owners.get(email)
            if owner is not None and owner['user_id'] != row['user_id']:
                conflicts.append(row | {'email': email, 'owner': owner['user_id']})
                continue
            if owner is None:
                owners[email] = row | {'email': email, 'payload': payload}
        desired = {email: row for email, row in owners.items() if row['enabled'] and not row['user_disabled']}
        return desired, conflicts, missing

    def conflicts(self, uid=None):
        _, conflicts, _ = self.plan()
        return [c for c in conflicts if uid is None or c['user_id'] == uid]

    # ---- sync ---------------------------------------------------------
    async def sync(self, force=False):
        async with self.sync_lock:
            self.dirty.clear()
            desired, conflicts, _ = self.plan()
            async with self.admin() as c:
                r = await c.get('/api/accounts')
                r.raise_for_status()
                remote = {a['id'].lower(): a for a in r.json().get('accounts', [])}
                known = {row['engine_id']: row for row in self.store.all('SELECT * FROM engine_accounts')}
                now = time.time()
                for email, row in desired.items():
                    try:
                        payload = engine_payload(row)
                        fingerprint = payload_fingerprint(payload)
                    except HTTPException as exc:
                        self._save(email, row, '', 'error', str(exc.detail), now)
                        continue
                    old = known.get(email)
                    unchanged = old and old['fingerprint'] == fingerprint and old['account_id'] == row['id'] and old['user_id'] == row['user_id']
                    if unchanged and email in remote:
                        continue
                    if unchanged and old['state'] == 'error' and not force:
                        continue  # same broken credentials; wait for the owner to re-import
                    if email in remote and old and (old['user_id'] != row['user_id'] or old['account_id'] != row['id']):
                        # Ownership moved (account deleted and re-imported elsewhere): drop the
                        # engine copy first so no runtime state follows it to the new owner.
                        await c.delete('/api/accounts/'+remote[email]['id'])
                        remote.pop(email, None)
                    state, message = await self._import(c, payload)
                    self._save(email, row, fingerprint, state, message, now)
                for engine_id, account in remote.items():
                    if engine_id not in desired:
                        r = await c.delete('/api/accounts/'+account['id'])
                        if r.status_code < 400 or r.status_code == 404:
                            self.store.run('DELETE FROM engine_accounts WHERE engine_id=?', (engine_id,))
                for engine_id in known:
                    if engine_id not in desired and engine_id not in remote:
                        self.store.run('DELETE FROM engine_accounts WHERE engine_id=?', (engine_id,))
                if desired:
                    r = await c.post('/api/control/start')
                    if r.status_code >= 400 and r.status_code != 409:
                        log.warning('engine start returned %s', r.status_code)
            for item in conflicts:
                self.alert('duplicate_account', item['id'], '同一个 Google 账号被多个用户导入，后导入的那份没有载入（不会混用）', 'info')
            self.last_sync = time.time()
            self.health.update(ok=True, checked=self.last_sync, message='')
            self.alert_resolve('engine_down', 'engine')
            await self.refresh_status()

    def alert_resolve(self, kind, subject):
        resolve = getattr(self.alert, 'resolve', None)
        if resolve:
            resolve(kind, subject)

    async def _import(self, c, payload):
        r = await c.post('/api/pairing')
        r.raise_for_status()
        token = r.json()['token']
        r = await c.post('/api/pairing/accounts', headers={'Authorization': 'Bearer '+token}, json=payload, timeout=180)
        if r.status_code < 400:
            return 'pending', ''
        message = ''
        try:
            message = r.json().get('error', {}).get('message', '')
        except (ValueError, AttributeError):
            pass
        if TERMS_REQUIRED in message:
            return 'error', TERMS_REQUIRED
        if '登录邮箱与账户不一致' in message:
            return 'error', '登录会话与所填 Google 邮箱不一致，请重新导出'
        if r.status_code in (400, 401, 403, 422):
            return 'error', 'Google 登录状态无效或已过期，请重新登录后导入'
        return 'error', '暂时没能载入，系统会自动重试'

    def _save(self, email, row, fingerprint, state, message, now):
        self.store.run('INSERT INTO engine_accounts(engine_id,account_id,user_id,fingerprint,state,message,synced) VALUES (?,?,?,?,?,?,?) '
                       'ON CONFLICT(engine_id) DO UPDATE SET account_id=excluded.account_id,user_id=excluded.user_id,'
                       'fingerprint=excluded.fingerprint,state=excluded.state,message=excluded.message,synced=excluded.synced',
                       (email, row['id'], row['user_id'], fingerprint, state, message, now))
        if state == 'error':
            self.alert('account_load_failed', row['id'], '账号载入失败：'+message, 'warning')
        else:
            self.alert_resolve('account_load_failed', row['id'])

    async def refresh_status(self):
        try:
            async with self.admin(timeout=15) as c:
                r = await c.get('/api/accounts')
                r.raise_for_status()
                accounts = r.json().get('accounts', [])
        except Exception:
            self.health.update(ok=False, checked=time.time(), message='生成服务无响应')
            self.alert('engine_down', 'engine', '生成服务无响应，用户请求会失败', 'critical')
            return
        now = time.time()
        self.health.update(ok=True, checked=now, message='')
        self.alert_resolve('engine_down', 'engine')
        for a in accounts:
            engine_id = a['id'].lower()
            state = a.get('state') or 'pending'
            if not a.get('enabled', True):
                state = 'disabled'
            self.store.run("UPDATE engine_accounts SET state=CASE WHEN state='error' THEN state ELSE ? END,tier=?,models=?,active=?,observed=?,"
                           "message=CASE WHEN state='error' THEN message ELSE ? END WHERE engine_id=?",
                           (state, str(a.get('benefit_tier') or ''), json.dumps(a.get('models') or []), int(a.get('active_requests') or 0), now,
                            str(a.get('message') or '')[:300], engine_id))
            row = self.store.one('SELECT account_id FROM engine_accounts WHERE engine_id=?', (engine_id,))
            if row:
                if state in ('auth_required', 'restricted'):
                    self.alert('account_'+state, row['account_id'], '账号需要处理：'+STATE_TEXT[state], 'warning')
                else:
                    self.alert_resolve('account_auth_required', row['account_id'])
                    self.alert_resolve('account_restricted', row['account_id'])
        self.last_status = now

    # ---- per-user view ------------------------------------------------
    def scope(self, uid):
        rows = self.store.all("SELECT e.engine_id FROM engine_accounts e JOIN accounts a ON a.id=e.account_id "
                              "WHERE e.user_id=? AND a.user_id=? AND a.enabled=1 AND e.state!='error' ORDER BY a.created", (uid, uid))
        return [r['engine_id'] for r in rows]

    def account_view(self, uid):
        """Human status per portal account of uid (no engine/pool terminology)."""
        rows = {r['account_id']: r for r in self.store.all('SELECT * FROM engine_accounts WHERE user_id=?', (uid,))}
        conflicts = {c['id'] for c in self.conflicts(uid)}
        out = {}
        for a in self.store.all('SELECT id,enabled FROM accounts WHERE user_id=?', (uid,)):
            if a['id'] in conflicts:
                state, message = 'conflict', ''
            elif not a['enabled']:
                state, message = 'disabled', ''
            elif a['id'] in rows:
                state, message = rows[a['id']]['state'], rows[a['id']]['message']
            else:
                state, message = 'pending', ''
            out[a['id']] = {'state': state, 'text': human_state(state, message),
                            'tier': (rows.get(a['id']) or {}).get('tier', '')}
        return out

    async def ensure_ready(self, uid, timeout=60):
        """Make sure the user's enabled accounts are loaded before a request."""
        scope = self.scope(uid)
        pending = self.store.one("SELECT COUNT(*) n FROM accounts a WHERE a.user_id=? AND a.enabled=1 AND NOT EXISTS "
                                 "(SELECT 1 FROM engine_accounts e WHERE e.account_id=a.id AND e.user_id=a.user_id)", (uid,))['n']
        if pending:
            try:
                await asyncio.wait_for(self.sync(), timeout)
            except Exception:
                log.warning('on-demand engine sync failed')
            scope = self.scope(uid)
        return scope

    # ---- catalogue ----------------------------------------------------
    def cached_catalogue(self):
        row = self.store.one('SELECT payload,updated FROM model_catalogues WHERE owner=?', (PUBLIC_CATALOGUE,))
        return (json.loads(row['payload']), row['updated']) if row else (None, 0)

    async def fetch_catalogue(self):
        scope = [r['engine_id'] for r in self.store.all("SELECT engine_id FROM engine_accounts WHERE state!='error'")]
        if not scope:
            try:
                await asyncio.wait_for(self.sync(), 60)
            except Exception:
                log.warning('engine sync before catalogue failed')
            scope = [r['engine_id'] for r in self.store.all("SELECT engine_id FROM engine_accounts WHERE state!='error'")]
        if not scope:
            raise HTTPException(503, '还没有可用账号，模型列表暂时为空')
        info = await self.endpoint()
        async with self.client(info, 120) as c:
            r = await c.get('/v1/models', headers={'Authorization': 'Bearer '+info['key'], SCOPE_HEADER: ','.join(scope)})
            r.raise_for_status()
            payload = r.json()
        if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
            raise ValueError('invalid catalogue')
        self.store.run('INSERT INTO model_catalogues VALUES (?,?,?,?) ON CONFLICT(owner) DO UPDATE SET fingerprint=excluded.fingerprint,payload=excluded.payload,updated=excluded.updated',
                       (PUBLIC_CATALOGUE, 'public', json.dumps(payload), time.time()))
        return payload

    async def catalogue(self):
        """Site-wide catalogue from SQLite; refreshed in the background, never starts anything per user."""
        payload, updated = self.cached_catalogue()
        if payload is not None:
            if time.time()-updated > CATALOGUE_TTL and (self.catalogue_task is None or self.catalogue_task.done()):
                self.catalogue_task = asyncio.create_task(self._refresh_catalogue())
            return payload
        return await self.fetch_catalogue()

    async def _refresh_catalogue(self):
        try:
            await self.fetch_catalogue()
        except Exception:
            log.warning('public catalogue refresh failed')

    def user_catalogue(self, uid, payload):
        """Filter the public catalogue by what the user's own accounts can use."""
        rows = self.store.all("SELECT e.tier,e.models FROM engine_accounts e JOIN accounts a ON a.id=e.account_id "
                              "WHERE e.user_id=? AND a.user_id=? AND a.enabled=1 AND e.state!='error'", (uid, uid))
        manual_pro = bool(self.store.one("SELECT 1 FROM accounts WHERE user_id=? AND enabled=1 AND plan='pro'", (uid,)))
        allowed = set()
        known = False
        paid = manual_pro
        for row in rows:
            try:
                models = json.loads(row['models'] or '[]')
            except ValueError:
                models = []
            if models:
                known = True
                allowed.update(m.removeprefix('models/') for m in models if isinstance(m, str))
            if str(row['tier']).lower() in PAID_TIERS:
                paid = True
        out = copy.deepcopy(payload)
        data = []
        for item in out.get('data', []):
            if not isinstance(item, dict) or not isinstance(item.get('id'), str):
                continue
            model_id = item['id'].removeprefix('models/')
            if known:
                if model_id not in allowed:
                    continue
            elif item.get('paid') and not paid:
                continue
            item.pop('channels', None)
            item.pop('access_modes', None)
            data.append(item)
        out['data'] = data
        return out
