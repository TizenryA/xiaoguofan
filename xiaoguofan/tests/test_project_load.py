from test_lite import setup


def test_load_requires_session_and_fails_unavailable_without_fake_zero(tmp_path):
    c,s,a,b,*_=setup(tmp_path)
    r=c.get('/api/load');assert r.status_code==200
    d=r.json();assert not d['available']
    assert d['memory']['used_bytes'] is None and d['pool']['running'] is None
    assert d['memory']['limit_bytes']==2147483648
    assert r.headers['cache-control']=='no-store'
    c.cookies.clear();assert c.get('/api/load').status_code==401


def test_load_only_global_aggregates(tmp_path):
    c,s,*_=setup(tmp_path)
    # Test fixture has no broker telemetry. Exposure must never include users or account material.
    d=c.get('/api/load').json()
    assert set(d)=={'available','timestamp','memory','cpu','pool','refresh_seconds','requests'}
    assert set(d['pool'])=={'running','active','idle','starting','evictions','limit'}
    assert d['requests']['active']==0
