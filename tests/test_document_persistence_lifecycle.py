"""Automated regression coverage for document persistence and lifecycle.

Validates autosave document identity, closing active/inactive tabs, session
switching isolation, slow and failed backend responses, queue serialization
with concurrent edits, document deletion cancellation, save-state transitions,
and prevention of cross-document overwrites.
"""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import stylesheet_link_tags

ROOT = Path(__file__).resolve().parents[1]


def _run_browser_test(js_script: str) -> dict:
    full_script = js_script.replace("__ODY_STYLESHEETS__", stylesheet_link_tags())
    result = subprocess.run(
        ["node", "--input-type=module", "-e", full_script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Node runner failed with exit code {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )
    return json.loads(result.stdout)


def test_scenario_1_autosave_identity_on_document_switch():
    """Scenario 1: Edit doc A, switch to B before autosave fires.

    Verifies A is saved with A's content and B is not overwritten.
    """
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      const puts = [];
      await page.route('**/api/document/**', async route => {
        if (route.request().method() === 'PUT') {
          puts.push({ url: route.request().url(), body: route.request().postDataJSON() });
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 2 }) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=' + Date.now());
        mod.init('/api');
        window.__doc = mod;
        await mod.injectFreshDoc({ id: 'doc-a', title: 'Doc A', language: 'markdown', current_content: 'A initial', version_count: 1 });
        await new Promise(r => setTimeout(r, 60));
        await mod.injectFreshDoc({ id: 'doc-b', title: 'Doc B', language: 'markdown', current_content: 'B initial', version_count: 1 });
        await new Promise(r => setTimeout(r, 60));
        await mod.loadDocument('doc-a');
        await new Promise(r => setTimeout(r, 60));
        const ta = document.querySelector('#doc-editor-textarea');
        ta.value = 'A modified';
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        await mod.loadDocument('doc-b');
        await new Promise(r => setTimeout(r, 120));
      });
      const activeValue = await page.locator('#doc-editor-textarea').inputValue();
      console.log(JSON.stringify({ puts, activeValue }));
      await browser.close();
    """
    data = _run_browser_test(script)
    doc_a_puts = [p for p in data["puts"] if p["url"].endswith("/api/document/doc-a")]
    doc_b_puts = [p for p in data["puts"] if p["url"].endswith("/api/document/doc-b")]
    assert len(doc_a_puts) == 1
    assert doc_a_puts[0]["body"]["content"] == "A modified"
    assert len(doc_b_puts) == 0
    assert data["activeValue"] == "B initial"


def test_scenario_2_close_active_document_flushes_pending_autosave():
    """Scenario 2: Edit doc A, close A before autosave.

    Verifies A is saved with A's content and no error toast is raised.
    """
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      const puts = [];
      await page.route('**/api/document/**', async route => {
        if (route.request().method() === 'PUT') {
          puts.push({ url: route.request().url(), body: route.request().postDataJSON() });
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 2 }) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=' + Date.now());
        mod.init('/api');
        window.__doc = mod;
        await mod.injectFreshDoc({ id: 'doc-a', title: 'Doc A', language: 'markdown', current_content: 'A initial', version_count: 1 });
        await new Promise(r => setTimeout(r, 60));
        await mod.injectFreshDoc({ id: 'doc-b', title: 'Doc B', language: 'markdown', current_content: 'B initial', version_count: 1 });
        await new Promise(r => setTimeout(r, 60));
        await mod.loadDocument('doc-a');
        await new Promise(r => setTimeout(r, 60));
        const ta = document.querySelector('#doc-editor-textarea');
        ta.value = 'A closing edit';
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        const closeBtn = document.querySelector('.doc-tab[data-doc-id="doc-a"] .doc-tab-close');
        closeBtn.click();
        await new Promise(r => setTimeout(r, 120));
      });
      const activeValue = await page.locator('#doc-editor-textarea').inputValue();
      const toast = await page.locator('#toast').textContent();
      console.log(JSON.stringify({ puts, activeValue, toast }));
      await browser.close();
    """
    data = _run_browser_test(script)
    doc_a_puts = [p for p in data["puts"] if p["url"].endswith("/api/document/doc-a")]
    assert len(doc_a_puts) == 1
    assert doc_a_puts[0]["body"]["content"] == "A closing edit"
    assert data["activeValue"] == "B initial"
    assert "Save failed" not in data["toast"]
    assert "could not be saved" not in data["toast"]


def test_scenario_3_close_inactive_document_preserves_active_edits():
    """Scenario 3: Close inactive doc B while active doc A has pending edits.

    Verifies B is closed, A's pending autosave/dirty state is untouched.
    """
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      const puts = [];
      await page.route('**/api/document/**', async route => {
        if (route.request().method() === 'PUT') {
          puts.push({ url: route.request().url(), body: route.request().postDataJSON() });
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 2 }) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=' + Date.now());
        mod.init('/api');
        window.__doc = mod;
        await mod.injectFreshDoc({ id: 'doc-a', title: 'Doc A', language: 'markdown', current_content: 'A initial', version_count: 1 });
        await new Promise(r => setTimeout(r, 60));
        await mod.injectFreshDoc({ id: 'doc-b', title: 'Doc B', language: 'markdown', current_content: 'B initial', version_count: 1 });
        await new Promise(r => setTimeout(r, 60));
        await mod.loadDocument('doc-a');
        await new Promise(r => setTimeout(r, 60));
        const ta = document.querySelector('#doc-editor-textarea');
        ta.value = 'A pending edit';
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        const closeBtnB = document.querySelector('.doc-tab[data-doc-id="doc-b"] .doc-tab-close');
        closeBtnB.click();
        await new Promise(r => setTimeout(r, 60));
      });
      const activeAfterClose = await page.evaluate(() => window.__doc.getChatDocumentId());
      const saveStateAfterClose = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
      await page.waitForTimeout(2200);
      const saveStateAfterAutoSave = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
      console.log(JSON.stringify({ puts, activeAfterClose, saveStateAfterClose, saveStateAfterAutoSave }));
      await browser.close();
    """
    data = _run_browser_test(script)
    assert data["activeAfterClose"] == "doc-a"
    assert data["saveStateAfterClose"] == "dirty"
    assert data["saveStateAfterAutoSave"] == "saved"
    doc_a_puts = [p for p in data["puts"] if p["url"].endswith("/api/document/doc-a")]
    assert len(doc_a_puts) == 1
    assert doc_a_puts[0]["body"]["content"] == "A pending edit"


def test_scenario_4_switch_session_while_save_is_pending():
    """Scenario 4: Switch sessions while a save is pending.

    Verifies previous session doc save does not target or affect new session.
    """
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      const puts = [];
      await page.route('**/api/document?session_id=session-2', async route => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([
            { id: 'doc-s2', session_id: 'session-2', title: 'Doc S2', content: 'S2 content', language: 'markdown', version_count: 1 }
          ]),
        });
      });
      await page.route('**/api/document/**', async route => {
        if (route.request().method() === 'PUT') {
          puts.push({ url: route.request().url(), body: route.request().postDataJSON() });
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 2 }) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=' + Date.now());
        mod.init('/api');
        window.__doc = mod;
        await mod.injectFreshDoc({ id: 'doc-s1', session_id: 'session-1', title: 'Doc S1', language: 'markdown', current_content: 'S1 initial', version_count: 1 });
        await new Promise(r => setTimeout(r, 60));
        const ta = document.querySelector('#doc-editor-textarea');
        ta.value = 'S1 edited content';
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        await mod.loadSessionDocs('session-2');
        await new Promise(r => setTimeout(r, 150));
      });
      const activeDoc = await page.evaluate(() => window.__doc.getChatDocumentId());
      console.log(JSON.stringify({ puts, activeDoc }));
      await browser.close();
    """
    data = _run_browser_test(script)
    s1_puts = [p for p in data["puts"] if p["url"].endswith("/api/document/doc-s1")]
    s2_puts = [p for p in data["puts"] if p["url"].endswith("/api/document/doc-s2")]
    assert len(s1_puts) == 1
    assert s1_puts[0]["body"]["content"] == "S1 edited content"
    assert len(s2_puts) == 0


