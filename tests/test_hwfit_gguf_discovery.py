"""Runtime GGUF recommendations must carry a real, compatible download."""
import io
import json
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from services.hwfit import hf_discovery as discovery, models, fit


def _source():
    return next(s for s in discovery.HF_COLLECTION_SOURCES if s.get("key") == "unsloth_gguf")


def _repo(files, *, pipeline="text-generation", name="unsloth/Example-26B-GGUF"):
    return {"id": name, "pipeline_tag": pipeline, "gguf": {"total": 26_000_000_000},
            "siblings": [{"rfilename": f} for f in files]}


def _fetch(monkeypatch, payload):
    class Response(io.BytesIO):
        headers = {}
    monkeypatch.setattr(discovery.urllib.request, "urlopen", lambda *a, **kw: Response(json.dumps(payload).encode()))
    return discovery.fetch_gguf_models(_source())


def _metal():
    return {"has_gpu": True, "backend": "metal", "platform": "darwin", "gpu_name": "Apple M1 Max",
            "gpu_vram_gb": 24, "gpu_count": 1, "available_ram_gb": 24, "total_ram_gb": 32,
            "unified_memory": True}


def test_discovered_gguf_is_ranked_and_uses_matching_quant_file(monkeypatch):
    rows = _fetch(monkeypatch, [_repo(["Example-Q4_K_M.gguf", "Example-Q8_0.gguf", "mmproj-F16.gguf"])])
    assert len(rows) == 1
    with patch.object(fit, "get_models", return_value=rows):
        ranked = fit.rank_models(_metal())
        assert ranked[0]["quant"] == "Q4_K_M"
        assert ranked[0]["gguf_sources"] == [{"repo": rows[0]["name"], "file": "Example-Q4_K_M.gguf", "quant": "Q4_K_M"}]
        assert fit.rank_models(_metal(), quant="Q6_K") == []
        q8 = fit.rank_models(_metal(), quant="Q8_0")[0]
        assert q8["gguf_sources"][0]["file"] == "Example-Q8_0.gguf"


def test_complete_shards_have_download_glob_and_first_serve_file(monkeypatch):
    rows = _fetch(monkeypatch, [_repo(["Q4/Example-Q4_K_M-00002-of-00002.gguf", "Q4/Example-Q4_K_M-00001-of-00002.gguf"])])
    assert rows[0]["gguf_sources"][0] == {
        "repo": rows[0]["name"], "quant": "Q4_K_M",
        "file": "Q4/Example-Q4_K_M-00001-of-00002.gguf", "include": "Q4/Example-Q4_K_M-*-of-00002.gguf",
    }


@pytest.mark.parametrize("repo", [
    _repo(["Example-Q4_K_M-00001-of-00002.gguf"]),
    _repo(["mmproj-Q4_K_M.gguf", "MTP/mtp-Q4_K_M.gguf"]),
    _repo(["Example-IQ2_XXS.gguf"]),
    _repo(["Example-Q4_K_M.gguf"], pipeline="text-to-image"),
    _repo(["Example-Q4_K_M.gguf"], pipeline="text-to-speech"),
    _repo(["Example-Q4_K_M.gguf"], name="another-owner/Example-GGUF"),
])
def test_unservable_or_unrelated_entries_are_not_recommended(monkeypatch, repo):
    assert _fetch(monkeypatch, [repo]) == []


def test_q4_0_only_repo_keeps_its_native_quant(monkeypatch):
    rows = _fetch(monkeypatch, [_repo(["Example-Q4_0.gguf"])])
    with patch.object(fit, "get_models", return_value=rows):
        result = fit.rank_models(_metal())[0]
    assert result["quant"] == "Q4_0"
    assert result["gguf_sources"][0]["file"] == "Example-Q4_0.gguf"


def test_projector_total_does_not_turn_a_large_moe_into_a_small_model(monkeypatch):
    repo = _repo(["Example-120B-A12B-Q4_K_M.gguf"], name="unsloth/Example-120B-A12B-GGUF")
    repo["gguf"]["total"] = 1_000_000_000
    row = _fetch(monkeypatch, [repo])[0]
    assert row["parameters_raw"] == 120_000_000_000
    assert row["active_parameters"] == 12_000_000_000
    with patch.object(fit, "get_models", return_value=[row]):
        assert fit.rank_models(_metal())[0]["fit_level"] == "too_tight"


def test_unknown_size_with_only_projector_metadata_is_not_guessed(monkeypatch):
    repo = _repo(["Example-Q4_K_M.gguf"], name="unsloth/Example-GGUF")
    repo["gguf"]["architecture"] = "clip"
    assert _fetch(monkeypatch, [repo]) == []


def test_verified_gguf_files_override_safetensors_name_heuristic(monkeypatch):
    row = _fetch(monkeypatch, [_repo(["Example-Q4_K_M.gguf"], name="unsloth/Example-26B-AWQ-GGUF")])[0]
    assert models._normalize_model_entry(row)["quantization"] == "Q4_K_M"
    assert models.is_prequantized(row) is False
    assert fit._native_quant(row) == "Q4_K_M"


