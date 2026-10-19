"""Build release packages on the explicitly selected builder, never production."""
import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
VERSION='0.9.6'
OUT=ROOT/'dist/xiaoguofan'


def run(*args,**kw):
    kw.setdefault('cwd',ROOT)
    return subprocess.run(list(args),check=True,**kw)


def image_archive(tag,dest):
    # Docker load supports gzip-compressed image archives.
    p=subprocess.Popen(['docker','save',tag],stdout=subprocess.PIPE)
    with gzip.open(dest,'wb',compresslevel=3) as out:shutil.copyfileobj(p.stdout,out)
    if p.wait():raise RuntimeError('docker save failed')


def main():
    p=argparse.ArgumentParser();p.add_argument('--arch',choices=['amd64','arm64','all'],default='all');a=p.parse_args()
    arches=['amd64','arm64'] if a.arch=='all' else [a.arch]
    OUT.mkdir(parents=True,exist_ok=True)
    run('npm','ci',cwd=ROOT/'web');run('npm','run','build',cwd=ROOT/'web')
    run('go','test','-p=2','./cmd/...','./internal/...',env=os.environ|{'GOMAXPROCS':'2'})
    commit=(ROOT/'BUILD_SOURCE_COMMIT').read_text().strip() if (ROOT/'BUILD_SOURCE_COMMIT').exists() else subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    for arch in arches:
        binary=ROOT/'build-binaries'/('linux-'+arch);binary.mkdir(parents=True,exist_ok=True)
        run('go','build','-p=2','-trimpath','-ldflags=-s -w','-o',str(binary/'aistudio2api'),'./cmd/aistudio2api',env=os.environ|{'GOOS':'linux','GOARCH':arch,'CGO_ENABLED':'0','GOMAXPROCS':'2'})
        for kind in ['worker','portal']:
            tag=f'xiaoguofan-{kind}:{VERSION}'
            run('docker','buildx','build','--platform','linux/'+arch,'--load','-f',f'xiaoguofan/packaging/Dockerfile.{kind}','-t',tag,'--label','org.opencontainers.image.revision='+commit,'--label','org.opencontainers.image.version='+VERSION,'.')
            dest=OUT/f'{kind}-{arch}.tar.gz';image_archive(tag,dest)
        for mode in ['linux','docker']:
            name=f'xiaoguofan-v{VERSION}-{mode}-{arch}';stage=OUT/name
            if stage.exists():shutil.rmtree(stage)
            (stage/'images').mkdir(parents=True)
            for f in ['install.py','runtime.py','README.md']:shutil.copy2(ROOT/'xiaoguofan/packaging'/f,stage/f)
            shutil.copy2(ROOT/'LICENSE',stage/'LICENSE')
            app=stage/'app';app.mkdir()
            shutil.copytree(ROOT/'xiaoguofan/portal',app/'portal',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
            for f in ['requirements.txt','pyproject.toml','uv.lock']:shutil.copy2(ROOT/'xiaoguofan'/f,app/f)
            (stage/'build-info.json').write_text(json.dumps({'version':VERSION,'commit':commit,'arch':arch,'mode':mode},indent=2)+'\n')
            shutil.copy2(OUT/f'worker-{arch}.tar.gz',stage/'images/worker.tar.gz')
            if mode=='docker':shutil.copy2(OUT/f'portal-{arch}.tar.gz',stage/'images/portal.tar.gz')
            with tarfile.open(OUT/(name+'.tar.gz'),'w:gz',compresslevel=3) as t:t.add(stage,arcname=name)
            shutil.rmtree(stage)
        for kind in ['worker','portal']:(OUT/f'{kind}-{arch}.tar.gz').unlink()
    files=sorted(OUT.glob('xiaoguofan-*.tar.gz'))
    (OUT/'SHA256SUMS').write_text(''.join(hashlib.sha256(f.read_bytes()).hexdigest()+'  '+f.name+'\n' for f in files))
    print('RELEASE_PACKAGES_BUILT',*[f.name for f in files])


if __name__=='__main__':main()
