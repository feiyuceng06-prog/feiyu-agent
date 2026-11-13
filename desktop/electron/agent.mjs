import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { existsSync } from 'node:fs';
import path from 'node:path';

export function resolveBackend({ packaged, resources, root, env = process.env }) {
  if (packaged) {
    const command = path.join(resources, 'backend', process.platform === 'win32' ? 'feiyu-backend.exe' : 'feiyu-backend');
    if (!existsSync(command)) throw new Error('安装包中的 agent 后端缺失，请重新安装 Feiyu');
    return { command, args: [] };
  }
  if (env.FEIYU_BIN) return { command: env.FEIYU_BIN, args: [] };
  const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  if (existsSync(python)) return { command: python, args: ['-m', 'feiyu'] };
  return { command: 'feiyu', args: [] };
}

export class AgentProcess {
  constructor({ backend, onEvent, onState, onLog }) {
    Object.assign(this, { backend, onEvent, onState, onLog });
    this.child = null;
    this.generation = 0;
  }
  async start(workspace, { model, resume } = {}) {
    await this.stop();
    const generation = ++this.generation;
    this.onState({ state: 'starting' });
    const args = [...this.backend.args, '--serve', '--own-window', '-w', workspace];
    if (model) args.push('-m', model);
    if (resume) args.push('--resume');
    const child = spawn(this.backend.command, args, {
      cwd: workspace, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true,
      env: { ...process.env, PYTHONUTF8: '1', PYTHONUNBUFFERED: '1' },
    });
    this.child = child;
    child.stdin.on('error', () => {});
    const lines = createInterface({ input: child.stdout });
    lines.on('line', line => {
      if (this.generation !== generation || !line.trim()) return;
      try {
        const event = JSON.parse(line);
        if (event.kind === 'status') this.onState({ state: 'ready' });
        this.onEvent(line);
      } catch { this.onLog('后端输出了无效的协议事件\n'); }
    });
    child.stderr.on('data', data => this.onLog(data));
    child.on('exit', (code, signal) => {
      lines.close();
      if (generation !== this.generation) return;
      this.child = null;
      this.onState({ state: 'error', message: `Agent 已退出 (${signal || code})。可以重试连接。` });
    });
    await new Promise((resolve, reject) => {
      child.once('spawn', resolve);
      child.once('error', error => {
        if (generation === this.generation) {
          this.child = null;
          this.onState({ state: 'error', message: `无法启动 agent：${error.message}` });
        }
        reject(error);
      });
    });
  }
  send(line) {
    if (typeof line !== 'string' || line.length > 2_000_000) throw new Error('无效的请求');
    const message = JSON.parse(line);
    if (!message || typeof message.op !== 'string') throw new Error('请求缺少 op');
    if (!this.child?.stdin.writable) throw new Error('Agent 未连接');
    return new Promise((resolve, reject) => this.child.stdin.write(JSON.stringify(message) + '\n', error => error ? reject(error) : resolve()));
  }
  async stop() {
    ++this.generation;
    const child = this.child;
    this.child = null;
    if (!child || child.exitCode !== null) return;
    await new Promise(resolve => {
      const soft = setTimeout(() => child.kill(), 400);
      const hard = setTimeout(() => { child.kill('SIGKILL'); resolve(); }, 2000);
      child.once('exit', () => { clearTimeout(soft); clearTimeout(hard); resolve(); });
      child.stdin.end();
    });
  }
}
