"""Portable service entry, safe online backup, and scoped worker cleanup."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
from pathlib import Path


def config():
    return json.loads(Path(os.environ.get('XGF_CONFIG', str(Path(__file__).parent/'config.json'))).read_text())


def backup(c):
    root=Path(c['root']);data=Path(c['data']);out=data/'backups'/('release-'+str(time.time_ns()))
    out.mkdir(parents=True,mode=0o700)
    db=data/'portal.db'
    if db.exists():
        with sqlite3.connect(db) as source,sqlite3.connect(out/'portal.db') as dest:
            source.backup(dest)
            assert dest.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    for p in data.iterdir():
        if p.name in ('backups','portal.db','portal.db-wal','portal.db-shm'):continue
        if p.is_file():shutil.copy2(p,out/p.name)
    with tarfile.open(out/'deployment.tar.gz','w:gz') as archive:
        for name in ['app','config.json','runtime.py','compose.yaml','runtime.env']:
            p=root/name
            if p.exists():archive.add(p,arcname=name,filter=lambda info:None if '.venv' in info.name.split('/') else info)
        for name in ['xiaoguofan-portal.service','xiaoguofan-broker.service','xiaoguofan-compose.service','xiaoguofan.slice']:
            p=Path('/etc/systemd/system')/name
            if p.exists():archive.add(p,arcname='systemd/'+name)
    print('BACKUP_OK',out)
    return out


def cleanup(c):
    # New installs label every worker with this instance; never remove another app's containers.
    ids=subprocess.check_output(['docker','ps','-aq','--filter','label=xiaoguofan.instance='+c['instance']],text=True).split()
    if ids:subprocess.run(['docker','rm','-f',*ids],check=True,stdout=subprocess.DEVNULL)


def launch(c,mode):
    os.umask(0o077)
    creds=json.loads((Path(c['data'])/'credentials.json').read_text())
    os.environ.update(PORTAL_DATA=c['data'],PORTAL_ORIGIN=c['origin'],PORTAL_BROKER=c['socket'],
        PORTAL_WORKER_IMAGE=c['worker_image'],PORTAL_INSTANCE=c['instance'],
        PORTAL_RESOURCE_ENFORCED='1',PORTAL_CGROUP_ROOT='/sys/fs/cgroup/xiaoguofan.slice',
        PORTAL_ADMIN_PASSWORD=creds['password'])
    if mode=='broker':
        os.umask(0o007)
        args=['portal.workers:create_broker','--uds',c['socket']]
    else:args=['portal.main:create_app','--host','127.0.0.1','--port',str(c['port'])]
    os.execv(sys.executable,[sys.executable,'-m','uvicorn',*args,'--factory','--no-access-log','--no-proxy-headers'])


if __name__=='__main__':
    c=config();mode=sys.argv[1]
    if mode=='backup':backup(c)
    elif mode=='cleanup':cleanup(c)
    elif mode in ('portal','broker'):launch(c,mode)
    else:raise SystemExit('invalid mode')
