const { app, BrowserWindow, clipboard, ipcMain } = require('electron');
const { spawn, exec } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');

let mainWindow;
let pythonProcess;
const BACKEND_HOST = '127.0.0.1';
const BACKEND_PORT = 7000;
const BACKEND_STARTUP_TIMEOUT_MS = 15 * 60 * 1000;
const STARTUP_LOG_MAX_LINES = 120;
const STARTUP_THEME_FILE = 'startup-theme.json';
const STARTUP_THEME_DEFAULT_COLORS = {
  bg: '#252a32',
  fg: '#d8e2e8',
  panel: '#0b0e0f',
  border: '#244a57',
  cyan: '#86d5e8',
  red: '#e05b67',
  warm: '#f0b45b'
};
const STARTUP_THEME_COLOR_KEYS = Object.keys(STARTUP_THEME_DEFAULT_COLORS);
const STARTUP_STEPS = [
  { id: 'prepare', label: 'Prepare runtime', detail: 'Resolve packaged files and startup paths.' },
  { id: 'data', label: 'Prepare data', detail: 'Use persistent app data and migrate bundled data if needed.' },
  { id: 'python', label: 'Find Python', detail: 'Locate a supported Python 3.11+ runtime.' },
  { id: 'venv', label: 'Python environment', detail: 'Create or reuse the local virtual environment.' },
  { id: 'deps', label: 'Dependencies', detail: 'Install Python packages only when requirements changed.' },
  { id: 'setup', label: 'First-time setup', detail: 'Create folders, database files, and admin/bootstrap state.' },
  { id: 'sidecars', label: 'Image sidecars', detail: 'Start configured local image edit servers when available.' },
  { id: 'backend', label: 'Backend server', detail: 'Start the local Odysseus web server.' },
  { id: 'ready', label: 'Open app', detail: 'Wait for the backend port and switch into Odysseus.' }
];
let startupProgress;

ipcMain.handle('startup:copy-text', (_event, text) => {
  const value = String(text || '');
  if (!value) return false;
  clipboard.writeText(value);
  return true;
});

ipcMain.handle('startup:theme-load', () => readStartupTheme());

ipcMain.handle('startup:theme-save', (_event, theme) => writeStartupTheme(theme));

function isThemeHex(value) {
  return /^#[0-9a-fA-F]{6}$/.test(String(value || ''));
}

function normalizeStartupTheme(theme) {
  if (!theme || typeof theme !== 'object') return null;
  const sourceColors = theme.colors && typeof theme.colors === 'object' ? theme.colors : theme;
  const colors = {};
  for (const key of STARTUP_THEME_COLOR_KEYS) {
    if (isThemeHex(sourceColors[key])) {
      colors[key] = String(sourceColors[key]).toLowerCase();
    } else if (key === 'cyan' || key === 'warm') {
      colors[key] = STARTUP_THEME_DEFAULT_COLORS[key];
    } else {
      return null;
    }
  }
  const name = String(theme.name || 'loader-custom').replace(/[^\w -]/g, '').slice(0, 64) || 'loader-custom';
  return { name, colors };
}

function startupThemePath() {
  return path.join(app.getPath('userData'), STARTUP_THEME_FILE);
}

function readStartupTheme() {
  try {
    const filePath = startupThemePath();
    if (!fs.existsSync(filePath)) return null;
    return normalizeStartupTheme(JSON.parse(fs.readFileSync(filePath, 'utf8')));
  } catch (err) {
    console.warn('Could not read startup theme:', err.message || err);
    return null;
  }
}

function writeStartupTheme(theme) {
  const clean = normalizeStartupTheme(theme);
  if (!clean) return null;
  try {
    const filePath = startupThemePath();
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    fs.writeFileSync(filePath, JSON.stringify(clean, null, 2), 'utf8');
    return clean;
  } catch (err) {
    console.warn('Could not save startup theme:', err.message || err);
    return null;
  }
}

function startupThemeInjectionScript(theme) {
  const clean = normalizeStartupTheme(theme);
  if (!clean) return '';
  const payload = JSON.stringify(clean).replace(/</g, '\\u003c');
  return `
    (() => {
      try {
        const theme = ${payload};
        const colors = theme.colors || {};
        localStorage.setItem('odysseus-theme', JSON.stringify(theme));
        const style = document.documentElement.style;
        style.setProperty('--bg', colors.bg);
        style.setProperty('--fg', colors.fg);
        style.setProperty('--panel', colors.panel);
        style.setProperty('--border', colors.border);
        style.setProperty('--red', colors.red);
        style.setProperty('--accent', colors.red);
        style.setProperty('--color-accent', colors.cyan || '${STARTUP_THEME_DEFAULT_COLORS.cyan}');
        style.setProperty('--accent-primary', colors.cyan || '${STARTUP_THEME_DEFAULT_COLORS.cyan}');
        style.setProperty('--accent-warm', colors.warm || '${STARTUP_THEME_DEFAULT_COLORS.warm}');
        style.setProperty('--warn', colors.warm || '${STARTUP_THEME_DEFAULT_COLORS.warm}');
        style.setProperty('--brand-color', colors.red);
        const meta = document.querySelector('meta[name="theme-color"]');
        if (meta && colors.bg) meta.setAttribute('content', colors.bg);
        fetch('/api/prefs/theme', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ value: theme })
        }).catch(() => {});
        window.dispatchEvent(new CustomEvent('odysseus:startup-theme-applied', { detail: theme }));
      } catch (err) {
        console.warn('Startup theme apply failed:', err);
      }
    })();
  `;
}

function applyStartupThemeToBackendPage() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const currentUrl = mainWindow.webContents.getURL() || '';
  if (!currentUrl.startsWith(`http://${BACKEND_HOST}:${BACKEND_PORT}`)) return;
  const theme = readStartupTheme();
  if (!theme) return;
  mainWindow.webContents.executeJavaScript(startupThemeInjectionScript(theme), true).catch(() => {});
}

function runtimeRoot() {
  // In the installer build, main.js runs from resources/app.asar, while
  // extraFiles such as launch-windows.ps1 live beside the resources folder.
  return app.isPackaged ? path.dirname(process.resourcesPath) : __dirname;
}

function persistentDataDir(rootDir) {
  return app.isPackaged ? path.join(app.getPath('userData'), 'data') : path.join(rootDir, 'data');
}

function hasDirectoryEntries(dirPath) {
  try {
    return fs.existsSync(dirPath) && fs.readdirSync(dirPath).length > 0;
  } catch {
    return false;
  }
}

function copyMissingRecursive(sourceDir, targetDir) {
  if (!fs.existsSync(sourceDir)) return 0;

  fs.mkdirSync(targetDir, { recursive: true });
  let copied = 0;
  for (const entry of fs.readdirSync(sourceDir, { withFileTypes: true })) {
    const sourcePath = path.join(sourceDir, entry.name);
    const targetPath = path.join(targetDir, entry.name);

    if (entry.isDirectory()) {
      copied += copyMissingRecursive(sourcePath, targetPath);
      continue;
    }
    if (!entry.isFile() || fs.existsSync(targetPath)) continue;

    fs.mkdirSync(path.dirname(targetPath), { recursive: true });
    fs.copyFileSync(sourcePath, targetPath);
    copied += 1;
  }
  return copied;
}

function migratePackagedData(rootDir, dataDir, logPath) {
  if (!app.isPackaged) return;

  const legacyDataDir = path.join(rootDir, 'data');
  if (path.resolve(legacyDataDir) === path.resolve(dataDir)) return;
  if (!hasDirectoryEntries(legacyDataDir)) return;

  try {
    const copied = copyMissingRecursive(legacyDataDir, dataDir);
    appendBackendLog(
      logPath,
      copied
        ? `Migrated ${copied} data file(s) from ${legacyDataDir} to ${dataDir}.\n`
        : `Persistent data already present at ${dataDir}; legacy data left untouched at ${legacyDataDir}.\n`
    );
  } catch (err) {
    appendBackendLog(logPath, `Data migration skipped: ${err.stack || err.message || err}\n`);
  }
}

