#!/usr/bin/env node
/**
 * `odysseus init <dir>` — scaffold a new Odysseus instance.
 *
 * Copies the package (minus node_modules, .git, data/) into <dir>,
 * then prints next steps.
 */
'use strict';

const fs = require('fs');
const path = require('path');

module.exports = function init(ROOT, args) {
  const target = args[0];

  if (!target) {
    process.stderr.write('error: missing directory name\n');
    process.stderr.write('Usage: odysseus init <dir>\n');
    process.exit(1);
  }

  const dest = path.resolve(process.cwd(), target);

  if (fs.existsSync(dest) && fs.readdirSync(dest).length > 0) {
    process.stderr.write(`error: "${target}" already exists and is not empty\n`);
    process.exit(1);
  }

  const skip = new Set(['node_modules', '.git', 'data', 'logs', '__pycache__',
    '.venv', 'venv', '.env', 'package-lock.json']);

  function copy(src, dst) {
    fs.mkdirSync(dst, { recursive: true });

    // Prevent infinite recursion if target is inside source
    const srcEntries = new Set(fs.readdirSync(src));
    for (const entry of srcEntries) {
      if (skip.has(entry) || entry === path.basename(dest)) continue;
      const s = path.join(src, entry);
      const d = path.join(dst, entry);
      const stat = fs.statSync(s);
      if (stat.isDirectory()) {
        copy(s, d);
      } else {
        // Skip binary/compiled, only copy text files — but for simplicity copy everything
        fs.copyFileSync(s, d);
      }
    }
  }

  process.stdout.write(`Scaffolding Odysseus in "${target}"...\n`);
  copy(ROOT, dest);
  process.stdout.write(`  ✓ Created ${dest}\n`);
  process.stdout.write(`\nNext steps:\n`);
  process.stdout.write(`  cd ${target}\n`);
  process.stdout.write(`  odysseus setup\n`);
  process.stdout.write(`  odysseus serve\n`);
};
