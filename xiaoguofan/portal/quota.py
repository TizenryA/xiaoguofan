"""Local reference budgets, not Google balance or enforced quotas."""
import time
from datetime import datetime, timedelta, timezone

CST=timezone(timedelta(hours=8))


def day_start(now):
    return int((now+28800)//86400)*86400-28800


def model_group(model):
    name=model.removeprefix('[防截断]').removeprefix('models/')
    if name in {'4a','gemini-4-argon'}:return '4a'
    if name in {'3.8f','gemini-3.8-flash'}:return '3.8f'
    return 'other'


def reference_quota(store,uid):
    now=time.time();start=day_start(now)
    rows=store.all('SELECT account_id,model,status,generation,created FROM requests WHERE user_id=? AND created>=?',(uid,now-30*86400))
    accounts=store.accounts(uid)
    result=[]
    for a in accounts:
        own=[r for r in rows if r['account_id']==a['id'] and r['generation']==1]
        today=[r for r in own if r['created']>=start]
        success=[r for r in today if 200<=r['status']<300]
        models={}
        for group,limit in [('4a',50 if a['plan']=='pro' else 2),('3.8f',100)]:
            used=sum(model_group(r['model'])==group for r in success)
            models[group]={'used':used,'limit':limit,'remaining':max(0,limit-used)}
        result.append({k:a[k] for k in ['id','label','email','plan','enabled']}|{
            'today':{'attempts':len(today),'success':len(success),'failed':sum(r['status']>=400 for r in today)},
            'period':{'attempts':len(own),'success':sum(200<=r['status']<300 for r in own)},'models':models})
    generation=[r for r in rows if r['generation']==1 or (r['generation'] is None and r['model'])]
    trend=[]
    for i in range(6,-1,-1):
        lo=start-i*86400;daily=[r for r in generation if lo<=r['created']<lo+86400]
        trend.append({'day':datetime.fromtimestamp(lo,CST).strftime('%m-%d'),'success':sum(200<=r['status']<300 for r in daily),'failed':sum(r['status']>=400 for r in daily),
                      '4a':sum(200<=r['status']<300 and model_group(r['model'])=='4a' for r in daily),
                      '3.8f':sum(200<=r['status']<300 and model_group(r['model'])=='3.8f' for r in daily)})
    unknown=[r for r in generation if not r['account_id']]
    return {'reference_only':True,'timezone':'Asia/Shanghai','reset_at':start+86400,'period_days':30,
            'account_usage':result,'trend':trend,'unattributed':{'attempts':len(unknown),'success':sum(200<=r['status']<300 for r in unknown)},
            'quota_note':'站内参考额度，每日北京时间 00:00 重置；仅成功生成计数，不是 Google 实际余额，不拦截调用。Free / PRO 由本人标注；站外调用不计入。'}


def choose_account(accounts,store,uid,model=''):
    """Keep a warm account until reference budget or an error suggests another own account.

    Reference limits only influence selection; they never deny a request.
    """
    if not accounts:return None
    previous=store.one('SELECT account_id,status FROM requests WHERE user_id=? AND generation=1 AND account_id IS NOT NULL ORDER BY id DESC LIMIT 1',(uid,))
    ids=[a['id'] for a in accounts]
    index=ids.index(previous['account_id']) if previous and previous['account_id'] in ids else 0
    group=model_group(model)
    if previous and previous['status']>=400:
        index=(index+1)%len(ids)
    if group!='other':
        counts=store.all('SELECT account_id,model,COUNT(*) n FROM requests WHERE user_id=? AND generation=1 AND status>=200 AND status<300 AND created>=? GROUP BY account_id,model',(uid,day_start(time.time())))
        used={aid:sum(row['n'] for row in counts if row['account_id']==aid and model_group(row['model'])==group) for aid in ids}
        for offset in range(len(accounts)):
            candidate=accounts[(index+offset)%len(accounts)]
            limit=100 if group=='3.8f' else 50 if candidate.get('plan')=='pro' else 2
            if used[candidate['id']]<limit:return candidate
    return accounts[index]