def test_scenario_5_slow_backend_response_resolves_safely():
    """Scenario 5: Slow backend response.

    Delayed PUT response resolves safely without race conditions.
    """
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      await page.route('**/api/document/slow-doc', async route => {
        await new Promise(r => setTimeout(r, 150));
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 3 }) });
      });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=' + Date.now());
        mod.init('/api');
        window.__doc = mod;
        await mod.injectFreshDoc({ id: 'slow-doc', title: 'Slow', language: 'markdown', current_content: 'initial', version_count: 1 });
        await new Promise(r => setTimeout(r, 60));
      });
      await page.evaluate(() => {
        const ta = document.querySelector('#doc-editor-textarea');
        ta.value = 'slow edited';
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        window.__savePromise = window.__doc.saveDocument({ silent: true });
      });
      await page.waitForTimeout(30);
      const stateDuringFlight = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
      await page.evaluate(() => window.__savePromise);
      const stateAfterFlight = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
      const badgeText = await page.locator('#doc-version-badge').textContent();
      console.log(JSON.stringify({ stateDuringFlight, stateAfterFlight, badgeText }));
      await browser.close();
    """
    data = _run_browser_test(script)
    assert data["stateDuringFlight"] == "saving"
    assert data["stateAfterFlight"] == "saved"
    assert data["badgeText"] == "v3"


def test_scenario_6_failed_backend_response_and_retry_recovery():
    """Scenario 6: Failed backend response.

    Transitions to 'error', retryable via subsequent edit and save.
    """
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      let attempts = 0;
      await page.route('**/api/document/fail-doc', async route => {
        attempts += 1;
        if (attempts === 1) {
          await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'Server exploded' }) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 2 }) });
        }
      });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=' + Date.now());
        mod.init('/api');
        window.__doc = mod;
        await mod.injectFreshDoc({ id: 'fail-doc', title: 'Fail', language: 'markdown', current_content: 'initial', version_count: 1 });
        await new Promise(r => setTimeout(r, 60));
      });
      await page.evaluate(async () => {
        const ta = document.querySelector('#doc-editor-textarea');
        ta.value = 'attempt 1';
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        await window.__doc.saveDocument({ silent: true });
      });
      const stateAfterFailure = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
      await page.evaluate(async () => {
        const ta = document.querySelector('#doc-editor-textarea');
        ta.value = 'attempt 2 retry';
        ta.dispatchEvent(new Event('input', { bubbles: true }));
      });
      const stateAfterEdit = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
      await page.evaluate(async () => {
        await window.__doc.saveDocument({ silent: true });
      });
      const stateAfterSuccess = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
      console.log(JSON.stringify({ stateAfterFailure, stateAfterEdit, stateAfterSuccess, attempts }));
      await browser.close();
    """
    data = _run_browser_test(script)
    assert data["stateAfterFailure"] == "error"
    assert data["stateAfterEdit"] == "dirty"
    assert data["stateAfterSuccess"] == "saved"
    assert data["attempts"] == 2


