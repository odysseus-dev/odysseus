const { app, BrowserWindow, shell, Menu, dialog } = require('electron');
const { spawn, execFile } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');
const os = require('os');
const crypto = require('crypto');

// Derive the Odysseus server port from the same env vars the server uses.
// Priority: APP_PORT > ODYSSEUS_PORT > 7860 (macOS default; avoid 7000 = AirPlay).
const PORT = process.env.APP_PORT || process.env.ODYSSEUS_PORT || 7860;
const SEARXNG_PORT = process.env.SEARXNG_PORT || 8080;
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

// ── First-launch bootstrap: create required subdirectories ──────────
// The server's own setup.py creates these, but in packaged mode the user
// never runs setup.py. We pre-create the subdirs the server expects so
// first launch doesn't fail on missing paths. Safe to re-run.
// (Adopts the gap #3769 addressed via bootstrap.py — admin/.env creation
// is handled by the server's own /setup endpoint + login.html, so we
// only need to create the dirs.)
const REQUIRED_SUBDIRS = [
  'uploads', 'personal_docs', 'personal_uploads', 'tts_cache',
  'generated_images', 'deep_research', 'chroma', 'rag',
  'memory_vectors', 'logs', 'skills', 'ssh', 'mail-attachments',
  'emoji_cache', 'local',
];
function ensureDataDirs() {
  for (const sub of REQUIRED_SUBDIRS) {
    const d = path.join(USER_DATA_DIR, sub);
    if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true });
  }
}
ensureDataDirs();

const CHROMA_PORT = 8100;
const APP_URL = `http://127.0.0.1:${PORT}`;
const SEARXNG_URL = `http://127.0.0.1:${SEARXNG_PORT}`;

let mainWindow = null;
let pythonProcess = null;
let chromaProcess = null;
let searxngProcess = null;
// Set if ChromaDB isn't reachable in packaged mode; shown to the user.
let chromaWarning = null;
// Set if SearXNG isn't reachable and we couldn't start it; shown to the user.
let searxngWarning = null;

function log(msg) {
  console.log(`[odysseus] ${msg}`);
}

// ── ChromaDB (optional sidecar) ───────────────────────────────────────
// Odysseus uses `chromadb-client` (thin HTTP client), so the server expects
// a standalone ChromaDB instance at 127.0.0.1:8100 — it does NOT embed it.
// In dev mode, if the user has installed the full `chromadb` package
// (which provides the `chroma` CLI), we spawn it as a convenience.
// In packaged mode there's no `chroma` binary bundled (PyInstaller only
// ships the client), so we just probe the port and warn the user if it's
// down so they know RAG / vector memory won't be available.
function startChromaDB() {
  return new Promise((resolve) => {
    // If already running, done.
    const probe = http.get(`http://127.0.0.1:${CHROMA_PORT}/api/v1/heartbeat`, (res) => {
      res.destroy();
      log('chroma already running');
      resolve();
    });
    probe.on('error', () => {
      const chromaBin = path.join(CHROMA_DIR, 'chroma');
      if (fs.existsSync(chromaBin)) {
        // Dev convenience: spawn the chroma CLI if present.
        log(`starting chroma on 127.0.0.1:${CHROMA_PORT}…`);
        chromaProcess = spawn(chromaBin, [
          'run', '--host', '127.0.0.1', '--port', String(CHROMA_PORT),
          '--path', path.join(USER_DATA_DIR, 'chroma')
        ], {
          cwd: PROJECT_DIR,
          stdio: ['ignore', 'pipe', 'pipe']
        });
        chromaProcess.stdout?.on('data', (d) => log(`[chroma] ${d.toString().trim()}`));
        chromaProcess.stderr?.on('data', (d) => log(`[chroma] ${d.toString().trim()}`));
        chromaProcess.on('exit', () => { chromaProcess = null; });
        // Give it a moment to bind, then resolve regardless.
        setTimeout(resolve, 3000);
      } else {
        // No binary to spawn. In packaged mode this is expected — surface a
        // clear warning instead of silently degrading RAG / vector memory.
        chromaWarning =
          `ChromaDB is not running at 127.0.0.1:${CHROMA_PORT}. ` +
          `RAG (Personal Docs) and vector memory will be unavailable until you start a ChromaDB server. ` +
          `Easiest: \`docker run -p 8100:8000 -v ~/.odysseus/chroma:/chroma/chroma chromadb/chroma:latest\`.`;
        log(`chroma not found at ${chromaBin}; ${chromaWarning}`);
        resolve();
      }
    });
    probe.setTimeout(2000, () => { probe.destroy(); resolve(); });
  });
}

