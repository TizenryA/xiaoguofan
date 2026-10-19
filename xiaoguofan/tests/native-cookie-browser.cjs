const{chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright-core'),assert=require('node:assert/strict');
const raw='SID=ISOLATED-SID; SAPISID=ISOLATED-SAP; __Secure-1PAPISID=ISOLATED-1P; __Secure-3PAPISID=ISOLATED-3P';
(async()=>{const b=await chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox']});try{for(const width of [320,393,1440]){for(const mode of ['lite','admin']){
 const c=await b.newContext({viewport:{width,height:950}}),p=await c.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));
 await p.goto('http://127.0.0.1:8098/');await p.evaluate(async()=>await fetch('/fixture/login',{method:'POST'}));await p.goto('http://127.0.0.1:8098/admin');await p.locator('#workspace').waitFor();
 // Empty existing fixture accounts, so each case proves exactly one creation.
 await p.evaluate(async()=>{const me=await(await fetch('/api/me')).json();for(const a of await(await fetch('/api/accounts')).json())await fetch('/api/accounts/'+a.id,{method:'DELETE',headers:{'X-CSRF-Token':me.csrf}})});
 if(mode==='lite'){await p.goto('http://127.0.0.1:8098/#upload');await p.locator('#workspace').waitFor()}else await p.locator('[data-tab=accounts]').click();
 await p.locator('#native-cookie-details > summary').click();assert(await p.locator('#native-cookie').isVisible());
 await p.locator('#native-cookie').fill(raw);await p.locator('#native-consent').check();await p.locator('#native-verify').click();await p.locator('#native-confirm').waitFor();assert.equal(await p.locator('#native-cookie').inputValue(),'');
 assert.equal(await p.locator('#native-account option').count(),2);await p.locator('#native-account').selectOption('1');
 assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,'horizontal overflow');
 await p.screenshot({path:require('path').join(process.env.ARTIFACT_DIR||require('os').tmpdir(),'原生Cookie导入-'+mode+'-'+width+'.png'),fullPage:true});
 const wait=p.waitForResponse(r=>r.url().endsWith('/api/accounts/native/import')&&r.request().method()==='POST');await p.locator('#native-save').click();assert.equal((await wait).status(),201);
 await p.locator('#account-list').getByText(mode==='lite'?'second@example.com':'我的专用账号',{exact:true}).waitFor();
 const accounts=await p.evaluate(async()=>await(await fetch('/api/accounts')).json());assert.equal(accounts.length,1);assert.equal(accounts[0].email,'second@example.com');
 assert.deepEqual(await p.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);assert.deepEqual(errors,[]);
 console.log('NATIVE_COOKIE_REAL_BROWSER_VERIFY_SELECT_ENCRYPTED_SAVE_PASS',mode,width);await c.close();
 }}}finally{await b.close()}})().catch(e=>{console.error(e);process.exit(1)});
