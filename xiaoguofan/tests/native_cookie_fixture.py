"""Isolated native Cookie importer fixture. No production data or Google network."""
import tempfile

import httpx
import uvicorn

import portal.native_cookie as native
from portal.main import create_app

original=native.verify_native

def handler(request):
    assert request.url.host in ['accounts.google.com','myaccount.google.com']
    assert 'SID=ISOLATED-SID' in request.headers['cookie']
    for k in ['SIDCC','__Secure-1PSIDCC','__Secure-3PSIDCC']:
        assert k+'=ISOLATED-NEW' in request.headers['cookie']
    assert 'OSID=ISOLATED-OSID' in request.headers['cookie']
    assert '__Secure-OSID=ISOLATED-SECURE-OSID' in request.headers['cookie']
    assert 'ISOLATED-OLD' not in request.headers['cookie']
    if request.url.host=='accounts.google.com':return httpx.Response(200,content=b'')
    assert str(request.url)=='https://myaccount.google.com/u/0/'
    import json
    d={'oPEP7c':'fixture@example.com','qDCSke':'123456789012345678901','W3Yyqf':'123456789012345678901','S06Grb':'123456789012345678901'}
    text='<html><head><base href="https://myaccount.google.com/u/0/"><script>window.WIZ_global_data = '+json.dumps(d)+';</script></head></html>'
    return httpx.Response(200,text=text,headers={'Content-Type':'text/html'})

async def verify(raw,accounts):
    return await original(raw,accounts,transport=httpx.MockTransport(handler))
native.verify_native=verify

class Broker:
    async def stop(self,uid):pass

with tempfile.TemporaryDirectory(prefix='native-cookie-fixture-') as root:
    app=create_app(root,'isolated-fixture-password','http://127.0.0.1:8098',Broker())
    app.state.store.save_settings({'public_login':True})
    @app.post('/fixture/login')
    def fixture_login():
        import secrets

        from fastapi.responses import JSONResponse
        store=app.state.store
        user=store.discord_user(str(secrets.randbelow(10**17)+10**17),'ISOLATED fixture user')
        token,csrf=store.login(user['id'])
        response=JSONResponse({'csrf':csrf});response.set_cookie('portal_session',token)
        return response
    uvicorn.run(app,host='127.0.0.1',port=8098,access_log=False)
