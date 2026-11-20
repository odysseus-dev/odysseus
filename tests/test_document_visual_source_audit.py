"""Chromium source fidelity and visual editor lifecycle audits."""

import json
import re

from tests.test_document_visual_editor import _run_browser


def test_noncanonical_source_edit_preserves_untouched_bytes_and_source_fallback():
    raw = (
        "\ufeffEdit target  \r\n\r\nEdit target\n\n"
        "Cafe\u0301 and emoji 😀\r\n\r\n"
        "| Name  | Value  |\n|:------|-------:|\n| A     |   001  |\n\n"
        "```js\nfirst backtick fence\n```\n\n~~~text\nsecond tilde fence\n~~~\n\n\n"
        "Tail with spaces   \n> outer\n>\n>   > nested quote\n\n- loose first\n\n"
        "  continuation\n\n:::note\nextension\n:::"
    )
    raw_json = json.dumps(raw)
    data = _run_browser(r"""
      await boot('source-fidelity-audit');
      const raw = __RAW__;
      await open({ id: 'source-fidelity', title: 'Source fidelity', language: 'markdown', current_content: raw });
      await page.waitForFunction(() => window.__state.activeDocId === 'source-fidelity');
      const opened = await state();
      await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      const saved = api.store.get('source-fidelity')?.current_content;
      await open({ id: 'editable-target', title: 'Editable target', language: 'markdown', current_content: 'Edit target\n\nUntouched block' });
      await page.waitForSelector('#doc-email-richbody.markdown-visual-mode');
      await page.evaluate(() => {
        const rich = document.getElementById('doc-email-richbody');
        const p = [...rich.querySelectorAll('p')].find(el => el.textContent.includes('Edit target'));
        if (p?.firstChild) {
          p.firstChild.textContent = 'Edit targetX';
          rich.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: 'X' }));
        }
      });
      const afterEdit = await state();
      await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      const editedSaved = api.store.get('editable-target')?.current_content;
      await boot('source-fidelity-reopen');
      await open({ id: 'editable-target', title: 'Editable target', language: 'markdown', current_content: editedSaved });
      await page.waitForFunction(() => window.__state.activeDocId === 'editable-target');
      const reopened = await state();
      await finish({ raw, opened, afterEdit, saved, editedSaved, reopened, puts: api.puts });
    """.replace("__RAW__", raw_json))

    assert data["opened"]["content"] == raw
    assert data["opened"]["visual"] is False
    assert data["opened"]["textarea"] == raw.replace("\r\n", "\n")
    assert data["saved"] == raw
    assert "Edit target  " in data["saved"] and "Cafe\u0301 and emoji 😀" in data["saved"]
    assert "|:------|-------:|" in data["saved"]
    assert "```js" in data["saved"] and "~~~text" in data["saved"]
    assert data["editedSaved"] == "Edit targetX\n\nUntouched block"
    assert data["reopened"]["content"] == data["editedSaved"]
    assert "Edit targetX" in data["reopened"]["richHtml"]
    assert not [p for p in data["puts"] if p["docId"] == "source-fidelity" and p["body"].get("content") != raw]


