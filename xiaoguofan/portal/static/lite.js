'use strict';
const portalBase=document.documentElement.dataset.portalBase||'';
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
let csrf='',offset=0,total=0,editId='',toastTimer,viewGeneration=0,recordFilter='',summaryRange='today',lastSummary=null;
const titles={home:'首页',records:'记录',accounts:'我的账号',access:'接入',guide:'账号导入教程'};
const aliases={quota:'home',upload:'accounts',load:'home'};
function el(tag,text,cls){const e=document.createElement(tag);if(text!==undefined&&text!==null)e.textContent=text;if(cls)e.className=cls;return e}
function notify(t){const modal=document.querySelector('dialog[open]');if(modal){let box=modal.querySelector('[data-operation-error]');if(!box){box=el('p','');box.dataset.operationError='';box.setAttribute('role','alert');(modal.querySelector('form')||modal).prepend(box)}box.textContent=t;box.hidden=false;return}$('#toast').textContent=t;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,5000)}
async function api(path,opt={}){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),path.endsWith('/check')?420000:120000);try{const r=await fetch(portalBase+path,{...opt,signal:controller.signal,credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,...opt.headers}});const j=await r.json().catch(()=>({detail:'服务器返回异常'}));if(!r.ok)throw Error(typeof j.detail==='string'?j.detail:j.error?.message||'输入格式不对');return j}catch(e){if(e.name==='AbortError')throw Error('服务器响应超时，操作可能没有完成；请先刷新页面再看，不要重复提交');if(e instanceof TypeError)throw Error('连接服务器失败，请检查网络后刷新页面；操作可能没有完成');throw e}finally{clearTimeout(timer)}}
async function task(button,fn){if(button&&button.disabled)return;if(button)button.disabled=true;try{await fn()}catch(e){notify(e.message)}finally{if(button)button.disabled=false}}
function ago(t){const s=Date.now()/1000-t;if(s<60)return '刚刚';if(s<3600)return Math.floor(s/60)+' 分钟前';if(s<86400)return Math.floor(s/3600)+' 小时前';return new Date(t*1000).toLocaleDateString('zh-CN',{month:'numeric',day:'numeric'})+' '+new Date(t*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false})}
function full(t){return new Date(t*1000).toLocaleString('zh-CN',{hour12:false})}
function confirmAction(message){return new Promise(resolve=>{const d=$('#confirm-dialog');$('#confirm-message').textContent=message;d.showModal();$('#confirm-yes').onclick=()=>{d.close();resolve(true)};$('#confirm-no').onclick=()=>{d.close();resolve(false)};d.oncancel=()=>resolve(false)})}
const STATE_CLASS={ready:'ok',busy:'ok',cooldown:'warn',unavailable:'warn',pending:'idle',disabled:'idle',auth_required:'bad',restricted:'bad',error:'bad',conflict:'bad'};
window.xgfStateClass=(state,enabled)=>enabled===false?'idle':STATE_CLASS[state]||'idle';

async function go(tab){tab=aliases[tab]||tab;if(!titles[tab])tab='home';window.homeStatusView?.(tab==='home');closeProfile();const gen=++viewGeneration;
 $$('[data-page]').forEach(e=>e.hidden=e.dataset.page!==tab);$$('[data-tab]').forEach(e=>{e.classList.toggle('active',e.dataset.tab===tab);e.setAttribute('aria-current',e.dataset.tab===tab?'page':'false')});
 $('#page-title').textContent=titles[tab];history.replaceState(null,'','#'+tab);window.scrollTo({top:0});
 try{if(tab==='home')await quota();if(tab==='records')await records();if(tab==='accounts')await accounts();if(tab==='access')await Promise.all([keys(),preferences()])}catch(e){if(gen===viewGeneration)notify(e.message)}}

/* ---------- home ---------- */
async function quota(){const [q,accs]=await Promise.all([api('/api/quota'),api('/api/accounts')]);window.renderReferenceQuota(q,accs);
 $('#google-remaining').textContent=q.google_remaining==null?'未提供':String(q.google_remaining);$('#google-note').textContent=q.google_note||'';
 $('#quota-accounts').textContent=q.accounts.used+' / '+q.accounts.limit;$('#enabled-count').textContent=q.accounts.enabled+' 个已启用';
 $('#quota-rate').textContent=q.concurrency?String(q.concurrency.per_user):'—';$('#quota-rate-note').textContent=q.requests_per_minute?`每分钟最多 ${q.requests_per_minute} 次`:'不限每分钟次数';
 const s=q.summary;$('#quota-tokens').textContent=s.metered_requests?(s.input_tokens+s.output_tokens).toLocaleString():'未统计';$('#quota-meter').textContent=`近 30 天 ${s.requests} 次请求`}