def test_scenario_7_multiple_edits_during_queued_saves_preserves_dirty():
    """Scenario 7: Multiple edits during queued saves.

    Verifies _editRevision vs saveRevision dirty preservation when edits happen
    during an in-flight save.
    """
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      const savedBodies = [];
      await page.route('**/api/document/rev-doc', async route => {
        savedBodies.push(route.request().postDataJSON().content);
        await new Promise(r => setTimeout(r, 120));
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: savedBodies.length }) });
      });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=' + Date.now());
        mod.init('/api');
        window.__doc = mod;
        await mod.injectFreshDoc({ id: 'rev-doc', title: 'Rev', language: 'markdown', current_content: 'initial', version_count: 1 });
        await new Promise(r => setTimeout(r, 60));
      });
      await page.evaluate(() => {
        const ta = document.querySelector('#doc-editor-textarea');
        ta.value = 'first in-flight edit';
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        window.__p1 = window.__doc.saveDocument({ silent: true });
        setTimeout(() => {
          ta.value = 'second concurrent edit';
          ta.dispatchEvent(new Event('input', { bubbles: true }));
        }, 30);
      });
      await page.evaluate(() => window.__p1);
      const stateAfterFirstCompleted = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
      await page.evaluate(async () => {
        await window.__doc.saveDocument({ silent: true });
      });
      const stateAfterSecondCompleted = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
      console.log(JSON.stringify({ savedBodies, stateAfterFirstCompleted, stateAfterSecondCompleted }));
      await browser.close();
    """
    data = _run_browser_test(script)
    assert data["savedBodies"] == ["first in-flight edit", "second concurrent edit"]
    assert data["stateAfterFirstCompleted"] == "dirty"
    assert data["stateAfterSecondCompleted"] == "saved"


def test_scenario_8_document_deletion_cancels_autosave_and_avoids_orphans():
    """Scenario 8: Document deletion during pending work.

    Verifies no orphan PUT is sent or error shown when doc is deleted while autosave is debounced.
    """
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      page.on('dialog', async dialog => {
        await dialog.accept();
      });
      const puts = [];
      let deleteReceived = false;
      await page.route('**/api/document/del-doc', async route => {
        if (route.request().method() === 'DELETE') {
          deleteReceived = true;
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
        } else if (route.request().method() === 'PUT') {
          puts.push(route.request().postDataJSON());
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 2 }) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        window.confirm = () => true;
        const uiMod = await import('/static/js/ui.js?v=20260916largetoolscroll1');
        if (uiMod.default) {
          uiMod.default.styledConfirm = () => Promise.resolve(true);
        }
        const mod = await import('/static/js/document.js?v=' + Date.now());
        mod.init('/api');
        window.__doc = mod;
        await mod.injectFreshDoc({ id: 'del-doc', title: 'Del Doc', language: 'markdown', current_content: 'initial', version_count: 1 });
        await new Promise(r => setTimeout(r, 100));
        const ta = document.querySelector('#doc-editor-textarea');
        ta.value = 'typed before delete';
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        ta.blur();
        const btn = document.getElementById('doc-footer-copy-btn');
        btn.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true, cancelable: true }));
      });
      await page.waitForTimeout(2200);
      console.log(JSON.stringify({ deleteReceived, putsCount: puts.length }));
      await browser.close();
    """
    data = _run_browser_test(script)
    assert data["deleteReceived"] is True
    assert data["putsCount"] == 0


