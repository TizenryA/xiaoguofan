"""Run only against isolated release install. Never supplies Google credentials."""
import hashlib
import http.cookiejar
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

root=Path(sys.argv[1]);c=json.loads((root/'config.json').read_text());data=Path(c['data']);base='http://127.0.0.1:'+str(c['port'])
cookies=http.cookiejar.CookieJar();opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))

def request(path,body=None):
    headers={'Content-Type':'application/json','Origin':base}
    r=opener.open(urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers),timeout=10)
    return json.load(r)


def broker(path,body):
    out=subprocess.check_output(['curl','--fail','--silent','--show-error','--max-time','90','--unix-socket',c['socket'],'-H','Content-Type: application/json','-d',json.dumps(body),'http://broker/'+path],text=True)
    return json.loads(out)

assert request('/health')['version']=='1.0.0'
password=json.loads((data/'credentials.json').read_text())['password']
assert request('/api/auth/admin',{'password':password})
assert request('/api/admin/stats')['totals']['registered_users']==0
assert request('/api/admin/settings')['public_login'] is False
slice=Path('/sys/fs/cgroup/xiaoguofan.slice');assert (slice/'memory.max').read_text().strip()
# One shared engine container, inside the project slice, loopback only.
info=broker('engine',{});assert info['url'].startswith('http://127.0.0.1:')
labels=json.loads(subprocess.check_output(['docker','inspect','xiaoguofan-engine'],text=True))[0]
assert labels['Config']['Labels'].get('xiaoguofan.engine')=='1'
assert labels['HostConfig']['CgroupParent']=='xiaoguofan.slice'
assert 'XGF_ACCOUNT_SCOPE=required' in labels['Config']['Env']
# Without the trusted scope header the engine must refuse generation requests.
req=urllib.request.Request(info['url']+'/v1/chat/completions',data=b'{"model":"x","messages":[]}',headers={'Authorization':'Bearer '+info['key'],'Content-Type':'application/json'})
try:
    urllib.request.urlopen(req,timeout=10);raise SystemExit('engine accepted a request without account scope')
except urllib.error.HTTPError as exc:
    assert exc.code==403,exc.code
load=broker('load',{});assert load['engine']['running']
key=hashlib.sha256((data/'master.key').read_bytes()).hexdigest();(root/'smoke-key-hash').write_text(key)
print('RELEASE_REAL_INSTALL_LOGIN_STATS_SHARED_ENGINE_SCOPE_PASS',c['mode'],load['memory']['used_bytes'])
