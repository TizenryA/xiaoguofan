import sqlite3

from portal.store import Store


def test_legacy_account_schema_migrates_without_losing_encrypted_state(tmp_path):
    s=Store(tmp_path,'test-password-long-enough')
    aid=s.add_account('admin','preserved',{'marker':'persist'})
    s.db.close()
    c=sqlite3.connect(tmp_path/'portal.db')
    for col in ['enabled','status','checked','note']:
        c.execute('ALTER TABLE accounts DROP COLUMN '+col)
    c.commit();c.close()
    upgraded=Store(tmp_path,'test-password-long-enough')
    assert upgraded.account('admin',aid)['payload']=={'marker':'persist'}
    assert upgraded.accounts('admin')[0]['enabled']==1
    assert upgraded.accounts('admin')[0]['status']=='unverified'
    upgraded.db.close()
    reopened=Store(tmp_path,'test-password-long-enough')
    assert reopened.account('admin',aid)['payload']=={'marker':'persist'}
