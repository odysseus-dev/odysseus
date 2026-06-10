#!/usr/bin/env node
/**
 * scripts/setup.js — Interactive Odysseus setup wizard.
 *
 * Called by `odysseus setup` or `npm start`.
 * Pure Node.js stdlib — zero npm dependencies.
 */
'use strict';

const readline = require('readline');
const { spawnSync, spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

// ── ANSI helpers ──────────────────────────────────────
const BOLD = '\x1b[1m', RESET = '\x1b[0m';
const GREEN = '\x1b[32m', CYAN = '\x1b[36m', YELLOW = '\x1b[33m';
const RED = '\x1b[31m', GRAY = '\x1b[90m', DIM = '\x1b[2m';

const CHECK = GREEN + '✓' + RESET;
const CROSS = RED + '✗' + RESET;
const WARN = YELLOW + '⚠' + RESET;
const ARROW = CYAN + '→' + RESET;

function clear() { process.stdout.write('\x1b[2J\x1b[H'); }

function banner() {
  clear();
  console.log(`
  ${BOLD}╔═══════════════════════════════════════╗${RESET}
  ${BOLD}║         Odysseus Setup Wizard          ║${RESET}
  ${BOLD}║     One command to run on any machine   ║${RESET}
  ${BOLD}╚═══════════════════════════════════════╝${RESET}
  `);
}

function status(label, ok) {
  console.log(`  ${ok ? CHECK : CROSS} ${label}`);
}

// ── spinner ───────────────────────────────────────────
function withSpinner(msg, fn) {
  const frames = ['⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏'];
  let i = 0;
  const interval = setInterval(() => {
    process.stdout.write(`\r  ${DIM}${frames[i]}${RESET} ${msg}...`);
    i = (i + 1) % frames.length;
  }, 80);

  try {
    const result = fn();
    clearInterval(interval);
    process.stdout.write(`\r  ${CHECK} ${msg}               \n`);
    return result;
  } catch (e) {
    clearInterval(interval);
    process.stdout.write(`\r  ${CROSS} ${msg}               \n`);
    throw e;
  }
}

// ── prompt ────────────────────────────────────────────
function question(q, defaultVal = '') {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });
  return new Promise(resolve => {
    const hint = defaultVal ? ` [${defaultVal}]` : '';
    rl.question(`  ${ARROW} ${q}${hint}: `, (ans) => {
      rl.close();
      resolve(ans.trim() || defaultVal);
    });
  });
}

async function menu(items) {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });
  return new Promise(resolve => {
    const ask = () => {
      rl.question(`  ${ARROW} Enter choice [1-${items.length}]: `, (ans) => {
        const n = parseInt(ans, 10);
        if (n >= 1 && n <= items.length) {
          rl.close();
          resolve(n);
        } else {
          console.log(`  ${WARN} Enter a number between 1 and ${items.length}`);
          ask();
        }
      });
    };
    ask();
  });
}

// ── prerequisite checks ───────────────────────────────
function checkPrerequisites(ROOT) {
  console.log(`  ${DIM}Checking prerequisites...${RESET}\n`);

  const checks = {};
  const run = (cmd, args) => {
    const r = spawnSync(cmd, args, { encoding: 'utf8', timeout: 10000 });
    if (r.error) throw r.error;
    return r.stdout?.trim() || r.stderr?.trim() || '';
  };

  const tryPython = (cmd, args) => {
    try { return run(cmd, args); } catch { return null; }
  };

  checks.python = tryPython('python', ['--version'])
    || tryPython('python3', ['--version'])
    || tryPython('py', ['--version']);

  if (checks.python) {
    console.log(`  ${CHECK} Python:    ${checks.python}`);
  } else {
    console.log(`  ${CROSS} Python:    not found`);
  }

  try {
    checks.node = run('node', ['--version']);
    console.log(`  ${CHECK} Node.js:   ${checks.node}`);
  } catch {
    checks.node = null;
    console.log(`  ${CROSS} Node.js:   not found`);
  }

  try {
    checks.git = run('git', ['--version']);
    console.log(`  ${CHECK} Git:       ${checks.git}`);
  } catch {
    checks.git = null;
    console.log(`  ${CROSS} Git:       not found`);
  }

  // Check if in repo root
  checks.inRepo = fs.existsSync(path.join(ROOT, 'app.py'));
  if (!checks.inRepo) {
    console.log(`  ${WARN} Not in Odysseus repo root (app.py not found)`);
  }

  console.log();
  return checks;
}