def test_paste_order_revision_conflicts_composition_and_undo_redo():
    data = _run_browser(r"""
      await boot('source-lifecycle-audit');
      await open({ id: 'paste-order', title: 'Paste order', language: 'markdown', current_content: 'A\n\nOriginal block\n\nZ' });
      await page.waitForSelector('#doc-email-richbody.markdown-visual-mode');
      await page.locator('#doc-email-richbody').evaluate(el => {
        el.focus();
        const p = [...el.querySelectorAll('p')].find(e => e.textContent === 'Original block');
        const range = document.createRange(); range.selectNodeContents(p); range.collapse(false);
        const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range);
        const dt = new DataTransfer(); dt.setData('text/html', '<p>B</p><p>C</p>');
        el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
      });
      await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      const pasteSaved = api.store.get('paste-order')?.current_content;
      await boot('source-lifecycle-paste-reload');
      await open({ id: 'paste-order', title: 'Paste order', language: 'markdown', current_content: pasteSaved });
      await page.waitForFunction(() => window.__state.activeDocId === 'paste-order');
      const pasteReload = await state();

      await open({ id: 'stale-revision', title: 'Stale revision', language: 'markdown', current_content: 'Base paragraph' });
      await page.waitForSelector('#doc-email-richbody.markdown-visual-mode');
      await page.evaluate(() => {
        const doc = window.__state.docs.get('stale-revision');
        doc._visualExpectedRevision = 'intentionally-stale-revision';
        const rich = document.getElementById('doc-email-richbody');
        rich.querySelector('p').firstChild.textContent = 'Base paragraph typed';
        rich.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: ' typed' }));
      });
      const stale = await page.evaluate(() => ({
        state: window.__state.docs.get('stale-revision'),
        html: document.getElementById('doc-email-richbody').innerHTML,
        })).then(v => ({ blocked: v.state._visualSyncBlocked, content: v.state.content, draft: v.state._visualDraftHtml, html: v.html }));

      await open({ id: 'external-change', title: 'External change', language: 'markdown', current_content: 'Original' });
      await page.waitForSelector('#doc-email-richbody.markdown-visual-mode');
      await page.evaluate(() => {
        const doc = window.__state.docs.get('external-change');
        doc.content = 'External canonical update';
        const rich = document.getElementById('doc-email-richbody');
        rich.querySelector('p').firstChild.textContent = 'Original local edit';
        rich.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: ' local edit' }));
      });
      const external = await page.evaluate(() => {
        const doc = window.__state.docs.get('external-change');
        return { blocked: doc._visualSyncBlocked, content: doc.content, draft: doc._visualDraftHtml, html: document.getElementById('doc-email-richbody').innerHTML };
      });
      const staleServer = api.store.get('stale-revision')?.current_content;

      await open({ id: 'composition', title: 'Composition', language: 'markdown', current_content: '# Heading\n\n- list item\n\nComposition target' });
      await page.waitForSelector('#doc-email-richbody.markdown-visual-mode h1');
      const composition = await page.evaluate(() => {
        const rich = document.getElementById('doc-email-richbody');
        const p = rich.querySelector('p');
        p.textContent = 'Composing';
        rich.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
        rich.dispatchEvent(new InputEvent('input', { bubbles: true, isComposing: true, inputType: 'insertCompositionText', data: 'Composing' }));
        const beforeEnter = rich.innerHTML;
        rich.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, isComposing: true }));
        const afterEnter = rich.innerHTML;
        p.textContent = 'Composed final';
        rich.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: 'Composed final' }));
        return { beforeEnter, afterEnter, content: window.__state.docs.get('composition').content, html: rich.innerHTML };
      });

      // Native undo/redo for heading Backspace and list soft-break operations.
      await page.evaluate(() => {
        const rich = document.getElementById('doc-email-richbody');
        const h1 = rich.querySelector('h1');
        const range = document.createRange(); range.selectNodeContents(h1); range.collapse(true);
        getSelection().removeAllRanges(); getSelection().addRange(range); rich.focus();
      });
      await page.keyboard.press('Backspace');
      const headingAfter = await page.locator('#doc-email-richbody').evaluate(el => el.innerHTML);
      await page.keyboard.press('Control+z');
      const headingUndo = await page.locator('#doc-email-richbody').evaluate(el => el.innerHTML);
      await page.keyboard.press('Control+Shift+z');
      const headingRedo = await page.locator('#doc-email-richbody').evaluate(el => el.innerHTML);
      await page.evaluate(() => {
        const rich = document.getElementById('doc-email-richbody');
        const li = rich.querySelector('li');
        const range = document.createRange(); range.selectNodeContents(li); range.collapse(false);
        getSelection().removeAllRanges(); getSelection().addRange(range); rich.focus();
      });
      await page.keyboard.press('Shift+Enter');
      await page.keyboard.type('second line');
      const listAfter = await page.locator('#doc-email-richbody').evaluate(el => el.innerHTML);
      await page.keyboard.press('Control+z');
      const listUndo = await page.locator('#doc-email-richbody').evaluate(el => el.innerHTML);
      await page.keyboard.press('Control+Shift+z');
      const listRedo = await page.locator('#doc-email-richbody').evaluate(el => el.innerHTML);
      await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      const interactionsSaved = api.store.get('composition')?.current_content;
      await boot('source-lifecycle-interaction-reload');
      await open({ id: 'composition', title: 'Composition', language: 'markdown', current_content: interactionsSaved });
      await page.waitForFunction(() => window.__state.activeDocId === 'composition');
      const interactionReload = await state();

      await finish({ pasteSaved, pasteReload, stale, staleServer, external, composition, headingAfter, headingUndo, headingRedo, listAfter, listUndo, listRedo, interactionsSaved, interactionReload });
    """);

    assert data["pasteSaved"].index("B") < data["pasteSaved"].index("C")
    assert "A" in data["pasteSaved"] and "Original block" in data["pasteSaved"] and "Z" in data["pasteSaved"]
    assert data["pasteReload"]["content"] == data["pasteSaved"]
    assert "Original blockB" in data["pasteReload"]["richHtml"]
    assert "<p>C</p>" in data["pasteReload"]["richHtml"]
    assert data["stale"]["blocked"] is True
    assert data["stale"]["content"] == "Base paragraph"
    assert "Base paragraph typed" in data["stale"]["html"]
    assert data["staleServer"] == "Base paragraph"
    assert data["external"]["blocked"] is True
    assert data["external"]["content"] == "External canonical update"
    assert "Original local edit" in data["external"]["html"]
    assert data["composition"]["afterEnter"] == data["composition"]["beforeEnter"]
    assert "Composed final" in data["composition"]["content"]
    assert "<h1>Heading</h1>" in data["headingUndo"]
    assert "<p>Heading</p>" in data["headingRedo"]
    assert ("<br" in data["listAfter"] or "\n" in data["listAfter"]) and "second line" in data["listAfter"]
    assert "second line" not in data["listUndo"]
    assert "second line" in data["listRedo"]
    assert data["interactionReload"]["content"] == data["interactionsSaved"]
    assert "second line" in data["interactionsSaved"]
    assert len(re.findall(r"<li(?:\s|>)", data["interactionReload"]["richHtml"])) == 1
    assert "second line" in data["interactionReload"]["richHtml"]
    assert "<br" in data["interactionReload"]["richHtml"] or "\n" in data["interactionReload"]["richHtml"]


