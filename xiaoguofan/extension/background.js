'use strict';
// User action only. Never reads cookies or sends network requests in background.
async function openImporter(tab){
 if(!tab?.id)return;
 await chrome.windows.create({url:chrome.runtime.getURL('popup.html')+'?tab='+tab.id,type:'popup',width:440,height:760});
}
chrome.action.onClicked.addListener(openImporter);
