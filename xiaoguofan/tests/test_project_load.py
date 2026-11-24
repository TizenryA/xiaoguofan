from test_lite import setup


def test_load_requires_session_and_shows_no_internals(tmp_path):
    c,s,a,b,*_=setup(tmp_path)
    r=c.get('/api/load');assert r.status_code==200
    d=r.json()
    assert set(d)=={'ok','message','busy','mine_running','mine_waiting','refresh_seconds'}
    assert d['mine_running']==0 and d['mine_waiting']==0 and d['busy'] is False
    assert r.headers['cache-control']=='no-store'
    c.cookies.clear();assert c.get('/api/load').status_code==401
