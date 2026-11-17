import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { parse } = require('acorn');

// These are web platform names used by the maintained browser modules. Keep this
// list explicit so newly introduced ambient dependencies appear in the report.
export const browserGlobals = new Set(`window self document navigator location history localStorage sessionStorage indexedDB caches fetch Request Response Headers URL URLSearchParams Blob File FileReader FormData Image ImageData ImageBitmap DOMParser XMLSerializer MutationObserver ResizeObserver IntersectionObserver AbortController AbortSignal Event CustomEvent KeyboardEvent MouseEvent PointerEvent InputEvent Element HTMLElement HTMLInputElement HTMLTextAreaElement HTMLSelectElement Node NodeFilter FileList CSS crypto performance console alert confirm prompt atob btoa structuredClone queueMicrotask requestAnimationFrame cancelAnimationFrame setTimeout clearTimeout setInterval clearInterval requestIdleCallback cancelIdleCallback WebSocket XMLHttpRequest Worker TextEncoder TextDecoder Intl getComputedStyle Highlight createImageBitmap matchMedia`.split(' '));
const languageGlobals = new Set(`Array ArrayBuffer BigInt Boolean DataView Date Error EvalError FinalizationRegistry Float32Array Float64Array Function Infinity Int8Array Int16Array Int32Array Intl isFinite isNaN JSON Map Math NaN Number Object Promise Proxy RangeError ReferenceError Reflect RegExp Set SharedArrayBuffer String Symbol SyntaxError TypeError Uint8Array Uint8ClampedArray Uint16Array Uint32Array WeakMap WeakRef WeakSet undefined decodeURI decodeURIComponent encodeURI encodeURIComponent escape unescape globalThis parseFloat parseInt`.split(' '));
const globals = new Set([...browserGlobals, ...languageGlobals]);
const ignored = new Set(['undefined']);
const isId = n => n?.type === 'Identifier';
const childNodes = n => Object.entries(n).flatMap(([k, v]) => {
  if (k === 'loc' || k === 'start' || k === 'end') return [];
  return Array.isArray(v) ? v.filter(x => x?.type).map(x => [x, k]) : v?.type ? [[v, k]] : [];
});

function visitPatternDefaults(pattern, scope, visit) {
  if (!pattern) return;
  if (pattern.type === 'AssignmentPattern') { visit(pattern.right, pattern, 'right', scope); visitPatternDefaults(pattern.left, scope, visit); }
  else if (pattern.type === 'RestElement') visitPatternDefaults(pattern.argument, scope, visit);
  else if (pattern.type === 'ArrayPattern') pattern.elements.forEach(p => visitPatternDefaults(p, scope, visit));
  else if (pattern.type === 'ObjectPattern') pattern.properties.forEach(p => {
    if (p.type === 'RestElement') visitPatternDefaults(p.argument, scope, visit);
    else { if (p.computed) visit(p.key, p, 'key', scope); visitPatternDefaults(p.value, scope, visit); }
  });
}

function patternIds(pattern, out = []) {
  if (!pattern) return out;
  if (pattern.type === 'Identifier') out.push(pattern);
  else if (pattern.type === 'RestElement') patternIds(pattern.argument, out);
  else if (pattern.type === 'AssignmentPattern') patternIds(pattern.left, out);
  else if (pattern.type === 'ArrayPattern') pattern.elements.forEach(x => patternIds(x, out));
  else if (pattern.type === 'ObjectPattern') pattern.properties.forEach(p => patternIds(p.type === 'RestElement' ? p.argument : p.value, out));
  return out;
}