def test_scenario_9_correct_save_state_transitions():
    """Scenario 9: Correct save-state transitions.

    Verifies transitions between 'saved', 'dirty', 'saving', and back to 'saved'.
    """
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      await page.route('**/api/document/trans-doc', async route => {
        await new Promise(r => setTimeout(r, 80));
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 2 }) });
      });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=' + Date.now());
        mod.init('/api');
        window.__doc = mod;
        await mod.injectFreshDoc({ id: 'trans-doc', title: 'Trans', language: 'markdown', current_content: 'initial', version_count: 1 });
        await new Promise(r => setTimeout(r, 60));
      });
      const sInitial = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
      await page.evaluate(() => {
        const ta = document.querySelector('#doc-editor-textarea');
        ta.value = 'mod 1';
        ta.dispatchEvent(new Event('input', { bubbles: true }));
      });
      const sDirty = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
      await page.evaluate(() => {
        window.__saveP = window.__doc.saveDocument({ silent: true });
      });
      await page.waitForTimeout(20);
      const sSaving = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
      await page.evaluate(() => window.__saveP);
      const sSaved = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
      console.log(JSON.stringify({ sInitial, sDirty, sSaving, sSaved }));
      await browser.close();
    """
    data = _run_browser_test(script)
    assert data["sInitial"] == "saved"
    assert data["sDirty"] == "dirty"
    assert data["sSaving"] == "saving"
    assert data["sSaved"] == "saved"


def test_scenario_10_no_updates_to_wrong_document():
    """Scenario 10: No updates to the wrong document.

    Verifies targeting docId 'target-b' while 'target-a' is active does not steal
    target-a's textarea contents.
    """
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      const puts = [];
      await page.route('**/api/document/**', async route => {
        if (route.request().method() === 'PUT') {
          puts.push({ url: route.request().url(), body: route.request().postDataJSON() });
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 2 }) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=' + Date.now());
        mod.init('/api');
        window.__doc = mod;
        await mod.injectFreshDoc({ id: 'target-a', title: 'Target A', language: 'markdown', current_content: 'Doc A Text In Textarea', version_count: 1 });
        await new Promise(r => setTimeout(r, 60));
        await mod.injectFreshDoc({ id: 'target-b', title: 'Target B', language: 'markdown', current_content: 'Doc B Stored Content', version_count: 1 });
        await new Promise(r => setTimeout(r, 60));
        await mod.loadDocument('target-a');
        await new Promise(r => setTimeout(r, 60));
        await window.__doc.saveDocument({ silent: true, docId: 'target-b' });
      });
      console.log(JSON.stringify({ puts }));
      await browser.close();
    """
    data = _run_browser_test(script)
    target_b_puts = [p for p in data["puts"] if p["url"].endswith("/api/document/target-b")]
    assert len(target_b_puts) == 1
    assert target_b_puts[0]["body"]["content"] == "Doc B Stored Content"


