"""Bounded streaming usage extraction; never persist response bodies."""
import json
import time


class UsageMeter:
    def __init__(self, started=None):
        self.pending = bytearray()
        self.usage = {}
        self.started = started if started is not None else time.monotonic()
        self.first_token_seconds = None
        self.last_received = self.started

    def has_content(self, obj):
        for choice in obj.get('choices') or []:
            if not isinstance(choice,dict):
                continue
            delta=choice.get('delta') or choice.get('message') or {}
            if choice.get('text') or any(delta.get(k) for k in ['content','reasoning_content','tool_calls','function_call']):
                return True
        delta=obj.get('delta') or {}
        if isinstance(delta,dict) and any(delta.get(k) for k in ['text','thinking','partial_json']):
            return True
        for candidate in obj.get('candidates') or []:
            if any(p.get('text') or p.get('functionCall') for p in (candidate.get('content') or {}).get('parts',[])):
                return True
        return bool(obj.get('type') in ['response.output_text.delta','response.function_call_arguments.delta'] and delta)

    def parse(self, raw):
        try:
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                return
            if self.first_token_seconds is None and self.has_content(obj):
                self.first_token_seconds = max(0, self.last_received-self.started)
            data = obj.get('usage') or obj.get('message', {}).get('usage') or obj.get('usageMetadata') or {}
            if not isinstance(data, dict):
                return
            for target, names in [('input_tokens', ['prompt_tokens','input_tokens','promptTokenCount']),
                                  ('output_tokens', ['completion_tokens','output_tokens','candidatesTokenCount'])]:
                for name in names:
                    n = data.get(name)
                    if isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 10**12:
                        self.usage[target] = n
                        break
        except (ValueError, AttributeError, TypeError):
            return

    def feed(self, chunk):
        self.last_received = time.monotonic()
        self.pending.extend(chunk)
        while b'\n' in self.pending:
            line, _, remaining = self.pending.partition(b'\n')
            self.pending = bytearray(remaining)
            if line.startswith(b'data:'):
                self.parse(line[5:].strip())
        if len(self.pending) > 1024*1024:
            self.pending.clear()

    def finish(self):
        self.parse(self.pending)
        self.pending.clear()
        return self.usage
