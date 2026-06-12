from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_chat_context_uses_cached_models_before_live_model_probe():
    source = (ROOT / "routes" / "chat_helpers.py").read_text()

    assert "def _normalize_model_id_from_cache" in source
    assert "cached_models" in source
    assert "norm = _normalize_model_id_from_cache(sess) or normalize_model_id" in source


def test_cached_model_match_keeps_basename_normalization():
    source = (ROOT / "routes" / "chat_helpers.py").read_text()

    assert "def _match_cached_model_id" in source
    assert "os.path.basename(requested.rstrip(\"/\"))" in source
    assert "os.path.basename(model_id.rstrip(\"/\")) == req_base" in source
