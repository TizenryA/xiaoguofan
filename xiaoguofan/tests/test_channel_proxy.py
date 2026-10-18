import http.server
import json
import threading

from fastapi.testclient import TestClient

from portal.main import create_app


def test_suffix_models_route_without_suffix_and_keep_anti_quota_and_tenant_scope(tmp_path):
    calls=[]
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(json.dumps({'data':[{'id':'gemini-4-argon','name':'Gemini 4 Argon','channels':['playground','build']}]}).encode())
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));calls.append(body)
            synthetic=next((t['function']['name'] for t in body.get('tools',[]) if t['function']['name'].startswith('agw_emit_')),None)
            msg={'role':'assistant','content':'isolated fixture'}
            if synthetic:msg={'role':'assistant','tool_calls':[{'id':'fixture','type':'function','function':{'name':synthetic,'arguments':json.dumps({'content':'isolated restored'})}}]}
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps({'model':body['model'],'choices':[{'index':0,'message':msg,'finish_reason':'tool_calls' if synthetic else 'stop'}]}).encode())
        def log_message(self,*args):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    class Broker:
        def __init__(self):self.channels=[]
        async def ensure(self,uid,accounts,limit,channel=''):
            self.channels.append((uid,[a['id'] for a in accounts],channel))
            return {'url':f'http://127.0.0.1:{server.server_port}','key':'fixture-only'}
    broker=Broker();app=create_app(tmp_path,'fixture-long-password','https://fixture.test',broker);s=app.state.store
    s.save_settings({'public_login':True});a=s.discord_user('111','Alice');b=s.discord_user('222','Bob')
    aid=s.add_account(a['id'],'fixture',{});key=s.create_key(a['id'],'fixture')['key'];kb=s.create_key(b['id'],'fixture')['key'];h={'Authorization':'Bearer '+key}
    s.save_preferences(a['id'],True)
    try:
        with TestClient(app,base_url='https://fixture.test') as c:
            rows=c.get('/v1/models',headers=h).json()['data']
            assert [r['id'] for r in rows]==['gemini-4-argon-pg','gemini-4-argon-build','[防截断]gemini-4-argon-pg','[防截断]gemini-4-argon-build']
            assert rows[-1]['name']=='[防截断]Gemini 4 Argon-build'
            for model,channel in [('gemini-4-argon-pg','playground'),('gemini-4-argon-build','build'),('[防截断]gemini-4-argon-build','build'),('[防截断]gemini-4-argon-pg','playground')]:
                r=c.post('/v1/chat/completions',headers=h,json={'model':model,'messages':[]});assert r.status_code==200,r.text
                assert broker.channels[-1]==(a['id'],[aid],channel)
                assert calls[-1]['model']=='gemini-4-argon'
                if model.startswith('[防截断]'):assert 'isolated restored' in r.text and 'agw_emit_' not in r.text
            assert c.get('/v1/models',headers={'Authorization':'Bearer '+kb}).status_code==503
            assert all(x[0]==a['id'] for x in broker.channels)
            records=s.all('SELECT model FROM requests WHERE generation=1');assert len(records)==4
            assert all(r['model'].endswith(('-pg','-build')) for r in records)
            token,csrf=s.login(a['id']);c.cookies.set('portal_session',token)
            assert c.get('/api/quota').json()['account_usage'][0]['models']['4a']['used']==4
            s.save_preferences(a['id'],False)
            assert len(c.get('/v1/models',headers=h).json()['data'])==2
    finally:server.shutdown();server.server_close()
