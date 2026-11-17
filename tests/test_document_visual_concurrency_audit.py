"""Visual Markdown persistence through delayed acknowledgements and HTTP failures."""

import pytest
from tests.test_document_visual_editor import _run_browser


def test_pending_visual_save_acknowledges_only_its_snapshot_across_navigation():
    data = _run_browser(r"""
      await boot('audit-pending-save');
      await open({ id: 'other-md', title: 'Other', language: 'markdown', current_content: 'Other document' });
      await open({ id: 'pending-md', title: 'Pending', language: 'markdown', current_content: 'Original' });
      let release;
      let entered;
      const pending = new Promise(resolve => { release = resolve; });
      const seen = new Promise(resolve => { entered = resolve; });
      const writes = [];
      await page.route('**/api/document/pending-md', async route => {
        if (route.request().method() !== 'PUT') return route.fallback();
        const body = route.request().postDataJSON();
        writes.push(body.content);
        if (writes.length === 1) { entered(); await pending; }
        api.store.get('pending-md').current_content = body.content;
        await route.fulfill({ json: { version_count: 1 } });
      });
      await caretAtEnd();
      await page.keyboard.type(' A');
      await page.evaluate(() => { window.__pendingSave = window.__doc.saveDocument({ silent: true }); });
      await seen;
      await page.keyboard.type(' B');
      await page.evaluate(() => window.__doc.loadDocument('other-md'));
      release();
      await page.evaluate(() => window.__pendingSave);
      const afterAck = await page.evaluate(() => ({
        activeId: window.__state.activeDocId,
        other: window.__state.docs.get('other-md').content,
        pending: window.__state.docs.get('pending-md').content,
        state: window.__state.docs.get('pending-md')._saveState,
      }));
      await page.evaluate(() => window.__doc.saveDocument({ silent: true, docId: 'pending-md' }));
      await finish({ afterAck, writes, server: api.store.get('pending-md').current_content });
    """)
    assert data["afterAck"]["activeId"] == "other-md"
    assert data["afterAck"]["other"] == "Other document"
    assert data["afterAck"]["pending"] == "Original A B"
    assert data["afterAck"]["state"] != "saved"
    assert data["writes"][0] == "Original A"
    assert data["writes"][-1] == data["server"] == "Original A B"


@pytest.mark.parametrize("status", [404, 409, 500])
def test_failed_visual_save_recovers_after_reload_without_acknowledging(status):
    data = _run_browser(r"""
      await boot('audit-failed-save');
      await open({ id: 'failed-md', title: 'Failed', language: 'markdown', current_content: 'Persisted baseline' });
      await page.route('**/api/document/failed-md', async route => {
        if (route.request().method() !== 'PUT') return route.fallback();
        await route.fulfill({ status: __STATUS__, json: { detail: 'deterministic failure' } });
      });
      await caretAtEnd();
      await page.keyboard.type(' recovered edit');
      const saved = await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      const failedState = await page.evaluate(() => window.__state.docs.get('failed-md')._saveState);
      await page.reload();
      await boot('audit-failed-save-reloaded');
      await open({ id: 'failed-md', title: 'Failed', language: 'markdown', current_content: 'Persisted baseline' });
      const recovered = await state();
      const recoveredState = await page.evaluate(() => window.__state.docs.get('failed-md')._saveState);
      await finish({ saved, failedState, recovered, recoveredState, server: api.store.get('failed-md').current_content });
    """.replace("__STATUS__", str(status)))
    assert data["saved"] is False
    assert data["failedState"] == "error"
    assert data["server"] == "Persisted baseline"
    assert data["recovered"]["content"] == "Persisted baseline recovered edit"
    assert "recovered edit" in data["recovered"]["richHtml"]
    assert data["recoveredState"] != "saved"


def test_browser_unload_warns_for_unsaved_markdown():
    data = _run_browser(r"""
      await boot('audit-browser-unload');
      await open({ id: 'close-warning', title: 'Unsaved', language: 'markdown', current_content: 'Persisted' });
      await page.locator('#doc-email-richbody').click();
      await caretAtEnd();
      await page.keyboard.type(' pending');
      let warnings = 0;
      page.on('dialog', dialog => { if (dialog.type() === 'beforeunload') warnings++; });
      // Navigation exercises the trusted native beforeunload event. This
      // Chromium's CDP Page.close skips it, including in the minimal control.
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentOutline.js?after-unload`);
      await finish({ warnings, puts: api.puts, server: api.store.get('close-warning').current_content });
    """)
    assert data['warnings'] == 1
    assert data['puts'] == []
    assert data['server'] == 'Persisted'
