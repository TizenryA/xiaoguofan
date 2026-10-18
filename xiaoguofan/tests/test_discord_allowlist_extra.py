from test_oauth_flow import USER, fixture, start


def test_policy_change_during_allowlisted_login_is_rejected(tmp_path,monkeypatch):
    c,s,_=fixture(tmp_path,monkeypatch,'policy_race')
    s.save_settings({'discord_allowlist':[USER]})
    state=start(c)
    assert c.get('/auth/discord/callback',params={'state':state,'code':'fixture'},follow_redirects=False).status_code==403
    assert s.one('SELECT COUNT(*) n FROM sessions')['n']==0


def test_nonallowlisted_member_still_checks_blocked_guilds(tmp_path,monkeypatch):
    c,s,calls=fixture(tmp_path,monkeypatch,'blocked')
    s.save_settings({'discord_allowlist':['100000000000000002']})
    state=start(c)
    assert c.get('/auth/discord/callback',params={'state':state,'code':'fixture'},follow_redirects=False).status_code==403
    assert any(p.endswith('/guilds') for p in calls)


def test_allowlist_does_not_override_verification_expiry(tmp_path,monkeypatch):
    import time
    c,s,_=fixture(tmp_path,monkeypatch,'blocked')
    s.save_settings({'discord_allowlist':[USER]})
    state=start(c)
    assert c.get('/auth/discord/callback',params={'state':state,'code':'fixture'},follow_redirects=False).status_code==302
    uid=c.get('/api/me').json()['id']
    s.run('UPDATE users SET verified=? WHERE id=?',(time.time()-25*3600,uid))
    assert c.get('/api/me').status_code==403
