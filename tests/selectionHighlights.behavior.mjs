import { describe, it, before } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { SourceTextModule, SyntheticModule } from 'node:vm';
import { installDom } from './documentLaneDom.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const entryPath = path.join(root, 'frontend/src/features/documents/ui/selection/selectionHighlights.js');
const source = fs.readFileSync(entryPath, 'utf8') + '\nexport { installSelectionResizeListener, _validateSelections };';
const listeners = [];
let api;
async function load() {
 const cache=new Map();
 async function linker(spec,parent) {
  const target=path.resolve(path.dirname(fileURLToPath(parent.identifier)),spec), name=path.basename(target), url=pathToFileURL(target).href;
  if(cache.has(url)) return cache.get(url);
  const imports={
   'documentState.js':{activeDocId:null,docs:new Map()},
   'documentKinds.js':{_isRichTextLang:lang=>lang==='richtext'},
   'documentFlows.js':{_ensureDocPaneMounted(){},loadDocument:async()=>true},
   'richBodySync.js':{_emailRichbodyActive:()=>null},
   'markdownVisualSync.js':{isMarkdownVisualModeActive:()=>false},
   'documentStatus.js':{_scheduleDocumentStats(){}},
  };
  const exports=imports[name];
  const mod=new SyntheticModule(Object.keys(exports),function(){for(const [k,v] of Object.entries(exports))this.setExport(k,v);},{identifier:url});
  cache.set(url,mod); await mod.link(linker); return mod;
 }
 const url=pathToFileURL(entryPath).href, entry=new SourceTextModule(source,{identifier:url});
 await entry.link(linker); await entry.evaluate(); return entry.namespace;
}

describe('selectionHighlights lifecycle and reanchoring',()=>{
 before(async()=>{
  installDom();
  window.addEventListener=(type,fn)=>listeners.push({type,fn});
  window.removeEventListener=(type,fn)=>{const i=listeners.findIndex(x=>x.type===type&&x.fn===fn);if(i>=0)listeners.splice(i,1);};
  api=await load();
 });
 it('installs one page resize listener and returns an idempotent disposer',()=>{
  assert.equal(listeners.filter(x=>x.type==='resize').length,1);
  const dispose=api.installSelectionResizeListener();
  assert.equal(api.installSelectionResizeListener(),dispose);
  assert.equal(listeners.filter(x=>x.type==='resize').length,1);
  dispose(); dispose();
  assert.equal(listeners.filter(x=>x.type==='resize').length,0);
 });
 it('reanchors stale offsets to the nearest exact text occurrence and drops removed text',()=>{
  api.replaceSelections([{text:'target',start:16,end:22,startLine:2,endLine:2},{text:'gone',start:0,end:4}]);
  api._validateSelections('target\nfar target\n');
  assert.equal(api._selections.length,1);
  assert.deepEqual({start:api._selections[0].start,end:api._selections[0].end,startLine:api._selections[0].startLine}, {start:11,end:17,startLine:2});
 });
});
