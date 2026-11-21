"""Exercise the real broadcast/stop functions with controlled pane streams."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.helpers.js_modules import js_function_source

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("scenario", ["complete", "stop-first", "stop-second", "stop-retry", "individual", "parallel", "reset", "presearch"])
def test_compare_broadcast_cancellation(scenario):
    if not shutil.which("node"):
        pytest.skip("node not installed")
    sources = {}
    for name, file in [("_executeCompare", "index.js"), ("stopAll", "panes.js"), ("stopPane", "panes.js")]:
        sources[name] = js_function_source(name, (ROOT / "static/js/compare" / file).read_text())
    script = r"""
const assert=require('node:assert/strict');
let chromium;try{({chromium}=require('playwright'));}catch(e){if(e.code==='MODULE_NOT_FOUND')process.exit(77);throw e;}
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage();
  await page.goto(process.env.ODYSSEUS_TEST_STATIC_ORIGIN+'/static/js/storage.js');
  await page.setContent('<input id="message"><input type="checkbox" id="web-toggle">'+[0,1].map(i=>`<div class="compare-pane" data-pane="${i}"><div id="cmp-history-${i}"></div></div>`).join(''));
  const result=await page.evaluate(async({sources,scenario})=>{
   const {default:state,reset}=await import('/static/js/compare/state.js');
   Object.assign(state,{isActive:true,_selectedModels:[{name:'One'},{name:'Two'}],_paneSessionIds:['one','two'],_compareMode:'chat',_parallel:scenario==='parallel',_continueChat:false});
   const calls=[],pending=[];let automatic=scenario==='complete';
   const _setSendBtn=()=>{},buildVoteBar=()=>{},_slotChar=i=>String(i+1),escapeHtml=s=>s;
   const uiModule={showError(message){throw Error(message);}};
   const spinnerModule={create(label){let node;return {createElement(){node=document.createElement('span');node.textContent=label;return node;},start(){},updateLabel(text){node.textContent=text;},destroy(){node.remove();}};}};
   const settle=async()=>{for(let n=0;n<10;n++)await Promise.resolve();};
   const streamToPane=(i,sid,message,element)=>{
    calls.push({i,message});const controller=new AbortController();state._abortControllers[i]=controller;
    return new Promise(resolve=>{
     const finish=()=>{
      if(state._abortControllers[i]===controller)state._abortControllers[i]=null;
      if(!controller.signal.aborted){element._spinner?.destroy();element._spinner=null;element.querySelector('.body').textContent='Completed';document.querySelector(`[data-pane="${i}"]`).classList.add('is-done');}
      resolve();
     };
     if(automatic||(scenario==='stop-second'&&i===0))finish();
     else pending.push({finish,controller,done:false});
    });
   };
   let searchReject=null;
   const fetch=(url,options)=>new Promise((resolve,reject)=>{searchReject=()=>reject(new DOMException('Stopped','AbortError'));options.signal?.addEventListener('abort',searchReject,{once:true});});
   eval(sources.stopPane+';'+sources.stopAll+';'+sources._executeCompare+';window.runBroadcast=_executeCompare;window.stopBroadcast=stopAll;window.stopOne=stopPane;');
   if(scenario==='presearch')document.getElementById('web-toggle').checked=true;
   const run=window.runBroadcast('original');await settle();
   if(scenario==='complete'){await run;return {calls,busy:state._streaming};}
   if(scenario==='presearch'){window.stopBroadcast();searchReject();}
   else if(scenario==='individual')window.stopOne(0);
   else if(scenario==='reset'){reset();state._paneSessionIds=['replacement-one','replacement-two'];}
   else window.stopBroadcast();
   if(scenario==='stop-retry'){
    window.runBroadcast('too-early');await settle();
    if(calls.length!==1)throw Error('A replacement broadcast started before cancellation settled');
   }
   if(scenario==='stop-second'&&!document.querySelector('[data-pane="0"]').classList.contains('is-done'))throw Error('Stop changed a completed pane');
   for(let round=0;round<3;round++){
    for(const item of pending)if(!item.done){item.done=true;item.finish();}
    await settle();
   }
   await run;
   if(scenario==='stop-retry'){automatic=true;await window.runBroadcast('replacement');}
   return {calls,busy:state._streaming,texts:[...document.querySelectorAll('.msg-ai .body')].map(el=>el.textContent)};
  },{sources:JSON.parse(process.argv[1]),scenario:process.argv[2]});
  const scenario=process.argv[2],expected={complete:[0,1],'stop-first':[0],'stop-second':[0,1],'stop-retry':[0,0,1],individual:[0,1],parallel:[0,1],reset:[0],presearch:[]}[scenario];
  assert.deepEqual(result.calls.map(x=>x.i),expected);assert.equal(result.busy,false);
  if(['stop-first','parallel'].includes(scenario))assert(result.texts.includes('Stopped'));
  console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run(["node", "-e", script, json.dumps(sources), scenario], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    if result.returncode == 77:
        pytest.skip("playwright not installed")
    assert result.returncode == 0, result.stdout + result.stderr
