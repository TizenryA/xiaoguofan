// Fallback: one short-lived process per call over private stdin/stdout pipes.
// The resident service (server.js) is preferred; this keeps the portal working
// when the service is not running.
import readline from 'node:readline';
import {runSession} from './session.js';

const rl = readline.createInterface({input:process.stdin,crlfDelay:Infinity});
const lines = rl[Symbol.asyncIterator]();
const writeStdout = value => new Promise((resolve, reject) => {
  process.stdout.write(value, error => error ? reject(error) : resolve());
});
try {
  await runSession(lines, writeStdout);
} catch {
  process.stderr.write('anti-truncation component failed\n'); process.exitCode = 1;
} finally { rl.close(); process.stdin.destroy(); }
