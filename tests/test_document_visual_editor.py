"""Chromium integration coverage for the visual-first Markdown document editor.

Each test drives the published document module in a real Chromium page against a
mocked document API, so mode switching, persistence, lifecycle and formatting run
through the shipped module graph.
"""

import json
import os
import subprocess
from pathlib import Path

from tests.helpers.stylesheets import stylesheet_link_tags

ROOT = Path(__file__).resolve().parents[1]

# Shared page setup: a stateful mock of the document API (create, read, save,
# versions) that records every write, plus page-error capture.
HARNESS = r"""
  import { chromium } from 'playwright';
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1024, height: 760 } });
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(String(error?.message || error)));
  page.on('dialog', async dialog => {
    if (dialog.type() === 'beforeunload') await dialog.accept();
    else {
      pageErrors.push(`Unexpected browser dialog: ${dialog.type()}`);
      await dialog.dismiss();
    }
  });

  const api = { creates: [], puts: [], store: new Map(), versions: new Map() };
  await page.route('**/api/**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const method = req.method();
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/api/document' && method === 'POST') {
      const body = JSON.parse(req.postData() || '{}');
      api.creates.push(body);
      const created = { id: `new-doc-${api.creates.length}`, session_id: body.session_id || 's1', title: body.title || '', language: body.language || 'markdown', current_content: body.content || '', version_count: 1 };
      api.store.set(created.id, created);
      return json(created);
    }
    const versionsMatch = url.pathname.match(/^\/api\/document\/([^/]+)\/versions$/);
    if (versionsMatch) return json(api.versions.get(versionsMatch[1]) || []);
    const versionMatch = url.pathname.match(/^\/api\/document\/([^/]+)\/version\/(\d+)$/);
    if (versionMatch) {
      const found = (api.versions.get(versionMatch[1]) || []).find(v => String(v.version_number) === versionMatch[2]);
      return json(found || {}, found ? 200 : 404);
    }
    const docMatch = url.pathname.match(/^\/api\/document\/([^/]+)$/);
    if (docMatch && method === 'PUT') {
      const body = JSON.parse(req.postData() || '{}');
      api.puts.push({ docId: docMatch[1], body });
      const cur = api.store.get(docMatch[1]) || { id: docMatch[1], version_count: 1 };
      if (typeof body.content === 'string') cur.current_content = body.content;
      if (body.force_version) cur.version_count = (cur.version_count || 1) + 1;
      api.store.set(docMatch[1], cur);
      return json(cur);
    }
    if (docMatch && method === 'GET') {
      const cur = api.store.get(docMatch[1]);
      return json(cur || {}, cur ? 200 : 404);
    }
    return json({});
  });

  const boot = async (tag) => {
    await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentOutline.js`);
    await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
    await page.evaluate(async (tag) => {
      localStorage.removeItem('odysseus-doc-md-editor-mode');
      const mod = await import(`/static/js/document.js?v=${tag}`);
      window.__doc = mod;
      window.__state = await import('/static/js/document/application/documentState.js');
      mod.init('');
    }, tag);
  };
  const open = async (doc) => {
    api.store.set(doc.id, { session_id: 's1', version_count: 1, ...doc });
    await page.evaluate((d) => window.__doc.injectFreshDoc(d), { session_id: 's1', version_count: 1, ...doc });
  };
  const state = () => page.evaluate(() => {
    const rich = document.getElementById('doc-email-richbody');
    const wrap = document.getElementById('doc-editor-wrap');
    const banner = document.getElementById('doc-visual-compat-banner');
    const doc = window.__state.docs.get(window.__state.activeDocId);
    return {
      activeId: window.__state.activeDocId,
      visual: getComputedStyle(rich).display !== 'none' && rich.classList.contains('markdown-visual-mode'),
      wrapVisible: getComputedStyle(wrap).display !== 'none',
      bannerVisible: getComputedStyle(banner).display !== 'none',
      bannerText: banner.textContent,
      content: doc?.content,
      textarea: document.getElementById('doc-editor-textarea').value,
      richHtml: rich.innerHTML,
      editable: rich.contentEditable,
    };
  });
  const caretAtEnd = () => page.evaluate(() => {
    const rich = document.getElementById('doc-email-richbody');
    rich.focus();
    const walker = document.createTreeWalker(rich, NodeFilter.SHOW_TEXT);
    let last = null;
    while (walker.nextNode()) last = walker.currentNode;
    const range = document.createRange();
    if (last) range.setStart(last, last.length);
    else range.selectNodeContents(rich);
    range.collapse(true);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  });
  const finish = async (payload) => {
    console.log(JSON.stringify({ ...payload, pageErrors }));
    await browser.close();
  };
"""


def _run_browser(body: str) -> dict:
    script = (HARNESS + body).replace("__ODY_STYLESHEETS__", stylesheet_link_tags())
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=240,
    )
    assert result.returncode == 0, f"Node script failed ({result.returncode}):\n{result.stderr}\n{result.stdout}"
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["pageErrors"] == [], data["pageErrors"]
    return data


SEED = "# Seed Heading\n\nKeep   exact   spacing\n\n- Alpha\n- Beta"


