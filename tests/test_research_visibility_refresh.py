"""Foreground refresh uses the real queue module and a native EventTarget."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _scenario(name):
    if not shutil.which("node"):
        pytest.skip("node is required to execute the research queue")
    script = r"""
const fs=require('fs'), vm=require('vm');
const name=process.argv[1], doc=new EventTarget();doc.hidden=false;
const calls=[], timers=new Map(), storage=new Map([['odysseus-research-dismissed','["dismissed"]']]);
let nextTimer=1, items=[], hold=false, fail=false, release;
const response=url=>({ok:true,json:async()=>url.includes('/active')?{active:[]}:{research:items}});
const context={document:doc, Event, localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},
 setInterval:fn=>{const id=nextTimer++;timers.set(id,fn);return id},clearInterval:id=>timers.delete(id),
 fetch:async url=>{calls.push(url);if(fail){fail=false;throw new Error('scripted transport failure')}
  if(hold){hold=false;return new Promise(resolve=>{release=()=>resolve(response(url))})}return response(url)},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('static/js/research/jobs.js','utf8').replace(/^export /gm,''),context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const event=()=>doc.dispatchEvent(new Event('visibilitychange'));
(async()=>{
 vm.runInContext("init('')",context);await flush();await flush();
 if(name==='repeat'){vm.runInContext("init('')",context);await flush();await flush();}
 calls.length=0;
 items=[{id:'fresh',status:'done',query:'New report'},{id:'dismissed',status:'done',query:'Dismissed report'}];
 if(name==='hidden'){doc.hidden=true;event();for(const fn of timers.values())fn();}
 else if(name==='periodic'){for(const fn of timers.values())fn();}
 else if(name==='inflight'){hold=true;event();event();if(release)release();}
 else if(name==='failure'){fail=true;event();await flush();event();}
 else {doc.hidden=true;event();doc.hidden=false;event();}
 await flush();await flush();
 console.log(JSON.stringify({calls,ids:vm.runInContext('getJobs().map(j=>j.id)',context),timers:timers.size}));
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run(["node", "-e", script, name], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("scenario", ["visible", "repeat", "inflight"])
def test_visible_transition_refreshes_active_and_recent_library_once(scenario):
    result = _scenario(scenario)
    assert result["calls"] == ["/api/research/active", "/api/research/library?sort=recent&limit=20"]
    # Fresh data bypasses the ordinary two-minute library throttle, while
    # user-dismissed results remain hidden after the refresh.
    assert result["ids"] == ["fresh"]
    assert result["timers"] == 1


def test_hidden_transition_and_hidden_timer_do_not_poll():
    result = _scenario("hidden")
    assert result["calls"] == []
    assert result["ids"] == []


def test_periodic_visible_poll_keeps_existing_lightweight_behavior():
    result = _scenario("periodic")
    assert result["calls"] == ["/api/research/active"]
    assert result["ids"] == []


def test_failed_foreground_request_does_not_block_the_next_refresh():
    result = _scenario("failure")
    assert result["calls"] == ["/api/research/active", "/api/research/active",
                               "/api/research/library?sort=recent&limit=20"]
    assert result["ids"] == ["fresh"]
