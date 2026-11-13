import test from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { AgentProcess, resolveBackend } from '../electron/agent.mjs';

test('bundled applications do not silently adopt an external backend', () => {
  assert.throws(() => resolveBackend({ packaged: true, resources: '/nonexistent', env: { FEIYU_BIN: 'other' } }), /后端缺失/);
});

test('agent protocol survives chunking and switches without leaking stale events', async t => {
  const events = [], states = [];
  const code = `process.stdout.write('{"kind":'); setTimeout(()=>process.stdout.write('"status"}\\n'),20);
    require('readline').createInterface({input:process.stdin}).on('line',line=>{
      const msg=JSON.parse(line); process.stdout.write(JSON.stringify({kind:'echo',text:msg.text})+'\\n');
    }).on('close',()=>process.exit(0));`;
  const agent = new AgentProcess({ backend: { command: process.execPath, args: ['-e', code, '--'] },
    onEvent: line => events.push(JSON.parse(line)), onState: s => states.push(s), onLog: () => {} });
  t.after(() => agent.stop());
  await agent.start(os.tmpdir());
  await new Promise(resolve => setTimeout(resolve, 100));
  await agent.send(JSON.stringify({ op: 'prompt', text: 'one\ntwo' }));
  await new Promise(resolve => setTimeout(resolve, 150));
  assert.ok(events.some(e => e.text === 'one\ntwo'));
  assert.ok(states.some(s => s.state === 'ready'));
  const old = agent.child;
  await agent.start(path.resolve(os.tmpdir()));
  assert.notEqual(agent.child.pid, old.pid);
  assert.equal(old.exitCode, 0);
  await agent.stop();
  assert.equal(states.some(s => s.state === 'error'), false);
  assert.throws(() => agent.send('{}'), /op/);
});
