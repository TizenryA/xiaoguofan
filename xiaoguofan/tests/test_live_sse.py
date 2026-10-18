"""Real TCP HTTP/SSE verification with a fixture upstream, NOT a Google test."""
import asyncio
import http.server
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from portal.main import create_app


@pytest.mark.parametrize('anti',[False,True])
def test_live_sse_disconnect_releases_user_and_global_slots(tmp_path,anti):
    seen=[]
    class Upstream(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length=int(self.headers.get('content-length','0'));self.rfile.read(length)
            seen.append(self.headers['Authorization'])
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            try:
                for _ in range(500):
                    self.wfile.write(b'data: {"choices":[{"delta":{"content":"fixture"}}]}\n\n');self.wfile.flush();time.sleep(.02)
            except (BrokenPipeError,ConnectionResetError):pass
        def log_message(self,*a):pass
    upstream=http.server.ThreadingHTTPServer(('127.0.0.1',0),Upstream)
    upstream.daemon_threads=True
    threading.Thread(target=upstream.serve_forever,daemon=True).start()
    class Broker:
        async def ensure(self,uid,accounts,limit):
            assert len(accounts)==1 and accounts[0]['user_id']==uid
            return {'url':f'http://127.0.0.1:{upstream.server_port}','key':'private-'+uid}
    app=create_app(tmp_path,'test-password-long-enough','http://127.0.0.1',Broker())
    store=app.state.store;store.save_settings({'public_login':True,'global_concurrency':2,'requests_per_minute':120})
    users=[store.discord_user(str(100000000000000001+i),'user'+str(i)) for i in range(3)]
    keys=[]
    for u in users:
        store.add_account(u['id'],'own',{});keys.append(store.create_key(u['id'],'test')['key']);store.save_preferences(u['id'],anti)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='critical',access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.02)
    assert server.started
    async def exercise():
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',timeout=5) as c:
            async def request(i):
                return await c.send(c.build_request('POST','/v1/chat/completions',headers={'Authorization':'Bearer '+keys[i]},json={'model':('[防截断]' if anti else '')+'fixture','stream':True}),stream=True)
            a=await request(0);assert a.status_code==200
            lines=a.aiter_lines()
            assert 'fixture' in (await anext(lines))
            b=await request(1);assert b.status_code==200
            assert (await request(0)).status_code==429
            assert (await request(2)).status_code==429
            await a.aclose()
            for _ in range(30):
                r=await request(0)
                if r.status_code==200:break
                await r.aclose();await asyncio.sleep(.03)
            assert r.status_code==200, 'Disconnect leaked per-user or global slot'
            await r.aclose();await b.aclose()
            for _ in range(100):
                if len(store.stats(users[0]['id'])['recent'])>=2:break
                await asyncio.sleep(.02)
            rows=store.stats(users[0]['id'])['recent']
            assert len(rows)>=2 and all(r['status']==499 for r in rows)
            assert store.stats(users[2]['id'])['summary']['requests']==0
    try:
        asyncio.run(exercise())
        assert set(seen)=={'Bearer private-'+u['id'] for u in users[:2]}
    finally:
        server.should_exit=True;thread.join(timeout=10);upstream.shutdown();upstream.server_close()
