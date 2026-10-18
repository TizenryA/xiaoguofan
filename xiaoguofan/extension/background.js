'use strict';
importScripts('compat.js');
// User action only; bind only the supplied portal tab, never pick another tab.
async function openImporter(tab){
 if(!Number.isSafeInteger(tab?.id)||tab.id<0)return;
 const url=ImportAPI.raw.runtime.getURL('popup.html')+'?tab='+tab.id;
 try{await ImportAPI.call('windows','create',{url,type:'popup',width:440,height:760})}
 catch{await ImportAPI.call('tabs','create',{url,active:true})}
}
ImportAPI.raw.action.onClicked.addListener(tab=>{openImporter(tab).catch(()=>{})});
