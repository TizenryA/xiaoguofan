'use strict';
// Shared by Lite/admin. Credentials exist only in the input until verification completes.
(()=>{
 const form=document.querySelector('#native-cookie-form');if(!form)return;
 const $=id=>document.getElementById(id);let ticket='',revision=0,pendingLabel='',busy=false;
 const request=(path,opt)=>api('/api/accounts/native/'+path,opt);
 function invalidate(){revision++;ticket='';$('native-confirm').hidden=true;$('native-account').replaceChildren()}
 async function discard(){try{await request('pending',{method:'DELETE'})}catch{/* expiry is the fallback */}}
 function sensitiveClear(){form.elements.cookie.value='';form.elements.accounts_cookie.value=''}
 function controls(disabled){busy=disabled;for(const e of form.elements)e.disabled=disabled;$('native-save').disabled=disabled;$('native-account').disabled=disabled}
 for(const e of form.elements)e.addEventListener('input',()=>{invalidate();$('native-status').textContent='内容已变更，请重新核对。'});
 $('native-clear').onclick=()=>{invalidate();sensitiveClear();form.reset();$('native-status').textContent='本页内容已清空，正在取消待确认数据。';discard().then(()=>{if(!ticket)$('native-status').textContent='本页内容已清空；待确认数据已请求删除，最迟五分钟失效。'})};
 form.onsubmit=async e=>{
  e.preventDefault();if(busy)return;
  if(!$('native-consent').checked){$('native-status').textContent='请先确认本人授权。';return}
  invalidate();const current=revision;pendingLabel=form.elements.label.value.trim();controls(true);$('native-status').textContent='正在向 Google 核对身份，请稍候…';
  try{
   const result=await request('verify',{method:'POST',body:JSON.stringify({cookie:form.elements.cookie.value,accounts_cookie:form.elements.accounts_cookie.value,consent:true})});
   sensitiveClear();
   if(revision!==current){await discard();return}
   ticket=result.ticket;
   for(const a of result.accounts){const option=document.createElement('option');option.value=String(a.authuser);option.textContent=a.email;$('native-account').append(option)}
   $('native-confirm').hidden=false;$('native-status').textContent=result.message+' 原始输入已清空；请在五分钟内确认。';
  }catch(err){if(revision===current)$('native-status').textContent=err.message}
  finally{controls(false)}
 };
 $('native-save').onclick=async()=>{
  if(busy||!ticket||!$('native-consent').checked)return;
  const current=revision;controls(true);
  try{
   await request('import',{method:'POST',body:JSON.stringify({ticket,authuser:Number($('native-account').value),label:pendingLabel,consent:true})});
   invalidate();sensitiveClear();form.reset();$('native-status').textContent='账号已加密保存。身份核对不等于模型生成成功，请到账号管理检查。';
   notify('原生 Cookie 账号已加密保存');await go('accounts');
  }catch(err){if(current===revision)$('native-status').textContent=err.message}
  finally{controls(false)}
 };
 document.querySelectorAll('[data-tab],#logout').forEach(button=>button.addEventListener('click',()=>{if(ticket||form.elements.cookie.value||form.elements.accounts_cookie.value){invalidate();sensitiveClear();discard()}}));
 window.addEventListener('pagehide',()=>{invalidate();sensitiveClear()});
})();
