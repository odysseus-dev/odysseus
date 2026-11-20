"""Security and renderer regression checks against the published document modules."""

from tests.test_document_visual_editor import _run_browser


def test_visual_paste_security_matrix_survives_save_mode_switch_and_reload():
    data = _run_browser(r"""
      await boot('security-paste-audit');
      const cases = [
        { name: 'svg', html: '<svg onload="window.__auditXss=1"><script>window.__auditXss=2</script><a href="javascript:window.__auditXss=3">svg</a></svg>' },
        { name: 'mathml', html: '<math><mtext><img src=x onerror="window.__auditXss=4"></mtext></math>' },
        { name: 'events', html: '<p onclick="window.__auditXss=5"><span onmouseover="window.__auditXss=6">event text</span></p>' },
        { name: 'entities', html: '<a href="java&#x73;cript:window.__auditXss=7">entity link</a>' },
        { name: 'controls', html: '<a href="java&#10;script:window.__auditXss=8">control link</a>' },
        { name: 'malformed', html: '<p><b>bold <i>nested</b></i><img src=x onerror="window.__auditXss=9"><a href="javascript:window.__auditXss=10">broken link' },
      ];
      const results = [];
      for (const c of cases) {
        const id = `paste-${c.name}`;
        await open({ id, title: c.name, language: 'markdown', current_content: 'before' });
        await page.waitForSelector('#doc-email-richbody.markdown-visual-mode');
        await page.locator('#doc-email-richbody').evaluate((el, html) => {
          el.focus();
          const range = document.createRange();
          range.selectNodeContents(el);
          range.collapse(false);
          const sel = window.getSelection();
          sel.removeAllRanges();
          sel.addRange(range);
          const dt = new DataTransfer();
          dt.setData('text/html', html);
          el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
        }, c.html);
        await page.waitForTimeout(20);
        await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
        const afterSave = await state();
        const serialized = await page.evaluate(() => ({
          xss: window.__auditXss || null,
          html: document.getElementById('doc-email-richbody').innerHTML,
          content: window.__state.docs.get(window.__state.activeDocId)?.content,
        }));
        await page.locator('[data-mdview="edit"]').click();
        const source = await page.locator('#doc-editor-textarea').inputValue();
        await page.locator('[data-mdview="visual"]').click();
        await page.waitForSelector('#doc-email-richbody.markdown-visual-mode');
        const toggled = await state();
        const stored = api.store.get(id)?.current_content;
        await boot(`security-paste-reload-${c.name}`);
        await open({ id, title: c.name, language: 'markdown', current_content: stored });
        await page.waitForFunction((id) => window.__state.activeDocId === id, id);
        const reloaded = await state();
        results.push({ name: c.name, afterSave, ...serialized, source, toggled, stored, reloaded });
      }
      const raw = '<div onclick="keep source exact">Raw <img src=x onerror="literal"> block</div>\n\n<script>literal source</script>';
      await open({ id: 'raw-source', title: 'Raw source', language: 'markdown', current_content: raw });
      await page.waitForFunction(() => window.__state.activeDocId === 'raw-source');
      const rawState = await state();
      await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      const rawStored = api.store.get('raw-source')?.current_content;
      await finish({ results, raw, rawState, rawStored, puts: api.puts });
    """);

    assert len(data["results"]) == 6
    for result in data["results"]:
        assert result["xss"] is None, result["name"]
        assert "onerror" not in result["html"].lower(), result["name"]
        assert "onclick" not in result["html"].lower(), result["name"]
        assert "onmouseover" not in result["html"].lower(), result["name"]
        assert "javascript:" not in result["html"].lower(), result["name"]
        assert "javascript:" not in result["content"].lower(), result["name"]
        assert result["source"] == result["content"], result["name"]
        assert result["toggled"]["content"] == result["content"], result["name"]
        assert result["stored"] == result["content"], result["name"]
        assert result["reloaded"]["content"] == result["content"], result["name"]
    assert data["rawState"]["visual"] is False
    assert data["rawState"]["textarea"] == data["raw"].replace("\r\n", "\n")
    assert data["rawState"]["content"] == data["raw"]
    assert data["rawStored"] == data["raw"]
    assert not [p for p in data["puts"] if p["docId"] == "raw-source" and p["body"].get("content") != data["raw"]]


def test_shared_markdown_renderer_emphasis_fences_and_code_is_inert():
    data = _run_browser(r"""
      await boot('security-renderer-audit');
      const cases = [
        { name: 'nested-adjacent', md: '**bold *nested*** *one* *two*' },
        { name: 'tilde-longer-close', md: '~~~js\nconst marker = "~~~";\n~~~~\nafter' },
        { name: 'backtick-longer-close', md: '````js\nconst marker = "```";\n`````\nafter' },
        { name: 'mismatched-fences', md: '~~~js\nconst marker = "~~~";\n```\nafter' },
      ];
      const result = await page.evaluate(async (cases) => {
        const mod = await import('/static/js/markdown.js?renderer-audit');
        const rendered = cases.map(c => ({ name: c.name, html: mod.mdToHtml(c.md, { shortcodes: false, commonmark: true }) }));
        const host = document.createElement('div');
        document.body.appendChild(host);
        host.innerHTML = rendered.map(r => r.html).join('');
        const dom = {
          strongTexts: [...host.querySelectorAll('strong')].map(e => e.textContent),
          emTexts: [...host.querySelectorAll('em')].map(e => e.textContent),
          codeTexts: [...host.querySelectorAll('pre code')].map(e => e.textContent),
          afterParagraphs: [...host.querySelectorAll('p')].map(e => e.textContent).filter(t => t === 'after'),
          executableElements: host.querySelectorAll('script,iframe,object,embed,svg[onload]').length,
          eventAttrs: [...host.querySelectorAll('*')].flatMap(e => [...e.attributes].filter(a => /^on/i.test(a.name)).map(a => a.name)),
        };
        return { rendered, dom, xss: window.__auditXss || null };
      }, cases);
      await finish(result);
    """);

    rendered = {case["name"]: case["html"] for case in data["rendered"]}
    assert "<strong>bold <em>nested</em></strong>" in rendered["nested-adjacent"]
    assert rendered["nested-adjacent"].count("<em>") == 3
    assert "<em>one</em>" in rendered["nested-adjacent"]
    assert "<em>two</em>" in rendered["nested-adjacent"]
    assert len(data["dom"]["codeTexts"]) == 3
    assert data["dom"]["codeTexts"][0] == 'const marker = "~~~";\n'
    assert data["dom"]["codeTexts"][1] == 'const marker = "```";\n'
    # A fence closes only on its own marker type with a run at least as long as the opener.
    assert "```\nafter" in data["dom"]["codeTexts"][2]
    assert data["dom"]["executableElements"] == 0
    assert data["dom"]["eventAttrs"] == []
    assert data["xss"] is None