def test_deletion_http_500_then_save_and_retry():
    """HTTP 500 on delete keeps doc savable and allows retrying deletion later."""
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      page.on('dialog', async dialog => { await dialog.accept(); });
      const puts = [];
      let deleteAttempts = 0;
      await page.route('**/api/document/retry-doc', async route => {
        if (route.request().method() === 'DELETE') {
          deleteAttempts += 1;
          if (deleteAttempts === 1) {
            await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'boom' }) });
          } else {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
          }
        } else if (route.request().method() === 'PUT') {
          puts.push(route.request().postDataJSON());
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: puts.length + 1 }) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      const res = await page.evaluate(async () => {
        window.confirm = () => true;
        const uiMod = await import('/static/js/ui.js?v=20260916largetoolscroll1');
        if (uiMod.default) uiMod.default.styledConfirm = () => Promise.resolve(true);
        const mod = await import('/static/js/document.js?v=del-500-' + Date.now());
        mod.init('/api');
        await mod.injectFreshDoc({ id: 'retry-doc', title: 'Retry Doc', language: 'markdown', current_content: 'start', version_count: 1 });
        await new Promise(r => setTimeout(r, 80));

        // 1. Trigger failed DELETE (HTTP 500)
        const triggerDelete = () => {
          const btn = document.getElementById('doc-footer-copy-btn');
          btn.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true, cancelable: true }));
        };
        triggerDelete();
        await new Promise(r => setTimeout(r, 150));

        const stillOpenAfter500 = mod.isPanelOpen() && mod.getCurrentDocId() === 'retry-doc';
        const tabExistsAfter500 = Boolean(document.querySelector('.doc-tab[data-doc-id="retry-doc"]'));

        // 2. Edit & manually save after failed deletion
        const ta = document.getElementById('doc-editor-textarea');
        ta.value = 'saved after failed delete';
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        const manualSaveOk = await mod.saveDocument({ silent: true, docId: 'retry-doc' });

        // 3. Edit & wait for debounced autosave (2000ms debounce) after failed deletion
        ta.value = 'autosaved after failed delete';
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        await new Promise(r => setTimeout(r, 2300));

        // 4. Retry deletion -> backend now returns 200
        ta.blur();
        triggerDelete();
        await new Promise(r => setTimeout(r, 150));

        const tabExistsAfterSuccess = Boolean(document.querySelector('.doc-tab[data-doc-id="retry-doc"]'));
        const saveAfterSuccess = await mod.saveDocument({ silent: true, docId: 'retry-doc' });

        return {
          stillOpenAfter500,
          tabExistsAfter500,
          manualSaveOk,
          tabExistsAfterSuccess,
          saveAfterSuccess,
          activeDocAfterSuccess: mod.getCurrentDocId(),
        };
      });
      console.log(JSON.stringify({ ...res, deleteAttempts, puts }));
      await browser.close();
    """
    data = _run_browser_test(script)
    assert data["deleteAttempts"] == 2
    assert data["stillOpenAfter500"] is True
    assert data["tabExistsAfter500"] is True
    assert data["manualSaveOk"] is True
    assert [p["content"] for p in data["puts"]] == [
        "saved after failed delete",
        "autosaved after failed delete",
    ]
    assert data["tabExistsAfterSuccess"] is False
    assert data["saveAfterSuccess"] is False
    assert data["activeDocAfterSuccess"] is None


def test_deletion_network_failure_preserves_doc_and_saves():
    """Network abort during delete leaves document active and savable."""
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      const puts = [];
      let deleteAborted = false;
      await page.route('**/api/document/net-doc', async route => {
        if (route.request().method() === 'DELETE') {
          deleteAborted = true;
          await route.abort('failed');
        } else if (route.request().method() === 'PUT') {
          puts.push(route.request().postDataJSON());
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 2 }) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      const res = await page.evaluate(async () => {
        window.confirm = () => true;
        const uiMod = await import('/static/js/ui.js?v=20260916largetoolscroll1');
        if (uiMod.default) uiMod.default.styledConfirm = () => Promise.resolve(true);
        const mod = await import('/static/js/document.js?v=del-net-' + Date.now());
        mod.init('/api');
        await mod.injectFreshDoc({ id: 'net-doc', title: 'Net Doc', language: 'markdown', current_content: 'start', version_count: 1 });
        await new Promise(r => setTimeout(r, 80));

        const btn = document.getElementById('doc-footer-copy-btn');
        btn.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true, cancelable: true }));
        await new Promise(r => setTimeout(r, 150));

        const ta = document.getElementById('doc-editor-textarea');
        ta.value = 'saved after network abort';
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        const savedAfterAbort = await mod.saveDocument({ silent: true, docId: 'net-doc' });
        return {
          savedAfterAbort,
          docIdStillPresent: mod.getCurrentDocId() === 'net-doc',
        };
      });
      console.log(JSON.stringify({ ...res, deleteAborted, puts }));
      await browser.close();
    """
    data = _run_browser_test(script)
    assert data["deleteAborted"] is True
    assert data["docIdStillPresent"] is True
    assert data["savedAfterAbort"] is True
    assert len(data["puts"]) == 1
    assert data["puts"][0]["content"] == "saved after network abort"


