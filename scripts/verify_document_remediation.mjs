import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const { parse } = require('acorn');
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, '..');
const verifierRoot = process.env.DOCUMENT_EXTRACTION_VERIFIER_ROOT || path.resolve(repoRoot, '../../audit-artifacts/tools/document-extraction-verifier');
const canonicalImport = path.join(verifierRoot, 'lib/index.mjs');
const canonicalExports = path.join(verifierRoot, 'lib/exports.mjs');
const amendmentPath = path.join(repoRoot, 'docs/document-editor-lanes/audit-remediation/verifier-amendments.json');
const removalPath = path.join(repoRoot, 'docs/document-editor-lanes/lane-c-panel-lifecycle/removal.json');
const BASELINE_REF = '087e6c8837969339e9e7823dd84a4d717d536cea';
const AUDITED_REVISION = '032d98575f5a7492e2377089cbc68f148753dd64';
const BASELINE_SHA256 = 'e0f7aa5983e9e34af872ac275eb885259d5bb3fc7ac5bd86df196632f2b5ff21';

const sha256 = value => createHash('sha256').update(value).digest('hex');
const parseSource = source => parse(source, { ecmaVersion: 'latest', sourceType: 'module', locations: true });

export function validateAmendmentMetadata(amendments, removalManifest) {
  const errors=[];
  if(amendments.schemaVersion!==1) errors.push('unsupported verifier amendments schemaVersion');
  if(amendments.auditedRevision!==AUDITED_REVISION) errors.push('amendments auditedRevision does not match the audited source revision');
  if(amendments.accountingBaseline!==BASELINE_REF) errors.push('amendments accountingBaseline does not match the canonical verifier baseline');
  if(removalManifest.baseline!=='f3a05c10c998e4efff14ac6498b01bd50138ad6e') errors.push('active removal manifest baseline changed');
  if(removalManifest.statementBaseline!==BASELINE_REF) errors.push('active removal manifest statementBaseline changed');
  if(!amendments.exports || typeof amendments.exports!=='object' || Array.isArray(amendments.exports)) errors.push('amendments exports must be a module keyed object');
  if(!amendments.functionBodyRewrites || typeof amendments.functionBodyRewrites!=='object' || Array.isArray(amendments.functionBodyRewrites)) errors.push('functionBodyRewrites must be a symbol keyed object');
  if(!Array.isArray(amendments.openPanelStatementRewrites)) errors.push('openPanelStatementRewrites must be an array');
  for(const [module,entries] of Object.entries(amendments.exports||{})) {
    if(!Array.isArray(entries)) errors.push(`export amendment for ${module} must be an array`);
    for(const e of Array.isArray(entries)?entries:[]) {
      if(!e.name || !e.rationale?.trim() || !/^[a-f0-9]{64}$/.test(e.declarationSha256||'')) errors.push(`export amendment ${module}:${e.name||'<unnamed>'} needs a name, rationale and declarationSha256`);
    }
  }
  for(const [symbol,entry] of Object.entries(amendments.functionBodyRewrites||{})) {
    if(!entry.file || !entry.rationale?.trim() || !Array.isArray(entry.replacements) || !/^[a-f0-9]{64}$/.test(entry.targetFunctionSha256||'')) errors.push(`function amendment ${symbol} needs file, rationale, replacements and targetFunctionSha256`);
    for(const replacement of entry.replacements||[]) if(typeof replacement.from!=='string'||typeof replacement.to!=='string'||!replacement.rationale?.trim()) errors.push(`function amendment ${symbol} replacement needs exact from/to strings and rationale`);
  }
  for(const entry of amendments.openPanelStatementRewrites||[]) {
    if(!entry.id || !entry.rationale?.trim() || !Array.isArray(entry.replacements) || !entry.targetFunctionFile || !entry.targetFunctionName || !/^[a-f0-9]{64}$/.test(entry.targetFunctionSha256||'')) errors.push(`openPanel statement amendment ${entry.id||'<unnamed>'} needs rationale, replacements and a target function hash`);
    for(const replacement of entry.replacements||[]) if(typeof replacement.from!=='string'||typeof replacement.to!=='string'||!replacement.rationale?.trim()) errors.push(`openPanel statement amendment ${entry.id} replacement needs exact from/to strings and rationale`);
  }
  return errors;
}

export function composeRemovalManifest(removalManifest, amendments) {
  const combined=structuredClone(removalManifest);
  combined.functionBodyRewrites={...(combined.functionBodyRewrites||{})};
  for(const [symbol,entry] of Object.entries(amendments.functionBodyRewrites||{})) {
    combined.functionBodyRewrites[symbol]=[...(combined.functionBodyRewrites[symbol]||[]),...entry.replacements];
  }
  const statements=new Map((combined.openPanelStatementRewrites||[]).map(item=>[item.id,item]));
  for(const entry of amendments.openPanelStatementRewrites||[]) {
    const prior=statements.get(entry.id);
    if(prior) prior.replacements=[...prior.replacements,...entry.replacements];
    else statements.set(entry.id,{id:entry.id,replacements:[...entry.replacements]});
  }
  combined.openPanelStatementRewrites=[...statements.values()];
  return combined;
}

