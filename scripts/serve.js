#!/usr/bin/env node
/**
 * scripts/serve.js — server launcher.
 *
 * Called by `odysseus serve` or `npm run serve`.
 * Detects LAN IPs, Tailscale, spawns uvicorn from venv.
 */
'use strict';

const { spawn, spawnSync } = require('child_process');
const path = require('path');
const os = require('os');

const GREEN = '\x1b[32m', CYAN = '\x1b[36m', DIM = '\x1b[2m', RESET = '\x1b[0m';
const CHECK = GREEN + '✓' + RESET;

module.exports = function serve(ROOT) {
  // resolve Python from venv
  const venv = path.join(ROOT, '.venv');
  const python = os.platform() === 'win32'
    ? path.join(venv, 'Scripts', 'python.exe')
    : path.join(venv, 'bin', 'python');

  // detect LAN IPs
  const lanIps = [];
  const nets = os.networkInterfaces();
  for (const name of Object.keys(nets)) {
    for (const net of nets[name]) {
      if (net.family === 'IPv4' && !net.internal) {
        const ip = net.address;
        if (ip.startsWith('192.168.') || ip.startsWith('10.') || ip.startsWith('172.')) {
          lanIps.push(ip);
        }
      }
    }
  }

  console.log(`  ${CHECK} Detected ${lanIps.length} network interfaces\n`);
  console.log(`  Odysseus starting:\n`);
  console.log(`    Local:     http://localhost:7000`);
  for (const ip of lanIps.slice(0, 3)) {
    console.log(`    LAN:       http://${ip}:7000`);
  }

  // detect Tailscale
  try {
    const r = spawnSync('tailscale', ['ip', '-4'], { encoding: 'utf8', timeout: 3000 });
    const tailscale = r.stdout?.trim();
    if (tailscale) {
      console.log(`    Tailscale: http://${tailscale}:7000`);
    }
  } catch {
    // not installed, fine
  }

  console.log(`\n  ${DIM}Press Ctrl+C to stop.${RESET}\n`);

  // spawn uvicorn
  const child = spawn(python, [
    '-m', 'uvicorn', 'app:app',
    '--host', '0.0.0.0',
    '--port', '7000',
  ], {
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
