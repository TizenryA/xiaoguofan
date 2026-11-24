import copy

from portal.channels import split_catalogue, split_model
from portal.quota import model_group


def test_channel_catalogue_uses_upstream_metadata_and_preserves_unknown():
    original={'data':[{'id':'gemini-4-argon','name':'Gemini 4 Argon','channels':['playground','build']},{'id':'only-pg','name':'Only PG','channels':['playground']},{'id':'unknown','name':'Unknown'}]}
    snapshot=copy.deepcopy(original)
    out=split_catalogue(original)
    assert [(x['id'],x['name']) for x in out['data']]==[('gemini-4-argon-pg','Gemini 4 Argon-pg'),('gemini-4-argon-build','Gemini 4 Argon-build'),('only-pg-pg','Only PG-pg'),('unknown','Unknown')]
    assert [x.get('channels') for x in out['data'][:3]]==[['playground'],['build'],['playground']]
    assert original==snapshot and split_catalogue(out)==out


def test_suffix_decoding_and_quota_share_base_model():
    assert split_model('gemini-4-argon-pg')==('gemini-4-argon','playground')
    assert split_model('gemini-4-argon-build')==('gemini-4-argon','build')
    assert split_model('gemini-4-argon')==('gemini-4-argon','')
    for suffix in ['-pg','-build']:
        assert model_group('[防截断]gemini-4-argon'+suffix)=='4a'
        assert model_group('gemini-3.8-flash'+suffix)=='3.8f'


def test_upstream_assistant_suffix_cannot_shadow_existing_build_api_alias():
    original={'data':[
        {'id':'gemini-example','name':'Gemini','channels':['playground','build']},
        {'id':'gemini-example-build','name':'Gemini (Build)','channels':['assistant'],'capabilities':{'assistant_route':True}},
    ]}
    snapshot=copy.deepcopy(original)
    out=split_catalogue(original)
    assert [r['id'] for r in out['data']]==['gemini-example-pg','gemini-example-build','gemini-example-assistant']
    assert out['data'][1]['channels']==['build']
    assert out['data'][2]['channels']==['assistant']
    assert split_model('gemini-example-build')==('gemini-example','build')
    assert split_model('gemini-example-assistant')==('gemini-example-build','')
    assert split_catalogue(out)==out and original==snapshot


def test_assistant_alias_rewrites_chat_and_gemini_at_proxy_boundary(tmp_path):
    from engine_fixture import FakeEngine, cookie_payload, portal
    fake=FakeEngine().start()
    try:
        app,s,alice,bob,client=portal(tmp_path,fake)
        s.add_account(alice['id'],'own',cookie_payload('own@gmail.com'))
        headers={'Authorization':'Bearer '+s.create_key(alice['id'],'fixture')['key']}
        with client:
            response=client.post('/v1/chat/completions',headers=headers,json={'model':'gemini-example-assistant','messages':[]})
            assert response.status_code==200,response.text
            assert response.json()['model']=='gemini-example-build'
            response=client.post('/v1beta/models/gemini-example-assistant:generateContent',headers=headers,json={'contents':[]})
            assert response.status_code==200,response.text
            assert fake.seen[-1][0]=='/v1beta/models/gemini-example-build:generateContent'
    finally:
        fake.stop()
