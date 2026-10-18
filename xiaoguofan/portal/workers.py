import asyncio
import os
import re
import secrets
import subprocess
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request

from portal.accounts import worker_payload

IMAGE = os.environ.get('PORTAL_WORKER_IMAGE', 'xiaoguofan-worker:0.8.0')


class BrokerClient:
    def __init__(self, socket='/run/aistudio-portal/broker.sock'):
        self.socket = socket

    async def call(self, path, payload):
        async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=self.socket), base_url='http://broker', timeout=240) as c:
            r = await c.post(path, json=payload)
            if r.status_code >= 400:
                detail = '独立账号池未就绪，请稍后重试或重新导入账号'
                if r.status_code == 422:
                    safe = {'请在账号管理中补填该账号的 Google 邮箱后重试；无需重新上传凭据', 'Google 登录凭据已过期，请重新登录并导出；不会延长原凭据有效期', 'Cookie SameSite 格式无效，请重新导出认证文件', 'Cookie 有效期格式错误', 'Cookie 格式错误', '尚未完成 Google 登录，请在画面中完成验证', '无法核对 Google 身份，请完成登录后重试', '无法核对 Google 身份', '无法唯一核对登录账号；请只登录一个专用 Google 账号', 'Google 账号已变化，请重新核对', '输入格式无效'}
                    try:
                        candidate = r.json().get('detail')
                        detail = candidate if isinstance(candidate,str) and candidate in safe else '认证文件格式无效，请重新导出'
                    except (ValueError, AttributeError):
                        detail = '认证文件格式无效，请重新导出'
                raise HTTPException(r.status_code, detail)
            return r.json()

    async def ensure(self, uid, accounts, limit, channel=''):
        return await self.call('/ensure', {'uid': uid, 'accounts': accounts, 'limit': limit, 'channel': channel})

    async def stop(self, uid):
        return await self.call('/stop', {'uid': uid})

    async def check(self, uid, account, limit):
        try:
            worker = await self.ensure(uid, [account], limit)
            async with httpx.AsyncClient(timeout=180) as c:
                r = await c.get(worker['url']+'/v1/models', headers={'Authorization':'Bearer '+worker['key']})
                r.raise_for_status()
                models = r.json().get('data', [])
                if not isinstance(models, list) or not models:
                    raise ValueError('empty catalog')
                return {'status':'catalog_ok','models':len(models),'message':'模型列表读取通过，不代表生成成功或剩余额度'}
        finally:
            await self.stop(uid)


