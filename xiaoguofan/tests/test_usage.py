from portal.usage import UsageMeter


def test_sse_usage_after_large_content_is_reported():
    m=UsageMeter()
    m.feed(b'data: {"choices":[{"delta":{"content":"'+b'a'*600000+b'"}}]}\n\n')
    for p in [b'data: {"choices":[], "usage":{"prompt_tokens":12,',b'"completion_tokens":7}}\n\ndata: [DONE]\n\n']:
        m.feed(p)
    assert m.finish()=={'input_tokens':12,'output_tokens':7}


def test_gemini_and_anthropic_usage():
    m=UsageMeter()
    m.feed(b'{"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":2}}')
    assert m.finish()=={'input_tokens':5,'output_tokens':2}
    m=UsageMeter()
    m.feed(b'data: {"type":"message_start","message":{"usage":{"input_tokens":9,"output_tokens":0}}}\n\n')
    m.feed(b'data: {"type":"message_delta","usage":{"output_tokens":6}}\n\n')
    assert m.finish()=={'input_tokens':9,'output_tokens':6}


def test_unknown_usage_is_not_estimated():
    m=UsageMeter();m.feed(b'{"choices":[]}')
    assert m.finish()=={}