def test_list_tab_outdent_and_accessible_visual_textbox():
    data = _run_browser(r"""
      await boot('audit-list-tab');
      await open({ id: 'tab-md', title: 'Tab', language: 'markdown', current_content: '- Parent\n- Child' });
      const rich = page.getByRole('textbox', { name: 'Visual document editor', exact: true });
      await rich.waitFor();
      await rich.evaluate(el => {
        const li = [...el.querySelectorAll('li')].find(li => li.textContent === 'Child');
        const range = document.createRange(); range.setStart(li.firstChild, 2); range.collapse(true);
        getSelection().removeAllRanges(); getSelection().addRange(range); el.focus();
      });
      await page.keyboard.press('Tab');
      const nested = await state();
      const nestedCount = await rich.locator('ul ul').count();
      await page.keyboard.press('Shift+Tab');
      const outdented = await state();
      const restoredCount = await rich.locator('ul ul').count();
      await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      await finish({ nested, nestedCount, outdented, restoredCount, saved: api.store.get('tab-md').current_content });
    """)
    assert data['nestedCount'] == 1, data['nested']
    assert '  - Child' in data['nested']['content']
    assert data['restoredCount'] == 0
    assert data['saved'] == '- Parent\n- Child'


def test_mode_caret_accounts_for_heading_and_inline_markdown_delimiters():
    data = _run_browser(r"""
      await boot('audit-caret-delimiters');
      await open({ id: 'caret-md', title: 'Caret', language: 'markdown', current_content: '# Heading\n\n**Bold** tail' });
      const results = [];
      for (const [selector, offset, expected] of [['h1', 3, 5], ['strong', 2, 15]]) {
        await page.locator('#doc-email-richbody').evaluate((el, {selector, offset}) => {
          const text = el.querySelector(selector).firstChild;
          const range = document.createRange(); range.setStart(text, offset); range.collapse(true);
          el.focus(); getSelection().removeAllRanges(); getSelection().addRange(range);
        }, {selector, offset});
        await page.locator('[data-mdview="edit"]').click();
        const sourceOffset = await page.locator('#doc-editor-textarea').evaluate(el => el.selectionStart);
        await page.locator('[data-mdview="visual"]').click();
        const restored = await page.evaluate(() => ({ text: getSelection().anchorNode?.textContent, offset: getSelection().anchorOffset }));
        results.push({sourceOffset, expected, restored, offset});
      }
      await finish({results});
    """)
    for result in data['results']:
        assert result['sourceOffset'] == result['expected']
        assert result['restored']['offset'] == result['offset']


