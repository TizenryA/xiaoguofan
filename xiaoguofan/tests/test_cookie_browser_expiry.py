import time

import pytest
from fastapi import HTTPException

from portal.accounts import normalize_cookie, worker_payload
from portal.main import validate_account


def cookies(expires):
    return [{'name':n,'value':'fixture','domain':'.google.com','path':'/','sameSite':'no_restriction','session':False,'expirationDate':expires} for n in ['SAPISID','__Secure-1PAPISID','__Secure-3PAPISID']]


def test_browser_expiration_date_mapped_and_session_expiry_preserved():
    future=time.time()+3600
    assert normalize_cookie(cookies(future)[0])['expires']==future
    assert normalize_cookie(cookies(future)[0]|{'session':True})['expires']==-1
    assert normalize_cookie(cookies(future)[0]|{'expires':-1})['expires']==-1
    assert normalize_cookie({'name':'SID','value':'fixture'})['expires']==-1


@pytest.mark.parametrize('expires',[None,True,'tomorrow',float('nan'),float('inf')])
def test_bad_browser_expiration_rejected(expires):
    with pytest.raises(HTTPException) as e:
        normalize_cookie(cookies(expires)[0])
    assert e.value.status_code==422


def test_expired_browser_required_cookies_rejected_before_worker_start():
    p={'label':'fixture@gmail.com','locale':'en-US','timezone':'UTC','storage_state':{'cookies':cookies(1790000000)}}
    with pytest.raises(HTTPException) as e:
        worker_payload({'payload':p})
    assert e.value.status_code==422
    assert '过期' in e.value.detail
    with pytest.raises(HTTPException) as e:
        validate_account(p)
    assert e.value.status_code==422
    assert '过期' in e.value.detail or '缺少有效' in e.value.detail