function functionRaw(source,file,symbol) {
  const ast=parseSource(source), found=[];
  const visit=n=>{
    if(!n||typeof n!=='object')return;
    if(n.type==='FunctionDeclaration'&&n.id?.name===symbol)found.push(source.slice(n.start,n.end));
    for(const [key,value] of Object.entries(n)) if(!['loc','start','end'].includes(key)) {
      if(Array.isArray(value)) value.forEach(visit); else if(value&&typeof value==='object'&&value.type) visit(value);
    }
  };
  visit(ast);
  if(found.length!==1)throw new Error(`${file}:${symbol} expected one function declaration, found ${found.length}`);
  return found[0];
}

function exportDeclarationRaw(source,module,name) {
  const ast=parseSource(source), found=[];
  for(const node of ast.body) {
    if(node.type==='ExportNamedDeclaration') {
      if(node.declaration) {
        const d=node.declaration;
        const names=d.type==='VariableDeclaration'?d.declarations.flatMap(v=>v.id.type==='Identifier'?[v.id.name]:[]):d.id?[d.id.name]:[];
        if(names.includes(name))found.push(source.slice(node.start,node.end));
      } else if(node.specifiers?.some(s=>s.exported.name===name)) found.push(source.slice(node.start,node.end));
    } else if(node.type==='ExportDefaultDeclaration'&&name==='default') found.push(source.slice(node.start,node.end));
  }
  if(found.length!==1)throw new Error(`${module}:${name} expected one exported declaration, found ${found.length}`);
  return found[0];
}

export function validateRecordedSource(root, amendments) {
  const errors=[];
  for(const [symbol,entry] of Object.entries(amendments.functionBodyRewrites||{})) {
    const full=path.join(root,'frontend/src/features/documents',entry.file);
    try { if(sha256(functionRaw(fs.readFileSync(full,'utf8'),entry.file,symbol))!==entry.targetFunctionSha256) errors.push(`corrected function hash changed: ${entry.file}:${symbol}`); }
    catch(error) { errors.push(error.message); }
  }
  for(const [module,entries] of Object.entries(amendments.exports||{})) {
    const full=path.join(root,'frontend/src/features/documents',`${module}.js`);
    for(const e of entries) try {
      if(sha256(exportDeclarationRaw(fs.readFileSync(full,'utf8'),module,e.name))!==e.declarationSha256) errors.push(`authorized export hash changed: ${module}:${e.name}`);
    } catch(error) { errors.push(error.message); }
  }
  for(const e of amendments.openPanelStatementRewrites||[]) {
    const full=path.join(root,'frontend/src/features/documents',e.targetFunctionFile);
    try { if(sha256(functionRaw(fs.readFileSync(full,'utf8'),e.targetFunctionFile,e.targetFunctionName))!==e.targetFunctionSha256) errors.push(`corrected function hash changed: ${e.targetFunctionFile}:${e.targetFunctionName}`); }
    catch(error) { errors.push(error.message); }
  }
  return errors;
}

export function reconcileExportChecks(canonicalResult, amendedResult) {
  const canonicalViolations=new Set(canonicalResult.exports.violations);
  canonicalResult.allViolations=canonicalResult.allViolations.filter(v=>!canonicalViolations.has(v));
  canonicalResult.allViolations.push(...amendedResult.violations);
  canonicalResult.exports=amendedResult;
  canonicalResult.passed=canonicalResult.allViolations.length===0;
  return canonicalResult;
}

// The canonical engine routes extracted openPanel statements through its
// exact-replacement comparator, but omits top-level statement wrappers.
// Reuse that same comparator and retain every unrelated canonical failure.
export function reconcileTopLevelStatement(result, entry, comparison) {
  if (!comparison.matched) {
    result.allViolations.push({ type: 'INVALID_AMENDMENT', id: entry.id, message: 'Top-level statement differs after its exact recorded replacements' });
    result.passed = false;
    return result;
  }
  const module = entry.targetFunctionFile.replace(/\.js$/, '');
  const matches = violation => violation.type === 'UNAUTHORIZED_AST_MUTATION'
    && (violation.id || violation.name) === entry.id
    && (violation.module || violation.targetModule) === module;
  const resolved = result.allViolations.filter(matches);
  result.allViolations = result.allViolations.filter(violation => !matches(violation));
  if (!result.statementAmendments) result.statementAmendments = [];
  result.statementAmendments.push({ id: entry.id, module, rationale: entry.rationale, resolvedCanonicalDiagnostics: resolved, comparison });
  if (resolved.length) result.bodies.rewritesCount += comparison.rewrites.length;
  const removal = result.removal;
  if (removal?.violations.some(matches)) {
    removal.violations = removal.violations.filter(violation => !matches(violation));
    removal.verifiedDeclarations.push({ name: entry.id, targetModule: module, rewrites: comparison.rewrites, exceptions: comparison.exceptions });
    removal.rewritesApplied.push(...comparison.rewrites);
    removal.summary.verifiedDeclarationsCount = removal.verifiedDeclarations.length;
    removal.summary.rewritesCount = removal.rewritesApplied.length;
    removal.summary.violationsCount = removal.violations.length;
    removal.summary.passed = removal.violations.length === 0;
  }
  result.passed = result.allViolations.length === 0;
  return result;
}

