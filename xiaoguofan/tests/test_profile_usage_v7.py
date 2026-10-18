import json

from fastapi.testclient import TestClient

from portal.main import create_app
from portal.store import Store
from portal.usage import UsageMeter


def test_discord_avatar_stored_and_returned_without_token(tmp_path):
    s=Store(tmp_path,'fixture-long-password')
    u=s.discord_user('100000000000000001','Alice',avatar='a_'+'b'*32)
    assert u['avatar']=='a_'+'b'*32
    app=create_app(tmp_path,'fixture-long-password','https://portal.test')
    app.state.store.save_settings({'public_login':True})
    token,_=app.state.store.login(u['id'])
    c=TestClient(app,base_url='https://portal.test');c.cookies.set('portal_session',token)
    me=c.get('/api/me').json()
    assert me['avatar_url']=='https://cdn.discordapp.com/avatars/100000000000000001/a_'+ 'b'*32+'.png?size=128'
    assert not any(k in me for k in ['access_token','discord_id','avatar'])
    s.discord_user('100000000000000001','Alice',avatar='https://evil.example/tracker')
    assert s.user(u['id'])['avatar'] is None


def test_first_token_ignores_role_usage_and_partial_frame(monkeypatch):
    import portal.usage
    now=[10.0];monkeypatch.setattr(portal.usage.time,'monotonic',lambda:now[0])
    m=UsageMeter(started=10)
    now[0]=11;m.feed(b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n')
    assert m.first_token_seconds is None
    now[0]=12;m.feed(b'data: {"choices":[{"delta":{"content":"')
    now[0]=12.5;m.feed(b'Hello"}}]}\n\n')
    assert m.first_token_seconds==2.5
    now[0]=15;m.feed(b'data: {"usage":{"prompt_tokens":4,"completion_tokens":2}}\n\n')
    assert m.finish()=={'input_tokens':4,'output_tokens':2}
    assert m.first_token_seconds==2.5


def test_record_migration_keeps_old_ttft_unknown(tmp_path):
    s=Store(tmp_path,'fixture-long-password');s.record('admin','key','m',200,3)
    s.record('admin','key','m',200,3,{'input_tokens':4,'output_tokens':2},first_token_seconds=1.25)
    rows=s.records('admin')['items']
    assert rows[0]['first_token_seconds']==1.25
    assert rows[1]['first_token_seconds'] is None
    assert rows[0]['input_tokens']==4


def test_nonstream_json_first_response_is_measured(monkeypatch):
    import portal.usage
    monkeypatch.setattr(portal.usage.time,'monotonic',lambda:13.0)
    m=UsageMeter(started=10);m.feed(json.dumps({'choices':[{'message':{'content':'Hi'}}]}).encode())
    m.finish();assert m.first_token_seconds==3.0
