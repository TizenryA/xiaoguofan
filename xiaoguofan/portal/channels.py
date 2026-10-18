"""Public channel suffixes backed by single-channel tenant workers, not name-only aliases."""
import copy

CHANNELS={'playground':'-pg','build':'-build'}


def split_model(model):
    for channel,suffix in CHANNELS.items():
        if model.endswith(suffix) and len(model)>len(suffix):
            return model[:-len(suffix)],channel
    return model,''


def split_catalogue(body):
    out=copy.deepcopy(body)
    if not isinstance(out,dict) or not isinstance(out.get('data'),list):return out
    rows=[]
    seen=set()
    upstream_ids={r.get('id') for r in out['data'] if isinstance(r,dict) and isinstance(r.get('id'),str)}
    for row in out['data']:
        if not isinstance(row,dict) or not isinstance(row.get('id'),str):
            rows.append(row)
            continue
        if row.get('xiaoguofan_channel') in CHANNELS:
            variants=[row]
        else:
            variants=[]
            available=row.get('channels')
            for channel,suffix in CHANNELS.items():
                if not isinstance(available,list) or channel not in available:continue
                alias_id=row['id']+suffix
                if alias_id in upstream_ids:continue
                alias=copy.deepcopy(row)
                alias['id']=alias_id
                for field in ('name','display_name','displayName'):
                    if isinstance(alias.get(field),str) and alias[field]:alias[field]+=suffix
                alias['channels']=[channel]
                alias['xiaoguofan_channel']=channel
                variants.append(alias)
            # No channel evidence: keep upstream item, never invent channel access.
            if not variants:variants=[row]
        for variant in variants:
            if variant['id'] not in seen:
                rows.append(variant)
                seen.add(variant['id'])
    out['data']=rows
    return out
