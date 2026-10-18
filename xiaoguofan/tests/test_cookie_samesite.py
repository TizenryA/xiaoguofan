import copy

import pytest
from fastapi import HTTPException

from portal.accounts import worker_payload
from portal.main import validate_account


@pytest.mark.parametrize('source,expected', [('no_restriction','None'),('lax','Lax'),('strict','Strict'),('unspecified',''),('None','None'),('Lax','Lax'),('Strict','Strict'),('','')])
def test_legacy_cookie_samesite_is_normalized_without_mutation(source, expected):
    payload={'label':'fixture@gmail.com','storage_state':{'cookies':[{'name':'SID','value':'fixture-secret','domain':'.google.com','path':'/','sameSite':source,'httpOnly':True,'secure':True,'expires':-1}], 'origins':[]}}
    before=copy.deepcopy(payload)
    result=worker_payload({'payload':payload})
    assert result['storage_state']['cookies'][0]['sameSite']==expected
    assert result['storage_state']['cookies'][0]['value']=='fixture-secret'
    assert payload==before
    result['storage_state']['cookies'][0]['value']='changed'
    assert payload==before


def test_invalid_samesite_is_rejected_on_upload_and_legacy_worker():
    p={'label':'fixture@gmail.com','locale':'en-US','timezone':'UTC','storage_state':{'cookies':[{'name':n,'value':'fixture','domain':'.google.com','path':'/','sameSite':'broken'} for n in ['SAPISID','__Secure-1PAPISID','__Secure-3PAPISID']]}}
    with pytest.raises(HTTPException) as err:
        worker_payload({'payload':p})
    assert err.value.status_code==422
    with pytest.raises(HTTPException) as err:
        validate_account(p)
    assert err.value.status_code==422


def test_upload_accepts_browser_enum_and_keeps_cookie_value():
    p={'label':'fixture@gmail.com','locale':'en-US','timezone':'UTC','storage_state':{'cookies':[{'name':n,'value':'fixture','domain':'.google.com','path':'/','sameSite':'no_restriction'} for n in ['SAPISID','__Secure-1PAPISID','__Secure-3PAPISID']]}}
    validate_account(p)
    assert all(c['sameSite']=='None' and c['value']=='fixture' for c in p['storage_state']['cookies'])