function collectTypeofProbes(expr, target = 'defined', out = new Set()) {
  if (!expr) return out;
  if (expr.type === 'UnaryExpression' && expr.operator === '!') {
    return collectTypeofProbes(expr.argument, target === 'defined' ? 'absent' : 'defined', out);
  }
  if (expr.type === 'LogicalExpression') {
    if ((target === 'defined' && expr.operator === '&&') || (target === 'absent' && expr.operator === '||')) {
      collectTypeofProbes(expr.left, target, out);
      collectTypeofProbes(expr.right, target, out);
    }
    return out;
  }
  if (expr.type === 'BinaryExpression' && ['===', '==', '!==', '!='].includes(expr.operator)) {
    const isEq = expr.operator === '===' || expr.operator === '==';
    const pair = [expr.left, expr.right];
    const typeofNode = pair.find(n => n?.type === 'UnaryExpression' && n.operator === 'typeof' && isId(n.argument));
    const litNode = pair.find(n => n?.type === 'Literal' && typeof n.value === 'string');
    if (typeofNode && litNode) {
      const checksDefined = litNode.value === 'undefined' ? !isEq : isEq;
      if ((target === 'defined' && checksDefined) || (target === 'absent' && !checksDefined)) {
        out.add(typeofNode.argument.name);
      }
    }
  }
  return out;
}

