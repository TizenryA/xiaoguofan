// One anti-truncation session: the first line is a JSON command, stream sessions
// then receive {"chunk":base64} lines until {"end":true}. Output is written with
// awaited writes so a slow reader applies backpressure to this session only.
import {RequestInjector} from './core/synthetic/injector.js';
import {ResponseNormalizer} from './core/synthetic/normalizer.js';
import {StreamTransformer} from './core/synthetic/stream-transformer.js';

export const config = {wrapperMode:'prefer',controlMessagePosition:'tail',controlMessageRole:'system',syntheticToolPrefix:'agw_emit_',maxResponseBytes:Infinity,streamSideBufferBytes:0};

export async function runSession(lines, write) {
  const first = await lines.next();
  if (first.done) throw Error('missing input');
  const command = JSON.parse(first.value);
  if (command.op === 'ping') {
    await write(JSON.stringify({ok:true}));
  } else if (command.op === 'inject') {
    const result = new RequestInjector(config).inject(command.body);
    await write(JSON.stringify({body:result.transformedBody,name:result.syntheticToolName}));
  } else if (command.op === 'normalize') {
    const result = new ResponseNormalizer(config).normalizeNonStreaming(command.body, command.name).normalized;
    if (result && typeof result === 'object') result.model = command.model;
    await write(JSON.stringify(result));
  } else if (command.op === 'stream') {
    async function* chunks() {
      for await (const line of lines) {
        const d = JSON.parse(line);
        if (d.end) break;
        yield Buffer.from(d.chunk, 'base64');
      }
      yield Buffer.from('\n\n');
    }
    // The transformer writes complete events; comments need no JSON/model rewrite.
    const sink = {async write(value) {
      if (value.startsWith('data: ') && !value.startsWith('data: [DONE]')) {
        const obj = JSON.parse(value.slice(6)); obj.model = command.model;
        value = 'data: ' + JSON.stringify(obj) + '\n\n';
      }
      await write(value);
    }, flush() {}};
    await new StreamTransformer(config, command.name).transform(chunks(), sink);
  } else {
    throw Error('unknown operation');
  }
}
