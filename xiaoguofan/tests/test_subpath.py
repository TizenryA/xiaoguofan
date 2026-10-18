from fastapi.testclient import TestClient

from portal.main import create_app


def test_subpath_deployment_preserves_host_origin_csrf_and_routes(tmp_path):
    app=create_app(tmp_path,'test-password-long-enough','https://aistudio.tizenty.xyz/lite')
    with TestClient(app,base_url='https://aistudio.tizenty.xyz') as c:
        r=c.get('/lite')
        assert r.status_code==200
        assert 'href="/lite/static/style.css"' in r.text
        assert 'href="/guanlimanage"' in r.text
        a=c.get('/guanlimanage')
        assert a.status_code==200
        assert 'src="/lite/static/app.js"' in a.text
        assert 'href="/lite"' in a.text
        cfg=c.get('/lite/api/config').json()
        assert cfg['api_base']=='https://aistudio.tizenty.xyz/lite/v1'
        assert cfg['callback_url']=='https://aistudio.tizenty.xyz/lite/auth/discord/callback'
        r=c.post('/lite/api/auth/admin',json={'password':'test-password-long-enough'},headers={'Origin':'https://aistudio.tizenty.xyz'})
        assert r.status_code==200
        assert 'Path=/lite' in r.headers['set-cookie']
        csrf=r.json()['csrf']
        assert c.get('/lite/api/me').status_code==200
        assert c.post('/lite/api/keys',json={'label':'same domain'},headers={'Origin':'https://aistudio.tizenty.xyz','X-CSRF-Token':csrf}).status_code==201
        assert c.post('/lite/api/keys',json={'label':'wrong domain'},headers={'Origin':'https://evil.test','X-CSRF-Token':csrf}).status_code==403
        assert c.get('/lite/static/lite.js').status_code==200
        assert c.get('/api/me').status_code==404
        assert c.get('/lite/').status_code==200
