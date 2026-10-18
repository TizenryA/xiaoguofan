'use strict';
const $=s=>document.querySelector(s);
let tabId,origin,owner,payload,busy=false;
function status(t){$('#status').textContent=t}
async function pageCall(mode,data,expected){
 const tab=await chrome.tabs.get(tabId);
 if(new URL(tab.url).origin!==origin)throw Error('门户标签已切换，请重新打开扩展');
 const result=await chrome.scripting.executeScript({target:{tabId},func:async(mode,data,expected,targetOrigin)=>{
  try{
   if(location.origin!==targetOrigin)return {error:"门户地址已变更，请重新打开扩展"};
   const base=document.documentElement.dataset.portalBase||'';
   if(!/^\/[a-zA-Z0-9_-]+$/.test(base)&&base!=='')return {error:'门户路径无效'};
   const r=await fetch(base+'/api/me',{credentials:'same-origin',redirect:'error',cache:'no-store'});if(!r.ok)return {error:'请先登录当前门户'};
   const u=await r.json();if(!u.id||!u.csrf)return {error:'当前页面不是兼容的Lite门户'};
   if(mode==='identity')return {id:u.id,name:u.name};
   if(u.id!==expected)return {error:'门户用户已变更，请重新读取并确认归属'};
   const upload=await fetch(base+'/api/accounts/import',{method:'POST',credentials:'same-origin',redirect:'error',headers:{'Content-Type':'application/json','X-CSRF-Token':u.csrf},body:JSON.stringify(data)});
   const j=await upload.json();if(!upload.ok)return {error:typeof j.detail==='string'?j.detail:'上传被拒绝'};
   return {ok:true,id:j.id};
  }catch{return {error:'门户连接失败，请刷新登录后重试'}}
 },args:[mode,data??null,expected??null,origin]});
 const r=result[0]?.result;if(!r||r.error)throw Error(r?.error||'无法访问门户');return r;
}
async function work(fn){if(busy)return;busy=true;$('#read').disabled=true;$('#upload').disabled=true;try{await fn()}catch(e){payload=null;$('#preview').hidden=true;status(e.message)}finally{busy=false;$('#read').disabled=false;$('#upload').disabled=!payload}}
$('#read').onclick=()=>{
 if(!$('#consent').checked)return status('请先阅读并勾选授权');
 // Request only in direct user-gesture handler, before other awaits.
 const permission=chrome.permissions.request({origins:['https://*.google.com/*']});
 work(async()=>{
  if(!await permission)throw Error('未授权读取Google Cookie');
  owner=await pageCall('identity');
  const cookies=await chrome.cookies.getAll({url:'https://aistudio.google.com/'});
  payload=ImportCore.makePayload($('#label').value,cookies,$('#email').value);
  $('#identity').textContent='归属：'+owner.name+' · '+origin;
  $('#summary').textContent=`已读取 ${payload.storage_state.cookies.length} 个适用Cookie（含HttpOnly）；尚未上传。`;
  $('#preview').hidden=false;status('请再次确认目标及本人身份');
 });
};
$('#upload').onclick=()=>work(async()=>{
 if(!$('#consent').checked)throw Error('授权已取消，请重新确认');
 if(!payload||!owner)throw Error('请先读取并检查');
 await pageCall('upload',payload,owner.id);payload=null;$('#preview').hidden=true;status('上传成功，已加密保存。请返回门户刷新账号管理。');
});
for(const id of ['label','email'])$('#'+id).addEventListener('input',()=>{payload=null;$('#preview').hidden=true;$('#upload').disabled=true});
(async()=>{const id=Number(new URL(location.href).searchParams.get('tab'));if(!Number.isSafeInteger(id)||id<=0)throw Error('请从已登录的门户标签点击扩展图标打开');const tab=await chrome.tabs.get(id);tabId=tab.id;const u=new URL(tab.url);origin=ImportCore.validateTarget(u.origin);$('#target').value=origin;})().catch(e=>{status(e.message);$('#read').disabled=true});