/* ---------- records ---------- */
const STATUS={ok:['成功','ok'],limited:['额度用完','limited'],cancelled:['已取消','cancelled'],failed:['失败','failed']};
function kind(code){return code<400?'ok':code===429?'limited':code===499?'cancelled':'failed'}
function statusLabel(code){if(code<400)return '成功';return ({499:'已取消',429:'额度用完',401:'密钥无效',403:'被拒绝',400:'请求有误',413:'内容太大',502:'暂时失败',503:'暂时失败',504:'超时'})[code]||('失败 '+code)}
function ttftClass(v){return v==null?'':v<2?'ttft-good':v<6?'ttft-mid':'ttft-slow'}
function fmt(n){return n==null?'—':n>=10000?(n/10000).toFixed(n>=100000?0:1)+' 万':n.toLocaleString()}
function renderStats(s){const box=$('#record-stats');box.replaceChildren();if(!s)return;
 const tile=(label,value,unit,note,cls,extra)=>{const t=el('div',undefined,'stat '+(cls||''));const b=el('b',value);if(unit)b.append(el('small',unit));t.append(el('span',label),b);if(note)t.append(el('em',note));if(extra)t.append(extra);box.append(t)};
 const rate=s.success_rate;const bar=el('div',undefined,'rate-track');const fill=el('i');fill.style.width=(rate??0)+'%';bar.append(fill);
 tile('对话请求',String(s.n),'次',s.n?`成功 ${s.ok} 次`:'还没有请求');
 tile('成功率',rate==null?'—':String(rate),rate==null?'':'%','取消的不算',rate==null?'':rate>=90?'ok':rate>=60?'warn':'bad',bar);
 tile('平均开始回复',s.ttft==null?'—':String(s.ttft),s.ttft==null?'':'秒','从发出到第一个字',s.ttft==null?'':s.ttft<2?'ok':s.ttft<6?'warn':'bad');
 tile('用量（Token）',s.metered?fmt(s.input_tokens+s.output_tokens):'—','',s.metered?`输入 ${fmt(s.input_tokens)} · 输出 ${fmt(s.output_tokens)}`:'聊天软件没报告用量');
 tile('额度用完',String(s.limited),'次',s.limited?'会自动换你的其他账号':'没有遇到',s.limited?'warn':'');
 tile('失败',String(s.failed),'次',s.cancelled?`另有 ${s.cancelled} 次取消`:'不含取消',s.failed?'bad':'')}
async function records(){const list=$('#records-list');if(!list.children.length)list.replaceChildren(el('p','正在读取…','hint'));
 const d=await api(`/api/records?limit=20&offset=${offset}${recordFilter?'&status='+recordFilter:''}`);total=d.total;lastSummary=d.summary;renderStats(d.summary?.[summaryRange]);
 $('#page-info').textContent=total?`${offset+1}–${Math.min(offset+20,total)} / 共 ${total} 条`:'';$('#prev').disabled=offset===0;$('#next').disabled=offset+20>=total;
 if(!d.items.length){const e=el('div',undefined,'empty');e.append(el('p',recordFilter?'这一类还没有记录':'还没有记录。在聊天软件里发一句话，这里就会出现。'));list.replaceChildren(e);return}
 list.replaceChildren();for(const r of d.items){const k=kind(r.status),row=el('article',undefined,'rec '+k);
  const pill=el('span',undefined,'chip rec-status '+({ok:'ok',limited:'warn',failed:'bad',cancelled:'idle'})[k]);pill.append(el('i',undefined,'dot'),document.createTextNode(statusLabel(r.status)));pill.title='状态码 '+r.status;
  const main=el('div',undefined,'rec-main');main.append(el('strong',r.model||'查询模型列表'),el('small',(r.account_label||(r.account_id?'账号已删除':'未标账号'))+(r.generation===0?' · 查询':'')));
  const nums=el('div',undefined,'rec-nums');
  const n1=el('span');n1.append('开始回复 ',el('b',r.first_token_seconds==null?'—':r.first_token_seconds.toFixed(1)+' 秒',ttftClass(r.first_token_seconds)));
  const n2=el('span');n2.append('用时 ',el('b',r.duration.toFixed(1)+' 秒'));
  const n3=el('span');n3.append('Token ',el('b',r.input_tokens==null&&r.output_tokens==null?'—':`${fmt(r.input_tokens)} → ${fmt(r.output_tokens)}`));
  nums.append(n1,n2,n3);const time=el('span',ago(r.created),'rec-time');time.title=full(r.created);
  row.append(pill,main,nums,time);list.append(row)}}

