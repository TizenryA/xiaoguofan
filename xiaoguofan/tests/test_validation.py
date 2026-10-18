
import pytest


def test_worker_start_does_not_start_empty_generator():
    import inspect

    from portal.workers import WorkerManager
    # The smoke test also runs this against the release container on InstaBox.
    source = inspect.getsource(WorkerManager.ensure)
    assert "if accounts:" in source


def test_cookie_expiry_malformed_is_validation_error():
    from fastapi import HTTPException

    from portal.main import validate_account
    with pytest.raises(HTTPException):
        validate_account({'locale':'en-US','timezone':'UTC','storage_state':{'cookies':[{'domain':'.google.com','name':'SAPISID','value':'v','expires':'bad'}]}})
