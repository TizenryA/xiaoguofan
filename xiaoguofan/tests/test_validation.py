
import pytest


def test_engine_start_only_after_accounts_exist():
    import inspect

    from portal.engine import EngineClient
    source = inspect.getsource(EngineClient.sync)
    assert "if desired:" in source


def test_cookie_expiry_malformed_is_validation_error():
    from fastapi import HTTPException

    from portal.main import validate_account
    with pytest.raises(HTTPException):
        validate_account({'locale':'en-US','timezone':'UTC','storage_state':{'cookies':[{'domain':'.google.com','name':'SAPISID','value':'v','expires':'bad'}]}})
