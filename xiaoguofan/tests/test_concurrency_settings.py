import re

import pytest
from pydantic import ValidationError
from test_lite import setup

from portal.main import Settings


def test_concurrency_100_admin_save_and_worker_limit_unchanged(tmp_path):
    c,s,*_=setup(tmp_path)
    s.save_settings({'public_login':False,'max_workers':4})
    token,csrf=s.login('admin');c.cookies.set('portal_session',token)
    r=c.put('/api/admin/settings',json={'global_concurrency':100},headers={'X-CSRF-Token':csrf})
    assert r.status_code==200,r.text
    assert r.json()['global_concurrency']==100
    assert s.settings()['global_concurrency']==100
    assert s.settings()['max_workers']==4
    html=c.get('/admin').text
    assert re.search(r'name="global_concurrency"[^>]*max="100"',html)
    assert c.put('/api/admin/settings',json={'global_concurrency':101},headers={'X-CSRF-Token':csrf}).status_code==422
    assert s.settings()['global_concurrency']==100


@pytest.mark.parametrize('value',[0,101])
def test_concurrency_boundary(value):
    with pytest.raises(ValidationError):Settings(global_concurrency=value)
