import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const src = await readFile(new URL('../static/js/sessions.js', import.meta.url), 'utf8');
const START = '// === copy-chat header (debug trace block) ===';
const END = '// === end copy-chat header ===';
const block = src.slice(src.indexOf(START) + START.length, src.indexOf(END));
const extract = new Function(block + '\nreturn { _buildCopyChatHeader, _toolSurfaceLabel, _formatJst };');
const { _buildCopyChatHeader, _toolSurfaceLabel, _formatJst } = extract();

test('copy-chat header has exact shape and values', () => {
  const header = _buildCopyChatHeader({
    version: '1.0.3',
    build: '20261006.1',
    sourceCommit: 'a1b2c3d4e5f6',
    model: 'gpt-4o',
    endpointUrl: 'https://api.example.com/v1',
    toolSurface: 'Ajax Spec (router)',
    sessionId: 'sess-123',
    copiedAt: new Date(Date.UTC(2026, 9, 13, 5, 30)),
  });
  const expected = [
    '--- Odysseus info ---',
    'Odysseus: 1.0.3 | Harness build: 20261006.1 | Commit: a1b2c3d4',
    'Model: gpt-4o @ https://api.example.com/v1 | Tool surface: Ajax Spec (router)',
    'Copied: 2026-10-13 14:30 JST | Session: sess-123',
    '----------------------',
  ].join('\n');
  assert.equal(header, expected);
});

test('unknown fields are never omitted', () => {
  const header = _buildCopyChatHeader({});
  const expected = [
    '--- Odysseus info ---',
    'Odysseus: unknown | Harness build: unknown | Commit: unknown',
    'Model: unknown @ unknown | Tool surface: unknown',
    'Copied: unknown JST | Session: unknown',
    '----------------------',
  ].join('\n');
  assert.equal(header, expected);
});

test('closing separator is exactly 22 dashes', () => {
  const header = _buildCopyChatHeader({});
  const lines = header.split('\n');
  assert.equal(lines.length, 5);
  assert.equal(lines[lines.length - 1], '----------------------');
  assert.equal(lines[lines.length - 1].length, 22);
});

test('tool surface labels map modes to human labels', () => {
  assert.equal(_toolSurfaceLabel('full'), 'Regular tools');
  assert.equal(_toolSurfaceLabel('compact'), 'Ajax Compact');
  assert.equal(_toolSurfaceLabel('spec'), 'Ajax Spec (router)');
  assert.equal(_toolSurfaceLabel('none'), 'Tools off');
  assert.equal(_toolSurfaceLabel(''), 'Auto');
  assert.equal(_toolSurfaceLabel(undefined), 'Auto');
  assert.equal(_toolSurfaceLabel(null), 'Auto');
  assert.equal(_toolSurfaceLabel('weird'), 'unknown');
});

test('JST timestamp is UTC+9 with HH:MM padding', () => {
  assert.equal(_formatJst(new Date(Date.UTC(2026, 0, 1, 0, 5))), '2026-01-01 09:05');
  assert.equal(_formatJst(new Date(Date.UTC(2026, 11, 31, 15, 0))), '2027-01-01 00:00');
});