def test_visual_markdown_default_formatting_slash_links_and_noop_roundtrip():
    data = _run_browser(r"""
      await boot('visual-default');
      await open({ id: 'seed-md', title: 'Seed Markdown', language: 'markdown', current_content: '__SEED__' });
      await page.waitForSelector('#doc-email-richbody.markdown-visual-mode h1');
      const seedInitial = await page.evaluate(() => ({
        visualActive: document.querySelector('[data-mdview="visual"]')?.classList.contains('active'),
        markdownActive: document.querySelector('[data-mdview="edit"]')?.classList.contains('active'),
        h1Text: document.querySelector('#doc-email-richbody h1')?.textContent,
        dirty: window.__state.docs.get('seed-md')?._saveState === 'dirty',
      }));
      const seedState = await state();

      // Visual -> Markdown -> Visual without edits keeps the exact source.
      await page.locator('[data-mdview="edit"]').click();
      const inMarkdown = await state();
      await page.locator('[data-mdview="visual"]').click();
      const backToVisual = await state();

      // Undo of a visual edit restores the exact original source.
      await caretAtEnd();
      await page.keyboard.type('X');
      const afterTyping = await state();
      await page.keyboard.press('Control+z');
      const afterUndo = await state();

      // A new document is Markdown and opens in the visual editor.
      await page.evaluate(async () => {
        localStorage.removeItem('odysseus-doc-md-editor-mode');
        await window.__doc.createDocument('s1');
      });
      await page.waitForFunction(() => window.__state.activeDocId === 'new-doc-1');
      await page.waitForSelector('#doc-email-richbody.markdown-visual-mode');
      const createdInfo = await page.evaluate(() => ({
        language: window.__state.docs.get(window.__state.activeDocId)?.language,
        placeholder: document.getElementById('doc-email-richbody').dataset.placeholder,
        separatorVisible: getComputedStyle(document.querySelector('[data-dd="separator"]')).display !== 'none',
        colorHidden: getComputedStyle(document.querySelector('[data-dd="color"]')).display === 'none',
        quoteVisible: getComputedStyle(document.querySelector('[data-md="quote"]')).display !== 'none',
        tableVisible: getComputedStyle(document.querySelector('[data-dd="table"]')).display !== 'none',
        underlineHidden: getComputedStyle(document.querySelector('[data-md="underline"]')).display === 'none',
      }));

      // Visual editing: heading input rule, bold toggled on and off by keyboard with
      // no typing delay, slash menu divider.
      await page.locator('#doc-email-richbody').click();
      await page.keyboard.type('# My Visual Doc');
      await page.keyboard.press('Enter');
      await page.keyboard.type('Plain ');
      await page.keyboard.press('Control+b');
      await page.keyboard.type('bold');
      await page.keyboard.press('Control+b');
      await page.keyboard.type(' text');
      await page.keyboard.press('Enter');
      await page.keyboard.type('/');
      await page.waitForSelector('.doc-rich-slash-menu');
      const slashItems = await page.locator('.doc-rich-slash-item').allTextContents();
      await page.keyboard.type('div');
      await page.keyboard.press('Enter');
      await page.keyboard.type('After divider');

      // The separator dropdown offers only the Markdown-representable divider.
      await page.locator('[data-dd="separator"]').click();
      const separatorItems = await page.locator('#doc-md-dd-menu .doc-overflow-item').evaluateAll(els => els.map(el => el.textContent.trim()));
      await page.keyboard.press('Escape');

      // Rich-only actions are ignored in visual Markdown.
      await page.evaluate(() => {
        document.getSelection().selectAllChildren(document.getElementById('doc-email-richbody'));
        document.querySelector('[data-md="underline"]')?.click();
      });
      await page.evaluate(() => window.__doc.saveDocument({ silent: true, forceVersion: true }));
      const savedAfterVisual = await state();

      await page.locator('[data-mdview="edit"]').click();
      const sourceText = await page.locator('#doc-editor-textarea').inputValue();

      // Paste: scripts, unsafe URLs and rich-only formatting are dropped.
      await page.locator('[data-mdview="visual"]').click();
      await page.locator('#doc-email-richbody').evaluate((el) => {
        el.focus();
        const range = document.createRange();
        range.selectNodeContents(el);
        range.collapse(false);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        const dt = new DataTransfer();
        dt.setData('text/html', '<p><img src=x onerror="window.__xss=2"><script>window.__xss=1<\/script><span style="color:red"><u>Safe</u></span> <a href="javascript:alert(1)">BadLink</a> <a href="https://example.com" onclick="window.__xss=3">GoodLink</a></p>');
        el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
      });
      await page.waitForTimeout(50);
      await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      const pasteResult = await page.evaluate(() => ({
        xssRan: Boolean(window.__xss),
        content: window.__state.docs.get(window.__state.activeDocId)?.content,
        html: document.getElementById('doc-email-richbody').innerHTML,
      }));

      await finish({ api: { creates: api.creates, puts: api.puts }, seedInitial, seedState, inMarkdown, backToVisual, afterTyping, afterUndo, createdInfo, slashItems, separatorItems, savedAfterVisual, sourceText, pasteResult });
    """.replace("__SEED__", SEED.replace("\n", "\\n")))

    assert data["seedInitial"] == {"visualActive": True, "markdownActive": False, "h1Text": "Seed Heading", "dirty": False}
    assert data["seedState"]["visual"] is True and data["seedState"]["wrapVisible"] is False

    assert data["inMarkdown"]["visual"] is False and data["inMarkdown"]["wrapVisible"] is True
    assert data["inMarkdown"]["textarea"] == SEED
    assert data["backToVisual"]["visual"] is True
    assert data["backToVisual"]["content"] == SEED

    assert data["afterTyping"]["content"] == SEED + "X"
    assert data["afterUndo"]["content"] == SEED

    # Opening and toggling without edits never writes the seed document.
    assert [p for p in data["api"]["puts"] if p["docId"] == "seed-md" and p["body"].get("content") != SEED] == []

    assert data["api"]["creates"][0]["language"] == "markdown"
    assert data["createdInfo"] == {
        "language": "markdown",
        "placeholder": "Start writing, or type '/' for commands...",
        "separatorVisible": True,
        "colorHidden": True,
        "quoteVisible": True,
        "tableVisible": True,
        "underlineHidden": True,
    }

    assert all("Page break" not in label for label in data["slashItems"])
    assert len(data["separatorItems"]) == 1 and "Solid line" in data["separatorItems"][0]
    assert data["sourceText"] == "# My Visual Doc\n\nPlain **bold** text\n\n---\n\nAfter divider"
    assert data["savedAfterVisual"]["content"] == data["sourceText"]
    assert "<u>" not in data["savedAfterVisual"]["richHtml"]

    assert data["pasteResult"]["xssRan"] is False
    assert "javascript:" not in data["pasteResult"]["html"]
    assert "onerror" not in data["pasteResult"]["html"] and "onclick" not in data["pasteResult"]["html"]
    assert "<u>" not in data["pasteResult"]["html"]
    assert "[GoodLink](https://example.com)" in data["pasteResult"]["content"]
    assert "BadLink" in data["pasteResult"]["content"]
    assert "javascript:" not in data["pasteResult"]["content"]


def test_rapid_keyboard_toggles_are_not_swallowed_by_toolbar_duplicate_guard():
    """Regression: a second Ctrl+B within 120 ms of the first was dropped.

    The toolbar's ghost-click guard keyed only on the action name, so a fast
    on/off keyboard toggle left bold active. Keyboard toggles must work in both
    rich-text and visual Markdown documents while duplicate toolbar clicks still
    toggle once.
    """
    data = _run_browser(r"""
      await boot('keyboard-toggle');
      // Freeze the clock the duplicate guard reads, so every toggle below falls
      // inside its 120 ms window regardless of machine speed.
      await page.evaluate(() => { const frozen = Date.now(); Date.now = () => frozen; });
      const results = {};
      for (const [id, language] of [['kbd-rich', 'richtext'], ['kbd-md', 'markdown']]) {
        await open({ id, title: id, language, current_content: '' });
        await page.waitForFunction((id) => window.__state.activeDocId === id, id);
        await page.locator('#doc-email-richbody').click();
        await page.keyboard.type('Plain ');
        await page.keyboard.press('Control+b');
        await page.keyboard.type('bold');
        await page.keyboard.press('Control+b');
        await page.keyboard.type(' text');
        results[id] = await page.evaluate(() => {
          const rich = document.getElementById('doc-email-richbody');
          const bold = Array.from(rich.querySelectorAll('b, strong')).map(el => el.textContent);
          return { bold, text: rich.textContent, content: window.__state.docs.get(window.__state.activeDocId)?.content };
        });
      }

      // Two synchronous toolbar clicks are one ghost click: the word ends up bold.
      // This runs right after the keyboard toggles, which must not arm the guard.
      await open({ id: 'ghost-md', title: 'ghost', language: 'markdown', current_content: 'word' });
      await page.waitForSelector('#doc-email-richbody.markdown-visual-mode p');
      await page.evaluate(() => {
        const p = document.querySelector('#doc-email-richbody p');
        document.getElementById('doc-email-richbody').focus();
        document.getSelection().selectAllChildren(p);
        const button = document.querySelector('[data-md="bold"]');
        button.click();
        button.click();
      });
      results.ghost = await state();
      await finish({ results });
    """)

    for doc_id in ("kbd-rich", "kbd-md"):
        result = data["results"][doc_id]
        assert result["bold"] == ["bold"], result
        assert result["text"].replace(" ", " ") == "Plain bold text"
    assert data["results"]["kbd-md"]["content"] == "Plain **bold** text"
    assert data["results"]["ghost"]["content"] == "**word**"


