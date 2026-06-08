"""Cookbook Scan list UI contracts — model labels, AA badge, row clickability."""

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
    out = [ln for ln in res.stdout.splitlines() if ln.strip()]
    if not out:
        raise AssertionError("node produced no stdout")
    return json.loads(out[-1])


def test_qwen3_8b_shows_instruct_label(node_available):
    result = _run_node(textwrap.dedent("""
        import { resolveModelIdentity } from './static/js/aaModelLinks.js';
        const id = resolveModelIdentity('Qwen/Qwen3-8B', {});
        console.log(JSON.stringify({ display: id.displayName, aaSlug: id.aaSlug }));
    """))
    assert result["display"] == "Qwen3-8B-Instruct"
    assert result["aaSlug"] is None


def test_deepseek_v3_aa_badge_when_slug_in_index(node_available):
    result = _run_node(textwrap.dedent("""
        import { readFileSync } from 'fs';
        import { renderModelNameLink, resolveModelIdentity } from './static/js/aaModelLinks.js';
        const esc = (s) => String(s);
        const payload = JSON.parse(readFileSync('./data/aa_model_index.json', 'utf8'));
        const id = resolveModelIdentity('deepseek-ai/DeepSeek-V3', payload.aliases);
        const html = renderModelNameLink('deepseek-ai/DeepSeek-V3', id.displayName, esc, payload.aliases);
        console.log(JSON.stringify({
          aaSlug: id.aaSlug,
          hasNameSpan: html.includes('class="cookbook-model-name"'),
          nameNotAnchor: !html.match(/<a[^>]*cookbook-model-name/),
          hasAaBadge: html.includes('cookbook-aa-badge'),
          href: html.includes('deepseek-v3'),
        }));
    """))
    assert result["aaSlug"] == "deepseek-v3"
    assert result["hasNameSpan"] is True
    assert result["nameNotAnchor"] is True
    assert result["hasAaBadge"] is True
    assert result["href"] is True


def test_unmapped_model_is_plain_span(node_available):
    result = _run_node(textwrap.dedent("""
        import { renderModelNameLink } from './static/js/aaModelLinks.js';
        const esc = (s) => String(s);
        const html = renderModelNameLink('solidrust/gemma-2-9b-it-AWQ', null, esc, {});
        console.log(JSON.stringify({
          hasSpan: html.includes('cookbook-model-name'),
          hasBadge: html.includes('cookbook-aa-badge'),
          hasAnchor: html.includes('<a '),
        }));
    """))
    assert result["hasSpan"] is True
    assert result["hasBadge"] is False
    assert result["hasAnchor"] is False
