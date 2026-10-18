
def test_tenant_accounts_and_keys_are_isolated(tmp_path):
    from portal.store import Store
    s = Store(tmp_path, 'test-password-long-enough')
    a = s.discord_user('100000000000000001', 'Alice')
    b = s.discord_user('100000000000000002', 'Bob')
    aid = s.add_account(a['id'], '私有账户', {'storage_state': {'cookies': []}})
    assert s.accounts(b['id']) == []
    assert s.account(b['id'], aid) is None
    assert s.account(a['id'], aid)['label'] == '私有账户'
    key = s.create_key(a['id'], '测试密钥')
    assert s.key_user(key['key'])['id'] == a['id']
    assert s.keys(b['id']) == []
    assert not s.revoke_key(b['id'], key['id'])
    assert s.revoke_key(a['id'], key['id'])
    assert s.key_user(key['key']) is None
    assert key['key'].encode() not in (tmp_path / 'portal.db').read_bytes()
    assert b'storage_state' not in (tmp_path / 'portal.db').read_bytes()
