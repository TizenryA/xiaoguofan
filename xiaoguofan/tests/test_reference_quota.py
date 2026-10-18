import time

from test_lite import setup


def test_plan_owner_isolation_and_quotas(tmp_path):
    c,s,a,b,h,_=setup(tmp_path);headers={'X-CSRF-Token':h}
    own=s.add_account(a['id'],'mine',{});other=s.add_account(b['id'],'private',{})
    assert c.patch('/api/accounts/'+other,json={'plan':'pro'},headers=headers).status_code==404
    assert c.patch('/api/accounts/'+own,json={'plan':'pro'},headers=headers).status_code==200
    assert c.patch('/api/accounts/'+own,json={'plan':'enterprise'},headers=headers).status_code==422
    s.record(a['id'],'k','gemini-4-argon',200,1,account_id=own,generation=True)
    s.record(a['id'],'k','gemini-4-argon',503,1,account_id=own,generation=True)
    s.record(a['id'],'k','',200,1,account_id=own,generation=False)
    s.record(a['id'],'k','gemini-3.8-flash',200,1,account_id=own,generation=True)
    q=c.get('/api/quota').json();row=q['account_usage'][0]
    assert row['id']==own and row['plan']=='pro' and row['today']['attempts']==3 and row['today']['success']==2
    assert row['models']['4a']=={'used':1,'limit':50,'remaining':49}
    assert row['models']['3.8f']=={'used':1,'limit':100,'remaining':99}
    assert other not in str(q) and 'private' not in str(q)
    assert len(q['trend'])==7 and q['reference_only'] is True
    assert q['google_remaining'] is None


def test_unknown_history_failures_and_midnight_boundaries(tmp_path):
    from portal.quota import day_start, model_group
    c,s,a,b,*_=setup(tmp_path);aid=s.add_account(a['id'],'free',{})
    s.record(a['id'],'k','gemini-4-argon',200,1)
    s.record(a['id'],'k','gemini-4-argon',200,1,account_id=aid,generation=True)
    before=day_start(time.time())-1
    s.run('UPDATE requests SET created=? WHERE account_id=?',(before,aid))
    q=c.get('/api/quota').json()
    assert q['unattributed']['success']==1
    assert q['account_usage'][0]['models']['4a']=={'used':0,'limit':2,'remaining':2}
    assert q['account_usage'][0]['period']['success']==1
    assert model_group('gemini-4-argon')=='4a' and model_group('gemini-3.8-flash')=='3.8f'
    assert model_group('unknown-4-argon-custom')=='other'
    assert day_start(0)==-28800


def test_records_include_only_owned_account_label_and_request_type(tmp_path):
    c,s,a,b,*_=setup(tmp_path);own=s.add_account(a['id'],'mine',{})
    s.record(a['id'],'k','gemini-4-argon',200,1,account_id=own,generation=True)
    r=c.get('/api/records').json()['items'][0]
    assert r['account_id']==own and r['account_label']=='mine' and r['generation']==1
    s.delete_account(a['id'],own)
    r=c.get('/api/records').json()['items'][0]
    assert r['account_id']==own and r['account_label'] is None


def test_reference_limit_is_not_a_hard_limit(tmp_path):
    c,s,a,b,*_=setup(tmp_path);aid=s.add_account(a['id'],'free',{})
    for _ in range(3):s.record(a['id'],'k','gemini-4-argon',200,1,account_id=aid,generation=True)
    q=c.get('/api/quota').json()['account_usage'][0]
    assert q['models']['4a']['used']==3 and q['models']['4a']['remaining']==0
