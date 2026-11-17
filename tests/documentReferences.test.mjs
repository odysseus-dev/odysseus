import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { analyzeDocumentReferences, checkDocumentReferences } from '../scripts/check_document_references.mjs';
import { validateDocumentImports } from '../scripts/check_document_references.mjs';

const errors = source => analyzeDocumentReferences(source).diagnostics;

test('resolves declarations, destructuring, parameters and browser globals', () => {
  const result = errors(`const input = {};\nconst { value: local = window.location } = input;\nfunction run({ item }) { return item + local + document.title; }\nconst later = 1;`);
  assert.deepEqual(result, []);
});

test('reports unresolved reads and does not treat object keys as references', () => {
  const result = errors(`const obj = { key: missing };\nobj.key;\ntypeof maybeMissing;`);
  assert.deepEqual(result.map(x => [x.line, x.name]), [[1, 'missing']]);
});

test('reports imported writes while respecting nested shadowing', () => {
  const result = analyzeDocumentReferences(`import { value } from './x.js';\nvalue = 2;\nfunction f(value) { value++; }\n[value] = values;`);
  assert.deepEqual(result.importedWrites.map(x => [x.name, x.line]), [['value', 2], ['value', 4]]);
  assert.equal(result.diagnostics[0].name, 'values');
});

test('recognizes compound, update, destructuring and loop writes', () => {
  const result = analyzeDocumentReferences(`import { item } from './x.js';\nitem += 1;\n++item;\n({x: item} = source);\nfor (item of items) {}`);
  assert.deepEqual(result.importedWrites.map(x => x.line), [2, 3, 4, 5]);
});

test('handles export aliases, shorthand keys, computed pattern keys and typeof imports', () => {
  const result = analyzeDocumentReferences(`import { item } from './x.js';\nconst { [key]: local = fallback } = source;\nconst shorthand = { item };\nexport { item as renamed };\ntypeof item;`);
  assert.deepEqual(result.importedWrites, []);
  assert.equal(result.imports[0].uses, 2);
  assert.deepEqual(result.diagnostics.map(x => x.name), ['key', 'fallback', 'source']);
});

test('loop bindings are scoped to the loop and imported iteration targets are writes', () => {
  const result = analyzeDocumentReferences(`import { item } from './x.js';\nfor (let local of values) { local; }\nlocal;\nfor (item of values) {}`);
  assert.deepEqual(result.importedWrites.map(x => x.line), [4]);
  assert.deepEqual(result.diagnostics.map(x => x.name), ['values', 'local', 'values']);
});

test('unused imports are reported by binding use, respecting shadowing', () => {
  const result = analyzeDocumentReferences(`import { used, unused } from './x.js';\nfunction f(used) { return used; }\nused();`);
  assert.deepEqual(result.unusedImports.map(x => x.local), ['unused']);
});

test('namespace export aliases are export names, not lexical reads', () => {
  const result = analyzeDocumentReferences(`export * as namespace from './dependency.js';\nexport * from './other.js';`);
  assert.deepEqual(result.exports, ['namespace']);
  assert.deepEqual(result.diagnostics, []);
});

test('validates relative maintained exports and published static targets', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'document-refs-'));
  try {
    const docs = path.join(root, 'frontend/src/features/documents/ui');
    fs.mkdirSync(docs, { recursive: true });
    fs.mkdirSync(path.join(root, 'static/js'), { recursive: true });
    fs.writeFileSync(path.join(docs, 'entry.js'), `import { value } from './dependency.js';\nimport { absent } from '/static/js/published.js';\nexport { missing } from './dependency.js';`);
    fs.writeFileSync(path.join(docs, 'dependency.js'), `export const value = 1;`);
    fs.writeFileSync(path.join(root, 'static/js/published.js'), `export const present = 1;`);
    fs.writeFileSync(path.join(root, 'static', 'js', 'document.js'), `export {};`);
    assert.deepEqual(validateDocumentImports(root).map(e => [e.type, e.name]), [['missing-export', 'absent'], ['missing-reexport', 'missing']]);
    fs.writeFileSync(path.join(docs, 'entry.js'), `import { value } from './dependency.js';\nimport * as namespace from '/static/js/published.js';`);
    assert.deepEqual(validateDocumentImports(root), []);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('compatibility exceptions require a per-file rationale and typeof probe', () => {
  const result = analyzeDocumentReferences(`if (typeof legacyHook === 'function') legacyHook();\n// document-reference-compat: legacyHook - retained for old host pages`);
  assert.deepEqual(result.diagnostics, []);
  assert.deepEqual(result.compatibilityAllowances.map(x => x.name), ['legacyHook']);
  assert.deepEqual(analyzeDocumentReferences(`// document-reference-compat: ordinary - rationale\nordinary();`).diagnostics.map(x => x.name), ['ordinary']);
});

test('maintained document graph has no unresolved names or imported writes', () => {
  assert.deepEqual(checkDocumentReferences(path.resolve('')), []);
  assert.deepEqual(validateDocumentImports(path.resolve('')), []);
});

test('namespace properties are read-only while imported objects retain mutable properties', () => {
  const result = analyzeDocumentReferences("import * as ns from './owner.js'; import { object } from './owner.js'; ns.value = 2; object.value = 2; ns.object.value = 3;");
  assert.deepEqual(result.importedWrites.map(write => write.name), ['ns']);
});

test('compatibility exceptions apply only to typeof-guarded sites, not unguarded reads in the same file', () => {
  const result = analyzeDocumentReferences(
    `if (typeof legacyHook === 'function') legacyHook();\n` +
    `legacyHook();\n` +
    `// document-reference-compat: legacyHook - retained for old host pages`
  );
  assert.deepEqual(result.compatibilityAllowances.map(x => x.name), ['legacyHook']);
  assert.deepEqual(result.diagnostics.map(x => [x.line, x.name, x.type]), [[2, 'legacyHook', 'unresolved']]);
});

test('checkDocumentReferences fails closed on unresolved reads, unguarded compat reads, imported writes, and unused imports', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'document-refs-neg-'));
  try {
    const docs = path.join(root, 'frontend/src/features/documents/ui');
    fs.mkdirSync(docs, { recursive: true });
    fs.mkdirSync(path.join(root, 'static/js'), { recursive: true });
    fs.writeFileSync(path.join(docs, 'owner.js'), 'export const val = 1;\nexport const unusedVal = 2;\n');
    fs.writeFileSync(
      path.join(docs, 'broken.js'),
      [
        "import { val, unusedVal } from './owner.js';",
        'val = 3;',
        'missingSymbol();',
        '// document-reference-compat: legacyHook - fallback',
        'if (typeof legacyHook === "function") legacyHook();',
        'legacyHook();',
      ].join('\n')
    );
    fs.writeFileSync(path.join(root, 'static', 'js', 'document.js'), 'export {};\n');
    const results = checkDocumentReferences(root);
    assert.deepEqual(
      results.map(r => [r.type, r.name, r.line]),
      [
        ['unused-import', 'unusedVal', 1],
        ['imported-write', 'val', 2],
        ['unresolved', 'missingSymbol', 3],
        ['unresolved', 'legacyHook', 6],
      ]
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
