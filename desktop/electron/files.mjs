import fs from 'node:fs/promises';
import path from 'node:path';

export const MAX_FILE = 2 * 1024 * 1024;
export const MAX_IMAGE = 32 * 1024 * 1024;
export function contained(root, target) {
  const rel = path.relative(root, target);
  return rel === '' || (!rel.startsWith(`..${path.sep}`) && rel !== '..' && !path.isAbsolute(rel));
}
export async function underRoot(root, requested = '.') {
  if (typeof requested !== 'string') throw new Error('路径必须是文本');
  const base = await fs.realpath(root);
  const target = await fs.realpath(path.resolve(base, requested));
  if (!contained(base, target)) throw new Error('这个路径不在工作区内');
  return target;
}
export async function readBounded(root, requested, limit) {
  const target = await underRoot(root, requested);
  const file = await fs.open(target, 'r');
  try {
    const stat = await file.stat();
    if (!stat.isFile()) throw new Error('只能读取普通文件');
    if (stat.size > limit) throw new Error('文件太大，无法预览');
    const buffer = Buffer.alloc(Math.min(stat.size + 1, limit + 1));
    const { bytesRead } = await file.read(buffer, 0, buffer.length, 0);
    if (bytesRead > limit) throw new Error('文件太大，无法预览');
    return buffer.subarray(0, bytesRead);
  } finally { await file.close(); }
}
export async function listFiles(root, requested = '.') {
  const target = await underRoot(root, requested);
  const entries = await fs.readdir(target, { withFileTypes: true });
  return entries.filter(e => !['.git', 'node_modules', '.venv', '__pycache__'].includes(e.name))
    .sort((a, b) => Number(b.isDirectory()) - Number(a.isDirectory()) || a.name.localeCompare(b.name))
    .slice(0, 2000).map(e => ({ name: e.name, directory: e.isDirectory(),
      path: path.relative(root, path.join(target, e.name)).split(path.sep).join('/') }));
}
export async function previewFile(root, requested) {
  const ext = path.extname(requested).toLowerCase();
  const mime = { '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.gif': 'image/gif', '.webp': 'image/webp' }[ext];
  const data = await readBounded(root, requested, mime ? MAX_IMAGE : MAX_FILE);
  if (mime) return { kind: 'image', content: `data:${mime};base64,${data.toString('base64')}` };
  if (data.includes(0)) throw new Error('这个文件是二进制格式，暂不支持预览');
  return { kind: 'text', content: data.toString('utf8') };
}
export async function uploadFile(root, name, data) {
  if (!Array.isArray(data) || data.length > MAX_IMAGE || data.some(n => !Number.isInteger(n) || n < 0 || n > 255)) {
    throw new Error('附件无效或超过 32 MB');
  }
  const safe = String(name).split(/[\\/]/).at(-1).replace(/[\x00-\x1f:*?"<>|]/g, '_')
    .replace(/^\.+/, '').trim().slice(0, 120) || 'upload';
  // Check each directory before writing, including pre-existing symlinks.
  for (const relative of ['.feiyu', '.feiyu/uploads']) {
    await fs.mkdir(path.join(root, relative), { recursive: true });
    await underRoot(root, relative);
  }
  const directory = await underRoot(root, '.feiyu/uploads');
  const { name: stem, ext } = path.parse(safe);
  for (let i = 0; i < 1000; i++) {
    const filename = i ? `${stem}-${i + 1}${ext}` : safe;
    try {
      await fs.writeFile(path.join(directory, filename), Buffer.from(data), { flag: 'wx', mode: 0o600 });
      return `.feiyu/uploads/${filename}`;
    } catch (error) { if (error.code !== 'EEXIST') throw error; }
  }
  throw new Error('同名附件太多，请重命名后再试');
}
