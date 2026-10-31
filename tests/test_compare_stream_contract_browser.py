"""Compare's actual stream consumer must preserve evidence and terminal state."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

CASES = [
    ("lf", 'data: {"delta":"Hello"}\n\ndata: [DONE]\n\n', False, True, "Hello"),
    ("crlf", 'data:{"delta":"Héllö"}\r\n\r\ndata:[DONE]\r\n\r\n', False, True, "Héllö"),
    ("multiline", 'data: {"delta":\ndata: "Hello"}\n\ndata: [DONE]\n\n', False, True, "Hello"),
    ("eof", 'data: {"delta":"Partial"}\n\n', False, False, "ended before completion"),
    ("named-error", 'event: error\ndata: {"text":"Provider Z private-model failed"}\n\n', False, False, "Provider Z private-model failed"),
    ("typed-error", 'data: {"type":"error","data":{"message":"Provider Z <img src=x onerror=alert(1)>"}}\n\ndata: [DONE]\n\n', False, False, "Provider Z <img"),
    ("blind-error", 'event:error\ndata:{"message":"Provider Z private-model failed"}\n\n', True, False, "request failed"),
    ("empty-error", 'event:error\ndata:\n\n', False, False, "request failed"),
    ("done-open", 'data: {"delta":"Hello"}\n\ndata: [DONE]\n\n', False, True, "Hello"),
    ("heartbeat", 'data: {"delta":"Hello"}\n\ndata: [DONE]\n\n', False, True, "Hello"),
    ("cr", 'data: {"delta":"Hello"}\r\rdata:[DONE]\r\r', False, True, "Hello"),
    ("invalid", 'data: not-json\n\ndata: [DONE]\n\n', False, False, "Invalid model stream data"),
]


@pytest.mark.parametrize("kind,wire,blind,success,text", CASES)
def test_compare_stream_contract(kind, wire, blind, success, text):
    if not shutil.which("node"):
        pytest.skip("node not installed")
    script = r"""
const assert=require('node:assert/strict');
let chromium;try{({chromium}=require('playwright'));}catch(e){if(e.code==='MODULE_NOT_FOUND')process.exit(77);throw e;}
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage();
  await page.goto(process.env.ODYSSEUS_TEST_STATIC_ORIGIN+'/static/js/storage.js');
  await page.setContent('<div class="compare-pane" data-pane="0"><div id="cmp-badge-0"></div><div id="cmp-history-0"><div class="msg msg-ai"><div class="body"></div></div></div></div>');
  const result=await page.evaluate(async(data)=>{
   const {default:state}=await import('/static/js/compare/state.js');
   const {streamToPane}=await import('/static/js/compare/stream.js');
   Object.assign(state,{isActive:true,_selectedModels:[{model:'fixture',name:'Fixture'}],_paneSessionIds:['session'],_compareMode:'chat',_parallel:true,_blindMode:data.blind,_timeout:data.kind==='heartbeat'?1:5});
   const nativeFetch=window.fetch,bytes=new TextEncoder().encode(data.wire);let index=0,cancelled=false,context=null;
   window.fetch=async(url,options)=>{
    if(!String(url).endsWith('/api/chat_stream'))return nativeFetch(url,options);
    context=options.body.get('search_context');
    const heartbeats=[...Array(6).fill(': ping\n\n'),data.wire];
    return new Response(new ReadableStream({start(controller){options.signal.addEventListener('abort',()=>controller.error(new DOMException('Stopped','AbortError')),{once:true});},async pull(controller){if(data.kind==='heartbeat'){if(index<heartbeats.length){await new Promise(r=>setTimeout(r,200));controller.enqueue(new TextEncoder().encode(heartbeats[index++]));}else controller.close();}else if(index<bytes.length)controller.enqueue(bytes.slice(index,index+=1));else if(data.kind!=='done-open')controller.close();},cancel(){cancelled=true;}}),{headers:{'Content-Type':'text/event-stream'}});
   };
   const message=document.querySelector('.msg-ai'),spinner=document.createElement('span');let spinnerDestroyed=false;spinner.textContent='Loading';message.querySelector('.body').appendChild(spinner);message._spinner={element:spinner,destroy(){spinnerDestroyed=true;spinner.remove();}};
   await Promise.race([streamToPane(0,'session','Prompt',message,{skipBadge:true}),new Promise((_,reject)=>setTimeout(()=>reject(Error('Consumer did not finish at DONE')),data.kind==='heartbeat'?6000:1500))]);
   const pane=document.querySelector('.compare-pane');
   return {done:pane.classList.contains('is-done'),failed:pane.classList.contains('is-failed'),text:pane.innerText,images:pane.querySelectorAll('img').length,cancelled,spinnerDestroyed,spinnerOwned:!!message._spinner};
  },JSON.parse(process.argv[1]));
  const expected=JSON.parse(process.argv[1]);
  assert.equal(result.done,expected.success);assert.equal(result.failed,!expected.success);assert(result.text.includes(expected.text),JSON.stringify(result));assert.equal(result.images,0);
  assert(result.spinnerDestroyed);assert(!result.spinnerOwned);
  if(expected.blind)assert(!/Provider Z|private-model/.test(result.text),JSON.stringify(result));
  if(expected.kind==='done-open')assert(result.cancelled);
  console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    data = dict(kind=kind, wire=wire, blind=blind, success=success, text=text)
    result = subprocess.run(["node", "-e", script, json.dumps(data)], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    if result.returncode == 77:
        pytest.skip("playwright not installed")
    assert result.returncode == 0, result.stdout + result.stderr
