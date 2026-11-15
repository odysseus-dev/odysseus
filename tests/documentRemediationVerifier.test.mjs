import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
import { parse } from 'acorn';
import {
  composeRemovalManifest,
  reconcileExportChecks,
  reconcileTopLevelStatement,
  validateAmendmentMetadata,
  validateRecordedSource
} from '../scripts/verify_document_remediation.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const candidateVerifierRoots = [
  process.env.DOCUMENT_EXTRACTION_VERIFIER_ROOT,
  path.resolve(repoRoot, '../../audit-artifacts/tools/document-extraction-verifier'),
  path.resolve(repoRoot, '../audit-artifacts/tools/document-extraction-verifier'),
].filter(Boolean);
const verifierRoot = candidateVerifierRoots.find(candidate => fs.existsSync(candidate)) || candidateVerifierRoots[0];
const hasExternalVerifier = fs.existsSync(path.join(verifierRoot, 'lib/exports.mjs'))
  && fs.existsSync(path.join(verifierRoot, 'lib/transforms.mjs'));
const sha=value=>createHash('sha256').update(value).digest('hex');
const baseAmendments=()=>({schemaVersion:1,auditedRevision:'032d98575f5a7492e2377089cbc68f148753dd64',accountingBaseline:'087e6c8837969339e9e7823dd84a4d717d536cea',functionBodyRewrites:{},openPanelStatementRewrites:[],exports:{}});
const baseRemoval=()=>({baseline:'f3a05c10c998e4efff14ac6498b01bd50138ad6e',statementBaseline:'087e6c8837969339e9e7823dd84a4d717d536cea',functionBodyRewrites:{closePanel:[{from:'old',to:'new'}]},openPanelStatementRewrites:[{id:'stmt#x',replacements:[{from:'a',to:'b'}]}]});

test('amendment metadata pins the audited and canonical baselines',()=>{
  const amendment=baseAmendments();
  assert.deepEqual(validateAmendmentMetadata(amendment,baseRemoval()),[]);
  amendment.accountingBaseline='future';
  assert.ok(validateAmendmentMetadata(amendment,baseRemoval()).some(e=>e.includes('accountingBaseline')));
});

test('manifest composition appends only exact amendment replacements',()=>{
  const amendment=baseAmendments(); amendment.functionBodyRewrites.closePanel={file:'ui/panel/panelLifecycle.js',rationale:'Delegate state cleanup to its owner.',replacements:[{from:'one',to:'two',rationale:'Replace direct cleanup with owner operation.'}],targetFunctionSha256:'a'.repeat(64)};
  amendment.openPanelStatementRewrites=[{id:'stmt#x',rationale:'Use the source editor owner for timer scheduling.',replacements:[{from:'c',to:'d',rationale:'Replace timer writes with owner callback.'}],targetFunctionFile:'ui/panel/panelLifecycle.js',targetFunctionName:'bindComposerWiring',targetFunctionSha256:'b'.repeat(64)}];
  const composed=composeRemovalManifest(baseRemoval(),amendment);
  assert.deepEqual(composed.functionBodyRewrites.closePanel.map(x=>x.from),['old','one']);
  assert.deepEqual(composed.openPanelStatementRewrites[0].replacements.map(x=>x.from),['a','c']);
});

test('export reconciliation preserves non-export canonical failures and rejects extra exports',async t=>{
  if (!hasExternalVerifier) {
    t.skip(`External document-extraction-verifier tool not present at ${verifierRoot}`);
    return;
  }
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'remediation-exports-'));
  try {
    const file=path.join(dir,'ui/example.js'); fs.mkdirSync(path.dirname(file),{recursive:true});
    fs.writeFileSync(file,'export function approved() {}\nexport function accidental() {}\n');
    const {checkExports}=await import(pathToFileURL(path.join(verifierRoot,'lib/exports.mjs')).href);
    const appendix={'ui/example':{exports:['approved']}};
    const original=checkExports({featuresDir:dir,appendixA:appendix});
    const canonical={exports:original,allViolations:[...original.violations,{type:'STATE_OWNER_VIOLATION',id:'retained'}],passed:false};
    const amended=checkExports({featuresDir:dir,appendixA:{'ui/example':{exports:['approved','accidental']}}});
    const result=reconcileExportChecks(canonical,amended);
    assert.deepEqual(result.allViolations.map(v=>v.type),['STATE_OWNER_VIOLATION']);
    const unauthorized=checkExports({featuresDir:dir,appendixA:{'ui/example':{exports:['approved']}}});
    reconcileExportChecks({exports:amended,allViolations:[],passed:true},unauthorized);
    assert.equal(unauthorized.violations[0].type,'EXTRA_EXPORT');
  } finally { fs.rmSync(dir,{recursive:true,force:true}); }
});

