"""Client for the resident anti-truncation service (repo subdirectory ``anti/``).

The portal talks to ``anti/server.js`` over a private unix socket
(``XGF_ANTI_SOCKET``). When that service is not reachable it falls back to the
old one-process-per-call bridge so requests keep working; the admin backend
shows an alert in that case.
"""
import asyncio
import base64
import copy
import json
import logging
import os
import re
from pathlib import Path

PREFIX = '[防截断]'
ANTI_DIR = Path(__file__).resolve().parent.parent / 'anti'
BRIDGE = ANTI_DIR / 'bridge.js'
LAST_ERROR = ''
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


class _Session:
    """One protocol session: a connection to the resident service, or a fallback child process."""

    def __init__(self, reader, writer, proc=None):
        self.reader, self.writer, self.proc = reader, writer, proc

    @classmethod
    async def open(cls):
        global LAST_ERROR
        path = os.environ.get('XGF_ANTI_SOCKET', '')
        if path:
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(path, limit=2**24), 5)
                LAST_ERROR = ''
                return cls(reader, writer)
            except (OSError, TimeoutError):
                LAST_ERROR = '防截断常驻服务连接失败，已临时改用单次进程'
                logging.getLogger(__name__).warning('anti-truncation service unavailable; using fallback bridge')
        proc = await asyncio.create_subprocess_exec('node', str(BRIDGE), stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=2**24)
        return cls(proc.stdout, proc.stdin, proc)

    async def send(self, value):
        self.writer.write(json.dumps(value, ensure_ascii=True).encode()+b'\n')
        await self.writer.drain()

    def finish_input(self):
        if self.proc is not None:
            self.writer.close()
        elif self.writer.can_write_eof():
            self.writer.write_eof()

    async def close(self):
        try:
            if self.proc is not None:
                if self.proc.returncode is None:
                    try:
                        self.proc.kill()
                    except ProcessLookupError:
                        pass
                await self.proc.wait()
            else:
                self.writer.close()
                try:
                    await self.writer.wait_closed()
                except (OSError, ConnectionError):
                    pass
        except Exception:
            pass

    async def failed(self):
        if self.proc is not None:
            return bool(await self.proc.wait())
        return False


def _decode(raw):
    value = json.loads(raw)
    if isinstance(value, dict) and set(value) == {'error'}:
        raise ValueError('防截断转换失败')
    return value


async def _call(command):
    session = await _Session.open()
    try:
        await session.send(command)
        session.finish_input()
        raw = await asyncio.wait_for(session.reader.read(), timeout=120)
        if await session.failed() or not raw:
            raise ValueError('防截断转换失败')
        return _decode(raw)
    finally:
        await session.close()


async def ping():
    return await _call({'op': 'ping'})


async def inject(body):
    result = await _call({'op': 'inject', 'body': body})
    return result['body'], result['name']


async def normalize(body, name, model):
    return await _call({'op': 'normalize', 'body': body, 'name': name, 'model': model})


async def transform_stream(source, name, model):
    session = await _Session.open()

    async def pump():
        await session.send({'op': 'stream', 'name': name, 'model': model})
        try:
            async for chunk in source:
                await session.send({'chunk': base64.b64encode(chunk).decode()})
            await session.send({'end': True})
            session.finish_input()
        except BaseException:
            await session.close()
            raise
    task = asyncio.create_task(pump())
    try:
        while chunk := await session.reader.read(65536):
            if chunk.startswith(b'{"error"'):
                raise ValueError('防截断流式转换失败')
            yield chunk
        await task
        if await session.failed():
            raise ValueError('防截断流式转换失败')
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await session.close()
