"""Run the shipped Group picker/render callbacks with explicit DOM/metadata ports."""
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("scenario", [
    "commit-first", "commit-last", "blank-pending", "remove-with-pending",
    "single-commit", "remove-empty", "preset", "max-eight",
])
def test_pending_participants_survive_group_edits(scenario):
    script = r"""
const fs=require('node:fs'),vm=require('node:vm'),acorn=require('acorn'),assert=require('node:assert/strict');
const source=fs.readFileSync('static/js/group.js','utf8');
let render,add,preset;
function walk(node){
 if(!node||typeof node!=='object')return;
 if(node.type==='FunctionDeclaration'&&node.id.name==='_render')render=node;
 if(node.type==='CallExpression'&&node.callee?.property?.name==='addEventListener'&&node.arguments[0]?.value==='click'){
  if(node.callee.object?.name==='addBtn')add=node.arguments[1];
  if(node.callee.object?.name==='chip')preset=node.arguments[1];
 }
 for(const value of Object.values(node)){
  if(Array.isArray(value))value.forEach(walk);else if(value&&typeof value==='object')walk(value);
 }
}
walk(acorn.parse(source,{ecmaVersion:'latest',sourceType:'module'}));
if(!render||!add||!preset)throw new Error('Missing actual Group callbacks');
class Element{
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.style={};this.events={};this.value='';this.parentNode=null;this._html='';}
 appendChild(child){child.remove();this.children.push(child);child.parentNode=this;return child;}
 remove(){if(this.parentNode){const a=this.parentNode.children;a.splice(a.indexOf(this),1);this.parentNode=null;}}
 set innerHTML(html){for(const child of this.children)child.parentNode=null;this.children=[];this._html=html;this.button=null;}
 get innerHTML(){return this._html;}
 addEventListener(type,fn){this.events[type]=fn;}
 async fire(type){return this.events[type]?.({stopPropagation(){}});}
 querySelector(selector){
  if(selector==='button'&&this._html.includes('<button'))return this.button||(this.button=new Element('button'));
  throw new Error('Unexpected DOM port selector '+selector);
 }
 querySelectorAll(selector){
  if(selector==='[data-group-picker]')return this.children.filter(c=>c.dataset.groupPicker);
  throw new Error('Unexpected DOM port selector '+selector);
 }
}
const scenario=__SCENARIO__,participantsEl=new Element('div'),toasts=[];
const models=[{mid:'model-a',display:'Model A',url:'http://owned.invalid/a'},{mid:'model-b',display:'Model B',url:'http://owned.invalid/b'}];
const chars=[{id:'char-a',name:'Persona A'},{id:'char-b',name:'Persona B'}];
const context={participantsEl,_groupParticipants:[],_mode:'parallel',
 document:{createElement:tag=>new Element(tag)},
 _getModels:async()=>models,_getCharacterList:async()=>chars,
 uiModule:{esc:x=>x,showToast:x=>toasts.push(x)},
 g:{mode:'round-robin',participants:[{modelId:'model-b',characterId:'char-b'},{modelId:'model-a'}]},
};
vm.createContext(context);vm.runInContext(source.slice(render.start,render.end),context);
const addPicker=vm.runInContext('('+source.slice(add.start,add.end)+')',context);
const loadPreset=vm.runInContext('('+source.slice(preset.start,preset.end)+')',context);
const pending=()=>participantsEl.children.filter(row=>row.children.some(c=>c.dataset.selectionType==='model'));
const char=row=>row.children.find(c=>c.dataset.selectionType==='character');
const model=row=>row.children.find(c=>c.dataset.selectionType==='model');
async function append(){await addPicker();return pending().at(-1);}
async function commit(row,mid){model(row).value=mid;await model(row).fire('change');}
function removeFirst(){return participantsEl.children.find(row=>row.button).button.fire('click');}
(async()=>{
 if(['commit-first','commit-last','blank-pending'].includes(scenario)){
  const rows=[await append(),await append(),await append()];
  rows.forEach((row,i)=>char(row).value=scenario==='blank-pending'&&i>0?'':chars[i%2].id);
  const index=scenario==='commit-last'?2:0,remaining=rows.filter((_,i)=>i!==index);
  const values=remaining.map(row=>char(row).value);
  await commit(rows[index],'model-a');
  assert.equal(pending().length,2);remaining.forEach(row=>assert.equal(row.parentNode,participantsEl));
  assert.deepEqual(pending().map(row=>char(row).value),values);
  for(const row of remaining)await commit(row,'model-b');
  assert.equal(context._groupParticipants.length,3);assert.equal(pending().length,0);
 }else if(scenario==='remove-with-pending'){
  await commit(await append(),'model-a');const a=await append(),b=await append();char(a).value='char-a';char(b).value='char-b';
  await removeFirst();assert.equal(context._groupParticipants.length,0);assert.deepEqual(pending(),[a,b]);
  assert.deepEqual(pending().map(row=>char(row).value),['char-a','char-b']);
 }else if(scenario==='single-commit'){
  const row=await append();char(row).value='char-b';await commit(row,'model-a');
  assert.equal(pending().length,0);assert.equal(context._groupParticipants[0].character.id,'char-b');
 }else if(scenario==='remove-empty'){
  await commit(await append(),'model-a');await removeFirst();assert.equal(context._groupParticipants.length,0);assert.equal(participantsEl.children.length,0);
 }else if(scenario==='preset'){
  await commit(await append(),'model-a');await append();await append();await loadPreset();
  assert.equal(pending().length,0);assert.deepEqual(Array.from(context._groupParticipants,p=>p.model.mid),['model-b','model-a']);
  assert.equal(context._groupParticipants[0].character.id,'char-b');assert.equal(context._mode,'round-robin');
 }else{
  for(let i=0;i<8;i++)context._groupParticipants.push({model:models[0],character:null});context._render();
  const row=await append();await commit(row,'model-b');assert.equal(context._groupParticipants.length,8);
  assert.deepEqual(pending(),[row]);assert.deepEqual(toasts,['Max 8']);
 }
 console.log(JSON.stringify({scenario,verified:true}));
})().catch(error=>{console.error(error);process.exitCode=1});
""".replace("__SCENARIO__", json.dumps(scenario))
    result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["verified"] is True
