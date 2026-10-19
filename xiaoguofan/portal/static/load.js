'use strict';
(function(){
 let enabled=false,timer=null,inflight=false,epoch=0,last=0;
 const node=id=>document.getElementById(id),number=n=>n==null?'—':Number(n).toLocaleString('zh-CN');
 function state(message,error){document.querySelector('[data-page=load]').classList.toggle('load-stale',Boolean(error));node('load-state').textContent=message;node('load-error').hidden=!error;if(error)node('load-error').textContent=error;}
 function render(d){
  if(!d.available){state('采样不可用','资源保护或采样服务暂不可用，旧值不代表当前负载。');return;}
  last=d.timestamp;const m=d.memory,p=d.pool,c=d.cpu;
  node('load-memory').textContent=m.used_bytes==null?'—':(m.used_bytes/1048576).toFixed(0)+' MiB';
  const bar=node('load-memory-bar');bar.max=m.limit_bytes;if(m.used_bytes==null)bar.removeAttribute('value');else bar.value=m.used_bytes;
  node('load-memory-note').textContent=m.used_bytes>=m.high_bytes?'内存接近预算，优先回收空闲池':'总预算包含所有用户池与门户进程';
  node('load-cpu').textContent=c.percent==null?'采样中':c.percent.toFixed(1)+'%';
  node('load-requests').textContent=number(d.requests.active)+' / '+number(d.requests.limit);
  node('load-running').textContent=number(p.running);node('load-pool-note').textContent='名额上限 '+number(p.limit)+' · 实际受内存预算限制';
  for(const k of ['active','idle','starting','evictions'])node('load-'+k).textContent=number(p[k]);
  node('load-time').textContent='更新于 '+new Date(last*1000).toLocaleTimeString('zh-CN');state('实时采样',null);
 }
 function schedule(){clearTimeout(timer);if(enabled&&!document.hidden)timer=setTimeout(refresh,3000);}
 async function refresh(){
  if(!enabled||document.hidden||inflight)return;
  const ticket=epoch;inflight=true;node('load-refresh').disabled=true;
  try{const d=await api('/api/load');if(enabled&&ticket===epoch&&!document.hidden)render(d);}
  catch(e){if(enabled&&ticket===epoch)state(last?'数据已过期':'连接失败','负载刷新失败，当前展示上次成功数据，请重试。');}
  finally{inflight=false;node('load-refresh').disabled=false;schedule();}
 }
 window.poolLoadView=function(on){enabled=on;epoch++;clearTimeout(timer);if(on){state(last?'正在更新':'正在连接',null);refresh();}};
 document.addEventListener('visibilitychange',()=>{epoch++;clearTimeout(timer);if(enabled&&!document.hidden)refresh();});
 node('load-refresh').onclick=()=>{clearTimeout(timer);refresh();};
 window.addEventListener('pagehide',()=>{enabled=false;epoch++;clearTimeout(timer);});
})();
