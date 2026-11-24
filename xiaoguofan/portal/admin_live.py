"""Admin-only realtime dashboard data: engine/admission snapshot and account x model health."""
import json
import time

HEALTH_TEXT = {'good': '正常', 'warn': '有失败', 'bad': '大量失败', 'idle': '无请求'}


def mask_email(email):
    if not email or '@' not in email:
        return email or ''
    name, _, domain = email.partition('@')
    return (name[:3] if len(name) > 4 else name[:1]) + '***@' + domain


def grade(ok, counted):
    if counted <= 0:
        return 'idle'
    rate = ok / counted
    return 'good' if rate >= .9 else 'warn' if rate >= .5 else 'bad'


def recent_traffic(store, seconds=300):
    since = time.time() - seconds
    r = store.one('SELECT COUNT(*) n, COALESCE(SUM(status<400),0) ok, COALESCE(SUM(status=429),0) limited, '
                  'COALESCE(SUM(status>=500),0) failed, COALESCE(SUM(status=499),0) cancelled, '
                  'AVG(CASE WHEN status<400 THEN first_token_seconds END) ttft '
                  'FROM requests WHERE created>=? AND generation=1', (since,))
    users = store.one('SELECT COUNT(DISTINCT user_id) n FROM requests WHERE created>=? AND generation=1', (since,))['n']
    return r | {'users': users, 'window_seconds': seconds,
                'ttft': round(r['ttft'], 2) if r['ttft'] is not None else None}


def account_states(store):
    counts = {r['state']: r['n'] for r in store.all('SELECT state, COUNT(*) n FROM engine_accounts GROUP BY state')}
    total = store.one('SELECT COUNT(*) n FROM accounts')['n']
    enabled = store.one('SELECT COUNT(*) n FROM accounts a JOIN users u ON u.id=a.user_id WHERE a.enabled=1 AND u.disabled=0')['n']
    return {'by_state': counts, 'loaded': sum(counts.values()), 'saved': total, 'enabled': enabled,
            'active_requests': store.one('SELECT COALESCE(SUM(active),0) n FROM engine_accounts')['n']}


def health_matrix(store, hours=24, max_models=8, only_used=False):
    """Account x model success matrix for generation requests.

    Client cancellations (499) and bad requests (400) are not the account's fault and are
    excluded from the success rate; 429 counts against it (quota).
    """
    since = time.time() - hours*3600
    cells = store.all(
        'SELECT account_id, model, COUNT(*) n, COALESCE(SUM(status<400),0) ok, COALESCE(SUM(status=429),0) limited, '
        'COALESCE(SUM(status>=500),0) failed, COALESCE(SUM(status IN (400,499)),0) excluded, '
        'AVG(CASE WHEN status<400 THEN first_token_seconds END) ttft, MAX(created) last '
        'FROM requests WHERE created>=? AND generation=1 AND account_id IS NOT NULL AND model IS NOT NULL AND model<>\'\' '
        'GROUP BY account_id, model', (since,))
    usage = {}
    for c in cells:
        usage[c['model']] = usage.get(c['model'], 0) + c['n']
    models = [m for m, _ in sorted(usage.items(), key=lambda kv: (-kv[1], kv[0]))[:max_models]]
    accounts = store.all(
        'SELECT a.id, a.label, a.plan, a.enabled, u.name owner, u.id owner_id, e.engine_id, e.state, e.tier, e.models, e.active '
        'FROM accounts a JOIN users u ON u.id=a.user_id LEFT JOIN engine_accounts e ON e.account_id=a.id '
        'ORDER BY u.name, a.created')
    by_account = {}
    for c in cells:
        by_account.setdefault(c['account_id'], {})[c['model']] = c
    from portal.engine import human_state
    rows = []
    for a in accounts:
        seen = by_account.get(a['id'], {})
        if only_used and not seen:
            continue
        total = {'n': 0, 'ok': 0, 'counted': 0}
        out = {}
        for m in models:
            c = seen.get(m)
            if not c:
                out[m] = None
                continue
            counted = c['n'] - c['excluded']
            out[m] = {'n': c['n'], 'ok': c['ok'], 'limited': c['limited'], 'failed': c['failed'],
                      'grade': grade(c['ok'], counted),
                      'ttft': round(c['ttft'], 2) if c['ttft'] is not None else None}
        for c in seen.values():
            total['n'] += c['n']; total['ok'] += c['ok']; total['counted'] += c['n'] - c['excluded']
        try:
            engine_models = len(json.loads(a['models'] or '[]'))
        except ValueError:
            engine_models = 0
        state = a['state'] or ('disabled' if not a['enabled'] else 'pending')
        rows.append({'account_id': a['id'], 'label': a['label'], 'owner': a['owner'], 'owner_id': a['owner_id'],
                     'email': mask_email(a['engine_id']), 'plan': a['plan'], 'tier': a['tier'] or '',
                     'state': state, 'state_text': human_state(state), 'active': a['active'] or 0,
                     'engine_models': engine_models, 'cells': out, 'total': total['n'],
                     'grade': grade(total['ok'], total['counted'])})
    rank = {'bad': 0, 'warn': 1, 'good': 2, 'idle': 3}
    rows.sort(key=lambda r: (rank[r['grade']], -r['total']))
    return {'hours': hours, 'models': models, 'rows': rows, 'grades': HEALTH_TEXT}
