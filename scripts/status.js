#!/usr/bin/env node
/**
 * scripts/status.js — health check.
 *
 * Called by `odysseus status`.
 */
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const CHECK = '\x1b[32m✓\x1b[0m';
const CROSS = '\x1b[31m✗\x1b[0m';

module.exports = function status(ROOT) {
  // Read port from .env if available, fall back to 7000
  let port = 7000;
  try {
    const envPath = path.join(ROOT, '.env');
    if (fs.existsSync(envPath)) {
      const env = fs.readFileSync(envPath, 'utf8');
      const match = env.match(/^ODYSSEUS_PORT=(\d+)/m);
      if (match) port = parseInt(match[1], 10);
    }
  } catch {}

  const req = http.get(`http://127.0.0.1:${port}/api/health`, (res) => {
    printStatus(ROOT, port, true);
  });

  req.on('error', () => {
    printStatus(ROOT, port, false);
  });

  req.setTimeout(3000, () => {
    req.destroy();
    printStatus(ROOT, port, false);
  });
};

function printStatus(ROOT, port, running) {
  console.log(`\n  Odysseus Status\n`);
  console.log(`  ${running ? CHECK : CROSS} Server:     ${running ? `running on port ${port}` : 'not running'}`);

  // DB check (relative to package root, not cwd)
  const db = path.join(ROOT, 'data', 'app.db');
  if (fs.existsSync(db)) {
    const size = fs.statSync(db).size;
    console.log(`  ${CHECK} Database:   present (${(size / 1024).toFixed(0)} KB)`);
  } else {
    console.log(`  ${CROSS} Database:   not found`);
  }

  // Python check
  const tryPython = (cmd, args) => {
    try {
      const r = spawnSync(cmd, args, { encoding: 'utf8', timeout: 10000 });
      if (r.stdout?.trim() || r.stderr?.trim()) {
        return r.stdout?.trim() || r.stderr?.trim();
      }
    } catch {}
    return null;
  };

  const pyVer = tryPython('python', ['--version'])
    || tryPython('python3', ['--version'])
    || tryPython('py', ['--version']);

  if (pyVer) {
    console.log(`  ${CHECK} Python:     ${pyVer}`);
  } else {
    console.log(`  ${CROSS} Python:     not found`);
  }

  console.log(`  ${CHECK} Data dir:   ${path.join(ROOT, 'data')}`);
  console.log();
}