/* ---------- accounts ---------- */
async function accounts(){const rows=await api('/api/accounts'),list=$('#account-list');window.xgfAccountCount=rows.length;
 if(!rows.length){const e=el('div',undefined,'empty');e.append(el('p','还没有账号。跟着下面的步骤导入一个，大约 3 分钟。'));list.replaceChildren(e);return rows}
 list.replaceChildren();for(const a of rows){const l=a.live||{},card=el('article',undefined,'card acc');
  const head=el('div',undefined,'acc-head'),who=el('div');who.append(el('h3',a.label),el('small',a.email||'还没填 Google 邮箱'));
  const plan=el('span',a.plan==='pro'?'PRO':'Free','chip soft');head.append(who,plan);
  const st=el('div',undefined,'acc-state');const chip=el('span',undefined,'chip '+xgfStateClass(l.state,a.enabled));chip.append(el('i',undefined,'dot'),document.createTextNode(l.text||(a.enabled?'正在准备':'已停用')));st.append(chip);
  if(l.recover_at)st.append(el('span','约 '+xgfBeijing(l.recover_at)+' 恢复（'+xgfUntil(l.recover_at)+'）','chip warn'));
  const actions=el('div',undefined,'acc-actions');
  const check=el('button','检查','btn tonal sm');check.onclick=()=>task(check,async()=>{notify('正在检查，稍等片刻');const r=await api('/api/accounts/'+a.id+'/check',{method:'POST'});notify(r.message);await accounts()});
  const edit=el('button','资料','btn ghost sm');edit.onclick=()=>{editId=a.id;$('#edit-dialog [data-operation-error]')?.remove();$('#edit-form [name=label]').value=a.label;$('#edit-form [name=email]').value=a.email||'';$('#edit-form [name=plan]').value=a.plan;$('#edit-dialog').showModal()};
  const more=el('details');const sum=el('summary','更多','btn ghost sm');const menu=el('div',undefined,'more-menu');
  const toggle=el('button',a.enabled?'暂停使用':'恢复使用','btn ghost sm');toggle.onclick=()=>task(toggle,async()=>{await api('/api/accounts/'+a.id,{method:'PATCH',body:JSON.stringify({enabled:!a.enabled})});await accounts()});
  const del=el('button','删除账号','btn text-danger sm');del.onclick=()=>task(del,async()=>{if(await confirmAction(`删除“${a.label}”和保存的登录信息？删除后不能恢复。`)){await api('/api/accounts/'+a.id,{method:'DELETE'});await accounts()}});
  menu.append(toggle,del);more.append(sum,menu);actions.append(check,edit,more);
  card.append(head,st,actions);list.append(card)}
 return rows}

/* ---------- import wizard ---------- */
const wizard={step:1,method:'',startCount:0,label:''};
function showStep(n){wizard.step=n;$$('[data-step-panel]').forEach(p=>p.hidden=Number(p.dataset.stepPanel)!==n);
 $$('#stepper li').forEach(li=>{const s=Number(li.dataset.step);li.classList.toggle('on',s===n&&n<4);li.classList.toggle('done',s<n||n===4)});
 $$('[data-method-panel]').forEach(p=>p.hidden=p.dataset.methodPanel!==wizard.method);
 if(n>1)$('#add-account').scrollIntoView({behavior:'smooth',block:'start'})}
