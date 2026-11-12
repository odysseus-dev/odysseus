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