export function analyzeDocumentReferences(source, filename = '<source>') {
  const ast = parse(source, { ecmaVersion: 'latest', sourceType: 'module', locations: true, allowHashBang: true });
  const root = { parent: null, names: new Map() }, refs = [], importedWrites = [], imports = [], diagnostics = [], exports = new Set(), exportAll = [], reexports = [];
  const scopeAt = new WeakMap();
  const declare = (scope, id, kind = 'local') => { if (isId(id)) scope.names.set(id.name, kind); };
  const targetRefs = (n, mode = 'read', scope = root, guarded = new Set()) => {
    if (!n) return;
    if (n.type === 'Identifier') { scopeAt.set(n, scope); refs.push({ node: n, mode, typeofGuarded: guarded.has(n.name) }); return; }
    if (n.type === 'MemberExpression') {
      visit(n.object, n, 'object', scope, guarded);
      if (mode !== 'read' && isId(n.object)) refs.push({ node: n.object, mode: 'namespace-write', typeofGuarded: guarded.has(n.object.name) });
      if (n.computed) visit(n.property, n, 'property', scope, guarded);
      return;
    }
    if (n.type === 'RestElement') return targetRefs(n.argument, mode, scope, guarded);
    if (n.type === 'AssignmentPattern') { targetRefs(n.left, mode, scope, guarded); visit(n.right, null, '', scope, guarded); return; }
    if (n.type === 'ArrayPattern') { n.elements.forEach(x => targetRefs(x, mode, scope, guarded)); return; }
    if (n.type === 'ObjectPattern') { n.properties.forEach(p => { if (p.type === 'RestElement') targetRefs(p.argument, mode, scope, guarded); else { if (p.computed) visit(p.key, p, 'key', scope, guarded); if (p.value.type === 'AssignmentPattern') { targetRefs(p.value.left, mode, scope, guarded); visit(p.value.right, p.value, 'right', scope, guarded); } else targetRefs(p.value, mode, scope, guarded); } }); return; }
    visit(n, null, '', scope, guarded);
  };
  const patternDefaults = (p, s, g) => visitPatternDefaults(p, s, (node, parent, field, sc) => visit(node, parent, field, sc, g));
  function visit(n, parent = null, field = '', scope = root, guarded = new Set()) {
    if (!n) return;
    if (['FunctionDeclaration','FunctionExpression','ArrowFunctionExpression'].includes(n.type)) {
      if (n.type === 'FunctionDeclaration') declare(scope, n.id);
      const s = { parent: scope, names: new Map(), functionScope: true }; scopeAt.set(n, s);
      if (n.type === 'FunctionExpression') declare(s, n.id);
      const fnGuarded = new Set();
      n.params.forEach(p => patternIds(p).forEach(id => declare(s, id)));
      n.params.forEach(p => patternDefaults(p, s, fnGuarded));
      visit(n.body, n, 'body', s, fnGuarded); return;
    }
    if (n.type === 'BlockStatement' || n.type === 'SwitchStatement' || n.type === 'CatchClause') {
      const s = { parent: scope, names: new Map() }; scopeAt.set(n, s);
      if (n.type === 'CatchClause') { patternIds(n.param).forEach(id => declare(s, id)); patternDefaults(n.param, s, guarded); }
      for (const [c, f] of childNodes(n)) visit(c, n, f, s, guarded); return;
    }
    if (n.type === 'IfStatement') {
      visit(n.test, n, 'test', scope, guarded);
      const thenGuarded = new Set(guarded);
      collectTypeofProbes(n.test, 'defined', thenGuarded);
      visit(n.consequent, n, 'consequent', scope, thenGuarded);
      if (n.alternate) {
        const elseGuarded = new Set(guarded);
        collectTypeofProbes(n.test, 'absent', elseGuarded);
        visit(n.alternate, n, 'alternate', scope, elseGuarded);
      }
      return;
    }
    if (n.type === 'ConditionalExpression') {
      visit(n.test, n, 'test', scope, guarded);
      const thenGuarded = new Set(guarded);
      collectTypeofProbes(n.test, 'defined', thenGuarded);
      visit(n.consequent, n, 'consequent', scope, thenGuarded);
      const elseGuarded = new Set(guarded);
      collectTypeofProbes(n.test, 'absent', elseGuarded);
      visit(n.alternate, n, 'alternate', scope, elseGuarded);
      return;
    }
    if (n.type === 'LogicalExpression' && (n.operator === '&&' || n.operator === '||')) {
      visit(n.left, n, 'left', scope, guarded);
      const rightGuarded = new Set(guarded);
      collectTypeofProbes(n.left, n.operator === '&&' ? 'defined' : 'absent', rightGuarded);
      visit(n.right, n, 'right', scope, rightGuarded);
      return;
    }
    if (n.type === 'ForStatement' || n.type === 'ForInStatement' || n.type === 'ForOfStatement') {
      const loop = { parent: scope, names: new Map() };
      const left = n.init || n.left;
      if (left?.type === 'VariableDeclaration' && left.kind !== 'var') left.declarations.forEach(d => patternIds(d.id).forEach(id => declare(loop, id)));
      for (const [c,f] of childNodes(n)) {
        if ((n.type === 'ForInStatement' || n.type === 'ForOfStatement') && f === 'left' && c.type !== 'VariableDeclaration') targetRefs(c,'write',loop,guarded);
        else if ((f === 'left' || f === 'init') && c.type === 'VariableDeclaration') visit(c,n,f,loop,guarded);
        else visit(c,n,f,loop,guarded);
      }
      return;
    }
    scopeAt.set(n, scope);
    if (n.type === 'VariableDeclaration') {
      const dest = n.kind === 'var' ? nearestFunction(scope) : scope;
      n.declarations.forEach(d => patternIds(d.id).forEach(id => declare(dest, id)));
      n.declarations.forEach(d => { patternDefaults(d.id, scope, guarded); if (d.init) visit(d.init, d, 'init', scope, guarded); }); return;
    }
    if (n.type === 'ImportDeclaration') {
      for (const spec of n.specifiers) { const record={ source: n.source.value, name: spec.type === 'ImportDefaultSpecifier' ? 'default' : spec.type === 'ImportNamespaceSpecifier' ? '*' : spec.imported.name, local: spec.local.name, line: spec.loc.start.line, uses: 0 }; scope.names.set(spec.local.name,{kind:'import',record}); imports.push(record); } return;
    }
    if (n.type === 'ExportDefaultDeclaration') { exports.add('default'); visit(n.declaration,n,'declaration',scope,guarded); return; }
    if (n.type === 'ExportAllDeclaration') { if (n.exported) exports.add(n.exported.name); else exportAll.push(n.source.value); return; }
    if (n.type === 'ExportNamedDeclaration') {
      if (n.declaration) {
        if (n.declaration.type === 'VariableDeclaration') n.declaration.declarations.forEach(d => patternIds(d.id).forEach(id => exports.add(id.name)));
        else if (n.declaration.id) exports.add(n.declaration.id.name);
        visit(n.declaration,n,'declaration',scope,guarded);
      } else n.specifiers.forEach(s => { if (s.type === 'ExportSpecifier') { exports.add(s.exported.name); if (n.source) reexports.push({source:n.source.value,name:s.local.name,line:s.loc.start.line}); else visit(s.local,s,'local',scope,guarded); } });
      return;
    }
    if (n.type === 'ClassDeclaration') declare(scope, n.id);
    if (n.type === 'ClassExpression' && n.id) { const s = { parent: scope, names: new Map() }; declare(s, n.id); for (const [c,f] of childNodes(n)) visit(c,n,f,s,new Set()); return; }
    if (n.type === 'AssignmentExpression') { targetRefs(n.left, n.operator === '=' ? 'write' : 'readwrite', scope, guarded); visit(n.right,n,'right',scope,guarded); return; }
    if (n.type === 'UpdateExpression') { targetRefs(n.argument, 'readwrite', scope, guarded); return; }
    if (n.type === 'UnaryExpression' && n.operator === 'typeof' && n.argument.type === 'Identifier') { scopeAt.set(n.argument, scope); refs.push({ node:n.argument, mode:'typeof', typeofGuarded: true }); return; }
    if (n.type === 'ForInStatement' || n.type === 'ForOfStatement') { targetRefs(n.left, 'write', scope, guarded); visit(n.right,n,'right',scope,guarded); visit(n.body,n,'body',scope,guarded); return; }
    if (n.type === 'Identifier') {
      if (parent?.type === 'MemberExpression' && field === 'property' && !parent.computed) return;
      if (parent?.type === 'Property' && field === 'key' && (!parent.computed || parent.shorthand)) return;
      if (parent?.type === 'MethodDefinition' && field === 'key' && !parent.computed) return;
      if (parent?.type === 'LabeledStatement' && field === 'label' || parent?.type === 'BreakStatement' || parent?.type === 'ContinueStatement') return;
      if (parent?.type === 'VariableDeclarator' && field === 'id' || parent?.type === 'FunctionDeclaration' && field === 'id' || parent?.type === 'FunctionExpression' && field === 'id' || parent?.type === 'ClassDeclaration' && field === 'id' || parent?.type === 'ClassExpression' && field === 'id' || parent?.type === 'CatchClause' && field === 'param' || parent?.type.startsWith('Import')) return;
      refs.push({ node: n, mode: 'read', typeofGuarded: guarded.has(n.name) }); return;
    }
    for (const [c, f] of childNodes(n)) visit(c, n, f, scope, guarded);
  }
  function nearestFunction(s) { while (s && !s.functionScope && s !== root) s = s.parent; return s || root; }
  visit(ast);
  function resolve(name, s) { for (let p=s; p; p=p.parent) if (p.names.has(name)) return p.names.get(name); return null; }
  for (const ref of refs) {
    const name=ref.node.name, binding=resolve(name,scopeAt.get(ref.node)||root), line=ref.node.loc.start.line;
    if (binding?.kind === 'import') {
      if (ref.mode !== 'typeof') binding.record.uses++;
      const namespaceWrite = ref.mode === 'namespace-write';
      if (namespaceWrite ? binding.record.name === '*' : ref.mode !== 'read' && ref.mode !== 'typeof') importedWrites.push({ name,line,mode:ref.mode });
    }
    if (ref.mode !== 'namespace-write' && !binding && !globals.has(name) && !ignored.has(name) && ref.mode !== 'typeof') diagnostics.push({ file:filename,line,name,type:'unresolved',typeofGuarded:ref.typeofGuarded });
  }
  const compatibilityAllowances = [];
  for (const line of source.split(/\r?\n/)) {
    const match = line.match(/\/\/\s*document-reference-compat:\s*([A-Za-z_$][\w$]*)\s+-\s+(.+?)\s*$/);
    if (match && refs.some(r => r.node.name === match[1] && r.mode === 'typeof')) compatibilityAllowances.push({ name:match[1], rationale:match[2], line:source.slice(0,source.indexOf(line)).split('\n').length });
  }
  for (let i=diagnostics.length-1;i>=0;i--) {
    if (diagnostics[i].typeofGuarded && compatibilityAllowances.some(a=>a.name===diagnostics[i].name)) diagnostics.splice(i,1);
    else delete diagnostics[i].typeofGuarded;
  }
  return { imports, unusedImports: imports.filter(i => i.uses === 0), importedWrites, diagnostics, exports:[...exports], exportAll, reexports, compatibilityAllowances };
}

