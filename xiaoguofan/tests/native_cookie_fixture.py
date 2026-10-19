"""Isolated native Cookie importer fixture. No production data or Google network."""
import tempfile

import httpx
import uvicorn

import portal.native_cookie as native
from portal.main import create_app

original=native.verify_native

def handler(request):
    assert request.url.host=='accounts.google.com'
    assert 'SID=ISOLATED-SID' in request.headers['cookie']
    for k in ['SIDCC','__Secure-1PSIDCC','__Secure-3PSIDCC']:
        assert k+'=ISOLATED-NEW' in request.headers['cookie']
    assert 'OSID=ISOLATED-OSID' in request.headers['cookie']
    assert '__Secure-OSID=ISOLATED-SECURE-OSID' in request.headers['cookie']
    assert 'ISOLATED-OLD' not in request.headers['cookie']
    rows=b''
    import base64
    for email,gid in [('fixture@example.com','111'),('second@example.com','222')]:
        row=bytes([26,len(email)])+email.encode()+bytes([72,1,82,len(gid)])+gid.encode()
        rows+=bytes([10,len(row)])+row
    return httpx.Response(200,content=base64.b64encode(rows))

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
