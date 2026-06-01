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
  const port = 7000;
  let running = false;

  const req = http.get(`http://127.0.0.1:${port}/`, () => {
    printStatus(port, true);
  });

  req.on('error', () => {
    printStatus(port, false);
  });

  req.setTimeout(3000, () => {
    req.destroy();
    printStatus(port, false);
  });
};

function printStatus(port, running) {
  console.log(`\n  Odysseus Status\n`);
  console.log(`  ${running ? CHECK : CROSS} Server:     ${running ? `running on port ${port}` : 'not running'}`);

  // DB check
  const db = path.join(process.cwd(), 'data', 'app.db');
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

  console.log(`  ${CHECK} Data dir:   ${path.join(process.cwd(), 'data')}`);
  console.log();
}
