// Preload script: safe bridge between renderer and main process
const { contextBridge, ipcRenderer } = require('electron');

// Expose a minimal API to the renderer if needed in the future.
// Currently the app uses standard HTTP requests to the local server,
// so no custom IPC channels are required — except launchSidecar, used
// by the optional-sidecar warning banners (ChromaDB / SearXNG) to spawn
// the recommended Docker container on the user's behalf.
contextBridge.exposeInMainWorld('odysseusElectron', {
  isElectron: true,
  platform: process.platform,
  // Returns a Promise<{ok:boolean, error?:string}> — resolves when the
  // docker run command has been spawned (not when the container is ready).
  launchSidecar: (kind) => ipcRenderer.invoke('launch-sidecar', kind),
});
