"""The real Skills module must leave test results before opening its editor."""
import json
import os
from pathlib import Path
import shutil
import subprocess

from bs4 import BeautifulSoup
import pytest


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("mode", ["done", "running", "source"])
def test_skill_editor_transitions(tmp_path, width, mode):
    if not shutil.which("node"):
        pytest.skip("node not installed")
    repo = Path(__file__).resolve().parents[1]
    index = BeautifulSoup((repo / "static/index.html").read_text(), "html.parser")
    modal = index.select_one("#memory-modal")
    modal["class"] = [value for value in modal.get("class", []) if value != "hidden"]
    for panel in modal.select("[data-memory-panel]"):
        classes = set(panel.get("class", []))
        if panel["data-memory-panel"] == "skills":
            classes.discard("hidden")
        else:
            classes.add("hidden")
        panel["class"] = sorted(classes)
    styles = "".join(str(node) for node in index.select('link[rel="stylesheet"]'))
    document = tmp_path / "skills.html"
    document.write_text(styles + str(modal) + str(index.select_one("#toast")), encoding="utf-8")
    script = r"""
const fs=require('node:fs'), assert=require('node:assert/strict');
let chromium;
try { ({chromium}=require('playwright')); }
catch(e) { if(e.code==='MODULE_NOT_FOUND')process.exit(77); throw e; }
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const origin=process.env.ODYSSEUS_TEST_STATIC_ORIGIN, mode=process.argv[3];
  const page=await browser.newPage({viewport:{width:Number(process.argv[2]),height:900}});
  page.setDefaultTimeout(4000);
  const source='---\nname: fixture-editor\nversion: "1.0"\n---\n\n## Procedure\nOriginal source';
  let markdown=source,posts=0,statusCalls=0;
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route(origin+'/api/**',async route=>{
   const request=route.request(),path=new URL(request.url()).pathname;
   let data={};
   if(path==='/api/skills')data={skills:[{name:'fixture-editor',description:'Editor fixture',status:'draft',source:'user',confidence:0.9,procedure:['Original source']}]};
   else if(path.endsWith('/markdown')){
    if(request.method()==='POST'){posts++;markdown=request.postDataJSON().markdown;data={ok:true};}
    else data={markdown};
   }else if(path.endsWith('/test-status')){
    statusCalls++;
    const status=mode==='running'&&statusCalls===1?'running':'done';
    data={status,log:[{type:'say',text:'Completed fixture output'}],verdict:status==='done'?{verdict:'pass',confidence:0.95,summary:'Fixture verdict',issues:[]}:null};
   }else if(path.endsWith('/audit-all/status'))data={status:'none'};
   await route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto(origin+'/static/js/storage.js');
  await page.setContent(fs.readFileSync(process.argv[1],'utf8'));
  await page.evaluate(async()=>{const skills=await import('/static/js/skills.js');await skills.loadSkills();});
  const card=page.locator('.skill-card[data-skill-name="fixture-editor"]');
  await card.locator('.skill-card-name').click();
  if(mode!=='source'){
   await card.getByRole('button',{name:'Test',exact:true}).click();
   if(mode==='running'){
    await card.getByText('…running (you can close this — it keeps going)',{exact:true}).waitFor();
    await card.locator('.skill-chevron-up').click();
    await card.locator('.skill-kebab-btn').click();
    await page.locator('.skill-kebab-menu:visible').getByRole('button',{name:'Edit',exact:true}).click();
   }else{
    await card.getByText('Fixture verdict',{exact:true}).waitFor();
    await card.locator('[data-act="edit"]').click();
   }
  }else await card.getByRole('button',{name:'Edit',exact:true}).click();
  const editor=card.locator('.skill-md-editor');await editor.waitFor({state:'visible'});
  assert.equal(await editor.inputValue(),source);
  assert.equal(await card.locator('.skill-test').count(),0);
  assert(await card.locator('.doclib-card-expanded-actions button:visible').evaluateAll(buttons=>buttons.every(button=>{
    const bounds=button.getBoundingClientRect(),card=button.closest('.skill-card').getBoundingClientRect();
    return bounds.left>=card.left&&bounds.right<=card.right;
  })), 'Editor actions must fit inside the card');
  await editor.fill(source+'\nCancelled draft');
  if(mode==='running'){
   await page.waitForFunction(()=>document.querySelector('.skill-card')?._testPoll===null);
   assert.equal(await editor.inputValue(),source+'\nCancelled draft');
  }
  await card.getByRole('button',{name:'Cancel',exact:true}).click();
  assert.equal(await card.locator('.skill-md-editor').count(),0);
  assert.equal(posts,0);assert.equal(markdown,source);
  assert.equal(await card.locator('.skill-md-pre').innerText(),source);
  if(mode!=='source'){
   await card.getByRole('button',{name:'Test',exact:true}).click();
   await card.getByText('Fixture verdict',{exact:true}).waitFor();
   await card.locator('[data-act="edit"]').click();
  }else await card.getByRole('button',{name:'Edit',exact:true}).click();
  await editor.fill(source+'\nSaved change');
  await card.getByRole('button',{name:'Save',exact:true}).click();
  await page.waitForFunction(()=>!document.querySelector('.skill-md-editor'));
  assert.equal(posts,1);assert.equal(markdown,source+'\nSaved change');
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({mode,width:process.argv[2],posts}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run(["node", "-e", script, str(document), str(width), mode],
                            cwd=repo, env=os.environ.copy(), capture_output=True,
                            text=True, timeout=45)
    if result.returncode == 77:
        pytest.skip("playwright not installed")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["posts"] == 1
