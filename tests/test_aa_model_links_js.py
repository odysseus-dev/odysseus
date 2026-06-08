"""Tests for static/js/aaModelLinks.js — AA slug resolution and link rendering."""

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


def test_normalize_strips_org_prefix_and_quant_suffix(node_available):
    result = _run_node(textwrap.dedent("""
        import { normalizeAaKey } from './static/js/aaModelLinks.js';
        console.log(JSON.stringify({
          hf: normalizeAaKey('Qwen/Qwen3-8B-AWQ'),
          llama: normalizeAaKey('meta-llama/Llama-3.2-3B-Instruct'),
        }));
    """))
    assert result["hf"] == "qwen3-8b"
    assert result["llama"] == "llama-3-2-3b-instruct"


def test_render_includes_aa_link_when_mapped(node_available):
    result = _run_node(textwrap.dedent("""
        import { renderModelNameLink } from './static/js/aaModelLinks.js';
        const esc = (s) => String(s);
        const aliases = { 'qwen3-8b': 'qwen3-8b-instruct' };
        const valid = new Set(['qwen3-8b-instruct']);
        const html = renderModelNameLink('Qwen/Qwen3-8B', 'Qwen3-8B-Instruct', esc, aliases);
        console.log(JSON.stringify({
          hasBadge: html.includes('cookbook-aa-badge'),
          nameIsSpan: html.includes('<span class="cookbook-model-name">'),
          href: html.includes('https://artificialanalysis.ai/models/qwen3-8b-instruct'),
        }));
    """))
    assert result["hasBadge"] is True
    assert result["nameIsSpan"] is True
    assert result["href"] is True


def test_resolve_known_alias(node_available):
    result = _run_node(textwrap.dedent("""
        import { resolveAaSlug } from './static/js/aaModelLinks.js';
        const aliases = { 'qwen3-8b': 'qwen3-8b-instruct', 'deepseek-v3': 'deepseek-v3' };
        console.log(JSON.stringify({
          qwen: resolveAaSlug('Qwen/Qwen3-8B', aliases),
          deepseek: resolveAaSlug('deepseek-ai/DeepSeek-V3', aliases),
          unknown: resolveAaSlug('peft-internal-testing/tiny-random-gpt2', aliases),
        }));
    """))
    assert result["qwen"] == "qwen3-8b-instruct"
    assert result["deepseek"] == "deepseek-v3"
    assert result["unknown"] is None


def test_render_omits_link_when_unmapped(node_available):
    result = _run_node(textwrap.dedent("""
        import { renderModelNameLink } from './static/js/aaModelLinks.js';
        const esc = (s) => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');
        const html = renderModelNameLink('unknown/model', 'MyModel', esc);
        console.log(JSON.stringify({
          hasAnchor: html.includes('<a '),
          hasSpan: html.includes('cookbook-model-name'),
        }));
    """))
    assert result["hasAnchor"] is False
    assert result["hasSpan"] is True


def test_aa_index_file_has_aliases():
    index_path = _REPO / "data" / "aa_model_index.json"
    assert index_path.is_file(), "data/aa_model_index.json should exist"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert payload.get("aliases")
    assert payload.get("valid_slugs")
    assert "qwen3-8b" in payload["aliases"]
    assert payload["aliases"]["qwen3-8b"] == "qwen3-8b-instruct"
    assert "deepseek-r1-0528-qwen3-8b" not in payload["aliases"]
    assert "deepseek-r1-0528" not in payload["aliases"]
    assert "gemma-2-9b-it" not in payload["aliases"]


def test_no_aa_link_for_unsupported_gemma2(node_available):
    result = _run_node(textwrap.dedent("""
        import { readFileSync } from 'fs';
        import { resolveAaSlug, resolveModelIdentity, renderModelNameLink } from './static/js/aaModelLinks.js';
        const payload = JSON.parse(readFileSync('./data/aa_model_index.json', 'utf8'));
        const aliases = payload.aliases;
        const valid = new Set(payload.valid_slugs || []);
        // simulate loaded valid slugs via exact alias only path
        const id = resolveModelIdentity('solidrust/gemma-2-9b-it-AWQ', aliases);
        const html = renderModelNameLink('solidrust/gemma-2-9b-it-AWQ', id.displayName, s => s, aliases);
        console.log(JSON.stringify({
          slug: resolveAaSlug('solidrust/gemma-2-9b-it-AWQ', aliases),
          display: id.displayName,
          hfRepo: id.hfRepo,
          hasAnchor: html.includes('cookbook-aa-name-link'),
        }));
    """))
    assert result["slug"] is None
    assert result["display"] == "gemma-2-9b-it"
    assert result["hfRepo"] == "google/gemma-2-9b-it"
    assert result["hasAnchor"] is False


def test_deepseek_distill_uses_canonical_name(node_available):
    result = _run_node(textwrap.dedent("""
        import { readFileSync } from 'fs';
        import { resolveModelIdentity, resolveAaSlug, renderModelNameLink } from './static/js/aaModelLinks.js';
        const payload = JSON.parse(readFileSync('./data/aa_model_index.json', 'utf8'));
        const aliases = payload.aliases;
        const id = resolveModelIdentity('deepseek-ai/DeepSeek-R1-0528-Qwen3-8B', aliases);
        const html = renderModelNameLink('deepseek-ai/DeepSeek-R1-0528-Qwen3-8B', id.displayName, s => s, aliases);
        console.log(JSON.stringify({
          display: id.displayName,
          hfRepo: id.hfRepo,
          aaSlug: id.aaSlug,
          slugLookup: resolveAaSlug('deepseek-ai/DeepSeek-R1-0528-Qwen3-8B', aliases),
          nameNotAnchor: !html.match(/<a[^>]*cookbook-model-name/),
        }));
    """))
    assert result["display"] == "DeepSeek-R1-0528"
    assert result["hfRepo"] == "deepseek-ai/DeepSeek-R1-0528"
    assert result["aaSlug"] is None
    assert result["slugLookup"] is None
    assert result["nameNotAnchor"] is True
