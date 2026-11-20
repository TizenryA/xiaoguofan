"""Send SSE keepalives between complete frames, including during preparation."""
import asyncio
import re
from contextlib import suppress


async def with_heartbeats(source, interval=15.0):
    iterator = source.__aiter__()
    pending = None
    buffered = bytearray()
    try:
        while True:
            pending = asyncio.ensure_future(anext(iterator))
            while not pending.done():
                done, _ = await asyncio.wait({pending}, timeout=interval)
                if not done:
                    yield b': ping\n\n'
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                pending = None
                if buffered:
                    yield bytes(buffered)
                return
            pending = None
            buffered.extend(chunk)
            # HTTP chunks can split a JSON line. Keep incomplete frames local so
            # a heartbeat can never become part of an upstream data field.
            while match := re.search(rb'\r\n\r\n|\n\n|\r\r', buffered):
                end = match.end()
                frame = bytes(buffered[:end])
                del buffered[:end]
                yield frame
    finally:
        if pending is not None:
            if not pending.done():
                pending.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        if hasattr(iterator, 'aclose'):
            await iterator.aclose()