def test_legacy_safetensors_with_gguf_alternative_keeps_native_format():
    row = {"name": "test/Example-26B-AWQ", "quantization": "AWQ-4bit",
           "gguf_sources": [{"repo": "test/Example-GGUF", "file": "Example-Q4_K_M.gguf", "quant": "Q4_K_M"}]}
    assert models.gguf_quantizations(row) == []
    assert models.is_prequantized(row) is True
    assert fit._native_quant(row) == "AWQ-4bit"


def test_failed_source_retains_previous_rows_without_reviving_successfully_empty_source(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery, "HF_COLLECTION_MODELS_CACHE", tmp_path / "cache.json")
    previous = [{"name": "unsloth/old-GGUF", "_source_owner": "unsloth"},
                {"name": "bartowski/removed-GGUF", "_source_owner": "bartowski"}]
    discovery.HF_COLLECTION_MODELS_CACHE.write_text(json.dumps(previous))
    monkeypatch.setattr(discovery, "HF_COLLECTION_SOURCES", [
        {"key": "unsloth_gguf", "owner": "unsloth", "gguf_only": True},
        {"key": "bartowski_gguf", "owner": "bartowski", "gguf_only": True},
    ])
    def fetch(source):
        if source["owner"] == "unsloth":
            raise OSError("offline")
        return []
    monkeypatch.setattr(discovery, "fetch_gguf_models", fetch)
    assert discovery.refresh_hf_collection_models_cache(force=True) == [previous[0]]


def test_successfully_empty_catalog_clears_previous_source(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery, "HF_COLLECTION_MODELS_CACHE", tmp_path / "cache.json")
    discovery.HF_COLLECTION_MODELS_CACHE.write_text(json.dumps([{"name": "unsloth/old", "_source_owner": "unsloth"}]))
    monkeypatch.setattr(discovery, "HF_COLLECTION_SOURCES", [_source()])
    monkeypatch.setattr(discovery, "fetch_gguf_models", lambda source: [])
    assert discovery.refresh_hf_collection_models_cache(force=True) == []
    assert discovery.load_cached_hf_collection_models() == []


def test_bulk_pagination_deduplicates_and_keeps_requests_on_hub(monkeypatch):
    requested = []
    class Response(io.BytesIO):
        def __init__(self, body, next_url=None):
            super().__init__(json.dumps(body).encode())
            self.headers = {"Link": f'<{next_url}>; rel="next"'} if next_url else {}
    repo = _repo(["Example-Q4_K_M.gguf"])
    def open_page(request, **kwargs):
        requested.append(request.full_url)
        return Response([repo], discovery.HF_MODELS_URL + "?cursor=next") if len(requested) == 1 else Response([repo])
    monkeypatch.setattr(discovery.urllib.request, "urlopen", open_page)
    assert len(discovery.fetch_gguf_models(_source())) == 1
    assert len(requested) == 2
    assert "author=unsloth" in requested[0] and "filter=gguf" in requested[0]

    requested.clear()
    monkeypatch.setattr(discovery.urllib.request, "urlopen", lambda *a, **kw: Response([repo], "http://other.invalid/?cursor=next"))
    with pytest.raises(ValueError, match="pagination"):
        discovery.fetch_gguf_models(_source())


def test_runtime_rows_survive_offline_reload_and_keep_verified_quant(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery, "HF_COLLECTION_MODELS_CACHE", tmp_path / "cache.json")
    monkeypatch.setattr(discovery, "MLX_COMMUNITY_CACHE", tmp_path / "mlx.json")
    monkeypatch.setattr(discovery, "HF_COLLECTION_SOURCES", [_source()])
    rows = _fetch(monkeypatch, [_repo(["Example-Q4_0.gguf"])])
    monkeypatch.setattr(discovery, "fetch_gguf_models", lambda source: rows)
    discovery.refresh_hf_collection_models_cache(force=True)
    monkeypatch.setattr(discovery, "fetch_gguf_models", lambda source: pytest.fail("offline read must not fetch"))
    monkeypatch.setattr(models, "_load_model_file", lambda path: [])
    monkeypatch.setattr(models, "_models_cache", None)
    stored = models.get_models()
    assert stored[0]["quantization"] == "Q4_0"
    with patch.object(fit, "get_models", return_value=stored):
        assert fit.rank_models(_metal())[0]["quant"] == "Q4_0"


def test_download_preview_uses_all_shards_but_preserves_single_file_sources():
    if not shutil.which("node"):
        pytest.skip("Node is required")
    source = (Path(__file__).resolve().parents[1] / "static/js/cookbookDownload.js").read_text(encoding="utf-8")
    helper = re.search(r"^function _ggufIncludePattern\(.*?^}", source, re.M | re.S).group()
    script = helper + """
const assert = require('node:assert/strict');
assert.equal(_ggufIncludePattern({}, {file:'part-00001-of-00002.gguf', include:'part-*-of-00002.gguf'}), 'part-*-of-00002.gguf');
assert.equal(_ggufIncludePattern({}, {file:'model.gguf'}), 'model.gguf');
assert.equal(_ggufIncludePattern({quant:'Q4_K_M'}, {}), '*Q4_K_M*');
"""
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
