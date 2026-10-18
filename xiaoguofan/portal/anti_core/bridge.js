// In-process application component invoked over private stdin/stdout pipes.
// No listener, no key store, no .env loader, no debug endpoints, no retry loop.
import readline from 'node:readline';
import {RequestInjector} from './synthetic/injector.js';
import {ResponseNormalizer} from './synthetic/normalizer.js';
import {StreamTransformer} from './synthetic/stream-transformer.js';
const cfg={wrapperMode:'prefer',controlMessagePosition:'tail',controlMessageRole:'system',syntheticToolPrefix:'agw_emit_',maxResponseBytes:16*1024*1024,streamSideBufferBytes:0};
const rl=readline.createInterface({input:process.stdin,crlfDelay:Infinity});
const lines=rl[Symbol.asyncIterator]();
try{
 const first=await lines.next();if(first.done)throw Error('missing input');
 const command=JSON.parse(first.value);
 if(command.op==='inject'){
  const result=new RequestInjector(cfg).inject(command.body);
  process.stdout.write(JSON.stringify({body:result.transformedBody,name:result.syntheticToolName}));
 }else if(command.op==='normalize'){
  const result=new ResponseNormalizer(cfg).normalizeNonStreaming(command.body,command.name).normalized;
  if(result&&typeof result==='object')result.model=command.model;
  process.stdout.write(JSON.stringify(result));
 }else if(command.op==='stream'){
  async function* chunks(){for await(const line of lines){const d=JSON.parse(line);if(d.end)break;yield Buffer.from(d.chunk,'base64')}yield Buffer.from('\n\n');}
  let pending='';
  const sink={write(value){pending+=value;let i;while((i=pending.indexOf('\n\n'))>=0){const event=pending.slice(0,i);pending=pending.slice(i+2);if(event.startsWith('data: ')&&event!=='data: [DONE]'){const obj=JSON.parse(event.slice(6));obj.model=command.model;process.stdout.write('data: '+JSON.stringify(obj)+'\n\n')}else process.stdout.write(event+'\n\n')}},flush(){}};
  await new StreamTransformer(cfg,command.name).transform(chunks(),sink);
 }else throw Error('unknown operation');
}catch{
 process.stderr.write('anti-truncation component failed\n');process.exitCode=1;
}finally{rl.close();process.stdin.destroy();}
