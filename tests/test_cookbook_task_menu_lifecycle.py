"""Exercise shipped task-menu wiring, card disposal and the shared dismiss helper."""
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("scenario", [
    "dispose-attached", "dispose-same-tick", "dispose-twice", "outside", "anchor", "inside",
    "escape", "window-scroll", "viewport-scroll",
])
def test_card_menu_lifecycle(scenario):
    script = r"""
const fs = require('node:fs'), vm = require('node:vm'), acorn = require('acorn');
const source = fs.readFileSync('static/js/cookbookRunning.js','utf8');
const tree = acorn.parse(source,{ecmaVersion:'latest',sourceType:'module'});
let dispose, open;
function walk(node) {
  if (!node || typeof node !== 'object') return;
  if (node.type==='FunctionDeclaration' && node.id.name==='_disposeTaskCard') dispose=node;
  if (node.type==='VariableDeclarator' && node.id.name==='_openTaskMenu') open=node.init;
  for(const value of Object.values(node)) {
    if(Array.isArray(value)) value.forEach(walk);
    else if(value && typeof value==='object') walk(value);
  }
}
walk(tree);
if(!dispose || !open) throw new Error('Missing actual Cookbook lifecycle functions');
const start=open.body.body.find(node=>node.type==='VariableDeclaration'
  && node.declarations.some(d=>['closeHandler','scrollClose'].includes(d.id.name)));
if(!start) throw new Error('Missing actual task-menu dismiss wiring');
const wiring=source.slice(start.start,open.body.end-1);
const scenario=__SCENARIO__, timers=new Map();let nextTimer=0,aborts=0,clearedIntervals=0;
function events() {
  const listeners=new Map();
  return {
    addEventListener(type,fn,capture=false) {listeners.set(fn,{type,capture})},
    removeEventListener(type,fn,capture=false) {
      const entry=listeners.get(fn);if(entry?.type===type && entry.capture===capture) listeners.delete(fn);
    },
    fire(type,event={}) {for(const [fn,entry] of [...listeners]) if(entry.type===type) fn(event)},
    count:()=>listeners.size,
  };
}
const document=events(), window=events();window.visualViewport=events();
const dropdown={isConnected:true,contains:target=>target===dropdown,remove(){this.isConnected=false}};
const menuBtn={active:true,contains:target=>target===menuBtn,
  classList:{remove(){menuBtn.active=false}}};
const el={_abort:{abort(){aborts++}},_uptimeInterval:99,_longPressTimer:100,querySelector:()=>null};
const context={document,window,_stack:[],_waveEls:new Set(),_waveTimer:null,
  setTimeout:fn=>{const id=++nextTimer;timers.set(id,fn);return id},
  clearTimeout:id=>timers.delete(id),clearInterval:()=>{clearedIntervals++}};
vm.createContext(context);
const esc=fs.readFileSync('static/js/escMenuStack.js','utf8');
for(const node of acorn.parse(esc,{ecmaVersion:'latest',sourceType:'module'}).body) {
  const fn=node.type==='ExportNamedDeclaration'?node.declaration:node;
  if(fn?.type==='FunctionDeclaration' && ['registerEscapeLayer','registerMenuDismiss','bindMenuDismiss','dismissTopEscapeLayer','_openMenuCount'].includes(fn.id.name))
    vm.runInContext(esc.slice(fn.start,fn.end),context);
}
vm.runInContext(source.slice(dispose.start,dispose.end),context);
const wire=vm.runInContext('(function(dropdown,menuBtn,el){'+wiring+'})',context);
wire(dropdown,menuBtn,el);
function flush(){for(const [id,fn] of [...timers]){timers.delete(id);fn()}}
if(scenario!=='dispose-same-tick') flush();
let anchorPreserved=null;
if(scenario.startsWith('dispose')) {
  context._disposeTaskCard(el);
  if(scenario==='dispose-twice') context._disposeTaskCard(el);
  flush();
} else if(scenario==='outside') document.fire('click',{target:{}});
else if(scenario==='anchor' || scenario==='inside') {
  document.fire('click',{target:scenario==='anchor'?menuBtn:dropdown});
  anchorPreserved=dropdown.isConnected && context._openMenuCount()===1;
  dropdown._dismiss();
} else if(scenario==='escape') context.dismissTopEscapeLayer();
else if(scenario==='window-scroll') window.fire('scroll');
else window.visualViewport.fire('scroll');
console.log(JSON.stringify({connected:dropdown.isConnected,active:menuBtn.active,
  documentListeners:document.count(),scrollListeners:window.count(),viewportListeners:window.visualViewport.count(),
  layers:context._openMenuCount(),aborts,clearedIntervals,anchorPreserved}));
""".replace("__SCENARIO__", json.dumps(scenario))
    result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, check=True)
    observed = json.loads(result.stdout)
    assert observed["connected"] is False
    assert observed["active"] is False
    assert observed["documentListeners"] == 0
    assert observed["scrollListeners"] == 0
    assert observed["viewportListeners"] == 0
    assert observed["layers"] == 0
    if scenario.startswith("dispose"):
        assert observed["aborts"] == 1
        assert observed["clearedIntervals"] == 1
    if scenario in {"anchor", "inside"}:
        assert observed["anchorPreserved"] is True
