"""An unloaded or failed SKILL.md must never become editable placeholder text."""
import json
import os
from pathlib import Path
import shutil
import subprocess

from bs4 import BeautifulSoup
import pytest


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("mode", ["delayed", "error", "menu-error", "empty", "collapse"])
def test_skill_editor_waits_for_source(tmp_path, width, mode):
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
const fs=require('node:fs'),assert=require('node:assert/strict');
let chromium;try{({chromium}=require('playwright'));}catch(e){if(e.code==='MODULE_NOT_FOUND')process.exit(77);throw e;}
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const origin=process.env.ODYSSEUS_TEST_STATIC_ORIGIN,mode=process.argv[3];
  const page=await browser.newPage({viewport:{width:Number(process.argv[2]),height:900}});page.setDefaultTimeout(5000);
  const original=mode==='empty'?'':'---\nname: fixture-loading\n---\n\n## Procedure\nOriginal source';
  let source=original,posts=0,gets=0,release;const gate=new Promise(r=>release=r),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route(origin+'/api/**',async route=>{
   const request=route.request(),path=new URL(request.url()).pathname;let data={};
   if(path==='/api/skills')data={skills:[{name:'fixture-loading',description:'Lazy loading fixture',status:'draft',source:'user',confidence:0.9}]};
   else if(path.endsWith('/markdown')){
    if(request.method()==='POST'){posts++;source=request.postDataJSON().markdown;data={ok:true};}
    else{
     gets++;if(gets===1)await gate;
     if((mode==='error'&&gets===1)||(mode==='menu-error'&&gets<=2)){
      await route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({detail:'Fixture read failed'})});return;
     }
     data={markdown:source};
    }
   }else if(path.endsWith('/audit-all/status'))data={status:'none'};
   await route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto(origin+'/static/js/storage.js');await page.setContent(fs.readFileSync(process.argv[1],'utf8'));
  await page.evaluate(async()=>{const skills=await import('/static/js/skills.js');await skills.loadSkills();});
  const card=page.locator('.skill-card[data-skill-name="fixture-loading"]');await card.locator('.skill-card-name').click();
  await card.getByText('Loading…',{exact:true}).waitFor();
  const edit=card.getByRole('button',{name:'Edit',exact:true});assert(await edit.isDisabled(),'Edit must stay disabled while source is pending');
  assert.equal(await card.locator('.skill-md-editor').count(),0);assert.equal(posts,0);
  if(mode==='collapse')await card.locator('.skill-chevron-up').click();
  release();
  if(mode==='error'||mode==='menu-error'){
   await card.getByText('Failed to load SKILL.md: Fixture read failed',{exact:true}).waitFor();
   assert(await edit.isDisabled(),'Failed source must not be editable');
   assert.equal(await card.locator('.skill-md-editor').count(),0);assert.equal(posts,0);
   await card.locator('.skill-chevron-up').click();
   if(mode==='menu-error'){
    await card.locator('.skill-kebab-btn').click();
    await page.locator('.skill-kebab-menu:visible').getByRole('button',{name:'Edit',exact:true}).click();
    await card.getByText('Failed to load SKILL.md: Fixture read failed',{exact:true}).waitFor();
    assert.equal(await card.locator('.skill-md-editor').count(),0);
    assert(await edit.isDisabled());assert.equal(posts,0);
    await card.locator('.skill-chevron-up').click();
   }
   await card.locator('.skill-card-name').click();
  }else if(mode==='collapse'){
   await page.waitForFunction(()=>document.querySelector('.skill-card')?._mdLoaded);
   assert.equal(await card.locator('.skill-md-editor').count(),0);
   assert(!(await card.getAttribute('class')).includes('doclib-card-expanded'));
   await card.locator('.skill-card-name').click();
  }
  await page.waitForFunction(()=>document.querySelector('.skill-card')?._mdLoaded);
  assert(await edit.isEnabled());await edit.click();const editor=card.locator('.skill-md-editor');await editor.waitFor({state:'visible'});assert.equal(await editor.inputValue(),original);
  await editor.fill(original+'\nSaved real source');await card.getByRole('button',{name:'Save',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('.skill-md-editor'));
  assert.equal(posts,1);assert.equal(source,original+'\nSaved real source');assert.deepEqual(errors,[]);
  console.log(JSON.stringify({mode,width:process.argv[2],gets,posts}));
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
