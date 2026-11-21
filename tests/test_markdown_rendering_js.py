"""Regression coverage for the browser markdown renderer."""

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HAS_NODE = shutil.which("node") is not None


@pytest.fixture(scope="module")
def node_available():
    if not _HAS_NODE:
        pytest.skip("node binary not on PATH")


def test_blockquoted_html_codefence_does_not_leak_placeholders(node_available):
    result = subprocess.run(
        ["node", "tests/markdown_codefence_placeholder_regression.mjs"],
        cwd=_REPO, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_markdown_renderer_modes_stay_independent(node_available):
    result = subprocess.run(
        ["node", "--test", "tests/markdownRendererModes.test.mjs"],
        cwd=_REPO, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _run_markdown_case(markdown: str, render_expr: str = "mod.mdToHtml(input)", with_katex: bool = False):
    script = textwrap.dedent(
        r"""
        import fs from 'node:fs';

        globalThis.window = { location: { origin: 'http://localhost' }, katex: null };
        if (__WITH_KATEX__) {
          // Minimal stand-in for the CDN katex global: wraps the source so tests
          // can assert what was (or wasn't) handed to KaTeX.
          const katexStub = {
            renderToString(src, opts) {
              const display = !!(opts && opts.displayMode);
              return `<span class="katex" data-display="${display}">${src}</span>`;
            },
          };
          globalThis.window.katex = katexStub;
          globalThis.katex = katexStub;
        }
        globalThis.document = {
          readyState: 'loading',
          addEventListener() {},
          createElement(tag) {
            if (tag !== 'template') throw new Error(`unsupported element: ${tag}`);
            return {
              _html: '',
              content: { querySelectorAll() { return []; } },
              set innerHTML(value) { this._html = value; },
              get innerHTML() { return this._html; },
            };
          },
        };
        globalThis.MutationObserver = class { observe() {} };

        let source = fs.readFileSync('./static/js/markdown.js', 'utf8');
        source = source.replace(/from '(\.\/shared\/domain\/[^']+)'/g, (_match, specifier) =>
          `from '${new URL(specifier, import.meta.resolve('./static/js/')).href}'`);
        source = source.replace(
          /import uiModule from ['"]\.\/ui\.js(?:[?#][^'"]*)?['"];?/,
          ''
        );
        source = source.replace(
          /import \{ splitTableRow \} from ['"]\.\/markdown\/tableRow\.js['"];/,
          `function splitTableRow(row) {
            return (row || '').replace(/^\\s*\\|/, '').replace(/\\|\\s*$/, '').split('|').map(c => c.trim());
          }`
        );
        // markdown.js imports the emoji-shortcode helpers relatively (issue #345),
        // which a data: URL module can't resolve. Inline the REAL helpers (minus
        // their export keywords) so the renderer's shortcode pass behaves exactly
        // as it does in the browser.
        const emojiSource = fs.readFileSync('./static/js/emojiShortcodes.js', 'utf8')
          .replace(/^export default .*$/m, '')
          .replace(/export const /g, 'const ')
          .replace(/export function /g, 'function ');
        source = source.replace(
          /import \{ replaceEmojiShortcodes, hasEmojiShortcode \} from ['"]\.\/emojiShortcodes\.js['"];/,
          () => emojiSource
        );
        source = source.replace(
          /var escapeHtml = uiModule\.esc;/,
          `var escapeHtml = (value) => String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');`
        );

        const moduleUrl = 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
        const mod = await import(moduleUrl);
        const input = JSON.parse(process.argv[1]);
        console.log(JSON.stringify({ html: __RENDER_EXPR__ }));
        """
    ).replace("__RENDER_EXPR__", render_expr).replace(
        "__WITH_KATEX__", "true" if with_katex else "false"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(markdown)],
        cwd=_REPO,
        capture_output=True,
        timeout=15,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed:\nSTDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}")
    return json.loads(result.stdout.splitlines()[-1])["html"]


def _run_svg_case(markdown: str):
    # SVG title extraction requires a real inert DOM, not the Node template stub.
    script = r'''
      const { chromium } = require('playwright');
      (async () => {
        const browser = await chromium.launch({ headless: true });
        try {
          const page = await browser.newPage();
          await page.goto(process.env.ODYSSEUS_TEST_STATIC_ORIGIN + '/static/js/documentStats.js');
          await page.setContent('<div id="toast"></div><div id="sidebar"></div>');
          const html = await page.evaluate(async input => {
            const mod = await import('/static/js/markdown.js');
            return mod.mdToHtml(input);
          }, JSON.parse(process.env.ODYSSEUS_SVG_TEST_INPUT));
          console.log(JSON.stringify({ html }));
        } finally { await browser.close(); }
      })().catch(error => { console.error(error); process.exit(1); });
    '''
    result = subprocess.run(
        ["node", "-e", script], cwd=_REPO, capture_output=True, text=True,
        env={**os.environ, "ODYSSEUS_SVG_TEST_INPUT": json.dumps(markdown)}, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["html"]


def test_ordered_lists_render_as_one_unwrapped_ol(node_available):
    html = _run_markdown_case(
        "Before\n\n"
        "1. **Check against the home page** — that's the visual reference for how things should feel.\n"
        "2. **Open DevTools** and inspect the element — check fonts, colors, and spacing against this guide.\n"
        "3. **Flag it** — note the page, the section, what's wrong, and what CSS rule you suspect.\n"
        "4. **Small fixes** — if you know the fix (e.g. wrong CSS variable, wrong font), go ahead and change it in the CSS Module file.\n"
        "5. **Big changes** — Talk it through before making wide changes across many pages.\n\n"
        "After"
    )

    assert html.count("<ol>") == 1
    assert html.count("</ol>") == 1
    assert html.count("<li>") == 5
    assert "<ul>" not in html
    assert "<oli>" not in html
    assert "<uli>" not in html
    assert "<p><ol>" not in html
    assert "<p><li>" not in html


def test_fenced_svg_renders_inline_in_a_locked_sandbox(node_available):
    html = _run_svg_case(
        "```svg\n"
        '<svg viewBox="0 0 1200 800"><title>Black hole formation</title>'
        '<circle cx="200" cy="300" r="80"/></svg>\n'
        "```"
    )

    assert 'class="chat-svg-visual"' in html
    assert 'class="chat-svg-preview"' in html
    assert 'sandbox=""' in html
    assert "Content-Security-Policy" in html
    assert "default-src &#39;none&#39;" in html
    assert '--bg:#282c34' in html
    assert '--panel:#111111' in html
    assert '--accent:#e06c75' in html
    assert 'background:var(--bg)' in html
    assert 'title="Black hole formation"' in html
    assert 'style="aspect-ratio:1.5"' in html
    assert "SVG source" not in html
    assert '<details class="chat-svg-source">' not in html
    assert '&lt;circle cx=&quot;200&quot;' in html


def test_multiple_fenced_svgs_remain_interleaved_with_explanations(node_available):
    html = _run_svg_case(
        "```svg\n"
        '<svg viewBox="0 0 720 360"><title>Stage one</title></svg>\n'
        "```\n\nThe first mechanism explained.\n\n"
        "```svg\n"
        '<svg viewBox="0 0 720 360"><title>Stage two</title></svg>\n'
        "```\n\nThe second mechanism explained."
    )

    assert html.count('class="chat-svg-visual"') == 2
    assert html.index('title="Stage one"') < html.index("The first mechanism explained.")
    assert html.index("The first mechanism explained.") < html.index('title="Stage two"')
    assert html.index('title="Stage two"') < html.index("The second mechanism explained.")


def test_complete_raw_svg_uses_the_same_locked_renderer(node_available):
    html = _run_svg_case(
        "Before the visual.\n\n"
        '<svg viewBox="0 0 720 360"><title>Raw model SVG</title>'
        '<rect width="720" height="360" fill="var(--bg)"/></svg>'
        "\n\nAfter the visual."
    )

    assert html.count('class="chat-svg-visual"') == 1
    assert 'sandbox=""' in html
    assert 'title="Raw model SVG"' in html
    assert html.index("Before the visual.") < html.index('title="Raw model SVG"')
    assert html.index('title="Raw model SVG"') < html.index("After the visual.")


def test_svg_examples_inside_code_are_not_auto_rendered(node_available):
    html = _run_markdown_case(
        "Inline: `<svg></svg>`\n\n"
        "```html\n<svg viewBox=\"0 0 10 10\"></svg>\n```"
    )

    assert 'class="chat-svg-visual"' not in html
    assert "<code>&lt;svg&gt;&lt;/svg&gt;</code>" in html
    assert '<code class="language-html"' in html
    assert '&lt;svg viewBox=&quot;0 0 10 10&quot;&gt;' in html


def test_fenced_svg_receives_the_active_theme_palette(node_available):
    html = _run_markdown_case(
        "```svg\n<svg viewBox=\"0 0 720 360\"><title>Themed</title></svg>\n```",
        render_expr="""(
          globalThis.document.documentElement = {},
          globalThis.getComputedStyle = () => ({ getPropertyValue: name => ({
            '--bg': '#101214', '--panel': '#202428', '--fg': '#f1f5f9',
            '--border': '#475569', '--red': '#22d3ee', '--color-muted': '#94a3b8',
            '--color-success': '#4ade80', '--color-warning': '#facc15'
          })[name] || '' }),
          mod.mdToHtml(input)
        )""",
    )

    assert '--bg:#101214' in html
    assert '--panel:#202428' in html
    assert '--fg:#f1f5f9' in html
    assert '--accent:#22d3ee' in html


def test_more_list_expanders_render_as_clickable_links(node_available):
    html = _run_markdown_case(
        "Here are your notes:\n"
        "<!-- ody-more-notes:abc123\n"
        "📝 [Hidden note](#note-note-1)\n"
        "-->\n"
        "[...and 1 more notes](#notes-more-abc123)\n\n"
        "Available skills:\n"
        "<!-- ody-more-skills:def456\n"
        "- [hidden-skill](#skill-hidden-skill) (draft)\n"
        "-->\n"
        "[...and 1 more skills](#skills-more-def456)\n\n"
        "Memory:\n"
        "<!-- ody-more-memories:ghi789\n"
        "- [fact mem1](#memory-mem1) — Hidden memory\n"
        "-->\n"
        "[...and 1 more saved memories](#memories-more-ghi789)\n\n"
        "Calendar:\n"
        "<!-- ody-more-events:jkl012\n"
        "- [Hidden event](#event-event-1) — Sep 4, 7:00 PM–8:00 PM\n"
        "-->\n"
        "[...and 1 more events](#events-more-jkl012)\n\n"
        "Chats:\n"
        "<!-- ody-more-sessions:mno345\n"
        "- [Hidden chat](#session-chat-1) (last active yesterday)\n"
        "-->\n"
        "[...and 1 more chats](#sessions-more-mno345)"
    )

    assert 'href="#notes-more-abc123"' in html
    assert 'href="#skills-more-def456"' in html
    assert 'href="#memories-more-ghi789"' in html
    assert 'href="#events-more-jkl012"' in html
    assert 'href="#sessions-more-mno345"' in html
    assert "ody-more-" not in html
    assert "<details" not in html


def test_expanded_skill_payload_uses_normal_skill_list_markup(node_available):
    html = _run_markdown_case(
        "- [last-published](#skill-last-published) (general)\n"
        "## Drafts\n"
        "- [first-draft](#skill-first-draft) (draft)"
    )

    assert html.count("<ul>") == 2
    assert '<h2>Drafts</h2>' in html
    assert 'href="#skill-last-published" class="chat-link"' in html
    assert 'href="#skill-first-draft" class="chat-link"' in html


def test_saved_ajax_skill_inventory_plain_names_open_skills(node_available):
    html = _run_markdown_case(
        'Skills (44):\n\n**Published**\n'
        '- action-evidence-synthesis (communication)\n'
        '- artifact-completion (agent)\n\n**Drafts**\n'
        '- add-search-delete-and-verify-temporary-memory-by-marker\n'
        '- ...and 24 more skills.'
    )
    assert 'href="#skill-action-evidence-synthesis" class="chat-link"' in html
    assert 'href="#skill-artifact-completion" class="chat-link"' in html
    assert 'href="#skill-add-search-delete-and-verify-temporary-memory-by-marker" class="chat-link"' in html
    assert 'href="#skill-...and"' not in html


def test_session_titles_with_escaped_brackets_remain_clickable(node_available):
    html = _run_markdown_case(
        r"- [\[domain-audit\] theme](#session-ae3ee537-0a13-443f-a456-91e6fd061edf) "
        "(last active just now)"
    )

    assert 'href="#session-ae3ee537-0a13-443f-a456-91e6fd061edf"' in html
    assert "[domain-audit] theme" in html
    assert "ody-math-pending" not in html


def test_table_separator_row_not_rendered_as_data(node_available):
    html = _run_markdown_case("| A | B |\n|---|---|\n| 1 | 2 |")

    assert html.count("<tr>") == 2
    assert "<th" in html
    assert "<td" in html
    assert "---" not in html


def test_process_with_thinking_handles_gemma4_thought_channel(node_available):
    html = _run_markdown_case(
        "<|channel>thought\ninternal reasoning<channel|>Final answer.",
        "mod.processWithThinking(input)",
    )

    assert "thinking-section" in html
    assert "internal reasoning" in html
    assert "Final answer." in html
    assert "&lt;|channel&gt;" not in html
    assert "<|channel>" not in html


def test_process_with_thinking_strips_empty_gemma4_thought_channel(node_available):
    html = _run_markdown_case(
        "<|channel>thought\n<channel|>Final answer.",
        "mod.processWithThinking(input)",
    )

    assert "thinking-section" not in html
    assert "Final answer." in html
    assert "&lt;|channel&gt;" not in html
    assert "<|channel>" not in html


def test_process_with_thinking_unwraps_gemma4_response_channel(node_available):
    html = _run_markdown_case(
        "<|channel>thought\ninternal reasoning<channel|><|channel>response\nFinal answer.<channel|>",
        "mod.processWithThinking(input)",
    )

    assert "thinking-section" in html
    assert "internal reasoning" in html
    assert "Final answer." in html
    assert "&lt;|channel&gt;" not in html
    assert "<|channel>" not in html


def test_extract_thinking_blocks_handles_thought_tag(node_available):
    result = _run_markdown_case(
        "<thought>internal reasoning</thought>Final answer.",
        "mod.extractThinkingBlocks(input)",
    )

    assert result["thinkingBlocks"] == ["internal reasoning"]
    assert result["content"] == "Final answer."


def test_url_inside_inline_code_is_not_autolinked(node_available):
    # A URL inside a backtick span is preceded by a space, so the bare-URL
    # autolink used to wrap it in an <a> tag (then swap it for an
    # ___ALLOWED_HTML_ placeholder), corrupting the command shown to the user.
    html = _run_markdown_case("Run `$j = irm http://127.0.0.1:3000/x` to fetch.")

    assert "<code>$j = irm http://127.0.0.1:3000/x</code>" in html
    assert "___ALLOWED_HTML_" not in html
    assert "<a " not in html
    assert 'href="http://127.0.0.1:3000/x"' not in html


def test_url_outside_inline_code_is_still_autolinked(node_available):
    # Inline code must not disable autolinking for bare URLs elsewhere in the
    # same line.
    html = _run_markdown_case("Use `irm` then visit https://example.com/page now.")

    assert "<code>irm</code>" in html
    assert 'href="https://example.com/page"' in html


def test_https_markdown_image_renders_inline_as_clickable_thumbnail(node_available):
    # Assistant `![alt](url)` images must render inline: https-only, lazy,
    # max-width-100% (via CSS), and click-to-open the full size in a new tab.
    html = _run_markdown_case(
        "Here is a map:\n\n"
        "![Map of Sweden](https://upload.wikimedia.org/wikipedia/commons/thumb/3/32/Sweden_location_map.svg/600px-Sweden_location_map.svg.png)"
    )

    assert html.count('class="chat-image"') == 1
    assert html.count('class="chat-inline-image"') == 1
    assert 'alt="Map of Sweden"' in html
    assert 'loading="lazy"' in html
    assert 'decoding="async"' in html
    assert 'referrerpolicy="no-referrer"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html
    # The fallback caption (alt + URL) is emitted so a broken load never leaves
    # an empty gap; it stays hidden until the capture-phase error listener
    # toggles .image-broken.
    assert 'class="chat-image-fallback"' in html
    # The fallback caption carries the alt text plus the URL (the `//` is
    # entity-encoded so the bare-URL autolink pass can't nest another <a>).
    assert "Map of Sweden — https:&#47;&#47;upload.wikimedia.org" in html


def test_http_markdown_image_is_downgraded_to_alt_text(node_available):
    # Non-https image URLs must not render as an <img> (https-only policy); the
    # alt text is kept as plain text so nothing is silently dropped.
    html = _run_markdown_case("Look: ![a http pic](http://example.com/pic.png) done.")

    assert "<img" not in html
    assert 'class="chat-image"' not in html
    assert "a http pic" in html


def test_javascript_scheme_image_url_is_never_rendered(node_available):
    # A javascript: URL inside image syntax must be rejected outright, never
    # emitted as a src/href.
    html = _run_markdown_case("![x](javascript:alert(1))")

    assert "<img" not in html
    assert "javascript:" not in html
    assert 'class="chat-image"' not in html


def test_inline_code_content_is_html_escaped(node_available):
    # Inline code is now extracted before the global escape pass, so it must be
    # escaped at extraction time (matching the fenced-code-block handling).
    html = _run_markdown_case("Render `<b>$1 & 'q'</b>` literally.")

    assert "<code>&lt;b&gt;$1 &amp; &#39;q&#39;&lt;/b&gt;</code>" in html
    assert "<b>" not in html


def test_fenced_code_keeps_dollar_ampersand(node_available):
    # Issue #5663: the block-restore pass used a string replacement, so `$&` in a
    # restored block was read as "the matched text" and re-inserted the
    # placeholder. `perl -pe 's/world/$& again/'` rendered as
    # "s/world/___CODE_BLOCK_0___amp; again/" — the trailing "amp;" is the orphan
    # left behind after `$&` consumed the `$&` of the escaped `$&amp;`.
    html = _run_markdown_case(
        "```sh\necho \"hello world\" | perl -pe 's/world/$& again/'\n```"
    )

    assert "___CODE_BLOCK_" not in html
    assert "s/world/$&amp; again/" in html
    assert "amp; again" not in html.replace("$&amp; again", "")


def test_fenced_code_keeps_dollar_backtick_and_quote(node_available):
    # `` $` `` and `$'` splice the text before/after the placeholder into the
    # block. Unlike `$&` these leave no placeholder behind — the characters just
    # vanish — so assert the content survives verbatim.
    html = _run_markdown_case("```sh\nsed \"s/$`/x/\" && sed \"s/$'/y/\"\n```")

    assert "___CODE_BLOCK_" not in html
    assert "s/$`/x/" in html
    assert "s/$&#39;/y/" in html


def test_fenced_code_keeps_double_dollar(node_available):
    # `$$` collapsed to a single `$` in the restored block.
    html = _run_markdown_case('```sh\necho "$$USD and $$"\n```')

    assert "$$USD and $$" in html


def test_mermaid_block_keeps_dollar_ampersand(node_available):
    # The mermaid restore site had the same hazard: a node label containing `$&`
    # re-inserted the ___MERMAID_BLOCK_n___ placeholder into the diagram source,
    # which then fails to parse. The math and allowed-HTML sites are fixed the
    # same way; they need KaTeX/sanitizer conditions this harness doesn't set up.
    html = _run_markdown_case('```mermaid\ngraph TD; A["$&"] --> B;\n```')

    assert "___MERMAID_BLOCK_" not in html
    assert "$&amp;" in html


def test_currency_dollar_amounts_are_not_rendered_as_math(node_available):
    # "$5 to $10" used to pair the two dollar signs as inline-math delimiters
    # and render "5 to" through KaTeX. Pandoc-style rules now reject it: the
    # closing $ is preceded by a space and followed by a digit.
    html = _run_markdown_case(
        "The price rose from $5 to $10 overnight.", with_katex=True
    )

    assert 'class="katex"' not in html
    assert "$5" in html
    assert "$10" in html


def test_inline_math_still_renders_through_katex(node_available):
    html = _run_markdown_case("Pythagoras: $x^2 + y^2 = z^2$ holds.", with_katex=True)

    assert '<span class="katex" data-display="false">x^2 + y^2 = z^2</span>' in html
    assert "$" not in html


def test_display_math_still_renders_through_katex(node_available):
    html = _run_markdown_case("$$\\frac{a}{b}$$", with_katex=True)

    assert 'data-display="true"' in html
    assert "$$" not in html


def test_dotted_python_import_paths_are_not_autolinked(node_available):
    html = _run_markdown_case(
        "from imblearn.combine import SMOTETomek\n"
        "from sklearn.metrics import f1_score\n"
        "from sklearn.compose import ColumnTransformer\n\n"
        "See example.com/docs for normal domain autolinking."
    )

    assert "___ALLOWED_HTML_" not in html
    assert "imblearn.combine" in html
    assert "sklearn.metrics" in html
    assert "sklearn.compose" in html
    assert 'href="https://imblearn.com' not in html
    assert 'href="https://sklearn.me' not in html
    assert 'href="https://example.com/docs"' in html


def _doc_case(markdown: str):
    """Render with the options every Markdown document surface passes."""
    return _run_markdown_case(markdown, render_expr="mod.mdToHtml(input, { shortcodes: false, commonmark: true })")

def test_markdown_asterisk_lists_render_equivalent_to_dash_and_plus_lists(node_available):
    asterisk_html = _doc_case("* First item\n* Second item\n* Third item")
    dash_html = _doc_case("- First item\n- Second item\n- Third item")
    plus_html = _doc_case("+ First item\n+ Second item\n+ Third item")

    expected = "<ul><li>First item</li>\n<li>Second item</li>\n<li>Third item</li></ul>"
    assert asterisk_html == expected
    assert dash_html == expected
    assert plus_html == expected
    assert "<em>" not in asterisk_html
    assert "<p>" not in asterisk_html


def test_markdown_nested_and_task_lists(node_available):
    # Nested lists
    nested_ast = _doc_case("* Parent\n  * Child\n* Tail")
    nested_dash = _doc_case("- Parent\n  - Child\n- Tail")
    nested_plus = _doc_case("+ Parent\n  + Child\n+ Tail")

    assert "<em>" not in nested_ast
    assert nested_ast == nested_dash == nested_plus
    assert "<li>Parent</li>" in nested_ast
    assert "<li>Child</li>" in nested_ast
    assert "<li>Tail</li>" in nested_ast

    # Task lists across marker styles
    task_ast = _doc_case("* [ ] Open\n* [x] Done")
    task_dash = _doc_case("- [ ] Open\n- [x] Done")
    task_plus = _doc_case("+ [ ] Open\n+ [x] Done")

    assert task_ast == task_dash == task_plus
    assert 'class="task-item"' in task_ast
    assert 'class="task-item task-done"' in task_ast


def test_markdown_emphasis_inside_list_items(node_available):
    html = _doc_case(
        "* First with *italic* text\n"
        "* Second with **bold** text\n"
        "* Third with ***both*** text\n"
        "- Dash with *italic* text\n"
        "+ Plus with *italic* text"
    )

    assert "<em>italic</em>" in html
    assert "<strong>bold</strong>" in html
    assert "<strong><em>both</em></strong>" in html
    assert "<em> First with" not in html
    assert html.count("<ul>") == 1
    assert html.count("</ul>") == 1
    assert html.count("<li>") == 5


def test_markdown_adjacent_emphasis_and_multiline_constructs(node_available):
    # Adjacent bold and italic
    adj_html = _doc_case("*foo***bar** and **foo***bar* and ***both***")
    assert "<em>foo</em><strong>bar</strong>" in adj_html
    assert "<strong>foo</strong><em>bar</em>" in adj_html
    assert "<strong><em>both</em></strong>" in adj_html

    # Strikethrough
    strike_html = _doc_case("~~strikethrough text~~")
    assert "<del>strikethrough text</del>" in strike_html

    # Legitimate multiline Markdown constructs
    # Multiline blockquote
    bq_html = _doc_case("> First line of quote\n> Second line of quote")
    assert "<blockquote>" in bq_html
    assert "First line of quote" in bq_html
    assert "Second line of quote" in bq_html

    # Multiline pipe table
    tbl_html = _doc_case("| Col1 | Col2 |\n| --- | --- |\n| A | B |\n| C | D |")
    assert "<table" in tbl_html
    assert ">Col1</th>" in tbl_html
    assert ">A</td>" in tbl_html


def test_markdown_escaped_asterisks_and_arithmetic(node_available):
    # Escaped asterisks must remain literal and not trigger emphasis
    escaped_html = _doc_case("Literal \\*stars\\* stay and \\*\\*bold\\*\\* stay")
    assert "<em>" not in escaped_html
    assert "<strong>" not in escaped_html
    assert escaped_html == "<p>Literal *stars* stay and **bold** stay</p>"

    # Arithmetic expressions with space-flanked asterisks
    math_html = _doc_case("Calculation: 4 * 5 = 20 and 6 * 7 = 42")
    assert "<em>" not in math_html
    assert "4 * 5 = 20 and 6 * 7 = 42" in math_html


def test_markdown_fenced_code_and_security_sanitization(node_available):
    # Fenced code block preserving asterisks and newlines
    code_html = _doc_case(
        "```python\n"
        "def multiply(x, y):\n"
        "    *nums = [x, y]  # asterisk unpack\n"
        "    return nums[0] * nums[1]\n"
        "```"
    )
    assert "<pre><code" in code_html
    assert "nums[0] * nums[1]" in code_html
    assert "<em>" not in code_html

    # Security: raw script tags escaped, javascript: URLs rejected
    sec_html = _doc_case(
        "<script>alert('xss')</script>\n\n"
        "[Click here](javascript:alert(1))\n\n"
        "![Alt](javascript:alert(2))"
    )
    assert "<script>" not in sec_html
    assert "&lt;script&gt;alert(&#39;xss&#39;)&lt;/script&gt;" in sec_html or "&lt;script&gt;" in sec_html
    assert 'href="javascript:' not in sec_html
    assert 'src="javascript:' not in sec_html


# ── Chat rendering is unchanged by the Documents dialect ──────────────────────
# `mdToHtml` is shared. The CommonMark behavior the Markdown editor needs is opt-in
# (`commonmark: true`), so chat keeps rendering exactly what it rendered before.

def test_chat_fence_indented_under_a_numbered_step_is_a_code_block(node_available):
    html = _run_markdown_case("1. Step\n    ```bash\n    ls -la\n    ```\n2. Next")
    assert '<pre><code class="language-bash"' in html
    assert "ls -la" in html
    assert "``<code>" not in html
    assert "<li>Next</li>" in html


def test_chat_fence_closing_line_may_carry_text(node_available):
    html = _run_markdown_case("Intro\n```py\nx=1\n``` then done.\nafter text")
    code = html.split("</code>")[0]
    assert "then done" not in code
    assert "</pre> then done." in html
    assert "<p>after text</p>" in html


def test_chat_fence_inside_a_quote_is_a_code_block(node_available):
    html = _run_markdown_case("> note\n> ```js\n> x()\n> ```\n\nafter")
    assert '<pre><code class="language-js"' in html
    assert "``<code>" not in html


def test_chat_code_block_copy_payload_has_no_trailing_newline(node_available):
    html = _run_markdown_case("```js\nlet a = 1;\n```")
    assert '<code class="language-js" data-lang="js">let a = 1;</code>' in html
    assert 'data-code="let a = 1;"' in html


def test_chat_list_rules_are_unchanged_outside_documents(node_available):
    # Characterization of the shared renderer without the document option.
    assert _run_markdown_case("+ a\n+ b") == "<p>+ a</p>\n<p>+ b</p>"
    assert _run_markdown_case("- a\n  - b") == "<ul><li>a</li></ul>  - b"
    assert _run_markdown_case("1. a\n   1. b").startswith("<ol><li>a</li></ol>\n<p>   1. b")
    assert _run_markdown_case("- [ ] a\n  - [x] b").endswith("</ul>  - [x] b")
    assert _run_markdown_case("- a\n- b") == "<ul><li>a</li>\n<li>b</li></ul>"
    assert _run_markdown_case("1. a\n2. b") == "<ol><li>a</li>\n<li>b</li></ol>"


def test_chat_keeps_backslashes_math_delimiters_and_table_pipes(node_available):
    assert _run_markdown_case("C:\\temp\\x") == "<p>C:\\temp\\x</p>"
    assert "\\(" not in _run_markdown_case("Inline \\(x+1\\) here", with_katex=True)
    assert "\\[" not in _run_markdown_case("\\[x^2\\]", with_katex=True)
    row = _run_markdown_case("| a | b |\n| --- | --- |\n| x\\|y | 2 |")
    assert row.count("<td") == 3


def test_document_dialect_fences_lists_and_escapes(node_available):
    # Fences: tildes, longer fences, closing run of at least the opening length.
    tilde = _doc_case("~~~py\nx=1\n~~~")
    assert '<pre><code class="language-py"' in tilde and "<del>" not in tilde
    nested = _doc_case("````md\n```js\ninner\n```\n````")
    assert nested.count("<pre>") == 1 and "```js" in nested
    # A closing fence followed by text is code, as in CommonMark.
    assert "then done" in _doc_case("```\ncode\n``` then done.\n```").split("</code>")[0]
    # Indented and nested items render as list items.
    assert _doc_case("- a\n  - b").count("<li>") == 2
    assert _doc_case("+ a\n+ b").count("<li>") == 2
    # Escapes: literal characters, not backslashes; \( is not math here.
    assert _doc_case("2\\*3\\*4") == "<p>2*3*4</p>"
    assert _doc_case("C:\\\\temp") == "<p>C:\\temp</p>"
    assert "<td" in _doc_case("| a | b |\n| --- | --- |\n| x\\|y | 2 |") and _doc_case("| a | b |\n| --- | --- |\n| x\\|y | 2 |").count("<td") == 2
    assert _doc_case("\\`not code\\`") == "<p>`not code`</p>"
    assert _doc_case("`a\\` b") == "<p><code>a\\</code> b</p>"
    assert _doc_case("``a`b`` and `c`") == "<p><code>a`b</code> and <code>c</code></p>"