def test_unrepresentable_heading_and_table_breaks_are_recoverable_without_saving():
    data = _run_browser(r"""
      await boot('audit-structural-breaks');
      const results = [];
      for (const [id, source, selector] of [['heading-break', '# One Two', 'h1'], ['table-break', '| A |\n| --- |\n| One Two |', 'td']]) {
        await open({ id, title: id, language: 'markdown', current_content: source });
        await page.locator('#doc-email-richbody').evaluate((el, selector) => {
          el.focus();const text=el.querySelector(selector).firstChild;
          const r=document.createRange();r.setStart(text,3);r.collapse(true);getSelection().removeAllRanges();getSelection().addRange(r);
        }, selector);
        await page.keyboard.press('Shift+Enter');
        await page.keyboard.type('X');
        const saved=await page.evaluate(()=>window.__doc.saveDocument({silent:true}));
        const local=await state();
        results.push({id,source,saved,local,server:api.store.get(id).current_content});
      }
      await finish({results});
    """)
    for result in data['results']:
        assert result['saved'] is False, result
        assert result['local']['content'] == result['server'] == result['source']
        assert result['local']['bannerVisible'] is True
        assert 'X' in result['local']['richHtml']


def test_duplicate_visual_blocks_never_delete_a_different_source_spelling():
    data = _run_browser(r"""
      await boot('audit-duplicate-identity');
      const original='~~~js\nsame\n~~~\n\n```js\nsame\n```';
      await open({id:'duplicate-normal',title:'Normal edit',language:'markdown',current_content:original});
      await page.locator('#doc-email-richbody').evaluate(el=>{
        const code=el.querySelector('code');el.focus();const r=document.createRange();r.setStart(code.firstChild,4);r.collapse(true);getSelection().removeAllRanges();getSelection().addRange(r);
      });
      await page.keyboard.type('X');
      const normalSaved=await page.evaluate(()=>window.__doc.saveDocument({silent:true}));
      const normal=api.store.get('duplicate-normal').current_content;
      const cases=[];
      for(const [id,append] of [['duplicate-delete',false],['duplicate-replace',true]]) {
        await open({id,title:id,language:'markdown',current_content:original});
        await page.locator('#doc-email-richbody').evaluate((el,append)=>{
          el.querySelector('pre').remove();
          if(append){const p=document.createElement('p');p.textContent='New block';el.appendChild(p);}
          el.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'deleteContentBackward'}));
        },append);
        const saved=await page.evaluate(()=>window.__doc.saveDocument({silent:true}));
        const local=await state();
        cases.push({saved,local,server:api.store.get(id).current_content});
      }
      await finish({original,normalSaved,normal,cases});
    """)
    assert data['normalSaved'] is True
    assert data['normal'] == data['original'].replace('same', 'sameX', 1)
    for case in data['cases']:
        assert case['saved'] is False, case['server']
        assert case['server'] == case['local']['content'] == data['original']
        assert case['local']['bannerVisible'] is True
        assert case['local']['richHtml'].count('<pre>') == 1