def test_pending_save_during_deletion_outcomes():
    """Verify pending saves during deletion: suppressed on 200, resumed on 500."""
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      const puts = [];
      await page.route('**/api/document/**', async route => {
        const url = route.request().url();
        const method = route.request().method();
        if (method === 'DELETE' && url.endsWith('/del-ok-doc')) {
          await new Promise(r => setTimeout(r, 120));
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
        } else if (method === 'DELETE' && url.endsWith('/del-fail-doc')) {
          await new Promise(r => setTimeout(r, 120));
          await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'fail' }) });
        } else if (method === 'PUT') {
          puts.push({ url, content: route.request().postDataJSON()?.content });
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 2 }) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      const res = await page.evaluate(async () => {
        window.confirm = () => true;
        const uiMod = await import('/static/js/ui.js?v=20260916largetoolscroll1');
        if (uiMod.default) uiMod.default.styledConfirm = () => Promise.resolve(true);
        const mod = await import('/static/js/document.js?v=del-pending-' + Date.now());
        mod.init('/api');

        // Subcase A: pending autosave + in-flight save call during successful DELETE
        await mod.injectFreshDoc({ id: 'del-ok-doc', title: 'Del Ok', language: 'markdown', current_content: 'init', version_count: 1 });
        await new Promise(r => setTimeout(r, 60));
        const ta = document.getElementById('doc-editor-textarea');
        ta.value = 'pending edit before success delete';
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        document.getElementById('doc-footer-copy-btn').dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true, cancelable: true }));
        const saveDuringOkDelete = await mod.saveDocument({ silent: true, docId: 'del-ok-doc' });
        await new Promise(r => setTimeout(r, 950));

        // Subcase B: pending autosave during failing DELETE must be preserved and saved after failure
        await mod.injectFreshDoc({ id: 'del-fail-doc', title: 'Del Fail', language: 'markdown', current_content: 'init', version_count: 1 });
        await new Promise(r => setTimeout(r, 60));
        const ta2 = document.getElementById('doc-editor-textarea');
        ta2.value = 'pending edit preserved on fail';
        ta2.dispatchEvent(new Event('input', { bubbles: true }));
        document.getElementById('doc-footer-copy-btn').dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true, cancelable: true }));
        await new Promise(r => setTimeout(r, 1200));

        return { saveDuringOkDelete };
      });
      console.log(JSON.stringify({ ...res, puts }));
      await browser.close();
    """
    data = _run_browser_test(script)
    assert data["saveDuringOkDelete"] is False
    ok_puts = [p for p in data["puts"] if p["url"].endswith("/del-ok-doc")]
    fail_puts = [p for p in data["puts"] if p["url"].endswith("/del-fail-doc")]
    assert len(ok_puts) == 0
    assert len(fail_puts) >= 1
    assert fail_puts[-1]["content"] == "pending edit preserved on fail"


def test_pdf_pane_delayed_autosave_targets_originating_doc_after_tab_switch():
    """Delayed PDF form/annotation edits save to originating doc, not newly active tab."""
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 1000, height: 750 } });
      const puts = [];
      const storedContent = {
        'pdf-a': '<!-- pdf_form_source upload_id="up-a" -->\n- **FullName:** _(empty)_ <!-- field=FullName type=text -->\n',
        'pdf-b': '<!-- pdf_form_source upload_id="up-b" -->\n- **FullName:** Bob Original <!-- field=FullName type=text -->\n',
      };
      const tinyPngBase64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
      await page.route('**/api/document/**', async route => {
        const url = route.request().url();
        const method = route.request().method();
        const docId = url.includes('/pdf-b') ? 'pdf-b' : 'pdf-a';
        if (url.endsWith('/render-pages')) {
          const md = storedContent[docId] || '';
          const m = md.match(/\*\*FullName:\*\*[ ]*(.*?) <!-- field=FullName/);
          const val = (m && m[1] !== '_(empty)_') ? m[1] : '';
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              pages: [{
                page: 1,
                width: 400,
                height: 500,
                fields: [{ name: 'FullName', label: 'Full Name', type: 'text', value: val, rect_px: [20, 40, 220, 70] }],
              }],
            }),
          });
        } else if (url.endsWith('/page/1.png')) {
          await route.fulfill({
            status: 200,
            contentType: 'image/png',
            body: Buffer.from(tinyPngBase64, 'base64'),
          });
        } else if (method === 'PUT') {
          const body = route.request().postDataJSON();
          storedContent[docId] = body.content;
          puts.push({ url, body });
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: puts.length + 1 }) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      const res = await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=pdf-tab-' + Date.now());
        const pdfTools = await import('/static/js/document/ui/pdfTools.js');
        mod.init('/api');
        const pdfContentA = '<!-- pdf_form_source upload_id="up-a" -->\n- **FullName:** _(empty)_ <!-- field=FullName type=text -->\n';
        const pdfContentB = '<!-- pdf_form_source upload_id="up-b" -->\n- **FullName:** Bob Original <!-- field=FullName type=text -->\n';
        await mod.injectFreshDoc({ id: 'pdf-a', title: 'PDF A', language: 'markdown', current_content: pdfContentA, version_count: 1 });
        await new Promise(r => setTimeout(r, 60));
        await mod.injectFreshDoc({ id: 'pdf-b', title: 'PDF B', language: 'markdown', current_content: pdfContentB, version_count: 1 });
        await new Promise(r => setTimeout(r, 60));

        await mod.loadDocument('pdf-a');
        await new Promise(r => setTimeout(r, 150));

        // 1. Edit PDF A field, then switch immediately to PDF B before 600ms debounce expires
        const inputA = document.querySelector('#doc-pdf-view input[data-field-name="FullName"]');
        inputA.value = 'Alice FormValue';
        inputA.dispatchEvent(new Event('input', { bubbles: true }));
        await mod.loadDocument('pdf-b');
        await new Promise(r => setTimeout(r, 150));

        const renderedDocWhenOnB = document.getElementById('doc-pdf-view')?.dataset?.renderedDocId || null;
        const inputB = document.querySelector('#doc-pdf-view input[data-field-name="FullName"]');
        const fieldBValue = inputB ? inputB.value : null;

        // A detached control is no longer authoritative. Late callbacks must
        // neither revive its refs nor overwrite the captured originating draft.
        const retainedDetachedRefs = pdfTools._pdfPaneFieldsByDoc.has('pdf-a');
        inputA.value = 'Alice DelayedTimer';
        pdfTools._schedulePdfPaneSave('pdf-a');
        await new Promise(r => setTimeout(r, 850));

        // Verify PDF B's textarea & rendered value were never overwritten by PDF A's save
        const textareaB = document.getElementById('doc-editor-textarea')?.value || '';
        await mod.loadDocument('pdf-a');
        await new Promise(r => setTimeout(r, 150));
        const inputAAfterReturn = document.querySelector('#doc-pdf-view input[data-field-name="FullName"]')?.value || '';

        return {
          renderedDocWhenOnB,
          fieldBValue,
          textareaB,
          inputAAfterReturn,
          retainedDetachedRefs,
        };
      });
      console.log(JSON.stringify({ ...res, puts }));
      await browser.close();
    """
    data = _run_browser_test(script)
    assert data["renderedDocWhenOnB"] == "pdf-b"
    assert data["fieldBValue"] == "Bob Original"
    assert "Alice" not in data["textareaB"]
    assert "Bob Original" in data["textareaB"]
    assert data["retainedDetachedRefs"] is False
    assert data["inputAAfterReturn"] == "Alice FormValue"
    pdf_a_puts = [p for p in data["puts"] if p["url"].endswith("/api/document/pdf-a")]
    pdf_b_puts = [p for p in data["puts"] if p["url"].endswith("/api/document/pdf-b")]
    assert len(pdf_a_puts) == 1
    assert "Alice FormValue" in pdf_a_puts[-1]["body"]["content"]
    assert "Alice DelayedTimer" not in pdf_a_puts[-1]["body"]["content"]
    assert len(pdf_b_puts) == 0


