"""Regression coverage for the browser markdown renderer."""

import json
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
          /import uiModule from ['"]\.\/ui\.js['"];/,
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
    assert "<p>Before</p>" in html
    assert "<p>After</p>" in html


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


# --- Nested markdown lists (indentation-stack builder) ----------------------
# The old flatten-and-group passes anchored each list regex on the ABSOLUTE
# start of the line, so an indented item (e.g. "  - Sub") never matched and fell
# through to the paragraph pass as literal "- Sub" text, while the parent list
# fragmented. The ordered-nested case additionally emitted an <ol> inside a <p>
# (invalid HTML). These pin the fix: items nest as <li>...<ul>...</ul></li>
# (valid HTML), for unordered, ordered, mixed and task lists at arbitrary depth.


def test_nested_unordered_list_nests_sublist_inside_parent_li(node_available):
    html = _run_markdown_case(
        "- Item one\n"
        "- Item two\n"
        "  - Nested sub-item"
    )

    # The sublist lives INSIDE the parent <li>, not loose after it.
    assert (
        "<ul><li>Item one</li><li>Item two"
        "<ul><li>Nested sub-item</li></ul></li></ul>"
    ) in html
    # Exactly one level of nesting (two <ul>, two </ul>).
    assert html.count("<ul>") == 2
    assert html.count("</ul>") == 2
    # The sub-item is NOT rendered as literal text or a stray paragraph.
    assert "- Nested sub-item" not in html
    assert "<p>- Nested sub-item" not in html
    assert "<p><ul>" not in html
    # No leftover sentinels.
    assert "<uli" not in html
    assert "<oli" not in html


def test_nested_ordered_list_is_valid_html_not_ol_in_p(node_available):
    # THE invalid-HTML bug: an indented "   1. Sub" used to drop out of the list
    # passes and get wrapped as <p><ol>...</ol></p>. It must nest inside the
    # parent <li> as valid HTML instead.
    html = _run_markdown_case(
        "1. First\n"
        "2. Second\n"
        "   1. Sub"
    )

    assert (
        "<ol><li>First</li><li>Second"
        "<ol><li>Sub</li></ol></li></ol>"
    ) in html
    assert html.count("<ol>") == 2
    assert html.count("</ol>") == 2
    # No <ol> is ever wrapped in / adjacent-inside a paragraph.
    assert "<p><ol>" not in html
    assert "<ol></p>" not in html
    assert "<p>" not in html  # no surrounding prose in this sample
    assert "<ul>" not in html


def test_mixed_unordered_under_ordered_nests_ul_inside_ol_li(node_available):
    html = _run_markdown_case(
        "1. Parent\n"
        "   - bullet child\n"
        "2. Second"
    )

    assert (
        "<ol><li>Parent<ul><li>bullet child</li></ul></li>"
        "<li>Second</li></ol>"
    ) in html
    assert html.count("<ol>") == 1
    assert html.count("<ul>") == 1
    assert "<p>" not in html


def test_nested_ordered_under_unordered_nests_ol_inside_ul_li(node_available):
    html = _run_markdown_case(
        "- Parent bullet\n"
        "  1. numbered child\n"
        "- Sibling bullet"
    )

    assert (
        "<ul><li>Parent bullet<ol><li>numbered child</li></ol></li>"
        "<li>Sibling bullet</li></ul>"
    ) in html
    assert html.count("<ul>") == 1
    assert html.count("<ol>") == 1


def test_nested_task_checkbox_preserves_class_and_structure(node_available):
    html = _run_markdown_case(
        "- [ ] parent task\n"
        "  - [x] child done"
    )

    # Parent keeps its task-item <li>, child keeps task-done, and the child list
    # is nested inside the parent <li>.
    assert (
        '<ul><li class="task-item">'
        '<span class="task-check" aria-hidden="true"></span>'
        '<span class="task-text">parent task</span>'
        '<ul><li class="task-item task-done">'
        '<span class="task-check" aria-hidden="true"></span>'
        '<span class="task-text">child done</span>'
        "</li></ul></li></ul>"
    ) in html
    assert html.count("<ul>") == 2
    assert "task-text\">child done</span>" in html


def test_three_level_deep_nesting(node_available):
    html = _run_markdown_case(
        "- A\n"
        "  - B\n"
        "    - C"
    )

    assert (
        "<ul><li>A<ul><li>B<ul><li>C</li></ul></li></ul></li></ul>"
    ) in html
    assert html.count("<ul>") == 3
    assert html.count("</ul>") == 3


def test_inconsistent_indent_widths_still_nest_consistently(node_available):
    # Models emit inconsistent indent (here 3 then 6 spaces). Depth must follow
    # the RELATIVE increase, not a fixed bucket width.
    html = _run_markdown_case(
        "- top\n"
        "   - mid\n"
        "      - deep"
    )

    assert (
        "<ul><li>top<ul><li>mid<ul><li>deep</li></ul></li></ul></li></ul>"
    ) in html
    assert html.count("<ul>") == 3


def test_nested_list_after_prose_keeps_paragraph_and_indent(node_available):
    # A lead-in paragraph, then a bullet list whose last item carries a nested
    # sub-item, then a trailing paragraph.
    html = _run_markdown_case(
        "Bullet list:\n\n"
        "- Item one\n"
        "- Item two\n"
        "  - Nested sub-item\n\n"
        "After."
    )

    assert "<p>Bullet list:</p>" in html
    assert "<p>After.</p>" in html
    assert (
        "<li>Item two<ul><li>Nested sub-item</li></ul></li></ul>"
    ) in html
    # The sub-item never leaks out as a literal-text paragraph (the old bug).
    assert "<p>- Nested sub-item" not in html
    assert "- Nested sub-item" not in html


def test_flat_unordered_list_still_renders_as_one_ul(node_available):
    # Guard the flat path the builder must not regress.
    html = _run_markdown_case("- one\n- two\n- three")

    assert "<ul><li>one</li><li>two</li><li>three</li></ul>" in html
    assert html.count("<ul>") == 1
    assert html.count("<li>") == 3
    assert "<oli>" not in html
    assert "<uli>" not in html
