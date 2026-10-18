from portal.quota import choose_account
from portal.store import Store


def test_reference_budget_selects_next_own_account_but_never_enforces_limit(tmp_path):
    s=Store(tmp_path,'fixture-long-password')
    a=s.add_account('admin','a',{});b=s.add_account('admin','b',{})
    rows=[s.account('admin',a),s.account('admin',b)]
    assert choose_account(rows,s,'admin','gemini-4-argon')['id']==a
    for _ in range(2):s.record('admin','k','gemini-4-argon',200,1,account_id=a,generation=True)
    assert choose_account(rows,s,'admin','gemini-4-argon')['id']==b
    for _ in range(2):s.record('admin','k','gemini-4-argon',200,1,account_id=b,generation=True)
    assert choose_account(rows,s,'admin','gemini-4-argon')['id'] in {a,b}
    assert choose_account(rows,s,'admin','gemini-3.8-flash')['id']==b
    s.record('admin','k','gemini-3.8-flash',503,1,account_id=b,generation=True)
    assert choose_account(rows,s,'admin','gemini-3.8-flash')['id']==a
