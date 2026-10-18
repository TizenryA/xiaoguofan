import time

from portal.store import Store


def test_stats_exclude_old_requests_even_without_new_traffic(tmp_path):
    s=Store(tmp_path,'test-password-long-enough')
    s.record('admin','k','old',200,1)
    s.run('UPDATE requests SET created=?',(time.time()-31*86400,))
    assert s.stats('admin')['summary']['requests']==0
    assert s.stats('admin')['recent']==[]


def test_mark_verified_cannot_stamp_unchecked_new_policy(tmp_path):
    s=Store(tmp_path,'test-password-long-enough')
    u=s.discord_user('100000000000000001','a')
    checked=s.settings()['policy']
    s.save_settings({'block_enabled':True,'blocked_guilds':['100000000000000002']})
    assert not s.mark_verified(u['id'], checked)
    assert s.user(u['id'])['verified']==0