export async function verifyDocumentRemediation({root=repoRoot, amendmentFile=amendmentPath}={}) {
  const amendments=JSON.parse(fs.readFileSync(amendmentFile,'utf8'));
  const activeRemoval=JSON.parse(fs.readFileSync(removalPath,'utf8'));
  const errors=[...validateAmendmentMetadata(amendments,activeRemoval),...validateRecordedSource(root,amendments)];
  const baselineFile=path.join(verifierRoot,'fixtures/baseline-087e6c88.js');
  if(sha256(fs.readFileSync(baselineFile,'utf8'))!==BASELINE_SHA256) errors.push('canonical 087e6c88 baseline fixture hash changed');
  if(errors.length) return {passed:false,amendments:{path:path.relative(root,amendmentFile),errors},allViolations:errors.map(message=>({type:'INVALID_AMENDMENT',message}))};
  const {verifyExtraction}=await import(pathToFileURL(canonicalImport).href);
  const {checkExports}=await import(pathToFileURL(canonicalExports).href);
  const appendixPath=path.join(verifierRoot,'data/appendix-A.json');
  const appendix=JSON.parse(fs.readFileSync(appendixPath,'utf8'));
  const amendedAppendix=structuredClone(appendix);
  for(const [module,entries] of Object.entries(amendments.exports||{})) {
    if(!amendedAppendix[module]) throw new Error(`export amendment module ${module} is absent from Appendix A`);
    amendedAppendix[module].exports=[...new Set([...amendedAppendix[module].exports,...entries.map(e=>e.name)])].sort();
  }
  const manifestData=composeRemovalManifest(activeRemoval,amendments);
  const result=await verifyExtraction({worktreeDir:root,baselineSourcePath:path.join(verifierRoot,'fixtures/baseline-087e6c88.js'),baselineRef:BASELINE_REF,manifestData});
  const amendedExports=checkExports({featuresDir:path.join(root,'frontend/src/features/documents'),appendixA:amendedAppendix});
  reconcileExportChecks(result,amendedExports);
  const topLevel = amendments.openPanelStatementRewrites.filter(entry => entry.id.startsWith('stmt#'));
  if (topLevel.length) {
    const { loadBaselineFromFile } = await import(pathToFileURL(path.join(verifierRoot, 'lib/baseline.mjs')).href);
    const { compareManifestStatement } = await import(pathToFileURL(path.join(verifierRoot, 'lib/transforms.mjs')).href);
    const catalog = loadBaselineFromFile(baselineFile);
    for (const entry of topLevel) {
      const baseItem = catalog.get(entry.id);
      if (baseItem?.kind !== 'stmt') throw new Error(`Unknown top-level amendment ${entry.id}`);
      const source = fs.readFileSync(path.join(root, 'frontend/src/features/documents', entry.targetFunctionFile), 'utf8');
      const wrapper = parseSource(functionRaw(source, entry.targetFunctionFile, entry.targetFunctionName)).body[0];
      const comparison = compareManifestStatement({ name: entry.id, baseItem, targetItem: { node: wrapper }, manifest: manifestData });
      reconcileTopLevelStatement(result, entry, comparison);
    }
  }
  result.amendments={path:path.relative(root,amendmentFile),auditedRevision:amendments.auditedRevision,accountingBaseline:amendments.accountingBaseline,functionBodies:Object.fromEntries(Object.entries(amendments.functionBodyRewrites||{}).map(([symbol,e])=>[symbol,{file:e.file,rationale:e.rationale,targetFunctionSha256:e.targetFunctionSha256}])),openPanelStatements:(amendments.openPanelStatementRewrites||[]).map(e=>({id:e.id,rationale:e.rationale,targetFunctionFile:e.targetFunctionFile,targetFunctionName:e.targetFunctionName,targetFunctionSha256:e.targetFunctionSha256})),exports:Object.fromEntries(Object.entries(amendments.exports||{}).map(([m,es])=>[m,es.map(e=>({name:e.name,rationale:e.rationale,declarationSha256:e.declarationSha256}))]))};
  return result;
}

if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  try {
    const result=await verifyDocumentRemediation({root:repoRoot});
    console.log(JSON.stringify(result,null,2));
    process.exitCode=result.passed?0:1;
  } catch(error) { console.log(JSON.stringify({passed:false,error:error.message},null,2)); process.exitCode=1; }
}
