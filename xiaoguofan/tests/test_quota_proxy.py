def test_proxy_selects_one_owned_account_and_records_non_generation_separately(tmp_path):
    import http.server
    import threading

    from fastapi.testclient import TestClient
    from test_isolation import RecordingBroker

    from portal.main import create_app

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"FIXTURE"}}]}\n\ndata: [DONE]\n\n')
        def do_GET(self):
            self.send_response(200);self.end_headers();self.wfile.write(b'{"data":[]}')
        def log_message(self,*args):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    broker=RecordingBroker('http://127.0.0.1:'+str(server.server_port))
    app=create_app(tmp_path,'fixture-long-password','https://portal.example',broker);s=app.state.store
    first=s.add_account('admin','first',{});second=s.add_account('admin','second',{})
    key=s.create_key('admin','fixture')['key'];headers={'Authorization':'Bearer '+key}
    try:
        with TestClient(app,base_url='https://portal.example') as c:
            for _ in range(2):
                r=c.post('/v1/chat/completions',headers=headers,json={'model':'gemini-4-argon','messages':[{'role':'user','content':'fixture'}],'stream':True})
                assert r.status_code==200 and 'FIXTURE' in r.text
            assert c.get('/v1/models',headers=headers).status_code==200
            assert c.post('/v1beta/models/gemini-3.8-flash:generateContent',headers=headers,json={'contents':[]}).status_code==200
        rows=s.all('SELECT account_id,generation,model FROM requests ORDER BY id')
        assert rows[0]['account_id']==rows[1]['account_id']
        assert rows[0]['account_id'] in {first,second}
        assert rows[2]['generation']==0
        assert rows[3]['model']=='gemini-3.8-flash' and rows[3]['generation']==1
        assert all(len(ids)==1 for _,ids in broker.seen)
        assert all(uid=='admin' for uid,_ in broker.seen)
    finally:
        server.shutdown();server.server_close()


def test_legacy_database_migration_is_idempotent_and_preserves_payloads(tmp_path):
    import sqlite3

    from portal.store import Store
    s=Store(tmp_path,'fixture-long-password');aid=s.add_account('admin','old',{'label':'old'})
    s.record('admin','k','gemini-4-argon',200,1)
    original=s.one('SELECT payload FROM accounts WHERE id=?',(aid,))['payload'];s.db.close()
    db=sqlite3.connect(tmp_path/'portal.db')
    for table,col in [('accounts','plan'),('requests','account_id'),('requests','generation')]:db.execute(f'ALTER TABLE {table} DROP COLUMN {col}')
    db.commit();db.close()
    s=Store(tmp_path,'fixture-long-password')
    assert s.accounts('admin')[0]['plan']=='free'
    assert s.records('admin')['items'][0]['account_id'] is None
    assert s.one('SELECT payload FROM accounts WHERE id=?',(aid,))['payload']==original
    s.db.close();s=Store(tmp_path,'fixture-long-password');assert len(s.accounts('admin'))==1
