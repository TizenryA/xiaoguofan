'use strict';
// Admin realtime dashboard: engine + admission snapshot, alerts, account x model health matrix.
(function(){
 let on=false,timer=null,inflight=false,epoch=0,matrixAt=0;
 const $1=s=>document.querySelector(s);
 const make=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined&&text!==null)e.textContent=text;if(cls)e.className=cls;return e};
 const mib=b=>b==null?'—':b>=1073741824?(b/1073741824).toFixed(1)+' GiB':(b/1048576).toFixed(0)+' MiB';
 const time=t=>t?new Date(t*1000).toLocaleString((globalThis.XGF_I18N?.locale||'zh-CN'),{hour12:false}):'—';
 const ago=t=>{if(!t)return '—';const s=Math.max(0,Date.now()/1000-t);return s<60?Math.round(s)+' 秒前':s<3600?Math.round(s/60)+' 分钟前':s<86400?Math.round(s/3600)+' 小时前':Math.round(s/86400)+' 天前'};
 const STATE={ready:'可用',busy:'处理中',cooldown:'额度用完（会恢复）',auth_required:'登录失效',restricted:'被 Google 限制',unavailable:'暂时连不上',disabled:'已停用',pending:'准备中',error:'载入失败',conflict:'邮箱冲突'};
 const STATE_CLASS={ready:'good',busy:'good',cooldown:'warn',unavailable:'warn',pending:'idle',disabled:'idle',auth_required:'bad',restricted:'bad',error:'bad',conflict:'bad'};
 const LEVEL={critical:'严重',warning:'注意',info:'提示'};

 function renderLive(d){
  const e=d.engine||{},a=d.admission||{},t=d.traffic||{},load=d.load||{},m=(load.memory)||{},cpu=load.cpu||{},eng=load.engine||{};
  const up=e.ok!==false&&eng.running!==false;
  $1('#live-engine-card').classList.toggle('is-bad',!up);
  $1('#live-engine').textContent=e.ok===null||e.ok===undefined?'检查中':up?'在线':'不可用';
  $1('#live-engine-note').textContent=(e.message||'')+(e.checked?' 检查于 '+ago(e.checked):'')+(eng.memory_bytes?' · 占用 '+mib(eng.memory_bytes):'');
  $1('#live-active').textContent=(a.active??'—')+' / '+(a.limit??'—');
  $1('#live-active-note').textContent='每人最多 '+(a.per_user??'—')+' 条 · 峰值 '+(a.peak??'—');
  $1('#live-queued').textContent=String(a.queued??'—');
  $1('#live-queued-note').textContent=a.queued?a.queued_users+' 位用户在等':'没有人在排队';
  $1('#live-traffic').textContent=(t.n??0)+' 次';
  $1('#live-traffic-note').textContent=t.n?`成功 ${t.ok} · 额度用完 ${t.limited} · 失败 ${t.failed} · 取消 ${t.cancelled} · ${t.users} 人 · 平均开始回复 ${t.ttft??'—'} 秒`:'最近 5 分钟没有对话';
  $1('#live-memory').textContent=mib(m.used_bytes)+' / '+mib(m.limit_bytes);
  const bar=$1('#live-memory-bar');if(m.used_bytes!=null&&m.limit_bytes){bar.max=m.limit_bytes;bar.value=m.used_bytes}else bar.removeAttribute('value');
  $1('#live-memory-note').textContent=load.available===false||!d.load?'取不到采样（只在服务器上可用）':m.used_bytes>=m.high_bytes?'接近上限，系统会开始回收内存':'正常';
  $1('#live-cpu').textContent=cpu.percent==null?'—':cpu.percent.toFixed(1)+'%';
  $1('#live-cpu-note').textContent=cpu.cores?cpu.cores+' 核整机归一化':'';
  const acc=d.accounts||{},box=$1('#live-states');box.replaceChildren();
  $1('#live-accounts-note').textContent=`已保存 ${acc.saved??'—'} 个 · 启用 ${acc.enabled??'—'} 个 · 已载入 ${acc.loaded??'—'} 个 · 正在处理 ${acc.active_requests??0} 个请求`;
  for(const [k,n] of Object.entries(acc.by_state||{}).sort((x,y)=>y[1]-x[1])){const c=make('span',undefined,'state-chip '+(STATE_CLASS[k]||'idle'));c.append(make('strong',String(n)),make('span',STATE[k]||k));box.append(c)}
  if(!box.children.length)box.append(make('p','还没有载入任何账号','muted'));
  $1('#live-updated').textContent='更新于 '+new Date((d.timestamp||Date.now()/1000)*1000).toLocaleTimeString((globalThis.XGF_I18N?.locale||'zh-CN'),{hour12:false})+' · 每 5 秒刷新'+(d.alerts_open?' · '+d.alerts_open+' 条告警待处理':'');
 }

 async function renderAlerts(){
  const d=await api('/api/admin/alerts'),list=$1('#alert-list'),history=$1('#alerts-history').checked;
  const rows=history?d.recent:d.open;list.replaceChildren();
  if(!rows.length){list.append(make('p',history?'还没有告警记录':'没有待处理的告警 ✓','muted'));return}
  for(const r of rows){
   const item=make('div',undefined,'alert-item level-'+r.level+(r.resolved?' resolved':''));
   const info=make('div');info.append(make('strong',(LEVEL[r.level]||r.level)+' · '+r.message));
   info.append(make('small',`${r.subject?r.subject+' · ':''}首次 ${time(r.created)} · 最近 ${ago(r.updated)}${r.count>1?' · 共 '+r.count+' 次':''}${r.resolved?' · 已处理':''}`));
   item.append(info);
   if(!r.resolved){const b=make('button','标记已处理','text');b.onclick=()=>task(b,async()=>{await api('/api/admin/alerts/'+r.id+'/dismiss',{method:'POST'});await renderAlerts()});item.append(b)}
   list.append(item)}
 }

 async function renderMatrix(){
  const hours=$1('#matrix-hours').value,used=$1('#matrix-used').checked;
  const d=await api(`/api/admin/health-matrix?hours=${hours}&used=${used}`);
  const table=$1('#health-matrix'),head=table.tHead,body=table.tBodies[0];
  head.replaceChildren();body.replaceChildren();
  $1('#matrix-empty').hidden=d.rows.length>0;
  const hr=make('tr');hr.append(make('th','账号'),make('th','状态'));
  for(const m of d.models)hr.append(make('th',m,'model-col'));head.append(hr);
  for(const r of d.rows){
   const tr=make('tr'),who=make('td',undefined,'who');
   who.append(make('strong',r.label),make('small',`${r.owner} · ${r.email||'未载入'}${r.tier?' · '+r.tier:''}`));
   const st=make('td',undefined,'state');st.append(make('span',STATE[r.state]||r.state_text,'state-chip '+(STATE_CLASS[r.state]||'idle')));
   if(r.active)st.append(make('small',r.active+' 个请求中'));
   tr.append(who,st);
   for(const m of d.models){
    const c=r.cells[m],td=make('td',undefined,'cell '+(c?c.grade:'idle'));
    if(c){td.textContent=`${c.ok}/${c.n}`;td.title=`${m}\n成功 ${c.ok} / 共 ${c.n}\n额度用完 ${c.limited} · 失败 ${c.failed}\n平均开始回复 ${c.ttft??'—'} 秒`}
    else td.textContent='·';
    tr.append(td)}
   body.append(tr)}
  matrixAt=Date.now();
 }

 async function refresh(full){
  if(!on||document.hidden||inflight)return;
  const ticket=epoch;inflight=true;
  try{
   const d=await api('/api/admin/live');if(ticket!==epoch)return;renderLive(d);
   if(full||Date.now()-matrixAt>60000)await Promise.all([renderAlerts(),renderMatrix()]);
   else if(d.alerts_open!==undefined&&d.alerts_open!==$1('#alert-list').querySelectorAll('.alert-item:not(.resolved)').length&&!$1('#alerts-history').checked)await renderAlerts();
  }catch(e){if(ticket===epoch)$1('#live-updated').textContent='刷新失败：'+e.message}
  finally{inflight=false;clearTimeout(timer);if(on&&!document.hidden)timer=setTimeout(()=>refresh(false),5000)}
 }

 window.adminLiveView=function(show){on=show;epoch++;clearTimeout(timer);if(show)refresh(true)};
 document.addEventListener('visibilitychange',()=>{epoch++;clearTimeout(timer);if(on&&!document.hidden)refresh(false)});
 document.addEventListener('DOMContentLoaded',()=>{
  $1('#matrix-hours').onchange=()=>task($1('#matrix-hours'),renderMatrix);
  $1('#matrix-used').onchange=()=>task($1('#matrix-used'),renderMatrix);
  $1('#alerts-history').onchange=()=>task($1('#alerts-history'),renderAlerts);
  $1('#live-sync').onclick=()=>task($1('#live-sync'),async()=>{const r=await api('/api/admin/engine/sync',{method:'POST'});notify(r.message);await refresh(true)});
  $1('#live-restart').onclick=()=>task($1('#live-restart'),async()=>{if(!await confirmAction('重启生成服务？正在进行的对话会中断，大约需要 1 分钟恢复。'))return;notify('正在重启，请稍等…');const r=await api('/api/admin/engine/restart',{method:'POST'});notify(r.message);await refresh(true)});
 });
})();
