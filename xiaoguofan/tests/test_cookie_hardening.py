import pytest
from fastapi import HTTPException


def test_cookie_name_must_be_string():
    from portal.main import validate_account
    with pytest.raises(HTTPException):
        validate_account({'locale':'en-US','timezone':'UTC','storage_state':{'cookies':[{'domain':'.google.com','name':['bad'],'value':'v'}]}})


def test_cookie_host_only_subdomain_cannot_supply_required_google_signature():
    from portal.main import validate_account
    with pytest.raises(HTTPException):
        validate_account({'locale':'en-US','timezone':'UTC','storage_state':{'cookies':[
            {'domain':'accounts.google.com','name':n,'value':'v'}
            for n in ['SAPISID','__Secure-1PAPISID','__Secure-3PAPISID']]}})
