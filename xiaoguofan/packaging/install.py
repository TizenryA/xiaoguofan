"""Linux / Docker installer. No implicit firewall changes or public password logging."""
import argparse
import datetime
import json
import os
import platform
import pwd
import re
import secrets
import shutil
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

HERE=Path(__file__).resolve().parent
VERSION='0.9.1'


def run(*args,**kw):return subprocess.run(list(args),check=True,**kw)


def validate(origin,root,data,port,allow_http=False):
    u=urlsplit(origin)
    if u.scheme!='https' and not (allow_http and u.scheme=='http' and u.hostname in ('127.0.0.1','localhost')):raise ValueError('origin必须是HTTPS地址')
    if not u.hostname or u.username or u.password or u.query or u.fragment:raise ValueError('origin格式错误')
    if u.path.rstrip('/') and not re.fullmatch(r'/[A-Za-z0-9_-]+',u.path.rstrip('/')):raise ValueError('origin仅支持单段路径')
    for p in (root,data):
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+',p) or '..' in Path(p).parts or Path(p).is_symlink() or p=='/':raise ValueError('安装路径不安全')
    if Path(root)==Path(data) or Path(root) in Path(data).parents or Path(data) in Path(root).parents:raise ValueError('源码与数据目录必须分离')
    if not 1024<=port<=65535:raise ValueError('端口须在1024-65535之间')


def compose(c,uid,gid):
    common={'image':c['portal_image'],'network_mode':'host','cgroup_parent':'xiaoguofan.slice','cgroup':'host',
        'restart':'unless-stopped','read_only':True,'security_opt':['no-new-privileges:true'],'cap_drop':['ALL'],
        'tmpfs':['/tmp:rw,size=67108864'],'environment':{'XGF_CONFIG':'/config/config.json'},'pids_limit':160,'healthcheck':{'test':['CMD','python','-c','import os,pathlib,urllib.request,json;c=json.loads(pathlib.Path("/config/config.json").read_text());urllib.request.urlopen("http://127.0.0.1:"+str(c["port"])+"/health",timeout=2)'],'interval':'5s','timeout':'3s','retries':12,'start_period':'10s'}}
    # host networking is intentional: workers publish random loopback ports on the host.
    volumes=[c['root']+'/config.json:/config/config.json:ro',c['data']+':'+c['data'],c['socket_dir']+':'+c['socket_dir']]
    portal=common|{'command':['portal'],'user':f'{uid}:{gid}','volumes':volumes,'mem_limit':'200m','memswap_limit':'200m'}
    broker=common|{'command':['broker'],'user':f'{uid}:{gid}','group_add':[str(c['docker_gid'])],'volumes':volumes+['/var/run/docker.sock:/var/run/docker.sock','/sys/fs/cgroup:/sys/fs/cgroup:ro'],'mem_limit':'150m','memswap_limit':'150m'}
    # Both processes need read-only project metrics, but only broker has Docker privilege.
    portal['volumes']=volumes+['/sys/fs/cgroup:/sys/fs/cgroup:ro']
    return {'name':'xiaoguofan-release','services':{'broker':broker,'portal':portal}}


def units(c):
    common=f'''Slice=xiaoguofan.slice
WorkingDirectory={c['root']}/app
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=XGF_CONFIG={c['root']}/config.json
Restart=on-failure
RestartSec=3
MemorySwapMax=0
NoNewPrivileges=true
ProtectHome=true
UMask=0077
'''
    broker=f'''[Unit]
Description=Xiaoguofan worker broker
After=docker.service
Requires=docker.service
[Service]
User=root
Group=xiaoguofan
RuntimeDirectory=xiaoguofan-release
RuntimeDirectoryMode=0770
ExecStartPre={c['python']} {c['root']}/runtime.py cleanup
ExecStart={c['python']} {c['root']}/runtime.py broker
ExecStopPost={c['python']} {c['root']}/runtime.py cleanup
MemoryMax=150M
'''+common.replace('UMask=0077','UMask=0007')+'''[Install]
WantedBy=multi-user.target
'''
    portal=f'''[Unit]
Description=Xiaoguofan BYOA portal
After=xiaoguofan-broker.service
Requires=xiaoguofan-broker.service
[Service]
User=xiaoguofan
Group=xiaoguofan
ExecStart={c['python']} {c['root']}/runtime.py portal
MemoryMax=200M
ProtectSystem=strict
ReadWritePaths={c['data']} {c['socket_dir']}
PrivateTmp=true
'''+common+'''[Install]
WantedBy=multi-user.target
'''
    start=f'/usr/bin/docker compose --env-file {c["root"]}/runtime.env -f {c["root"]}/compose.yaml'
    docker=f'''[Unit]
Description=Xiaoguofan Docker deployment
After=docker.service
Requires=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/usr/bin/python3 {c['root']}/runtime.py cleanup
ExecStartPre=/usr/bin/install -d -m 0770 -o root -g xiaoguofan {c['socket_dir']}
ExecStart={start} up -d --wait --wait-timeout 90
ExecStop={start} down
ExecStopPost=/usr/bin/python3 {c['root']}/runtime.py cleanup
[Install]
WantedBy=multi-user.target
'''
    return {'xiaoguofan-broker.service':broker,'xiaoguofan-portal.service':portal} if c['mode']=='linux' else {'xiaoguofan-compose.service':docker}


