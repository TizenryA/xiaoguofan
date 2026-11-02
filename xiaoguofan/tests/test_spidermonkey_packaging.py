"""SpiderMonkey releases must keep cgo and pinned musl toolchains."""
import hashlib
import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

P = Path(__file__).resolve().parents[1] / 'packaging/build.py'
spec = importlib.util.spec_from_file_location('xgf_build', P)
assert spec is not None and spec.loader is not None
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_linux_build_uses_static_cgo_for_each_arch(tmp_path):
    for arch, target in [('amd64', 'x86_64'), ('arm64', 'aarch64')]:
        env = builder.build_env(arch, tmp_path)
        assert env['CGO_ENABLED'] == '1'
        assert env['GOARCH'] == arch and env['GOOS'] == 'linux'
        assert f'{target}-linux-musl' in env['CC']
        assert f'{target}-linux-musl' in env['CXX']
        assert f'/spidermonkey/linux-{arch}/include' in env['CGO_CXXFLAGS']
        assert f'/spidermonkey/linux-{arch}/lib' in env['CGO_LDFLAGS']
    assert '-static' in builder.LINK_FLAGS
    assert 'internal/app.version=' in builder.LINK_FLAGS


def test_toolchain_downloads_are_pinned_and_hashed():
    assert builder.SPIDERMONKEY == 'v152.0.4-aistudio2api.1'
    assert builder.ZIG_VERSION == '0.16.0'
    assert set(builder.SM_SHA256) == {'amd64', 'arm64'}
    assert all(len(h) == 64 for h in builder.SM_SHA256.values())
    assert len(builder.ZIG_SHA256) == 64


def test_worker_runtime_selects_supported_backend():
    text = (P.parents[1] / 'portal/workers.py').read_text()
    assert 'WAA_BACKEND=spidermonkey' in text
    assert 'WAA_BACKEND=go' not in text


def _official_archive(payload=b'official'):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        info = tarfile.TarInfo('tool/payload.txt')
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return stream.getvalue()


class _UrlopenResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


def test_download_extract_rebuilds_marked_tampered_cache(tmp_path, monkeypatch):
    archive = _official_archive()
    digest = hashlib.sha256(archive).hexdigest()
    dest = tmp_path / 'toolchain'
    dest.mkdir()
    (dest / '.verified-sha256').write_text(digest + chr(10))
    (dest / 'tool').mkdir()
    (dest / 'tool/payload.txt').write_text('tampered')
    calls = []

    def urlopen(url, timeout):
        calls.append((url, timeout))
        return _UrlopenResponse(archive)

    monkeypatch.setattr(builder.urllib.request, 'urlopen', urlopen)
    builder.download_extract('https://official.example/tool.tar.gz', digest, dest)

    assert calls == [('https://official.example/tool.tar.gz', 180)]
    assert (dest / 'tool/payload.txt').read_bytes() == b'official'
    assert (dest / '.verified-sha256').read_text() == digest + chr(10)


def test_download_extract_rejects_wrong_archive_hash(tmp_path, monkeypatch):
    archive = _official_archive(b'untrusted')
    monkeypatch.setattr(
        builder.urllib.request,
        'urlopen',
        lambda url, timeout: _UrlopenResponse(archive),
    )

    with pytest.raises(RuntimeError, match='Toolchain SHA256 mismatch'):
        builder.download_extract('https://official.example/tool.tar.gz', '0' * 64, tmp_path / 'toolchain')

    assert not (tmp_path / 'toolchain').exists()


def test_worker_image_has_native_browser_runtime():
    text = P.with_name('Dockerfile.worker').read_text()
    assert 'xvfb' in text
    assert 'build-binaries/linux-${TARGETARCH}/aistudio2api' in text
