"""Raw skill source needs a usable editor height without hiding its footer."""
import json
import os
from pathlib import Path
import shutil
import subprocess

from bs4 import BeautifulSoup
import pytest


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("lines", [8, 40, 400])
def test_skill_editor_source_height(tmp_path, width, lines):
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
let chromium;
try{({chromium}=require('playwright'));}catch(e){if(e.code==='MODULE_NOT_FOUND')process.exit(77);throw e;}
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const origin=process.env.ODYSSEUS_TEST_STATIC_ORIGIN,lines=Number(process.argv[3]);
  const page=await browser.newPage({viewport:{width:Number(process.argv[2]),height:900}});page.setDefaultTimeout(5000);
  let source=Array.from({length:lines},(_,i)=>'Line '+(i+1)+' of synthetic skill source').join('\n'),posts=0;
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route(origin+'/api/**',async route=>{
   const request=route.request(),path=new URL(request.url()).pathname;let data={};
   if(path==='/api/skills')data={skills:[{name:'fixture-height',description:'Source height fixture',status:'draft',source:'user',confidence:0.9}]};
   else if(path.endsWith('/markdown')){
    if(request.method()==='POST'){posts++;source=request.postDataJSON().markdown;data={ok:true};}else data={markdown:source};
   }else if(path.endsWith('/audit-all/status'))data={status:'none'};
   await route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto(origin+'/static/js/storage.js');await page.setContent(fs.readFileSync(process.argv[1],'utf8'));
  await page.evaluate(async()=>{const skills=await import('/static/js/skills.js');await skills.loadSkills();});
  const card=page.locator('.skill-card[data-skill-name="fixture-height"]');await card.locator('.skill-card-name').click();
  await card.getByRole('button',{name:'Edit',exact:true}).click();const editor=card.locator('.skill-md-editor');await editor.waitFor({state:'visible'});
  const geometry=await editor.evaluate(el=>{
   const box=el.getBoundingClientRect(),lineHeight=parseFloat(getComputedStyle(el).lineHeight);
   const modal=el.closest('.modal-content').getBoundingClientRect();
   return {height:box.height,visibleLines:el.clientHeight/lineHeight,modalBottom:modal.bottom,viewportHeight:innerHeight};
  });
  assert(geometry.visibleLines>=Math.min(lines,8),JSON.stringify(geometry));
  const save=card.getByRole('button',{name:'Save',exact:true}),bounds=await save.boundingBox();
  assert(bounds.y>=0&&bounds.y+bounds.height<=geometry.viewportHeight,JSON.stringify({geometry,bounds}));
  assert(geometry.modalBottom<=geometry.viewportHeight+1,JSON.stringify(geometry));
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await editor.fill(source+'\nSaved final line');await save.click();await page.waitForFunction(()=>!document.querySelector('.skill-md-editor'));
  assert.equal(posts,1);assert(source.endsWith('Saved final line'));assert.deepEqual(errors,[]);
  console.log(JSON.stringify({width:process.argv[2],lines,geometry,posts}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run(["node", "-e", script, str(document), str(width), str(lines)],
                            cwd=repo, env=os.environ.copy(), capture_output=True,
                            text=True, timeout=45)
    if result.returncode == 77:
        pytest.skip("playwright not installed")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["posts"] == 1
