from fastapi.testclient import TestClient

from portal.main import create_app


def test_remote_login_fully_withdrawn_original_import_preserved(tmp_path):
    c=TestClient(create_app(tmp_path,'long-test-password','https://portal.example/lite'))
    for path in ['/lite/mobile-login','/mobile-login','/lite/static/mobile-login.js','/lite/static/mobile-login.css','/lite/static/mobile-login.html']:
        assert c.get(path).status_code==404
    for method,path in [('POST','/lite/api/mobile-login/start'),('GET','/lite/api/mobile-login/'+'a'*32+'/frame')]:
        assert c.request(method,path).status_code==404
    page=c.get('/lite').text
    assert 'mobile-login' not in page and '安卓手机直接登录' not in page
    assert 'google-login-entry' in page and 'google-import-extension' in page and 'upload-form' in page
