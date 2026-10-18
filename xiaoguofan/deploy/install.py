import json
import os
import pathlib
import secrets
import subprocess

ROOT=pathlib.Path('/data/aistudio-portal')
SOURCE=pathlib.Path('/data/aistudio-portal-src')
ROOT.mkdir(mode=0o700,exist_ok=True)
SOURCE.mkdir(mode=0o755,exist_ok=True)
backup=ROOT/'backups'
backup.mkdir(exist_ok=True,mode=0o700)
# No database should be copied while WAL writes are active; use SQLite's backup API on upgrades.
if (ROOT/'portal.db').exists():
    import sqlite3,time
    src=sqlite3.connect(ROOT/'portal.db')
    dst=sqlite3.connect(backup/(str(int(time.time()))+'.db'))
    src.backup(dst);dst.close();src.close()
    key=ROOT/'master.key'
    if key.exists():
        import shutil
        shutil.copy2(key,backup/'master.key')
if subprocess.run(['id','aistudio-portal'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
    subprocess.run(['useradd','--system','--home-dir',str(ROOT),'--shell','/usr/sbin/nologin','aistudio-portal'],check=True)
cred=ROOT/'credentials.json'
if not cred.exists():
    cred.write_text(json.dumps({'username':'admin','password':secrets.token_urlsafe(28)},indent=2))
    cred.chmod(0o600)
p=json.loads(cred.read_text())
originfile=ROOT/'origin'
origin=originfile.read_text().strip() if originfile.exists() else 'http://127.0.0.1:8088'
env=ROOT/'portal.env'
env.write_text('PORTAL_DATA='+str(ROOT)+'\nPORTAL_ADMIN_PASSWORD='+p['password']+'\nPORTAL_ORIGIN='+origin+'\nPORTAL_BROKER=/run/aistudio-portal/broker.sock\n')
env.chmod(0o600)
subprocess.run(['chown','-R','aistudio-portal:aistudio-portal',str(ROOT)],check=True)
subprocess.run(['python3','-m','venv',str(SOURCE/'.venv')],check=True)
subprocess.run([str(SOURCE/'.venv/bin/pip'),'install','--disable-pip-version-check','-r',str(SOURCE/'requirements.txt')],check=True)
unit='''[Unit]
Description=AI Studio multi-tenant portal
After=network-online.target aistudio-portal-broker.service
Requires=aistudio-portal-broker.service
[Service]
User=aistudio-portal
Group=aistudio-portal
WorkingDirectory=/data/aistudio-portal-src
EnvironmentFile=/data/aistudio-portal/portal.env
ExecStart=/data/aistudio-portal-src/.venv/bin/uvicorn portal.main:create_app --factory --host 0.0.0.0 --port 8088 --no-access-log --no-proxy-headers
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/data/aistudio-portal /run/aistudio-portal
ProtectHome=true
PrivateTmp=true
MemoryMax=400M
CPUQuota=100%
[Install]
WantedBy=multi-user.target
'''
broker='''[Unit]
Description=Restricted AI Studio tenant-worker broker
After=docker.service
Requires=docker.service
[Service]
User=root
Group=aistudio-portal
WorkingDirectory=/data/aistudio-portal-src
Environment=PORTAL_DATA=/data/aistudio-portal
RuntimeDirectory=aistudio-portal
RuntimeDirectoryMode=0770
ExecStartPre=/bin/sh -c 'docker ps -aq --filter label=aistudio-portal.worker=1 | xargs -r docker rm -f'
ExecStart=/data/aistudio-portal-src/.venv/bin/uvicorn portal.workers:create_broker --factory --uds /run/aistudio-portal/broker.sock --no-access-log --no-proxy-headers
Restart=on-failure
RestartSec=3
UMask=0007
NoNewPrivileges=true
ProtectHome=true
MemoryMax=200M
[Install]
WantedBy=multi-user.target
'''
pathlib.Path('/etc/systemd/system/aistudio-portal.service').write_text(unit)
pathlib.Path('/etc/systemd/system/aistudio-portal-broker.service').write_text(broker)
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','--now','aistudio-portal-broker','aistudio-portal'],check=True)
print('Portal services enabled; credentials retained in restricted file')