def test_unsupported_markdown_protection_mode_persistence_switching_ai_update_and_mobile():
    data = _run_browser(r"""
      await boot('visual-protection');
      const unsupportedSource = '---\ntitle: Front Matter Doc\n---\n\n# Safe Title\n\n<div>raw block</div>\n\n$$E=mc^2$$';
      await open({ id: 'doc-unsupported', title: 'Complex Doc', language: 'markdown', current_content: unsupportedSource });
      await page.waitForFunction(() => window.__state.activeDocId === 'doc-unsupported');
      const unsupportedState = { ...(await state()),
        pref: await page.evaluate(() => localStorage.getItem('odysseus-doc-md-editor-mode')),
        visualCompatBlocked: await page.evaluate(() => document.querySelector('[data-mdview="visual"]')?.classList.contains('is-compat-blocked')),
      };

      // Clicking Visual on an unsupported doc must refuse conversion and preserve source
      await page.locator('[data-mdview="visual"]').click();
      const afterAttemptVisual = { ...(await state()), toastText: await page.evaluate(() => document.getElementById('toast').textContent) };

      // Rich Text and code documents never show visual Markdown state.
      await open({ id: 'doc-rich', title: 'Rich Doc', language: 'richtext', current_content: '<p><u>Rich underline</u></p>' });
      await page.waitForSelector('#doc-email-richbody.richtext-mode');
      const richState = await page.evaluate(() => ({
        isRichMode: document.getElementById('doc-email-richbody').classList.contains('richtext-mode'),
        isMdVisualMode: document.getElementById('doc-email-richbody').classList.contains('markdown-visual-mode'),
        bannerVisible: getComputedStyle(document.getElementById('doc-visual-compat-banner')).display !== 'none',
        underlineVisible: getComputedStyle(document.querySelector('[data-md="underline"]')).display !== 'none',
      }));
      await open({ id: 'doc-py', title: 'Python Script', language: 'python', current_content: 'print("hello")' });
      await page.waitForFunction(() => window.__state.activeDocId === 'doc-py');
      const pyState = { ...(await state()), mdToggleHidden: await page.evaluate(() => getComputedStyle(document.getElementById('doc-md-view-toggle')).display === 'none') };

      // AI doc_update re-renders the visual view; an unsupported update falls back.
      await open({ id: 'doc-simple-md', title: 'Simple MD', language: 'markdown', current_content: '## Initial' });
      await page.waitForSelector('#doc-email-richbody.markdown-visual-mode h2');
      await page.evaluate(() => window.__doc.handleDocUpdate({ doc_id: 'doc-simple-md', title: 'Simple MD', language: 'markdown', content: '## Updated by AI\n\n- [x] Verified', version: 2 }));
      const afterCleanAiUpdate = { ...(await state()),
        h2Text: await page.evaluate(() => document.querySelector('#doc-email-richbody h2')?.textContent),
        checkedCount: await page.evaluate(() => document.querySelectorAll('#doc-email-richbody ul.rich-checklist li[data-checked="true"]').length),
      };
      await page.evaluate(() => window.__doc.handleDocUpdate({ doc_id: 'doc-simple-md', title: 'Simple MD', language: 'markdown', content: '# Diagram\n\n```mermaid\ngraph TD;\nA-->B;\n```', version: 3 }));
      const afterMermaidAiUpdate = await state();

      // Mobile layout (390x844) on a visual Markdown document.
      await page.setViewportSize({ width: 390, height: 844 });
      await open({ id: 'doc-mobile-md', title: 'Mobile Spec', language: 'markdown', current_content: '# Mobile Ready\n\nEditable paragraph on mobile.' });
      await page.waitForSelector('#doc-email-richbody.markdown-visual-mode h1');
      const mobileState = await page.evaluate(() => ({
        pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        toggleBox: document.getElementById('doc-md-view-toggle').getBoundingClientRect().toJSON(),
        richBox: document.getElementById('doc-email-richbody').getBoundingClientRect().toJSON(),
      }));

      // An explicit Markdown preference is respected across documents.
      await page.locator('[data-mdview="edit"]').click();
      await open({ id: 'doc-next-md', title: 'Next MD', language: 'markdown', current_content: '# Respect Mode' });
      await page.waitForFunction(() => window.__state.activeDocId === 'doc-next-md');
      const persistedPrefState = { ...(await state()), pref: await page.evaluate(() => localStorage.getItem('odysseus-doc-md-editor-mode')) };

      await finish({ unsupportedState, afterAttemptVisual, richState, pyState, afterCleanAiUpdate, afterMermaidAiUpdate, mobileState, persistedPrefState, puts: api.puts });
    """)

    unsupported_source = "---\ntitle: Front Matter Doc\n---\n\n# Safe Title\n\n<div>raw block</div>\n\n$$E=mc^2$$"
    assert data["unsupportedState"]["visual"] is False
    assert data["unsupportedState"]["wrapVisible"] is True
    assert data["unsupportedState"]["bannerVisible"] is True
    assert "Source mode" in data["unsupportedState"]["bannerText"]
    assert "front matter" in data["unsupportedState"]["bannerText"]
    assert data["unsupportedState"]["pref"] is None
    assert data["unsupportedState"]["visualCompatBlocked"] is True
    assert data["unsupportedState"]["textarea"] == unsupported_source

    assert data["afterAttemptVisual"]["visual"] is False
    assert data["afterAttemptVisual"]["wrapVisible"] is True
    assert data["afterAttemptVisual"]["textarea"] == unsupported_source
    assert data["afterAttemptVisual"]["content"] == unsupported_source
    assert "Markdown mode preserved" in data["afterAttemptVisual"]["toastText"]

    assert data["richState"] == {"isRichMode": True, "isMdVisualMode": False, "bannerVisible": False, "underlineVisible": True}

    assert data["pyState"]["visual"] is False
    assert data["pyState"]["wrapVisible"] is True
    assert data["pyState"]["mdToggleHidden"] is True

    assert data["afterCleanAiUpdate"]["visual"] is True
    assert data["afterCleanAiUpdate"]["h2Text"] == "Updated by AI"
    assert data["afterCleanAiUpdate"]["checkedCount"] == 1
    assert data["afterCleanAiUpdate"]["content"] == "## Updated by AI\n\n- [x] Verified"

    assert data["afterMermaidAiUpdate"]["visual"] is False
    assert data["afterMermaidAiUpdate"]["wrapVisible"] is True
    assert data["afterMermaidAiUpdate"]["bannerVisible"] is True
    assert data["afterMermaidAiUpdate"]["textarea"] == "# Diagram\n\n```mermaid\ngraph TD;\nA-->B;\n```"

    assert data["mobileState"]["pageOverflow"] <= 0
    assert data["mobileState"]["toggleBox"]["width"] > 40
    assert data["mobileState"]["toggleBox"]["right"] <= 390
    assert data["mobileState"]["richBox"]["width"] > 200

    assert data["persistedPrefState"]["pref"] == "markdown"
    assert data["persistedPrefState"]["visual"] is False
    assert data["persistedPrefState"]["wrapVisible"] is True

    # None of the opens, toggles or fallbacks wrote any document.
    assert data["puts"] == []


DATA_INTEGRITY_CASES = [
    # (name, markdown, expected mode, expected Markdown after typing " edited" at the end)
    ("mermaid", "# D\n\n```mermaid\ngraph TD;\nA-->B;\n```", "markdown", None),
    ("math", "Energy $E=mc^2$ here", "markdown", None),
    ("raw-html", '<div align="center">x</div>', "markdown", None),
    ("reference-link", "See [docs][1].\n\n[1]: https://example.com", "markdown", None),
    ("footnote", "Claim[^1]\n\n[^1]: Source", "markdown", None),
    ("complex-table", "| a | b |\n| --- | --- |\n| 1 | 2 | 3 |", "markdown", None),
    ("lazy-list-continuation", "- item\ncontinued", "markdown", None),
    ("relative-link", "[spec](docs/spec.md)", "markdown", None),
    ("app-renderer-disagrees", "__strong__ text", "markdown", None),
    ("nested-list", "# N\n\n1. One\n   - a\n   - b\n2. Two", "visual", "# N\n\n1. One\n   - a\n   - b\n2. Two edited"),
    ("fenced-code", "Intro\n\n```js\nconst x = `y`;\n```", "visual", "Intro\n\n```js\nconst x = `y`; edited\n```"),
    ("escaped-markdown", "Literal \\*stars\\* stay", "visual", "Literal \\*stars\\* stay edited"),
    ("asterisk-list", "* Alpha\n* Beta\n* Gamma", "visual", "* Alpha\n* Beta\n* Gamma edited"),
    ("plus-list", "+ Alpha\n+ Beta\n+ Gamma", "visual", "+ Alpha\n+ Beta\n+ Gamma edited"),
    ("nested-asterisk-list", "# N\n\n1. One\n   * a\n   * b\n2. Two", "visual", "# N\n\n1. One\n   * a\n   * b\n2. Two edited"),
    ("table", "| Name | Score |\n| :--- | ---: |\n| a | 1 |", "visual", "| Name | Score |\n| :--- | ---: |\n| a | 1 edited |"),
    # Mode transitions and localized visual edits retain untouched CRLF bytes.
    ("crlf-line-endings", "# T\r\n\r\nBody\r\n\r\n- a\r\n- b", "visual", "# T\r\n\r\nBody\r\n\r\n- a\r\n- b edited"),
]


