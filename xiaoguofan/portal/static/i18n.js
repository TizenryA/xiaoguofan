/* 小锅饭 i18n: Chinese source text is the key; English comes from window.XGF_EN (i18n-en.js).
 * zh: nothing is changed. en: text nodes and a few attributes are translated as they appear
 * (MutationObserver), so JS-rendered UI, toasts and server messages are covered too. */
(function(root){'use strict';
const STORE='xgf-lang';
const CJK=/[\u3400-\u9fff\uff01-\uff5e\u3000-\u303f\u201c\u201d]/;
const HAN=/[\u3400-\u9fff]/;
function pickLang(nav,saved){if(saved==='zh'||saved==='en')return saved;return /^zh\b/i.test(nav||'')?'zh':'en'}
function compile(dict){
  const exact=Object.assign({},dict&&dict.exact);
  const templates=Object.entries(dict&&dict.templates||{}).sort((a,b)=>b[0].replace(/\{\w+\}/g,'').length-a[0].replace(/\{\w+\}/g,'').length).map(([k,v])=>{
    const names=[];const src='^'+k.split(/(\{\w+\})/).map(p=>{const m=/^\{(\w+)\}$/.exec(p);if(m){names.push(m[1]);return '([\\s\\S]+?)'}return p.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')}).join('')+'$';
    return {re:new RegExp(src),names,value:v}});
  return {exact,templates}}
function fill(value,vars){return value.replace(/\{(\w+)(?:\|([^|}]*)\|([^}]*))?\}/g,(m,n,one,many)=>{if(!(n in vars))return m;const v=vars[n];if(one!==undefined)return String(v).replace(/[^\d.]/g,'')==='1'?one:many;return v})}
const ALLOWED=/\[防截断\]|小锅饭 · 导出 JSON/g;
function hanCount(s){return (s.replace(ALLOWED,'').match(/[\u3400-\u9fff]/g)||[]).length}
function makeTranslator(dict){
  const c=compile(dict),memo=new Map();
  function tr(s,depth){
    if(!s||!HAN.test(s))return s;
    if(memo.has(s))return memo.get(s);if(memo.size>5000)memo.clear();
    const r=tr1(s,depth);memo.set(s,r);return r}
  function tr1(s,depth){
    const lead=s.match(/^\s*/)[0],trail=s.slice(lead.length).match(/\s*$/)[0],core=s.trim();
    if(Object.prototype.hasOwnProperty.call(c.exact,core))return lead+c.exact[core]+trail;
    // Try templates from most to least specific; keep the first result with no Chinese left,
    // else the one with the least (placeholders can split a sentence in more than one way).
    let best=null,bestLeft=Infinity;
    if((depth||0)<8)for(const t of c.templates){const m=t.re.exec(core);if(!m)continue;const vars={};t.names.forEach((n,i)=>vars[n]=tr(m[i+1],(depth||0)+1));const out=fill(t.value,vars),left=hanCount(out);if(left===0)return lead+out+trail;if(left<bestLeft){best=out;bestLeft=left}}
    return best===null?s:lead+best+trail}
  return tr}
function format(s,vars){return vars?s.replace(/\{(\w+)\}/g,(m,n)=>n in vars?vars[n]:m):s}
const api={pickLang,makeTranslator,CJK,HAN};
if(typeof module!=='undefined'&&module.exports){module.exports=api;return}

let saved=null;try{saved=localStorage.getItem(STORE)}catch{}
// The server reads this cookie for pages it renders itself (error pages, CSV export).
function cookie(l){document.cookie='xgf_lang='+l+'; path=/; max-age=31536000; SameSite=Lax'}
if((saved==='zh'||saved==='en')&&!document.cookie.includes('xgf_lang='+saved))cookie(saved);
const lang=pickLang(navigator.language,saved);
const tr=lang==='en'?makeTranslator(root.XGF_EN||{}):(s=>s);
const I={lang,locale:lang==='en'?'en-US':'zh-CN',
  // Times are shown in Beijing time for Chinese, and in the visitor's own zone for English.
  tz:lang==='en'?undefined:'Asia/Shanghai',
  tr:s=>tr(s),
  t:(s,vars)=>{if(lang!=='en')return format(s,vars);const v={};for(const k in vars||{})v[k]=tr(String(vars[k]));const keyed=tr(s);if(keyed!==s)return fill(keyed,v);return tr(format(s,vars))},
  setLang(l){try{localStorage.setItem(STORE,l)}catch{}cookie(l);location.reload()},
  num:n=>Number(n||0).toLocaleString(lang==='en'?'en-US':'zh-CN'),
  big(n){if(n==null)return '—';if(lang==='en')return n>=10000?Intl.NumberFormat('en-US',{notation:'compact',maximumFractionDigits:n>=100000?0:1}).format(n):n.toLocaleString('en-US');return n>=10000?(n/10000).toFixed(n>=100000?0:1)+' 万':n.toLocaleString('zh-CN')},
  time(t,o){return new Date(t*1000).toLocaleString(I.locale,Object.assign({timeZone:I.tz},o||{}))}};
root.XGF_I18N=I;
document.documentElement.lang=lang==='en'?'en':'zh-CN';
const ATTRS=['placeholder','title','aria-label','alt'];
const SKIP='script,style,textarea,code.raw,[data-i18n-skip],[translate=no]';
function skip(node){const e=node.nodeType===1?node:node.parentElement;return !e||!!e.closest(SKIP)}
function doText(n){if(skip(n))return;const v=tr(n.nodeValue);if(v!==n.nodeValue)n.nodeValue=v}
function doAttrs(e){if(e.closest&&e.closest('[data-i18n-skip],[translate=no]'))return;for(const a of ATTRS){const v=e.getAttribute&&e.getAttribute(a);if(v&&HAN.test(v)){const x=tr(v);if(x!==v)e.setAttribute(a,x)}}
  if((e.tagName==='INPUT'&&/^(button|submit)$/i.test(e.type))&&HAN.test(e.value)){e.value=tr(e.value)}}
function walk(rootNode){if(rootNode.nodeType===3){doText(rootNode);return}if(rootNode.nodeType!==1&&rootNode.nodeType!==9&&rootNode.nodeType!==11)return;
  if(rootNode.nodeType===1){if(skip(rootNode))return;doAttrs(rootNode)}
  const w=document.createTreeWalker(rootNode,5);let n;while((n=w.nextNode())){if(n.nodeType===3)doText(n);else doAttrs(n)}}
function mountToggles(){document.querySelectorAll('[data-lang-toggle]').forEach(b=>{b.setAttribute('translate','no');b.textContent=lang==='en'?'中文':'EN';b.title=lang==='en'?'切换到中文':'Switch to English';b.setAttribute('aria-label',b.title);b.setAttribute('lang',lang==='en'?'zh-CN':'en');if(!b.dataset.bound){b.dataset.bound='1';b.addEventListener('click',()=>I.setLang(lang==='en'?'zh':'en'))}})}
document.addEventListener('DOMContentLoaded',mountToggles);
// Server messages follow the page language, not the browser default.
const _fetch=root.fetch.bind(root),wanted=lang==='en'?'en':'zh-CN';
root.fetch=(input,init)=>{init=Object.assign({},init);const h=new Headers(init.headers||(input instanceof Request?input.headers:undefined));if(!h.has('Accept-Language'))h.set('Accept-Language',wanted);init.headers=h;return _fetch(input,init)};
if(lang!=='en')return;
new MutationObserver(list=>{for(const m of list){if(m.type==='characterData')doText(m.target);else if(m.type==='attributes')doAttrs(m.target);else m.addedNodes.forEach(walk)}}).observe(document.documentElement,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:ATTRS});
document.addEventListener('DOMContentLoaded',()=>walk(document.documentElement));
const _alert=root.alert,_confirm=root.confirm,_prompt=root.prompt;
root.alert=m=>_alert.call(root,tr(String(m)));root.confirm=m=>_confirm.call(root,tr(String(m)));root.prompt=(m,d)=>_prompt.call(root,tr(String(m)),d);
})(typeof window!=='undefined'?window:globalThis);
