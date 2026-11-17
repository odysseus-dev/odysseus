import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { chromium } from 'playwright';
const origin = process.env.ODYSSEUS_TEST_STATIC_ORIGIN;
assert.ok(origin, 'pytest static fixture must set ODYSSEUS_TEST_STATIC_ORIGIN');
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  if (process.env.ODYSSEUS_DOCUMENT_CONTROL_REVISION) {
    for (const asset of ['document.js', 'document/ui/pdfTools.js']) {
      const body = execFileSync('git', ['show', `${process.env.ODYSSEUS_DOCUMENT_CONTROL_REVISION}:static/js/${asset}`], { encoding: 'utf8' });
      await page.route(`**/static/js/${asset}*`, route => route.fulfill({ contentType: 'application/javascript', body }));
    }
  }
  page.setDefaultTimeout(7000);
  const pageErrors=[];
  page.on('pageerror',error=>pageErrors.push(error.message));
  const styles=(await fs.readFile('static/index.html','utf8')).match(/<link[^>]+rel="stylesheet"[^>]*>/g).join('');
  let holdNextRender=null;
  let nextRenderStarted=null;
  let failNextSave = false;
  let holdNextPut=null;
  const puts = [];
  await page.route('**/api/document/pdf-remediation', async route => {
    if (route.request().method() !== 'PUT') return route.continue();
    const body = route.request().postDataJSON(); puts.push(body.content);
    const gate=holdNextPut; holdNextPut=null;
    if(gate){gate.started();await gate.promise;}
    if (failNextSave) { failNextSave=false; return route.fulfill({status:500,contentType:'application/json',body:'{"error":"retry me"}'}); }
    await route.fulfill({contentType:'application/json',body:'{"version_count":2}'});
  });
  let renderRequests=0;
  await page.route('**/api/document/pdf-remediation/render-pages', async route => {
    renderRequests++;
    const release=holdNextRender;
    holdNextRender=null;
    if (release) { nextRenderStarted?.(); await new Promise(resolve => { release.resolve=resolve; }); }
    await route.fulfill({contentType:'application/json',body:JSON.stringify({pages:[{page:1,width:600,height:800,fields:[{name:'Name',type:'text',rect_px:[40,40,220,80],value:'Ada'}]}]})});
  });
  await page.goto(`${origin}/static/js/documentStats.js`);
  await page.setContent(`${styles}<div id="toast"></div><aside id="sidebar" class="sidebar"></aside><main id="chat-container" class="chat-container"></main>`);
  await page.evaluate(async () => {
    const mod=await import('/static/js/document.js?v=20260831richtexttools91&pdf-remediation=1');
    mod.init('/api');
    mod.injectFreshDoc({id:'pdf-remediation',title:'PDF remediation',language:'markdown',current_content:'<!-- pdf_source upload_id="source" -->\n\n- Name:** Ada <!-- field=Name type=text -->\n\n## Annotations\n\n- Keep me <!-- annotation id=a page=1 x=10 y=10 w=20 h=5 kind=text -->\n',version_count:1});
    window.editor=mod;
    window.pdf=await import('/static/js/document/ui/pdfTools.js');
  });
  const setPdfView=active=>page.evaluate(active=>window.pdf._setPdfViewActive(active),active);
  await setPdfView(true);
  await page.locator('.pdf-annotation-wrap').waitFor();
  await setPdfView(false);
  const sourceSave=page.waitForRequest(request=>request.method()==='PUT'&&/annotation id=a/.test(request.postDataJSON()?.content||''));
  await page.locator('#doc-editor-textarea').press('Control+s');
  assert.match((await sourceSave).postDataJSON().content,/annotation id=a/,'source-mode save preserves persisted annotation');

  let started;
  const renderStarted = new Promise(resolve => { started=resolve; });
  nextRenderStarted=started;
  const renderGate=holdNextRender = {};
  page.evaluate(()=>{ void window.pdf._setPdfViewActive(true); });
  await renderStarted;
  await page.evaluate(()=>{
    const editor=document.getElementById('doc-editor-textarea');
    editor.value += '\nnewer draft while PDF pages load';
    editor.dispatchEvent(new Event('input',{bubbles:true}));
  });
  await setPdfView(false);
  const pendingSourceSave=page.waitForRequest(request=>request.method()==='PUT'&&/annotation id=a/.test(request.postDataJSON()?.content||''));
  await page.locator('#doc-editor-textarea').press('Control+s');
  assert.match((await pendingSourceSave).postDataJSON().content,/annotation id=a/,'save while render is pending preserves annotations');
  const staleResponse=page.waitForResponse(response=>response.url().includes('/render-pages'));
  renderGate.resolve?.();
  await staleResponse;
  assert.equal(await page.locator('#doc-pdf-view').getAttribute('data-rendered-doc-id'),null,'late render for an older content revision is ignored');

  await page.evaluate(()=>window.editor.closePanel('down'));
  await page.locator('#doc-editor-pane').waitFor({state:'detached'});
  await page.evaluate(()=>window.editor.loadDocument('pdf-remediation'));
  await page.locator('#doc-editor-textarea').waitFor();

  await setPdfView(true);
  await page.locator('.pdf-annotation-wrap').waitFor();

  failNextSave=true;
  const field=page.locator('[data-field-name="Name"]');
  const failedSave=page.waitForResponse(response=>response.request().method()==='PUT'&&response.status()===500);
  await field.fill('Grace');
  await failedSave;
  assert.match(puts.at(-1) || '', /Grace/, 'first save attempt contains the draft that failed');
  const retriedSave=page.waitForResponse(response=>response.request().method()==='PUT'&&response.status()===200);
  await field.fill('Grace Hopper');
  await retriedSave;
  assert.match(puts.at(-1) || '', /Grace Hopper/, 'retry persists latest pane draft');

  failNextSave=true;
  const failedUndo=page.waitForResponse(response=>response.request().method()==='PUT'&&response.status()===500);
  const undoFailed=await page.evaluate(async () => {
    const pdf=window.pdf;
    const id='pdf-remediation';
    const ann=pdf._pdfPaneAnnotationsByDoc.get(id)?.[0];
    if (!ann) return false;
    pdf._pushPdfUndoSnapshot(id);
    ann.el.value='Changed annotation';
    return await pdf._undoPdfPaneAction(id);
  });
  await failedUndo;
  assert.equal(undoFailed,true,'failed PDF undo is consumed for native undo but retained for retry');
  assert.equal(await page.evaluate(()=>window.pdf._pdfUndoStackByDoc.get('pdf-remediation')?.length),1,'failed undo snapshot remains available');
  const undoRetry=page.waitForResponse(response=>response.request().method()==='PUT'&&response.status()===200);
  const undoSaved=await page.evaluate(()=>window.pdf._undoPdfPaneAction('pdf-remediation'));
  await undoRetry;
  assert.equal(undoSaved, true, 'PDF undo is consumed and saved');
  assert.match(puts.at(-1) || '', /Keep me/, 'undo restores and persists the annotation snapshot');

  await page.evaluate(()=>window.pdf._renderPdfPane('pdf-remediation'));
  await page.locator('.pdf-annotation-wrap textarea').waitFor({timeout:5000});
  assert.equal(await page.locator('.pdf-annotation-wrap textarea').inputValue(),'Keep me');

  let gateStarted;
  const undoGateStarted=new Promise(resolve=>{gateStarted=resolve;});
  let releaseUndoGate;
  const undoGate={promise:new Promise(resolve=>{releaseUndoGate=resolve;}),started:gateStarted,resolve:()=>releaseUndoGate()};
  holdNextPut=undoGate;
  await page.evaluate(()=>{
    window.pdf._pushPdfUndoSnapshot('pdf-remediation');
    window.pdf._pdfPaneAnnotationsByDoc.get('pdf-remediation')[0].el.value='Interim annotation';
    window.undoDuringSave=window.pdf._undoPdfPaneAction('pdf-remediation');
  });
  await undoGateStarted;
  await page.evaluate(()=>{
    const ann=window.pdf._pdfPaneAnnotationsByDoc.get('pdf-remediation')[0];
    ann.el.value='newer draft during undo';
    window.pdf._schedulePdfPaneSave('pdf-remediation');
    window.closeDuringUndo=window.editor.closePanel('down');
  });
  await page.locator('#doc-editor-pane').waitFor({state:'detached'});
  undoGate.resolve();
  await page.evaluate(()=>Promise.all([window.undoDuringSave,window.closeDuringUndo]));
  await page.evaluate(()=>window.editor.loadDocument('pdf-remediation'));
  await page.waitForFunction(()=>document.getElementById('doc-editor-textarea')?.value.includes('newer draft during undo'));
  await setPdfView(true);
  await page.waitForFunction(()=>window.pdf._pdfPaneAnnotationsByDoc.get('pdf-remediation')?.length===1);
  const rendersBeforeMountCheck=renderRequests;
  const refreshResponse=page.waitForResponse(response=>response.url().includes('/render-pages'));
  await page.evaluate(() => {
    const btn=document.getElementById('doc-pdf-refresh-btn');
    window.pdf.initPdfTools();
    window.disposedPdfTools=window.pdf.initPdfTools();
    btn.click();
  });
  await refreshResponse;
  assert.equal(renderRequests-rendersBeforeMountCheck,1,`repeated mount binds one refresh handler (total=${renderRequests}, before=${rendersBeforeMountCheck}, rendered=${await page.locator('#doc-pdf-view').getAttribute('data-rendered-doc-id')}, errors=${pageErrors})`);
  assert.equal(await page.evaluate(()=>window.pdf._pdfPaneAnnotationsByDoc.get('pdf-remediation')?.length),1,'rebinding preserves live pane refs');

  await page.locator('.pdf-annotation-wrap').hover();
  const finalDelete=page.waitForResponse(response=>response.request().method()==='PUT'&&!/annotation id=a/.test(response.request().postDataJSON()?.content||''));
  await page.locator('.pdf-annotation-wrap [title="Delete annotation"]').click();
  await finalDelete;
  assert.doesNotMatch(puts.at(-1) || '', /annotation id=a/, 'initialized empty pane state deletes final annotation');

  const beforeDisposeRefresh=renderRequests;
  await page.evaluate(()=>{window.disposedPdfTools();window.disposedPdfTools();});
  assert.equal(await page.evaluate(()=>window.pdf._pdfPaneAnnotationsByDoc.has('pdf-remediation')),false,'teardown releases mounted refs');
  await page.locator('#doc-pdf-refresh-btn').evaluate(button=>button.click());
  await new Promise(resolve=>setTimeout(resolve,50));
  assert.equal(renderRequests,beforeDisposeRefresh,'disposed mount no longer handles refresh');
  assert.deepEqual(pageErrors,[],'editor integration has no browser page errors');
  console.log(`PASS PDF persistence and lifecycle: ${puts.length} saves`);
} finally { await browser.close(); }
