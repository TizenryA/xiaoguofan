"""Build release packages on the explicitly selected builder, never production."""
import argparse
import gzip
import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION = '0.9.15'
OUT = ROOT / 'dist/xiaoguofan'
SPIDERMONKEY = 'v152.0.4-aistudio2api.1'
ZIG_VERSION = '0.16.0'
ZIG_SHA256 = '70e49664a74374b48b51e6f3fdfbf437f6395d42509050588bd49abe52ba3d00'
SM_SHA256 = {
    'amd64': 'd3ecebf1890355426ad261ab8723038849a524b348af53e1bb440ca53cc2cacb',
    'arm64': 'b3c9539b8d8378d99f86b267e0e3d007dd01ff80acebf4f52f3e1d6ac04d9d00',
}
LINK_FLAGS = ("-s -w -linkmode external -extldflags '-static -Wl,--strip-all' "
              f'-X github.com/Mag1cFall/AIStudio2API/internal/app.version=xiaoguofan-v{VERSION}')
TEST_LINK_FLAGS = "-linkmode external -extldflags '-static'"


def run(*args, **kw):
    kw.setdefault('cwd', ROOT)
    print('+', shlex.join(map(str, args)), flush=True)
    return subprocess.run(list(args), check=True, **kw)


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download_extract(url, digest, dest):
    """Verify official release bytes before unpacking; cache only complete trees."""
    marker = dest / '.verified-sha256'
    if marker.exists() and marker.read_text().strip() == digest:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    archive = dest.with_suffix('.download')
    try:
        with urllib.request.urlopen(url, timeout=180) as response, archive.open('wb') as out:
            shutil.copyfileobj(response, out)
        if sha256(archive) != digest:
            raise RuntimeError('Toolchain SHA256 mismatch: ' + url)
        stage = dest.with_name(dest.name + '.extracting')
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(stage, filter='data')
        if dest.exists():
            shutil.rmtree(dest)
        stage.rename(dest)
        marker.write_text(digest + '\n')
    finally:
        archive.unlink(missing_ok=True)


def prepare_toolchains(directory, arches):
    # This release recipe is run on the designated Linux x86_64 builder.
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'AMD64'):
        raise RuntimeError('Run packaging on a Linux x86_64 build host')
    zig_name = f'zig-x86_64-linux-{ZIG_VERSION}'
    download_extract(f'https://ziglang.org/download/{ZIG_VERSION}/{zig_name}.tar.xz',
                     ZIG_SHA256, directory / 'zig')
    for arch in arches:
        name = f'spidermonkey-{SPIDERMONKEY}-linux-{arch}.tar.gz'
        download_extract(f'https://github.com/Mag1cFall/firefox/releases/download/{SPIDERMONKEY}/{name}',
                         SM_SHA256[arch], directory / 'spidermonkey' / f'linux-{arch}')


def build_env(arch, directory):
    target = {'amd64': 'x86_64', 'arm64': 'aarch64'}[arch]
    zig = shlex.quote(str(directory / 'zig' / f'zig-x86_64-linux-{ZIG_VERSION}' / 'zig'))
    sm = directory / 'spidermonkey' / f'linux-{arch}'
    return os.environ | {
        'GOOS': 'linux', 'GOARCH': arch, 'CGO_ENABLED': '1', 'GOMAXPROCS': '2',
        'CC': f'{zig} cc -target {target}-linux-musl',
        'CXX': f'{zig} c++ -target {target}-linux-musl',
        'CGO_CXXFLAGS': '-I' + str(sm / 'include'),
        'CGO_LDFLAGS': '-L' + str(sm / 'lib'),
    }


