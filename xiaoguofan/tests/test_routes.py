"""Access page lists the original and the optimized (relay) entry; the relay origin is configurable."""
import pytest
from fastapi.testclient import TestClient

from portal.main import create_app


def config(tmp_path, monkeypatch, value=None):
    if value is None:
        monkeypatch.delenv('PORTAL_OPTIMIZED_ORIGIN', raising=False)
    else:
        monkeypatch.setenv('PORTAL_OPTIMIZED_ORIGIN', value)
    app = create_app(tmp_path, 'test-password-long-enough', 'https://aistudio.tizenty.xyz/lite')
    with TestClient(app, base_url='https://aistudio.tizenty.xyz') as c:
        return c.get('/lite/api/config').json()


def test_default_lists_optimized_first_then_original(tmp_path, monkeypatch):
    cfg = config(tmp_path, monkeypatch)
    assert [(r['id'], r['base']) for r in cfg['routes']] == [
        ('optimized', 'https://xiaoguofanapi.tizenty.xyz/v1'), ('original', 'https://aistudio.tizenty.xyz/lite/v1')]
    assert cfg['routes'][0]['badge'] == '国内推荐' and cfg['routes'][1]['root'] == 'https://aistudio.tizenty.xyz/lite'
    assert cfg['api_base'] == 'https://aistudio.tizenty.xyz/lite/v1'


def test_optimized_origin_is_configurable_or_hidden(tmp_path, monkeypatch):
    assert config(tmp_path/'a', monkeypatch, 'https://relay.example.com/')['routes'][0]['base'] == 'https://relay.example.com/v1'
    assert [r['id'] for r in config(tmp_path/'b', monkeypatch, '')['routes']] == ['original']
    for bad in ['http://relay.example.com', 'https://relay.example.com/lite', 'javascript:alert(1)']:
        with pytest.raises(RuntimeError):
            config(tmp_path/'c', monkeypatch, bad)
