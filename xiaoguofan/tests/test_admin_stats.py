import time
from datetime import datetime, timedelta, timezone

from test_lite import setup


def admin(c,s):
    token,_=s.login('admin');c.cookies.set('portal_session',token)


def test_admin_stats_permissions_and_aggregation(tmp_path):
    c,s,a,b,*_=setup(tmp_path)
    assert c.get('/api/admin/stats').status_code==403
    c.cookies.clear();assert c.get('/api/admin/stats').status_code==401
    admin(c,s)
    for uid in [a['id'],a['id'],b['id']]:s.add_account(uid,'SECRET_LABEL',{'secret':'SECRET_COOKIE'})
    s.run('UPDATE accounts SET enabled=0 WHERE user_id=?',(b['id'],))
    s.record(a['id'],'secret-key','gemini-pg',200,2,{'input_tokens':10,'output_tokens':5},generation=1)
    s.record(a['id'],'secret-key','gemini-pg',403,1,generation=1)
    s.record(b['id'],'secret-key','',200,.1,generation=0)
    s.record(b['id'],'secret-key','older',200,1,generation=1)
    s.run('UPDATE requests SET created=? WHERE model=?',(time.time()-40*86400,'older'))
    r=c.get('/api/admin/stats?days=7&limit=1');assert r.status_code==200,r.text
    d=r.json()
    assert d['totals']['registered_users']==2 and d['totals']['accounts']==3
    assert d['totals']['enabled_accounts']==2
    assert d['summary']['requests']==3 and d['summary']['success']==2 and d['summary']['failed']==1
    assert d['summary']['generations']==2 and d['summary']['active_users']==2
    assert d['summary']['input_tokens']==10 and d['summary']['output_tokens']==5
    assert d['summary']['metered_requests']==1 and len(d['daily'])==7
    assert d['users']['total']==3 and len(d['users']['items'])==1
    assert d['models'][0]['model']=='gemini-pg'
    assert d['models'][0]['requests']==2
    assert sum(x['requests'] for x in d['daily'])==3
    assert s.one("SELECT COUNT(*) n FROM requests WHERE model='older'")['n']==1
    for secret in ['SECRET_LABEL','SECRET_COOKIE','secret-key','payload','discord_id','hash']:assert secret not in r.text
    assert c.get('/api/admin/stats?days=0').status_code==422
    assert c.get('/api/admin/stats?days=31').status_code==422
    assert c.get('/api/admin/stats?limit=101').status_code==422
    assert c.get('/api/admin/stats?offset=-1').status_code==422


def test_admin_stats_beijing_day_and_zero_state(tmp_path):
    c,s,a,b,*_=setup(tmp_path);admin(c,s)
    from portal.admin_stats import admin_statistics
    tz=timezone(timedelta(hours=8));now=datetime(2026,10,19,0,10,tzinfo=tz).timestamp()
    s.record(a['id'],'k','yesterday',200,1)
    s.run('UPDATE requests SET created=?',(now-1200,))
    d=admin_statistics(s,days=1,now=now)
    assert d['summary']['requests']==0 and d['summary']['success_rate'] is None
    assert d['daily']==[{'day':'2026-10-19','requests':0,'success':0,'failed':0,'new_users':0}]
    d=admin_statistics(s,days=2,now=now)
    assert d['daily'][0]['requests']==1 and d['summary']['success_rate']==100
    assert all(x['accounts']==0 for x in d['users']['items'])
