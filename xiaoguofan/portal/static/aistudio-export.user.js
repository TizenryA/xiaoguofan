// ==UserScript==
// @name         小锅饭 · AI Studio 登录文件导出
// @namespace    https://github.com/TizenryA/xiaoguofan
// @version      1.1.2
// @description  在本人确认后读取 AI Studio 登录状态，仅下载到本机，不自动上传。官方 Tampermonkey Cookie API，HttpOnly 支持取决于版本。
// @author       渡鸦4455
// @match        https://aistudio.google.com/*
// @include      https://accounts.google.com/*
// @grant        GM_cookie
// @grant        GM.cookie
// @grant        GM_registerMenuCommand
// @grant        GM_xmlhttpRequest
// @connect      accounts.google.com
// @run-at       document-start
// @updateURL    https://aistudio.tizenty.xyz/lite/static/aistudio-export.user.js
// @downloadURL  https://aistudio.tizenty.xyz/lite/static/aistudio-export.user.js
// @noframes
// @license      MIT
// ==/UserScript==
'use strict';
(function(){
 const VERSION='1.1.2';
 const REQUIRED=['SAPISID','__Secure-1PAPISID','__Secure-3PAPISID'];
 function decodeAccounts(raw){
  let text=String(raw).trim();if(text.startsWith('"')){try{text=JSON.parse(text)}catch{throw Error('Google 账号列表格式变化，请稍后重试。')}}
  let bytes;try{bytes=Uint8Array.from(atob(text),c=>c.charCodeAt(0))}catch{throw Error('Google 账号列表格式变化，请稍后重试。')}
  function fields(b){let i=0,out=[];const v=()=>{let n=0,p=1,k=0;while(i<b.length&&k++<8){const c=b[i++];n+=(c&127)*p;if(!(c&128))return n;p*=128}throw Error('Google 账号列表字段无效。')};while(i<b.length){const tag=v(),wire=tag%8,num=Math.floor(tag/8);let x;if(wire===0)x=v();else if(wire===2){const len=v();if(len>b.length-i)throw Error('Google 账号列表字段无效。');x=b.slice(i,i+len);i+=len}else if(wire===1||wire===5){i+=wire===1?8:4;if(i>b.length)throw Error('Google 账号列表字段无效。');continue}else throw Error('Google 账号列表协议已变化。');out.push([num,wire,x])}return out}
  const rows=fields(bytes);if(rows.length>100)throw Error('登录账号过多。');return rows.map(([n,w,b],index)=>{if(n!==1||w!==2)throw Error('Google 账号列表协议已变化。');const f=fields(b),s=k=>{const x=f.find(x=>x[0]===k&&x[1]===2);return x?new TextDecoder().decode(x[2]):''},v=(k,d)=>{const x=f.find(x=>x[0]===k&&x[1]===0);return x?!!x[2]:d};return {email:s(3).toLowerCase(),gaia_id:s(10),authuser:index,valid:v(9,true)&&!v(14,false)&&v(15,true)}});
 }
 function selectIdentity(email,rows){
  email=String(email).trim().toLowerCase();const matched=rows.filter(x=>x.email===email&&x.valid&&/^\d{1,32}$/.test(x.gaia_id));
  if(matched.length!==1)throw Error('邮箱与当前登录会话不匹配，或账号已退出。请填写此浏览器已登录的邮箱，不要只改备注。');
  const x=matched[0];return {email:x.email,gaia_id:x.gaia_id,authuser:x.authuser};
 }
 function readIdentity(api,email){
  return new Promise((resolve,reject)=>{if(typeof api.GM_xmlhttpRequest!=='function')return reject(Error('缺少身份查询权限，请更新脚本并确认新增权限。'));
   api.GM_xmlhttpRequest({method:'POST',data:' ',headers:{'Content-Type':'application/x-www-form-urlencoded','Origin':'https://www.google.com'},url:'https://accounts.google.com/ListAccounts?gpsia=1&source=ChromiumBrowser&json=standard&laf=b64bin',timeout:20000,anonymous:false,redirect:'error',onload:r=>{try{if(r.status!==200||String(r.responseText).length>100000||r.finalUrl&&!r.finalUrl.startsWith('https://accounts.google.com/ListAccounts?'))throw Error('Google 身份查询失败，请确认浏览器仍登录该账号。');resolve(selectIdentity(email,decodeAccounts(r.responseText)))}catch(e){reject(e)}},onerror:()=>reject(Error('无法查询 Google 登录身份，请检查脚本的 accounts.google.com 授权。')),ontimeout:()=>reject(Error('Google 身份查询超时，请重试。'))});
  });
 }
 function readCookiesAt(api,url){
  return new Promise((resolve,reject)=>{
   let settled=false;
   const finish=(cookies,error)=>{if(settled)return;settled=true;clearTimeout(timer);if(error)return reject(Error('Cookie 读取被拒绝或不可用，请检查窜改猴的授权与版本；不要关闭浏览器的安全保护。'));if(!Array.isArray(cookies))return reject(Error('Cookie 接口没有返回登录数据。'));resolve(cookies)};
   const timer=setTimeout(()=>finish(null,true),12000);
   try{
    if(typeof api.GM_cookie?.list==='function'){
     const result=api.GM_cookie.list({url},finish);
     if(result&&typeof result.then==='function')result.then(c=>finish(c),()=>finish(null,true));
    }else if(typeof api.GM?.cookie?.list==='function'){
     Promise.resolve(api.GM.cookie.list({url})).then(c=>finish(c),()=>finish(null,true));
    }else{settled=true;clearTimeout(timer);reject(Error('当前窜改猴没有 GM_cookie 接口。请更新官方版；官方文档注明 HttpOnly 读取目前限 Beta 支持。'))}
   }catch{finish(null,true)}
  });
 }
 async function readCookies(api){
  const batches=await Promise.all(['https://aistudio.google.com/','https://accounts.google.com/'].map(url=>readCookiesAt(api,url)));
  const byKey=new Map();for(const c of batches.flat()){const key=JSON.stringify([c?.domain,c?.path,c?.name,c?.partitionKey,c?.firstPartyDomain]);const old=byKey.get(key);if(old&&old.value!==c.value)throw Error('登录会话在读取时变化，请刷新 AI Studio 后重新检查。');if(!old)byKey.set(key,c)}
  return [...byKey.values()];
 }
 function buildExport(email,label,cookies,identity){
  email=typeof email==='string'?email.trim().toLowerCase():'';
  label=typeof label==='string'?label.trim():'';
  if(email.length>254||!/^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$/.test(email))throw Error('请填当前专用 Google 账号的完整邮箱，不是昵称。');
  if(!label||label.length>80)throw Error('请填 1–80 字备注。');
  if(!Array.isArray(cookies)||cookies.length>300)throw Error('登录数据格式不正确，请重新登录后再试。');
  const now=Date.now()/1000;
  const normalized=cookies.filter(c=>{
   if(!c||typeof c.domain!=='string'||c.partitionKey||c.firstPartyDomain)return false;
   const domain=c.domain.replace(/^\./,'');
   if(!['google.com','aistudio.google.com','accounts.google.com'].includes(domain)||c.path!=='/')return false;
   const expiry=c.session===true?-1:(c.expirationDate??-1);
   return typeof expiry==='number'&&Number.isFinite(expiry)&&(expiry===-1||expiry>now);
  }).map(c=>{
   if(typeof c.name!=='string'||!c.name||c.name.length>256||typeof c.value!=='string'||c.value.length>20000)throw Error('登录数据有缺项，请重新导出。');
   const sameSite=({no_restriction:'None',lax:'Lax',strict:'Strict',unspecified:'Lax',None:'None',Lax:'Lax',Strict:'Strict'})[c.sameSite]||'Lax';
   return {name:c.name,value:c.value,domain:c.domain,path:c.path,expires:c.session===true?-1:(c.expirationDate??-1),httpOnly:c.httpOnly===true,secure:c.secure===true,sameSite};
  });
  for(const name of REQUIRED)if(normalized.filter(c=>c.name===name&&c.value).length!==1)throw Error('必要登录数据缺失、过期或有冲突。请检查浏览器登录状态并刷新 AI Studio 重试。');
  if(!normalized.some(c=>c.httpOnly&&c.value))throw Error('未读到 HttpOnly 登录数据，已阻止导出。官方文档注明该能力目前限 Tampermonkey Beta；普通网页 Cookie 不能代替完整登录文件。');
  if(identity&&(identity.email!==email||!/^\d{1,32}$/.test(identity.gaia_id)||!Number.isInteger(identity.authuser)||identity.authuser<0||identity.authuser>99))throw Error('Google 身份不匹配，请重新检查。');
  return {email,label,storage_state:{cookies:normalized,origins:[],...(identity?{xiaoguofan_identity:identity}:{})},locale:'en-US',timezone:'America/Los_Angeles'};
 }
 if(typeof module!=='undefined'&&module.exports){module.exports={readCookies,buildExport,decodeAccounts,selectIdentity,readIdentity};return}
 if(location.protocol!=='https:'||location.hostname!=='aistudio.google.com'||window.top!==window.self)return;
 function mount(){
 if(document.getElementById('xiaoguofan-export-host'))return;
 if(!document.body)return;
 const api={GM_xmlhttpRequest:typeof GM_xmlhttpRequest!=='undefined'?GM_xmlhttpRequest:undefined,GM_cookie:typeof GM_cookie!=='undefined'?GM_cookie:undefined,GM:typeof GM!=='undefined'?GM:undefined};
 const host=document.createElement('div');host.id='xiaoguofan-export-host';
 // Fixed host owns the stacking context; mobile page CSS cannot hide its children.
 host.style.cssText='all:initial!important;display:block!important;position:fixed!important;inset:0!important;width:0!important;height:0!important;overflow:visible!important;visibility:visible!important;opacity:1!important;z-index:2147483647!important;';
 document.body.appendChild(host);
 let shadow=host;
 try{if(typeof host.attachShadow==='function')shadow=host.attachShadow({mode:'closed'})}catch(_){/* old WebView: plain DOM fallback */}
 const ui=document.createElement('div');ui.id='xiaoguofan-export-ui';shadow.appendChild(ui);shadow=ui;

 const e1=document.createElement("style");
 shadow.appendChild(e1);
 e1.appendChild(document.createTextNode("\n #xiaoguofan-export-ui{all:initial}#xiaoguofan-export-ui *{box-sizing:border-box}#xiaoguofan-export-ui button,#xiaoguofan-export-ui input{font:inherit}#xiaoguofan-export-ui button{cursor:pointer;min-height:3vw;padding:.7vw 1.2vw;border:0;border-radius:3vw;background:#435a90;color:#fff}#xiaoguofan-export-ui button:disabled{opacity:.45;cursor:default}#xiaoguofan-export-ui button:focus-visible,#xiaoguofan-export-ui input:focus-visible,#xiaoguofan-export-ui a:focus-visible{outline:.2vw solid #8a64b6;outline-offset:.2vw}#xiaoguofan-export-ui #launch{position:fixed;right:2vw;bottom:7vh;z-index:2147483000;font:500 1vw/1.5 sans-serif;border:.08vw solid #d1ddff}#xiaoguofan-export-ui dialog{position:fixed;inset:0;margin:auto;width:38vw;max-height:86vh;overflow:auto;border:.08vw solid #a5b3cc;border-radius:1.5vw;padding:1.8vw;background:#f4f6fc;color:#192338;font:400 1vw/1.65 sans-serif;z-index:2147483001}#xiaoguofan-export-ui dialog:not([open]){display:none}#xiaoguofan-export-ui dialog::backdrop{background:#10192c88;backdrop-filter:blur(.35vw)}#xiaoguofan-export-ui h2{font-size:1.5vw;margin:0}#xiaoguofan-export-ui p{margin:.8vw 0}#xiaoguofan-export-ui .warning{background:#f7e7cb;color:#594016;border-radius:.7vw;padding:.8vw}#xiaoguofan-export-ui label{display:flex;flex-direction:column;gap:.3vw;margin:1vw 0}#xiaoguofan-export-ui input{width:100%;padding:.7vw;border:.08vw solid #9daec8;background:#fff;color:#18253c;border-radius:.5vw}#xiaoguofan-export-ui .consent{flex-direction:row;align-items:flex-start;gap:.6vw}#xiaoguofan-export-ui .consent input{width:1.3vw;height:1.3vw;flex-shrink:0}#xiaoguofan-export-ui .actions{display:flex;flex-wrap:wrap;gap:.6vw}#xiaoguofan-export-ui .text{background:#e2e8f5;color:#253d66}#xiaoguofan-export-ui #status{overflow-wrap:anywhere}#xiaoguofan-export-ui .small{font-size:.88vw;color:#455571}#xiaoguofan-export-ui a{color:#304f88}#xiaoguofan-export-ui [hidden]{display:none!important}\n @media(max-width:700px),(pointer:coarse){#xiaoguofan-export-ui #launch{right:4vw;font-size:max(3.5vw,1.8vh);min-height:max(11vw,5vh);padding:2.5vw 4vw;border-radius:8vw;border-width:.25vw}#xiaoguofan-export-ui dialog{width:92vw;max-width:92vw;max-height:84vh;padding:5vw;border-radius:5vw;border-width:.25vw;font-size:3.6vw}#xiaoguofan-export-ui h2{font-size:5.2vw}#xiaoguofan-export-ui p{margin:3vw 0}#xiaoguofan-export-ui .warning{padding:3vw;border-radius:2vw}#xiaoguofan-export-ui label{margin:4vw 0;gap:1vw}#xiaoguofan-export-ui input{padding:3vw;border-radius:2vw;border-width:.25vw}#xiaoguofan-export-ui .consent{gap:2vw}#xiaoguofan-export-ui .consent input{width:5vw;height:5vw}#xiaoguofan-export-ui .actions{gap:2vw}#xiaoguofan-export-ui button{font-size:3.6vw;min-height:11vw;padding:2.5vw 4vw;border-radius:8vw}#xiaoguofan-export-ui .small{font-size:3.2vw}#xiaoguofan-export-ui button:focus-visible,#xiaoguofan-export-ui input:focus-visible,#xiaoguofan-export-ui a:focus-visible{outline-width:.5vw;outline-offset:.5vw}}\n "));
 const e2=document.createElement("button");
 e2.setAttribute("id","launch");
 e2.setAttribute("type","button");
 shadow.appendChild(e2);
 e2.appendChild(document.createTextNode("小锅饭 · 导出 JSON"));
 const e3=document.createElement("dialog");
 e3.setAttribute("aria-labelledby","heading");
 shadow.appendChild(e3);
 const e4=document.createElement("h2");
 e4.setAttribute("id","heading");
 e3.appendChild(e4);
 e4.appendChild(document.createTextNode("导出本人的登录文件"));
 const e5=document.createElement("p");
 e5.setAttribute("class","small");
 e3.appendChild(e5);
 e5.appendChild(document.createTextNode('适配 Tampermonkey · '+VERSION+' · 只下载，不上传'));
 const e6=document.createElement("p");
 e6.setAttribute("class","warning");
 e3.appendChild(e6);
 e6.appendChild(document.createTextNode("文件就是账号的登录通行证。拿到它的人可能访问此浏览器会话里的全部 Google 账号，不只所选邮箱。建议专用浏览器环境，只用专用账号，不要发群里；上传前确认小锅饭网站可信。"));
 const e7=document.createElement("p");
 e3.appendChild(e7);
 e7.appendChild(document.createTextNode("可登录多个 Google 账号。填写要导出的已登录邮箱，脚本会向 Google 核对并绑定该账号；邮箱不匹配时不会导出。"));
 const e8=document.createElement("label");
 e3.appendChild(e8);
 e8.appendChild(document.createTextNode("Google 邮箱"));
 const e9=document.createElement("input");
 e9.setAttribute("id","email");
 e9.setAttribute("type","email");
 e9.setAttribute("autocomplete","off");
 e9.setAttribute("maxlength","254");
 e9.setAttribute("placeholder","\u6b64\u6d4f\u89c8\u5668\u5f53\u524d\u767b\u5f55\u7684\u90ae\u7bb1");
 e8.appendChild(e9);
 const e10=document.createElement("label");
 e3.appendChild(e10);
 e10.appendChild(document.createTextNode("备注"));
 const e11=document.createElement("input");
 e11.setAttribute("id","label");
 e11.setAttribute("maxlength","80");
 e11.setAttribute("autocomplete","off");
 e11.setAttribute("value","\u6211\u7684\u4e13\u7528\u8d26\u53f7");
 e10.appendChild(e11);
 const e12=document.createElement("label");
 e12.setAttribute("class","consent");
 e3.appendChild(e12);
 const e13=document.createElement("input");
 e13.setAttribute("id","consent");
 e13.setAttribute("type","checkbox");
 e12.appendChild(e13);
 e12.appendChild(document.createTextNode("我了解文件的风险，同意读取本人的登录数据并下载到本机。"));
 const e14=document.createElement("div");
 e14.setAttribute("class","actions");
 e3.appendChild(e14);
 const e15=document.createElement("button");
 e15.setAttribute("id","read");
 e15.setAttribute("type","button");
 e14.appendChild(e15);
 e15.appendChild(document.createTextNode("检查登录数据"));
 const e16=document.createElement("button");
 e16.setAttribute("id","save");
 e16.setAttribute("type","button");
 e16.setAttribute("disabled","");
 e14.appendChild(e16);
 e16.appendChild(document.createTextNode("下载 JSON"));
 const e17=document.createElement("button");
 e17.setAttribute("id","close");
 e17.setAttribute("type","button");
 e17.setAttribute("class","text");
 e14.appendChild(e17);
 e17.appendChild(document.createTextNode("关闭并清空"));
 const e18=document.createElement("p");
 e18.setAttribute("id","status");
 e18.setAttribute("role","status");
 e18.setAttribute("aria-live","polite");
 e3.appendChild(e18);
 e18.appendChild(document.createTextNode("尚未读取。不会显示或复制 Cookie 原值。"));
 const e19=document.createElement("p");
 e19.setAttribute("class","small");
 e3.appendChild(e19);
 e19.appendChild(document.createTextNode("若提示读不到 HttpOnly，请核对窜改猴版本。官方文档目前注明仅 Beta 支持，脚本不能绕过这个限制。下载成功也不代表 Google 登录或模型权限可用。"));
 const e20=document.createElement("p");
 e20.setAttribute("class","small");
 e3.appendChild(e20);
 e20.appendChild(document.createTextNode("下载后返回小锅饭 → 上传 → 选择 JSON。上传完成后删除下载目录和回收站里的登录文件。"));
 const e21=document.createElement("a");
 e21.setAttribute("href","https://www.tampermonkey.net/documentation.php?locale=en&q=GM_cookie");
 e21.setAttribute("target","_blank");
 e21.setAttribute("rel","noopener noreferrer");
 e3.appendChild(e21);
 e21.appendChild(document.createTextNode("查看官方 Cookie 接口说明 ↗"));
 const $=s=>shadow.querySelector(s),dialog=$('dialog');let prepared=null,busy=false,revision=0;
 const clear=()=>{revision++;prepared=null;$('#save').disabled=true};
 const close=()=>{clear();$('#consent').checked=false;if(dialog.open&&typeof dialog.close==='function')dialog.close();else dialog.removeAttribute('open');$('#status').textContent='已清空内存中的待导出数据。'};
 const open=()=>{if(dialog.open)return;try{if(typeof dialog.showModal==='function'){dialog.showModal();return}}catch(_){}dialog.setAttribute('open','')};
 $('#launch').onclick=open;$('#close').onclick=close;dialog.addEventListener('cancel',e=>{e.preventDefault();close()});
 for(const id of ['email','label','consent'])$('#'+id).addEventListener('input',()=>{clear();$('#status').textContent='内容已变更，请重新检查。'});
 $('#read').onclick=async()=>{
  if(busy)return;clear();if(!$('#consent').checked){$('#status').textContent='请先勾选本人授权。';return}
  // Validate form before invoking privileged API.
  if(!$('#email').value.trim()||!$('#email').checkValidity()||!$('#label').value.trim()){$('#status').textContent='请先填完整邮箱和备注。';return}
  const current=revision;busy=true;$('#read').disabled=true;
  try{const identity=await readIdentity(api,$('#email').value);const cookies=await readCookies(api);if(revision!==current||!dialog.open)return;prepared=buildExport($('#email').value,$('#label').value,cookies,identity);$('#save').disabled=false;$('#status').textContent=`身份已核对：${prepared.email}（账号 ${identity.authuser}），${prepared.storage_state.cookies.length} 项，包含 HttpOnly。未验证模型权限；点击“下载 JSON”保存到本机。`}
  catch(e){clear();$('#status').textContent=e.message}
  finally{busy=false;$('#read').disabled=false}
 };
 $('#save').onclick=()=>{
  if(!prepared||!$('#consent').checked)return;
  const blob=new Blob([JSON.stringify(prepared,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),anchor=document.createElement('a');
  anchor.href=url;anchor.download='小锅饭登录文件_'+new Date().toISOString().replace(/[:.]/g,'-')+'.json';shadow.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);clear();$('#status').textContent='已交给浏览器下载。若没有出现文件，请检查下载权限。接着到小锅饭上传，勿分享此文件。';
 };
 window.addEventListener('pagehide',clear);
 if(typeof GM_registerMenuCommand==='function'){try{GM_registerMenuCommand('小锅饭：导出登录 JSON',()=>{if(!host.isConnected){mount();return}open()})}catch(_){}}
 const hasCookies=typeof api.GM_cookie?.list==='function'||typeof api.GM?.cookie?.list==='function';
 if(!hasCookies)$('#status').textContent='导出面板已加载；当前脚本管理器未提供 Cookie API，无法导出完整登录文件。Via 内置脚本并不等于官方 Tampermonkey，请使用支持 GM_cookie 和 HttpOnly 的版本。';
 }
 // Show UI independently of privileged APIs, recover after SPA/body replacement.
 function safeMount(){try{mount()}catch(e){console.error('[小锅饭导出] 面板加载失败',e)}}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',safeMount,{once:true});
 else safeMount();
 window.addEventListener('pageshow',safeMount);
 let scheduled=false;
 const observer=new MutationObserver(()=>{if(document.getElementById('xiaoguofan-export-host')||scheduled)return;scheduled=true;setTimeout(()=>{scheduled=false;safeMount()},100)});
 observer.observe(document,{childList:true,subtree:true});
})();
