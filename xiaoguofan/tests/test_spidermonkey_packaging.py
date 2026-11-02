"""SpiderMonkey releases must keep cgo and pinned musl toolchains."""
import importlib.util
from pathlib import Path

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


def test_worker_image_has_native_browser_runtime():
    text = P.with_name('Dockerfile.worker').read_text()
    assert 'xvfb' in text
    assert 'build-binaries/linux-${TARGETARCH}/aistudio2api' in text
