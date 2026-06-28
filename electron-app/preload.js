// Preload script: safe bridge between renderer and main process
const { contextBridge, ipcRenderer } = require('electron');

// Expose a minimal API to the renderer if needed in the future.
// Currently the app uses standard HTTP requests to the local server,
// so no custom IPC channels are required.
contextBridge.exposeInMainWorld('odysseusElectron', {
  isElectron: true,
  platform: process.platform
});
