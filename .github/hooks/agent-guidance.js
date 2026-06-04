#!/usr/bin/env node

const fs = require('fs');

const payloadPath = process.argv[2];
if (!payloadPath) {
  console.error('Usage: node agent-guidance.js <payload.json>');
  process.exit(1);
}

let payload;
try {
  payload = JSON.parse(fs.readFileSync(payloadPath, 'utf8'));
} catch (err) {
  console.error('Unable to read payload:', err.message);
  process.exit(1);
}

const { command, tool, message, paths } = payload;
const warnings = [];

if (!message || message.trim().length === 0) {
  warnings.push('Missing preamble message to explain the upcoming tool call.');
}

if (paths && paths.some(p => !p.startsWith('/workspaces/odysseus/'))) {
  warnings.push('File edit path must use absolute workspace-rooted path under /workspaces/odysseus/.');
}

if (command && /npm|apt|rm|sudo/.test(command) && !message.toLowerCase().includes('review') && !message.toLowerCase().includes('confirm')) {
  warnings.push('Potentially dangerous command detected. Ensure the user explicitly confirmed the intent before execution.');
}

if (warnings.length > 0) {
  console.log('AGENT GUIDANCE WARNING:');
  warnings.forEach(w => console.log('- ' + w));
  process.exit(2);
}

console.log('Agent guidance checks passed.');
process.exit(0);
