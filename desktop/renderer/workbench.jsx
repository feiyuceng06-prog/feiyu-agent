import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './workbench.css';

const desktop = window.feiyuDesktop;
const invoke = (command, args) => desktop.core.invoke(command, args);
const errorText = error => String(error?.message || error).replace(/^Error invoking remote method '[^']+': Error: /, '');

function Workbench() {
  const [open, setOpen] = useState(() => localStorage.getItem('feiyu.files.open') !== 'no');
  const [workspace, setWorkspace] = useState('');
  const [directory, setDirectory] = useState('.');
  const [entries, setEntries] = useState([]);
  const [filter, setFilter] = useState('');
  const [tabs, setTabs] = useState([]);
  const [selected, setSelected] = useState('');
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState('');
  const [fileError, setFileError] = useState('');
  const [loading, setLoading] = useState(false);
  const [tick, refresh] = useState(0);
  const [status, setStatus] = useState({ state: 'starting' });
  const fileGeneration = useRef(0);
  useEffect(() => {
    const stop = desktop.event.listen('feiyu://state', e => setStatus(e.payload));
    const changed = e => {
      ++fileGeneration.current;
      setWorkspace(e.detail); setDirectory('.'); setTabs([]); setSelected(''); setPreview(null); setFileError(''); setFilter('');
    };
    const toggle = () => setOpen(value => !value);
    document.addEventListener('feiyu:workspace', changed);
    document.addEventListener('feiyu:files', toggle);
    const key = e => { if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'e') { e.preventDefault(); toggle(); } };
    document.addEventListener('keydown', key);
    return () => { stop(); document.removeEventListener('feiyu:workspace', changed); document.removeEventListener('feiyu:files', toggle); document.removeEventListener('keydown', key); };
  }, []);
  useEffect(() => {
    document.body.dataset.files = open ? 'on' : 'off';
    document.getElementById('act-files')?.setAttribute('aria-pressed', String(open));
    localStorage.setItem('feiyu.files.open', open ? 'yes' : 'no');
  }, [open]);
  useEffect(() => {
    if (!workspace || !open) return;
    let active = true;
    setLoading(true); setError('');
    invoke('list_files', { path: directory }).then(rows => { if (active) setEntries(rows); })
      .catch(e => { if (active) { setEntries([]); setError(errorText(e)); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [workspace, directory, open, tick]);
  async function selectFile(path) {
    const generation = ++fileGeneration.current;
    setSelected(path); setPreview(null); setFileError('');
    setTabs(current => current.includes(path) ? current : [...current, path].slice(-8));
    try {
      const data = await invoke('preview_file', { path });
      if (fileGeneration.current === generation) setPreview(data);
    } catch (e) { if (fileGeneration.current === generation) setFileError(errorText(e)); }
  }
  function closeFile(path) {
    const remaining = tabs.filter(tab => tab !== path);
    setTabs(remaining);
    if (selected === path) {
      ++fileGeneration.current;
      if (remaining.length) void selectFile(remaining.at(-1));
      else { setSelected(''); setPreview(null); setFileError(''); }
    }
  }
  return <>
    {status.state === 'error' && <div className="backend-error" role="alert">
      <span>{status.message}</span>
      <button onClick={() => { setStatus({ state: 'starting' }); document.dispatchEvent(new Event('feiyu:reconnect')); }}>重新连接</button>
    </div>}
    <section className="files-panel" aria-label="工作区文件" hidden={!open}>
      <header className="files-head"><strong>工作区文件</strong><div>
        <button title="刷新文件" onClick={() => { refresh(n => n + 1); if (selected) void selectFile(selected); }}>↻</button>
        <button title="收起文件面板" onClick={() => setOpen(false)}>×</button>
      </div></header>
      <div className="files-location" title={workspace + '/' + directory}>
        <button disabled={directory === '.'} aria-label="上一级目录" onClick={() => { setDirectory(directory.split('/').slice(0, -1).join('/') || '.'); setFilter(''); }}>↑</button>
        <span>{directory === '.' ? (workspace.split(/[\\/]/).at(-1) || '工作台') : directory}</span>
      </div>
      <input className="files-filter" aria-label="筛选文件" placeholder="筛选当前目录…" value={filter} onChange={e => setFilter(e.target.value)} />
      <div className="files-list" aria-busy={loading}>
        {loading ? <p className="files-empty">正在读取…</p> : error ? <p role="alert" className="files-empty">{error}</p> :
          entries.filter(entry => entry.name.toLowerCase().includes(filter.toLowerCase())).map(entry =>
            <button key={entry.path} className="file-row" data-selected={entry.path === selected} title={entry.path}
              onClick={() => { if (entry.directory) { setDirectory(entry.path); setFilter(''); } else void selectFile(entry.path); }}>
              <span className="file-kind">{entry.directory ? '▸' : '·'}</span><span>{entry.name}</span>
            </button>)}
        {!loading && !error && entries.length === 0 && <p className="files-empty">这个目录还没有文件</p>}
      </div>
      <div className="file-tabs" role="tablist" aria-label="已打开的文件">{tabs.map(tab => <div className="file-tab" key={tab} data-active={tab === selected}>
        <button role="tab" aria-selected={tab === selected} title={tab} onClick={() => void selectFile(tab)}>{tab.split('/').at(-1)}</button>
        <button aria-label={`关闭 ${tab}`} onClick={() => closeFile(tab)}>×</button>
      </div>)}</div>
      <div className="file-preview" role="region" aria-label="文件预览">
        {!selected ? <div className="preview-empty"><span>⌑</span><p>打开文件，一边聊一边看</p><small>代码、文本与图片预览</small></div> : fileError ? <p className="files-empty" role="alert">{fileError}</p> :
          !preview ? <p className="files-empty">正在打开…</p> : preview.kind === 'image' ? <img src={preview.content} alt={selected} /> :
            <pre><code>{preview.content}</code></pre>}
      </div>
      <footer className="preview-footer"><span title={selected}>{selected || '工作区'}</span><span>只读预览</span></footer>
    </section>
  </>;
}

if (desktop) {
  document.body.dataset.electron = 'true';
  const host = document.createElement('div');
  host.id = 'workbench';
  document.body.append(host);
  const toggle = document.createElement('button');
  toggle.id = 'act-files'; toggle.className = 'chip'; toggle.textContent = '文件';
  toggle.title = '文件与预览  Ctrl Shift E';
  toggle.onclick = () => document.dispatchEvent(new Event('feiyu:files'));
  document.getElementById('act-term').before(toggle);
  createRoot(host).render(<Workbench />);
}
