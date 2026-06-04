#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname);
const jsonPath = path.join(root, 'agent-guidance-hook.json');
const scriptPath = path.join(root, 'agent-guidance.js');

const errors = [];

if (!fs.existsSync(jsonPath)) {
  errors.push('Missing agent-guidance-hook.json');
} else {
  try {
    const content = fs.readFileSync(jsonPath, 'utf8');
    JSON.parse(content);
  } catch (err) {
    errors.push(`Invalid JSON in agent-guidance-hook.json: ${err.message}`);
  }
}

if (!fs.existsSync(scriptPath)) {
  errors.push('Missing agent-guidance.js');
} else {
  const stat = fs.statSync(scriptPath);
  if (!(stat.mode & 0o100)) {
    errors.push('agent-guidance.js is not executable');
  }
}

if (errors.length > 0) {
  console.error('Agent guidance hook validation failed:');
  errors.forEach(e => console.error('- ' + e));
  process.exit(1);
}

console.log('Agent guidance hook validation passed.');
process.exit(0);
