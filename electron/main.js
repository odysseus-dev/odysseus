const { app, BrowserWindow, globalShortcut, ipcMain, shell } = require('electron');
const path = require('path');

// Derive the Odysseus server port from the same env vars the server uses.
// Priority: ODYSSEUS_ELECTRON_URL (explicit override) > APP_PORT > ODYSSEUS_PORT > 7000.
const PORT = process.env.APP_PORT || process.env.ODYSSEUS_PORT || 7000;
const TARGET_URL = process.env.ODYSSEUS_ELECTRON_URL || `http://127.0.0.1:${PORT}`;

let mainWindow;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    frame: false,               // Native frame off — custom title bar from preload
    titleBarStyle: 'hidden',      // macOS: hide native title bar
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
    title: 'Odysseus',
    show: false,
  });

  mainWindow.loadURL(TARGET_URL);

  // External links open in the system browser, never inside the app.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.webContents.on('will-navigate', (event, url) => {
    const appOrigin = new URL(TARGET_URL).origin;
    if (new URL(url).origin !== appOrigin) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  // Show window once the DOM is ready (prevents white flash on success).
  mainWindow.webContents.once('dom-ready', () => {
    if (!mainWindow.isVisible()) mainWindow.show();
    if (process.env.NODE_ENV === 'development' || process.env.ODYSSEUS_DEV) {
      mainWindow.webContents.openDevTools();
    }
  });

  // If the server isn't running, show a friendly error below the custom title bar.
  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    if (!mainWindow.isVisible()) mainWindow.show();
    mainWindow.webContents.executeJavaScript(`
      document.documentElement.style.margin = '0';
      document.documentElement.style.padding = '0';
      document.documentElement.style.background = '#1a1a1a';
      document.body.style.margin = '0';
      document.body.style.padding = '0';
      document.body.style.background = '#1a1a1a';
      document.body.innerHTML = \`
        <div style="
          display:flex;flex-direction:column;align-items:center;justify-content:center;
          height:calc(100vh - 32px);font-family:sans-serif;color:#ccc;background:#1a1a1a;text-align:center;
          padding:40px;box-sizing:border-box;
        ">
          <h2 style="color:#ff6b6b;margin-bottom:12px;">Odysseus server not found</h2>
          <p style="max-width:480px;line-height:1.6;">
            Electron tried to load <code style="background:#2a2a2a;padding:2px 6px;border-radius:4px;">${TARGET_URL}</code>
            but the server is not running (or is on a different port).
          </p>
          <p style="margin-top:20px;color:#888;font-size:13px;">
            Start the server first, then press <strong>Ctrl+R</strong> to retry.
          </p>
        </div>
      \`;
    `).catch(() => {});
  });

  // Reload shortcuts (mirrors browser hard-refresh behaviour)
  globalShortcut.register('CommandOrControl+R', () => {
    if (mainWindow && mainWindow.isFocused()) {
      mainWindow.webContents.reloadIgnoringCache();
    }
  });

  // Toggle DevTools with F12
  globalShortcut.register('F12', () => {
    if (mainWindow && mainWindow.isFocused()) {
      mainWindow.webContents.toggleDevTools();
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// ── IPC window controls ──
ipcMain.on('window-minimize', () => {
  if (mainWindow) mainWindow.minimize();
});

ipcMain.on('window-maximize', () => {
  if (!mainWindow) return;
  if (mainWindow.isMaximized()) {
    mainWindow.unmaximize();
  } else {
    mainWindow.maximize();
  }
});

ipcMain.on('window-close', () => {
  if (mainWindow) mainWindow.close();
});

app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
});
