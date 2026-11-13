const { contextBridge, ipcRenderer } = require('electron');
const commands = new Set(['connect', 'send', 'where_am_i', 'switch', 'upload', 'read_image',
  'list_files', 'preview_file', 'open_url', 'notify', 'term_start', 'term_write', 'term_resize', 'term_stop']);
const channels = new Set(['feiyu://event', 'feiyu://term', 'feiyu://state']);
contextBridge.exposeInMainWorld('feiyuDesktop', {
  platform: process.platform,
  core: { invoke(command, args = {}) {
    if (!commands.has(command)) return Promise.reject(new Error('未知桌面能力'));
    return ipcRenderer.invoke('feiyu:command', command, args);
  } },
  event: { listen(channel, callback) {
    if (!channels.has(channel)) throw new Error('未知事件');
    const handler = (_event, payload) => callback({ payload });
    ipcRenderer.on(channel, handler);
    return () => ipcRenderer.removeListener(channel, handler);
  } },
  dialog: { open: () => ipcRenderer.invoke('feiyu:directory') },
  window: { getCurrentWindow: () => ({
    minimize: () => ipcRenderer.invoke('feiyu:window', 'minimize'),
    toggleMaximize: () => ipcRenderer.invoke('feiyu:window', 'maximize'),
    close: () => ipcRenderer.invoke('feiyu:window', 'close'),
    // Electron uses native resize borders and CSS drag regions.
    startResizeDragging: () => {},
  }) },
});
