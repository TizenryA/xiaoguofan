"""Run only against isolated release install. Never supplies Google credentials."""
import hashlib
import http.cookiejar
import json
import subprocess
import sys
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

assert request('/health')['version']=='0.9.6'
password=json.loads((data/'credentials.json').read_text())['password']
assert request('/api/auth/admin',{'password':password})
load=request('/api/load');assert load['available'] and load['memory']['limit_bytes']==2147483648
assert request('/api/admin/stats')['totals']['registered_users']==0
assert request('/api/admin/settings')['public_login'] is False
slice=Path('/sys/fs/cgroup/xiaoguofan.slice');assert (slice/'memory.max').read_text().strip()=='2147483648'
assert (slice/'memory.swap.max').read_text().strip()=='0'
uid='c'*32;token='d'*32
try:
    result=broker('ensure',{'uid':uid,'accounts':[],'limit':100,'lease':token});assert result['lease']==token
    labels=json.loads(subprocess.check_output(['docker','inspect','asp-'+uid],text=True))[0]
    assert labels['Config']['Labels']['xiaoguofan.instance']==c['instance']
    assert labels['HostConfig']['CgroupParent']=='xiaoguofan.slice'
    assert labels['HostConfig']['Memory']==labels['HostConfig']['MemorySwap']==650*1024**2
    assert any(slice.glob('docker-*.scope'))
    assert broker('load',{})['pool']['active']==1
    broker('release',{'uid':uid,'lease':token});assert broker('load',{})['pool']['active']==0
finally:
    broker('release',{'uid':uid,'lease':token});broker('stop',{'uid':uid})
key=hashlib.sha256((data/'master.key').read_bytes()).hexdigest();(root/'smoke-key-hash').write_text(key)
print('RELEASE_REAL_INSTALL_LOGIN_STATS_LOAD_WORKER_SHARED_BUDGET_PASS',c['mode'],load['memory']['used_bytes'])
