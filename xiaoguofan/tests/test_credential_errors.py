from fastapi import HTTPException
from test_lite import setup

from portal.workers import BrokerClient


def test_expired_credentials_are_422_not_generic_503(tmp_path):
    c,s,a,b,csrf,broker=setup(tmp_path)
    s.add_account(a['id'],'fixture',{'label':'fixture@gmail.com'})
    async def expired(*args):
        raise HTTPException(422,'Google 登录凭据已过期，请重新登录并导出；不会延长原凭据有效期')
    broker.ensure=expired
    key=s.create_key(a['id'],'fixture')['key']
    r=c.get('/v1/models',headers={'Authorization':'Bearer '+key})
    assert r.status_code==422
    assert '过期' in r.json()['detail']
    assert s.records(a['id'])['items'][0]['status']==422


def test_broker_preserves_only_known_credential_messages(monkeypatch):
    import asyncio

    import httpx
    real=httpx.AsyncClient
    message='Google 登录凭据已过期，请重新登录并导出；不会延长原凭据有效期'
    def client(**kw):
        kw['transport']=httpx.MockTransport(lambda r:httpx.Response(422,json={'detail':message}))
        return real(**kw)
    monkeypatch.setattr(httpx,'AsyncClient',client)
    import pytest
    with pytest.raises(HTTPException) as e:asyncio.run(BrokerClient().call('/ensure',{}))
    assert e.value.detail==message
    message='DO_NOT_LEAK_RAW_SECRET'
    with pytest.raises(HTTPException) as e:asyncio.run(BrokerClient().call('/ensure',{}))
    assert 'DO_NOT_LEAK' not in e.value.detail
