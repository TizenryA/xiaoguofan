"""Root broker: the only process with Docker access.

Since 1.0 it runs exactly one long-lived shared generation engine container
instead of one container per user. The portal (unprivileged) asks the broker
over a private unix socket for the engine's loopback URL and credentials.

Sizing comes from production data (8 vCPU, 15 GiB RAM, xiaoguofan.slice
MemoryMax 10 GiB / MemoryHigh 9 GiB, ~68 accounts / ~157 users) and can be
overridden with environment variables, see ``ENGINE_SIZING``.
"""
import asyncio
import json
import logging
import os
import re
import secrets
import subprocess
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException

from portal.resources import ProjectResources

ENGINE_NAME = 'xiaoguofan-engine'
LEGACY_WORKER_LABEL = 'aistudio-portal.worker=1'
STARTUP_TIMEOUT = 300
IMAGE = os.environ.get('PORTAL_ENGINE_IMAGE') or os.environ.get('PORTAL_WORKER_IMAGE', 'xiaoguofan-worker:1.0.0')

# name: (env var, default)
ENGINE_SIZING = {
    'memory': ('PORTAL_ENGINE_MEMORY', '8g'),              # container hard limit, inside the 10 GiB slice
    'gomemlimit': ('PORTAL_ENGINE_GOMEMLIMIT', '7GiB'),    # Go soft limit below the hard limit
    'cpus': ('PORTAL_ENGINE_CPUS', '6'),                   # leave 2 vCPU for portal/Caddy/system
    'warm': ('PORTAL_ENGINE_WARM_WORKERS', '16'),          # accounts kept with a ready VM
    'active': ('PORTAL_ENGINE_MAX_ACTIVE', '24'),          # peak concurrent account VMs
    'per_account': ('PORTAL_ENGINE_PER_ACCOUNT', '2'),     # concurrent requests per Google account
    'warm_startup': ('PORTAL_ENGINE_WARM_STARTUP', '4'),   # parallel VM warm-ups
    'port': ('PORTAL_ENGINE_PORT', '2049'),                # loopback only
}


def sizing():
    return {name: os.environ.get(env, default) for name, (env, default) in ENGINE_SIZING.items()}


