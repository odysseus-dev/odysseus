"""The native FileList must survive resetting the compose file input."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.helpers.document_source import function_body


@pytest.mark.parametrize("handler,uploader", [("_handleAttachUpload", "_uploadComposeFiles"), ("_handleMarkdownImageUpload", "_uploadMarkdownImages")])
@pytest.mark.parametrize("selections", [[["one.png"]], [["one.png", "two.png"]], [["one.png"], ["one.png"]], [[]]])
def test_compose_file_selection_survives_reset(tmp_path, selections, handler, uploader):
    if not shutil.which("node"):
        pytest.skip("node not installed")
    repo = Path(__file__).resolve().parents[1]
    for name, icon in [("one.png", "icon-192.png"), ("two.png", "icon-512.png")]:
        shutil.copyfile(repo / "static/icons" / icon, tmp_path / name)
    script = r"""
const fs=require('node:fs'),assert=require('node:assert/strict'),crypto=require('node:crypto');
let chromium;try{({chromium}=require('playwright'));}catch(e){if(e.code==='MODULE_NOT_FOUND')process.exit(77);throw e;}
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(process.env.ODYSSEUS_TEST_STATIC_ORIGIN+'/static/js/storage.js');
  await page.setContent('<input type="file" multiple aria-label="Compose attachments">');
  await page.addScriptTag({content:`
    window.uploads=[];window.finished=0;
    async function __UPLOADER__(files){
      for(const file of files){
        const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await file.arrayBuffer()))).map(x=>x.toString(16).padStart(2,'0')).join('');
        window.uploads.push({name:file.name,hash});
      }
    }
    __HANDLER__
    document.querySelector('input').addEventListener('change',async event=>{
      await __HANDLER_NAME__(event);window.finished++;
    });
  `});
  const selections=JSON.parse(process.argv[1]),root=process.argv[2],expected=[];
  for(const [index,names] of selections.entries()){
   await page.getByLabel('Compose attachments').setInputFiles(names.map(name=>root+'/'+name));
   await page.waitForFunction(n=>window.finished===n,index+1);
   expected.push(...names.map(name=>({name,hash:crypto.createHash('sha256').update(fs.readFileSync(root+'/'+name)).digest('hex')})));
   assert.deepEqual(await page.evaluate(()=>window.uploads),expected);
   assert.equal(await page.getByLabel('Compose attachments').inputValue(),'');
   assert.equal(await page.getByLabel('Compose attachments').evaluate(el=>el.files.length),0);
  }
  assert.deepEqual(errors,[]);console.log(JSON.stringify({files:expected.length}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
""".replace("__HANDLER__", function_body(handler)).replace("__HANDLER_NAME__", handler).replace("__UPLOADER__", uploader)
    result = subprocess.run(["node", "-e", script, json.dumps(selections), str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True,
                            text=True, timeout=30)
    if result.returncode == 77:
        pytest.skip("playwright not installed")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["files"] == sum(map(len, selections))
