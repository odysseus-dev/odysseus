import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { allowsDependency, imports, checkArchitecture, inventory } from '../scripts/check_frontend.mjs';
import * as source from '../frontend/src/shared/actionMenuOrder.js';
import * as deployed from '../static/js/actionMenuOrder.js';

test('stateless helper preserves its public API, ordering, references and inputs', () => {
  for (const module of [source, deployed]) {
    assert.deepEqual(Object.keys(module).sort(), ['SELECT_MENU_ICON', 'actionMenuRank', 'orderActionMenuItems']);
    const callback = () => {};
    const items = ['Delete', 'Select', 'Copy', 'Rename', 'Archive', 'Open', 'Cancel'].map(label => Object.freeze({ label, action: callback }));
    Object.freeze(items);
    const sorted = module.orderActionMenuItems(items);
    assert.deepEqual(sorted.map(i => i.label), ['Open', 'Rename', 'Copy', 'Select', 'Archive', 'Delete', 'Cancel']);
    assert.ok(sorted.every(i => items.includes(i)));
    assert.deepEqual(module.orderActionMenuItems([]), []);
    assert.equal(module.actionMenuRank({ menuOrder: 12, label: 'Delete' }), 12);
    assert.equal(module.actionMenuRank({ label: 'Unknown' }), 100);
    const equalRank = [{ label: 'Open first' }, { label: 'Open second' }];
    assert.deepEqual(module.orderActionMenuItems(equalRank), equalRank);
  }
  assert.equal(source.SELECT_MENU_ICON, deployed.SELECT_MENU_ICON);
});

test('dependency direction separates composition, workflow, adapters and views', () => {
  const n = name => `features/notes/${name}.js`;
  const allowed = [
    ['app/bootstrap.js', n('index')],
    [n('index'), n('infrastructure/api')],
    [n('application/crud'), n('domain/recurrence')],
    [n('infrastructure/api'), 'shared/platform/http.js'],
    [n('ui/panel'), n('application/crud')],
    [n('application/reminders'), 'features/calendar/index.js'],
  ];
  for (const [from, to] of allowed) assert.ok(allowsDependency(from, to), `${from} -> ${to}`);
  const forbidden = [
    [n('domain/recurrence'), n('infrastructure/api')],
    [n('application/crud'), n('ui/panel')],
    [n('application/crud'), n('infrastructure/api')],
    [n('ui/panel'), 'features/calendar/index.js'],
    [n('application/crud'), 'features/calendar/application/store.js'],
    ['app/bootstrap.js', n('domain/recurrence')],
    ['shared/platform/http.js', n('index')],
    [n('domain/recurrence'), 'app/bootstrap.js'],
  ];
  for (const [from, to] of forbidden) assert.equal(allowsDependency(from, to), false, `${from} -> ${to}`);
});

test('parser sees re-exports, multiline and dynamic imports without comment false positives', () => {
  const code = `// import './false.js';
    const text = "import './false.js'";
    import { a }\n from './a.js';
    export { b } from './b.js';
    export * from './c.js';
    const load = () => import('./d.js');`;
  assert.deepEqual(imports(code).map(i => i.specifier), ['./a.js', './b.js', './c.js', './d.js']);
  assert.throws(() => imports('const load = p => import(p);'), /computed import/);
  assert.throws(() => imports('export const now = () => Date.now();', { domain: true }), /ambient clock/);
  assert.throws(() => imports('export const load = () => fetch("/api/notes");', { domain: true }), /platform identifier/);
  assert.doesNotThrow(() => imports('export const day = value => new Date(value);', { domain: true }));
  assert.doesNotThrow(() => imports('export const copy = value => ({ document: value.document });', { domain: true }));
});

function fixture(t, modules, { cache = true } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ody-frontend-gates-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const entries = Object.entries(modules).map(([name, code]) => {
    const file = path.join(root, 'frontend/src', name);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, code);
    return { source: name, deployment: `js/${name}` };
  });
  fs.mkdirSync(path.join(root, 'static'), { recursive: true });
  fs.writeFileSync(path.join(root, 'frontend/modules.json'), JSON.stringify({ version: 1, modules: entries }));
  const urls = cache ? entries.map(m => `/static/${m.deployment}`) : [];
  fs.writeFileSync(path.join(root, 'static/sw.js'), `const PRECACHE = ${JSON.stringify(urls)}; const PANEL_PRECACHE = [];`);
  return root;
}