def test_large_noncanonical_block_never_silently_falls_back_to_canonical_source():
    data = _run_browser(r"""
      await boot('audit-no-canonical-fallback');
      const updateRequests = [];
      page.on('request', request => {
        if (request.method() === 'PUT' && new URL(request.url()).pathname.startsWith('/api/document/')) {
          updateRequests.push(request.url());
        }
      });
      const original=Array.from({length:1000},(_,i)=>`* Item ${i}`).join('\n');
      await open({id:'large-noncanonical',title:'Large list',language:'markdown',current_content:original});
      await page.waitForFunction(() => window.__state.activeDocId === 'large-noncanonical');
      const freshOpen = await state();
      // The shared preview renderer currently sends asterisk lists to Source.
      // Mount the production visual adapter directly to exercise reconciliation
      // of this review corpus, including an already-open visual draft.
      const admissionReason = await page.evaluate(async () => {
        const sync = await import('/static/js/document/ui/richEditor/markdownVisualSync.js');
        const wiring = await import('/static/js/document/ui/richEditor/richBodyWiring.js');
        const doc = window.__state.docs.get('large-noncanonical');
        const rich = document.getElementById('doc-email-richbody');
        document.getElementById('doc-editor-wrap').style.display = 'none';
        document.getElementById('doc-editor-textarea').style.display = 'none';
        rich.style.display = '';
        rich.classList.add('markdown-visual-mode');
        rich.contentEditable = 'true';
        doc._markdownEditorMode = 'visual';
        sync.renderMarkdownIntoVisualRich(rich, doc);
        const reason = sync.captureVisualBaseline(rich, doc);
        wiring._wireEmailRichbody(rich);
        return reason;
      });
      await page.waitForSelector('#doc-email-richbody.markdown-visual-mode li');
      await caretAtEnd();
      await page.keyboard.type('X');
      const saved=await page.evaluate(()=>window.__doc.saveDocument({silent:true}));
      const local = await state();
      const recovery = await page.evaluate(() => {
        const doc = window.__state.docs.get('large-noncanonical');
        return { blocked: doc._visualSyncBlocked, draft: doc._visualDraftHtml, persisted: doc._persistedContent,
          saveState: doc._saveState, buttonState: document.getElementById('doc-footer-copy-btn').dataset.saveState };
      });
      await page.locator('[data-mdview="edit"]').click();
      const afterSourceAttempt = await state();
      await open({ id: 'other-doc', title: 'Other', language: 'markdown', current_content: 'Other document' });
      await page.waitForFunction(() => window.__state.activeDocId === 'other-doc');
      await page.evaluate(() => window.__doc.loadDocument('large-noncanonical'));
      await page.waitForFunction(() => window.__state.activeDocId === 'large-noncanonical');
      const afterNavigation = await state();
      await page.reload();
      await boot('audit-no-canonical-fallback-reload');
      await open({ id: 'large-noncanonical', title: 'Large list', language: 'markdown', current_content: original });
      await page.waitForSelector('#doc-email-richbody.markdown-visual-mode li');
      const afterReload = await state();
      const savedAfterReload = await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      await finish({original,saved,local,recovery,freshOpen,admissionReason,afterSourceAttempt,afterNavigation,afterReload,savedAfterReload,
        puts:api.puts,updateRequests,server:api.store.get('large-noncanonical').current_content});
    """)
    assert data['saved'] is False
    assert data['local']['content'] == data['server'] == data['original']
    assert data['local']['bannerVisible'] is True
    assert 'Item 999X' in data['local']['richHtml']
    assert data['freshOpen']['visual'] is True
    assert data['freshOpen']['content'] == data['original']
    assert data['admissionReason'] == ''
    assert data['local']['textarea'] == data['original']
    assert data['recovery']['blocked'] is True
    assert 'Item 999X' in data['recovery']['draft']
    assert data['recovery']['persisted'] == data['original']
    assert data['recovery']['saveState'] == data['recovery']['buttonState'] == 'blocked'
    assert data['puts'] == []
    assert data['updateRequests'] == []
    assert data['savedAfterReload'] is False
    for state in (data['afterSourceAttempt'], data['afterNavigation'], data['afterReload']):
        assert state['content'] == state['textarea'] == data['original']
        assert state['bannerVisible'] is True
        assert state['visual'] is True
        assert 'Item 999X' in state['richHtml']


def test_supported_list_and_paragraph_edits_still_save_without_rewriting_source():
    data = _run_browser(r"""
      await boot('audit-supported-localized-edits');
      const original = '- First\n- Last\n\nUntouched paragraph';
      await open({ id: 'supported-localized', title: 'Supported', language: 'markdown', current_content: original });
      await page.waitForSelector('#doc-email-richbody.markdown-visual-mode li');
      await page.locator('#doc-email-richbody').evaluate(rich => {
        rich.focus();
        const text = rich.querySelector('li:last-child').firstChild;
        const range = document.createRange(); range.setStart(text, text.length); range.collapse(true);
        getSelection().removeAllRanges(); getSelection().addRange(range);
      });
      await page.keyboard.type('X');
      const listSaved = await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      const afterList = await state();
      await caretAtEnd();
      await page.keyboard.type('Y');
      const paragraphSaved = await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      const afterParagraph = await state();
      const saveState = await page.evaluate(() => window.__state.docs.get('supported-localized')._saveState);
      await finish({ listSaved, paragraphSaved, afterList, afterParagraph, saveState, puts: api.puts,
        server: api.store.get('supported-localized').current_content });
    """)
    assert data['listSaved'] is True
    assert data['paragraphSaved'] is True
    assert data['afterList']['content'] == '- First\n- LastX\n\nUntouched paragraph'
    assert data['afterParagraph']['content'] == data['server'] == '- First\n- LastX\n\nUntouched paragraphY'
    assert data['afterParagraph']['textarea'] == data['server']
    assert data['afterList']['bannerVisible'] is False
    assert data['afterParagraph']['bannerVisible'] is False
    assert data['saveState'] == 'saved'
    assert [put['body']['content'] for put in data['puts']] == [data['afterList']['content'], data['server']]
