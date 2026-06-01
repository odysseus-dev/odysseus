#!/usr/bin/env node
// Lightweight scanner for likely user-facing strings that are missing from
// static/js/i18n.js. It intentionally reports candidates, not hard failures:
// model names, URLs, CSS, and developer-only strings still need human review.

import fs from 'node:fs';
import path from 'node:path';

const ROOT = process.cwd();
const STATIC_DIR = path.join(ROOT, 'static');
const I18N_FILE = path.join(STATIC_DIR, 'js', 'i18n.js');
const LANG = process.argv.find(arg => arg.startsWith('--lang='))?.split('=')[1] || 'ko';

const ALLOW_SHORT = new Set([
  'Account', 'Actions', 'Add', 'Admin', 'Agent', 'All', 'Apply', 'Archive',
  'Calendar', 'Cancel', 'Chat', 'Clear', 'Close', 'Compare', 'Confirm',
  'Create', 'Delete', 'Dependencies', 'Download', 'Edit', 'Email', 'Export',
  'Gallery', 'Import', 'Install', 'Library', 'Local', 'Memory', 'Missing',
  'Model', 'Models', 'New', 'None', 'Notes', 'Open', 'Prompt', 'Reset',
  'Research', 'Save', 'Search', 'Select', 'Serve', 'Settings', 'Skills',
  'Start', 'System', 'Tasks', 'Tools', 'User', 'Users',
]);

function read(file) {
  return fs.readFileSync(file, 'utf8');
}

function walk(dir, files = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name.startsWith('.')) continue;
    if (entry.isDirectory()) {
      if (['fonts', 'img', 'vendor', 'lib'].includes(entry.name)) continue;
      walk(path.join(dir, entry.name), files);
    } else if (/\.(html|js)$/.test(entry.name) && entry.name !== 'i18n.js') {
      files.push(path.join(dir, entry.name));
    }
  }
  return files;
}