$$('.choice').forEach(c=>c.onclick=()=>{wizard.method=c.dataset.method;$$('.choice').forEach(x=>x.classList.toggle('on',x===c));wizard.startCount=window.xgfAccountCount||0;showStep(2)});
$$('[data-next]').forEach(b=>b.onclick=()=>showStep(wizard.step+1));
$$('[data-back]').forEach(b=>b.onclick=()=>showStep(Math.max(1,wizard.step-1)));
$('#wizard-again').onclick=()=>{wizard.method='';$$('.choice').forEach(x=>x.classList.remove('on'));showStep(1)};
async function finishImport(label){wizard.label=label||'';showStep(4);$('#done-title').textContent=label?`“${label}”已保存`:'账号已保存';
 const rows=await accounts().catch(()=>[]);const watch=async(left)=>{const fresh=await api('/api/accounts').catch(()=>null);if(!fresh)return;const a=fresh.find(x=>x.label===wizard.label)||fresh[fresh.length-1];const live=a?.live||{};
  const chip=$('#done-live');chip.className='chip '+xgfStateClass(live.state,a?.enabled);chip.textContent=live.text||'正在准备…';
  if(live.state==='ready'||live.state==='busy'){$('#done-note').textContent='已经可以用了。去“接入”复制地址和密钥，填到聊天软件里。';return}
  if(['auth_required','restricted','error','conflict'].includes(live.state)){$('#done-note').textContent='保存了，但这个账号现在用不了：'+(live.text||'')+'。可以换一个账号重新导入。';return}
  if(left>0&&wizard.step===4)setTimeout(()=>watch(left-1),5000)};watch(12);return rows}
window.onAccountImported=info=>finishImport(info?.label);
const EMAIL=/^[^\s@]+@[^\s@]+\.[^\s@]+$/;const REQUIRED=['SAPISID','__Secure-1PAPISID','__Secure-3PAPISID'];
let parsedFile=null;
function checkEmail(){const f=$('#upload-form'),input=f.elements.email,v=input.value.trim(),msg=f.querySelector('[data-check=email]');
 if(!v){input.classList.remove('is-bad','is-ok');msg.textContent='';msg.className='check';return false}
 const ok=EMAIL.test(v);input.classList.toggle('is-bad',!ok);input.classList.toggle('is-ok',ok);msg.textContent=ok?'✓ 邮箱格式没问题':'邮箱格式不对，应该像 me@gmail.com';msg.className='check '+(ok?'ok':'bad');
 if(ok&&parsedFile?.email&&parsedFile.email.toLowerCase()!==v.toLowerCase()){msg.textContent=`文件里的账号是 ${parsedFile.email}，和这里填的不一样`;msg.className='check bad';input.classList.add('is-bad');return false}
 return ok}
function renderChecks(box,items){box.replaceChildren();for(const [cls,text] of items)box.append(el('li',text,cls));box.hidden=!items.length}
async function checkFile(){const f=$('#upload-form'),file=f.elements.file.files[0],box=$('#file-checks'),zone=f.querySelector('.file-zone');parsedFile=null;
 $('#file-name').textContent=file?file.name:'还没有选择文件';zone.classList.toggle('has-file',Boolean(file));if(!file){box.hidden=true;return false}
 const items=[];let ok=true;
 if(file.size>1024*1024){items.push(['bad','文件超过 1MB，不像登录文件']);renderChecks(box,items);return false}
 let j;try{j=JSON.parse(await file.text())}catch{renderChecks(box,[['bad','打不开：这不是有效的 JSON 文件。请重新导出，不要用记事本改过']]);return false}
 if(!j||typeof j!=='object'||Array.isArray(j)){renderChecks(box,[['bad','文件内容格式不对，请用导出脚本重新导出']]);return false}
 const state=j.storage_state||j,cookies=Array.isArray(state.cookies)?state.cookies:null;
 if(!cookies){items.push(['bad','文件里没有登录信息（缺少 cookies），请重新导出']);ok=false}
 else{items.push(['',`读到 ${cookies.length} 条登录信息`]);const names=new Set(cookies.map(c=>c&&c.name));const miss=REQUIRED.filter(n=>!names.has(n));
  if(miss.length){items.push(['bad','缺少必要的登录项：'+miss.join('、')+'。请确认已登录 Google 后重新导出']);ok=false}else items.push(['','必要的登录项都在']);
  const now=Date.now()/1000,exp=cookies.filter(c=>REQUIRED.includes(c?.name)).map(c=>Number(c.expires??c.expirationDate??-1)).filter(x=>x>0);
  if(exp.length&&Math.min(...exp)<now){items.push(['bad','登录信息已经过期，请在浏览器重新登录后再导出']);ok=false}}
 const email=j.email||state.email;if(email){parsedFile={email:String(email)};const input=f.elements.email;if(!input.value.trim()){input.value=email;items.push(['','已自动填入文件里的邮箱'])}}
 renderChecks(box,items);checkEmail();return ok}
