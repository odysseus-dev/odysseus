#!/usr/bin/env node
/**
 * Odysseus CLI — entry point
 *
 * Installed globally via `npm install -g .` or `npm publish`.
 * Dispatches to subcommand scripts under scripts/.
 */
'use strict';

const path = require('path');
const fs = require('fs');

const ROOT = path.resolve(__dirname, '..');

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
  odysseus --help         Show this help
`;

const cmd = process.argv[2];

if (!cmd || cmd === '--help' || cmd === '-h') {
  process.stdout.write(HELP);
  process.exit(cmd ? 0 : 0);
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

dispatch[cmd]();