// ── SearXNG (optional sidecar for web search) ────────────────────────
// Like ChromaDB, SearXNG is an external service the server dials into via
// SEARXNG_INSTANCE (default http://localhost:8080). We don't bundle a
// SearXNG runtime (unlike #3769 which ships a standalone Python+searxng);
// instead we probe the port and, if the user has `searxng` installed in
// their venv (dev convenience), spawn it. Otherwise surface a warning so
// the user knows web search is unavailable — same pattern as ChromaDB.
function startSearXNG() {
  return new Promise((resolve) => {
    const probe = http.get(`${SEARXNG_URL}/healthz`, (res) => {
      res.destroy();
      log('searxng already running');
      resolve();
    });
    probe.on('error', () => {
      // Dev convenience: try `searx-run` from the venv if present.
      const searxCheck = [
        path.join(CHROMA_DIR, 'searx-run'),
        path.join(PROJECT_DIR, 'venv', 'bin', 'searx-run'),
      ];
      const bin = searxCheck.find((p) => fs.existsSync(p));
      if (bin) {
        log(`starting searxng on 127.0.0.1:${SEARXNG_PORT}…`);
        searxngProcess = spawn(bin, [
          '--host', '127.0.0.1', '--port', String(SEARXNG_PORT)
        ], {
          cwd: PROJECT_DIR,
          stdio: ['ignore', 'pipe', 'pipe'],
          env: { ...process.env, SEARXNG_SECRET: crypto.randomBytes(32).toString('hex') }
        });
        searxngProcess.stdout?.on('data', (d) => log(`[searxng] ${d.toString().trim()}`));
        searxngProcess.stderr?.on('data', (d) => log(`[searxng] ${d.toString().trim()}`));
        searxngProcess.on('exit', () => { searxngProcess = null; });
        setTimeout(resolve, 3000);
      } else {
        searxngWarning =
          `SearXNG is not running at ${SEARXNG_URL}. Web search will be unavailable. ` +
          `Easiest: \`docker run -p 8080:8080 searxng/searxng:latest\`.`;
        log(`searxng not found; ${searxngWarning}`);
        resolve();
      }
    });
    probe.setTimeout(2000, () => { probe.destroy(); resolve(); });
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
        env: {
          ...process.env,
          ODYSSEUS_DATA_DIR: USER_DATA_DIR,
          SEARXNG_INSTANCE: SEARXNG_URL,
          APP_PORT: String(PORT),
        },
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
    const appOrigin = new URL(APP_URL).origin;
    if (new URL(url).origin !== appOrigin) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  // Show loading screen while server starts
  mainWindow.loadURL(buildLoadingUrl()).then(() => {
    mainWindow.show();
    startBackend();
  });

  // Clean up window reference when closed (prevents dangling handle).
  mainWindow.on('closed', () => { mainWindow = null; });
}

// ── Start backend and then load the app UI ────────────────────────────
async function startBackend() {
  try {
    await startChromaDB();
    await startSearXNG();
    await startPythonServer();
    log('loading app URL');
    mainWindow?.loadURL(APP_URL);
    // Surface one-time warnings for any sidecars that aren't reachable so
    // users know which features degraded silently instead of guessing.
    const warnings = [chromaWarning, searxngWarning].filter(Boolean);
    if (warnings.length && mainWindow) {
      mainWindow.webContents.once('dom-ready', () => {
        mainWindow.webContents.executeJavaScript(`
          (function() {
            try {
              var warnings = ${JSON.stringify(warnings)};
              warnings.forEach(function(msg) {
                var n = document.createElement('div');
                n.style.cssText = 'position:fixed;bottom:16px;left:16px;right:16px;' +
                  'z-index:2147483647;padding:12px 16px;border-radius:8px;' +
                  'background:#2a1a1a;color:#ffb4b4;border:1px solid #ff6b6b;' +
                  'font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;' +
                  'box-shadow:0 4px 12px rgba(0,0,0,0.3);margin-bottom:8px;';
                n.textContent = msg;
                var b = document.createElement('button');
                b.textContent = 'Dismiss';
                b.style.cssText = 'margin-left:12px;background:transparent;border:1px solid #ff6b6b;' +
                  'color:#ffb4b4;padding:4px 10px;border-radius:4px;cursor:pointer;font:inherit;';
                b.onclick = function() { n.remove(); };
                n.appendChild(b);
                document.body.appendChild(n);
              });
            } catch (e) { console.warn('warning banner failed:', e); }
          })();
        `).catch(() => {});
      });
    }
  } catch (err) {
    log(`failed to start server: ${err.message}`);
    if (mainWindow) {
      mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(
        `<h1>Startup Error</h1><pre>${err.message}</pre>`
      )}`);
    }
  }
}

// ── Graceful child-process shutdown ─────────────────────────────────
// SIGTERM first, then SIGKILL after a 5s grace period — mirrors #3769's
// `trap cleanup EXIT TERM INT HUP` behaviour. Bound to before-quit so it
// fires on Cmd+Q, window-all-closed, and SIGINT/SIGTERM.
function shutdownChildren() {
  const children = [
    { name: 'python', proc: pythonProcess },
    { name: 'chroma', proc: chromaProcess },
    { name: 'searxng', proc: searxngProcess },
  ];
  for (const { name, proc } of children) {
    if (proc && !proc.killed) {
      log(`stopping ${name}…`);
      proc.kill('SIGTERM');
    }
  }
  // SIGKILL anything still alive after 5s.
  setTimeout(() => {
    for (const { name, proc } of children) {
      if (proc && !proc.killed) {
        log(`${name} didn't exit on SIGTERM, sending SIGKILL`);
        proc.kill('SIGKILL');
      }
    }
  }, 5000);
}

// ── App lifecycle ─────────────────────────────────────────────────────
// Single-instance guard — prevents multiple Odysseus windows from spawning
// duplicate Python/ChromaDB/SearXNG processes (adopts #3769's guard pattern
// using Electron's built-in requestSingleInstanceLock instead of mkdir lock).
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  log('another instance is already running — exiting');
  app.quit();
} else {
  app.on('second-instance', () => {
    // Focus the existing window instead of spawning a new one.
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

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

  // Graceful shutdown on quit signals (adopts #3769's trap behaviour).
  app.on('before-quit', shutdownChildren);
  app.on('quit', shutdownChildren);

  // On macOS, also handle SIGTERM/SIGINT directly since Electron's default
  // handlers don't always route them through before-quit.
  process.on('SIGTERM', () => { shutdownChildren(); app.quit(); });
  process.on('SIGINT', () => { shutdownChildren(); app.quit(); });
}
