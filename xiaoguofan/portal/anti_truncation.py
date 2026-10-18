"""Vendored Antigravity transformations, private child process, no network gateway."""
import asyncio
import base64
import copy
import json
import re
from pathlib import Path

PREFIX = '[防截断]'
LIMIT = 16 * 1024 * 1024
BRIDGE = Path(__file__).parent / 'anti_core' / 'bridge.js'
NONCHAT = re.compile(r'image|vision-preview|audio|tts|stt|whisper|embed|video|flux|dall-e|sdxl|diffusion|moderation', re.I)


def eligible(model):
    return isinstance(model,str) and bool(model) and not model.startswith(PREFIX) and not NONCHAT.search(model)


def augment_models(body):
    out=copy.deepcopy(body)
    if not isinstance(out,dict) or not isinstance(out.get('data'),list):
        return out
    seen={r.get('id') for r in out['data'] if isinstance(r,dict) and isinstance(r.get('id'),str)}
    added=[]
    for row in out['data']:
        if not isinstance(row,dict) or not eligible(row.get('id')) or PREFIX+row['id'] in seen:
            continue
        alias=copy.deepcopy(row)
        alias['id']=PREFIX+row['id']
        for field in ('name', 'display_name', 'displayName'):
            if isinstance(alias.get(field), str) and alias[field] and not alias[field].startswith(PREFIX):
                alias[field]=PREFIX+alias[field]
        added.append(alias)
        seen.add(alias['id'])
    out['data'].extend(added)
    return out


async def _spawn():
    return await asyncio.create_subprocess_exec('node','--max-old-space-size=64',str(BRIDGE),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)


async def _stop(proc):
    if proc.returncode is None:
        try:proc.kill()
        except ProcessLookupError:pass
    await proc.wait()


async def _call(command):
    raw=json.dumps(command,ensure_ascii=True).encode()+b'\n'
    if len(raw)>LIMIT:raise ValueError('防截断请求过大')
    proc=await _spawn()
    try:
        out,_=await asyncio.wait_for(proc.communicate(raw),timeout=20)
        if proc.returncode or len(out)>LIMIT:raise ValueError('防截断转换失败')
        return json.loads(out)
    finally:
        await _stop(proc)


async def inject(body):
    result=await _call({'op':'inject','body':body})
    return result['body'],result['name']


async def normalize(body,name,model):
    return await _call({'op':'normalize','body':body,'name':name,'model':model})


async def transform_stream(source,name,model):
    proc=await _spawn()
    async def pump():
        proc.stdin.write(json.dumps({'op':'stream','name':name,'model':model},ensure_ascii=True).encode()+b'\n')
        total=0
        try:
            async for chunk in source:
                total+=len(chunk)
                if total>LIMIT:raise ValueError('防截断响应过大')
                proc.stdin.write(json.dumps({'chunk':base64.b64encode(chunk).decode()}).encode()+b'\n')
                await proc.stdin.drain()
            proc.stdin.write(b'{"end":true}\n');await proc.stdin.drain();proc.stdin.close()
        except BaseException:
            proc.stdin.close()
            await _stop(proc)
            raise
    task=asyncio.create_task(pump())
    try:
        while chunk:=await proc.stdout.read(65536):
            yield chunk
        await task
        if await proc.wait():raise ValueError('防截断流式转换失败')
    finally:
        if not task.done():task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        await _stop(proc)