// ── subprocess wrappers ───────────────────────────────
function runPython(ROOT, cmd, args = []) {
  const py = 'python';
  const script = path.join(ROOT, 'scripts', 'odysseus.py');
  const result = spawnSync(py, [script, cmd, ...args], {
    encoding: 'utf8',
    cwd: ROOT,
    timeout: 120000,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(result.stderr?.trim() || `exit code ${result.status}`);
  return result.stdout;
}

function cmdSync(args, opts = {}) {
  const result = spawnSync(args[0], args.slice(1), {
    encoding: 'utf8',
    cwd: opts.cwd || process.cwd(),
    timeout: opts.timeout || 120000,
    stdio: opts.silent ? 'pipe' : 'inherit',
  });
  if (result.error) throw result.error;
  if (result.status !== 0 && opts.silent) {
    throw new Error(result.stderr?.trim() || `exit code ${result.status}`);
  }
  return result.stdout || '';
}

// ── setup steps ───────────────────────────────────────
function createVenv(ROOT) {
  const venvPath = path.join(ROOT, '.venv');
  if (fs.existsSync(venvPath)) {
    console.log(`  ${CHECK} Virtual environment exists (.venv/)`);
    return;
  }
  console.log(`  ${ARROW} Creating virtual environment...`);
  const py = getVenvPython(ROOT);
  cmdSync([py, '-m', 'venv', '.venv'], { cwd: ROOT, silent: true });
  console.log(`  ${CHECK} Virtual environment created (.venv/)`);
  return venvPath;
}

function getVenvPython(ROOT) {
  const venv = path.join(ROOT, '.venv');
  if (fs.existsSync(venv)) {
    return os.platform() === 'win32'
      ? path.join(venv, 'Scripts', 'python.exe')
      : path.join(venv, 'bin', 'python');
  }
  return 'python';
}

function installPythonDeps(ROOT) {
  return new Promise((resolve, reject) => {
    const py = getVenvPython(ROOT);
    console.log(`  ${ARROW} Installing Python dependencies...\n`);
    const proc = spawn(py, ['-m', 'pip', 'install', '-r', 'requirements.txt'], {
      cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'],
      timeout: 300000, // 5 minutes
    });
    proc.stdout.on('data', d => {
      for (const line of d.toString().split('\n').filter(Boolean)) {
        process.stdout.write(`    ${line}\n`);
      }
    });
    proc.stderr.on('data', d => {
      for (const line of d.toString().split('\n').filter(Boolean)) {
        process.stdout.write(`    ${line}\n`);
      }
    });
    proc.on('close', code => {
      if (code === 0) {
        console.log(`\n  ${CHECK} Python dependencies installed`);
        resolve();
      } else {
        reject(new Error(`pip install exited with code ${code}`));
      }
    });
    proc.on('error', reject);
  });
}

function installNpmDeps(ROOT) {
  return new Promise((resolve, reject) => {
    console.log(`  ${ARROW} Installing npm dependencies...\n`);
    // Run npm's CLI directly through node.exe (bypasses npm.cmd on Windows)
    const npmCli = path.join(path.dirname(process.execPath), 'node_modules', 'npm', 'bin', 'npm-cli.js');
    const cmd = fs.existsSync(npmCli) ? process.execPath : 'npm';
    const args = fs.existsSync(npmCli) ? [npmCli, 'install'] : ['install'];
    const proc = spawn(cmd, args, {
      cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'],
      timeout: 300000, // 5 minutes
    });
    proc.stdout.on('data', d => {
      for (const line of d.toString().split('\n').filter(Boolean)) {
        process.stdout.write(`    ${line}\n`);
      }
    });
    proc.stderr.on('data', d => {
      for (const line of d.toString().split('\n').filter(Boolean)) {
        process.stdout.write(`    ${line}\n`);
      }
    });
    proc.on('close', code => {
      if (code === 0) {
        console.log(`\n  ${CHECK} npm dependencies installed`);
        resolve();
      } else {
        reject(new Error(`npm install exited with code ${code}`));
      }
    });
    proc.on('error', reject);
  });
}

function createEnvFile(ROOT) {
  const envPath = path.join(ROOT, '.env');
  const example = path.join(ROOT, '.env.example');
  if (fs.existsSync(envPath)) {
    console.log(`  ${CHECK} .env already exists`);
    return;
  }
  if (fs.existsSync(example)) {
    fs.copyFileSync(example, envPath);
    console.log(`  ${CHECK} .env created from .env.example`);
    console.log(`  ${WARN} Edit .env to add your LLM host and API keys`);
  } else {
    console.log(`  ${WARN} .env.example not found — create .env manually`);
  }
}

function runSetupPython(ROOT) {
  const output = runPython(ROOT, 'setup');
  for (const line of output.split('\n').filter(l => l.trim())) {
    console.log(`  ${line}`);
  }
}

function detectNetwork(ROOT) {
  console.log();
  const output = runPython(ROOT, 'serve', ['--dry-run']);
  for (const line of output.split('\n').filter(l => l.trim())) {
    console.log(`  ${line}`);
  }
}

// ── main wizard ───────────────────────────────────────
async function startServer(ROOT, skipPrompt = false) {
  const ans = skipPrompt ? 'y' : await question('Start server now?', 'Y');
  if (ans.toLowerCase() === 'y' || ans === '') {
    console.log();
    const py = getVenvPython(ROOT);
    const serveScript = path.join(ROOT, 'scripts', 'odysseus.py');
    const child = spawn(py, [serveScript, 'serve'], {
      cwd: ROOT,
      stdio: 'inherit',
    });
    child.on('exit', (code) => {
      console.log(`\n  Server stopped (exit code ${code})`);
      process.exit(0);
    });
  } else {
    console.log(`\n  ${DIM}Run "odysseus serve" or "npm run serve" to start later.${RESET}`);
  }
}

async function quickSetup(ROOT) {
  console.log(`\n  ${BOLD}Quick Setup${RESET}\n`);

  try {
    createVenv(ROOT);
    await installPythonDeps(ROOT);
    await installNpmDeps(ROOT);
    createEnvFile(ROOT);
    runSetupPython(ROOT);
    detectNetwork(ROOT);
    await startServer(ROOT);
  } catch (e) {
    console.error(`\n  ${CROSS} Quick Setup failed: ${e.message}`);
    process.exit(1);
  }
}

async function guidedSetup(ROOT) {
  console.log(`\n  ${BOLD}Guided Setup${RESET}\n`);

  const port = await question('Port', '7000');
  const admin = await question('Admin username', 'admin');
  const passGen = (await question('Auto-generate admin password?', 'Y')).toLowerCase();

  // set env for setup script
  if (admin !== 'admin') process.env.ODYSSEUS_ADMIN_USER = admin;
  if (passGen === 'n') {
    const pw = await question('Enter admin password (min 8 chars)');
    if (pw.length >= 8) process.env.ODYSSEUS_ADMIN_PASSWORD = pw;
  }

  const llm = (await question('LLM backend (ollama/openai/skip)', 'skip')).toLowerCase();

  try {
    createVenv(ROOT);
    await installPythonDeps(ROOT);
    await installNpmDeps(ROOT);
    createEnvFile(ROOT);
    runSetupPython(ROOT);

    if (llm === 'ollama') {
      console.log(`  ${DIM}  Set OLLAMA_HOST in .env to your Ollama server URL${RESET}`);
    } else if (llm === 'openai') {
      console.log(`  ${DIM}  Set OPENAI_API_KEY in .env${RESET}`);
    }

    detectNetwork(ROOT);
    await startServer(ROOT);
  } catch (e) {
    console.error(`\n  ${CROSS} Setup failed: ${e.message}`);
    process.exit(1);
  }
}

// ── module export ────────────────────────────────────
module.exports = async function setup(ROOT) {
  banner();

  const checks = checkPrerequisites(ROOT);
  if (!checks.python && !checks.node) {
    console.log(`  ${CROSS} Python and Node.js are required. Install them first.\n`);
    process.exit(1);
  }
  if (!checks.inRepo) {
    console.log(`  ${WARN} Run this from the Odysseus repo root.\n`);
    const ans = await question('Continue anyway?', 'N');
    if (ans.toLowerCase() !== 'y') process.exit(0);
  }

  console.log(`  ${BOLD}What would you like to do?${RESET}\n`);
  console.log(`    1) Quick Setup — automated, sensible defaults`);
  console.log(`    2) Start Server — skip setup, already configured`);
  console.log(`    3) Exit\n`);

  const choice = await menu([1, 2, 3]);

  switch (choice) {
    case 1:
      await quickSetup(ROOT);
      break;
    case 2:
      await startServer(ROOT, true);
      break;
    case 3:
      console.log(`\n  ${DIM}Goodbye.${RESET}\n`);
      process.exit(0);
  }
};