def image_archive(tag, dest):
    p = subprocess.Popen(['docker', 'save', tag], stdout=subprocess.PIPE)
    assert p.stdout is not None
    try:
        with gzip.open(dest, 'wb', compresslevel=3) as out:
            shutil.copyfileobj(p.stdout, out)
    finally:
        p.stdout.close()
    if p.wait():
        raise RuntimeError('docker save failed')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--arch', choices=['amd64', 'arm64', 'all'], default='all')
    p.add_argument('--toolchain-dir', type=Path,
                   default=Path(os.environ.get('XGF_TOOLCHAIN_DIR', str(Path.home() / '.cache/xiaoguofan/toolchains'))))
    p.add_argument('--prepare-only', action='store_true')
    a = p.parse_args()
    arches = ['amd64', 'arm64'] if a.arch == 'all' else [a.arch]
    prepare_toolchains(a.toolchain_dir, ['amd64', *[x for x in arches if x != 'amd64']])
    if a.prepare_only:
        print('PINNED_TOOLCHAINS_READY', a.toolchain_dir, flush=True)
        return
    OUT.mkdir(parents=True, exist_ok=True)
    run('npm', 'ci', cwd=ROOT / 'web')
    run('npm', 'run', 'build', cwd=ROOT / 'web')
    run('go', 'test', '-mod=readonly', '-p=2', '-tags=netgo,osusergo',
        '-ldflags=' + TEST_LINK_FLAGS, './cmd/...', './internal/...',
        env=build_env('amd64', a.toolchain_dir))
    commit_file = ROOT / 'BUILD_SOURCE_COMMIT'
    commit = commit_file.read_text().strip() if commit_file.exists() else subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    files = []
    for arch in arches:
        binary = ROOT / 'build-binaries' / ('linux-' + arch)
        binary.mkdir(parents=True, exist_ok=True)
        run('go', 'build', '-mod=readonly', '-p=2', '-trimpath', '-tags=netgo,osusergo',
            '-ldflags=' + LINK_FLAGS, '-o', str(binary / 'aistudio2api'), './cmd/aistudio2api',
            env=build_env(arch, a.toolchain_dir))
        for kind in ['worker', 'portal']:
            tag = f'xiaoguofan-{kind}:{VERSION}'
            run('docker', 'buildx', 'build', '--network=host', '--platform', 'linux/' + arch,
                '--load', '-f', f'xiaoguofan/packaging/Dockerfile.{kind}', '-t', tag,
                '--label', 'org.opencontainers.image.revision=' + commit,
                '--label', 'org.opencontainers.image.version=' + VERSION, '.')
            image_archive(tag, OUT / f'{kind}-{arch}.tar.gz')
        for mode in ['linux', 'docker']:
            name = f'xiaoguofan-v{VERSION}-{mode}-{arch}'
            stage = OUT / name
            if stage.exists():
                shutil.rmtree(stage)
            (stage / 'images').mkdir(parents=True)
            for f in ['install.py', 'runtime.py', 'README.md']:
                shutil.copy2(ROOT / 'xiaoguofan/packaging' / f, stage / f)
            shutil.copy2(ROOT / 'LICENSE', stage / 'LICENSE')
            app = stage / 'app'
            app.mkdir()
            shutil.copytree(ROOT / 'xiaoguofan/portal', app / 'portal',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            for f in ['requirements.txt', 'pyproject.toml', 'uv.lock']:
                shutil.copy2(ROOT / 'xiaoguofan' / f, app / f)
            (stage / 'build-info.json').write_text(json.dumps({
                'version': VERSION, 'commit': commit, 'arch': arch, 'mode': mode,
                'spidermonkey': SPIDERMONKEY, 'zig': ZIG_VERSION,
            }, indent=2) + '\n')
            shutil.copy2(OUT / f'worker-{arch}.tar.gz', stage / 'images/worker.tar.gz')
            if mode == 'docker':
                shutil.copy2(OUT / f'portal-{arch}.tar.gz', stage / 'images/portal.tar.gz')
            archive = OUT / (name + '.tar.gz')
            with tarfile.open(archive, 'w:gz', compresslevel=3) as tar:
                tar.add(stage, arcname=name)
            files.append(archive)
            shutil.rmtree(stage)
        for kind in ['worker', 'portal']:
            (OUT / f'{kind}-{arch}.tar.gz').unlink()
    # Do not accidentally include stale archives from another release.
    (OUT / 'SHA256SUMS').write_text(''.join(sha256(f) + '  ' + f.name + '\n' for f in sorted(files)))
    print('RELEASE_PACKAGES_BUILT', *[f.name for f in files], flush=True)


if __name__ == '__main__':
    main()
