import { build } from 'esbuild';
import { cp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
await mkdir(path.join(root, 'dist'), { recursive: true });
await cp(path.join(root, 'ui'), path.join(root, 'dist'), { recursive: true });
await cp(path.join(root, 'icons/256.png'), path.join(root, 'dist/icon.png'));
await build({ entryPoints: [path.join(root, 'renderer/workbench.jsx')], bundle: true,
  outfile: path.join(root, 'dist/workbench.js'), format: 'iife', jsx: 'automatic', minify: true,
  define: { 'process.env.NODE_ENV': '"production"' } });
const html = await readFile(path.join(root, 'ui/index.html'), 'utf8');
await writeFile(path.join(root, 'dist/index.html'), html.replace('<head>', `<head>
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'">
<link rel="stylesheet" href="workbench.css">`).replace('<script src="app.js"></script>', '<script src="workbench.js"></script>\n<script src="app.js"></script>'));
