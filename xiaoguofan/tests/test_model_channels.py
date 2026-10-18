import asyncio
import copy

import httpx
import pytest

from portal.channels import split_catalogue, split_model
from portal.quota import model_group
from portal.workers import WorkerManager


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


def test_worker_channel_switch_recreates_single_tenant_worker_with_only_selected_channel(tmp_path,monkeypatch):
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'token':'fixture'})),**kw))
    commands=[]
    class Manager(WorkerManager):
        async def docker(self,*args,**kw):
            commands.append(args)
            return '127.0.0.1:9999' if args[0]=='port' else ''
    async def run():
        m=Manager(tmp_path)
        await m.ensure('admin',[],2,channel='playground')
        await m.ensure('admin',[],2,channel='playground')
        assert len([c for c in commands if c[0]=='run'])==1
        await m.ensure('admin',[],2,channel='build')
        runs=[c for c in commands if c[0]=='run']
        assert len(runs)==2 and 'UPSTREAM_CHANNELS=playground' in runs[0] and 'UPSTREAM_CHANNELS=build' in runs[1]
        assert len(m.items)==1 and m.items['admin']['channel']=='build'
        with pytest.raises(ValueError):await m.ensure('admin',[],2,channel='evil')
    asyncio.run(run())