function sqliteUrlForDataDir(dataDir) {
  return `sqlite:///${path.join(dataDir, 'app.db').replace(/\\/g, '/')}`;
}

function stripAnsi(value) {
  return String(value || '').replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, '');
}

function createStartupProgress(rootDir, dataDir, logPath) {
  return {
    startedAt: Date.now(),
    currentAction: 'Preparing the local Odysseus runtime.',
    backendUrl: `http://${BACKEND_HOST}:${BACKEND_PORT}`,
    rootDir,
    dataDir,
    logPath,
    logLines: [],
    steps: STARTUP_STEPS.map((step, index) => ({
      ...step,
      index: index + 1,
      status: index === 0 ? 'running' : 'pending'
    }))
  };
}

function setStartupStep(stepId, status, detail) {
  if (!startupProgress) return;
  const step = startupProgress.steps.find((item) => item.id === stepId);
  if (!step) return;
  step.status = status;
  if (detail) step.detail = detail;
  if (status === 'running') {
    startupProgress.currentAction = step.detail || step.label;
  }
  if (status === 'error') {
    startupProgress.currentAction = detail || `${step.label} failed.`;
  }
}

function completePreviousSteps(stepId) {
  if (!startupProgress) return;
  const target = startupProgress.steps.findIndex((step) => step.id === stepId);
  if (target < 0) return;
  for (let i = 0; i < target; i += 1) {
    if (startupProgress.steps[i].status === 'pending' || startupProgress.steps[i].status === 'running') {
      startupProgress.steps[i].status = 'done';
    }
  }
}

function transitionStartupStep(stepId, detail) {
  completePreviousSteps(stepId);
  setStartupStep(stepId, 'running', detail);
}

function finishStartupStep(stepId, detail) {
  setStartupStep(stepId, 'done', detail);
}

function appendStartupLogLines(message) {
  if (!startupProgress) return;
  const cleaned = stripAnsi(message).replace(/\r/g, '\n');
  for (const rawLine of cleaned.split('\n')) {
    const line = rawLine.trimEnd();
    if (!line.trim()) continue;
    startupProgress.logLines.push(line);
  }
  if (startupProgress.logLines.length > STARTUP_LOG_MAX_LINES) {
    startupProgress.logLines = startupProgress.logLines.slice(-STARTUP_LOG_MAX_LINES);
  }
}

function updateStartupProgressFromOutput(message) {
  if (!startupProgress) return;
  const text = stripAnsi(message);
  const lower = text.toLowerCase();

  appendStartupLogLines(message);

  if (lower.includes('root:')) {
    setStartupStep('prepare', 'done', 'Runtime paths resolved.');
  }
  if (lower.includes('data:')) {
    setStartupStep('data', 'running', 'Using persistent data storage.');
  }
  if (lower.includes('migrated ') || lower.includes('persistent data already present') || lower.includes('data migration skipped')) {
    finishStartupStep('data', 'Data storage is ready.');
  }
  if (lower.includes('launcher:')) {
    transitionStartupStep('python', 'Launching the Windows setup script.');
  }
  if (lower.includes('checking for python')) {
    transitionStartupStep('python', 'Checking for Python 3.11 or newer.');
  }
  if (lower.includes('using python')) {
    const pythonLine = text.split(/\r?\n/).find((line) => line.toLowerCase().includes('using python')) || text;
    finishStartupStep('python', pythonLine.trim());
    transitionStartupStep('venv', 'Checking the local virtual environment.');
  }
  if (lower.includes('creating virtual environment')) {
    transitionStartupStep('venv', 'Creating the local Python virtual environment.');
  }
  if (lower.includes('venv already exists') || lower.includes('installing dependencies') || lower.includes('dependencies already match') || lower.includes('running first-time setup')) {
    finishStartupStep('venv', 'Python environment is ready.');
  }
  if (lower.includes('installing dependencies')) {
    transitionStartupStep('deps', 'Installing Python dependencies. First launch can take a few minutes.');
  }
  if (lower.includes('dependencies already match') || lower.includes('set-content') || lower.includes('running first-time setup')) {
    const depsStep = startupProgress.steps.find((step) => step.id === 'deps');
    const depsDetail = lower.includes('dependencies already match')
      ? 'Dependencies already match requirements.txt.'
      : (depsStep && depsStep.status === 'done' ? depsStep.detail : 'Dependencies installed.');
    finishStartupStep('deps', depsDetail);
  }
  if (lower.includes('running first-time setup')) {
    transitionStartupStep('setup', 'Running first-time app setup.');
  }
  if (lower.includes('checking local image edit sidecars')) {
    finishStartupStep('setup', 'First-time setup finished.');
    transitionStartupStep('sidecars', 'Checking configured local image edit sidecars.');
  }
  if (lower.includes('[image-sidecars]') || lower.includes('image sidecar autostart')) {
    finishStartupStep('sidecars', 'Image sidecar check finished.');
  }
  if (lower.includes('starting odysseus at')) {
    finishStartupStep('sidecars', 'Image sidecar check finished.');
    transitionStartupStep('backend', `Starting backend at ${startupProgress.backendUrl}.`);
  }
  if (lower.includes('application startup complete') || lower.includes('uvicorn running on')) {
    finishStartupStep('backend', 'Backend process is running.');
    transitionStartupStep('ready', 'Waiting for the backend health check.');
  }
  if (lower.includes('odysseus is already running')) {
    for (const step of startupProgress.steps) step.status = 'done';
    startupProgress.currentAction = 'Existing backend found; opening Odysseus.';
  }
  if (lower.includes('backend is ready')) {
    finishStartupStep('backend', 'Backend is ready.');
    finishStartupStep('ready', 'Opening Odysseus.');
    startupProgress.currentAction = 'Backend is ready. Opening Odysseus.';
  }
  if (/\berror:|\btraceback\b|startup failed|launcher failed|timed out waiting/i.test(text)) {
    const running = startupProgress.steps.find((step) => step.status === 'running');
    if (running) {
      running.status = 'error';
      running.detail = text.trim().slice(0, 240) || `${running.label} failed.`;
    }
    startupProgress.currentAction = 'Startup needs attention. Check the live output below.';
  }
}

function sendStartupProgress() {
  if (!mainWindow || !startupProgress || mainWindow.isDestroyed()) return;
  const payload = JSON.stringify(startupProgress);
  mainWindow.webContents.executeJavaScript(
    `window.__odysseusStartupUpdate && window.__odysseusStartupUpdate(${payload});`,
    true
  ).catch(() => {});
}

// Let Chromium pick the stable rendering path by default. The standalone
// Windows build can paint a blank white window on some drivers when zero-copy
// or RawDraw is forced, even though the startup page has loaded.
if (process.env.ODYSSEUS_EXPERIMENTAL_CHROMIUM_GPU === '1') {
  app.commandLine.appendSwitch('ignore-gpu-blocklist');
  app.commandLine.appendSwitch('enable-gpu-rasterization');
  app.commandLine.appendSwitch('enable-zero-copy');
  app.commandLine.appendSwitch('enable-accelerated-video-decode');
  app.commandLine.appendSwitch('enable-features', 'VaapiVideoDecoder,CanvasOopRasterization,RawDraw');
}

