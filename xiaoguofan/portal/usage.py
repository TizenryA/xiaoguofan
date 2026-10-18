"""Bounded streaming usage extraction; never persist response bodies."""
import json


class UsageMeter:
    def __init__(self):
        self.pending = bytearray()
        self.usage = {}

    def parse(self, raw):
        try:
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                return
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
