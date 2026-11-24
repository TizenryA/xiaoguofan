import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));

async function startService() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'xgf-anti-'));
  const sock = path.join(dir, 'anti.sock');
  const child = spawn(process.execPath, [path.join(here, '..', 'server.js'), sock], {stdio:['ignore','pipe','inherit']});
  await new Promise((resolve, reject) => {
    child.stdout.once('data', resolve);
    child.once('exit', code => reject(Error('exited ' + code)));
  });
  return {sock, stop: () => { child.kill('SIGTERM'); fs.rmSync(dir, {recursive:true, force:true}); }};
}

function session(sock, lines) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection(sock);
    let out = '';
    socket.on('data', d => { out += d; });
    socket.on('end', () => resolve(out));
    socket.on('error', reject);
    for (const line of lines) socket.write(JSON.stringify(line) + '\n');
  });
}

test('resident service answers concurrent independent sessions', async () => {
  const svc = await startService();
  try {
    const body = {model:'gemini', messages:[{role:'user', content:'hi'}]};
    const results = await Promise.all([
      session(svc.sock, [{op:'ping'}]),
      session(svc.sock, [{op:'inject', body}]),
      session(svc.sock, [{op:'inject', body}]),
    ]);
    assert.deepEqual(JSON.parse(results[0]), {ok:true});
    for (const raw of results.slice(1)) {
      const value = JSON.parse(raw);
      assert.match(value.name, /^agw_emit_/);
      assert.ok(Array.isArray(value.body.tools));
    }
    const event = 'data: ' + JSON.stringify({model:'up', choices:[{index:0, delta:{content:'正文'}}]}) + '\n\n';
    const streamed = await session(svc.sock, [
      {op:'stream', name:'agw_emit_x', model:'[防截断]gemini'},
      {chunk: Buffer.from(event).toString('base64')},
      {end:true},
    ]);
    assert.match(streamed, /\[防截断\]gemini/);
    const bad = await session(svc.sock, [{op:'nope'}]);
    assert.match(bad, /error/);
  } finally {
    svc.stop();
  }
});
