def test_proxy_uses_only_owned_accounts_and_records_non_generation_separately(tmp_path):
    from engine_fixture import FakeEngine, cookie_payload, portal
    fake = FakeEngine().start()
    try:
        app, s, alice, bob, c = portal(tmp_path, fake)
        first = s.add_account(alice['id'], 'first', cookie_payload('first@gmail.com'))
        second = s.add_account(alice['id'], 'second', cookie_payload('second@gmail.com'))
        s.add_account(bob['id'], 'bob', cookie_payload('bob@gmail.com'))
        headers = {'Authorization': 'Bearer '+s.create_key(alice['id'], 'fixture')['key']}
        with c:
            for _ in range(2):
                r = c.post('/v1/chat/completions', headers=headers, json={'model': 'gemini-4-argon', 'messages': [{'role': 'user', 'content': 'fixture'}], 'stream': True})
                assert r.status_code == 200 and '片段0' in r.text
            assert c.get('/v1/models', headers=headers).status_code == 200
            assert c.post('/v1beta/models/gemini-3.8-flash:countTokens', headers=headers, json={'contents': []}).status_code in (200, 404, 405)
            assert c.post('/v1beta/models/gemini-3.8-flash:generateContent', headers=headers, json={'contents': []}).status_code == 200
        rows = s.all('SELECT account_id,generation,model FROM requests ORDER BY id')
        gens = [r for r in rows if r['generation'] == 1]
        assert all(r['account_id'] in {first, second} for r in gens)
        assert any(r['generation'] == 0 for r in rows)
        assert gens[-1]['model'] == 'gemini-3.8-flash'
        assert all(set(scope) == {'first@gmail.com', 'second@gmail.com'} for p, scope, _ in fake.seen if p != '/v1/models')
    finally:
        fake.stop()


def test_legacy_database_migration_is_idempotent_and_preserves_payloads(tmp_path):
    import sqlite3

    from portal.store import Store
    s=Store(tmp_path,'fixture-long-password');aid=s.add_account('admin','old',{'label':'old'})
    s.record('admin','k','gemini-4-argon',200,1)
    original=s.one('SELECT payload FROM accounts WHERE id=?',(aid,))['payload'];s.db.close()
    db=sqlite3.connect(tmp_path/'portal.db')
    for table,col in [('accounts','plan'),('requests','account_id'),('requests','generation')]:db.execute(f'ALTER TABLE {table} DROP COLUMN {col}')
    db.commit();db.close()
    s=Store(tmp_path,'fixture-long-password')
    assert s.accounts('admin')[0]['plan']=='free'
    assert s.records('admin')['items'][0]['account_id'] is None
    assert s.one('SELECT payload FROM accounts WHERE id=?',(aid,))['payload']==original
    s.db.close();s=Store(tmp_path,'fixture-long-password');assert len(s.accounts('admin'))==1
