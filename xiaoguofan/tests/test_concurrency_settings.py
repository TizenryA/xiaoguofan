import re

import pytest
from pydantic import ValidationError
from test_lite import setup

from portal.main import Settings


def test_engine_concurrency_admin_save_and_legacy_fields_kept(tmp_path):
    c,s,*_=setup(tmp_path)
    s.save_settings({'public_login':False,'max_workers':4,'global_concurrency':3})
    token,csrf=s.login('admin');c.cookies.set('portal_session',token)
    r=c.put('/api/admin/settings',json={'engine_concurrency':100,'user_concurrency':8},headers={'X-CSRF-Token':csrf})
    assert r.status_code==200,r.text
    assert (r.json()['engine_concurrency'],r.json()['user_concurrency'])==(100,8)
    # Legacy per-user-container settings still validate and are left untouched (rollback safety).
    assert (s.settings()['max_workers'],s.settings()['global_concurrency'])==(4,3)
    html=c.get('/admin').text
    assert re.search(r'name="engine_concurrency"[^>]*max="512"',html)
    assert re.search(r'name="user_concurrency"[^>]*max="32"',html)
    assert 'name="max_workers"' not in html and 'name="global_concurrency"' not in html
    assert c.put('/api/admin/settings',json={'engine_concurrency':513},headers={'X-CSRF-Token':csrf}).status_code==422
    assert s.settings()['engine_concurrency']==100


@pytest.mark.parametrize('field,value',[('engine_concurrency',0),('engine_concurrency',513),('user_concurrency',0),('user_concurrency',33),('global_concurrency',101)])
def test_concurrency_boundary(field,value):
    with pytest.raises(ValidationError):Settings(**{field:value})
