import fs from 'node:fs';
import path from 'node:path';

/* 从应用菜单点开时 cwd 是家目录（有的桌面是 /）—— 那不是要干活的地方，接着用上一次的工作台。
   在某个项目目录里敲 feiyu-desktop 的，照旧就用那个目录；-w 给了就听 -w。 */
export function pickWorkspace({ given, cwd, home, last }) {
  if (given) return given;
  const fromLauncher = cwd === home || cwd === path.parse(cwd).root;
  if (fromLauncher && last && isDirectory(last)) return last;
  return cwd;
}

export function readLast(file) {
  try {
    const last = JSON.parse(fs.readFileSync(file, 'utf8')).last;
    return typeof last === 'string' && last ? last : null;
  } catch { return null; }
}

export function writeLast(file, dir) {
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify({ last: dir }), { mode: 0o600 });
  } catch { /* 记不下来只是下次从家目录起，不该拦着这次启动 */ }
}

function isDirectory(dir) {
  try { return fs.statSync(dir).isDirectory(); } catch { return false; }
}
