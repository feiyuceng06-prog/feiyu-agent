// Real Electron + Python + PTY integration. No provider requests or user config.
import { _electron as electron, expect } from '@playwright/test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { checkModelForm } from './model-form.mjs';
const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'feiyu-desktop-test-'));
const workspace = path.join(temporary, 'workspace');
const other = path.join(temporary, 'other');
await fs.mkdir(workspace); await fs.mkdir(other);
await fs.writeFile(path.join(workspace, 'README.md'), '# Feiyu Workbench\n\nElectron + Python agent.\n');
await fs.writeFile(path.join(other, 'second.txt'), 'Second workspace');
await fs.mkdir(path.join(temporary, 'config'), { recursive: true });
const env = { ...process.env, HOME: temporary, XDG_CONFIG_HOME: path.join(temporary, 'config'),
  XDG_STATE_HOME: path.join(temporary, 'state'), XDG_DATA_HOME: path.join(temporary, 'data'),
  FEIYU_BIN: process.env.FEIYU_TEST_EXECUTABLE ? '/nonexistent-backend-override' : path.join(desktop, 'build/backend/feiyu-backend'), SHELL: '/bin/sh' };
delete env.ELECTRON_RUN_AS_NODE;
let application;
try {
  application = await electron.launch({
    ...(process.env.FEIYU_TEST_EXECUTABLE ? { executablePath: process.env.FEIYU_TEST_EXECUTABLE } : {}),
    args: [...(process.env.FEIYU_TEST_EXECUTABLE ? [] : [desktop]), '-w', workspace], env, timeout: 30000,
  });
  const page = await application.firstWindow();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await expect(page.locator('#s-ver')).not.toHaveText('—', { timeout: 30000 });
  await expect(page.locator('.backend-error')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '· README.md', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '· README.md', exact: true }).click();
  await expect(page.locator('.file-preview')).toContainText('Electron + Python agent.');
  const isolation = await page.evaluate(() => ({ require: typeof window.require, process: typeof window.process }));
  expect(isolation).toEqual({ require: 'undefined', process: 'undefined' });
  const denied = await page.evaluate(async () => {
    try { await window.feiyuDesktop.core.invoke('preview_file', { path: '../other/second.txt' }); return false; }
    catch { return true; }
  });
  expect(denied).toBe(true);
  await page.locator('#act-term').click();
  await page.waitForFunction(() => document.querySelector('.xterm-screen') !== null);
  await page.evaluate(() => {
    window.__terminalOutput = '';
    window.feiyuDesktop.event.listen('feiyu://term', event => { window.__terminalOutput += event.payload; });
  });
  // Await a successful PTY write, rather than racing the asynchronous shell startup.
  await expect.poll(async () => page.evaluate(async () => {
    try { await window.feiyuDesktop.core.invoke('term_write', { data: "printf 'FEIYU_%s_OK\\n' TERMINAL\n" }); return true; }
    catch { return false; }
  })).toBe(true);
  await page.waitForFunction(() => window.__terminalOutput.includes('FEIYU_TERMINAL_OK'));
  await fs.mkdir(path.join(desktop, 'test-results'), { recursive: true });
  await page.screenshot({ path: path.join(desktop, 'test-results/workbench.png') });
  await page.evaluate(dir => goDesk(dir), other);
  await expect(page.locator('#desk-path')).toHaveAttribute('title', other);
  await expect(page.getByRole('button', { name: '· second.txt', exact: true })).toBeVisible();
  await expect(page.locator('.file-tabs')).not.toContainText('README.md');
  await page.locator('#nav-shelf').click();
  await expect(page.locator('#shelf')).toBeVisible();
  await checkModelForm(page);
  expect(errors).toEqual([]);
  console.log('PASS: real backend, file preview, isolated renderer, path boundary, PTY, workspace switching, settings');
} catch (error) {
  try { console.error(await fs.readFile(path.join(temporary, 'state/feiyu/desktop.log'), 'utf8')); } catch {}
  throw error;
} finally {
  await application?.close();
  await fs.rm(temporary, { recursive: true, force: true });
}
