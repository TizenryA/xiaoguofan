import http.server
import json
import threading

from fastapi.testclient import TestClient
from test_isolation import RecordingBroker

from portal.main import create_app


def test_alias_api_toggle_isolation_plain_and_stream_quota(tmp_path):
    calls=[]
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps({'object':'list','data':[{'id':'gemini-4-argon','name':'Gemini 4 Argon','displayName':'Gemini 4 Argon'},{'id':'gemini-image','name':'Gemini Image'}]}).encode())
        def do_POST(self):
            b=json.loads(self.rfile.read(int(self.headers['Content-Length'])));calls.append(b)
            name=next((t['function']['name'] for t in b.get('tools',[]) if t['function']['name'].startswith('agw_emit_')),None)
            msg={'role':'assistant','content':'plain fixture'}
            if name:msg={'role':'assistant','tool_calls':[{'id':'t','type':'function','function':{'name':name,'arguments':json.dumps({'content':'restored fixture'})}}]}
            self.send_response(200);self.send_header('Content-Type','text/event-stream' if b.get('stream') else 'application/json');self.end_headers()
            if b.get('stream'):
                msg['tool_calls'][0]['index']=0
                event={'model':b['model'],'choices':[{'index':0,'delta':msg,'finish_reason':'tool_calls'}],'usage':{'prompt_tokens':2,'completion_tokens':4}}
                self.wfile.write(('data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n').encode())
            else:self.wfile.write(json.dumps({'model':b['model'],'choices':[{'index':0,'message':msg,'finish_reason':'tool_calls' if name else 'stop'}],'usage':{'prompt_tokens':2,'completion_tokens':4}}).encode())
        def log_message(self,*a):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    broker=RecordingBroker('http://127.0.0.1:'+str(server.server_port));app=create_app(tmp_path,'fixture-long-password','https://portal.example',broker);s=app.state.store
    owner=s.discord_user('111','owner');other=s.discord_user('222','other')
    s.save_settings({'public_login':True});s.mark_verified(owner['id']);s.mark_verified(other['id'])
    own=s.add_account(owner['id'],'own',{});s.add_account(other['id'],'other',{})
    key=s.create_key(owner['id'],'test')['key'];key2=s.create_key(other['id'],'test')['key']
    h={'Authorization':'Bearer '+key};h2={'Authorization':'Bearer '+key2}
    token,csrf=s.login(owner['id']);token2,csrf2=s.login(other['id'])
    try:
        with TestClient(app,base_url='https://portal.example') as c:
            assert c.get('/api/preferences').status_code==401
            c.cookies.set('portal_session',token)
            assert c.get('/api/preferences').json()=={'anti_truncation':False}
            assert c.put('/api/preferences',json={'anti_truncation':True}).status_code==403
            r=c.put('/api/preferences',headers={'X-CSRF-Token':csrf},json={'anti_truncation':True});assert r.status_code==200,r.text
            assert c.put('/api/preferences',headers={'X-CSRF-Token':csrf},json={'anti_truncation':'false'}).status_code==422
            catalogue=c.get('/v1/models',headers=h).json()['data']
            assert '[防截断]gemini-4-argon' in [r['id'] for r in catalogue]
            assert [r['name'] for r in catalogue]==['Gemini 4 Argon','Gemini Image','[防截断]Gemini 4 Argon']
            assert catalogue[-1]['displayName']=='[防截断]Gemini 4 Argon'
            assert '[防截断]' not in c.get('/v1/models',headers=h2).text
            for stream in [False,True]:
                r=c.post('/v1/chat/completions',headers=h,json={'model':'[防截断]gemini-4-argon','messages':[{'role':'user','content':'fixture'}],'stream':stream});assert r.status_code==200,r.text
                assert 'restored fixture' in r.text and 'agw_emit_' not in r.text
            assert all(b['model']=='gemini-4-argon' for b in calls)
            r=c.post('/v1/chat/completions',headers=h,json={'model':'gemini-4-argon','messages':[]});assert 'plain fixture' in r.text
            assert not calls[-1].get('tools')
            assert c.post('/v1/responses',headers=h,json={'model':'[防截断]gemini-4-argon'}).status_code==400
            assert c.post('/v1/chat/completions',headers=h2,json={'model':'[防截断]gemini-4-argon','messages':[]}).status_code==400
            assert c.post('/v1/chat/completions',headers=h,json={'model':'[防截断]gemini-image','messages':[]}).status_code==400
            r=c.get('/api/quota');assert r.json()['account_usage'][0]['models']['4a']['used']==3,r.text
            rows=s.all('SELECT * FROM requests WHERE generation=1 AND status=200');assert len(rows)==3
            assert all(r['user_id']==owner['id'] and r['account_id']==own for r in rows)
            assert rows[0]['model'].startswith('[防截断]') and rows[1]['output_tokens']==4
            c.put('/api/preferences',headers={'X-CSRF-Token':csrf},json={'anti_truncation':False})
            assert '[防截断]' not in c.get('/v1/models',headers=h).text
            assert c.post('/v1/chat/completions',headers=h,json={'model':'[防截断]gemini-4-argon'}).status_code==400
    finally:server.shutdown();server.server_close()