function refreshUploadButton(){const f=$('#upload-form');const ready=EMAIL.test(f.elements.email.value.trim())&&f.elements.label.value.trim()&&f.elements.file.files[0]&&!$('#file-checks').querySelector('.bad')&&f.elements.consent.checked&&!f.querySelector('[data-check=email].bad');$('#upload-submit').disabled=!ready}
$('#upload-form').addEventListener('input',e=>{if(e.target.name==='email')checkEmail();$('#upload-error').hidden=true;refreshUploadButton()});
$('#upload-form').elements.file.addEventListener('change',async()=>{await checkFile();refreshUploadButton()});
$('#upload-form').onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{const f=e.target.elements,err=$('#upload-error');err.hidden=true;
 try{if(!(await checkFile())||!checkEmail())throw Error('请先按上面的提示修好再上传');const j=JSON.parse(await f.file.files[0].text());
  const p={email:f.email.value.trim(),label:f.label.value.trim(),storage_state:j.storage_state||j,locale:j.locale||'en-US',timezone:j.timezone||'America/Los_Angeles'};if(j.fingerprint)p.fingerprint=j.fingerprint;
  await api('/api/accounts/import',{method:'POST',body:JSON.stringify(p)});const label=p.label;e.target.reset();parsedFile=null;$('#file-checks').hidden=true;$('#file-name').textContent='还没有选择文件';e.target.querySelector('.file-zone').classList.remove('has-file');checkEmail();refreshUploadButton();await finishImport(label)}
 catch(x){err.textContent=x.message;err.hidden=false}})};
