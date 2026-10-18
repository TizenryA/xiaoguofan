import copy

import pytest
from fastapi import HTTPException

from portal.accounts import worker_payload


def test_selected_identity_survives_worker_payload_and_rejects_email_relabel():
    identity={'email':'pro@example.com','gaia_id':'222','authuser':1}
    a={'payload':{'email':'pro@example.com','label':'own','storage_state':{'cookies':[],'origins':[],'xiaoguofan_identity':identity}}}
    original=copy.deepcopy(a)
    assert worker_payload(a)['storage_state']['xiaoguofan_identity']==identity
    assert a==original
    a['payload']['email']='other@example.com'
    with pytest.raises(HTTPException) as e:worker_payload(a)
    assert e.value.status_code==422


def test_identity_validation_rejects_invalid_selector():
    for identity in [{'email':'pro@example.com','gaia_id':'x','authuser':1},{'email':'pro@example.com','gaia_id':'222','authuser':True},{'email':'pro@example.com','gaia_id':'222','authuser':-1}]:
        with pytest.raises(HTTPException):worker_payload({'payload':{'email':'pro@example.com','storage_state':{'cookies':[],'xiaoguofan_identity':identity}}})
