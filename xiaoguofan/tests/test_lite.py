from engine_fixture import offline_engine
from fastapi.testclient import TestClient

from portal.main import create_app


def setup(tmp_path):
    broker=offline_engine();app=create_app(tmp_path,'test-password-long-enough','https://portal.example',engine=broker)
    s=app.state.store;s.save_settings({'public_login':True})
    a=s.discord_user('100000000000000001','Alice');b=s.discord_user('100000000000000002','Bob')
    token,csrf=s.login(a['id']);c=TestClient(app,base_url='https://portal.example');c.cookies.set('portal_session',token)
    return c,s,a,b,csrf,broker

def test_lite_entry_and_admin_are_separate(tmp_path):
    c,*_=setup(tmp_path)
    assert 'lite.js' in c.get('/').text
    assert 'app.js' in c.get('/admin').text
    assert c.get('/api/admin/settings').status_code==403

def test_quota_is_personal_and_unknown_upstream_is_null(tmp_path):
    c,s,a,b,*_=setup(tmp_path)
    s.add_account(b['id'],'private',{})
    d=c.get('/api/quota').json()
    assert d['google_remaining'] is None
    assert d['accounts']['used']==0
    # The per-minute limit is off unless the admin enables it; concurrency is per user.
    assert d['requests_per_minute'] is None and d['concurrency']==4

def test_account_controls_and_check_are_owner_scoped(tmp_path):
    c,s,a,b,csrf,broker=setup(tmp_path);h={'X-CSRF-Token':csrf}
    own=s.add_account(a['id'],'own',{});other=s.add_account(b['id'],'other',{})
    assert c.patch('/api/accounts/'+other,json={'label':'stolen'},headers=h).status_code==404
    assert c.post('/api/accounts/'+other+'/check',headers=h).status_code==404
    assert c.patch('/api/accounts/'+own,json={'label':'renamed','enabled':False},headers=h).status_code==200
    row=c.get('/api/accounts').json()[0]
    assert row['label']=='renamed' and not row['enabled']
    r=c.post('/api/accounts/'+own+'/check',headers=h)
    assert r.status_code==200 and r.json()['message']=='已停用'
    assert c.get('/api/accounts').json()[0]['live']['text']=='已停用'

def test_record_pages_csv_no_cross_user_data(tmp_path):
    c,s,a,b,*_=setup(tmp_path)
    for i in range(4):s.record(a['id'],'k','=formula' if i==0 else 'own',200,1)
    s.record(b['id'],'k','BOB-PRIVATE',200,1)
    d=c.get('/api/records?limit=2').json()
    assert d['total']==4 and len(d['items'])==2
    assert c.get('/api/records?offset=3&limit=2').json()['items'][0]['model']=='=formula'
    csv=c.get('/api/records.csv')
    assert csv.status_code==200 and 'BOB-PRIVATE' not in csv.text
    assert "'=formula" in csv.text
    assert c.get('/api/records?limit=100000').status_code==422

def test_disabled_accounts_never_reach_worker(tmp_path):
    c,s,a,b,csrf,_=setup(tmp_path)
    own=s.add_account(a['id'],'own',{})
    c.patch('/api/accounts/'+own,json={'enabled':False},headers={'X-CSRF-Token':csrf})
    key=s.create_key(a['id'],'k')['key']
    assert c.get('/v1/models',headers={'Authorization':'Bearer '+key}).status_code==503
