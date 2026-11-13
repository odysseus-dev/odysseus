const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const {createRequire} = require('node:module');
const {chromium} = createRequire(process.cwd()+'/package.json')('playwright');
const root=process.cwd();
const server=http.createServer(async(req,res)=>{try { const file=path.join(root,decodeURIComponent(new URL(req.url,'http://localhost').pathname)); const body=await fs.readFile(file);res.setHeader('Content-Type',file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/plain');res.end(body);}catch{res.statusCode=404;res.end();}});
(async()=>{
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true});
try{
const page=await browser.newPage();
const puts=[];const errors=[];page.on('pageerror',e=>errors.push(e.message));
let holdPut=null,holdRestore=null;let failPut=false;
await page.route('**/api/**',r=>r.fulfill({json:{}}));
await page.route('**/api/document/a',async r=>{ if(r.request().method()!=='PUT')return r.fulfill({json:{}}); puts.push(r.request().postDataJSON().content);if(holdPut){const h=holdPut;holdPut=null;h.start();await h.promise;}await r.fulfill({status:failPut?500:200,json:{version_count:3}});});
const gate=()=>{let release,start;const promise=new Promise(r=>release=r);const started=new Promise(r=>start=r);return {promise,started,release,start};};
await page.route('**/api/document/a/versions',r=>r.fulfill({json:[{version_number:2,content:'latest',source:'user'},{version_number:1,content:'old',source:'user'}]}));
await page.route('**/api/document/a/restore/1',async r=>{holdRestore.start();await holdRestore.promise;await r.fulfill({json:{id:'a',title:'a',language:process.argv[2]==='rich-restore'?'richtext':'markdown',current_content:process.argv[2]==='rich-restore'?'<p>restored</p>':'restored',version_count:3}});});
await page.route('**/api/document/a/render-pages',r=>r.fulfill({json:{pages:[{page:1,width:600,height:800,fields:[]}]}}));
await page.goto('http://127.0.0.1:'+server.address().port+'/static/js/documentStats.js');
const styles=(await fs.readFile('static/index.html','utf8')).match(/<link[^>]+rel="stylesheet"[^>]*>/g).join('');
await page.setContent(styles+'<div id="toast"></div><aside id="sidebar" class="sidebar"></aside><main id="chat-container" class="chat-container"></main>');
await page.evaluate(async()=>{window.editor=await import('/static/js/document.js');editor.init('');window.pdf=await import('/static/js/document/ui/pdfTools.js');});
if(process.argv[2]==='stream-suggestions'){
await page.evaluate(async()=>{const sessions=await import('/static/js/sessions.js');sessions.setCurrentSessionId('s1');
await editor.injectFreshDoc({id:'a',title:'a',language:'markdown',session_id:'s1',current_content:'Common one Common two',version_count:2});
await editor.injectFreshDoc({id:'b',title:'b',language:'markdown',session_id:'s1',current_content:'Common one Common two',version_count:2});
await editor.loadDocument('a');
await editor.handleDocSuggestions({doc_id:'a',suggestions:[{id:'one',find:'Common one',replace:'A replacement one',reason:'Only A one'},{id:'two',find:'Common two',replace:'A replacement two',reason:'Only A two'}]});
editor.streamDocOpen('b','markdown');
});
const cardOnB=await page.locator('#doc-suggestion-active').count();
if (cardOnB) await page.locator('.doc-suggestion-dismiss').dispatchEvent('click');
const storage=await page.evaluate(()=>Object.fromEntries(Object.keys(localStorage).filter(k=>k.includes('suggestions')).map(k=>[k,localStorage.getItem(k)])));
await page.evaluate(()=>editor.loadDocument('a'));await page.evaluate(()=>editor.loadDocument('b'));
await page.waitForTimeout(300);const cardReason=await page.locator('.doc-suggestion-reason').allTextContents();
assert.equal(cardOnB,0); assert.equal(storage['odysseus-suggestions-b'],undefined); assert.deepEqual(cardReason,[]);
console.log(JSON.stringify({scenario:'stream-suggestions',cardOnB,storage,cardReason,active:await page.evaluate(()=>editor.getCurrentDocId()),errors}));
}else if(process.argv[2]==='rich-restore'){
await page.evaluate(()=>editor.injectFreshDoc({id:'a',title:'a',language:'richtext',current_content:'<p>latest</p>',version_count:2}));
await page.locator('.doc-tab-version').dispatchEvent('click');await page.locator('.doc-version-restore').waitFor();
holdRestore=gate();await page.locator('.doc-version-restore').dispatchEvent('click');await holdRestore.started;holdRestore.release();
await page.waitForFunction(()=>document.getElementById('doc-editor-textarea').value==='<p>restored</p>');
const afterRestore=await page.locator('#doc-email-richbody').innerHTML();
await page.locator('#doc-version-close').dispatchEvent('click');
await page.evaluate(()=>editor.saveDocument({silent:true}));
assert.equal(afterRestore,'<p>restored</p>'); assert.equal(puts.at(-1),'<p>restored</p>');
console.log(JSON.stringify({scenario:'rich-restore',afterRestore,puts,visible:await page.locator('#doc-email-richbody').innerHTML(),source:await page.locator('#doc-editor-textarea').inputValue(),errors}));
}else if(process.argv[2]==='session-save-failure'){
await page.route('**/api/documents/*',r=>r.fulfill({json: new URL(r.request().url()).pathname.endsWith('/s1')?[{id:'a',title:'a',language:'markdown',current_content:'stale persisted',session_id:'s1',is_active:true}]:[{id:'b',title:'b',language:'markdown',current_content:'b latest',session_id:'s2',is_active:true}]}));
await page.evaluate(()=>editor.injectFreshDoc({id:'a',title:'a',language:'markdown',current_content:'stale persisted',session_id:'s1',version_count:2}));
await page.evaluate(()=>{const ta=document.getElementById('doc-editor-textarea');ta.value='Recover me';ta.dispatchEvent(new Event('input',{bubbles:true}));});
holdPut=gate();const h=holdPut;failPut=true;
await page.evaluate(()=>{window.saving=editor.saveDocument({silent:true});});await h.started;
await page.evaluate(()=>editor.loadSessionDocs('s2'));
h.release();await page.evaluate(()=>window.saving);
await page.evaluate(()=>editor.loadSessionDocs('s1'));
assert.equal(await page.locator('#doc-editor-textarea').inputValue(),'Recover me');
console.log(JSON.stringify({scenario:'session-save-failure',puts,visible:await page.locator('#doc-editor-textarea').inputValue(),errors}));
}else if(process.argv[2]==='restore-save'){
await page.evaluate(()=>editor.injectFreshDoc({id:'a',title:'a',language:'markdown',current_content:'latest',version_count:2}));
await page.locator('.doc-tab-version').dispatchEvent('click');await page.locator('.doc-version-restore').waitFor();
holdRestore=gate();await page.locator('.doc-version-restore').dispatchEvent('click');await holdRestore.started;
await page.evaluate(()=>{window.saving=editor.saveDocument({silent:true});});
holdRestore.release();await page.evaluate(()=>window.saving);await page.waitForTimeout(100);
assert.equal(puts.at(-1),'restored'); assert.equal(await page.locator('#doc-editor-textarea').inputValue(),'restored');
console.log(JSON.stringify({scenario:'restore-save',puts,visible:await page.locator('#doc-editor-textarea').inputValue(),saveState:await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state'),errors}));
}else{
await page.evaluate(()=>editor.injectFreshDoc({id:'a',title:'a',language:'markdown',current_content:'<!-- pdf_source upload_id="source" -->\n\n## Annotations\n\n- Initial <!-- annotation id=a page=1 x=10 y=10 w=20 h=5 kind=text -->\n',version_count:2}));
await page.evaluate(()=>pdf._setPdfViewActive(true));await page.locator('.pdf-annotation-wrap textarea').waitFor();
if(process.argv[2]==='undo-normal') {
await page.locator('.pdf-annotation-wrap textarea').fill('Changed before undo');
await page.evaluate(()=>pdf._undoPdfPaneAction('a'));
await page.locator('.pdf-annotation-wrap textarea').waitFor();
assert.equal(await page.locator('.pdf-annotation-wrap textarea').inputValue(),'Initial');
console.log(JSON.stringify({scenario:'undo-normal',puts,errors}));
} else {
await page.evaluate(()=>{pdf._pushPdfUndoSnapshot('a');const ann=pdf._pdfPaneAnnotationsByDoc.get('a')[0];ann.el.value='Before undo';pdf._syncPdfPaneToDoc('a');});
holdPut=gate();const h=holdPut;
await page.evaluate(()=>{window.undoing=pdf._undoPdfPaneAction('a');});await h.started;
await page.locator('.pdf-annotation-wrap textarea').fill('Newer edit while undo pending');
h.release();await page.evaluate(()=>window.undoing);await page.waitForTimeout(1000);
assert((await page.locator('#doc-editor-textarea').inputValue()).includes('Newer edit while undo pending')); assert(puts.at(-1).includes('Newer edit while undo pending')); assert.equal(await page.locator('.pdf-annotation-wrap textarea').inputValue(),'Newer edit while undo pending');
console.log(JSON.stringify({scenario:'undo-edit',puts,visible:await page.locator('.pdf-annotation-wrap textarea').allTextContents(),pane:await page.locator('#doc-pdf-view').innerHTML(),source:await page.locator('#doc-editor-textarea').inputValue(),errors}));
}
}
assert.deepEqual(errors,[]);
}finally{await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exitCode=1;server.close();});
