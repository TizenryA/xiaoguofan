import time

from test_oauth_flow import fixture, start


def test_discord_session_and_verification_can_last_thirty_days(tmp_path,monkeypatch):
    c,s,_=fixture(tmp_path,monkeypatch)
    s.save_settings({'verification_hours':720})
    state=start(c)
    r=c.get('/auth/discord/callback',params={'state':state,'code':'fixture'},follow_redirects=False)
    assert r.status_code==302
    assert 'Max-Age=2592000' in r.headers['set-cookie']
    row=s.one('SELECT expires FROM sessions')
    assert 2591990 < row['expires']-time.time() <= 2592000
    now=time.time()
    monkeypatch.setattr(time,'time',lambda:now+29*86400)
    assert c.get('/api/me').status_code==200
    monkeypatch.setattr(time,'time',lambda:now+31*86400)
    assert c.get('/api/me').status_code==401


def test_admin_session_still_one_day(tmp_path):
    from portal.store import Store
    s=Store(tmp_path,'fixture-password-long');s.login('admin')
    assert 86390 < s.one('SELECT expires FROM sessions')['expires']-time.time() <= 86400


def test_admin_settings_accepts_thirty_day_verification():
    from portal.main import Settings
    assert Settings(verification_hours=720).verification_hours==720
