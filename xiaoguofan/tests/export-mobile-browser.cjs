// Browser compatibility fixtures only: no Google session / Cookie values.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright-core');
const fs=require('fs'),assert=require('node:assert/strict');
const file=process.env.EXPORT_SCRIPT||require('path').join(__dirname,'../portal/static/aistudio-export.user.js');
const script=fs.readFileSync(file,'utf8');
(async()=>{
 const b=await chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox']});
 try{
  for(const scenario of ['trusted-types','no-shadow','shadow-throws','document-start','no-gm','dom-replaced','desktop-ua','dialog-throws','no-dialog']){
   const c=await b.newContext({viewport:{width:393,height:851},isMobile:true,hasTouch:true});
   await c.addInitScript(({scenario,script})=>{
    window.__errors=[];window.addEventListener('error',e=>__errors.push(e.message));
    if(scenario==='no-shadow')Element.prototype.attachShadow=undefined;
    else if(scenario==='shadow-throws')Element.prototype.attachShadow=function(){throw Error('fixture incompatible shadow')};
    else {const attach=Element.prototype.attachShadow;Element.prototype.attachShadow=function(o){const r=attach.call(this,o);if(this.id==='xiaoguofan-export-host')window.__root=r;return r}}
    if(scenario==='dialog-throws')HTMLDialogElement.prototype.showModal=function(){throw Error('fixture old dialog')};
    if(scenario==='no-dialog'){HTMLDialogElement.prototype.showModal=undefined;HTMLDialogElement.prototype.close=undefined}
    window.__calls=0;window.GM_registerMenuCommand=()=>{};
    if(scenario!=='no-gm')window.GM_cookie={list(d,cb){__calls++;cb([],null)}};
    // addInitScript executes before the site scripts, like the userscript manager.
    try{(0,eval)(script)}catch(e){window.__errors.push(e.message)}
   },{scenario,script});
   const p=await c.newPage();
   await p.route('https://aistudio.google.com/**',r=>r.fulfill({contentType:'text/html',headers:scenario==='trusted-types'?{'Content-Security-Policy':"require-trusted-types-for 'script'; trusted-types 'none'"}:{},body:'<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><title>ISOLATED fixture NOT Google</title><body><main>模拟手机注入环境</main></body>'}));
   await p.goto('https://aistudio.google.com/prompts/new_chat');
   if(scenario==='desktop-ua')await p.setViewportSize({width:980,height:1600});
   await p.waitForFunction(()=>{const h=document.getElementById('xiaoguofan-export-host'),r=window.__root||h?.shadowRoot||h;return !!r?.querySelector('#launch')},null,{timeout:3000});
   assert.deepEqual(await p.evaluate(()=>__errors),[],scenario);
   if(scenario==='dom-replaced'){
    await p.evaluate(()=>document.getElementById('xiaoguofan-export-host').remove());
    await p.waitForFunction(()=>!!document.getElementById('xiaoguofan-export-host')?.isConnected);
   }
   const result=await p.evaluate(()=>{const h=document.getElementById('xiaoguofan-export-host'),r=window.__root||h.shadowRoot||h,l=r.querySelector('#launch');const box=l.getBoundingClientRect(),at=document.elementFromPoint(box.left+box.width/2,box.top+box.height/2);l.click();return {w:box.width,h:box.height,x:box.left,y:box.top,bottom:box.bottom,hit:at===h||at===l||h.contains(at),opened:r.querySelector('dialog').open,calls:__calls,hosts:document.querySelectorAll('#xiaoguofan-export-host').length}});
   assert(result.w>30&&result.h>30&&result.y>=0&&result.bottom<=(scenario==='desktop-ua'?1600:851),JSON.stringify({scenario,...result}));
   assert(result.hit&&result.opened,JSON.stringify({scenario,...result}));assert.equal(result.calls,0);assert.equal(result.hosts,1);
   await p.screenshot({path:require('path').join(process.env.ARTIFACT_DIR||require('os').tmpdir(),'导出脚本-'+scenario+'.png')});
   console.log('MOBILE_INJECTION_BUTTON_OPEN_NO_PRIVILEGED_READ_PASS',scenario);await c.close();
  }
 }finally{await b.close()}
})().catch(e=>{console.error(e);process.exit(1)});
