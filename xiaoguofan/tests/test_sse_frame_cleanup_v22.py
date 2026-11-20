import asyncio

import pytest

from portal.streaming import with_heartbeats
from portal.proxy_response import ManagedStreamingResponse


@pytest.mark.parametrize('newline', [b'\n', b'\r\n'])
def test_partial_json_survives_heartbeat(newline):
    async def run():
        gate = asyncio.Event()
        async def source():
            yield b'data: {"content":"hel'
            await gate.wait()
            yield b'lo"}'+newline+newline
        stream = with_heartbeats(source(), interval=.001)
        assert await anext(stream) == b': ping\n\n'
        gate.set()
        assert await anext(stream) == b'data: {"content":"hello"}'+newline+newline
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    asyncio.run(run())


def test_send_failure_closes_pending_source_before_lease_cleanup():
    async def run():
        order = []
        async def source():
            try:
                await asyncio.Event().wait()
                yield b''
            finally:
                order.append('source closed')
        async def cleanup():
            order.append('lease released')
        response = ManagedStreamingResponse(with_heartbeats(source(), interval=.001), cleanup=cleanup)
        async def send(message):
            if message['type'] == 'http.response.body':
                raise RuntimeError('fixture closed socket')
        async def receive():
            await asyncio.Event().wait()
        with pytest.raises(RuntimeError, match='fixture closed socket'):
            await response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send)
        assert order == ['source closed', 'lease released']
    asyncio.run(run())
