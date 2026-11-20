"""Focused offline fixtures; run on HD only, no live upstream required."""
import asyncio
import json

import pytest

from portal import anti_truncation as anti


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def event(delta, finish=None):
    return ('data: ' + json.dumps({
        'id': 'chatcmpl-heartbeat', 'model': 'fixture',
        'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}],
    }, ensure_ascii=False) + '\n\n').encode()


def contents(raw):
    return ''.join(
        choice['delta'].get('content', '')
        for line in raw.decode().splitlines()
        if line.startswith('data: ') and line != 'data: [DONE]'
        for choice in json.loads(line[6:]).get('choices', [])
    )


@pytest.mark.anyio
async def test_tool_wait_heartbeats_cross_bridge_before_arguments_arrive():
    name = 'agw_emit_fixture'
    release = asyncio.Event()
    heartbeat = ': ping 等待  \r\n: trace 42\r\n\r\n'.encode()

    async def source():
        yield event({'tool_calls': [{'index': 0, 'function': {'name': name}}]})
        for offset in range(0, len(heartbeat), 3):
            yield heartbeat[offset:offset + 3]
        await release.wait()
        yield event({'tool_calls': [{'index': 0, 'function': {'arguments': '{"content":"原文✅"}'}}]})
        yield event({}, 'tool_calls')
        yield b'data: [DONE]\n\n'

    stream = anti.transform_stream(source(), name, anti.PREFIX + 'fixture')
    try:
        first = await asyncio.wait_for(anext(stream), 10)
        assert first == heartbeat
        assert b'data:' not in first
        release.set()
        raw = first + b''.join([chunk async for chunk in stream])
        assert contents(raw) == '原文✅'
        assert raw.count(b'data: [DONE]') == 1
        assert b'reasoning_content' not in raw
    finally:
        release.set()
        await stream.aclose()


@pytest.mark.anyio
async def test_standard_content_is_immediate_while_upstream_is_still_open():
    release = asyncio.Event()

    async def source():
        yield event({'content': '普通正文  ✅\n'})
        await release.wait()
        yield b': ping\n\n'
        yield event({}, 'stop')

    stream = anti.transform_stream(source(), 'agw_emit_fixture', anti.PREFIX + 'fixture')
    try:
        first = await asyncio.wait_for(anext(stream), 10)
        assert contents(first) == '普通正文  ✅\n'
        assert json.loads(first.decode()[6:])['model'] == anti.PREFIX + 'fixture'
        release.set()
        raw = first + b''.join([chunk async for chunk in stream])
        assert b': ping\n\n' in raw
        assert contents(raw) == '普通正文  ✅\n'
    finally:
        release.set()
        await stream.aclose()


@pytest.mark.anyio
async def test_call_accepts_large_request_and_response_with_relaxed_wait(monkeypatch):
    # Isolate pipe limits/timeouts without sleeping or making Node hold both copies.
    text = 'x' * (16 * 1024 * 1024 + 1)
    original_wait_for = asyncio.wait_for

    class Process:
        returncode = 0
        received = None

        async def communicate(self, raw):
            self.received = raw
            return json.dumps({'content': text}).encode(), None

        async def wait(self):
            return 0

    proc = Process()

    async def spawn():
        return proc

    async def wait_for(awaitable, timeout):
        assert timeout >= 120
        return await original_wait_for(awaitable, timeout)

    monkeypatch.setattr(anti, '_spawn', spawn)
    monkeypatch.setattr(anti.asyncio, 'wait_for', wait_for)
    assert await anti._call({'op': 'fixture', 'content': text}) == {'content': text}
    assert isinstance(proc.received, bytes)
    assert json.loads(proc.received)['content'] == text


@pytest.mark.anyio
async def test_normalizer_accepts_synthetic_arguments_above_old_limit():
    text = 'x' * (16 * 1024 * 1024 + 1)
    body = {'choices': [{'index': 0, 'finish_reason': 'tool_calls', 'message': {
        'role': 'assistant', 'tool_calls': [{'type': 'function', 'function': {
            'name': 'agw_emit_fixture', 'arguments': json.dumps({'content': text}),
        }}],
    }}]}
    out = await anti.normalize(body, 'agw_emit_fixture', anti.PREFIX + 'fixture')
    assert out['choices'][0]['message']['content'] == text
    assert out['choices'][0]['finish_reason'] == 'stop'


@pytest.mark.anyio
async def test_stream_accepts_total_above_old_limit_and_preserves_comments():
    heartbeat = b':' + b' ' * 65533 + b'\n\n'
    count = 257

    async def source():
        for _ in range(count):
            yield heartbeat
        yield event({'content': '尾部完整'})
        yield event({}, 'stop')

    raw = b''.join([chunk async for chunk in anti.transform_stream(
        source(), 'agw_emit_fixture', anti.PREFIX + 'fixture',
    )])
    assert raw.startswith(heartbeat * count)
    assert contents(raw) == '尾部完整'
    assert raw.count(b'data: [DONE]') == 1