test('recorded hashes reject changed corrected functions and exports',()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'remediation-hashes-'));
  try {
    const feature=path.join(dir,'frontend/src/features/documents/ui/example.js');fs.mkdirSync(path.dirname(feature),{recursive:true});
    const fn='function corrected() { return 1; }';
    const ex='export function authorized() { return true; }';
    fs.writeFileSync(feature,`${fn}\n${ex}\n`);
    const amendment=baseAmendments();
    amendment.functionBodyRewrites.corrected={file:'ui/example.js',replacements:[],targetFunctionSha256:sha(fn)};
    amendment.exports['ui/example']=[{name:'authorized',rationale:'Owner API required by the extraction',declarationSha256:sha(ex)}];
    assert.deepEqual(validateRecordedSource(dir,amendment),[]);
    fs.writeFileSync(feature,`${fn.replace('return 1','return 2')}\n${ex}\n`);
    assert.ok(validateRecordedSource(dir,amendment).some(e=>e.includes('corrected function hash changed')));
  } finally { fs.rmSync(dir,{recursive:true,force:true}); }
});

test('canonical function rewrite comparison rejects any unrecorded body change',async t=>{
  if (!hasExternalVerifier) {
    t.skip(`External document-extraction-verifier tool not present at ${verifierRoot}`);
    return;
  }
  const {compareManifestFunction}=await import(pathToFileURL(path.join(verifierRoot,'lib/transforms.mjs')).href);
  const base='function corrected() { return 1; }';
  const targetAst=code=>parse(code,{ecmaVersion:'latest',sourceType:'module'}).body[0];
  const manifest={functionBodyRewrites:{corrected:[{from:'return 1',to:'return 2'}]}};
  const baseline={raw:base};
  const approved=compareManifestFunction({name:'corrected',baseItem:baseline,targetItem:{node:targetAst('function corrected() { return 2; }')},manifest});
  const unrecorded=compareManifestFunction({name:'corrected',baseItem:baseline,targetItem:{node:targetAst('function corrected() { return 3; }')},manifest});
  assert.equal(approved.matched,true);
  assert.equal(unrecorded.matched,false);
});


test('top-level reconciliation requires an exact comparison and preserves other body failures', () => {
  const entry = { id: 'stmt#11782', targetFunctionFile: 'ui/panel/panelLifecycle.js', rationale: 'Owner sentinel' };
  const violation = { type: 'UNAUTHORIZED_AST_MUTATION', id: entry.id, module: 'ui/panel/panelLifecycle' };
  const unrelated = { type: 'UNAUTHORIZED_AST_MUTATION', id: 'other', module: 'ui/panel/panelLifecycle' };
  const result = { allViolations: [violation, unrelated], bodies: { rewritesCount: 0 }, passed: false };
  reconcileTopLevelStatement(result, entry, { matched: false, rewrites: [], exceptions: [] });
  assert(result.allViolations.includes(violation));
  assert(result.allViolations.includes(unrelated));
  assert.equal(result.passed, false);
  const verified = { allViolations: [violation, unrelated], bodies: { rewritesCount: 0 }, passed: false };
  reconcileTopLevelStatement(verified, entry, { matched: true, rewrites: [{ class: 2 }], exceptions: [] });
  assert.deepEqual(verified.allViolations, [unrelated]);
  assert.equal(verified.passed, false);
  assert.equal(verified.statementAmendments[0].resolvedCanonicalDiagnostics.length, 1);
});
