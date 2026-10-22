/** Architecture gates for maintained source. Legacy static modules stay under existing guards. */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from 'acorn';

const layers = ['domain', 'application', 'infrastructure', 'ui'];
const platformIdentifiers = new Set(['window', 'document', 'globalThis', 'navigator', 'localStorage', 'sessionStorage', 'fetch', 'Notification', 'indexedDB', 'WebSocket', 'XMLHttpRequest', 'Worker', 'setTimeout', 'setInterval', 'clearTimeout', 'clearInterval', 'requestAnimationFrame', 'cancelAnimationFrame', 'performance', 'eval', 'Function']);

function ownership(source) {
  const parts = source.split('/');
  if (parts[0] === 'app' && parts.length >= 2) return { feature: 'app', layer: 'index' };
  if (parts[0] === 'shared') {
    if (parts.length === 2) return { feature: 'shared', layer: 'domain' };
    const layer = { domain: 'domain', platform: 'infrastructure', ui: 'ui' }[parts[1]];
    if (layer) return { feature: 'shared', layer };
  }
  if (parts[0] === 'features' && parts.length >= 3) {
    if (['app', 'shared'].includes(parts[1])) throw new Error(`reserved feature name: ${parts[1]}`);
    if (parts.length === 3 && parts[2] === 'index.js') return { feature: parts[1], layer: 'index' };
    if (layers.includes(parts[2]) && parts.length >= 4) return { feature: parts[1], layer: parts[2] };
  }
  throw new Error(`unsupported source ownership: ${source}`);
}

export function allowsDependency(from, to) {
  const a = ownership(from), b = ownership(to);
  if (b.feature === 'app') return a.feature === 'app';
  if (a.feature === 'app') return b.feature === 'shared' || b.layer === 'index';
  if (b.feature === 'shared') {
    const shared = { domain: ['domain'], application: ['domain'], infrastructure: ['domain', 'infrastructure'], ui: ['domain', 'infrastructure', 'ui'], index: layers };
    return shared[a.layer].includes(b.layer);
  }
  if (a.feature === 'shared') return false;
  if (a.feature !== b.feature) return ['application', 'index'].includes(a.layer) && b.layer === 'index';
  const internal = { domain: ['domain'], application: ['domain', 'application'], infrastructure: ['domain', 'infrastructure'], ui: ['domain', 'application', 'ui'], index: layers };
  return internal[a.layer].includes(b.layer);
}

export function imports(source, { domain = false } = {}) {
  const ast = parse(source, { ecmaVersion: 'latest', sourceType: 'module', locations: true });
  const found = [];
  function visit(node, parent, field) {
    if (!node || typeof node !== 'object') return;
    // Domain modules use explicit values/ports, including an injected clock.
    // Keep platform vocabulary out even when a local binding shadows a global.
    const propertyName = parent && !parent.computed && (
      (parent.type === 'MemberExpression' && field === 'property') ||
      (['Property', 'MethodDefinition'].includes(parent.type) && field === 'key' && !parent.shorthand)
    );
    if (domain && !propertyName && node.type === 'Identifier' && platformIdentifiers.has(node.name)) {
      throw new Error(`platform identifier in domain at line ${node.loc.start.line}: ${node.name}`);
    }
    if (domain && ((node.type === 'MemberExpression' && node.object.name === 'Date' && (node.property.name === 'now' || node.property.value === 'now')) || (['CallExpression', 'NewExpression'].includes(node.type) && node.callee.name === 'Date' && !node.arguments.length))) {
      throw new Error(`ambient clock in domain at line ${node.loc.start.line}; inject time`);
    }
    if (['ImportDeclaration', 'ExportNamedDeclaration', 'ExportAllDeclaration', 'ImportExpression'].includes(node.type) && node.source) {
      if (node.source.type !== 'Literal' || typeof node.source.value !== 'string') {
        throw new Error(`computed import at line ${node.loc.start.line}; use explicit import thunks`);
      }
      found.push({ specifier: node.source.value, line: node.loc.start.line, dynamic: node.type === 'ImportExpression' });
    }
    for (const [key, value] of Object.entries(node)) {
      if (Array.isArray(value)) value.forEach(item => visit(item, node, key));
      else if (value && typeof value === 'object' && value.type) visit(value, node, key);
    }
  }
  visit(ast);
  return found;
}

