"""Regression tests for the chat markdown renderer."""

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


def _run_node(script: str) -> dict:
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_REPO,
        capture_output=True,
        timeout=15,
        text=True,
    )
    if res.returncode != 0:
        raise AssertionError(f"node failed:\n{res.stderr}")
    out_lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    if not out_lines:
        raise AssertionError("node produced no stdout")
    return json.loads(out_lines[-1])


def _markdown_import_script() -> str:
    return """
        import { readFileSync } from 'node:fs';

        globalThis.window = { location: { origin: 'http://localhost' } };
        let source = readFileSync('./static/js/markdown.js', 'utf8');
        source = source.replace(
          "import uiModule from './ui.js';",
          "const uiModule = { esc: (s) => String(s ?? '').replace(/[&<>\\\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\\\\\"':'&quot;',\\\"'\\\":'&#39;'}[c])) };"
        );
        source = source.replace(/\\/\\/ Mermaid is loaded async[\\s\\S]*$/, '');
        const markdown = await import(
          'data:text/javascript;base64,' + Buffer.from(source).toString('base64')
        );
    """


def test_thinking_details_reload_as_collapsed_thinking_section(node_available):
    """Some thinking models persist their reasoning as a `<details>` block.

    Reloaded chat history goes through `processWithThinking()`, so thinking
    details should normalize to the app's collapsed thinking UI instead of
    being forced open by the generic details passthrough.
    """
    script = textwrap.dedent(_markdown_import_script() + """
        const html = markdown.processWithThinking(
          '<details><summary>Thinking Process</summary>private reasoning</details>\\n\\nFinal answer.'
        );
        console.log(JSON.stringify({
          hasThinkingSection: html.includes('class="thinking-section"'),
          hasThinkingText: html.includes('private reasoning'),
          hasFinalAnswer: html.includes('Final answer.'),
          hasOpenDetails: /<details\\s+open/i.test(html),
          hasRawDetails: /<details/i.test(html),
        }));
    """)
    out = _run_node(script)
    assert out == {
        "hasThinkingSection": True,
        "hasThinkingText": True,
        "hasFinalAnswer": True,
        "hasOpenDetails": False,
        "hasRawDetails": False,
    }


def test_non_thinking_details_still_render_as_details(node_available):
    script = textwrap.dedent(_markdown_import_script() + """
        const html = markdown.processWithThinking(
          '<details><summary>Output</summary>tool output</details>'
        );
        console.log(JSON.stringify({
          hasThinkingSection: html.includes('class="thinking-section"'),
          hasOpenDetails: /<details\\s+open/i.test(html),
          hasToolOutput: html.includes('tool output'),
        }));
    """)
    out = _run_node(script)
    assert out == {
        "hasThinkingSection": False,
        "hasOpenDetails": True,
        "hasToolOutput": True,
    }
