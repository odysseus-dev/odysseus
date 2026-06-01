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

const CHECK = '\x1b[32m\u2713\x1b[0m';
const CROSS = '\x1b[31m\u2717\x1b[0m';

module.exports = function status(ROOT) {
  const port = 7000;
  let running = false;

  const req = http.get(`http://127.0.0.1:${port}/`, () => {
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
  try {
    const r = spawnSync('python', ['--version'], { encoding: 'utf8', timeout: 10000 });
    if (r.stdout?.trim() || r.stderr?.trim()) {
      console.log(`  ${CHECK} Python:     ${r.stdout?.trim() || r.stderr?.trim()}`);
    } else throw new Error();
  } catch {
    try {
      const r = spawnSync('py', ['--version'], { encoding: 'utf8', timeout: 5000 });
      if (r.stdout?.trim() || r.stderr?.trim()) {
        console.log(`  ${CHECK} Python:     ${r.stdout?.trim() || r.stderr?.trim()}`);
      } else throw new Error();
    } catch {
      console.log(`  ${CROSS} Python:     not found`);
    }
  }

  console.log(`  ${CHECK} Data dir:   ${path.join(ROOT, 'data')}`);
  console.log();
}
