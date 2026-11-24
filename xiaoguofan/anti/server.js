// Resident anti-truncation service. Listens on a private unix socket only
// (no TCP, no keys, no .env); every connection is one independent session with
// the same line protocol as bridge.js, so sessions never block each other.
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import readline from 'node:readline';
import {runSession} from './session.js';

const socketPath = process.env.XGF_ANTI_SOCKET || process.argv[2] || '/run/xiaoguofan-anti/anti.sock';
const maxSessions = Number(process.env.XGF_ANTI_MAX_SESSIONS || 256);
let active = 0;

function handle(socket) {
  if (active >= maxSessions) { socket.end(); return; }
  active += 1;
  socket.on('error', () => {});
  const rl = readline.createInterface({input:socket,crlfDelay:Infinity});
  const lines = rl[Symbol.asyncIterator]();
  const write = value => new Promise((resolve, reject) => {
    if (socket.destroyed) { reject(Error('closed')); return; }
    socket.write(value, error => error ? reject(error) : resolve());
  });
  runSession(lines, write)
    .catch(() => { if (!socket.destroyed) socket.write(JSON.stringify({error:'anti-truncation component failed'}) + '\n'); })
    .finally(() => { active -= 1; rl.close(); socket.end(); });
}

fs.mkdirSync(path.dirname(socketPath), {recursive:true, mode:0o750});
try { fs.unlinkSync(socketPath); } catch {}
const server = net.createServer(handle);
server.listen(socketPath, () => {
  fs.chmodSync(socketPath, 0o660);
  process.stdout.write(`anti-truncation service listening on ${socketPath}\n`);
});
const stop = () => server.close(() => process.exit(0));
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
