import pytest

from portal.quota import reference_quota
from portal.store import Store


def payload(email='own@example.com', gaia='123'):
    return {'email':email,'storage_state':{'xiaoguofan_identity':{'email':email,'gaia_id':gaia,'authuser':0},'cookies':[]}}


def test_reimport_same_owner_identity_preserves_id_plan_disabled_and_usage(tmp_path):
    s=Store(tmp_path,'fixture-password-long')
    aid=s.add_account('admin','old',payload())
    s.update_account('admin',aid,{'plan':'pro','enabled':False})
    s.record('admin',None,'gemini-4-argon',200,.1,account_id=aid,generation=True)
    s.save_settings({'max_accounts':1})
    again=s.add_account('admin','refreshed',payload())
    assert again==aid
    assert len(s.accounts('admin'))==1
    assert s.account('admin',aid)['plan']=='pro'
    assert not s.account('admin',aid)['enabled']
    assert reference_quota(s,'admin')['account_usage'][0]['models']['4a']['used']==1
    assert s.add_account('other','separate',payload())!=aid


def test_deleted_identity_can_reimport_without_resetting_usage(tmp_path):
    s=Store(tmp_path,'fixture-password-long')
    aid=s.add_account('admin','old',payload())
    s.record('admin',None,'gemini-4-argon',200,.1,account_id=aid,generation=True)
    assert s.delete_account('admin',aid)
    assert s.account('admin',aid) is None
    assert reference_quota(s,'admin')['unattributed']['success']==1
    assert s.add_account('admin','fresh',payload())==aid
    assert reference_quota(s,'admin')['account_usage'][0]['today']['success']==1


def test_conflicting_gaia_never_overwrites_existing_account(tmp_path):
    s=Store(tmp_path,'fixture-password-long')
    aid=s.add_account('admin','old',payload())
    with pytest.raises(ValueError,match='身份'):
        s.add_account('admin','conflict',payload(gaia='456'))
    assert s.account('admin',aid)['label']=='old'


def test_legacy_account_delete_remembers_identity_without_retaining_credentials(tmp_path):
    s=Store(tmp_path,'fixture-password-long')
    aid=s.add_account('admin','legacy',payload())
    s.run('DELETE FROM account_links')
    s.delete_account('admin',aid)
    assert s.add_account('admin','fresh',payload())==aid


def test_identity_link_survives_restart_and_cannot_cross_tenant(tmp_path):
    s=Store(tmp_path,'fixture-password-long')
    aid=s.add_account('admin','first',payload())
    s.record('admin',None,'gemini-4-argon',200,.1,account_id=aid,generation=True)
    s.delete_account('admin',aid)
    s.db.close()
    s=Store(tmp_path,'fixture-password-long')
    assert s.add_account('other','separate',payload())!=aid
    assert s.add_account('admin','restored',payload())==aid
    assert reference_quota(s,'admin')['account_usage'][0]['today']['success']==1