export function checkDocumentReferences(root) {
  const files = [];
  const walk = dir => { for (const e of fs.readdirSync(dir,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name))) { const p=path.join(dir,e.name); if(e.isDirectory()) walk(p); else if(e.isFile()&&e.name.endsWith('.js')) files.push(p); } };
  walk(path.join(root,'frontend/src/features/documents'));
  files.push(path.join(root,'static/js/document.js'));
  const results=[];
  for (const file of files) {
    const rel=path.relative(root,file).split(path.sep).join('/');
    const result=analyzeDocumentReferences(fs.readFileSync(file,'utf8'),rel);
    for (const d of result.diagnostics) results.push(d);
    for (const w of result.importedWrites) results.push({file:rel,line:w.line,name:w.name,type:'imported-write'});
    for (const u of result.unusedImports) results.push({file:rel,line:u.line,name:u.local,type:'unused-import'});
  }
  return results.sort((a,b)=>a.file.localeCompare(b.file)||a.line-b.line||a.type.localeCompare(b.type));
}

export function validateDocumentImports(root) {
  const files=[];
  const walk=dir=>{for(const e of fs.readdirSync(dir,{withFileTypes:true})){const p=path.join(dir,e.name);if(e.isDirectory())walk(p);else if(e.isFile()&&e.name.endsWith('.js'))files.push(p);}};
  walk(path.join(root,'frontend/src/features/documents')); files.push(path.join(root,'static/js/document.js'));
  const cache=new Map(), errors=[];
  const moduleInfo=file=>{
    const resolved=path.resolve(file); if(cache.has(resolved))return cache.get(resolved);
    if(!fs.existsSync(resolved)||!fs.statSync(resolved).isFile())return null;
    const info=analyzeDocumentReferences(fs.readFileSync(resolved,'utf8'),path.relative(root,resolved));cache.set(resolved,info);return info;
  };
  const exported=(file,seen=new Set())=>{
    const resolved=path.resolve(file); if(seen.has(resolved))return new Set(); seen.add(resolved);
    const info=moduleInfo(resolved);if(!info)return new Set();
    const names=new Set(info.exports);
    for(const spec of info.exportAll){const target=resolveTarget(resolved,spec);if(target)for(const name of exported(target,seen))if(name!=='default')names.add(name);}
    return names;
  };
  function resolveTarget(importer,specifier) {
    let target;
    if(specifier.startsWith('/static/')) target=path.join(root,'static',specifier.slice('/static/'.length).split(/[?#]/)[0]);
    else if(specifier.startsWith('.')) target=path.resolve(path.dirname(importer),specifier.split(/[?#]/)[0]);
    else return null;
    if(!path.extname(target))target += '.js';
    return target;
  }
  for(const file of files){
    const info=moduleInfo(file); if(!info)continue;
    const rel=path.relative(root,file).split(path.sep).join('/');
    for(const imp of info.imports){
      if(!imp.source.startsWith('.')&&!imp.source.startsWith('/static/'))continue;
      const target=resolveTarget(file,imp.source), targetInfo=moduleInfo(target);
      if(!targetInfo){errors.push({file:rel,line:imp.line,type:'missing-import-target',specifier:imp.source});continue;}
      if(imp.name==='*')continue;
      if(!exported(target).has(imp.name))errors.push({file:rel,line:imp.line,type:'missing-export',specifier:imp.source,name:imp.name});
    }
    for(const spec of info.exportAll){const target=resolveTarget(file,spec);if(!target||!moduleInfo(target))errors.push({file:rel,line:1,type:'missing-export-target',specifier:spec});}
    for(const spec of info.reexports){const target=resolveTarget(file,spec.source);if(!target||!moduleInfo(target))errors.push({file:rel,line:spec.line,type:'missing-export-target',specifier:spec.source});else if(spec.name!=='*'&&!exported(target).has(spec.name))errors.push({file:rel,line:spec.line,type:'missing-reexport',specifier:spec.source,name:spec.name});}
  }
  return errors.sort((a,b)=>a.file.localeCompare(b.file)||a.line-b.line||a.type.localeCompare(b.type));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
  try { const errors=checkDocumentReferences(root), imports=validateDocumentImports(root), all=[...errors,...imports]; if(all.length) { if(process.argv.includes('--json')) console.log(JSON.stringify(all,null,2)); else for(const e of all) console.error(`${e.file}:${e.line}: ${e.type} ${e.name||e.specifier}`); process.exitCode=1; } else console.log('Document reference check passed.'); }
  catch(e) { console.error(e.message); process.exitCode=1; }
}
