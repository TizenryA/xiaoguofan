from test_admin_stats import admin
from test_lite import setup


def test_admin_stats_search_literal_and_join_counts(tmp_path):
    c,s,a,b,*_=setup(tmp_path);admin(c,s)
    s.run('UPDATE users SET name=? WHERE id=?',('100%_test',a['id']))
    for _ in range(3):s.add_account(a['id'],'private',{})
    for _ in range(4):s.record(a['id'],'k','m',200,1,{'output_tokens':3},generation=1)
    d=c.get('/api/admin/stats?q=%25_').json()
    assert d['users']['total']==1
    u=d['users']['items'][0]
    assert u['accounts']==3 and u['requests']==4 and u['output_tokens']==12
    assert u['metered_requests']==4
    d=c.get('/api/admin/stats?q='+a['id']).json();assert d['users']['total']==1
    assert c.get('/api/admin/stats?q=not-here').json()['users']['items']==[]
    s.run('UPDATE users SET disabled=1 WHERE id=?',('admin',))
    assert c.get('/api/admin/stats').status_code==401
