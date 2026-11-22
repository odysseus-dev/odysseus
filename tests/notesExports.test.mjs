import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { parse } from 'acorn';

// Every export of a Notes module is used by another module or a test. An
// export nothing imports is dead surface the layer checker still has to
// reason about; drop the `export` (or the code).
const root = new URL('../', import.meta.url).pathname;
const walk = dir => fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
  const full = path.join(dir, entry.name);
  return entry.isDirectory() ? walk(full) : /\.m?js$/.test(entry.name) ? [full] : [];
});
const notes = walk(path.join(root, 'frontend/src/features/notes'));
// The static mirror of the Notes modules is a byte copy; everything else can use an export.
const users = [...walk(path.join(root, 'frontend/src')), ...walk(path.join(root, 'tests')), ...walk(path.join(root, 'static/js')).filter(f => !f.includes('/static/js/features/notes/') && !f.includes('/static/js/lib/'))];
const sources = new Map(users.map(file => [file, fs.readFileSync(file, 'utf8')]));
// Unused on `dev` before the markdown/note-window rework; not this test's to remove.
const PREDATES = new Set(['REMINDER_GLOWED_KEY', 'REMINDER_PENDING_HIGHLIGHT_KEY', 'REMINDER_ACTIVE_HIGHLIGHT_KEY', '_DRAFT_PREFIX'].map(name => `frontend/src/features/notes/infrastructure/storage.js: ${name}`));

function exportedNames(source) {
  const names = [];
  for (const node of parse(source, { ecmaVersion: 'latest', sourceType: 'module' }).body) {
    if (node.type !== 'ExportNamedDeclaration') continue;
    if (node.declaration?.id) names.push(node.declaration.id.name);
    for (const decl of node.declaration?.declarations || []) if (decl.id.type === 'Identifier') names.push(decl.id.name);
    for (const spec of node.specifiers || []) names.push(spec.exported.name);
  }
  return names;
}

test('every Notes export is used outside its own module', () => {
  const unused = [];
  for (const file of notes) {
    for (const name of exportedNames(fs.readFileSync(file, 'utf8'))) {
      const word = new RegExp(`\\b${name}\\b`);
      const used = [...sources].some(([other, text]) => other !== file && word.test(text));
      const label = `${path.relative(root, file)}: ${name}`;
      if (!used && !PREDATES.has(label)) unused.push(label);
    }
  }
  assert.deepEqual(unused, []);
});
