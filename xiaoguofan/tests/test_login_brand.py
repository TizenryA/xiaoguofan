from fastapi.testclient import TestClient

from portal.main import create_app


def test_new_brand_and_welcome_picture_on_both_entrypoints(tmp_path):
    c=TestClient(create_app(tmp_path,'fixture-long-password','https://portal.example/lite'))
    assert c.get('/lite/api/config').json()['site_name']=='渡鸦の小锅饭'
    for path in ['/lite','/guanlimanage']:
        r=c.get(path)
        assert r.status_code==200
        assert '欢迎来到渡鸦の小锅饭，喵' in r.text
        assert '登录欢迎图.webp' in r.text
        assert '渡鸦のais公益大锅饭' not in r.text
    image=c.get('/lite/static/登录欢迎图.webp')
    assert image.status_code==200 and image.headers['content-type']=='image/webp'
