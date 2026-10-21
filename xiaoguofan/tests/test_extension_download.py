import io
import json
import zipfile

from fastapi.testclient import TestClient

from portal.main import create_app


def test_extension_download_and_upload_help(tmp_path):
    app = create_app(tmp_path, 'test-password-long-enough', 'https://portal.test')
    with TestClient(app, base_url='https://portal.test') as c:
        page = c.get('/').text
        assert '/static/AIStudio-Lite-账号导入.zip' in page
        assert '一键导入尚未提供' not in page
        r = c.get('/static/AIStudio-Lite-账号导入.zip')
        assert r.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            assert z.testzip() is None
            assert set(z.namelist()) == {'manifest.json', 'core.js', 'compat.js', 'popup.js', 'background.js', 'popup.html', 'popup.css', '使用说明.txt'}
            m = json.loads(z.read('manifest.json'))
            assert m['version'] == '0.3.6'
            assert 'host_permissions' not in m
            assert m['permissions'] == ['activeTab', 'scripting', 'cookies']
            assert 'externally_connectable' not in m
            assert m['optional_host_permissions'] == ['https://*.google.com/*']