class EngineManager:
    def __init__(self, root='/data/aistudio-portal'):
        self.root = Path(root)/'engine'
        self.lock = asyncio.Lock()
        self.enforced = os.environ.get('PORTAL_RESOURCE_ENFORCED') == '1'
        self.resources = ProjectResources()
        self.started = None

    def credentials(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root/'credentials.json'
        if path.exists():
            return json.loads(path.read_text())
        value = {'key': secrets.token_urlsafe(32), 'admin_password': secrets.token_urlsafe(32)}
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f)
        return value

    async def docker(self, *args, check=True, timeout=60):
        p = await asyncio.create_subprocess_exec('docker', *args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(p.communicate(), timeout)
        except BaseException:
            if p.returncode is None:
                try:
                    p.kill()
                except ProcessLookupError:
                    pass
                await p.wait()
            raise
        if check and p.returncode:
            if not (args[:2] == ('rm', '-f') and b'No such container' in err):
                raise RuntimeError('engine docker operation failed: '+args[0])
        return out.decode()

    def url(self):
        return 'http://127.0.0.1:'+sizing()['port']

    async def healthy(self):
        try:
            async with httpx.AsyncClient(base_url=self.url(), timeout=5) as c:
                return (await c.get('/health')).status_code == 200
        except httpx.HTTPError:
            return False

    async def remove_legacy_workers(self):
        ids = (await self.docker('ps', '-aq', '--filter', 'label='+LEGACY_WORKER_LABEL, check=False)).split()
        if ids:
            await self.docker('rm', '-f', *ids, check=False)
            logging.getLogger(__name__).info('removed %d legacy per-user worker containers', len(ids))

    async def run(self, creds):
        size = sizing()
        data = self.root/'data'
        data.mkdir(parents=True, exist_ok=True, mode=0o700)
        instance = os.environ.get('PORTAL_INSTANCE', '')
        if instance and not re.fullmatch(r'[a-f0-9]{32}', instance):
            raise RuntimeError('invalid instance label')
        await self.docker('rm', '-f', ENGINE_NAME)
        await self.docker(
            'run', '-d', '--name', ENGINE_NAME, '--init', '--restart', 'unless-stopped',
            '--label', 'xiaoguofan.engine=1', *(['--label', 'xiaoguofan.instance='+instance] if instance else []),
            '--memory', size['memory'], '--memory-swap', size['memory'],
            *(['--cgroup-parent', 'xiaoguofan.slice'] if self.enforced else []),
            '--cpus', size['cpus'], '--pids-limit', '4096', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--read-only', '--tmpfs', '/tmp:rw,size=512m',
            '-v', str(data)+':/data:rw',
            '-p', '127.0.0.1:'+size['port']+':2048',
            '-e', 'WAA_BACKEND=spidermonkey', '-e', 'AUTO_START=true',
            '-e', 'GOMEMLIMIT='+size['gomemlimit'], '-e', 'GOGC=75',
            '-e', 'HOME=/data', '-e', 'AISTUDIO_AUTH_STATES=auth', '-e', 'XDG_CACHE_HOME=/data/.cache',
            '-e', 'LISTEN_ADDR=0.0.0.0:2048',
            '-e', 'ADMIN_AUTH_ENABLED=true', '-e', 'ADMIN_USERNAME=admin', '-e', 'ADMIN_PASSWORD='+creds['admin_password'],
            '-e', 'PROXY_API_KEY='+creds['key'], '-e', 'LOG_FILE=false', '-e', 'REQUEST_BODY_LOG=false',
            '-e', 'XGF_ACCOUNT_SCOPE=required', '-e', 'AISTUDIO_COOLDOWN_QUEUE_SECONDS=0',
            '-e', 'MAX_ACTIVE_WORKERS='+size['active'], '-e', 'WARM_WORKER_LIMIT='+size['warm'],
            '-e', 'WARM_STARTUP_CONCURRENCY='+size['warm_startup'], '-e', 'PER_ACCOUNT_CONCURRENCY='+size['per_account'],
            '-e', 'UPSTREAM_CHANNELS=playground,build',
            '-e', 'INIT_TIMEOUT=5m', '-e', 'REQUEST_TIMEOUT=30m', IMAGE, timeout=120)
        deadline = time.monotonic()+STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if await self.healthy():
                self.started = time.time()
                return
            await asyncio.sleep(.5)
        raise HTTPException(503, '生成服务启动超时')

    async def ensure(self):
        async with self.lock:
            creds = self.credentials()
            if not await self.healthy():
                state = {}
                try:
                    state = json.loads(await self.docker('inspect', '--format', '{{json .State}}', ENGINE_NAME, check=False) or '{}')
                except ValueError:
                    pass
                if state.get('Running') and state.get('Health', {}).get('Status') != 'unhealthy':
                    # Running but still booting: give it a moment before recreating.
                    for _ in range(60):
                        if await self.healthy():
                            break
                        await asyncio.sleep(.5)
                if not await self.healthy():
                    await self.run(creds)
            return {'url': self.url(), 'key': creds['key'], 'admin_password': creds['admin_password']}

    async def restart(self):
        async with self.lock:
            await self.run(self.credentials())
        return {'ok': True}

    async def load(self):
        data = self.resources.snapshot()
        engine = {'running': await self.healthy(), 'memory_bytes': None, 'started': self.started, 'sizing': sizing()}
        try:
            cid = (await self.docker('inspect', '--format', '{{.Id}}', ENGINE_NAME, check=False)).strip()
            if cid:
                engine['memory_bytes'] = self.resources.worker_memory(cid) or None
        except Exception:
            pass
        data['engine'] = engine
        return data


def create_broker():
    from contextlib import asynccontextmanager, suppress

    manager = EngineManager(os.environ.get('PORTAL_DATA', '/data/aistudio-portal'))

    @asynccontextmanager
    async def lifespan(app):
        socket_path = os.environ.get('PORTAL_BROKER', '/run/aistudio-portal/broker.sock')

        async def maintain():
            for _ in range(50):
                if os.path.exists(socket_path):
                    os.chmod(socket_path, 0o660)
                    break
                await asyncio.sleep(.1)
            with suppress(Exception):
                await manager.remove_legacy_workers()
            while True:
                with suppress(Exception):
                    await manager.ensure()
                await asyncio.sleep(30)
        task = asyncio.create_task(maintain())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            # The engine keeps running across broker restarts (--restart unless-stopped).

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.post('/engine')
    async def engine():
        return await manager.ensure()

    @app.post('/engine/restart')
    async def engine_restart():
        return await manager.restart()

    @app.post('/load')
    async def load():
        return await manager.load()

    @app.post('/{action}')
    async def unknown(action: str):
        raise HTTPException(404)

    return app
