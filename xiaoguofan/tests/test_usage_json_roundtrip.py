"""Exercise actual encoder-style JSON HTTP response through proxy and SQLite."""
import http.server
import json
import threading

from fastapi.testclient import TestClient

from portal.main import create_app


def test_json_with_trailing_newline_usage_reaches_records(tmp_path):
    class Upstream(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('content-length',0)))
            raw=(json.dumps({'choices':[{'message':{'content':'fixture'}}], 'usage':{'prompt_tokens':123,'completion_tokens':45}})+'\n').encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        def log_message(self,*args):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Upstream)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    class Broker:
        async def ensure(self,*args):return {'url':f'http://127.0.0.1:{server.server_port}', 'key':'fixture'}
    try:
        app=create_app(tmp_path,'fixture-password-long','http://fixture',Broker());s=app.state.store
        s.add_account('admin','own',{})
        key=s.create_key('admin','fixture')['key']
        with TestClient(app,base_url='http://fixture') as c:
            r=c.post('/v1/chat/completions',headers={'Authorization':'Bearer '+key},json={'model':'fixture','stream':False,'messages':[]})
            assert r.status_code==200
            assert r.content.endswith(b'\n')
            row=s.records('admin')['items'][0]
            assert row['input_tokens']==123 and row['output_tokens']==45
            assert row['first_token_seconds'] is not None
    finally:
        server.shutdown();server.server_close()
