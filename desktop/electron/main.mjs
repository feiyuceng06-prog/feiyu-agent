import { app, BrowserWindow, ipcMain, dialog, shell, Notification, Menu } from 'electron';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { AgentProcess, resolveBackend } from './agent.mjs';
import { underRoot, readBounded, listFiles, previewFile, uploadFile, MAX_IMAGE } from './files.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const rendererURL = pathToFileURL(path.resolve(here, '../dist/index.html')).href;
const require = createRequire(import.meta.url);
const options = { workspace: process.cwd(), resume: false, model: null };
const argv = process.argv.slice(app.isPackaged ? 1 : 2);
for (let i = 0; i < argv.length; i++) {
  if (['-w', '--workspace'].includes(argv[i])) options.workspace = argv[++i] || options.workspace;
  else if (['-m', '--model'].includes(argv[i])) options.model = argv[++i];
  else if (['-r', '--resume'].includes(argv[i])) options.resume = true;
}
let win, agent, terminal, workspace, transition;
let state = { state: 'starting' };
let quitting = false;
const logDir = path.join(process.env.XDG_STATE_HOME || path.join(os.homedir(), '.local/state'), 'feiyu');
fs.mkdirSync(logDir, { recursive: true, mode: 0o700 });
const logPath = path.join(logDir, 'desktop.log');
function log(data) {
  try {
    if (fs.existsSync(logPath) && fs.statSync(logPath).size > 4 * 1024 * 1024) fs.truncateSync(logPath);
    fs.appendFileSync(logPath, data, { mode: 0o600 });
    fs.chmodSync(logPath, 0o600);
  } catch { /* Logging must not prevent a desktop launch. */ }
  if (process.env.FEIYU_DESKTOP_STDERR) process.stderr.write(data);
}
function emit(channel, payload) { if (win && !win.isDestroyed()) win.webContents.send(channel, payload); }
function setState(next) { state = next; emit('feiyu://state', next); }
function trusted(event) {
  if (!win || event.sender !== win.webContents || event.senderFrame !== win.webContents.mainFrame || event.senderFrame.url !== rendererURL) {
    throw new Error('不允许的桌面调用来源');
  }
}
function stopTerminal() { const old = terminal; terminal = null; try { old?.kill(); } catch {} }
function dimensions(args) {
  return { cols: Math.max(2, Math.min(500, Number(args.cols) || 80)), rows: Math.max(2, Math.min(300, Number(args.rows) || 24)) };
}
async function startAgent(next, resume = false) {
  if (transition) throw new Error('工作台正在切换，请稍后再试');
  transition = (async () => {
    const target = await fsp.realpath(next);
    if (!(await fsp.stat(target)).isDirectory()) throw new Error('请选择文件夹');
    stopTerminal();
    if (!agent) agent = new AgentProcess({
      backend: resolveBackend({ packaged: app.isPackaged, resources: process.resourcesPath, root }),
      onEvent: line => emit('feiyu://event', line), onState: setState, onLog: log,
    });
    await agent.start(target, { model: options.model, resume });
    workspace = target;
    return workspace;
  })();
  try { return await transition; }
  catch (error) { setState({ state: 'error', message: error.message }); throw error; }
  finally { transition = null; }
}
ipcMain.handle('feiyu:directory', async event => {
  trusted(event);
  const result = await dialog.showOpenDialog(win, { title: '选择工作台', properties: ['openDirectory'] });
  return result.canceled ? null : result.filePaths[0];
});
ipcMain.handle('feiyu:window', (event, action) => {
  trusted(event);
  if (action === 'minimize') win.minimize();
  else if (action === 'maximize') win.isMaximized() ? win.unmaximize() : win.maximize();
  else if (action === 'close') win.close();
});
ipcMain.handle('feiyu:command', async (event, command, args = {}) => {
  trusted(event);
  switch (command) {
    case 'connect':
      if (agent?.child && !transition) { emit('feiyu://state', state); return workspace; }
      return startAgent(workspace || options.workspace, options.resume);
    case 'send': return agent?.send(args.line) ?? Promise.reject(new Error('Agent 未连接'));
    case 'where_am_i': return workspace || options.workspace;
    case 'switch': return startAgent(args.dir);
    case 'upload': return uploadFile(workspace, args.name, args.data);
    case 'read_image': return new Uint8Array(await readBounded(workspace, args.path, MAX_IMAGE)).buffer;
    case 'list_files': return listFiles(workspace, args.path);
    case 'preview_file': return previewFile(workspace, args.path);
    case 'open_url': {
      const url = new URL(args.url);
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error('只允许 http/https 链接');
      return shell.openExternal(url.href);
    }
    case 'notify':
      if (Notification.isSupported()) {
        const notification = new Notification({ title: String(args.title).slice(0, 80), body: String(args.body).slice(0, 240), icon: path.join(here, '../dist/icon.png') });
        notification.on('click', () => { win.show(); win.focus(); });
        notification.show();
      }
      return;
    case 'term_start': {
      stopTerminal();
      const cwd = await underRoot(workspace, args.cwd || '.');
      const pty = require('node-pty');
      const command = process.platform === 'win32' ? (process.env.COMSPEC || 'cmd.exe') : (process.env.SHELL || '/bin/sh');
      const child = pty.spawn(command, process.platform === 'win32' ? [] : ['-i'], {
        ...dimensions(args), cwd, name: 'xterm-256color', env: { ...process.env, TERM: 'xterm-256color' },
      });
      terminal = child;
      child.onData(data => { if (terminal === child) emit('feiyu://term', data); });
      child.onExit(() => { if (terminal === child) { terminal = null; emit('feiyu://term', '\r\n（shell 已结束）\r\n'); } });
      return;
    }
    case 'term_write':
      if (!terminal) throw new Error('终端未启动');
      if (typeof args.data !== 'string' || args.data.length > 1_000_000) throw new Error('无效终端输入');
      terminal.write(args.data); return;
    case 'term_resize': { const size = dimensions(args); terminal?.resize(size.cols, size.rows); return; }
    case 'term_stop': stopTerminal(); return;
    default: throw new Error('未知桌面能力');
  }
});

app.whenReady().then(async () => {
Menu.setApplicationMenu(null);
win = new BrowserWindow({
  title: 'Feiyu', width: 1440, height: 920, minWidth: 800, minHeight: 560,
  frame: false, backgroundColor: '#ffffff', show: false,
  icon: path.join(here, '../dist/icon.png'),
  webPreferences: { preload: path.join(here, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true },
});
win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
win.webContents.on('will-navigate', event => event.preventDefault());
win.webContents.on('will-attach-webview', event => event.preventDefault());
win.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
win.webContents.session.setPermissionCheckHandler(() => false);
win.once('ready-to-show', () => win.show());
win.webContents.on('render-process-gone', () => { stopTerminal(); void agent?.stop(); });
app.on('before-quit', event => {
  if (quitting) return;
  event.preventDefault();
  quitting = true;
  stopTerminal();
  Promise.resolve(agent?.stop()).finally(() => app.quit());
});
app.on('window-all-closed', () => app.quit());
await win.loadURL(rendererURL);

}).catch(error => { log(error.stack + "\n"); dialog.showErrorBox("Feiyu 启动失败", error.message); app.quit(); });