class WorkerManager:
    def __init__(self, root='/data/aistudio-portal'):
        self.root = Path(root)
        self.items = {}
        self.lock = asyncio.Lock()

    async def docker(self, *args, check=True):
        p = await asyncio.create_subprocess_exec('docker', *args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = await asyncio.wait_for(p.communicate(), 240)
        if check and p.returncode:
            raise RuntimeError('worker operation failed')
        return out.decode()

    async def stop(self, uid):
        await self.docker('rm', '-f', 'asp-'+uid, check=False)
        self.items.pop(uid, None)

    async def reap(self):
        async with self.lock:
            for old, v in list(self.items.items()):
                if time.time()-v['used'] > 900:
                    await self.stop(old)

    async def ensure(self, uid, accounts, limit, channel=''):
        if channel not in ('', 'playground', 'build'):
            raise ValueError('invalid worker channel')
        payloads = [worker_payload(a) for a in accounts]
        async with self.lock:
            # A worker's lifetime is bounded; no fallback to another tenant is ever possible.
            for old, v in list(self.items.items()):
                if time.time()-v['used'] > 900:
                    await self.stop(old)
            revision = [a['id'] for a in accounts]
            if uid in self.items:
                v = self.items[uid]
                if v['revision'] == revision and v.get('channel', '') == channel:
                    v['used'] = time.time()
                    return {'url': v['url'], 'key': v['key']}
                await self.stop(uid)
            if len(self.items) >= limit:
                raise HTTPException(503, '服务器忙，请稍后重试')
            await self.stop(uid)
            password, key = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            runtime = self.root/'runtime'
            runtime.mkdir(parents=True, exist_ok=True)
            name = 'asp-'+uid
            try:
                await self.docker('run', '-d', '--name', name, '--label', 'aistudio-portal.worker=1', '--init',
                    '--memory', '650m', '--cpus', '0.7', '--pids-limit', '160', '--cap-drop', 'ALL',
                    '--security-opt', 'no-new-privileges', '--read-only', '--tmpfs', '/tmp:rw,size=64m',
                    '--tmpfs', '/data:rw,size=160m',
                    '-p', '127.0.0.1::2048', '-e', 'WAA_BACKEND=go', '-e', 'AUTO_START=false',
                    '-e', 'AISTUDIO_AUTH_STATES=auth', '-e', 'XDG_CACHE_HOME=/data/.cache', '-e', 'LISTEN_ADDR=0.0.0.0:2048',
                    '-e', 'ADMIN_AUTH_ENABLED=true', '-e', 'ADMIN_USERNAME=admin', '-e', 'ADMIN_PASSWORD='+password,
                    '-e', 'PROXY_API_KEY='+key, '-e', 'LOG_FILE=false', '-e', 'REQUEST_BODY_LOG=false',
                    '-e', 'MAX_ACTIVE_WORKERS=1', '-e', 'WARM_WORKER_LIMIT=1', '-e', 'WARM_STARTUP_CONCURRENCY=1',
                    '-e', 'PER_ACCOUNT_CONCURRENCY=1', '-e', 'UPSTREAM_CHANNELS='+(channel or 'playground,build'),
                    '-e', 'INIT_TIMEOUT=2m', '-e', 'REQUEST_TIMEOUT=5m', IMAGE)
                port = (await self.docker('port', name, '2048/tcp')).strip().split(':')[-1]
                if not port.isdigit():
                    raise ValueError('invalid worker port')
                url = 'http://127.0.0.1:'+port
                async with httpx.AsyncClient(base_url=url, timeout=180) as c:
                    for _ in range(80):
                        try:
                            r = await c.get('/health')
                            if r.status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(.5)
                    r = await c.post('/api/auth/login', json={'username': 'admin', 'password': password})
                    r.raise_for_status()
                    for payload in payloads:
                        r = await c.post('/api/pairing')
                        r.raise_for_status()
                        token = r.json()['token']
                        r = await c.post('/api/pairing/accounts', headers={'Authorization':'Bearer '+token}, json=payload)
                        r.raise_for_status()
                    if accounts:
                        r = await c.post('/api/control/start')
                        r.raise_for_status()
                self.items[uid] = {'url': url, 'key': key, 'revision': revision, 'channel': channel, 'used': time.time()}
                return {'url': url, 'key': key}
            except BaseException as exc:
                import logging
                response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
                logging.getLogger(__name__).error('worker startup failed: %s stage=%s http=%s', type(exc).__name__, response.request.url.path if response is not None else 'runtime', response.status_code if response is not None else '-')
                await self.stop(uid)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise HTTPException(503, '独立账号池启动失败') from None


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
        uid = p.get('uid', '')
        if not re.fullmatch(r'(?:[a-f0-9]{32}|admin)', uid):
            raise HTTPException(400)
        if action == 'stop':
            async with manager.lock:
                await manager.stop(uid)
            return {'ok': True}
        if action != 'ensure' or not isinstance(p.get('accounts'), list) or len(p['accounts']) > 20:
            raise HTTPException(400)
        channel = p.get('channel', '')
        if channel not in ('', 'playground', 'build'):
            raise HTTPException(400, 'invalid worker channel')
        return await manager.ensure(uid, p['accounts'], min(8, max(1, int(p.get('limit', 6)))), channel=channel)

    return app