test('complete mapped graph and exact precache pass', t => {
  const root = fixture(t, { 'features/notes/index.js': "export { value } from './domain/value.js';", 'features/notes/domain/value.js': 'export const value = 1;' });
  assert.equal(checkArchitecture(root), 2);
});

test('feature-private import fails through the full checker', t => {
  const root = fixture(t, { 'features/notes/application/a.js': "import '../../calendar/domain/b.js';", 'features/calendar/domain/b.js': 'export const value = 1;' });
  assert.throws(() => checkArchitecture(root), /forbidden dependency/);
});

test('undeclared legacy coupling and alternate identities fail', t => {
  for (const specifier of ['/static/js/notes.js?v=legacy', './b.js?v=other', '/frontend/src/shared/b.js', 'some-package']) {
    const root = fixture(t, { 'shared/a.js': `import '${specifier}';`, 'shared/b.js': 'export const value = 1;' });
    assert.throws(() => checkArchitecture(root), /legacy import|one unversioned|unsupported|canonical static/);
  }
});

test('a copy mapping cannot silently change relative import resolution', t => {
  const root = fixture(t, { 'shared/a.js': "import './b.js';", 'shared/b.js': 'export const value = 1;' });
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'frontend/modules.json')));
  manifest.modules[1].deployment = 'js/elsewhere/b.js';
  fs.writeFileSync(path.join(root, 'frontend/modules.json'), JSON.stringify(manifest));
  fs.writeFileSync(path.join(root, 'static/sw.js'), "const PRECACHE = ['/static/js/shared/a.js', '/static/js/elsewhere/b.js'];");
  assert.throws(() => checkArchitecture(root), /resolve differently/);
});

test('new modules must join exact offline manifests and cannot introduce cycles', t => {
  const missing = fixture(t, { 'shared/a.js': 'export const a = 1;' }, { cache: false });
  assert.throws(() => checkArchitecture(missing), /absent from explicit precache/);
  const cyclic = fixture(t, { 'shared/a.js': "import './b.js';", 'shared/b.js': "import './a.js';" });
  assert.throws(() => checkArchitecture(cyclic), /dependency cycle/);
});

test('legacy adapters require exact, used exceptions and reserved names cannot bypass ownership', t => {
  const root = fixture(t, { 'features/notes/infrastructure/api.js': "import '/static/js/legacy.js?v=existing';" });
  fs.mkdirSync(path.join(root, 'static/js'), { recursive: true });
  fs.writeFileSync(path.join(root, 'static/js/legacy.js'), 'export const legacy = true;');
  const file = path.join(root, 'frontend/modules.json');
  const manifest = JSON.parse(fs.readFileSync(file));
  manifest.modules[0].legacyImports = [{ specifier: '/static/js/legacy.js?v=existing', reason: 'Temporary existing transport adapter; remove in N2.' }];
  fs.writeFileSync(file, JSON.stringify(manifest));
  assert.equal(checkArchitecture(root), 1);
  manifest.modules[0].legacyImports.push({ specifier: '/static/js/unused.js', reason: 'stale' });
  fs.writeFileSync(file, JSON.stringify(manifest));
  assert.throws(() => checkArchitecture(root), /unused legacy import exception/);
  assert.throws(() => allowsDependency('features/shared/index.js', 'app/bootstrap.js'), /reserved feature name/);
});

test('inventory reports executable static cycles and separates dynamic edges', t => {
  const root = fixture(t, { 'shared/a.js': 'export const a = 1;' });
  fs.mkdirSync(path.join(root, 'static/js'), { recursive: true });
  fs.writeFileSync(path.join(root, 'static/js/a.js'), "import './b.js'; const load = () => import('./c.js');");
  fs.writeFileSync(path.join(root, 'static/js/b.js'), "import './a.js';");
  fs.writeFileSync(path.join(root, 'static/js/c.js'), 'export const c = 1;');
  const result = inventory(root);
  assert.equal(result.staticImports, 2);
  assert.equal(result.dynamicLiteralImports, 1);
  assert.deepEqual(result.staticCycles, [['js/a.js', 'js/b.js']]);
  assert.deepEqual(result.missing, []);
});