def test_pdf_pane_autosave_across_session_switch_and_revision_serialization():
    """Session change while PDF save is debounced persists to originating doc and serializes PUTs."""
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 1000, height: 750 } });
      const putEvents = [];
      let putSeq = 0;
      const tinyPngBase64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
      await page.route('**/api/documents/**', async route => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([
            { id: 'doc-sess-2', title: 'Session 2 Doc', language: 'markdown', current_content: 'Session Two Clean', version_count: 1, is_active: true },
          ]),
        });
      });
      await page.route('**/api/document/**', async route => {
        const url = route.request().url();
        const method = route.request().method();
        if (url.endsWith('/render-pages')) {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              pages: [{
                page: 1,
                width: 400,
                height: 500,
                fields: [{ name: 'Signer', label: 'Signer', type: 'text', value: '', rect_px: [20, 40, 220, 70] }],
              }],
            }),
          });
        } else if (url.endsWith('/page/1.png')) {
          await route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from(tinyPngBase64, 'base64') });
        } else if (method === 'PUT') {
          const seq = ++putSeq;
          const body = route.request().postDataJSON();
          putEvents.push({ phase: 'start', seq, url, content: body.content });
          await new Promise(r => setTimeout(r, seq === 1 ? 150 : 20));
          putEvents.push({ phase: 'end', seq, url });
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: seq + 1 }) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      const res = await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=pdf-sess-' + Date.now());
        mod.init('/api');
        const pdfMd = '<!-- pdf_form_source upload_id="up-s1" -->\n- **Signer:** _(empty)_ <!-- field=Signer type=text -->\n';
        await mod.injectFreshDoc({ id: 'pdf-s1', session_id: 'sess-1', title: 'PDF S1', language: 'markdown', current_content: pdfMd, version_count: 1 });
        await new Promise(r => setTimeout(r, 150));

        // 1. Concurrent serialized edits on pdf-s1 before switching session
        const inp = document.querySelector('#doc-pdf-view input[data-field-name="Signer"]');
        inp.value = 'First Draft';
        inp.dispatchEvent(new Event('input', { bubbles: true }));
        const p1 = mod.saveDocument({ silent: true, docId: 'pdf-s1' });
        await new Promise(r => setTimeout(r, 20));
        inp.value = 'Session One Final';
        inp.dispatchEvent(new Event('input', { bubbles: true }));
        // Switch chat session while debounce timer is pending and first PUT is in flight
        await mod.loadSessionDocs('sess-2');
        await p1;
        await new Promise(r => setTimeout(r, 800));

        const activeTextareaAfterSwitch = document.getElementById('doc-editor-textarea')?.value || '';
        return {
          activeDocAfterSwitch: mod.getCurrentDocId(),
          activeTextareaAfterSwitch,
        };
      });
      console.log(JSON.stringify({ ...res, putEvents }));
      await browser.close();
    """
    data = _run_browser_test(script)
    assert data["activeDocAfterSwitch"] == "doc-sess-2"
    assert data["activeTextareaAfterSwitch"] == "Session Two Clean"
    s1_starts = [e for e in data["putEvents"] if e["phase"] == "start" and e["url"].endswith("/pdf-s1")]
    s2_puts = [e for e in data["putEvents"] if e["url"].endswith("/doc-sess-2")]
    assert len(s2_puts) == 0
    assert len(s1_starts) == 2
    assert "First Draft" in s1_starts[0]["content"]
    assert "Session One Final" in s1_starts[1]["content"]
    # Strict per-doc serialization: second PUT starts only after first PUT completes
    end_1 = next(i for i, e in enumerate(data["putEvents"]) if e["phase"] == "end" and e["seq"] == 1)
    start_2 = next(i for i, e in enumerate(data["putEvents"]) if e["phase"] == "start" and e["seq"] == 2)
    assert end_1 < start_2


def test_pdf_pane_annotation_undo_and_export_preserved():
    """PDF form/annotation save, undo (Ctrl+Z), and export flush remain intact."""
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 1000, height: 750 } });
      const requests = [];
      const tinyPngBase64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
      await page.route('**/api/document/**', async route => {
        const url = route.request().url();
        const method = route.request().method();
        if (url.endsWith('/render-pages')) {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ pages: [{ page: 1, width: 400, height: 500, fields: [] }] }),
          });
        } else if (url.endsWith('/page/1.png')) {
          await route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from(tinyPngBase64, 'base64') });
        } else if (url.endsWith('/export-pdf')) {
          requests.push({ type: 'EXPORT' });
          await route.fulfill({
            status: 200,
            contentType: 'application/pdf',
            headers: { 'Content-Disposition': 'attachment; filename="filled.pdf"' },
            body: '%PDF-1.4\n',
          });
        } else if (method === 'PUT') {
          requests.push({ type: 'PUT', content: route.request().postDataJSON()?.content || '' });
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: requests.length + 1 }) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=pdf-undo-exp-' + Date.now());
        mod.init('/api');
        const pdfMd = '<!-- pdf_source upload_id="up-ann" -->\n';
        await mod.injectFreshDoc({ id: 'pdf-ops', title: 'PDF Ops', language: 'markdown', current_content: pdfMd, version_count: 1 });
        await new Promise(r => setTimeout(r, 150));

        // Add text annotation via toolbar + blank page click
        document.getElementById('doc-pdf-add-text-btn').click();
        const pageImg = document.querySelector('#doc-pdf-view img');
        const r = pageImg.getBoundingClientRect();
        pageImg.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: r.left + 40, clientY: r.top + 40 }));
        pageImg.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: r.left + 40, clientY: r.top + 40 }));
        const annTa = document.querySelector('#doc-pdf-view .pdf-annotation-wrap textarea');
        annTa.value = 'Note One';
        annTa.dispatchEvent(new Event('input', { bubbles: true }));

        // Click export immediately before 600ms timer expires -> must flush PUT before EXPORT
        document.getElementById('doc-export-pdf-btn').click();
        await new Promise(r => setTimeout(r, 300));
      });

      // Undo the annotation edit / creation via header Undo button (#doc-undo-btn)
      const beforeUndoCount = await page.locator('#doc-pdf-view .pdf-annotation-wrap').count();
      await page.locator('#doc-undo-btn').click();
      await page.waitForTimeout(250);
      const afterUndoText = await page.evaluate(() => {
        const ta = document.querySelector('#doc-pdf-view .pdf-annotation-wrap textarea');
        return ta ? ta.value : null;
      });
      await page.locator('#doc-undo-btn').click();
      await page.waitForTimeout(250);
      const afterSecondUndoCount = await page.locator('#doc-pdf-view .pdf-annotation-wrap').count();

      console.log(JSON.stringify({ requests, beforeUndoCount, afterUndoText, afterSecondUndoCount }));
      await browser.close();
    """
    data = _run_browser_test(script)
    assert data["beforeUndoCount"] == 1
    assert data["requests"][0]["type"] == "PUT"
    assert "Note One" in data["requests"][0]["content"]
    assert any(r["type"] == "EXPORT" for r in data["requests"])
    first_export = next(i for i, r in enumerate(data["requests"]) if r["type"] == "EXPORT")
    assert first_export >= 1
    assert data["afterUndoText"] == ""
    assert data["afterSecondUndoCount"] == 0
