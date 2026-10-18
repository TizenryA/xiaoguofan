"""Generated test Cookies only; never real Google authentication."""
import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from portal.main import create_app


def test_userscript_output_accepted_by_real_import_endpoint(tmp_path):
    script=Path(__file__).parents[1]/'portal/static/aistudio-export.user.js'
    code="""const {buildExport}=require(process.argv[1]);const cookies=['SAPISID','__Secure-1PAPISID','__Secure-3PAPISID','SID'].map(name=>({name,value:'FIXTURE-NOT-REAL-GOOGLE',domain:'.google.com',path:'/',session:false,expirationDate:2000000000,httpOnly:name==='SID',secure:true,sameSite:'no_restriction'}));process.stdout.write(JSON.stringify(buildExport('fixture@example.com','Userscript Fixture',cookies)));"""
    data=json.loads(subprocess.check_output(['node','-e',code,str(script)],text=True))
    class Broker:
        async def stop(self,uid):return {'ok':True}
    app=create_app(tmp_path,'fixture-long-password','https://portal.test/lite',Broker());s=app.state.store;s.save_settings({'public_login':True})
    u=s.discord_user('100000000000000007','Fixture Alice');token,csrf=s.login(u['id'])
    with TestClient(app,base_url='https://portal.test') as c:
        c.cookies.set('portal_session',token)
        r=c.post('/lite/api/accounts/import',json=data,headers={'X-CSRF-Token':csrf})
        assert r.status_code==201,r.text
        rows=c.get('/lite/api/accounts').json();assert len(rows)==1 and rows[0]['email']=='fixture@example.com'
        public=c.get('/lite/static/aistudio-export.user.js');assert public.status_code==200 and '@grant        GM_cookie\n' in public.text
        assert '/static/aistudio-export.user.js' in c.get('/lite').text