def test_markdown_data_integrity_matrix_preserves_untouched_content():
    cases = json.dumps([{"name": n, "md": md, "mode": mode} for n, md, mode, _ in DATA_INTEGRITY_CASES])
    data = _run_browser(r"""
      await boot('visual-integrity');
      const cases = __CASES__;
      const out = {};
      for (const c of cases) {
        const id = `case-${c.name}`;
        await open({ id, title: c.name, language: 'markdown', current_content: c.md });
        await page.waitForFunction((id) => window.__state.activeDocId === id, id);
        const opened = await state();
        const entry = { opened };
        if (opened.visual) {
          await page.locator('[data-mdview="edit"]').click();
          await page.locator('[data-mdview="visual"]').click();
          entry.toggled = await state();
          await caretAtEnd();
          await page.keyboard.type(' edited');
          await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
          entry.edited = await state();
          entry.saved = api.store.get(id)?.current_content;
        }
        out[c.name] = entry;
      }

      // Reload the application and reopen a saved document from the server copy.
      const savedNested = api.store.get('case-nested-list').current_content;
      await boot('visual-integrity-reload');
      await open({ id: 'case-nested-list', title: 'nested', language: 'markdown', current_content: savedNested });
      await page.waitForFunction(() => window.__state.activeDocId === 'case-nested-list');
      const reopened = await state();
      await finish({ out, reopened, savedNested, puts: api.puts });
    """.replace("__CASES__", cases))

    for name, md, mode, edited in DATA_INTEGRITY_CASES:
        entry = data["out"][name]
        opened = entry["opened"]
        assert opened["content"] == md, name
        assert opened["textarea"] == md.replace("\r\n", "\n"), name
        if mode == "markdown":
            assert opened["visual"] is False, name
            assert opened["wrapVisible"] is True, name
            assert opened["bannerVisible"] is True, name
            continue
        assert opened["visual"] is True, name
        assert entry["toggled"]["content"] == md, name
        assert entry["edited"]["content"] == edited, (name, entry["edited"]["content"])
        assert entry["saved"] == edited, name

    # Fallback documents were never written; edited ones were written only with their edit.
    written = {p["docId"] for p in data["puts"]}
    for name, _, mode, _ in DATA_INTEGRITY_CASES:
        assert (f"case-{name}" in written) == (mode == "visual"), name

    assert data["reopened"]["visual"] is True
    assert data["reopened"]["content"] == data["savedNested"]
    assert "<ol><li>One<ul><li>a</li><li>b</li></ul></li><li>Two edited</li></ol>" in data["reopened"]["richHtml"]


def test_visual_lifecycle_switching_history_preview_and_find_replace():
    data = _run_browser(r"""
      await boot('visual-lifecycle');
      await open({ id: 'doc-a', title: 'A', language: 'markdown', current_content: '# Alpha\n\nalpha body' });
      await open({ id: 'doc-b', title: 'B', language: 'markdown', current_content: '# Beta\n\nbeta body' });
      await page.evaluate(() => window.__doc.loadDocument('doc-a'));
      await page.waitForFunction(() => window.__state.activeDocId === 'doc-a' && document.querySelector('#doc-email-richbody h1')?.textContent === 'Alpha');

      // Edit A, then switch to B before A's autosave fires.
      await caretAtEnd();
      await page.keyboard.type(' typed');
      await page.evaluate(() => window.__doc.loadDocument('doc-b'));
      await page.waitForFunction(() => window.__state.activeDocId === 'doc-b' && document.querySelector('#doc-email-richbody h1')?.textContent === 'Beta');
      await page.waitForTimeout(2600);
      const afterSwitch = {
        a: await page.evaluate(() => window.__state.docs.get('doc-a').content),
        b: await page.evaluate(() => window.__state.docs.get('doc-b').content),
        visibleB: await state(),
        puts: api.puts.map(p => ({ docId: p.docId, content: p.body.content })),
      };

      // A read-only visual preview of an old version can never be written into B.
      api.versions.set('doc-b', [
        { version_number: 2, content: '# Beta\n\nbeta body', source: 'user', created_at: '2026-11-01T00:00:00Z' },
        { version_number: 1, content: '# Old Beta\n\nold version body', source: 'user', created_at: '2026-10-01T00:00:00Z' },
      ]);
      await page.locator('.doc-tab-version[data-doc-id="doc-b"]').click();
      await page.waitForSelector('.doc-version-item[data-version="1"]');
      await page.locator('.doc-version-item[data-version="1"]').click();
      await page.waitForFunction(() => document.querySelector('#doc-email-richbody h1')?.textContent === 'Old Beta');
      const preview = await state();
      await page.evaluate(async () => {
        const sync = await import('/static/js/document/ui/richEditor/richBodySync.js');
        sync._syncEmailRichbody(document.getElementById('doc-email-richbody'));
      });
      const afterPreviewSync = await page.evaluate(() => window.__state.docs.get('doc-b').content);
      await page.evaluate(async () => {
        const history = await import('/static/js/document/ui/history/versionHistory.js');
        history._closeVersionPanel();
      });
      await page.waitForFunction(() => document.querySelector('#doc-email-richbody h1')?.textContent === 'Beta');
      const afterPreviewClose = await state();

      // Find & Replace operates on the visual surface, keeps block structure (a
      // leading heading used to absorb the following blocks) and is undoable.
      await open({ id: 'doc-find', title: 'Find', language: 'markdown', current_content: '# Beta\n\nbeta body beta\n\n- beta item' });
      await page.waitForFunction(() => window.__state.activeDocId === 'doc-find');
      await page.locator('#doc-email-richbody').click();
      await page.keyboard.press('Control+h');
      await page.locator('#doc-find-input').fill('beta');
      await page.locator('#doc-replace-input').fill('gamma');
      await page.locator('#doc-replace-all').click();
      const afterReplace = await state();
      await page.locator('#doc-email-richbody').focus();
      for (let i = 0; i < 4; i++) await page.keyboard.press('Control+z');
      const afterReplaceUndo = await state();

      // The same shared code path in a rich-text document.
      await open({ id: 'rt-find', title: 'RT', language: 'richtext', current_content: '<h1>Beta</h1><p>beta body</p>' });
      await page.waitForSelector('#doc-email-richbody.richtext-mode h1');
      await page.locator('#doc-email-richbody').click();
      await page.keyboard.press('Control+h');
      await page.locator('#doc-find-input').fill('beta');
      await page.locator('#doc-replace-input').fill('gamma');
      await page.locator('#doc-replace-all').click();
      const richReplaceHtml = await page.evaluate(() => document.getElementById('doc-email-richbody').innerHTML);

      await finish({ afterSwitch, preview, afterPreviewSync, afterPreviewClose, afterReplace, afterReplaceUndo, richReplaceHtml });
    """)

    switch = data["afterSwitch"]
    assert switch["a"] == "# Alpha\n\nalpha body typed"
    assert switch["b"] == "# Beta\n\nbeta body"
    assert switch["visibleB"]["visual"] is True
    assert {"docId": "doc-a", "content": "# Alpha\n\nalpha body typed"} in switch["puts"]
    assert all(p["content"] == "# Beta\n\nbeta body" for p in switch["puts"] if p["docId"] == "doc-b")

    assert data["preview"]["visual"] is True
    assert data["preview"]["editable"] == "false"
    assert data["afterPreviewSync"] == "# Beta\n\nbeta body"
    assert data["afterPreviewClose"]["visual"] is True
    assert data["afterPreviewClose"]["editable"] == "true"
    assert data["afterPreviewClose"]["content"] == "# Beta\n\nbeta body"

    assert data["afterReplace"]["visual"] is True
    assert data["afterReplace"]["content"] == "# gamma\n\ngamma body gamma\n\n- gamma item"
    assert data["afterReplaceUndo"]["content"] == "# Beta\n\nbeta body beta\n\n- beta item"
    assert data["richReplaceHtml"] == "<h1>gamma</h1><p>gamma body</p>"


