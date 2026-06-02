#!/usr/bin/env node
/**
 * scripts/serve.js — server launcher.
 *
 * Called by `odysseus serve` or `npm run serve`.
 * Shells out to odysseus.py serve (single source of truth for detection + startup).
 */
'use strict';

const { spawn } = require('child_process');
const path = require('path');
const os = require('os');

module.exports = function serve(ROOT) {
  const py = path.join(ROOT, 'scripts', 'odysseus.py');

  // Detect venv python
  const venv = path.join(ROOT, '.venv');
  const venvPy = os.platform() === 'win32'
    ? path.join(venv, 'Scripts', 'python.exe')
    : path.join(venv, 'bin', 'python');
  const fs = require('fs');
  const python = fs.existsSync(venvPy) ? venvPy : 'python3';

  const child = spawn(python, [py, 'serve'], {
    cwd: ROOT,
    stdio: 'inherit',
  });

  child.on('exit', (code) => {
    process.exit(code || 0);
  });

  process.on('SIGINT', () => {
    child.kill('SIGINT');
  });
};
