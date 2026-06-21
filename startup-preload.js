const { contextBridge, ipcRenderer } = require('electron');

if (window.location.protocol === 'data:') {
  contextBridge.exposeInMainWorld('odysseusStartupClipboard', {
    copyText(value) {
      return ipcRenderer.invoke('startup:copy-text', String(value || ''));
    }
  });

  contextBridge.exposeInMainWorld('odysseusStartupTheme', {
    loadTheme() {
      return ipcRenderer.invoke('startup:theme-load');
    },
    saveTheme(theme) {
      return ipcRenderer.invoke('startup:theme-save', theme);
    }
  });
}
