

def test_discord_policy_is_server_side_and_fails_closed():
    from portal.oauth import check_guilds
    config = {'block_enabled': True, 'blocked_guilds': ['100000000000000001']}
    assert check_guilds(config, [{'id': '200000000000000002'}])
    assert not check_guilds(config, [{'id': '100000000000000001'}])
    assert not check_guilds(config, None)
    assert check_guilds({'block_enabled': False}, None)


def test_oauth_state_bound_to_browser_and_single_use(tmp_path):
    from portal.oauth import OAuthStates
    states = OAuthStates()
    token = states.issue('browser-a', 5)
    assert states.consume(token, 'browser-b', 5) is False
    assert states.consume(token, 'browser-a', 6) is False
    assert states.consume(token, 'browser-a', 5)
    assert states.consume(token, 'browser-a', 5) is False