def test_block_shortcuts_exit_quotes_and_keep_adjacent_list_types():
    """Regressions found in the running app.

    `[] ` typed on the line after a bullet list converted the whole bullet list
    into a checklist (the browser merges the new item into the adjacent list),
    and Enter on an empty quote line created more empty quotes instead of
    leaving the quote.
    """
    data = _run_browser(r"""
      await boot('block-shortcuts');
      const out = {};
      for (const [id, language] of [['blocks-md', 'markdown'], ['blocks-rt', 'richtext']]) {
        await open({ id, title: id, language, current_content: '' });
        await page.waitForFunction((id) => window.__state.activeDocId === id, id);
        await page.locator('#doc-email-richbody').click();
        const k = page.keyboard;
        await k.type('# Plan'); await k.press('Enter');
        await k.type('- '); await k.type('First'); await k.press('Enter'); await k.type('Second'); await k.press('Enter'); await k.press('Enter');
        await k.type('[] '); await k.type('Task'); await k.press('Enter'); await k.press('Enter');
        if (language === 'markdown') {
          await k.type('> '); await k.type('Quote'); await k.press('Enter'); await k.press('Enter');
        }
        await k.type('After');
        out[id] = await page.evaluate(() => ({
          html: document.getElementById('doc-email-richbody').innerHTML,
          content: window.__state.docs.get(window.__state.activeDocId)?.content,
          bullets: Array.from(document.querySelectorAll('#doc-email-richbody ul:not(.rich-checklist) > li')).map(li => li.textContent),
          tasks: Array.from(document.querySelectorAll('#doc-email-richbody ul.rich-checklist > li')).map(li => li.textContent),
        }));
      }
      await finish({ out });
    """)

    md = data["out"]["blocks-md"]
    assert md["content"] == "# Plan\n\n- First\n- Second\n\n- [ ] Task\n\n> Quote\n\nAfter", md
    rt = data["out"]["blocks-rt"]
    assert rt["bullets"] == ["First", "Second"], rt
    assert rt["tasks"] == ["Task"], rt


def test_checklist_toggle_after_divider_keeps_the_list_and_caret():
    data = _run_browser(r"""
      await boot('task-after-divider');
      await open({ id: 'task-md', title: 'Task', language: 'markdown', current_content: '' });
      const rich = page.locator('#doc-email-richbody.markdown-visual-mode');
      await rich.click();
      await page.keyboard.type('Keep this paragraph');
      await page.keyboard.press('Enter');
      await page.locator('[data-dd="separator"]').click();
      await page.getByText('Solid line', { exact: true }).click();
      await page.keyboard.type('[] Review the scope');
      const before = await state();
      await page.keyboard.press('Control+Enter');
      const after = await state();
      const caret = await page.evaluate(() => {
        const selection = getSelection();
        return { text: selection.anchorNode?.textContent, offset: selection.anchorOffset };
      });
      const saved = await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      await finish({ before, after, caret, saved, persisted: api.store.get('task-md').current_content });
    """)
    assert data["before"]["content"] == "Keep this paragraph\n\n---\n\n- [ ] Review the scope"
    assert data["after"]["content"] == data["persisted"] == "Keep this paragraph\n\n---\n\n- [x] Review the scope"
    assert '<ul class="rich-checklist">' in data["after"]["richHtml"]
    assert data["caret"] == {"text": "Review the scope", "offset": len("Review the scope")}
    assert data["saved"] is True


def test_streamed_ai_updates_never_leave_a_stale_visual_surface():
    """A streamed AI document must render its final content in the visual editor.

    The visual surface stayed on the previous document during streaming, and the
    finalizing update skipped the visual re-render. Saving then wrote the stale
    baseline back over the streamed content.
    """
    data = _run_browser(r"""
      await boot('visual-streaming');
      // 1. Stream a new document while a visual document is open.
      await open({ id: 'doc-a', title: 'A', language: 'markdown', current_content: '# A\n\nalpha' });
      await page.waitForSelector('#doc-email-richbody.markdown-visual-mode h1');
      await page.evaluate(() => {
        window.__doc.streamDocOpen('Streamed', 'markdown');
        window.__doc.streamDocDelta('# Streamed\n\npartial');
      });
      const duringStream = await state();
      await page.evaluate(() => window.__doc.handleDocUpdate({ doc_id: 'streamed-1', title: 'Streamed', language: 'markdown', content: '# Streamed\n\nfinal body', version: 1 }));
      const afterNew = { ...(await state()), h1: await page.evaluate(() => document.querySelector('#doc-email-richbody h1')?.textContent) };
      await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      const aContent = await page.evaluate(() => window.__state.docs.get('doc-a')?.content);

      // 2. Stream an update into the document that is open in the visual editor.
      await open({ id: 'doc-b', title: 'B', language: 'markdown', current_content: '# Old\n\nold body' });
      await page.waitForFunction(() => document.querySelector('#doc-email-richbody h1')?.textContent === 'Old');
      await page.evaluate(() => {
        window.__doc.streamDocOpen('B', 'markdown');
        window.__doc.streamDocDelta('# New\n\nnew');
      });
      await page.evaluate(() => window.__doc.handleDocUpdate({ doc_id: 'doc-b', title: 'B', language: 'markdown', content: '# New\n\nnew body', version: 2 }));
      const afterSame = { ...(await state()), h1: await page.evaluate(() => document.querySelector('#doc-email-richbody h1')?.textContent) };
      await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      await caretAtEnd();
      await page.keyboard.type(' typed');
      const afterTyping = await state();
      await finish({ duringStream, afterNew, aContent, afterSame, afterTyping, puts: api.puts.map(p => ({ docId: p.docId, content: p.body.content })) });
    """)

    assert data["duringStream"]["visual"] is False, "streaming shows the live source, not the previous document"
    assert data["duringStream"]["textarea"] == "# Streamed\n\npartial"
    assert data["afterNew"]["visual"] is True
    assert data["afterNew"]["h1"] == "Streamed"
    assert data["afterNew"]["content"] == "# Streamed\n\nfinal body"
    assert data["aContent"] == "# A\n\nalpha"
    assert {"docId": "streamed-1", "content": "# Streamed\n\nfinal body"} in data["puts"]
    assert data["afterSame"]["visual"] is True
    assert data["afterSame"]["h1"] == "New"
    assert data["afterTyping"]["content"] == "# New\n\nnew body typed"
    assert all(p["content"] in ("# New\n\nnew body", "# New\n\nnew body typed") for p in data["puts"] if p["docId"] == "doc-b"), data["puts"]
    assert all(p["docId"] != "doc-a" or p["content"] == "# A\n\nalpha" for p in data["puts"])


