import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { pickWorkspace, readLast, writeLast } from '../electron/workspace.mjs';

test('launcher starts reopen the last workspace; terminal starts keep their directory', async t => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'feiyu-ws-'));
  t.after(() => fs.rm(dir, { recursive: true, force: true }));
  const home = path.join(dir, 'home'); const project = path.join(dir, 'project'); const last = path.join(dir, 'last');
  await Promise.all([home, project, last].map((d) => fs.mkdir(d)));
  assert.equal(pickWorkspace({ given: project, cwd: home, home, last }), project);
  assert.equal(pickWorkspace({ given: null, cwd: home, home, last }), last);
  assert.equal(pickWorkspace({ given: null, cwd: path.parse(home).root, home, last }), last);
  assert.equal(pickWorkspace({ given: null, cwd: project, home, last }), project);
  assert.equal(pickWorkspace({ given: null, cwd: home, home, last: path.join(dir, 'gone') }), home);
  assert.equal(pickWorkspace({ given: null, cwd: home, home, last: null }), home);
});

test('the last workspace survives a round trip and a broken file reads as none', async t => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'feiyu-last-'));
  t.after(() => fs.rm(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'state', 'workspace.json');
  assert.equal(readLast(file), null);
  writeLast(file, '/srv/project');
  assert.equal(readLast(file), '/srv/project');
  assert.equal((await fs.stat(file)).mode & 0o777, 0o600);
  await fs.writeFile(file, '{not json');
  assert.equal(readLast(file), null);
  await fs.writeFile(file, '{"last": 42}');
  assert.equal(readLast(file), null);
});
