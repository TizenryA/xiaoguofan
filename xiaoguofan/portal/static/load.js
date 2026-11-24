'use strict';
// Home page status line: short, human, no internals. Polls only while the home tab is visible.
(function(){
 let enabled=false,timer=null,inflight=false,epoch=0;
 const node=id=>document.getElementById(id);
 function render(d){
  const card=node('home-status-card');if(!card)return;
  card.classList.toggle('is-warning',!d.ok);card.classList.toggle('is-busy',Boolean(d.ok&&d.busy));
  let title=d.ok?(d.busy?'现在人比较多，可能要稍等一下':'一切正常，可以直接用'):d.message;
  const notes=[];
  if(d.mine_running)notes.push('你有 '+d.mine_running+' 条对话正在进行');
  if(d.mine_waiting)notes.push(d.mine_waiting+' 条在排队，马上轮到');
  node('home-status').textContent=title;
  node('home-status-note').textContent=notes.join(' · ')||'更新于 '+new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});
 }
 function schedule(seconds){clearTimeout(timer);if(enabled&&!document.hidden)timer=setTimeout(refresh,(seconds||5)*1000);}
 async function refresh(){
  if(!enabled||document.hidden||inflight)return;
  const ticket=epoch;inflight=true;let wait=5;
  try{const d=await api('/api/load');wait=d.refresh_seconds||5;if(enabled&&ticket===epoch)render(d);}
  catch(e){if(enabled&&ticket===epoch){node('home-status').textContent='暂时连不上服务器';node('home-status-note').textContent='稍后会自动重试';}wait=15;}
  finally{inflight=false;schedule(wait);}
 }
 window.homeStatusView=function(on){enabled=on;epoch++;clearTimeout(timer);if(on)refresh();};
 document.addEventListener('visibilitychange',()=>{epoch++;clearTimeout(timer);if(enabled&&!document.hidden)refresh();});
 window.addEventListener('pagehide',()=>{enabled=false;epoch++;clearTimeout(timer);});
})();
