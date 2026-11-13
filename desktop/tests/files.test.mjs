import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { underRoot, previewFile, uploadFile, listFiles } from '../electron/files.mjs';

test('workspace boundary rejects traversal and external symlinks', async t => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'feiyu-files-'));
  t.after(() => fs.rm(dir, { recursive: true, force: true }));
  const root = path.join(dir, 'workspace'); await fs.mkdir(root);
  await fs.writeFile(path.join(dir, 'secret'), 'outside');
  await fs.symlink(path.join(dir, 'secret'), path.join(root, 'escape'));
  await assert.rejects(underRoot(root, '../secret'), /工作区/);
  await assert.rejects(previewFile(root, 'escape'), /工作区/);
  await fs.writeFile(path.join(root, 'safe.txt'), '<script>not executable</script>');
  assert.deepEqual(await previewFile(root, 'safe.txt'), { kind: 'text', content: '<script>not executable</script>' });
  await fs.writeFile(path.join(root, 'binary'), Buffer.from([0, 1]));
  await assert.rejects(previewFile(root, 'binary'), /二进制/);
});

test('uploads cannot overwrite files or escape via upload directory symlinks', async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'feiyu-upload-'));
  const outside = await fs.mkdtemp(path.join(os.tmpdir(), 'feiyu-outside-'));
  t.after(() => Promise.all([root, outside].map(dir => fs.rm(dir, { recursive: true, force: true }))));
  const first = await uploadFile(root, '../../hello.txt', [65]);
  const second = await uploadFile(root, 'hello.txt', [66]);
  assert.notEqual(first, second);
  assert.equal(await fs.readFile(path.join(root, first), 'utf8'), 'A');
  await fs.rm(path.join(root, '.feiyu'), { recursive: true });
  await fs.symlink(outside, path.join(root, '.feiyu'));
  await assert.rejects(uploadFile(root, 'escape.txt', [67]), /工作区/);
  assert.deepEqual(await fs.readdir(outside), []);
});

test('directory browsing sorts folders and hides dependency trees', async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'feiyu-list-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await fs.mkdir(path.join(root, 'z-folder'));
  await fs.mkdir(path.join(root, 'node_modules'));
  await fs.writeFile(path.join(root, 'a.txt'), 'hello');
  assert.deepEqual((await listFiles(root)).map(e => e.name), ['z-folder', 'a.txt']);
});