export function checkArchitecture(root) {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'frontend/modules.json'), 'utf8'));
  const bySource = new Map(manifest.modules.map(m => [m.source, m]));
  const byOutput = new Map(manifest.modules.map(m => [`/static/${m.deployment}`, m.source]));
  const graph = new Map();
  const errors = [];
  const sw = fs.readFileSync(path.join(root, 'static/sw.js'), 'utf8');
  // Parse the actual manifest arrays, not comments or unrelated strings.
  const swAst = parse(sw, { ecmaVersion: 'latest', sourceType: 'script' });
  const precached = new Set();
  for (const statement of swAst.body) {
    for (const declaration of statement.declarations || []) {
      if (['PRECACHE', 'PANEL_PRECACHE'].includes(declaration.id.name)) {
        for (const item of declaration.init.elements) {
          if (item?.type === 'Literal' && typeof item.value === 'string') precached.add(item.value);
        }
      }
    }
  }
  for (const module of manifest.modules) {
    try {
      const owner = ownership(module.source);
      const deployed = `/static/${module.deployment}`;
      if (!precached.has(deployed)) throw new Error(`deployment URL absent from explicit precache: ${deployed}`);
      const dependencies = [];
      const exceptions = module.legacyImports || [];
      const used = new Set();
      for (const { specifier, line } of imports(fs.readFileSync(path.join(root, 'frontend/src', module.source), 'utf8'), { domain: owner.layer === 'domain' })) {
        const fail = message => { throw new Error(`line ${line}: ${message}: ${specifier}`); };
        const runtime = new URL(specifier, `https://odysseus.invalid${deployed}`);
        if (!runtime.pathname.startsWith('/static/js/') || !runtime.pathname.endsWith('.js') || runtime.pathname.includes('%')) fail('runtime imports must name canonical static JS paths');
        let target;
        if (specifier.startsWith('.')) {
          if (runtime.search || runtime.hash) fail('maintained modules use one unversioned URL identity');
          target = path.posix.normalize(path.posix.join(path.posix.dirname(module.source), specifier));
          if (!bySource.has(target)) fail('relative import must name maintained source');
          if (runtime.pathname !== `/static/${bySource.get(target).deployment}`) fail('source and deployment relative paths resolve differently');
        } else if (specifier.startsWith('/static/')) {
          target = byOutput.get(runtime.pathname);
          if (!target) {
            const exception = exceptions.find(e => e.specifier === specifier && e.reason?.trim());
            if (!exception || !['index', 'infrastructure', 'ui'].includes(owner.layer) || owner.feature === 'shared') fail('legacy import requires an explicit module-scoped rationale at a composition, infrastructure or UI boundary');
            if (!fs.statSync(path.join(root, runtime.pathname.slice(1))).isFile()) fail('legacy target does not exist');
            used.add(exception);
            continue;
          }
        } else fail('bare, external and source-tree runtime imports are unsupported');
        if (runtime.search || runtime.hash) fail('maintained modules use one unversioned URL identity');
        if (!allowsDependency(module.source, target)) fail(`forbidden dependency on ${target}`);
        dependencies.push(target);
      }
      if (exceptions.some(e => !used.has(e))) throw new Error('unused legacy import exception');
      graph.set(module.source, dependencies);
    } catch (error) { errors.push(`${module.source}: ${error.message}`); }
  }
  const visiting = new Set(), visited = new Set();
  function visit(source, trail = []) {
    if (visiting.has(source)) throw new Error(`maintained-source dependency cycle: ${[...trail, source].join(' -> ')}`);
    if (visited.has(source)) return;
    visiting.add(source);
    for (const target of graph.get(source) || []) visit(target, [...trail, source]);
    visiting.delete(source);
    visited.add(source);
  }
  try { for (const source of graph.keys()) visit(source); }
  catch (error) { errors.push(error.message); }
  if (errors.length) throw new Error(errors.join('\n'));
  return graph.size;
}

export function inventory(root) {
  const staticRoot = path.join(root, 'static');
  const files = fs.readdirSync(staticRoot, { recursive: true }).map(p => p.split(path.sep).join('/')).filter(p => p.endsWith('.js') && !p.startsWith('lib/') && !p.startsWith('js/editor/build/')).sort();
  const graph = new Map(), fan = new Map(), missing = [];
  let staticImports = 0, dynamicLiteralImports = 0;
  for (const file of files) {
    const targets = [];
    for (const { specifier, dynamic } of imports(fs.readFileSync(path.join(staticRoot, file), 'utf8'))) {
      if (!specifier.startsWith('.') && !specifier.startsWith('/static/')) continue;
      const target = new URL(specifier, `https://odysseus.invalid/static/${file}`).pathname.slice('/static/'.length);
      if (dynamic) dynamicLiteralImports++;
      else { staticImports++; targets.push(target); }
      fan.set(target, (fan.get(target) || 0) + 1);
      if (!fs.existsSync(path.join(staticRoot, target))) missing.push({ file, specifier });
    }
    graph.set(file, targets);
  }
  const indices = new Map(), low = new Map(), stack = [], active = new Set(), staticCycles = [];
  function visit(v) {
    indices.set(v, indices.size); low.set(v, indices.get(v)); stack.push(v); active.add(v);
    for (const w of graph.get(v) || []) {
      if (!indices.has(w)) { visit(w); low.set(v, Math.min(low.get(v), low.get(w))); }
      else if (active.has(w)) low.set(v, Math.min(low.get(v), indices.get(w)));
    }
    if (low.get(v) === indices.get(v)) {
      const component = []; let w;
      do { w = stack.pop(); active.delete(w); component.push(w); } while (w !== v);
      if (component.length > 1) staticCycles.push(component.sort());
    }
  }
  for (const file of files) if (!indices.has(file)) visit(file);
  return { files: files.length, staticImports, dynamicLiteralImports, missing, fanIn: [...fan].sort((a, b) => b[1] - a[1]).slice(0, 8), staticCycles };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  try { console.log(process.argv.includes('--inventory') ? JSON.stringify(inventory(root), null, 2) : `Frontend architecture passed (${checkArchitecture(root)} maintained modules).`); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
