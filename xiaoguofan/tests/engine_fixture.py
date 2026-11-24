"""A recorded-behaviour fake of the shared generation engine (no Google, no cookies).

It mimics the parts of aistudio2api the portal relies on:
- admin login / pairing import / account list / delete / start
- public API protected by the engine key, *requiring* the trusted account
  scope header exactly like XGF_ACCOUNT_SCOPE=required (403 otherwise)
- account selection strictly inside the scope, 429 cooldown switching to the
  next account in the scope, Retry-After when every scoped account is cooling
- streaming with configurable first-token delay, Gemini JSON-array streaming,
  Responses API ids, and the X-Xiaoguofan-Account serving header.
"""
import asyncio
import json
import socket
import threading
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from portal.engine import EngineClient, static_locator

KEY = 'engine-internal-key'
PASSWORD = 'engine-admin-password'
MODELS = [
    {'id': 'gemini-3.8-flash', 'object': 'model', 'owned_by': 'google', 'channels': ['playground', 'build']},
    {'id': 'gemini-4-argon', 'object': 'model', 'owned_by': 'google', 'paid': True, 'channels': ['playground']},
]


class FakeEngine:
    def __init__(self):
        self.accounts = {}          # id -> {'id','label','state','models','benefit_tier'}
        self.cooling = {}           # (account, model) -> until (epoch)
        self.quota = {}             # account -> remaining successful generations before 429
        self.seen = []              # (path, scope list, serving account)
        self.imports = []
        self.deletes = []
        self.first_token_delay = 0.0
        self.chunk_delay = 0.0
        self.chunks = 3
        self.responses = 0
        self.models = [dict(m) for m in MODELS]
        self.chat_hook = None       # optional fn(body) -> (content_type, bytes)
        self.last_body = None
        self.app = self._build()
        self.server = None

    # ---- lifecycle ------------------------------------------------------
    def start(self):
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        self.port = sock.getsockname()[1]
        sock.close()
        config = uvicorn.Config(self.app, host='127.0.0.1', port=self.port, log_level='warning', lifespan='off')
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                break
            time.sleep(.02)
        return self

    def stop(self):
        if self.server:
            self.server.should_exit = True
            self.thread.join(5)

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}'

    def client(self, **kw):
        return EngineClient(static_locator({'url': self.url, 'key': KEY, 'admin_password': PASSWORD}), background=False, **kw)

    # ---- behaviour --------------------------------------------------------
    def pick(self, scope, model):
        now = time.time()
        earliest = None
        for account in scope:
            if account not in self.accounts:
                continue
            until = self.cooling.get((account, model), 0)
            if until > now:
                earliest = until if earliest is None else min(earliest, until)
                continue
            remaining = self.quota.get(account)
            if remaining is not None and remaining <= 0:
                # Google answered 429 on this account: cool it and switch (engine behaviour).
                self.cooling[(account, model)] = now+600
                earliest = now+600 if earliest is None else min(earliest, now+600)
                continue
            if remaining is not None:
                self.quota[account] = remaining-1
            return account, None
        return None, earliest

    def _build(self):
        app = FastAPI()
        engine = self

        @app.get('/health')
        def health():
            return {'status': 'ok'}

        @app.post('/api/auth/login')
        async def login(request: Request):
            body = await request.json()
            if body.get('password') != PASSWORD:
                return JSONResponse({'error': {'message': 'bad'}}, 401)
            response = JSONResponse({'ok': True})
            response.set_cookie('engine_session', 'ok')
            return response

        def admin(request):
            return request.cookies.get('engine_session') == 'ok'

        @app.post('/api/pairing')
        def pairing(request: Request):
            assert admin(request)
            return {'token': 'pair-token'}

        @app.post('/api/pairing/accounts')
        async def pairing_accounts(request: Request):
            assert request.headers.get('authorization') == 'Bearer pair-token'
            payload = await request.json()
            label = payload['label']
            assert '@' in label, 'engine accounts are keyed by Google email'
            if 'expired' in label:
                return JSONResponse({'error': {'message': '认证状态无法用于 AI Studio'}}, 422)
            engine.imports.append(label)
            engine.accounts[label] = {'id': label, 'label': label, 'enabled': True, 'state': 'ready',
                                      'models': [], 'benefit_tier': 'Free', 'active_requests': 0}
            return {'id': label}

        @app.get('/api/accounts')
        def accounts(request: Request):
            assert admin(request)
            return {'accounts': list(engine.accounts.values())}

        @app.delete('/api/accounts/{aid}')
        def delete(aid: str, request: Request):
            assert admin(request)
            engine.deletes.append(aid)
            engine.accounts.pop(aid, None)
            return {'ok': True}

        @app.post('/api/control/start')
        def start(request: Request):
            return {'ok': True}

        def guard(request):
            if request.headers.get('authorization') != 'Bearer '+KEY:
                return None, JSONResponse({'error': {'message': 'Unauthorized', 'code': 'invalid_api_key'}}, 401)
            if 'x-xiaoguofan-account-scope' not in request.headers:
                return None, JSONResponse({'error': {'message': 'request is missing the trusted account scope', 'code': 'account_scope_required'}}, 403)
            scope = [x.strip() for x in request.headers['x-xiaoguofan-account-scope'].split(',') if x.strip()]
            return scope, None

        @app.get('/v1/models')
        def models(request: Request):
            scope, error = guard(request)
            if error:
                return error
            engine.seen.append(('/v1/models', scope, None))
            return {'object': 'list', 'data': engine.models}

        async def generate(request, path, streaming_kind):
            scope, error = guard(request)
            if error:
                return error
            raw = await request.body()
            body = json.loads(raw) if raw else {}
            model = body.get('model') or path.split('/models/')[-1].split(':')[0]
            account, until = engine.pick(scope, model)
            engine.seen.append((path, scope, account))
            if account is None:
                if until is None:
                    return JSONResponse({'error': {'message': 'no eligible account', 'code': 'account_unavailable'}}, 503)
                seconds = max(1, int(until-time.time()))
                return JSONResponse({'error': {'message': f'模型 {model} 的可用账户均在冷却', 'code': 'rate_limit_exceeded'}}, 429,
                                    headers={'Retry-After': str(seconds)})
            headers = {'X-Xiaoguofan-Account': account}
            if engine.chat_hook is not None and path == '/v1/chat/completions':
                content_type, payload = engine.chat_hook(body)
                return StreamingResponse(iter([payload]), media_type=content_type, headers=headers)
            if streaming_kind == 'sse':
                async def events():
                    await asyncio.sleep(engine.first_token_delay)
                    for index in range(engine.chunks):
                        if index:
                            await asyncio.sleep(engine.chunk_delay)
                        yield ('data: '+json.dumps({'id': 'c1', 'model': model, 'choices': [{'index': 0, 'delta': {'content': f'片段{index}'}}]}, ensure_ascii=False)+'\n\n').encode()
                    yield ('data: '+json.dumps({'id': 'c1', 'model': model, 'choices': [], 'usage': {'prompt_tokens': 3, 'completion_tokens': engine.chunks}})+'\n\n').encode()
                    yield b'data: [DONE]\n\n'
                return StreamingResponse(events(), media_type='text/event-stream', headers=headers)
            if streaming_kind == 'json-array':
                async def array():
                    await asyncio.sleep(engine.first_token_delay)
                    for index in range(engine.chunks):
                        if index:
                            await asyncio.sleep(engine.chunk_delay)
                        prefix = b'[' if index == 0 else b',\r\n'
                        yield prefix+json.dumps({'candidates': [{'content': {'parts': [{'text': f'part{index}'}]}}]}).encode()
                    yield b']'
                return StreamingResponse(array(), media_type='application/json', headers=headers)
            if path == '/v1/responses':
                engine.responses += 1
                rid = f'resp_fixture{engine.responses:04d}'
                return JSONResponse({'id': rid, 'object': 'response', 'model': model, 'output': [],
                                     'usage': {'input_tokens': 2, 'output_tokens': 1}}, headers=headers)
            return JSONResponse({'id': 'c1', 'model': model, 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'ok'}}],
                                 'usage': {'prompt_tokens': 3, 'completion_tokens': 1}}, headers=headers)

        @app.post('/v1/chat/completions')
        async def chat(request: Request):
            body = await request.json()
            engine.last_body = body
            request._body = json.dumps(body).encode()
            return await generate(request, '/v1/chat/completions', 'sse' if body.get('stream') else 'json')

        @app.post('/v1/responses')
        async def responses(request: Request):
            return await generate(request, '/v1/responses', 'json')

        @app.post('/v1beta/models/{action}')
        async def gemini(action: str, request: Request):
            kind = 'json'
            if action.endswith(':streamGenerateContent'):
                kind = 'sse' if request.query_params.get('alt') == 'sse' else 'json-array'
            return await generate(request, '/v1beta/models/'+action, kind)

        return app


