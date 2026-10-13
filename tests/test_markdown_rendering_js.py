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
