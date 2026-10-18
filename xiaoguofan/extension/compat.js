'use strict';
(function(root){
 function makeAPI(chromeAPI,browserAPI){
  const native=browserAPI||null,api=native||chromeAPI;
  return {call(group,method,...args){
   const object=api?.[group],fn=object?.[method];
   if(typeof fn!=='function')return Promise.reject(Error('此浏览器缺少扩展接口：'+group+'.'+method));
   if(native){try{return Promise.resolve(fn.apply(object,args))}catch(e){return Promise.reject(e)}}
   return new Promise((resolve,reject)=>{
    let settled=false;const done=(error,value)=>{if(settled)return;settled=true;clearTimeout(timer);error?reject(Error(error)):resolve(value)};
    const timer=setTimeout(()=>done('浏览器接口未响应，请重新打开门户后重试'),15000);
    try{const result=fn.apply(object,[...args,value=>{const error=chromeAPI.runtime?.lastError;done(error?.message,value)}]);if(result&&typeof result.then==='function')result.then(v=>done(null,v),e=>done(e.message||String(e)))}catch(e){done(e.message||String(e))}
   });
  },raw:api};
 }
 const exports={makeAPI};if(typeof module!=='undefined')module.exports=exports;
 else root.ImportAPI=makeAPI(root.chrome,typeof root.browser==='object'?root.browser:null);
})(globalThis);