def test_visual_adjacent_and_nested_formatting_chromium_roundtrip():
    """Verify adjacent and nested formatting round-trip fidelity in Chromium.

    Covers:
    1. Italic immediately followed by bold (*foo***bar** <-> <em>foo</em><strong>bar</strong>)
    2. Bold immediately followed by italic (**foo***bar* <-> <strong>foo</strong><em>bar</em>)
    3. Adjacent formatting in both directions (*a***b***c* and **x***y***z**)
    4. Nested formatting (*foo **bar** baz*, **foo *bar* baz**, ***both***)
    5. Formatting adjacent to inline code and links
    6. Literal asterisks and backticks
    7. Keyboard typing of adjacent formatting runs in Chromium
    8. Save, reload, and reopen in Chromium verifying balanced DOM structure
    9. No-op mode switching preserving exact original Markdown
    """
    data = _run_browser(r"""
      await boot('visual-formatting');
      const seedContent = [
        '# Formatting Corpus',
        '',
        '*foo***bar** and **bold***italic*',
        '',
        '*a***b***c* and **x***y***z**',
        '',
        '*foo **bar** baz* and **foo *bar* baz** and ***both***',
        '',
        '*code*`span` and `code`**strong**',
        '',
        '*link*[item](https://example.com) and [item](https://example.com)**bold**',
        '',
        'Literal \\*stars\\* and \\`code\\`',
        '',
        'foo**~~bar~~** and foo*~~baz~~* and foo~~**bolddel**~~',
        '',
        'Plain text end',
      ].join('\n');

      await open({ id: 'fmt-md', title: 'Formatting', language: 'markdown', current_content: seedContent });
      await page.waitForSelector('#doc-email-richbody.markdown-visual-mode h1');

      // 1. Verify DOM structure rendered from the seed
      const initialDom = await page.evaluate(() => {
        const rich = document.getElementById('doc-email-richbody');
        const paras = Array.from(rich.querySelectorAll('p'));
        return paras.map(p => ({
          html: p.innerHTML,
          hasEm: Boolean(p.querySelector('em')),
          hasStrong: Boolean(p.querySelector('strong')),
          hasDel: Boolean(p.querySelector('del')),
          emTexts: Array.from(p.querySelectorAll('em')).map(e => e.textContent),
          strongTexts: Array.from(p.querySelectorAll('strong')).map(s => s.textContent),
          delTexts: Array.from(p.querySelectorAll('del')).map(d => d.textContent),
          adjacentP: paras[0] ? {
            firstEmNextSibling: paras[0].querySelector('em')?.nextSibling?.nodeName,
            firstStrongPrevSibling: paras[0].querySelector('strong')?.previousSibling?.nodeName,
          } : null,
        }));
      });

      // 2. Mode toggle without edits: Visual -> Markdown -> Visual keeps exact source
      await page.locator('[data-mdview="edit"]').click();
      const inMarkdown = await state();
      await page.locator('[data-mdview="visual"]').click();
      const backToVisual = await state();

      // 3. Type adjacent formatting runs via keyboard: Control+i 'alpha' Control+i Control+b 'beta' Control+b
      await caretAtEnd();
      await page.keyboard.press('Enter');
      await page.keyboard.press('Control+i');
      await page.keyboard.type('alpha');
      await page.keyboard.press('Control+i');
      await page.keyboard.press('Control+b');
      await page.keyboard.type('beta');
      await page.keyboard.press('Control+b');

      // 4. Type nested bold + italic in a new paragraph
      await caretAtEnd();
      await page.keyboard.press('Enter');
      if (await page.evaluate(() => document.queryCommandState('bold'))) {
        await page.keyboard.press('Control+b');
      }
      await page.keyboard.type('plainstart ');
      await page.keyboard.press('Control+b');
      await page.keyboard.type('outerbold ');
      await page.keyboard.press('Control+i');
      await page.keyboard.type('inneritalic');
      await page.keyboard.press('Control+i');
      await page.keyboard.press('Control+b');

      // 5. Save document
      await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      const afterSave = await state();
      const savedContent = api.store.get('fmt-md')?.current_content;

      // 6. Reload application and reopen from server copy
      await boot('visual-formatting-reloaded');
      await open({ id: 'fmt-md', title: 'Formatting', language: 'markdown', current_content: savedContent });
      await page.waitForSelector('#doc-email-richbody.markdown-visual-mode h1');
      const reloadedDom = await page.evaluate(() => {
        const rich = document.getElementById('doc-email-richbody');
        const paras = Array.from(rich.querySelectorAll('p'));
        const adjacentP = paras[paras.length - 2];
        const nestedP = paras[paras.length - 1];
        return {
          richHtml: rich.innerHTML,
          adjacentHtml: adjacentP?.innerHTML,
          nestedHtml: nestedP?.innerHTML,
          emTexts: adjacentP ? Array.from(adjacentP.querySelectorAll('em')).map(e => e.textContent) : [],
          strongTexts: adjacentP ? Array.from(adjacentP.querySelectorAll('strong')).map(s => s.textContent) : [],
          nestedHasStrong: Boolean(nestedP?.querySelector('strong, b')),
          nestedHasEm: Boolean(nestedP?.querySelector('em, i')),
          nestedHasDel: Boolean(nestedP?.querySelector('del, s, strike')),
          nestedText: nestedP?.textContent,
        };
      });

      // 7. Inject unrepresentable formatting (nested links created via DOM) and confirm save is safely refused
      await page.evaluate(() => {
        const rich = document.getElementById('doc-email-richbody');
        const p = document.createElement('p');
        p.id = 'unsupported-para';
        const aOuter = document.createElement('a');
        aOuter.href = 'https://outer.com';
        aOuter.appendChild(document.createTextNode('outer '));
        const aInner = document.createElement('a');
        aInner.href = 'https://inner.com';
        aInner.appendChild(document.createTextNode('inner'));
        aOuter.appendChild(aInner);
        p.appendChild(aOuter);
        rich.appendChild(p);
      });
      const blockedSaveSuccess = await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      const stateAfterBlocked = await state();
      const serverContentAfterBlocked = api.store.get('fmt-md')?.current_content;
      const docSaveState = await page.evaluate(() => window.__state.docs.get('fmt-md')?._saveState);

      await finish({
        initialDom,
        inMarkdown,
        backToVisual,
        afterSave,
        savedContent,
        reloadedDom,
        blockedSaveSuccess,
        stateAfterBlocked,
        serverContentAfterBlocked,
        docSaveState,
      });
    """)

    seed = (
        "# Formatting Corpus\n\n"
        "*foo***bar** and **bold***italic*\n\n"
        "*a***b***c* and **x***y***z**\n\n"
        "*foo **bar** baz* and **foo *bar* baz** and ***both***\n\n"
        "*code*`span` and `code`**strong**\n\n"
        "*link*[item](https://example.com) and [item](https://example.com)**bold**\n\n"
        "Literal \\*stars\\* and \\`code\\`\n\n"
        "foo**~~bar~~** and foo*~~baz~~* and foo~~**bolddel**~~\n\n"
        "Plain text end"
    )

    # Initial DOM structure check
    init = data["initialDom"]
    # Para 0: *foo***bar** and **bold***italic*
    assert "<em>foo</em><strong>bar</strong>" in init[0]["html"]
    assert "<strong>bold</strong><em>italic</em>" in init[0]["html"]
    assert init[0]["adjacentP"]["firstEmNextSibling"] == "STRONG"
    assert "<em>foo<strong>" not in init[0]["html"], "Browser must not contain corrupt tag nesting"

    # Para 1: *a***b***c* and **x***y***z**
    assert "<em>a</em><strong>b</strong><em>c</em>" in init[1]["html"]
    assert "<strong>x</strong><em>y</em><strong>z</strong>" in init[1]["html"]

    # Para 2: nested formatting
    assert "<em>foo <strong>bar</strong> baz</em>" in init[2]["html"]
    assert "<strong>foo <em>bar</em> baz</strong>" in init[2]["html"]
    assert "<strong><em>both</em></strong>" in init[2]["html"] or "<em><strong>both</strong></em>" in init[2]["html"]

    # Para 3: formatting adjacent to code
    assert "<em>code</em><code>span</code>" in init[3]["html"]
    assert "<code>code</code><strong>strong</strong>" in init[3]["html"]

    # Para 4: formatting adjacent to links
    assert "<em>link</em><a " in init[4]["html"]
    assert "</a><strong>bold</strong>" in init[4]["html"]

    # Para 5: literal asterisks and backticks
    assert "Literal *stars* and `code`" in init[5]["html"]

    # Para 6: nested bold, italic, and strikethrough combinations
    assert "<strong><del>bar</del></strong>" in init[6]["html"]
    assert "<em><del>baz</del></em>" in init[6]["html"]
    assert "<del><strong>bolddel</strong></del>" in init[6]["html"]
    assert "**<del>" not in init[6]["html"]
    assert "*<del>" not in init[6]["html"]

    # Mode switching preserves original markdown exactly
    assert data["inMarkdown"]["textarea"] == seed
    assert data["backToVisual"]["content"] == seed

    # Saved content has the keyboard-typed adjacent and nested formatting
    saved = data["savedContent"]
    assert "*alpha***beta**" in saved or ("*alpha*" in saved and "**beta**" in saved)
    assert "**outerbold *inneritalic***" in saved or ("outerbold" in saved and "inneritalic" in saved)

    # Reloaded DOM has valid sibling and nested elements
    reloaded = data["reloadedDom"]
    assert "alpha" in reloaded["emTexts"]
    assert "beta" in reloaded["strongTexts"]
    assert "<em>alpha</em><strong>beta</strong>" in reloaded["adjacentHtml"]
    assert reloaded["nestedHasStrong"] is True
    assert reloaded["nestedHasEm"] is True
    assert "inneritalic" in reloaded["nestedText"]

    # Confirms that unsupported edits cannot silently overwrite stored content
    assert data["blockedSaveSuccess"] is False, "Save of unrepresentable formatting must be refused"
    assert data["serverContentAfterBlocked"] == saved, "Server stored content must not be overwritten"
    assert data["docSaveState"] == "blocked", "Document save state must distinguish a blocked draft"
    assert data["stateAfterBlocked"]["bannerVisible"] is True, "Warning banner must be shown to user"
    assert "outer" in data["stateAfterBlocked"]["richHtml"]
    assert "inner" in data["stateAfterBlocked"]["richHtml"]


