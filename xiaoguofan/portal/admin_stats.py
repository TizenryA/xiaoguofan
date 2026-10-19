"""Admin-only metadata aggregates. Never decrypt accounts or read key/session secrets."""
import time
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))
METRICS = '''COUNT(*) requests, COALESCE(SUM(status>=200 AND status<300),0) success,
 COALESCE(SUM(CASE WHEN generation IS NULL THEN model!='' ELSE generation=1 END),0) generations,
 COALESCE(SUM(input_tokens),0) input_tokens, COALESCE(SUM(output_tokens),0) output_tokens,
 COALESCE(SUM(input_tokens IS NOT NULL OR output_tokens IS NOT NULL),0) metered_requests,
 AVG(duration) avg_duration, AVG(first_token_seconds) avg_first_token_seconds, MAX(created) last_request'''


def finish(row):
    row['failed'] = row['requests']-row['success']
    row['success_rate'] = round(row['success']*100/row['requests'],2) if row['requests'] else None
    return row


def admin_statistics(store, days=7, limit=20, offset=0, query='', now=None):
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now,BEIJING).replace(hour=0,minute=0,second=0,microsecond=0)
    start = (today-timedelta(days=days-1)).timestamp()
    args = (start,now)
    # One consistent read snapshot through the store's shared-connection lock. Read only.
    with store.lock:
        totals = store.one('''SELECT COUNT(*) users,
          COALESCE(SUM(discord_id IS NOT NULL),0) registered_users,
          COALESCE(SUM(discord_id IS NOT NULL AND disabled=1),0) disabled_users,
          COALESCE(SUM(discord_id IS NOT NULL AND created>=? AND created<=?),0) new_users
          FROM users''',args)
        totals.update(store.one('''SELECT COUNT(*) accounts,
          COALESCE(SUM(enabled=1),0) enabled_accounts,COUNT(DISTINCT user_id) account_owners FROM accounts'''))
        totals['active_keys'] = store.one('SELECT COUNT(*) n FROM api_keys WHERE revoked=0')['n']
        summary = finish(store.one('SELECT '+METRICS+',COUNT(DISTINCT user_id) active_users FROM requests WHERE created>=? AND created<=?',args))
        grouped = store.all("SELECT strftime('%Y-%m-%d',created,'unixepoch','+8 hours') day,COUNT(*) requests,SUM(status>=200 AND status<300) success FROM requests WHERE created>=? AND created<=? GROUP BY day",args)
        registered = store.all("SELECT strftime('%Y-%m-%d',created,'unixepoch','+8 hours') day,COUNT(*) n FROM users WHERE discord_id IS NOT NULL AND created>=? AND created<=? GROUP BY day",args)
        daily_map = {r['day']:r for r in grouped}
        new_map = {r['day']:r['n'] for r in registered}
        daily = []
        for i in range(days):
            day = (today-timedelta(days=days-1-i)).strftime('%Y-%m-%d')
            row = daily_map.get(day,{'day':day,'requests':0,'success':0})
            daily.append(row | {'failed':row['requests']-row['success'],'new_users':new_map.get(day,0)})
        pattern = '%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        where = "WHERE u.name LIKE ? ESCAPE '\\' OR u.id=?"
        total = store.one('SELECT COUNT(*) n FROM users u '+where,(pattern,query))['n']
        users = store.all('''WITH a AS (SELECT user_id,COUNT(*) accounts,SUM(enabled=1) enabled_accounts FROM accounts GROUP BY user_id),
          r AS (SELECT user_id,'''+METRICS+''' FROM requests WHERE created>=? AND created<=? GROUP BY user_id)
          SELECT u.id,u.name,u.role,u.disabled,u.created,
          COALESCE(a.accounts,0) accounts,COALESCE(a.enabled_accounts,0) enabled_accounts,
          COALESCE(r.requests,0) requests,COALESCE(r.success,0) success,COALESCE(r.generations,0) generations,
          COALESCE(r.input_tokens,0) input_tokens,COALESCE(r.output_tokens,0) output_tokens,
          COALESCE(r.metered_requests,0) metered_requests,r.last_request
          FROM users u LEFT JOIN a ON a.user_id=u.id LEFT JOIN r ON r.user_id=u.id '''+where+'''
          ORDER BY requests DESC,u.created DESC,u.id LIMIT ? OFFSET ?''',args+(pattern,query,limit,offset))
        models = store.all('SELECT model,'+METRICS+' FROM requests WHERE created>=? AND created<=? GROUP BY model ORDER BY requests DESC,model LIMIT 20',args)
        recent = store.all('''SELECT u.name,r.model,r.status,r.duration,r.first_token_seconds,r.input_tokens,r.output_tokens,r.created
          FROM requests r LEFT JOIN users u ON u.id=r.user_id WHERE r.created>=? AND r.created<=? ORDER BY r.created DESC,r.id DESC LIMIT 20''',args)
    return {'generated_at':now,'timezone':'Asia/Shanghai','days':days,'start':start,'end':now,
            'totals':totals,'summary':summary,'daily':daily,
            'users':{'total':total,'limit':limit,'offset':offset,'items':[finish(r) for r in users]},
            'models':[finish(r) for r in models],'recent':recent}
