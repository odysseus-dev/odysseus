"""Tests for scripts/sync_aa_model_index.py."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "sync_aa_model_index.py"


def _load_sync_module():
    spec = importlib.util.spec_from_file_location("sync_aa_model_index", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_build_aliases_from_api_uses_slug_and_name_only():
    mod = _load_sync_module()
    valid = {"deepseek-v3", "qwen3-8b-instruct"}
    models = [
        {"slug": "deepseek-v3", "name": "DeepSeek V3"},
        {"slug": "qwen3-8b-instruct", "name": "Qwen3 8B Instruct"},
    ]
    aliases = mod.build_aliases_from_api(models, valid)
    assert aliases["deepseek-v3"] == "deepseek-v3"
    assert aliases["deepseek-v3"] == aliases[mod.normalize_key("DeepSeek V3")]
    assert aliases["qwen3-8b-instruct"] == "qwen3-8b-instruct"
    assert "qwen3-8b" not in aliases


def test_build_slug_only_aliases_no_hf_guessing():
    mod = _load_sync_module()
    valid = {"deepseek-v3", "qwen3-8b-instruct"}
    aliases = mod.build_slug_only_aliases(valid)
    assert aliases["deepseek-v3"] == "deepseek-v3"
    assert aliases["qwen3-8b-instruct"] == "qwen3-8b-instruct"
    assert len(aliases) == 2


def test_main_requires_api_key_without_sitemap_only(monkeypatch, capsys):
    mod = _load_sync_module()
    monkeypatch.delenv("AA_API_KEY", raising=False)
    monkeypatch.setattr(mod, "fetch_sitemap_slugs", lambda: {"deepseek-v3"})
    monkeypatch.setattr(sys, "argv", ["sync_aa_model_index.py"])
    assert mod.main() == 1
    assert "AA_API_KEY is required" in capsys.readouterr().err


def test_main_sitemap_only_writes_index(tmp_path, monkeypatch):
    mod = _load_sync_module()
    out = tmp_path / "aa_model_index.json"
    monkeypatch.setattr(mod, "OUT_PATH", out)
    monkeypatch.setattr(mod, "fetch_sitemap_slugs", lambda: {"deepseek-v3", "gpt-4o"})
    monkeypatch.setattr(sys, "argv", ["sync_aa_model_index.py", "--sitemap-only"])
    assert mod.main() == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["source"] == "sitemap-slugs"
    assert payload["aliases"]["deepseek-v3"] == "deepseek-v3"
    assert "qwen3-8b" not in payload["aliases"]
