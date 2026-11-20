import asyncio
import json
import logging
import os
import re
import secrets
import subprocess
import time
from collections import defaultdict
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request

from portal.accounts import worker_payload
from portal.resources import BASE_RESERVE, HEADROOM, HIGH, LIMIT, WORKER_BYTES, ProjectResources

STARTUP_TIMEOUT = 300
IDLE_TIMEOUT = 30 * 60
TERMS_REQUIRED = '请先在 Google AI Studio 官网自行阅读并同意条款，再重试；本站不会代为确认'

IMAGE = os.environ.get('PORTAL_WORKER_IMAGE', 'xiaoguofan-worker:0.8.0')


class BrokerClient:
    def __init__(self, socket='/run/aistudio-portal/broker.sock'):
        self.socket = socket
        self.release_tasks = {}

    async def call(self, path, payload):
        async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=self.socket), base_url='http://broker', timeout=STARTUP_TIMEOUT + 30) as c:
            r = await c.post(path, json=payload)
            if r.status_code >= 400:
                detail = '独立账号池未就绪，请到账号管理点击检查重建；无需删除账号'
                if r.status_code in (409,429,503):
                    try:
                        candidate = r.json().get('detail')
                        if isinstance(candidate, str) and candidate:
                            detail = candidate
                    except (ValueError,AttributeError,TypeError):
                        pass
                if r.status_code == 422:
                    safe = {TERMS_REQUIRED, '登录会话与所选 Google 账号不匹配，请重新导出', '请在账号管理中补填该账号的 Google 邮箱后重试；无需重新上传凭据', 'Google 登录凭据已过期，请重新登录并导出；不会延长原凭据有效期', 'Cookie SameSite 格式无效，请重新导出认证文件', 'Cookie 有效期格式错误', 'Cookie 格式错误', '尚未完成 Google 登录，请在画面中完成验证', '无法核对 Google 身份，请完成登录后重试', '无法核对 Google 身份', '无法唯一核对登录账号；请只登录一个专用 Google 账号', 'Google 账号已变化，请重新核对', '输入格式无效'}
                    try:
                        candidate = r.json().get('detail')
                        detail = candidate if isinstance(candidate,str) and candidate in safe else '认证文件格式无效，请重新导出'
                    except (ValueError, AttributeError):
                        detail = '认证文件格式无效，请重新导出'
                raise HTTPException(r.status_code, detail)
            return r.json()

    async def ensure(self, uid, accounts, limit, channel='', lease=False):
        return await self.call('/ensure', {'uid': uid, 'accounts': accounts, 'limit': limit, 'channel': channel, 'lease': lease})

    async def acquire(self, uid, accounts, limit, channel=''):
        token = secrets.token_hex(16)
        try:
            return await self.ensure(uid, accounts, limit, channel=channel, lease=token)
        except BaseException:
            async def compensate():
                try:
                    async with asyncio.timeout(5):
                        await self.release(uid, token)
                except BaseException:
                    logging.getLogger(__name__).warning('cancelled acquisition lease release unconfirmed')
            await asyncio.shield(compensate())
            raise

    async def release(self, uid, lease):
        key = (uid, lease)
        if key not in self.release_tasks:
            async def retry_release():
                delay = 1
                while True:
                    try:
                        async with asyncio.timeout(5):
                            return await self.call('/release', {'uid': uid, 'lease': lease})
                    except Exception:
                        logging.getLogger(__name__).warning('broker lease release pending; retrying')
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, 30)

            task = asyncio.create_task(retry_release())
            self.release_tasks[key] = task
            task.add_done_callback(lambda done: self.release_tasks.pop(key, None))
        # The release owner survives caller cancellation/timeouts and retains a
        # strong reference until the broker confirms the idempotent release.
        return await asyncio.shield(self.release_tasks[key])

    async def touch(self, uid):
        return await self.call('/touch', {'uid': uid})

    async def load(self):
        return await self.call('/load', {})

    async def stop(self, uid):
        return await self.call('/stop', {'uid': uid})

    async def quarantine(self, uid):
        return await self.call('/quarantine', {'uid': uid})

    async def check(self, uid, account, limit):
        worker = None
        try:
            await self.stop(uid)
            worker = await self.acquire(uid, [account], limit)
            async with httpx.AsyncClient(timeout=180) as c:
                r = await c.get(worker['url']+'/v1/models', headers={'Authorization':'Bearer '+worker['key']})
                if r.status_code in (401,403):
                    raise HTTPException(502, 'Google／账号池认证失败，不是 Discord 登录失效；请检查 Google 会话或联系站长')
                r.raise_for_status()
                models = r.json().get('data', [])
                if not isinstance(models, list) or not models:
                    raise ValueError('empty catalog')
                return {'status':'catalog_ok','models':len(models),'message':'模型列表读取通过，不代表生成成功或剩余额度'}
        finally:
            if worker and worker.get('lease'):
                await asyncio.shield(self.release(uid,worker['lease']))
            # Check releases its request lease and starts the full idle period.


