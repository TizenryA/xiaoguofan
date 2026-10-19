'use strict';
let adminStatsOffset=0,adminStatsRequest=0;
const adminNumber=n=>Number(n||0).toLocaleString('zh-CN');
const adminDate=ts=>new Date(ts*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
const adminSeconds=n=>n===null||n===undefined?'未提供':Number(n).toFixed(2)+' 秒';
function adminMetric(title,value,note,id){const a=el('article');a.append(el('span',title));const strong=el('strong',value);if(id)strong.id=id;a.append(strong,el('small',note));return a}
function adminDataRow(title,details){const row=el('div',undefined,'admin-data-row'),head=el('h4',title),body=el('div',undefined,'admin-data-details');details.forEach(t=>body.append(el('span',t)));row.append(head,body);return row}
async function siteStats(){
 if(currentUser?.role!=='admin')return;
 const ticket=++adminStatsRequest,days=Number($('#admin-days').value),query=$('#admin-user-search').value.trim();
 $('#admin-stats-meta').textContent='正在更新统计…';
 try{
 const d=await api('/api/admin/stats?'+new URLSearchParams({days,limit:20,offset:adminStatsOffset,q:query}));if(ticket!==adminStatsRequest)return;
 const t=d.totals,s=d.summary;
 $('#admin-stats-meta').textContent=`近 ${d.days} 天 · 北京时间 · 更新于 ${new Date(d.generated_at*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}`;
 const metrics=$('#admin-stat-metrics');metrics.replaceChildren(
 adminMetric('累计注册用户',adminNumber(t.registered_users),`本期新增 ${adminNumber(t.new_users)} · 已禁用 ${adminNumber(t.disabled_users)}`,'admin-user-total'),
 adminMetric('当前接入账号',adminNumber(t.accounts),`启用 ${adminNumber(t.enabled_accounts)} · ${adminNumber(t.account_owners)} 位用户持有`),
 adminMetric('本期请求',adminNumber(s.requests),`生成调用 ${adminNumber(s.generations)} · 活跃用户 ${adminNumber(s.active_users)}`),
 adminMetric('请求成功率',s.success_rate===null?'—':s.success_rate+'%',`成功 ${adminNumber(s.success)} · 失败 ${adminNumber(s.failed)}`),
 adminMetric('已报告 Token',adminNumber(s.input_tokens+s.output_tokens),`输入 ${adminNumber(s.input_tokens)} · 输出 ${adminNumber(s.output_tokens)}`),
 adminMetric('平均请求耗时',adminSeconds(s.avg_duration),`平均首字 ${adminSeconds(s.avg_first_token_seconds)}`));
 metrics.append(el('p',`${adminNumber(s.metered_requests)} 次请求报告用量 · 当前有效密钥 ${adminNumber(t.active_keys)}`,'admin-metrics-note muted'));
 const trend=$('#admin-trend');trend.replaceChildren();const max=Math.max(1,...d.daily.map(x=>Math.max(x.requests,x.new_users)));const bars=el('div',undefined,'admin-trend-bars');
 for(const x of d.daily){const col=el('div',undefined,'admin-trend-col');col.title=`${x.day}：请求${x.requests}，成功${x.success}，失败${x.failed}，新增${x.new_users}`;col.append(el('small',`${x.requests} / ${x.new_users}`));const pair=el('div',undefined,'admin-trend-pair');for(const [n,cls] of [[x.requests,'admin-bar-requests'],[x.new_users,'admin-bar-users']]){const bar=el('div',undefined,cls);bar.style.height=(n/max*100)+'%';pair.append(bar)}col.append(pair,el('small',x.day.slice(5)));bars.append(col)}trend.append(bars);
 const list=$('#admin-user-rows');list.replaceChildren();if(!d.users.items.length)empty(list,'没有匹配的用户');
 d.users.items.forEach(u=>{const row=adminDataRow(u.name,[u.role==='admin'?'管理员':u.disabled?'已禁用':'正常',`账号 ${u.accounts} · 启用 ${u.enabled_accounts}`,`请求 ${u.requests} · 成功 ${u.success} · 失败 ${u.failed}`,`Token ${adminNumber(u.input_tokens+u.output_tokens)} · 已计量 ${u.metered_requests} 次`,`最近请求 ${u.last_request?adminDate(u.last_request):'暂无'}`]);row.append(el('small','站内 ID：'+u.id,'muted'));list.append(row)});
 $('#admin-prev').disabled=d.users.offset===0;$('#admin-next').disabled=d.users.offset+d.users.items.length>=d.users.total;
 $('#admin-page-info').textContent=d.users.total?`${d.users.offset+1}–${d.users.offset+d.users.items.length} / ${d.users.total}`:'0 / 0';
 const models=$('#admin-model-rows');models.replaceChildren();if(!d.models.length)empty(models,'本期暂无模型请求');d.models.forEach(m=>models.append(adminDataRow(m.model||'模型列表 / 其他请求',[`请求 ${m.requests}`,`成功 ${m.success} · 失败 ${m.failed}`,`Token ${adminNumber(m.input_tokens+m.output_tokens)}`,`平均 ${adminSeconds(m.avg_duration)}`])));
 const recent=$('#admin-recent-rows');recent.replaceChildren();if(!d.recent.length)empty(recent,'本期暂无请求记录');d.recent.forEach(r=>recent.append(adminDataRow(r.model||'模型列表 / 其他请求',[r.name||'已删除用户',`HTTP ${r.status}`,adminSeconds(r.duration),`Token ${r.input_tokens===null&&r.output_tokens===null?'未提供':adminNumber(r.input_tokens+r.output_tokens)}`,adminDate(r.created)])));
 }catch(e){if(ticket===adminStatsRequest)$('#admin-stats-meta').textContent='更新失败：'+e.message;throw e}
}
$('#admin-days').onchange=()=>{adminStatsOffset=0;siteStats().catch(e=>notify(e.message))};
$('#admin-stats-refresh').onclick=()=>task($('#admin-stats-refresh'),siteStats);
$('#admin-search-form').onsubmit=e=>{e.preventDefault();adminStatsOffset=0;task(e.submitter,siteStats)};
$('#admin-prev').onclick=()=>{adminStatsOffset=Math.max(0,adminStatsOffset-20);siteStats().catch(e=>notify(e.message))};
$('#admin-next').onclick=()=>{adminStatsOffset+=20;siteStats().catch(e=>notify(e.message))};
