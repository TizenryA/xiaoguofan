'use strict';
// Home page: how many times each account was used (real counts, no assumed limits),
// "used up" only when Google actually said so (engine cooldown), plus plan tier tags.
(function(){
 const make=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined&&text!==null)e.textContent=text;if(cls)e.className=cls;return e};
 const node=id=>document.getElementById(id);
 const GROUPS=[['4a','4a · Gemini 4 Argon'],['3.8f','3.8f · Gemini 3.8 Flash'],['other','其他模型']];
 const shown=(key,rows)=>key!=='other'||rows.some(m=>m&&(m.used||m.week));
 const bj=t=>new Date(t*1000).toLocaleTimeString((globalThis.XGF_I18N?.locale||'zh-CN'),{timeZone:(globalThis.XGF_I18N?globalThis.XGF_I18N.tz:'Asia/Shanghai'),hour:'2-digit',minute:'2-digit',hour12:false});
 function until(t){const m=Math.max(1,Math.round((t-Date.now()/1000)/60));return m<60?m+' 分钟后':Math.floor(m/60)+' 小时 '+(m%60)+' 分钟后'}
 window.xgfUntil=until;window.xgfBeijing=bj;
 function groupOf(model){const m=(model||'').toLowerCase();if(!m)return 'all';if(m.includes('argon')||m==='4a'||m.startsWith('4a'))return '4a';if(m.includes('3.8')&&m.includes('flash')||m==='3.8f')return '3.8f';return 'other'}
 const TIER_TIP={google:'Google 账号权益接口返回的套餐',models:'根据这个账号能用的付费模型推断（至少是这个套餐）','':'还没从 Google 读到套餐信息；账号载入模型列表后会自动识别'};
 window.xgfTierChip=function(t){t=t||{tier:'unknown',label:'未识别',source:''};const c=make('span',t.label,'chip tier tier-'+t.tier);c.title=TIER_TIP[t.source]??'';return c};
 // 7 little columns, scaled to this row's own busiest day (not to any quota).
 function spark(days,label){const box=make('div',undefined,'spark'),max=Math.max(1,...days);box.setAttribute('role','img');box.setAttribute('aria-label',label+'：'+days.join('、'));
  days.forEach((v,i)=>{const b=make('i',undefined,i===days.length-1?'today':'');b.style.height=Math.max(v?8:3,v/max*100)+'%';b.title=(i===days.length-1?'今天':(days.length-1-i)+' 天前')+' '+v+' 次';box.append(b)});return box}
 // usage({name,used,week,days,cool,limited}) -> element
 window.xgfUsage=function(o){
  const box=make('div',undefined,'usage'+(o.cool?' cool':''));
  const head=make('div',undefined,'usage-head'),val=make('span',undefined,'usage-val');
  val.append(make('b',String(o.used)),document.createTextNode(' 次'),make('small',' 今天','usage-day'));head.append(make('span',o.name,'usage-name'),val);
  box.append(head,spark(o.days||[],o.name+' 近 7 天每天成功次数'));
  if(o.cool){const b=make('div',undefined,'usage-cool');b.append(make('b','已用完'),document.createTextNode('，约 '+bj(o.cool.until)+' 恢复（'+until(o.cool.until)+'）'));box.append(b)}
  else{const f=make('div',undefined,'usage-foot');f.append(make('span','近 7 天 '+(o.week??0)+' 次'),make('span',o.limited?'今天 Google 说过 '+o.limited+' 次“用完”':''));box.append(f)}
  return box};
 window.renderReferenceQuota=function(q,accounts){
  const live={};for(const a of accounts||[])live[a.id]=a.live||{};
  node('reference-note').textContent=q.quota_note||'';
  node('today-success').textContent=q.account_usage.reduce((n,a)=>n+a.today.success,0);
  node('reset-note').textContent='北京时间 00:00 从零开始算 · 还有 '+(q.reset_at?until(q.reset_at).replace('后',''):'—');
  node('unattributed-count').textContent=q.unattributed.success;
  // hero: per model group, all enabled accounts together
  const hero=node('hero-bars');hero.replaceChildren();
  const enabled=q.account_usage.filter(a=>a.enabled);
  if(!enabled.length)hero.append(make('p','还没有启用的账号。去“我的账号”添加一个吧。','hero-sub'));
  else for(const [key,name] of GROUPS){if(!shown(key,enabled.map(a=>a.models[key])))continue;const sum=f=>enabled.reduce((n,a)=>n+(a.models[key][f]||0),0);
   const days=[0,1,2,3,4,5,6].map(i=>enabled.reduce((n,a)=>n+((a.models[key].days||[])[i]||0),0));
   const coolAll=enabled.every(a=>(live[a.id]?.cooling||[]).some(c=>['all',key].includes(groupOf(c.model))));
   const soonest=coolAll?Math.min(...enabled.flatMap(a=>(live[a.id]?.cooling||[]).filter(c=>['all',key].includes(groupOf(c.model))).map(c=>c.until))):0;
   hero.append(xgfUsage({name:name+' · 全部账号',used:sum('used'),week:sum('week'),days,limited:sum('limited'),cool:coolAll&&soonest?{until:soonest}:null}))}
  // per-account cards
  const list=node('account-quota-list');list.replaceChildren();
  if(!q.account_usage.length){const e=make('div',undefined,'empty');e.append(make('p','还没有账号。导入一个自己的 Google 账号就能开始用。'));const b=make('button','去添加账号','btn primary');b.dataset.go='accounts';e.append(b);list.append(e)}
  for(const a of q.account_usage){
   const l=live[a.id]||{},cooling=l.cooling||[],card=make('article',undefined,'card q-card');
   const head=make('div',undefined,'q-head'),who=make('div');const title=make('h3',a.label);title.append(xgfTierChip(l.plan_tier));who.append(title,make('small',a.email||'未填写邮箱'));
   const chip=make('span',undefined,'chip '+(window.xgfStateClass?.(l.state,a.enabled)||'idle'));chip.append(make('i',undefined,'dot'),document.createTextNode(a.enabled?(l.text||'正在准备'):'已停用'));
   head.append(who,chip);card.append(head);
   const wide=cooling.find(c=>groupOf(c.model)==='all');
   if(wide){const b=make('div',undefined,'cool-banner');b.textContent='⏳ Google 说这个账号额度用完了，约 '+bj(wide.until)+'（'+until(wide.until)+'）恢复。';card.append(b)}
   for(const [key,name] of GROUPS){const m=a.models[key];if(!m||!shown(key,[m]))continue;const c=wide||cooling.find(x=>groupOf(x.model)===key);
    card.append(xgfUsage({name,used:m.used,week:m.week,days:m.days,limited:m.limited,cool:c||null}))}
   const sub=make('div',undefined,'q-sub');
   const s1=make('span');s1.append('今天成功 ',make('b',String(a.today.success)));sub.append(s1);
   if(a.today.failed){const s2=make('span');s2.append('没成功 ',make('b',String(a.today.failed)));sub.append(s2)}
   const s3=make('span');s3.append('近 30 天 ',make('b',String(a.period.success)),' 次');sub.append(s3);
   card.append(sub);list.append(card);
  }
  // 7-day chart (collapsed)
  const chart=node('quota-chart');chart.replaceChildren();const max=Math.max(1,...q.trend.map(d=>d.success));
  for(const day of q.trend){const col=make('div',undefined,'chart-day'),stack=make('div',undefined,'chart-stack');
   const vals=[day['4a'],day['3.8f'],Math.max(0,day.success-day['4a']-day['3.8f'])];
   vals.forEach((v,i)=>{const s=make('span',undefined,'seg-'+i);s.style.height=(v/max*100)+'%';stack.append(s)});
   col.title=`${day.day}：成功 ${day.success}，失败 ${day.failed}`;
   col.append(make('b',String(day.success)),stack,make('span',day.day),make('span',day.failed?'失败 '+day.failed:'—',day.failed?'fail':''));chart.append(col)}
 };
})();
