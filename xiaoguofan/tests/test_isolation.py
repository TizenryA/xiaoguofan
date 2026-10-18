
from fastapi.testclient import TestClient

from portal.main import create_app


class RecordingBroker:
    def __init__(self, url):
        self.url = url
        self.seen = []

    async def stop(self, uid):
        return {'ok':True}

    async def ensure(self, uid, accounts, limit):
        self.seen.append((uid, [a['id'] for a in accounts]))
        return {'url':self.url, 'key':'internal-private-key'}


def test_two_users_request_data_and_control_are_isolated(tmp_path):
    # A real HTTP loopback upstream exercises the streaming proxy; this is not a Google generation test.
    import http.server
    import threading
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.headers['Authorization'] == 'Bearer internal-private-key'
            assert self.headers.get('Cookie') is None
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"data":[],"usage":{"prompt_tokens":4,"completion_tokens":2}}')
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1',0), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    broker = RecordingBroker('http://127.0.0.1:'+str(server.server_port))
    app = create_app(tmp_path, 'test-password-long-enough', 'https://portal.example', broker)
    s = app.state.store
    a,b = [s.discord_user(str(100000000000000000+i), name) for i,name in [(1,'Alice'),(2,'Bob')]]
    s.save_settings({'public_login':True})
    aid = s.add_account(a['id'], 'Alice 私有', {'label':'a', 'storage_state':{'cookies':[]}})
    ka,kb = [s.create_key(u['id'], 'test')['key'] for u in [a,b]]
    ta,ca = s.login(a['id'])
    tb,cb = s.login(b['id'])
    try:
        with TestClient(app, base_url='https://portal.example') as c:
            c.cookies.set('portal_session',tb)
            assert c.get('/api/accounts').json() == []
            assert c.delete('/api/accounts/'+aid,headers={'X-CSRF-Token':cb}).status_code == 404
            assert c.get('/api/admin/settings').status_code == 403
            assert c.get('/v1/models',headers={'Authorization':'Bearer '+kb}).status_code == 503
            assert broker.seen == []
            r = c.get('/v1/models?key=malicious-other-key',headers={'Authorization':'Bearer '+ka,'X-API-Key':kb,'Cookie':'google=secret'})
            assert r.status_code == 200
            assert broker.seen == [(a['id'], [aid])]
            assert s.stats(a['id'])['summary']['requests'] == 1
            assert s.stats(b['id'])['summary']['requests'] == 0
            s.save_settings({'block_enabled':True,'blocked_guilds':['123456789012345678']})
            assert c.get('/v1/models',headers={'Authorization':'Bearer '+ka}).status_code == 403
            s.mark_verified(a['id'])
            assert c.get('/v1/models',headers={'Authorization':'Bearer '+ka}).status_code == 200
    finally:
        server.shutdown()
        server.server_close()


def test_account_validation_rejects_wrong_domain(tmp_path):
    import pytest
    from fastapi import HTTPException

    from portal.main import validate_account
    with pytest.raises(HTTPException):
        validate_account({'locale':'en-US','timezone':'UTC','storage_state':{'cookies':[{'domain':'google.com.evil.test','value':'secret'}]}})


def test_policy_rejects_malformed_guild_results():
    from portal.oauth import check_guilds
    assert not check_guilds({'block_enabled':True,'blocked_guilds':['123']}, [{'name':'missing-id'}])
