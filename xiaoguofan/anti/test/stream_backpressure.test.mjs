// Focused fixtures for HD: node --test anti/test/*.test.mjs
import assert from 'node:assert/strict';
import { Writable } from 'node:stream';
import test from 'node:test';
import { StreamTransformer } from '../core/synthetic/stream-transformer.js';

const content = 'data: {"model":"fixture","choices":[{"index":0,"delta":{"content":"原文"}}]}\n\n';
const cfg = { maxResponseBytes: Infinity, streamSideBufferBytes: 0 };

// A paused downstream must pause upstream consumption, including for heartbeats.
test('awaits asynchronous sink writes before reading more upstream', async () => {
  let release;
  const blocked = new Promise(resolve => { release = resolve; });
  const output = [];
  async function* source() {
    yield Buffer.from(': ping\r\n\r\n');
    assert.equal(output.length, 1);
    yield Buffer.from(content);
  }
  const transforming = new StreamTransformer(cfg, 'agw_emit_fixture').transform(source(), {
    async write(value) {
      output.push(value);
      if (output.length === 1) await blocked;
    },
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(output, [': ping\r\n\r\n']);
  release();
  await transforming;
  assert.equal(JSON.parse(output[1].slice(6)).choices[0].delta.content, '原文');
  assert.equal(output[2], 'data: [DONE]\n\n');
});

test('honors Node Writable drain and preserves event order', async () => {
  const output = [];
  const sink = new Writable({
    highWaterMark: 1,
    write(chunk, encoding, callback) {
      output.push(chunk.toString());
      setImmediate(callback);
    },
  });
  async function* source() {
    yield Buffer.from(': ping\n\n' + content);
  }
  await new StreamTransformer(cfg, 'agw_emit_fixture').transform(source(), sink);
  assert.equal(output[0], ': ping\n\n');
  assert.equal(JSON.parse(output[1].slice(6)).choices[0].delta.content, '原文');
  assert.equal(output[2], 'data: [DONE]\n\n');
  assert.equal(sink.writableLength, 0);
  sink.end();
});
