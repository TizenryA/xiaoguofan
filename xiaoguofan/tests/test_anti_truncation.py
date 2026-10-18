"""Offline protocol fixtures: never evidence of a live Google generation."""
import json

import pytest

from portal.anti_truncation import PREFIX, augment_models, inject, normalize, transform_stream
from portal.quota import model_group
from portal.store import Store


def test_preferences_are_private_default_off_and_persist(tmp_path):
    s=Store(tmp_path,'fixture-long-password')
    assert s.preferences('admin')=={'anti_truncation':False}
    s.save_preferences('admin',True)
    assert s.preferences('other')=={'anti_truncation':False}
    assert s.preferences('admin')=={'anti_truncation':True}
    s.db.close();s=Store(tmp_path,'fixture-long-password')
    assert s.preferences('admin')=={'anti_truncation':True}


def test_model_aliases_preserve_originals_exclude_nonchat_and_never_duplicate():
    original={'object':'list','data':[{'id':'gemini-4-argon','object':'model'}, {'id':'gemini-3.8-flash'}, {'id':'gemini-image'}, {'id':'gemini-tts'}]}
    out=augment_models(original)
    assert [r['id'] for r in out['data']]==['gemini-4-argon','gemini-3.8-flash','gemini-image','gemini-tts',PREFIX+'gemini-4-argon',PREFIX+'gemini-3.8-flash']
    assert len(original['data'])==4
    assert augment_models(out)==out
    assert model_group(PREFIX+'gemini-4-argon')=='4a'
    assert model_group(PREFIX+'gemini-3.8-flash')=='3.8f'


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.anyio
async def test_vendored_injector_and_normalizer_preserve_real_tool_reasoning_usage():
    body={'model':'gemini-4-argon','messages':[{'role':'user','content':'fixture'}],'tools':[{'type':'function','function':{'name':'lookup','parameters':{'type':'object','properties':{}}}}]}
    transformed,name=await inject(body)
    assert transformed['model']==body['model'] and len(transformed['tools'])==2
    assert len(body['tools'])==1
    result={'model':body['model'],'usage':{'prompt_tokens':2,'completion_tokens':3},'choices':[{'index':0,'finish_reason':'tool_calls','message':{'role':'assistant','reasoning_content':'fixture reason','tool_calls':[{'id':'synthetic','type':'function','function':{'name':name,'arguments':json.dumps({'content':'猫猫✅\n完整正文'})}},{'id':'real','type':'function','function':{'name':'lookup','arguments':'{}'}}]}}]}
    out=await normalize(result,name,PREFIX+body['model'])
    assert out['model']==PREFIX+body['model']
    msg=out['choices'][0]['message']
    assert msg['content']=='猫猫✅\n完整正文'
    assert msg['reasoning_content']=='fixture reason'
    assert msg['tool_calls'][0]['function']['name']=='lookup'
    assert out['usage']==result['usage']


@pytest.mark.anyio
async def test_stream_restores_unicode_across_bytes_and_usage_and_real_tools():
    _,name=await inject({'model':'gemini-3.8-flash','messages':[{'role':'user','content':'fixture'}]})
    def event(delta,finish=None):return {'id':'chatcmpl-fixture','model':'gemini-3.8-flash','choices':[{'index':0,'delta':delta,'finish_reason':finish}]}
    chunks=[event({'role':'assistant','reasoning_content':'思考'}),event({'tool_calls':[{'index':0,'id':'syn','type':'function','function':{'name':name,'arguments':'{"content":"猫\\uD83D'}}]}),event({'tool_calls':[{'index':0,'function':{'arguments':'\\uDE3A\\n正文"}'}}]}),event({'tool_calls':[{'index':1,'id':'real','type':'function','function':{'name':'lookup','arguments':'{}'}}]}),event({},'tool_calls'),{'choices':[],'usage':{'prompt_tokens':3,'completion_tokens':5}}]
    wire=(''.join('data: '+json.dumps(c,ensure_ascii=False)+'\r\n\r\n' for c in chunks)+'data: [DONE]\n\n').encode()
    async def source():
        for i in range(0,len(wire),7):yield wire[i:i+7]
    raw=b''.join([part async for part in transform_stream(source(),name,PREFIX+'gemini-3.8-flash')]).decode()
    events=[json.loads(line[6:]) for line in raw.splitlines() if line.startswith('data: ') and line!='data: [DONE]']
    assert ''.join(c['delta'].get('content','') for e in events for c in e.get('choices',[]))=='猫😺\n正文'
    assert any(e.get('usage',{}).get('completion_tokens')==5 for e in events)
    assert name not in raw
    assert 'lookup' in raw
    assert '思考' in raw
    assert raw.count('data: [DONE]')==1
    assert all(e.get('model',PREFIX+'gemini-3.8-flash')==PREFIX+'gemini-3.8-flash' for e in events)


def test_alias_display_names_are_distinct_for_named_model_pickers():
    original={'object':'list','data':[{'id':'gemini-4-argon','name':'Gemini 4 Argon','display_name':'Argon Label','displayName':'Argon Display','object':'model','capability_options':{'aliases':['gemini-argon']}}]}
    out=augment_models(original)
    base,alias=out['data']
    assert base==original['data'][0]
    assert alias['id']==PREFIX+'gemini-4-argon'
    for field in ['name','display_name','displayName']:
        assert alias[field]==PREFIX+base[field]
    assert len({r['name']:r for r in out['data']})==2
    assert augment_models(out)==out
