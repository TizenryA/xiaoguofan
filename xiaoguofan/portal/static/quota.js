'use strict';
// Home page: big quota bars per account (reference budgets) plus recovery times from the engine.
(function(){
 const make=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined&&text!==null)e.textContent=text;if(cls)e.className=cls;return e};
 const node=id=>document.getElementById(id);
 const GROUPS=[['4a','4a · Gemini 4 Argon'],['3.8f','3.8f · Gemini 3.8 Flash']];
 const bj=t=>new Date(t*1000).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false});
 function until(t){const m=Math.max(1,Math.round((t-Date.now()/1000)/60));return m<60?m+' 分钟后':Math.floor(m/60)+' 小时 '+(m%60)+' 分钟后'}
 window.xgfUntil=until;window.xgfBeijing=bj;
 function groupOf(model){const m=(model||'').toLowerCase();if(!m)return 'all';if(m.includes('argon')||m==='4a'||m.startsWith('4a'))return '4a';if(m.includes('3.8')&&m.includes('flash')||m==='3.8f')return '3.8f';return 'other'}
 // meter({name,used,limit,cool,foot}) -> element. Thick rounded bar, colour by how full it is.
 window.xgfMeter=function(o){
  const pct=o.limit?Math.min(100,Math.round(o.used/o.limit*100)):0;
  const box=make('div',undefined,'meter'+(o.cool?' cool':pct>=100?' full':pct>=70?' warn':''));
  const head=make('div',undefined,'meter-head'),val=make('span',undefined,'meter-val');
  if(o.cool){val.textContent='额度用完'}else{val.append(make('b',String(o.used)),document.createTextNode(' / '+o.limit))}
  head.append(make('span',o.name,'meter-name'),val);
  const track=make('div',undefined,'meter-track'),fill=make('div',undefined,'meter-fill'+(pct===0&&!o.cool?' zero':''));
  fill.style.width=(o.cool?100:pct)+'%';track.append(fill);
  if(!o.cool)track.append(make('span',pct+'%','meter-pct'));
  track.setAttribute('role','progressbar');track.setAttribute('aria-valuemin','0');track.setAttribute('aria-valuemax',String(o.limit||0));track.setAttribute('aria-valuenow',String(o.used||0));track.setAttribute('aria-label',o.name);
  const foot=make('div',undefined,'meter-foot');foot.append(make('span',o.left),make('span',o.right));
  box.append(head,track,foot);return box;
 };
 window.renderReferenceQuota=function(q,accounts){
  const live={};for(const a of accounts||[])live[a.id]=a.live||{};
  const reset=q.reset_at?'北京时间 00:00 重置 · 还有 '+until(q.reset_at).replace('后',''):'北京时间 00:00 重置';
  node('reference-note').textContent=q.quota_note||'按本站成功次数估算，每天北京时间零点重新计算；不是 Google 的实时余额。';
  node('today-success').textContent=q.account_usage.reduce((n,a)=>n+a.today.success,0);
  node('reset-note').textContent=reset;
  node('unattributed-count').textContent=q.unattributed.success;
  // hero: totals across enabled accounts
  const hero=node('hero-bars');hero.replaceChildren();
  const enabled=q.account_usage.filter(a=>a.enabled);
  if(!enabled.length){hero.append(make('p','还没有启用的账号。去“我的账号”添加一个吧。','hero-sub'))}
  for(const [key,name] of GROUPS){const used=enabled.reduce((n,a)=>n+a.models[key].used,0),limit=enabled.reduce((n,a)=>n+a.models[key].limit,0);
   if(limit)hero.append(xgfMeter({name:name+' · 全部账号',used,limit,left:'今天还能用 '+Math.max(0,limit-used)+' 条',right:enabled.length+' 个账号合计'}))}
  // per-account cards
  const list=node('account-quota-list');list.replaceChildren();
  if(!q.account_usage.length){const e=make('div',undefined,'empty');e.append(make('p','还没有账号。导入一个自己的 Google 账号就能开始用。'));const b=make('button','去添加账号','btn primary');b.dataset.go='accounts';e.append(b);list.append(e)}
  for(const a of q.account_usage){
   const l=live[a.id]||{},cooling=l.cooling||[],card=make('article',undefined,'card q-card');
   const head=make('div',undefined,'q-head'),who=make('div');who.append(make('h3',a.label),make('small',a.email||'未填写邮箱'));
   const chip=make('span',undefined,'chip '+(window.xgfStateClass?.(l.state,a.enabled)||'idle'));chip.append(make('i',undefined,'dot'),document.createTextNode(a.enabled?(l.text||'正在准备'):'已停用'));
   head.append(who,chip);card.append(head);
   const wide=cooling.find(c=>groupOf(c.model)==='all');
   if(wide){const b=make('div',undefined,'cool-banner');b.textContent='⏳ 这个账号额度暂时用完，约 '+bj(wide.until)+'（'+until(wide.until)+'）恢复。';card.append(b)}
   for(const [key,name] of GROUPS){const m=a.models[key],c=wide||cooling.find(x=>groupOf(x.model)===key);
    card.append(xgfMeter({name,used:m.used,limit:m.limit,cool:Boolean(c),
     left:c?'约 '+bj(c.until)+' 恢复':m.remaining?'还剩 '+m.remaining+' 条':'今天用完了',right:c?until(c.until):'00:00 重置'}))}
   const sub=make('div',undefined,'q-sub');
   sub.append(make('span','套餐 '+(a.plan==='pro'?'PRO':'Free')));
   const s1=make('span');s1.append('今天成功 ',make('b',String(a.today.success)));sub.append(s1);
   if(a.today.failed){const s2=make('span');s2.append('失败 ',make('b',String(a.today.failed)));sub.append(s2)}
   const s3=make('span');s3.append('近 30 天 ',make('b',String(a.period.success)),' 次');sub.append(s3);
   card.append(sub);list.append(card);
  }
  // 7-day chart
  const chart=node('quota-chart');chart.replaceChildren();const max=Math.max(1,...q.trend.map(d=>d.success));
  for(const day of q.trend){const col=make('div',undefined,'chart-day'),stack=make('div',undefined,'chart-stack');
   const vals=[day['4a'],day['3.8f'],Math.max(0,day.success-day['4a']-day['3.8f'])];
   vals.forEach((v,i)=>{const s=make('span',undefined,'seg-'+i);s.style.height=(v/max*100)+'%';stack.append(s)});
   col.title=`${day.day}：成功 ${day.success}，失败 ${day.failed}`;
   col.append(make('b',String(day.success)),stack,make('span',day.day),make('span',day.failed?'失败 '+day.failed:'—',day.failed?'fail':''));chart.append(col)}
 };
})();
