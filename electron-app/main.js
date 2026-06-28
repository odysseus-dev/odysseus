const { app, BrowserWindow, shell, Menu } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');

const PORT = 7860;
const isDev = process.env.NODE_ENV === 'development';

// In dev: PROJECT_DIR is the repo root (where app.py lives)
// In packaged app: the Python server binary is bundled in Resources/server/
let PROJECT_DIR, SERVER_BIN, CHROMA_DIR, USER_DATA_DIR;

if (isDev) {
  PROJECT_DIR = process.env.ODYSSEUS_DIR || path.resolve(__dirname, '..');
  SERVER_BIN = path.join(PROJECT_DIR, 'venv', 'bin', 'python3');
  CHROMA_DIR = path.join(PROJECT_DIR, 'venv', 'bin');
  USER_DATA_DIR = process.env.ODYSSEUS_DATA_DIR || path.join(PROJECT_DIR, 'data');
} else {
  // Packaged: look for bundled server executable
  const serverDir = path.join(process.resourcesPath, 'server');
  SERVER_BIN = path.join(serverDir, 'odysseus-server');
  CHROMA_DIR = serverDir;
  PROJECT_DIR = serverDir;
  USER_DATA_DIR = path.join(require('os').homedir(), '.odysseus', 'data');
}

// Ensure the runtime data directory exists so the bundled server can write
// persistent state outside the read-only .app bundle.
if (!fs.existsSync(USER_DATA_DIR)) {
  fs.mkdirSync(USER_DATA_DIR, { recursive: true });
}

const APP_URL = `http://127.0.0.1:${PORT}`;

let mainWindow = null;
let pythonProcess = null;
let chromaProcess = null;

function log(msg) {
  console.log(`[odysseus] ${msg}`);
}

// ── Start ChromaDB (background, optional) ──────────────────────────────
function startChromaDB() {
  return new Promise((resolve) => {
    const chromaBin = path.join(CHROMA_DIR, 'chroma');

    if (!fs.existsSync(chromaBin)) {
      log('chroma not found, skipping');
      return resolve();
    }

    // Check if already running
    const req = http.get('http://127.0.0.1:8100/api/v1/heartbeat', (res) => {
      res.destroy();
      log('chroma already running');
      resolve();
    });
    req.on('error', () => {
      log('starting chroma on 127.0.0.1:8100…');
      chromaProcess = spawn(chromaBin, [
        'run', '--host', '127.0.0.1', '--port', '8100',
        '--path', path.join(USER_DATA_DIR, 'chroma')
      ], {
        cwd: PROJECT_DIR,
        stdio: ['ignore', 'pipe', 'pipe']
      });

      chromaProcess.stdout?.on('data', (d) => log(`[chroma] ${d.toString().trim()}`));
      chromaProcess.stderr?.on('data', (d) => log(`[chroma] ${d.toString().trim()}`));
      chromaProcess.on('exit', () => { chromaProcess = null; });

      setTimeout(resolve, 3000);
    });
    req.setTimeout(2000, () => { req.destroy(); resolve(); });
  });
}

// ── Start the Python server ───────────────────────────────────────────
function startPythonServer() {
  return new Promise((resolve, reject) => {
    log(`server binary: ${SERVER_BIN}`);
    log(`project dir: ${PROJECT_DIR}`);

    if (!fs.existsSync(SERVER_BIN)) {
      return reject(new Error(
        `Server binary not found at:\n${SERVER_BIN}\n\n` +
        `The app may be corrupted or incompletely built.`
      ));
    }

    if (isDev) {
      // Dev mode: run uvicorn module directly
      pythonProcess = spawn(SERVER_BIN, [
        '-m', 'uvicorn', 'app:app',
        '--host', '127.0.0.1', '--port', String(PORT)
      ], { cwd: PROJECT_DIR, stdio: ['ignore', 'pipe', 'pipe'] });
    } else {
      // Packaged mode: run the PyInstaller-built server binary
      pythonProcess = spawn(SERVER_BIN, [
        '--host', '127.0.0.1', '--port', String(PORT)
      ], {
        cwd: process.resourcesPath,
        env: { ...process.env, ODYSSEUS_DATA_DIR: USER_DATA_DIR },
        stdio: ['ignore', 'pipe', 'pipe']
      });
    }

    pythonProcess.stdout?.on('data', (data) => log(`[python] ${data.toString().trim()}`));
    pythonProcess.stderr?.on('data', (data) => log(`[python] ${data.toString().trim()}`));
    pythonProcess.on('error', (err) => { log(`server error: ${err}`); reject(err); });
    pythonProcess.on('exit', (code) => { log(`server exited: ${code}`); pythonProcess = null; });

    // Poll until ready
    let attempts = 0;
    const maxAttempts = 90;

    const checkReady = () => {
      const req = http.get(APP_URL, (res) => {
        res.destroy();
        log('server is ready!');
        resolve();
      });
      req.on('error', () => {
        if (++attempts >= maxAttempts) {
          reject(new Error(`Server did not start within ${maxAttempts} seconds.`));
          return;
        }
        setTimeout(checkReady, 1000);
      });
      req.setTimeout(1000, () => { req.destroy(); });
    };

    setTimeout(checkReady, 1000);
  });
}

// ── Build a UTF-8 loading screen as a data URL ────────────────────────
function buildLoadingUrl() {
  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Odysseus</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #1e1e2e;
    color: #cdd6f4;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    height: 100vh; text-align: center; user-select: none;
  }
  .logo { font-size: 4rem; margin-bottom: 1rem; }
  h1 { font-size: 1.8rem; font-weight: 600; margin-bottom: 0.5rem; }
  p { font-size: 1rem; color: #a6adc8; }
  .spinner {
    width: 40px; height: 40px; margin-top: 2rem;
    border: 4px solid rgba(205, 214, 244, 0.15);
    border-top-color: #89b4fa;
    border-radius: 50%;
    animation: spin 1s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
  <div class="logo">&#9973;</div>
  <h1>Odysseus</h1>
  <p>Starting background services&hellip;</p>
  <div class="spinner"></div>
</body>
</html>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

// ── Create the main window ────────────────────────────────────────────
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 800,
    minHeight: 600,
    title: 'Odysseus',
    icon: path.join(process.resourcesPath, 'icon.png'),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js')
    },
    show: false
  });

  // Hide default menu
  Menu.setApplicationMenu(null);

  // Open external links in the system browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith(APP_URL)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  // Show loading screen while server starts
  mainWindow.loadURL(buildLoadingUrl()).then(() => {
    mainWindow.show();
    startBackend();
  });
}

// ── Start backend and then load the app UI ────────────────────────────
async function startBackend() {
  try {
    await startChromaDB();
    await startPythonServer();
    log('loading app URL');
    mainWindow?.loadURL(APP_URL);
  } catch (err) {
    log(`failed to start server: ${err.message}`);
    if (mainWindow) {
      mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(
        `<h1>Startup Error</h1><pre>${err.message}</pre>`
      )}`);
    }
  }
}

// ── App lifecycle ─────────────────────────────────────────────────────
app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  if (pythonProcess) {
    log('stopping python server…');
    pythonProcess.kill('SIGTERM');
  }
  if (chromaProcess) {
    log('stopping chroma…');
    chromaProcess.kill('SIGTERM');
  }
});

app.on('quit', () => {
  if (pythonProcess && !pythonProcess.killed) {
    pythonProcess.kill('SIGKILL');
  }
  if (chromaProcess && !chromaProcess.killed) {
    chromaProcess.kill('SIGKILL');
  }
});
