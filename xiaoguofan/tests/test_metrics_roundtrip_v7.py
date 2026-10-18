"""Local HTTP fixtures, never real Google generation evidence."""
import http.server
import json
import threading
import time

from fastapi.testclient import TestClient

from portal.main import create_app


def test_ttft_and_usage_roundtrip_to_records_and_csv(tmp_path):
    class Upstream(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers.get('content-length',0))))
            self.server.seen=body

            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n');self.wfile.flush()
            time.sleep(.05)
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n');self.wfile.flush()
            self.wfile.write(b'data: {"usage":{"prompt_tokens":8,"completion_tokens":3}}\n\ndata: [DONE]\n\n');self.wfile.flush()
        def log_message(self,*a):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Upstream)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    class Broker:
        async def ensure(self,uid,accounts,limit):return {'url':f'http://127.0.0.1:{server.server_port}','key':'local-fixture'}
    try:
        app=create_app(tmp_path,'fixture-long-password','http://portal.test',Broker());s=app.state.store
        s.add_account('admin','own',{})
        key=s.create_key('admin','local')['key'];token,_=s.login('admin')
        c=TestClient(app,base_url='http://portal.test');c.cookies.set('portal_session',token)
        r=c.post('/v1/chat/completions',headers={'Authorization':'Bearer '+key},json={'model':'gemini-4-argon','stream':True,'messages':[]})
        assert r.status_code==200 and 'Hello' in r.text
        assert server.seen['stream_options']['include_usage'] is True
        row=c.get('/api/records').json()['items'][0]
        assert row['input_tokens']==8 and row['output_tokens']==3
        assert .04<row['first_token_seconds']<=row['duration']
        assert '首字耗时秒' in c.get('/api/records.csv').text
        assert 'Hello' not in json.dumps(row)
    finally:server.shutdown();server.server_close()