function readLanguageKeys(lang) {
  const source = read(I18N_FILE);
  const langStart = source.indexOf(`const ${lang.toUpperCase()} = {`);
  if (langStart < 0) throw new Error(`Could not find const ${lang.toUpperCase()} in ${I18N_FILE}`);
  const rest = source.slice(langStart);
  const end = rest.indexOf('\n};\n\nconst STRINGS');
  if (end < 0) throw new Error(`Could not find end of ${lang.toUpperCase()} dictionary`);
  const body = rest.slice(0, end);
  const keys = new Set();
  const keyRe = /^\s*(['"])((?:\\.|(?!\1).)*)\1\s*:/gm;
  let match;
  while ((match = keyRe.exec(body))) {
    keys.add(unescapeJs(match[2]));
  }
  return keys;
}

function unescapeJs(value) {
  return String(value)
    .replace(/\\n/g, '\n')
    .replace(/\\'/g, "'")
    .replace(/\\"/g, '"')
    .replace(/\\\\/g, '\\');
}

function cleanCandidate(value) {
  const s = String(value)
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/\s+/g, ' ')
    .trim();
  if (!s || s.length < 2 || s.length > 180) return null;
  if (!/[A-Za-z]/.test(s)) return null;
  if (!/^[A-Za-z0-9(]/.test(s)) return null;
  if (/[{}<>\\]|https?:|^#[0-9a-fA-F]{3,}/.test(s)) return null;
  if (/[;=]|(?:\bconst\b|\blet\b|\bfunction\b|\breturn\b|\bdocument\.|\bquerySelector\b)/.test(s)) return null;
  if (/(?:\(\?:|\(\?!|\[\^|\[[\w\s|\\.-]+\]|\bvar\(|color-mix\(|\bsrgb\b|\btransparent\b)/.test(s)) return null;
  if (/(?:\b\d+(?:px|rem|em|vh|vw)\b|rgba?\(|rotate\(|scale\(|translate[XY]?\(|cubic-bezier\(|opacity\b|monospace|safe-area-inset|box-shadow|border-radius)/.test(s)) return null;
  if (/(?:^|[\s,.])(?:btn|card|chip|dropdown|hidden|iframe|input|loading|menu|panel|preview|row|select|timer|title)-/.test(s)) return null;
  if (/(?:^|[\s,.])(?:admin|cal|cmp|cookbook|doclib|email|hwfit|memory|modal|msg|skill|settings|tour)-[\w-]+/.test(s)) return null;
  if (/^M\d+(?:\s|[A-Za-z0-9.,-])+$/.test(s)) return null;
  if (/(?:^|[\s,])(?:button|input|select|textarea|label|a\[href\]|a)(?:$|[\s,])/.test(s)) return null;
  if (/['"`]\s*\+|\+\s*['"`]|\$\{/.test(s)) return null;
  if (/^\(?[a-z]+:[^ ]/i.test(s) && !/^(From|To|Date|Subject|Active|Default server|Cc):/.test(s)) return null;
  if (/^[a-z_]+:$/.test(s)) return null;
  if (/^[-\w./]+$/.test(s) && !ALLOW_SHORT.has(s)) return null;
  if (/^[A-Z0-9_ -]{1,8}$/.test(s) && !ALLOW_SHORT.has(s)) return null;
  if (/^(data-|aria-|--|#[\w-]+|\.[\w-]+)/.test(s)) return null;
  return s;
}

function scanHtml(text) {
  const out = [];
  for (const m of text.matchAll(/>([^<]+)</g)) out.push(m[1]);
  for (const m of text.matchAll(/\b(?:title|placeholder|aria-label)=["']([^"']+)["']/g)) out.push(m[1]);
  return out;
}

function scanJsStrings(text) {
  const out = [];
  let quote = null;
  let buf = '';
  let escaped = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (!quote) {
      if (ch === '"' || ch === "'" || ch === '`') {
        quote = ch;
        buf = '';
        escaped = false;
      }
      continue;
    }
    if (escaped) {
      buf += `\\${ch}`;
      escaped = false;
      continue;
    }
    if (ch === '\\') {
      escaped = true;
      continue;
    }
    if (ch === quote) {
      const value = unescapeJs(buf);
      if (quote === '`') out.push(...scanHtml(value));
      else out.push(value);
      quote = null;
      buf = '';
      continue;
    }
    buf += ch;
  }
  return out;
}

function stripJsComments(text) {
  let out = '';
  let quote = null;
  let escaped = false;
  let lineComment = false;
  let blockComment = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    const next = text[i + 1];
    if (lineComment) {
      if (ch === '\n') {
        lineComment = false;
        out += ch;
      } else {
        out += ' ';
      }
      continue;
    }
    if (blockComment) {
      if (ch === '*' && next === '/') {
        blockComment = false;
        out += '  ';
        i += 1;
      } else {
        out += ch === '\n' ? '\n' : ' ';
      }
      continue;
    }
    if (!quote && ch === '/' && next === '/') {
      lineComment = true;
      out += '  ';
      i += 1;
      continue;
    }
    if (!quote && ch === '/' && next === '*') {
      blockComment = true;
      out += '  ';
      i += 1;
      continue;
    }
    if (quote) {
      out += ch;
      if (escaped) {
        escaped = false;
      } else if (ch === '\\') {
        escaped = true;
      } else if (ch === quote) {
        quote = null;
      }
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') quote = ch;
    out += ch;
  }
  return out;
}

const keys = readLanguageKeys(LANG);
const missing = new Map();

for (const file of walk(STATIC_DIR)) {
  const text = read(file);
  const rel = path.relative(ROOT, file).replaceAll('\\', '/');
  const values = rel.endsWith('.html') ? scanHtml(text) : scanJsStrings(stripJsComments(text));
  for (const raw of values) {
    const s = cleanCandidate(raw);
    if (!s || keys.has(s)) continue;
    if (!missing.has(s)) missing.set(s, new Set());
    missing.get(s).add(rel);
  }
}

const rows = [...missing.entries()]
  .map(([text, files]) => ({ text, files: [...files].slice(0, 4) }))
  .sort((a, b) => a.text.localeCompare(b.text));

console.log(`Missing ${LANG} candidate strings: ${rows.length}`);
for (const row of rows) {
  console.log(`${JSON.stringify(row.text)} :: ${row.files.join(', ')}`);
}