$('#native-cookie').addEventListener('input',e=>{const v=e.target.value.trim(),items=[];if(!v){$('#cookie-checks').hidden=true;return}
 if(/^https?:\/\//i.test(v))items.push(['bad','这是网址，不是 Cookie。请在 Via 菜单里用“复制 Cookie”']);
 else{if(!v.includes('='))items.push(['bad','看起来不是 Cookie（应该是一串 名字=值; 名字=值）']);for(const n of REQUIRED)items.push(v.includes(n+'=')?['','包含 '+n]:['bad','缺少 '+n+'，可能没登录或复制的不是 AI Studio 页面'])}
 renderChecks($('#cookie-checks'),items)});
$('#ext-check').onclick=()=>task($('#ext-check'),async()=>{const rows=await accounts();if(rows.length>wizard.startCount){await finishImport(rows[rows.length-1]?.label)}else $('#ext-status').textContent='还没看到新账号。确认在扩展里点了“确认上传”，并且当时打开的是本站页面，然后再点一次。'});

/* ---------- access ---------- */
async function preferences(){const p=await api('/api/preferences');$('#anti-truncation').checked=p.anti_truncation;$('#anti-truncation').disabled=false;$('#anti-state').textContent=p.anti_truncation?'已开启 · 去聊天软件刷新模型列表':'已关闭 · 用原来的模型就好'}
$('#anti-truncation').onchange=async e=>{const b=e.target,enabled=b.checked;b.disabled=true;try{await api('/api/preferences',{method:'PUT',body:JSON.stringify({anti_truncation:enabled})});await preferences();notify(enabled?'已开启，请刷新聊天软件的模型列表':'已关闭，原来的模型不受影响')}catch(err){b.checked=!enabled;notify(err.message)}finally{b.disabled=false}};
async function keys(){const rows=await api('/api/keys'),list=$('#key-list');if(!rows.length){list.replaceChildren(el('p','还没有密钥。点上面按钮新建一把。','hint'));return}list.replaceChildren();
 for(const k of rows){const row=el('div',undefined,'key'),info=el('div');info.append(el('strong',k.label),document.createElement('br'),el('small',k.prefix+'••••'));const b=el('button','撤销','btn text-danger sm');b.onclick=()=>task(b,async()=>{if(await confirmAction(`撤销“${k.label}”？用这把密钥的聊天软件会立刻不能用。`)){await api('/api/keys/'+k.id,{method:'DELETE'});await keys()}});row.append(info,b);list.append(row)}}
async function copy(button,text){await task(button,async()=>{try{await navigator.clipboard.writeText(text);notify('已复制')}catch{notify('复制不可用，请长按手动复制')}})}

/* ---------- wiring ---------- */
$$('[data-tab]').forEach(b=>b.onclick=()=>go(b.dataset.tab));
document.addEventListener('click',e=>{const t=e.target.closest('[data-go]');if(t){e.preventDefault();go(t.dataset.go)}});
$$('[data-close]').forEach(b=>b.onclick=()=>b.closest('dialog').close());
$('#goto-upload').onclick=()=>$('#add-account').scrollIntoView({behavior:'smooth',block:'start'});
$('#refresh-quota').onclick=()=>task($('#refresh-quota'),quota);
$('#prev').onclick=()=>task($('#prev'),async()=>{offset=Math.max(0,offset-20);await records()});
$('#next').onclick=()=>task($('#next'),async()=>{offset+=20;await records()});
$$('#record-filters button').forEach(b=>b.onclick=()=>{recordFilter=b.dataset.filter;offset=0;$$('#record-filters button').forEach(x=>x.classList.toggle('on',x===b));task(null,records)});
$$('#summary-range button').forEach(b=>b.onclick=()=>{summaryRange=b.dataset.range;$$('#summary-range button').forEach(x=>x.classList.toggle('on',x===b));renderStats(lastSummary?.[summaryRange])});
$('#logout').onclick=()=>task($('#logout'),async()=>{await api('/api/auth/logout',{method:'POST'});location.reload()});
$('#show-key').onclick=()=>$('#key-dialog').showModal();$('#copy-base').onclick=()=>copy($('#copy-base'),$('#api-base').textContent);$('#copy-key').onclick=()=>copy($('#copy-key'),$('#revealed-key').value);$('#reveal-dialog').addEventListener('close',()=>$('#revealed-key').value='');
$('#edit-form').onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{await api('/api/accounts/'+editId,{method:'PATCH',body:JSON.stringify({label:e.target.elements.label.value,email:e.target.elements.email.value.trim(),plan:e.target.elements.plan.value})});$('#edit-dialog').close();await accounts()})};
$('#key-form').onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{const r=await api('/api/keys',{method:'POST',body:JSON.stringify({label:e.target.elements.label.value})});e.target.reset();$('#key-dialog').close();$('#revealed-key').value=r.key;$('#reveal-dialog').showModal();await keys()})};
function closeProfile(){$('#profile-menu').hidden=true;$('#avatar-button').setAttribute('aria-expanded','false')}
$('#avatar-button').onclick=()=>{const hidden=!$('#profile-menu').hidden;$('#profile-menu').hidden=hidden;$('#avatar-button').setAttribute('aria-expanded',String(!hidden))};
document.addEventListener('click',e=>{if(!e.target.closest('.profile'))closeProfile();if(!e.target.closest('.acc-actions details'))$$('.acc-actions details[open]').forEach(d=>d.open=false)});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeProfile();$('#avatar-button').focus()}});
$('#open-guide').onclick=()=>go('guide');$('#guide-menu').onclick=()=>go('guide');$('#guide-back').onclick=()=>go('accounts');
(async()=>{const c=await api('/api/config');document.title=c.site_name;$('#version').textContent=c.version;$$('[data-site-name]').forEach(e=>e.textContent=c.site_name);$('#api-base').textContent=c.api_base;$('#discord-login').hidden=!c.discord_ready;$('#login-status').textContent=c.discord_ready?'用 Discord 验证身份后进入你的空间':'公共登录还没开放';
 let u;try{u=await api('/api/me')}catch{return}csrf=u.csrf;$('#login').hidden=true;$('#workspace').hidden=false;$('#user-name').textContent=u.name;$('#admin-link').hidden=u.role!=='admin';$('#user-role').textContent=u.role==='admin'?'管理员':'个人空间';
 if(u.avatar_url){$('#user-avatar').src=u.avatar_url;$('#user-avatar').alt=u.name+'的 Discord 头像'}$('#user-avatar').onerror=()=>{$('#user-avatar').onerror=null;$('#user-avatar').src=portalBase+'/static/猫娘图标.webp'};
 await go(location.hash.slice(1)||'home')})().catch(e=>notify(e.message));