class WorkerManager:
    def __init__(self, root='/data/aistudio-portal'):
        self.root = Path(root)
        self.items = {}
        self.pending_cleanup = set()
        self.locks = defaultdict(asyncio.Lock)
        self.resources = ProjectResources()
        self.starting = 0
        self.evictions = 0
        self.released = {}
        self.enforced = os.environ.get("PORTAL_RESOURCE_ENFORCED") == "1"

    async def docker(self, *args, check=True):
        p = await asyncio.create_subprocess_exec('docker', *args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(p.communicate(), 15 if args[:2] == ('rm','-f') else 30)
        except BaseException:
            if p.returncode is None:
                try:p.kill()
                except ProcessLookupError:pass
                await p.wait()
            raise
        if check and p.returncode:
            if not (args[:2] == ('rm', '-f') and b'No such container' in err):
                raise RuntimeError('worker operation failed')
        return out.decode()

    async def stop(self, uid):
        async with self.locks[uid]:
            await self._stop(uid)

    async def _stop(self, uid):
        # Caller owns the uid lock. Failed removals remain retryable.
        if self.items.get(uid, {}).get('leases'):
            raise HTTPException(409, '账号池正在处理请求，请结束后重试')
        self.pending_cleanup.add(uid)
        await self.docker('rm', '-f', 'asp-'+uid)
        self.items.pop(uid, None)
        self.pending_cleanup.discard(uid)

    async def quarantine(self, uid):
        # Compatibility endpoint; active requests retain their worker.
        await self.stop(uid)
        return {'ok': True}

    def result(self, item, lease):
        value = {'url':item['url'], 'key':item['key']}
        if lease:
            if isinstance(lease,str) and lease in self.released:
                raise HTTPException(409, '请求已取消')
            if item.get('leases'):
                raise HTTPException(429, '自己的账号池正在处理请求，请稍后重试')
            token = lease if isinstance(lease,str) else secrets.token_hex(16)
            item.setdefault('leases',set()).add(token)
            value['lease'] = token
        item['used'] = time.time()
        return value

    async def touch(self, uid):
        item = self.items.get(uid)
        if item:
            item['used'] = time.time()
        return {'ok': True}

    async def release(self, uid, token):
        # Event-loop atomic: do not wait behind a slow Google startup lock.
        self.released = {k:v for k,v in self.released.items() if time.monotonic()-v < 600}
        if len(self.released) >= 10000:
            self.released.pop(next(iter(self.released)))
        self.released[token] = time.monotonic()
        item = self.items.get(uid)
        if item and token in item.get('leases', set()):
            item['leases'].discard(token)
            item['used'] = time.time()
        return {'ok':True}

    def capacity(self, usage):
        memory = usage['memory']
        used = memory['used_bytes'] or BASE_RESERVE
        resident = sum(self.resources.worker_memory(v.get('container_id', '')) for v in self.items.values())
        base = max(BASE_RESERVE, used - resident)
        budget = min(LIMIT, memory.get('limit_bytes') or LIMIT)
        capacity = max(0, (budget - base - HEADROOM) // WORKER_BYTES)
        if used >= min(HIGH, memory.get('high_bytes') or HIGH):
            capacity = min(capacity, len(self.items))
        return capacity

    async def admit(self, limit, uid):
        # No awaits: capacity check and reservation are event-loop atomic.
        # Idle workers keep their full renewal period under capacity pressure.
        usage = self.resources.snapshot()
        reserved = self.items.keys() | self.pending_cleanup
        if len(reserved) >= min(limit, self.capacity(usage)):
            raise HTTPException(503, '项目内存预算已满，请稍后重试')
        self.pending_cleanup.add(uid)

    async def load(self):
        # No lock: startup can await Google for minutes. Load page must stay responsive.
        usage = self.resources.snapshot()
        return usage | {'pool':{
            'running':len(self.items), 'active':sum(bool(v.get('leases')) for v in self.items.values()),
            'idle':sum(not v.get('leases') for v in self.items.values()),
            'starting':self.starting, 'cleanup_pending':len(self.pending_cleanup), 'evictions':self.evictions,
            'limit':self.capacity(usage), 'dynamic':True}, 'refresh_seconds':3}

    async def reap(self):
        for uid in list(self.pending_cleanup | self.items.keys()):
            lock = self.locks[uid]
            if lock.locked():
                continue
            async with lock:
                item = self.items.get(uid)
                if item and item.get('leases'):
                    continue
                if uid in self.pending_cleanup or (item and time.time()-item['used'] >= IDLE_TIMEOUT):
                    try:
                        await self._stop(uid)
                    except Exception:
                        logging.getLogger(__name__).warning('worker removal pending; retrying next sweep')

    async def ensure(self, uid, accounts, limit, channel='', lease=False):
        if channel not in ('', 'playground', 'build'):
            raise ValueError('invalid worker channel')
        payloads = [worker_payload(a) for a in accounts]
        async with self.locks[uid]:
            revision = [a['id'] for a in accounts]
            if uid in self.items:
                v = self.items[uid]
                # Unspecified channel (catalogue/check) never forces a rebuild.
                if (uid not in self.pending_cleanup and v['revision'] == revision
                        and (not channel or v.get('channel', '') in ('', channel))):
                    return self.result(v, lease)
                await self._stop(uid)
            elif uid in self.pending_cleanup:
                try:
                    await self._stop(uid)
                except Exception:
                    raise HTTPException(503, '账号池正在启动或维护，请稍后重试') from None
            channel = channel or 'playground'
            password, key = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            runtime = self.root/'runtime'
            runtime.mkdir(parents=True, exist_ok=True)
            name = 'asp-'+uid
            instance = os.environ.get('PORTAL_INSTANCE','')
            if instance and not re.fullmatch(r'[a-f0-9]{32}',instance):
                raise RuntimeError('invalid instance label')
            await self.admit(limit, uid)
            self.starting += 1
            stage = 'docker_run'
            try:
                async with asyncio.timeout(STARTUP_TIMEOUT):
                    await self.docker('rm', '-f', name)
                    container_id = (await self.docker('run', '-d', '--name', name, '--label', 'aistudio-portal.worker=1', '--init',
                        *(['--label','xiaoguofan.instance='+instance] if instance else []),
                        '--memory', str(WORKER_BYTES), '--memory-swap', str(WORKER_BYTES),
                        *(['--cgroup-parent', 'xiaoguofan.slice'] if self.enforced else []), '--cpus', '0.7', '--pids-limit', '512', '--cap-drop', 'ALL',
                        '--security-opt', 'no-new-privileges', '--read-only', '--tmpfs', '/tmp:rw,size=64m',
                        '--tmpfs', '/data:rw,size=160m',
                        '-p', '127.0.0.1::2048', '-e', 'WAA_BACKEND=spidermonkey', '-e', 'AUTO_START=false',
                        '-e', 'GOMEMLIMIT=560MiB', '-e', 'GOGC=50',
                        '-e', 'HOME=/data', '-e', 'AISTUDIO_AUTH_STATES=auth', '-e', 'XDG_CACHE_HOME=/data/.cache', '-e', 'LISTEN_ADDR=0.0.0.0:2048',
                        '-e', 'ADMIN_AUTH_ENABLED=true', '-e', 'ADMIN_USERNAME=admin', '-e', 'ADMIN_PASSWORD='+password,
                        '-e', 'PROXY_API_KEY='+key, '-e', 'LOG_FILE=false', '-e', 'REQUEST_BODY_LOG=false',
                        '-e', 'MAX_ACTIVE_WORKERS=1', '-e', 'WARM_WORKER_LIMIT=1', '-e', 'WARM_STARTUP_CONCURRENCY=1',
                        '-e', 'PER_ACCOUNT_CONCURRENCY=1', '-e', 'UPSTREAM_CHANNELS='+channel,
                        '-e', 'INIT_TIMEOUT=5m', '-e', 'REQUEST_TIMEOUT=30m', IMAGE)).strip()
                    port = (await self.docker('port', name, '2048/tcp')).strip().split(':')[-1]
                    if not port.isdigit():
                        raise ValueError('invalid worker port')
                    url = 'http://127.0.0.1:'+port
                    async with httpx.AsyncClient(base_url=url, timeout=STARTUP_TIMEOUT) as c:
                        while True:
                            try:
                                r = await c.get('/health', timeout=5)
                                if r.status_code == 200:
                                    break
                            except httpx.HTTPError:
                                pass
                            await asyncio.sleep(.5)
                        stage = 'auth_login'
                        r = await c.post('/api/auth/login', json={'username': 'admin', 'password': password})
                        r.raise_for_status()
                        for payload in payloads:
                            stage = 'pairing'
                            r = await c.post('/api/pairing')
                            r.raise_for_status()
                            token = r.json()['token']
                            stage = 'pairing_accounts'
                            r = await c.post('/api/pairing/accounts', headers={'Authorization':'Bearer '+token}, json=payload)
                            r.raise_for_status()
                        if accounts:
                            stage = 'control_start'
                            r = await c.post('/api/control/start')
                            r.raise_for_status()

                self.items[uid] = {'url': url, 'key': key, 'revision': revision, 'channel': channel, 'used': time.time(), 'leases':set(), 'container_id':container_id}
                self.pending_cleanup.discard(uid)
                return self.result(self.items[uid], lease)
            except BaseException as exc:
                response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
                # Read only finite, non-sensitive state before cleanup removes evidence.
                oom = False
                state = {}
                try:
                    raw = await self.docker('inspect', '--format', '{{json .State}}', name)
                    value = json.loads(raw)
                    state = {k: value.get(k) for k in ('Status', 'OOMKilled', 'ExitCode')}
                    oom = value.get('OOMKilled') is True
                except (Exception,):
                    pass
                logging.getLogger(__name__).error('worker startup failed: %s stage=%s http=%s state=%s', type(exc).__name__, stage, response.status_code if response is not None else '-', state)
                # Cleanup holds only this uid's lock and has a bounded wait.
                try:
                    await asyncio.wait_for(self._stop(uid),20)
                except BaseException:
                    self.pending_cleanup.add(uid)
                    logging.getLogger(__name__).error('worker cleanup pending; full reservation retained')
                if isinstance(exc, asyncio.CancelledError):
                    raise
                if oom:
                    raise HTTPException(503, '独立账号池超过700MiB内存限制，启动已终止；请联系站长') from None
                if response is not None and response.request.url.path == '/api/control/start':
                    try:
                        message = response.json().get('error',{}).get('message','')
                        identity_error = '登录会话与所选 Google 账号不匹配，请重新导出'
                        if isinstance(message,str):
                            if identity_error in message:
                                raise HTTPException(422, identity_error) from None
                            if TERMS_REQUIRED in message:
                                raise HTTPException(422, TERMS_REQUIRED) from None
                    except (ValueError,AttributeError):
                        pass
                if response is not None and response.status_code in (401,403):
                    raise HTTPException(503, '独立账号池认证失败，不是 Discord 登录失效；请检查 Google 会话或联系站长') from None
                if isinstance(exc,TimeoutError):
                    raise HTTPException(503, '账号池启动超时，已结束本轮启动；请稍后重试') from None
                raise HTTPException(503, '独立账号池启动失败，请稍后检查重建；无需删除或重导账号') from None
            finally:
                self.starting -= 1


def create_broker():
    from contextlib import asynccontextmanager, suppress

    manager = WorkerManager(os.environ.get('PORTAL_DATA', '/data/aistudio-portal'))

    @asynccontextmanager
    async def lifespan(app):
        socket_path = os.environ.get('PORTAL_BROKER', '/run/aistudio-portal/broker.sock')
        async def maintain():
            for _ in range(50):
                if os.path.exists(socket_path):
                    os.chmod(socket_path, 0o660)
                    break
                await asyncio.sleep(.1)
            while True:
                await asyncio.sleep(60)
                await manager.reap()
        instance = os.environ.get('PORTAL_INSTANCE','')
        if instance:
            if not re.fullmatch(r'[a-f0-9]{32}',instance):
                raise RuntimeError('invalid instance label')
            ids = (await manager.docker('ps','-aq','--filter','label=xiaoguofan.instance='+instance)).split()
            if ids:
                await manager.docker('rm','-f',*ids)
        task = asyncio.create_task(maintain())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            for uid in list(manager.items):
                await manager.stop(uid)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.post('/{action}')
    async def action(action: str, request: Request):
        p = await request.json()
        if action == 'load':
            return await manager.load()
        uid = p.get('uid', '')
        if not re.fullmatch(r'(?:[a-f0-9]{32}|admin)', uid):
            raise HTTPException(400)
        if action == 'touch':
            return await manager.touch(uid)
        if action == 'quarantine':
            return await manager.quarantine(uid)
        if action == 'release':
            return await manager.release(uid,str(p.get('lease','')))
        if action == 'stop':
            await manager.stop(uid)
            return {'ok': True}
        if action != 'ensure' or not isinstance(p.get('accounts'), list) or len(p['accounts']) > 20:
            raise HTTPException(400)
        channel = p.get('channel', '')
        if channel not in ('', 'playground', 'build'):
            raise HTTPException(400, 'invalid worker channel')
        return await manager.ensure(uid, p['accounts'], min(100, max(1, int(p.get('limit', 6)))), channel=channel, lease=p.get('lease') if isinstance(p.get('lease'),str) and re.fullmatch(r'[a-f0-9]{32}',p['lease']) else p.get('lease') is True)

    return app