def main():
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['linux','docker'],required=True);p.add_argument('--origin',required=True)
    p.add_argument('--root',default='/opt/xiaoguofan-release');p.add_argument('--data',default='/var/lib/xiaoguofan');p.add_argument('--port',type=int,default=8890)
    p.add_argument('--upgrade',action='store_true');p.add_argument('--allow-local-http',action='store_true');a=p.parse_args()
    validate(a.origin,a.root,a.data,a.port,a.allow_local_http)
    assert os.geteuid()==0,'必须root运行'
    assert Path('/run/systemd/system').exists() and Path('/sys/fs/cgroup/cgroup.controllers').exists(),'需要systemd与cgroup v2'
    info=json.loads(subprocess.check_output(['docker','info','--format','{{json .}}'],text=True));assert info['CgroupDriver']=='systemd' and info['CgroupVersion']=='2','Docker需要systemd cgroup v2'
    for old in ['aistudio-portal','aistudio-portal-broker']:
        assert subprocess.run(['systemctl','is-active','--quiet',old]).returncode!=0,'检测到活动旧门户，先完整迁移'
    root=Path(a.root);data=Path(a.data);old=None
    arch={'x86_64':'amd64','aarch64':'arm64'}[platform.machine()]
    assert json.loads((HERE/'build-info.json').read_text())['arch']==arch,'架构包不匹配'
    assert (HERE/'images/worker.tar.gz').exists()
    if a.mode=='linux':
        assert shutil.which('uv') and shutil.which('node'),'请先安装uv和Node18+'
        assert int(subprocess.check_output(['node','--version'],text=True).strip().lstrip('v').split('.')[0])>=18
    else:
        run('docker','compose','version',stdout=subprocess.DEVNULL)
        assert (HERE/'images/portal.tar.gz').exists()
    if root.joinpath('config.json').exists():
        old=json.loads((root/'config.json').read_text());assert a.upgrade,'已有安装，升级须加--upgrade'
        assert (old['mode'],old['data'],old['port'])==(a.mode,a.data,a.port),'升级不能改变模式/数据目录/端口'
        run('/usr/bin/python3',str(root/'runtime.py'),'backup')
        stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        rollback=root.parent/(root.name+'-rollback-'+stamp);shutil.copytree(root,rollback,ignore=shutil.ignore_patterns('.venv','rollback-*'))
        run('systemctl','stop',*(['xiaoguofan-portal','xiaoguofan-broker'] if a.mode=='linux' else ['xiaoguofan-compose']))
    else:
        for existing in ['xiaoguofan-portal','xiaoguofan-broker','xiaoguofan-compose']:
            assert subprocess.run(['systemctl','is-active','--quiet',existing]).returncode!=0,'另一套小锅饭发行安装正在运行，拒绝覆盖'
        assert not data.exists() or not any(data.iterdir()),'数据目录非空，拒绝覆盖既有数据'
        assert not root.exists() or not any(root.iterdir()),'安装目录非空，拒绝覆盖'
        with socket.socket() as probe:probe.bind(('127.0.0.1',a.port))
        assert not subprocess.check_output(['docker','ps','-aq','--filter','label=aistudio-portal.worker=1'],text=True).strip(),'旧动态池存在，请先完整迁移'
    if a.mode=='linux':
        assert shutil.which('uv') and shutil.which('node'),'请先安装uv和Node18+'
        assert int(subprocess.check_output(['node','--version'],text=True).strip().lstrip('v').split('.')[0])>=18
    else:run('docker','compose','version',stdout=subprocess.DEVNULL)
    arch={'x86_64':'amd64','aarch64':'arm64'}[platform.machine()];assert json.loads((HERE/'build-info.json').read_text())['arch']==arch,'架构包不匹配'
    assert (HERE/'images/worker.tar.gz').exists()
    if a.mode=='docker':assert (HERE/'images/portal.tar.gz').exists()
    os.umask(0o077)
    root.mkdir(parents=True,exist_ok=True);root.chmod(0o755);data.mkdir(parents=True,exist_ok=True);data.chmod(0o700)
    try:pw=pwd.getpwnam('xiaoguofan')
    except KeyError:run('useradd','--system','--home-dir',str(data),'--shell','/usr/sbin/nologin','xiaoguofan');pw=pwd.getpwnam('xiaoguofan')
    creds=data/'credentials.json'
    if not creds.exists():creds.write_text(json.dumps({'password':secrets.token_urlsafe(32)}));creds.chmod(0o600)
    for path,dirs,files in os.walk(data):
        os.chown(path,pw.pw_uid,pw.pw_gid)
        for f in files:os.chown(Path(path)/f,pw.pw_uid,pw.pw_gid)
    if (root/'app').exists():shutil.rmtree(root/'app')
    shutil.copytree(HERE/'app',root/'app');shutil.copy2(HERE/'runtime.py',root/'runtime.py')
    # Readable code/venv does not contain credentials; data and config remain restricted.
    for path,dirs,files in os.walk(root/'app'):
        os.chmod(path,0o755)
        for f in files:os.chmod(Path(path)/f,0o644)
    c={'root':str(root),'data':str(data),'mode':a.mode,'origin':a.origin.rstrip('/'),'port':a.port,
       'socket_dir':'/run/xiaoguofan-release','socket':'/run/xiaoguofan-release/broker.sock',
       'worker_image':'xiaoguofan-worker:'+VERSION,'portal_image':'xiaoguofan-portal:'+VERSION,
       'docker_gid':Path('/var/run/docker.sock').stat().st_gid,
       'instance':old['instance'] if old else secrets.token_hex(16),'python':str(root/'app/.venv/bin/python')}
    (root/'config.json').write_text(json.dumps(c));os.chown(root/'config.json',0,pw.pw_gid);(root/'config.json').chmod(0o640);(root/'runtime.py').chmod(0o644)
    run('docker','load','-i',str(HERE/'images/worker.tar.gz'),stdout=subprocess.DEVNULL)
    if a.mode=='linux':
        env=os.environ|{'UV_PYTHON_INSTALL_DIR':str(root/'python'),'UV_CACHE_DIR':str(root/'uv-cache')}
        run('uv','venv','--python','3.12','--managed-python',str(root/'app/.venv'),env=env)
        run('uv','pip','install','--python',c['python'],'-r',str(root/'app/requirements.txt'),env=env)
        for name in ['python','app/.venv']:
            for path,dirs,files in os.walk(root/name):
                os.chmod(path,0o755)
                for f in files:
                    item=Path(path)/f
                    if not item.is_symlink():item.chmod(0o755 if item.stat().st_mode&0o100 else 0o644)
    else:
        run('docker','load','-i',str(HERE/'images/portal.tar.gz'),stdout=subprocess.DEVNULL)
        (root/'compose.yaml').write_text(json.dumps(compose(c,pw.pw_uid,pw.pw_gid),indent=2))
        (root/'runtime.env').write_text('COMPOSE_PROJECT_NAME=xiaoguofan-release\n')
    budget='''[Unit]\nDescription=Xiaoguofan project total budget\n[Slice]\nMemoryAccounting=true\nCPUAccounting=true\nMemoryHigh=1792M\nMemoryMax=2G\nMemorySwapMax=0\n[Install]\nWantedBy=multi-user.target\n'''
    Path('/etc/systemd/system/xiaoguofan.slice').write_text(budget)
    for name,text in units(c).items():Path('/etc/systemd/system',name).write_text(text)
    run('systemctl','daemon-reload');run('systemctl','enable','--now','xiaoguofan.slice')
    run('systemctl','enable','--now',*(['xiaoguofan-broker','xiaoguofan-portal'] if a.mode=='linux' else ['xiaoguofan-compose']))
    import time
    import urllib.request
    for i in range(30):
        try:
            with urllib.request.urlopen('http://127.0.0.1:'+str(a.port)+'/health',timeout=2) as response:assert json.load(response)['status']=='ok'
            break
        except Exception:
            if i==29:raise
            time.sleep(1)
    print('INSTALL_VERIFIED',a.mode,'loopback:'+str(a.port),'password_file='+str(creds))


if __name__=='__main__':main()