function htmlPage(title, body) {
  return `data:text/html;charset=utf-8,${encodeURIComponent(`<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${title}</title>
  <style>
    :root {
      color-scheme: dark;
      --ody-bg: #252a32;
      --ody-shell: #080b0c;
      --ody-panel: #0b0e0f;
      --ody-panel-soft: #10151a;
      --ody-border: #244a57;
      --ody-border-soft: rgba(64, 112, 128, 0.42);
      --ody-text: #d8e2e8;
      --ody-muted: #8198a5;
      --ody-dim: #617882;
      --ody-cyan: #86d5e8;
      --ody-cyan-soft: #4a8797;
      --ody-coral: #e05b67;
      --ody-warm: #f0b45b;
      --ody-warm-soft: #806746;
      --ody-on-accent: #080b0c;
      --ody-terminal: #050708;
      --ody-cyan-rgb: 134, 213, 232;
      --ody-coral-rgb: 224, 91, 103;
      --ody-warm-rgb: 240, 180, 91;
      --ody-progress: 6%;
    }
    * { box-sizing: border-box; }
    html, body {
      width: 100%;
      height: 100%;
      min-height: 0;
      margin: 0;
      background: var(--ody-bg);
      color: var(--ody-text);
      font-family: "Segoe UI", system-ui, sans-serif;
    }
    body {
      height: 100vh;
      min-height: 0;
      display: grid;
      place-items: center;
      padding: 28px;
      overflow: hidden;
      background:
        linear-gradient(180deg, color-mix(in srgb, var(--ody-bg) 88%, #111720) 0%, var(--ody-bg) 58%, color-mix(in srgb, var(--ody-bg) 92%, #07090b) 100%);
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      opacity: 0.22;
      background-image:
        linear-gradient(rgba(var(--ody-cyan-rgb), 0.16) 1px, transparent 1px),
        linear-gradient(90deg, rgba(var(--ody-cyan-rgb), 0.13) 1px, transparent 1px);
      background-size: 56px 56px;
      mask-image: linear-gradient(180deg, transparent 0%, #000 16%, #000 80%, transparent 100%);
    }
    main { position: relative; width: min(1460px, 100%); height: 100%; min-height: 0; line-height: 1.5; }
    h1 { margin: 0; font-size: 46px; line-height: 1.05; letter-spacing: 0; color: var(--ody-coral); text-wrap: balance; }
    h2 { margin: 0; font-size: 13px; text-transform: uppercase; letter-spacing: 0; color: var(--ody-cyan); }
    p { margin: 10px 0; color: var(--ody-text); font-size: 15px; }
    code { color: var(--ody-cyan); background: var(--ody-panel-soft); border: 1px solid var(--ody-border); border-radius: 6px; padding: 2px 6px; overflow-wrap: anywhere; }
    .startup-shell { display: grid; grid-template-rows: auto auto minmax(0, 1fr) auto; gap: 16px; height: 100%; min-height: 0; overflow: hidden; }
    .startup-top { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 32px; align-items: end; }
    .startup-title-block { min-width: 0; }
    .eyebrow { margin: 0 0 9px; color: var(--ody-cyan); font-size: 12px; letter-spacing: 0; text-transform: uppercase; }
    .current { margin: 12px 0 0; max-width: 780px; color: var(--ody-text); font-size: 18px; }
    .startup-summary {
      display: grid;
      grid-template-columns: 76px minmax(170px, 1fr);
      gap: 14px;
      align-items: center;
      justify-items: end;
      min-width: 286px;
      padding: 10px 12px;
      border: 1px solid var(--ody-border-soft);
      border-radius: 8px;
      background: linear-gradient(135deg, rgba(var(--ody-cyan-rgb), 0.08), rgba(var(--ody-warm-rgb), 0.05));
    }
    .startup-meter {
      position: relative;
      width: 68px;
      height: 68px;
      display: grid;
      place-items: center;
      border-radius: 50%;
      background: conic-gradient(var(--ody-warm) 0 var(--ody-progress), rgba(var(--ody-cyan-rgb), 0.14) var(--ody-progress) 100%);
      box-shadow: 0 0 0 1px var(--ody-border), 0 12px 34px rgba(0, 0, 0, 0.22);
    }
    .startup-meter::before {
      content: "";
      position: absolute;
      inset: 7px;
      border-radius: inherit;
      background: var(--ody-panel);
      border: 1px solid var(--ody-border-soft);
    }
    .startup-meter span { position: relative; color: var(--ody-warm); font-size: 15px; font-weight: 750; font-variant-numeric: tabular-nums; }
    .meta { display: grid; gap: 6px; justify-items: end; color: var(--ody-muted); font-size: 13px; }
    .meta div { white-space: nowrap; }
    .meta .phase { color: var(--ody-text); font-weight: 700; }
    .meta strong { color: var(--ody-cyan); font-weight: 700; }
    .bar {
      width: 100%;
      height: 10px;
      overflow: hidden;
      border-radius: 999px;
      background: color-mix(in srgb, var(--ody-shell) 82%, transparent);
      border: 1px solid var(--ody-border);
      box-shadow: inset 0 1px 8px rgba(0, 0, 0, 0.36);
    }
    .bar span {
      display: block;
      width: 8%;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--ody-cyan), var(--ody-warm) 56%, var(--ody-coral));
      box-shadow: 0 0 18px rgba(var(--ody-warm-rgb), 0.34);
      transition: width 220ms ease;
    }
    .startup-workbench { display: grid; grid-template-columns: minmax(0, 1fr) minmax(330px, 0.36fr); gap: 18px; align-items: stretch; min-height: 0; overflow: hidden; }
    .status-grid { display: grid; grid-template-columns: minmax(286px, 0.72fr) minmax(420px, 1.28fr); gap: 18px; align-items: stretch; min-width: 0; min-height: 0; overflow: hidden; }
    .panel {
      position: relative;
      display: flex;
      flex-direction: column;
      min-width: 0;
      min-height: 0;
      border: 1px solid var(--ody-border);
      background: linear-gradient(180deg, color-mix(in srgb, var(--ody-panel) 88%, var(--ody-panel-soft)), var(--ody-panel));
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 16px 42px rgba(0, 0, 0, 0.22);
    }
    .panel::before {
      content: "";
      position: absolute;
      inset: 0 0 auto;
      height: 2px;
      background: linear-gradient(90deg, var(--ody-cyan), var(--ody-warm), var(--ody-coral));
      opacity: 0.72;
      pointer-events: none;
    }
    .panel-head { flex: 0 0 auto; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 16px; border-bottom: 1px solid var(--ody-border-soft); background: rgba(var(--ody-cyan-rgb), 0.035); }
    .panel-head span { color: var(--ody-muted); font-size: 12px; }
    .startup-theme-panel { display: flex; flex-direction: column; }
    .startup-theme-panel button { margin: 0; }
    .startup-theme-body { flex: 1 1 auto; min-height: 0; overflow: auto; display: grid; gap: 11px; padding: 14px; scrollbar-color: var(--ody-border) transparent; }
    .theme-preview { display: grid; gap: 9px; padding: 11px; border: 1px solid var(--ody-border-soft); border-radius: 8px; background: linear-gradient(135deg, var(--ody-panel-soft), color-mix(in srgb, var(--ody-panel) 76%, var(--ody-warm))); }
    .theme-preview-top { display: flex; align-items: center; justify-content: space-between; gap: 10px; color: var(--ody-muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0; }
    .theme-preview-dot { width: 13px; height: 13px; border-radius: 50%; background: var(--ody-coral); box-shadow: 0 0 0 4px rgba(var(--ody-coral-rgb), 0.16), -16px 0 0 -3px var(--ody-warm); }
    .theme-preview-message { min-height: 48px; border: 1px solid var(--ody-border); border-radius: 8px; padding: 10px 11px; color: var(--ody-text); background: var(--ody-panel); font-size: 12px; box-shadow: inset 0 0 0 1px rgba(var(--ody-cyan-rgb), 0.04); }
    .theme-preview-bars { display: grid; gap: 6px; }
    .theme-preview-bars span { display: block; height: 5px; border-radius: 999px; background: var(--ody-border); opacity: 0.86; }
    .theme-preview-bars span:nth-child(1) { width: 96%; background: linear-gradient(90deg, var(--ody-cyan-soft), var(--ody-border)); }
    .theme-preview-bars span:nth-child(2) { width: 72%; background: var(--ody-cyan); opacity: 0.72; }
    .theme-preview-bars span:nth-child(3) { width: 52%; background: linear-gradient(90deg, var(--ody-coral), var(--ody-warm)); opacity: 0.8; }
    .theme-presets { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 7px; }
    .theme-preset { height: 43px; overflow: hidden; border: 1px solid var(--ody-border); border-radius: 7px; background: var(--ody-panel-soft); color: var(--ody-text); padding: 0; cursor: pointer; transition: border-color 160ms ease, transform 160ms ease; }
    .theme-preset:hover, .theme-preset.active { border-color: var(--ody-coral); }
    .theme-preset:hover { transform: translateY(-1px); }
    .theme-preset.active { box-shadow: 0 0 0 1px rgba(var(--ody-warm-rgb), 0.36); }
    .theme-preset-swatch { display: grid; grid-template-columns: 1.05fr 1fr 1fr 0.82fr 0.82fr 0.82fr; height: 23px; }
    .theme-preset-swatch span { display: block; }
    .theme-preset-name { display: block; padding: 2px 4px 0; color: var(--ody-muted); font-size: 10px; line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .theme-color-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .theme-color-field { display: grid; grid-template-columns: minmax(0, 1fr) 38px; align-items: center; gap: 8px; min-width: 0; padding: 8px; border: 1px solid var(--ody-border-soft); border-radius: 7px; background: color-mix(in srgb, var(--ody-panel-soft) 88%, transparent); }
    .theme-color-field span { display: block; color: var(--ody-muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0; }
    .theme-color-field small { display: block; margin-top: 1px; color: var(--ody-text); font-size: 11px; overflow: hidden; text-overflow: ellipsis; }
    .theme-color-field input[type="color"], .theme-harmony input[type="color"] { width: 38px; height: 28px; padding: 0; border: 1px solid var(--ody-border); border-radius: 6px; background: transparent; cursor: pointer; }
    .theme-harmony { display: grid; grid-template-columns: 38px minmax(0, 1fr); gap: 8px; align-items: center; }
    .theme-harmony select { min-width: 0; width: 100%; height: 31px; border: 1px solid var(--ody-border); border-radius: 7px; background: var(--ody-panel-soft); color: var(--ody-text); font: inherit; font-size: 12px; padding: 0 8px; }
    .theme-mode { display: grid; grid-template-columns: repeat(2, 1fr); grid-column: 1 / -1; border: 1px solid var(--ody-border); border-radius: 7px; overflow: hidden; }
    .theme-mode button { border: 0; border-radius: 0; padding: 7px 8px; color: var(--ody-muted); background: var(--ody-panel); font-size: 12px; }
    .theme-mode button.active { color: var(--ody-on-accent); background: linear-gradient(90deg, var(--ody-cyan), var(--ody-warm)); font-weight: 700; }
    .theme-actions { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 8px; }
    .theme-actions button { min-height: 34px; padding: 8px 10px; font-size: 12px; }
    .theme-primary { color: var(--ody-on-accent); background: linear-gradient(90deg, var(--ody-coral), var(--ody-warm)); border-color: var(--ody-coral); font-weight: 700; }
    .theme-secondary { color: var(--ody-text); background: var(--ody-panel-soft); }
    .steps { flex: 1 1 auto; list-style: none; margin: 0; padding: 7px 0; min-height: 0; overflow: auto; scrollbar-color: var(--ody-border) transparent; }
    .step { position: relative; display: grid; grid-template-columns: 28px minmax(0, 1fr); gap: 12px; padding: 9px 16px; color: var(--ody-muted); }
    .step + .step { border-top: 1px solid var(--ody-border-soft); }
    .step-mark { width: 24px; height: 24px; display: grid; place-items: center; border-radius: 50%; border: 1px solid var(--ody-border); color: var(--ody-dim); font-size: 11px; font-weight: 700; }
    .step strong { display: block; color: var(--ody-text); font-size: 14px; font-weight: 650; }
    .step small { display: block; margin-top: 2px; color: var(--ody-muted); font-size: 12px; overflow-wrap: anywhere; }
    .step.done .step-mark { border-color: var(--ody-cyan); color: var(--ody-on-accent); background: var(--ody-cyan); }
    .step.done strong { color: var(--ody-text); }
    .step.running { background: linear-gradient(90deg, rgba(var(--ody-cyan-rgb), 0.12), rgba(var(--ody-warm-rgb), 0.07)); }
    .step.running::before { content: ""; position: absolute; inset: 0 auto 0 0; width: 3px; background: var(--ody-warm); }
    .step.running .step-mark { border-color: var(--ody-warm); color: var(--ody-warm); box-shadow: 0 0 0 3px rgba(var(--ody-warm-rgb), 0.16); }
    .step.error { background: rgba(var(--ody-coral-rgb), 0.12); }
    .step.error .step-mark { border-color: var(--ody-coral); color: var(--ody-coral); }
    .paths { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
    .path-item { min-width: 0; border: 1px solid var(--ody-border); border-radius: 8px; background: linear-gradient(180deg, var(--ody-panel), color-mix(in srgb, var(--ody-panel) 84%, var(--ody-panel-soft))); padding: 11px 12px; box-shadow: 0 10px 28px rgba(0, 0, 0, 0.16); }
    .path-item span { display: block; color: var(--ody-muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0; }
    .path-value { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
    .path-item code { flex: 1 1 auto; min-width: 0; display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .copy-btn { flex: 0 0 auto; position: relative; width: 22px; height: 22px; margin: 0; padding: 0; border: 1px solid var(--ody-border); border-radius: 5px; background: var(--ody-panel-soft); color: var(--ody-cyan); cursor: pointer; }
    .copy-btn::before, .copy-btn::after { content: ""; position: absolute; width: 8px; height: 10px; border: 1.25px solid currentColor; border-radius: 2px; }
    .copy-btn::before { left: 6px; top: 7px; opacity: 0.62; }
    .copy-btn::after { left: 8px; top: 5px; background: var(--ody-panel-soft); }
    .copy-btn:hover { border-color: var(--ody-warm); color: var(--ody-text); }
    .copy-btn.copied { border-color: var(--ody-cyan); color: var(--ody-cyan); }
    .terminal { flex: 1 1 auto; min-height: 0; overflow: auto; padding: 14px 16px; background: var(--ody-terminal); border-radius: 0 0 8px 8px; font: 12px/1.55 Consolas, "Cascadia Mono", monospace; color: var(--ody-text); scrollbar-color: var(--ody-border) transparent; }
    .terminal-line { display: grid; grid-template-columns: 18px minmax(0, 1fr); gap: 8px; white-space: pre-wrap; overflow-wrap: anywhere; }
    .terminal-line span { color: var(--ody-warm); user-select: none; }
    .empty-log { color: var(--ody-dim); font-style: italic; }
    .hint { color: var(--ody-muted); }
    button { margin-top: 16px; border: 1px solid var(--ody-border); border-radius: 8px; background: var(--ody-panel-soft); color: var(--ody-text); padding: 10px 14px; font: inherit; cursor: pointer; }
    button:hover { border-color: var(--ody-warm); }
    @media (max-width: 1160px) {
      .startup-workbench, .status-grid { grid-template-columns: 1fr; }
      .theme-presets { grid-template-columns: repeat(6, minmax(0, 1fr)); }
    }
    @media (max-width: 880px) {
      body { padding: 20px; }
      .startup-top, .startup-workbench, .status-grid, .paths { grid-template-columns: 1fr; }
      h1 { font-size: 34px; }
      .startup-summary { grid-template-columns: 64px minmax(0, 1fr); justify-items: start; min-width: 0; }
      .startup-meter { width: 58px; height: 58px; }
      .meta { justify-items: start; }
      .terminal { min-height: 300px; }
      .theme-presets { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .theme-color-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body><main>${body}</main></body>
</html>`)}`;
}

function startupPage(progress) {
  const initialState = JSON.stringify(progress || {}).replace(/</g, '\\u003c');
  return htmlPage('Starting Odysseus', `
    <section class="startup-shell" aria-live="polite">
      <div class="startup-top">
        <div class="startup-title-block">
          <p class="eyebrow">Local installation and startup</p>
          <h1>Starting Odysseus</h1>
          <p class="current" id="startup-current">Preparing the local Odysseus runtime.</p>
        </div>
        <div class="startup-summary">
          <div class="startup-meter" id="startup-meter" aria-hidden="true"><span id="startup-meter-label">0%</span></div>
          <div class="meta">
            <div class="phase" id="startup-phase">Preparing runtime</div>
            <div>Elapsed <strong id="startup-elapsed">0s</strong></div>
            <div><strong id="startup-done">0</strong> of <strong id="startup-total">0</strong> steps complete</div>
          </div>
        </div>
      </div>
      <div class="bar" aria-hidden="true"><span id="startup-bar"></span></div>
      <div class="startup-workbench">
        <div class="status-grid">
          <section class="panel">
            <div class="panel-head">
              <h2>Install Process</h2>
              <span id="startup-percent">0%</span>
            </div>
            <ol class="steps" id="startup-steps"></ol>
          </section>
          <section class="panel">
            <div class="panel-head">
              <h2>Live Output</h2>
              <span id="startup-log-count">Waiting for launcher output</span>
            </div>
            <div class="terminal" id="startup-terminal">
              <div class="empty-log">Startup output will appear here as each step runs.</div>
            </div>
          </section>
        </div>
        <section class="panel startup-theme-panel">
          <div class="panel-head">
            <h2>Theme Studio</h2>
            <span id="startup-theme-status">Draft</span>
          </div>
          <div class="startup-theme-body">
            <div class="theme-preview" aria-hidden="true">
              <div class="theme-preview-top">
                <span>Preview</span>
                <span class="theme-preview-dot"></span>
              </div>
              <div class="theme-preview-message">
                <div class="theme-preview-bars">
                  <span></span>
                  <span></span>
                  <span></span>
                </div>
              </div>
            </div>
            <div class="theme-presets" id="startup-theme-presets"></div>
            <div class="theme-color-grid">
              <label class="theme-color-field">
                <span>Background<small id="startup-theme-bg-text">#252a32</small></span>
                <input type="color" id="startup-theme-bg" data-theme-color="bg" value="#252a32">
              </label>
              <label class="theme-color-field">
                <span>Text<small id="startup-theme-fg-text">#d8e2e8</small></span>
                <input type="color" id="startup-theme-fg" data-theme-color="fg" value="#d8e2e8">
              </label>
              <label class="theme-color-field">
                <span>Panel<small id="startup-theme-panel-text">#0b0e0f</small></span>
                <input type="color" id="startup-theme-panel-color" data-theme-color="panel" value="#0b0e0f">
              </label>
              <label class="theme-color-field">
                <span>Border<small id="startup-theme-border-text">#244a57</small></span>
                <input type="color" id="startup-theme-border" data-theme-color="border" value="#244a57">
              </label>
              <label class="theme-color-field">
                <span>Highlight<small id="startup-theme-cyan-text">#86d5e8</small></span>
                <input type="color" id="startup-theme-cyan" data-theme-color="cyan" value="#86d5e8">
              </label>
              <label class="theme-color-field">
                <span>Accent<small id="startup-theme-red-text">#e05b67</small></span>
                <input type="color" id="startup-theme-red" data-theme-color="red" value="#e05b67">
              </label>
              <label class="theme-color-field">
                <span>Signal<small id="startup-theme-warm-text">#f0b45b</small></span>
                <input type="color" id="startup-theme-warm" data-theme-color="warm" value="#f0b45b">
              </label>
            </div>
            <div class="theme-harmony">
              <input type="color" id="startup-harmony-accent" value="#e05b67" aria-label="Harmony accent">
              <select id="startup-harmony-type" aria-label="Harmony">
                <option value="complementary">Complementary</option>
                <option value="analogous">Analogous</option>
                <option value="triadic">Triadic</option>
                <option value="monochromatic">Monochrome</option>
              </select>
              <div class="theme-mode" aria-label="Mode">
                <button type="button" class="active" data-theme-mode="dark">Dark</button>
                <button type="button" data-theme-mode="light">Light</button>
              </div>
            </div>
            <div class="theme-actions">
              <button class="theme-primary" type="button" id="startup-theme-apply">Apply Theme</button>
              <button class="theme-secondary" type="button" id="startup-theme-generate">Generate</button>
              <button class="theme-secondary" type="button" id="startup-theme-reset">Reset</button>
            </div>
          </div>
        </section>
      </div>
      <div class="paths">
        <div class="path-item">
          <span>Backend</span>
          <div class="path-value">
            <code id="startup-backend"></code>
            <button class="copy-btn" type="button" data-copy-target="startup-backend" aria-label="Copy backend URL" title="Copy backend URL"></button>
          </div>
        </div>
        <div class="path-item">
          <span>Data</span>
          <div class="path-value">
            <code id="startup-data"></code>
            <button class="copy-btn" type="button" data-copy-target="startup-data" aria-label="Copy data path" title="Copy data path"></button>
          </div>
        </div>
        <div class="path-item">
          <span>Log</span>
          <div class="path-value">
            <code id="startup-log-path"></code>
            <button class="copy-btn" type="button" data-copy-target="startup-log-path" aria-label="Copy log path" title="Copy log path"></button>
          </div>
        </div>
      </div>
    </section>
    <script>
      (() => {
        let state = ${initialState};
        let followLog = true;
        const esc = (value) => String(value || '')
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;')
          .replace(/'/g, '&#39;');
        const THEME_PRESETS = {
          dark: { bg: '#252a32', fg: '#d8e2e8', panel: '#0b0e0f', border: '#244a57', cyan: '#86d5e8', red: '#e05b67', warm: '#f0b45b' },
          light: { bg: '#f6f7f9', fg: '#1f2933', panel: '#eef3f8', border: '#cfd8e3', cyan: '#2c7f95', red: '#d34b56', warm: '#b8792d' },
          midnight: { bg: '#050b16', fg: '#dce8ff', panel: '#0a1324', border: '#254367', cyan: '#78d7ff', red: '#6ea8ff', warm: '#ffd166' },
          forest: { bg: '#101810', fg: '#dfeadd', panel: '#172216', border: '#2e4b30', cyan: '#90dac1', red: '#8fcf73', warm: '#d7c76a' },
          ocean: { bg: '#071821', fg: '#d9f2f7', panel: '#0d2530', border: '#1c4f61', cyan: '#5bd8ff', red: '#45a6d8', warm: '#f8c75b' },
          ume: { bg: '#24141a', fg: '#ffe8ef', panel: '#301b23', border: '#6f3445', cyan: '#8fd8ff', red: '#ff7aa2', warm: '#ffb35c' },
          copper: { bg: '#211915', fg: '#f1dfd2', panel: '#2b201a', border: '#704c35', cyan: '#8dcbd2', red: '#d98a54', warm: '#f0b45b' },
          terminal: { bg: '#04100a', fg: '#c9ffd8', panel: '#07170d', border: '#245c37', cyan: '#77ffc8', red: '#6df58a', warm: '#f4d35e' }
        };
        const THEME_LABELS = {
          dark: 'Dark',
          light: 'Light',
          midnight: 'Midnight',
          forest: 'Forest',
          ocean: 'Ocean',
          ume: 'Ume',
          copper: 'Copper',
          terminal: 'Terminal'
        };
        const THEME_COLOR_KEYS = ['bg', 'fg', 'panel', 'border', 'cyan', 'red', 'warm'];
        const DEFAULT_THEME_DRAFT = { name: 'dark', colors: { ...THEME_PRESETS.dark } };
        let themeDraft = { name: DEFAULT_THEME_DRAFT.name, colors: { ...DEFAULT_THEME_DRAFT.colors } };
        let themeMode = 'dark';

        const isHex = (value) => /^#[0-9a-fA-F]{6}$/.test(String(value || ''));
        const cloneColors = (colors) => {
          const clean = {};
          for (const key of THEME_COLOR_KEYS) clean[key] = isHex(colors && colors[key]) ? String(colors[key]).toLowerCase() : DEFAULT_THEME_DRAFT.colors[key];
          return clean;
        };
        const hexToRgb = (hex) => {
          const raw = String(hex || '#000000').replace('#', '');
          return [parseInt(raw.slice(0, 2), 16), parseInt(raw.slice(2, 4), 16), parseInt(raw.slice(4, 6), 16)];
        };
        const rgbToHex = (r, g, b) => '#' + [r, g, b].map((value) => Math.round(Math.max(0, Math.min(255, value))).toString(16).padStart(2, '0')).join('');
        const mixHex = (a, b, amount) => {
          const ar = hexToRgb(a);
          const br = hexToRgb(b);
          const t = Math.max(0, Math.min(1, Number(amount) || 0));
          return rgbToHex(ar[0] * (1 - t) + br[0] * t, ar[1] * (1 - t) + br[1] * t, ar[2] * (1 - t) + br[2] * t);
        };
        const rgbaHex = (hex, alpha) => {
          const rgb = hexToRgb(hex);
          return 'rgba(' + rgb[0] + ', ' + rgb[1] + ', ' + rgb[2] + ', ' + Math.max(0, Math.min(1, Number(alpha) || 0)) + ')';
        };
        const hexToHsl = (hex) => {
          const rgb = hexToRgb(hex).map((value) => value / 255);
          const max = Math.max(rgb[0], rgb[1], rgb[2]);
          const min = Math.min(rgb[0], rgb[1], rgb[2]);
          let h = 0;
          let s = 0;
          const l = (max + min) / 2;
          if (max !== min) {
            const d = max - min;
            s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
            if (max === rgb[0]) h = (rgb[1] - rgb[2]) / d + (rgb[1] < rgb[2] ? 6 : 0);
            else if (max === rgb[1]) h = (rgb[2] - rgb[0]) / d + 2;
            else h = (rgb[0] - rgb[1]) / d + 4;
            h /= 6;
          }
          return [h * 360, s * 100, l * 100];
        };
        const hslToHex = (h, s, l) => {
          const hue = ((h % 360) + 360) % 360;
          const sat = Math.max(0, Math.min(100, s)) / 100;
          const light = Math.max(0, Math.min(100, l)) / 100;
          const a = sat * Math.min(light, 1 - light);
          const f = (n) => {
            const k = (n + hue / 30) % 12;
            return light - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
          };
          return rgbToHex(f(0) * 255, f(8) * 255, f(4) * 255);
        };
        const shiftLightness = (hex, amount) => {
          const hsl = hexToHsl(hex);
          return hslToHex(hsl[0], hsl[1], hsl[2] + amount);
        };
        const isDarkTheme = (colors) => hexToHsl((colors && colors.bg) || DEFAULT_THEME_DRAFT.colors.bg)[2] < 50;
        const generateHarmonyColors = (accentHex, harmonyType, mode) => {
          const hsl = hexToHsl(accentHex);
          const h = hsl[0];
          const s = Math.max(28, Math.min(82, hsl[1]));
          const isDark = mode !== 'light';
          let bgH = h;
          let fgH = (h + 180) % 360;
          let panelH = h;
          let borderH = h;
          let cyanH = h + 185;
          let warmH = h + 38;
          let accent = accentHex;
          if (harmonyType === 'analogous') {
            bgH = h - 18;
            panelH = h - 8;
            fgH = h + 32;
            borderH = h + 18;
            cyanH = h + 170;
            warmH = h + 46;
          } else if (harmonyType === 'triadic') {
            bgH = h + 120;
            panelH = h + 105;
            fgH = h + 240;
            borderH = h + 120;
            cyanH = h + 185;
            warmH = h + 60;
          } else if (harmonyType === 'monochromatic') {
            bgH = h;
            panelH = h;
            fgH = h;
            borderH = h;
            cyanH = h;
            warmH = h;
            accent = hslToHex(h, s, isDark ? 66 : 48);
          }
          return {
            bg: hslToHex(bgH, isDark ? s * 0.26 : s * 0.18, isDark ? 13 : 96),
            fg: hslToHex(fgH, isDark ? Math.max(s * 0.36, 18) : Math.max(s * 0.34, 16), isDark ? 88 : 18),
            panel: hslToHex(panelH, isDark ? s * 0.24 : s * 0.12, isDark ? 7 : 99),
            border: hslToHex(borderH, isDark ? s * 0.42 : s * 0.2, isDark ? 31 : 78),
            cyan: hslToHex(cyanH, isDark ? Math.min(88, Math.max(34, s + 8)) : Math.min(72, Math.max(30, s * 0.74)), isDark ? 72 : 38),
            red: accent,
            warm: hslToHex(warmH, isDark ? Math.max(s * 0.62, 38) : Math.max(s * 0.58, 32), isDark ? 68 : 44)
          };
        };
        const applyStartupColors = (colors) => {
          const root = document.documentElement.style;
          const clean = cloneColors(colors);
          const dark = isDarkTheme(clean);
          const coralRgb = hexToRgb(clean.red);
          const cyanRgb = hexToRgb(clean.cyan);
          const warmRgb = hexToRgb(clean.warm);
          root.setProperty('--ody-bg', clean.bg);
          root.setProperty('--ody-shell', shiftLightness(clean.bg, dark ? -5 : 3));
          root.setProperty('--ody-panel', clean.panel);
          root.setProperty('--ody-panel-soft', shiftLightness(clean.panel, dark ? 7 : -5));
          root.setProperty('--ody-border', clean.border);
          root.setProperty('--ody-border-soft', rgbaHex(clean.border, dark ? 0.54 : 0.68));
          root.setProperty('--ody-text', clean.fg);
          root.setProperty('--ody-muted', mixHex(clean.fg, clean.bg, dark ? 0.42 : 0.5));
          root.setProperty('--ody-dim', mixHex(clean.fg, clean.bg, 0.62));
          root.setProperty('--ody-cyan', clean.cyan);
          root.setProperty('--ody-cyan-soft', mixHex(clean.cyan, clean.bg, 0.45));
          root.setProperty('--ody-coral', clean.red);
          root.setProperty('--ody-warm', clean.warm);
          root.setProperty('--ody-warm-soft', mixHex(clean.warm, clean.bg, dark ? 0.48 : 0.62));
          root.setProperty('--ody-on-accent', dark ? shiftLightness(clean.bg, -9) : clean.fg);
          root.setProperty('--ody-terminal', dark ? shiftLightness(clean.panel, -3) : mixHex(clean.panel, clean.fg, 0.08));
          root.setProperty('--ody-coral-rgb', coralRgb.join(', '));
          root.setProperty('--ody-cyan-rgb', cyanRgb.join(', '));
          root.setProperty('--ody-warm-rgb', warmRgb.join(', '));
        };
        const startupThemeObject = () => ({ name: themeDraft.name || 'loader-custom', colors: cloneColors(themeDraft.colors) });
        const setThemeStatus = (text) => {
          const status = document.getElementById('startup-theme-status');
          if (status) status.textContent = text || 'Draft';
        };
        const renderThemeStudio = () => {
          const presets = document.getElementById('startup-theme-presets');
          if (presets) {
            presets.innerHTML = Object.keys(THEME_PRESETS).map((name) => {
              const colors = THEME_PRESETS[name];
              const active = themeDraft.name === name ? ' active' : '';
              return '<button class="theme-preset' + active + '" type="button" data-theme-preset="' + esc(name) + '" title="' + esc(THEME_LABELS[name] || name) + '">' +
                '<span class="theme-preset-swatch">' +
                  '<span style="background:' + esc(colors.bg) + '"></span>' +
                  '<span style="background:' + esc(colors.panel) + '"></span>' +
                  '<span style="background:' + esc(colors.border) + '"></span>' +
                  '<span style="background:' + esc(colors.cyan) + '"></span>' +
                  '<span style="background:' + esc(colors.red) + '"></span>' +
                  '<span style="background:' + esc(colors.warm) + '"></span>' +
                '</span>' +
                '<span class="theme-preset-name">' + esc(THEME_LABELS[name] || name) + '</span>' +
              '</button>';
            }).join('');
          }
          applyStartupColors(themeDraft.colors);
          for (const key of THEME_COLOR_KEYS) {
            const input = document.querySelector('[data-theme-color="' + key + '"]');
            const label = document.getElementById('startup-theme-' + key + '-text');
            const value = themeDraft.colors[key];
            if (input && input.value !== value) input.value = value;
            if (label) label.textContent = value;
          }
          const accent = document.getElementById('startup-harmony-accent');
          if (accent && accent.value !== themeDraft.colors.red) accent.value = themeDraft.colors.red;
          document.querySelectorAll('[data-theme-mode]').forEach((button) => {
            button.classList.toggle('active', button.dataset.themeMode === themeMode);
          });
        };
        const loadStartupTheme = async () => {
          let saved = null;
          if (window.odysseusStartupTheme && window.odysseusStartupTheme.loadTheme) {
            try { saved = await window.odysseusStartupTheme.loadTheme(); } catch (_) {}
          }
          if (!saved) {
            try { saved = JSON.parse(localStorage.getItem('odysseus-theme') || 'null'); } catch (_) {}
          }
          if (saved && saved.colors) {
            themeDraft = {
              name: THEME_PRESETS[saved.name] ? saved.name : 'loader-custom',
              colors: cloneColors(saved.colors)
            };
            themeMode = isDarkTheme(themeDraft.colors) ? 'dark' : 'light';
          }
          renderThemeStudio();
        };
        const saveStartupTheme = async () => {
          const theme = startupThemeObject();
          setThemeStatus('Applying');
          try { localStorage.setItem('odysseus-theme', JSON.stringify(theme)); } catch (_) {}
          let saved = true;
          if (window.odysseusStartupTheme && window.odysseusStartupTheme.saveTheme) {
            try { saved = Boolean(await window.odysseusStartupTheme.saveTheme(theme)); } catch (_) { saved = false; }
          }
          setThemeStatus(saved ? 'Applied' : 'Draft');
          setTimeout(() => setThemeStatus('Draft'), 1600);
        };
        const statusMark = (status, index) => {
          if (status === 'done') return 'OK';
          if (status === 'error') return '!';
          if (status === 'running') return '...';
          return String(index);
        };
        const elapsedText = (startedAt) => {
          const seconds = Math.max(0, Math.floor((Date.now() - Number(startedAt || Date.now())) / 1000));
          if (seconds < 60) return seconds + 's';
          const minutes = Math.floor(seconds / 60);
          const rest = seconds % 60;
          return minutes + 'm ' + String(rest).padStart(2, '0') + 's';
        };
        const render = (next) => {
          state = next || state || {};
          const steps = Array.isArray(state.steps) ? state.steps : [];
          const done = steps.filter((step) => step.status === 'done').length;
          const total = steps.length || 1;
          const runningIndex = steps.findIndex((step) => step.status === 'running');
          const basePercent = done / total;
          const runningBump = runningIndex >= 0 ? 0.5 / total : 0;
          const percent = Math.max(6, Math.min(100, Math.round((basePercent + runningBump) * 100)));
          const phaseStep = steps.find((step) => step.status === 'error')
            || (runningIndex >= 0 ? steps[runningIndex] : null)
            || (done >= steps.length && steps.length ? { label: 'Ready' } : null)
            || steps[Math.max(0, done - 1)]
            || { label: 'Preparing runtime' };

          document.getElementById('startup-current').textContent = state.currentAction || 'Preparing Odysseus.';
          document.getElementById('startup-phase').textContent = phaseStep.label || 'Preparing runtime';
          document.getElementById('startup-elapsed').textContent = elapsedText(state.startedAt);
          document.getElementById('startup-done').textContent = String(done);
          document.getElementById('startup-total').textContent = String(steps.length);
          document.getElementById('startup-percent').textContent = percent + '%';
          document.getElementById('startup-meter').style.setProperty('--ody-progress', percent + '%');
          document.getElementById('startup-meter-label').textContent = percent + '%';
          document.getElementById('startup-bar').style.width = percent + '%';
          document.getElementById('startup-backend').textContent = state.backendUrl || '';
          document.getElementById('startup-data').textContent = state.dataDir || '';
          document.getElementById('startup-log-path').textContent = state.logPath || '';

          document.getElementById('startup-steps').innerHTML = steps.map((step) => {
            const status = step.status || 'pending';
            return '<li class="step ' + esc(status) + '">' +
              '<span class="step-mark">' + esc(statusMark(status, step.index)) + '</span>' +
              '<div><strong>' + esc(step.label) + '</strong><small>' + esc(step.detail) + '</small></div>' +
            '</li>';
          }).join('');
          const activeStep = document.querySelector('.step.running, .step.error');
          if (activeStep) activeStep.scrollIntoView({ block: 'nearest' });

          const lines = Array.isArray(state.logLines) ? state.logLines.slice(-80) : [];
          document.getElementById('startup-log-count').textContent = lines.length ? lines.length + ' recent line' + (lines.length === 1 ? '' : 's') : 'Waiting for launcher output';
          const terminal = document.getElementById('startup-terminal');
          const nearBottom = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight < 28;
          const shouldFollow = followLog || nearBottom || Boolean(terminal.querySelector('.empty-log'));
          const previousScrollTop = terminal.scrollTop;
          terminal.innerHTML = lines.length
            ? lines.map((line) => '<div class="terminal-line"><span>&gt;</span><div>' + esc(line) + '</div></div>').join('')
            : '<div class="empty-log">Startup output will appear here as each step runs.</div>';
          terminal.scrollTop = shouldFollow ? terminal.scrollHeight : previousScrollTop;
        };
        const copyText = async (value) => {
          if (!value) return false;
          if (window.odysseusStartupClipboard && window.odysseusStartupClipboard.copyText) {
            try {
              return await window.odysseusStartupClipboard.copyText(value);
            } catch (_) {}
          }
          if (navigator.clipboard && navigator.clipboard.writeText) {
            try {
              await navigator.clipboard.writeText(value);
              return true;
            } catch (_) {}
          }
          const ta = document.createElement('textarea');
          ta.value = value;
          ta.setAttribute('readonly', '');
          ta.style.cssText = 'position:fixed;left:-9999px;top:-9999px';
          document.body.appendChild(ta);
          ta.select();
          let ok = false;
          try { ok = document.execCommand('copy'); } catch (_) {}
          ta.remove();
          return ok;
        };
        document.addEventListener('click', async (event) => {
          const button = event.target && event.target.closest ? event.target.closest('.copy-btn') : null;
          if (!button) return;
          const target = document.getElementById(button.dataset.copyTarget || '');
          const ok = await copyText(target ? target.textContent.trim() : '');
          button.classList.toggle('copied', ok);
          button.title = ok ? 'Copied' : 'Copy failed';
          setTimeout(() => {
            button.classList.remove('copied');
            if (button.dataset.copyTarget === 'startup-backend') button.title = 'Copy backend URL';
            if (button.dataset.copyTarget === 'startup-data') button.title = 'Copy data path';
            if (button.dataset.copyTarget === 'startup-log-path') button.title = 'Copy log path';
          }, 1200);
        });
        document.addEventListener('input', (event) => {
          const input = event.target && event.target.closest ? event.target.closest('[data-theme-color]') : null;
          if (!input || !isHex(input.value)) return;
          themeDraft = {
            name: 'loader-custom',
            colors: { ...themeDraft.colors, [input.dataset.themeColor]: input.value.toLowerCase() }
          };
          renderThemeStudio();
          setThemeStatus('Draft');
        });
        document.addEventListener('click', async (event) => {
          const preset = event.target && event.target.closest ? event.target.closest('[data-theme-preset]') : null;
          if (preset && THEME_PRESETS[preset.dataset.themePreset]) {
            themeDraft = {
              name: preset.dataset.themePreset,
              colors: { ...THEME_PRESETS[preset.dataset.themePreset] }
            };
            themeMode = isDarkTheme(themeDraft.colors) ? 'dark' : 'light';
            renderThemeStudio();
            setThemeStatus('Draft');
            return;
          }

          const modeButton = event.target && event.target.closest ? event.target.closest('[data-theme-mode]') : null;
          if (modeButton) {
            themeMode = modeButton.dataset.themeMode === 'light' ? 'light' : 'dark';
            renderThemeStudio();
            return;
          }

          const generateButton = event.target && event.target.closest ? event.target.closest('#startup-theme-generate') : null;
          if (generateButton) {
            const accent = document.getElementById('startup-harmony-accent');
            const type = document.getElementById('startup-harmony-type');
            const accentValue = accent && isHex(accent.value) ? accent.value.toLowerCase() : themeDraft.colors.red;
            themeDraft = {
              name: 'loader-custom',
              colors: generateHarmonyColors(accentValue, type ? type.value : 'complementary', themeMode)
            };
            renderThemeStudio();
            setThemeStatus('Generated');
            return;
          }

          const applyButton = event.target && event.target.closest ? event.target.closest('#startup-theme-apply') : null;
          if (applyButton) {
            await saveStartupTheme();
            return;
          }

          const resetButton = event.target && event.target.closest ? event.target.closest('#startup-theme-reset') : null;
          if (resetButton) {
            themeDraft = { name: DEFAULT_THEME_DRAFT.name, colors: { ...DEFAULT_THEME_DRAFT.colors } };
            themeMode = 'dark';
            renderThemeStudio();
            await saveStartupTheme();
          }
        });
        const harmonyAccent = document.getElementById('startup-harmony-accent');
        if (harmonyAccent) {
          harmonyAccent.addEventListener('input', () => {
            if (!isHex(harmonyAccent.value)) return;
            themeDraft = {
              name: 'loader-custom',
              colors: { ...themeDraft.colors, red: harmonyAccent.value.toLowerCase() }
            };
            renderThemeStudio();
            setThemeStatus('Draft');
          });
        }
        document.getElementById('startup-terminal').addEventListener('scroll', (event) => {
          const terminal = event.currentTarget;
          followLog = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight < 28;
        });
        window.__odysseusStartupUpdate = render;
        setInterval(() => render(state), 1000);
        loadStartupTheme();
        render(state);
      })();
    </script>
  `);
}

function failurePage(message, logPath) {
  return htmlPage('Odysseus Startup Failed', `
    <h1>Startup failed</h1>
    <p>${escapeHtml(message)}</p>
    <p class="hint">Startup log: <code>${escapeHtml(logPath)}</code></p>
    <button onclick="location.reload()">Try again</button>
  `);
}

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function createWindow(initialUrl) {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, 'startup-preload.js'),
      nodeIntegration: false,
      contextIsolation: true
    },
    title: 'Odysseus'
  });

  // Remove the default menu bar for a cleaner look
  mainWindow.setMenuBarVisibility(false);

  mainWindow.loadURL(initialUrl || `http://${BACKEND_HOST}:${BACKEND_PORT}`);
  mainWindow.webContents.on('did-finish-load', () => {
    sendStartupProgress();
    applyStartupThemeToBackendPage();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function loadBackend() {
  if (mainWindow) mainWindow.loadURL(`http://${BACKEND_HOST}:${BACKEND_PORT}`);
}

function showStartupFailure(message, logPath) {
  if (mainWindow) {
    mainWindow.loadURL(failurePage(message, logPath));
  }
}

function killProcess(pid) {
  if (process.platform === 'win32') {
    exec(`taskkill /pid ${pid} /t /f`, (err) => {
      if (err) {
        console.error(`Error killing process: ${err}`);
      }
    });
  } else {
    try {
      process.kill(-pid); // Kill process group on Unix
    } catch (e) {
      console.error(`Error killing process: ${e}`);
    }
  }
}

function waitForBackend(timeoutMs = 60000) {
  const started = Date.now();

  return new Promise((resolve, reject) => {
    const tryConnect = () => {
      const socket = net.createConnection({ host: BACKEND_HOST, port: BACKEND_PORT });
      let finished = false;

      const done = (err) => {
        if (finished) return;
        finished = true;
        socket.removeAllListeners();
        socket.destroy();
        if (!err) {
          resolve();
          return;
        }
        if (Date.now() - started >= timeoutMs) {
          reject(err);
          return;
        }
        setTimeout(tryConnect, 500);
      };

      socket.setTimeout(1000);
      socket.once('connect', () => done());
      socket.once('timeout', () => done(new Error(`Timed out connecting to ${BACKEND_HOST}:${BACKEND_PORT}`)));
      socket.once('error', done);
    };

    tryConnect();
  });
}

function appendBackendLog(logPath, message) {
  try {
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    fs.appendFileSync(logPath, message, 'utf8');
  } catch (err) {
    console.error('Failed to write backend startup log:', err);
  }
  updateStartupProgressFromOutput(message);
  sendStartupProgress();
}

app.on('ready', () => {
  console.log('Starting Odysseus backend...');
  const rootDir = runtimeRoot();
  const dataDir = persistentDataDir(rootDir);
  const logPath = path.join(app.getPath('userData'), 'backend-startup.log');
  startupProgress = createStartupProgress(rootDir, dataDir, logPath);
  appendBackendLog(logPath, `\n\n=== Odysseus startup ${new Date().toISOString()} ===\nRoot: ${rootDir}\n`);
  appendBackendLog(logPath, `Data: ${dataDir}\n`);
  migratePackagedData(rootDir, dataDir, logPath);
  finishStartupStep('data', 'Persistent data directory is ready.');
  createWindow(startupPage(startupProgress));

  const launcher = process.platform === 'win32'
    ? path.join(rootDir, 'launch-windows.ps1')
    : path.join(rootDir, 'launch-linux.sh');
  appendBackendLog(logPath, `Launcher: ${launcher}\n`);
  const backendEnv = {
    ...process.env,
    ODYSSEUS_DATA_DIR: dataDir,
    ODYSSEUS_DEFER_ADMIN_SETUP: '1',
    ODYSSEUS_SKIP_RUN_HINT: '1'
  };
  if (app.isPackaged) {
    backendEnv.DATABASE_URL = sqliteUrlForDataDir(dataDir);
  }

  // Spawn the Python backend
  if (process.platform === 'win32') {
    pythonProcess = spawn('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', launcher], {
      cwd: rootDir,
      env: backendEnv,
      detached: false,
      windowsHide: true
    });
  } else {
    pythonProcess = spawn('bash', [launcher], {
      cwd: rootDir,
      env: backendEnv,
      detached: true
    });
  }

  pythonProcess.stdout.on('data', (data) => {
    console.log(`Backend: ${data}`);
    appendBackendLog(logPath, String(data));
  });
  pythonProcess.stderr.on('data', (data) => {
    console.error(`Backend error: ${data}`);
    appendBackendLog(logPath, String(data));
  });
  pythonProcess.on('error', (err) => {
    appendBackendLog(logPath, `\nBackend launcher failed: ${err.stack || err.message || err}\n`);
    showStartupFailure(`Could not start the backend launcher: ${err.message || err}`, logPath);
  });
  pythonProcess.on('exit', (code, signal) => {
    appendBackendLog(logPath, `\nBackend launcher exited with code ${code}, signal ${signal}.\n`);
  });

  waitForBackend(BACKEND_STARTUP_TIMEOUT_MS).then(() => {
    console.log('Backend is ready, opening window...');
    appendBackendLog(logPath, 'Backend is ready.\n');
    loadBackend();
  }).catch((err) => {
    console.error('Error waiting for backend. Ensure port 7000 is available.', err);
    appendBackendLog(logPath, `\nTimed out waiting for backend: ${err.stack || err.message || err}\n`);
    showStartupFailure(`Odysseus did not become ready on port ${BACKEND_PORT} within ${Math.round(BACKEND_STARTUP_TIMEOUT_MS / 60000)} minutes.`, logPath);
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('will-quit', () => {
  if (pythonProcess && pythonProcess.pid) {
    console.log('Shutting down backend process...');
    killProcess(pythonProcess.pid);
  }
});
