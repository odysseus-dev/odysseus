#!/usr/bin/env node
/**
 * Odysseus CLI — entry point
 *
 * Installed globally via `npm install -g .` or `npm publish`.
 * Dispatches to subcommand scripts under scripts/.
 */
'use strict';

const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const pkg = require(path.join(ROOT, 'package.json'));

const HELP = `
╔═══════════════════════════════════════╗
║             Odysseus CLI               ║
║     Self-hosted AI workspace           ║
╚═══════════════════════════════════════╝

Usage:
  odysseus init <dir>     Scaffold a new instance in <dir>
  odysseus setup          Interactive setup wizard
  odysseus serve          Start the server
  odysseus status         Health check
  odysseus --version      Show version
  odysseus --help         Show this help
`;

const cmd = process.argv[2];

if (!cmd || cmd === '--help' || cmd === '-h') {
  process.stdout.write(HELP);
  process.exit(cmd ? 0 : 0);
}

if (cmd === '--version' || cmd === '-v') {
  process.stdout.write(pkg.version + '\n');
  process.exit(0);
}

const dispatch = {
  'init':   () => require(path.join(ROOT, 'scripts', 'init'))(ROOT, process.argv.slice(3)),
  'setup':  () => require(path.join(ROOT, 'scripts', 'setup'))(ROOT),
  'serve':  () => require(path.join(ROOT, 'scripts', 'serve'))(ROOT),
  'status': () => require(path.join(ROOT, 'scripts', 'status'))(ROOT),
};

if (!dispatch[cmd]) {
  process.stderr.write(`error: unknown command "${cmd}"\n`);
  process.stderr.write(`Run "odysseus --help" for available commands.\n`);
  process.exit(1);
}

const result = dispatch[cmd]();
if (result && typeof result.then === 'function') {
  result.catch(err => {
    process.stderr.write(`error: ${err.message}\n`);
    process.exit(1);
  });
}
