"""Recovery regressions for Markdown visual content the serializer cannot save.

These exercise the published document module in Chromium using the shared,
stateful API harness from ``test_document_visual_editor``.
"""

from tests.test_document_visual_editor import _run_browser


def test_failed_visual_save_discard_and_server_reopen_keep_last_valid_draft():
    data = _run_browser(r"""
      await boot('recovery-invalid-serialization');
      await open({ id: 'recover-md', title: 'Recover', language: 'markdown', current_content: 'Starting text' });
      await page.locator('#doc-email-richbody').click();
      await caretAtEnd();
      await page.keyboard.type(' valid edit');
      const valid = await state();

      // This nesting is outside the supported Markdown visual serializer.
      await page.locator('#doc-email-richbody').evaluate(el => {
        el.innerHTML = '<p>Starting text valid edit <code>foo<strong>bar</strong></code></p>';
        el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText' }));
      });
      await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      const afterFailedSave = await state();
      const savedBeforeDiscard = api.store.get('recover-md')?.current_content;

      // Discard only the unsupported visual fragment, then a stale server tab
      // response must not replace the valid local edit.
      await page.locator('[data-action="discard-visual-draft"]').click();
      await page.evaluate(async () => {
        const session = await import('/static/js/document/application/documentSession.js');
        session.addDocToTabs({ id: 'recover-md', title: 'Recover', language: 'markdown', current_content: 'Starting text', session_id: 's1' }, 's1');
      });
      const afterReopen = await state();
      const saveState = await page.evaluate(() => window.__state.docs.get('recover-md')?._saveState);
      await finish({ valid, afterFailedSave, savedBeforeDiscard, afterReopen, saveState, puts: api.puts });
    """)
    assert data["valid"]["content"] == "Starting text valid edit"
    assert data["savedBeforeDiscard"] == "Starting text"
    assert data["afterFailedSave"]["content"] == "Starting text valid edit"
    assert data["afterFailedSave"]["bannerVisible"] is True
    assert data["afterReopen"]["content"] == "Starting text valid edit"
    assert data["saveState"] != "saved"


def test_closing_blocked_visual_draft_and_creating_untitled_keeps_recovery_copy():
    data = _run_browser(r"""
      await boot('recovery-close');
      await open({ id: 'close-md', title: 'Blocked', language: 'markdown', current_content: '' });
      await page.locator('#doc-email-richbody').evaluate(el => {
        el.innerHTML = '<p>UNIQUE_RECOVERY_SENTINEL <code>x<strong>y</strong></code></p>';
        el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText' }));
      });
      await page.locator('.doc-tab-close[data-doc-id="close-md"]').click();
      await page.waitForTimeout(50);
      const closing = await page.evaluate(() => ({
        visible: window.__state.docs.has('close-md'),
        retained: window.__state._closingDocs.get('close-md')?._visualDraftHtml,
      }));
      await open({ id: 'untitled-target', title: '', language: 'markdown', current_content: '' });
      await page.waitForFunction(() => window.__state.activeDocId === 'untitled-target');
      await page.evaluate(() => window.__doc.createDocument('s1'));
      await page.waitForFunction(() => window.__state.activeDocId?.startsWith('new-doc-'));
      const result = await page.evaluate(() => ({
        retained: window.__state._closingDocs.get('close-md')?._visualDraftHtml,
        oldPresent: window.__state.docs.has('close-md'),
        newContent: window.__state.docs.get(window.__state.activeDocId)?.content,
        activeId: window.__state.activeDocId,
      }));
      await finish({ closing, result, creates: api.creates, puts: api.puts });
    """)
    assert data["closing"]["visible"] is False
    assert "UNIQUE_RECOVERY_SENTINEL" in data["closing"]["retained"]
    assert "UNIQUE_RECOVERY_SENTINEL" in data["result"]["retained"]
    assert data["result"]["newContent"] == ""
    assert data["result"]["activeId"].startswith("new-doc-")
    assert len(data["creates"]) == 1 and data["creates"][0]["content"] == ""
    assert data["puts"] == []


def test_reload_and_reboot_reopen_same_document_without_overwriting_blocked_draft():
    data = _run_browser(r"""
      await boot('recovery-reload-initial');
      await open({ id: 'reload-md', title: 'Reload', language: 'markdown', current_content: 'Server baseline' });
      await page.locator('#doc-email-richbody').evaluate(el => {
        el.innerHTML = '<p>SERVER baseline <code>UNIQUE_RELOAD_DRAFT<strong>!</strong></code></p>';
        el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText' }));
      });
      const beforeReload = await state();
      await page.reload();
      await boot('recovery-reload-after');
      await open({ id: 'reload-md', title: 'Reload', language: 'markdown', current_content: 'Server baseline' });
      await page.waitForFunction(() => window.__state.activeDocId === 'reload-md');
      const afterReload = await state();
      await page.waitForTimeout(2200);
      await finish({ beforeReload, afterReload, puts: api.puts, server: api.store.get('reload-md') });
    """)
    assert "UNIQUE_RELOAD_DRAFT" in data["beforeReload"]["richHtml"]
    assert "UNIQUE_RELOAD_DRAFT" in data["afterReload"]["richHtml"]
    assert data["afterReload"]["bannerVisible"] is True
    assert data["puts"] == []
    assert data["server"]["current_content"] == "Server baseline"


def test_blocked_visual_language_conversion_and_history_restore_do_not_mutate_server():
    data = _run_browser(r"""
      await boot('recovery-no-server-mutations');
      const writes = [];
      page.on('request', request => {
        if (request.method() !== 'GET') writes.push({ method: request.method(), url: request.url() });
      });
      api.versions.set('blocked-md', [
        { version_number: 2, content: '# Server version\n\nValid source', source: 'user' },
        { version_number: 1, content: '# Earlier supported version', source: 'user' },
      ]);
      await open({ id: 'blocked-md', title: 'Blocked', language: 'markdown', current_content: '# Server version\n\nValid source' });
      await page.locator('#doc-email-richbody').evaluate(el => {
        el.innerHTML = '<h1>Server version</h1><p>Valid source <code>foo<strong>bar</strong></code></p>';
        el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText' }));
      });
      await page.locator('#doc-language-select').selectOption('html');
      await page.waitForTimeout(50);
      await page.locator('.doc-tab-version[data-doc-id="blocked-md"]').click();
      await page.waitForSelector('.doc-version-restore[data-version="1"]');
      await page.locator('.doc-version-restore[data-version="1"]').click();
      await page.waitForTimeout(50);
      const before = await state();
      const language = await page.evaluate(() => window.__state.docs.get('blocked-md')?.language);
      const after = await state();
      await finish({ before, after, language, writes, puts: api.puts, creates: api.creates, server: api.store.get('blocked-md') });
    """)
    assert data["server"]["current_content"] == "# Server version\n\nValid source"
    assert data["before"]["content"] == "# Server version\n\nValid source"
    assert data["language"] == "markdown"
    assert data["puts"] == []
    assert data["creates"] == []
    assert data["writes"] == []