def test_blocked_visual_edit_recovery_lifecycle_and_markdown_save():
    """Verify that a blocked visual draft preserves unsaved edits across transitions,
    prevents destructive loss, and allows saving valid Markdown after explicit resolution.

    Covers:
    1. Unsupported visual formatting followed by a failed save.
    2. Attempting to switch from blocked Visual mode to Markdown is safely prevented
       (destructive transition refused, edits remain visible).
    3. Switching documents preserves the unresolved visual draft upon return.
    4. Closing and reopening the panel preserves the unresolved visual draft.
    5. Explicit resolution via 'Discard visual changes' clears the blocked state
       and restores valid baseline Markdown.
    6. Switching to Markdown and saving valid Markdown succeeds after resolution.
    7. No accidental PUT containing stale content.
    8. No false successful-save notifications.
    """
    data = _run_browser(r"""
      await boot('visual-recovery');
      await open({ id: 'doc-1', title: 'Doc 1', language: 'markdown', current_content: '# Doc 1\n\nClean paragraph' });
      await open({ id: 'doc-2', title: 'Doc 2', language: 'markdown', current_content: '# Doc 2\n\nDoc 2 paragraph' });

      await page.evaluate(() => window.__doc.loadDocument('doc-1'));
      await page.waitForSelector('#doc-email-richbody.markdown-visual-mode h1');

      // 1. Inject unrepresentable formatting into doc-1
      await page.evaluate(() => {
        const rich = document.getElementById('doc-email-richbody');
        const p = document.createElement('p');
        p.id = 'unsupported-para';
        p.innerHTML = '<code>foo <strong>bar</strong> baz</code>';
        rich.appendChild(p);
      });

      // Failed save
      const save1 = await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      const state1 = await state();
      const server1 = api.store.get('doc-1')?.current_content;
      const blocked1 = await page.evaluate(() => window.__state.docs.get('doc-1')?._visualSyncBlocked);

      // 2. Finding 1: Attempt to switch to Markdown mode while blocked
      await page.locator('[data-mdview="edit"]').click();
      const stateAfterSwitchAttempt = await state();
      const blockedAfterSwitchAttempt = await page.evaluate(() => window.__state.docs.get('doc-1')?._visualSyncBlocked);

      // 3. Document switching: switch to doc-2 then back to doc-1
      await page.evaluate(() => window.__doc.loadDocument('doc-2'));
      const stateDoc2 = await state();
      await page.evaluate(() => window.__doc.loadDocument('doc-1'));
      const stateDoc1Restored = await state();
      const blockedDoc1Restored = await page.evaluate(() => window.__state.docs.get('doc-1')?._visualSyncBlocked);

      // 4. Panel close and reopen: closePanel then loadDocument('doc-1')
      await page.evaluate(() => window.__doc.closePanel('down'));
      const stateClosed = await state();
      await page.evaluate(() => {
        window.__doc.openPanel();
        window.__doc.loadDocument('doc-1');
      });
      await page.waitForFunction(() => {
        const pane = document.getElementById('doc-editor-pane');
        return pane && getComputedStyle(pane).opacity !== '0';
      });
      const stateReopened = await state();

      // 5. Explicit resolution: click 'Discard visual changes' in the banner
      const discardResult = await page.evaluate(() => {
        const btn = document.querySelector('[data-action="discard-visual-draft"]');
        if (!btn) return { btnFound: false };
        btn.click();
        const doc = window.__state.docs.get('doc-1');
        return { btnFound: true, docBlocked: doc?._visualSyncBlocked, activeDocId: window.__state.activeDocId };
      });
      const discardBtnExists = discardResult.btnFound;
      const stateAfterDiscard = await state();
      const blockedAfterDiscard = await page.evaluate(() => window.__state.docs.get('doc-1')?._visualSyncBlocked);

      // 6. Finding 2: Switch to Markdown and save valid Markdown edits
      await page.evaluate(() => {
        const editBtn = document.querySelector('[data-mdview="edit"]');
        editBtn?.click();
      });
      const stateInMarkdown = await state();
      await page.evaluate(() => {
        const ta = document.getElementById('doc-editor-textarea');
        ta.value = '# Doc 1\n\nValid Markdown edit after recovery';
        ta.dispatchEvent(new Event('input', { bubbles: true }));
      });
      const save2 = await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      const stateAfterSave2 = await state();
      const server2 = api.store.get('doc-1')?.current_content;
      const docSaveState2 = await page.evaluate(() => window.__state.docs.get('doc-1')?._saveState);

      await finish({
        save1,
        state1,
        server1,
        blocked1,
        stateAfterSwitchAttempt,
        blockedAfterSwitchAttempt,
        stateDoc2,
        stateDoc1Restored,
        blockedDoc1Restored,
        stateClosed,
        stateReopened,
        discardResult,
        discardBtnExists,
        stateAfterDiscard,
        blockedAfterDiscard,
        stateInMarkdown,
        save2,
        stateAfterSave2,
        server2,
        docSaveState2,
        putCount: api.puts.length,
      });
    """)

    # 1. Failed save while blocked
    assert data["save1"] is False
    assert data["blocked1"] is True
    assert data["state1"]["bannerVisible"] is True
    assert data["server1"] == "# Doc 1\n\nClean paragraph"

    # 2. Finding 1: Switch attempt must be prevented; visual edits must not disappear
    assert data["stateAfterSwitchAttempt"]["visual"] is True, "Visual mode must be preserved when switch is blocked"
    assert data["stateAfterSwitchAttempt"]["wrapVisible"] is False
    assert "<strong>bar</strong>" in data["stateAfterSwitchAttempt"]["richHtml"], "Unsaved visual edits must not disappear"
    assert data["blockedAfterSwitchAttempt"] is True

    # 3. Switching documents preserves visual draft upon return
    assert data["stateDoc2"]["activeId"] == "doc-2"
    assert data["stateDoc2"]["bannerVisible"] is False, "Banner from doc-1 must not leak into doc-2"
    assert data["stateDoc1Restored"]["activeId"] == "doc-1"
    assert data["stateDoc1Restored"]["visual"] is True
    assert "<strong>bar</strong>" in data["stateDoc1Restored"]["richHtml"], "Visual draft must survive document switching"
    assert data["stateDoc1Restored"]["bannerVisible"] is True
    assert data["blockedDoc1Restored"] is True

    # 4. Closing and reopening preserves visual draft
    assert data["stateClosed"]["wrapVisible"] is False
    assert data["stateReopened"]["activeId"] == "doc-1"
    assert data["stateReopened"]["visual"] is True
    assert "<strong>bar</strong>" in data["stateReopened"]["richHtml"], "Visual draft must survive panel close/reopen"
    assert data["stateReopened"]["bannerVisible"] is True

    # 5. Explicit resolution clears blocked state and restores clean baseline
    assert data["blockedAfterDiscard"] is False, "Discarding visual draft must unblock document"
    assert data["stateAfterDiscard"]["bannerVisible"] is False
    assert "<strong>bar</strong>" not in data["stateAfterDiscard"]["richHtml"], "Discarded edits must be removed from visual DOM"

    # 6. Finding 2: Can switch to Markdown and save valid Markdown edits
    assert data["stateInMarkdown"]["visual"] is False
    assert data["stateInMarkdown"]["wrapVisible"] is True
    assert data["save2"] is True, "Valid Markdown save must succeed after resolving visual conflict"
    assert data["server2"] == "# Doc 1\n\nValid Markdown edit after recovery"
    assert data["docSaveState2"] == "saved"
    assert data["putCount"] == 1, "Only the single valid save after recovery should issue a PUT"


