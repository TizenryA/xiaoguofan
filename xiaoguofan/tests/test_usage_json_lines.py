import json

import pytest

from portal.usage import UsageMeter


@pytest.mark.parametrize('pretty',[False,True])
def test_json_encoder_newline_does_not_discard_usage(pretty):
    raw=(json.dumps({'choices':[{'message':{'content':'fixture'}}],'usage':{'prompt_tokens':123,'completion_tokens':45}},indent=2 if pretty else None)+'\n').encode()
    meter=UsageMeter()
    for i in range(0,len(raw),7):meter.feed(raw[i:i+7])
    assert meter.finish()=={'input_tokens':123,'output_tokens':45}
    assert meter.first_token_seconds is not None


def test_unknown_json_remains_unknown():
    meter=UsageMeter();meter.feed(b'{"choices":[]}\n')
    assert meter.finish()=={}