def portal(tmp_path, engine, **settings):
    """Portal app wired to the fake engine, with two users and API keys."""
    from fastapi.testclient import TestClient

    from portal.main import create_app
    app = create_app(tmp_path, 'test-password-long-enough', 'https://portal.example', engine=engine.client())
    s = app.state.store
    s.save_settings({'public_login': True} | settings)
    alice = s.discord_user('100000000000000001', 'Alice')
    bob = s.discord_user('100000000000000002', 'Bob')
    for u in (alice, bob):
        s.mark_verified(u['id'])
    client = TestClient(app, base_url='https://portal.example')
    return app, s, alice, bob, client


def cookie_payload(email):
    far = time.time()+86400*30
    cookies = [{'name': n, 'value': 'fixture-'+n, 'domain': '.google.com', 'path': '/', 'expires': far}
               for n in ('SAPISID', '__Secure-1PAPISID', '__Secure-3PAPISID')]
    return {'label': email, 'email': email, 'storage_state': {'cookies': cookies}, 'locale': 'en-US', 'timezone': 'UTC'}


class Served:
    """Run an ASGI app on a real loopback socket (true streaming timing)."""

    def __init__(self, app):
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self.server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=self.port, log_level='warning'))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(250):
            if self.server.started:
                break
            time.sleep(.02)
        return 'http://127.0.0.1:%d' % self.port

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(10)


def offline_engine():
    """Engine client for tests that never reach the engine (it is 'down')."""
    async def locate():
        raise ConnectionError('engine offline in this test')
    return EngineClient(locate, background=False)