def test_visual_markdown_preserves_noncanonical_blocks_byte_identical_after_saving():
    """Verify that editing a single block in a non-canonical Markdown document leaves

    every other block byte-identical after saving and reopening across fresh browser sessions.
    Covering:
    - Non-canonical list markers (* instead of -)
    - Custom table column padding and alignment
    - Code fence delimiters (~~~ instead of ```)
    - Untouched surrounding blocks preserved byte-for-byte
    """
    raw_content = (
        "* Non-canonical list marker\n"
        "\n"
        "| Custom | Table | Padding |\n"
        "|:-------|:-----:|--------:|\n"
        "| Alpha  |  123  |     456 |\n"
        "\n"
        "Original paragraph.\n"
        "\n"
        "~~~python\n"
        "# Tilde fence delimiter\n"
        "print(\"Hello\")\n"
        "~~~\n"
    )
    doc_json = json.dumps({"raw": raw_content})

    data = _run_browser(r"""
      await boot('noncanonical-preservation');
      const docData = __DOC_JSON__;
      const id = 'noncanonical-doc';
      await open({ id, title: 'Non-canonical Test', language: 'markdown', current_content: docData.raw });
      await page.waitForFunction((id) => window.__state.activeDocId === id, id);
      const opened = await state();

      // Edit ONLY the paragraph block in the visual editor
      await page.evaluate(() => {
        const rich = document.getElementById('doc-email-richbody');
        const p = Array.from(rich.querySelectorAll('p')).find(el => el.textContent.includes('Original paragraph'));
        if (p) {
          p.textContent = 'Updated paragraph.';
        }
      });
      const savedOk = await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      const afterSave = await state();
      const serverSaved = api.store.get(id)?.current_content;

      // Reopen in a fresh browser session to ensure persistence round-trips byte-for-byte
      await boot('noncanonical-reopen');
      await open({ id, title: 'Non-canonical Test', language: 'markdown', current_content: serverSaved });
      await page.waitForFunction((id) => window.__state.activeDocId === id, id);
      const reopened = await state();

      await finish({ opened, savedOk, afterSave, serverSaved, reopened });
    """.replace("__DOC_JSON__", doc_json))

    assert data["opened"]["visual"] is True, "Document with non-canonical list, table and tilde fences must open in Visual mode"
    assert data["savedOk"] is True, "Saving edited visual document must succeed"

    server_content = data["serverSaved"]
    assert "Updated paragraph." in server_content
    assert "Original paragraph." not in server_content

    assert "* Non-canonical list marker" in server_content, "List marker * must not be rewritten to -"
    assert "- Non-canonical list marker" not in server_content

    table_expected = (
        "| Custom | Table | Padding |\n"
        "|:-------|:-----:|--------:|\n"
        "| Alpha  |  123  |     456 |"
    )
    assert table_expected in server_content, "Custom table formatting and padding must be byte-identical"

    code_expected = (
        "~~~python\n"
        "# Tilde fence delimiter\n"
        "print(\"Hello\")\n"
        "~~~"
    )
    assert code_expected in server_content, "Tilde code fence must not be rewritten to backticks"
    assert "```python" not in server_content

    assert data["reopened"]["visual"] is True
    assert data["reopened"]["content"] == server_content


def test_visual_markdown_interaction_quality_shortcuts_and_caret_mapping():
    """Verify heading backspace, list soft breaks, and caret restoration across mode switches."""
    data = _run_browser(r"""
      await boot('interaction-quality');
      const id = 'interaction-doc';
      await open({
        id,
        title: 'Interaction Test',
        language: 'markdown',
        current_content: '# Heading One\n\n- First Item\n\nParagraph block',
      });
      await page.waitForFunction((id) => window.__state.activeDocId === id, id);
      const initial = await state();

      // 1. Backspace at offset 0 in heading converts it to <p> while retaining text
      await page.evaluate(() => {
        const rich = document.getElementById('doc-email-richbody');
        const h1 = rich.querySelector('h1');
        const range = document.createRange();
        range.selectNodeContents(h1);
        range.collapse(true);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        rich.focus();
      });
      await page.keyboard.press('Backspace');
      await page.waitForTimeout(50);
      const afterHeadingBackspace = await page.evaluate(() => {
        const rich = document.getElementById('doc-email-richbody');
        return {
          h1Count: rich.querySelectorAll('h1').length,
          firstPText: rich.querySelector('p')?.textContent,
          firstPTag: rich.firstElementChild?.tagName,
        };
      });

      // 2. Shift+Enter in list item inserts <br> soft break rather than splitting list item
      await page.evaluate(() => {
        const rich = document.getElementById('doc-email-richbody');
        const li = rich.querySelector('li');
        const range = document.createRange();
        range.selectNodeContents(li);
        range.collapse(false);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        rich.focus();
      });
      await page.keyboard.press('Shift+Enter');
      await page.keyboard.type('Subline text');
      await page.waitForTimeout(50);
      const afterListShiftEnter = await page.evaluate(() => {
        const rich = document.getElementById('doc-email-richbody');
        const lis = Array.from(rich.querySelectorAll('li'));
        return {
          liCount: lis.length,
          liHtml: lis[0]?.innerHTML,
          hasBr: Boolean(lis[0]?.querySelector('br')),
        };
      });

      // 3. Caret mapping across Visual -> Source -> Visual
      // Place caret in the Paragraph block (3rd block)
      await page.evaluate(() => {
        const rich = document.getElementById('doc-email-richbody');
        const p = Array.from(rich.querySelectorAll('p')).find(el => el.textContent.includes('Paragraph block'));
        const range = document.createRange();
        range.selectNodeContents(p);
        range.collapse(true);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        rich.focus();
      });

      // Switch to Source mode
      await page.locator('[data-mdview="edit"]').click();
      await page.waitForTimeout(50);
      const inSource = await page.evaluate(() => {
        const ta = document.getElementById('doc-editor-textarea');
        const selStart = ta.selectionStart;
        const textBefore = ta.value.slice(0, selStart);
        return {
          selStart,
          textBefore,
          inParagraphBlock: ta.value.indexOf('Paragraph block') <= selStart,
        };
      });

      // Switch back to Visual mode
      await page.locator('[data-mdview="visual"]').click();
      await page.waitForTimeout(50);
      const backInVisual = await page.evaluate(() => {
        const rich = document.getElementById('doc-email-richbody');
        const sel = window.getSelection();
        const anchorNode = sel?.anchorNode;
        const anchorEl = anchorNode?.nodeType === Node.ELEMENT_NODE ? anchorNode : anchorNode?.parentElement;
        return {
          visualActive: document.querySelector('[data-mdview="visual"]')?.classList.contains('active'),
          anchorTag: anchorEl?.tagName,
          anchorText: anchorEl?.textContent,
        };
      });

      await finish({ initial, afterHeadingBackspace, afterListShiftEnter, inSource, backInVisual });
    """)
    assert data["afterHeadingBackspace"]["h1Count"] == 0
    assert data["afterHeadingBackspace"]["firstPTag"] == "P"
    assert "Heading One" in data["afterHeadingBackspace"]["firstPText"]

    assert data["afterListShiftEnter"]["liCount"] == 1
    assert data["afterListShiftEnter"]["hasBr"] is True
    assert "Subline text" in data["afterListShiftEnter"]["liHtml"]

    assert data["inSource"]["inParagraphBlock"] is True
    assert data["backInVisual"]["visualActive"] is True
