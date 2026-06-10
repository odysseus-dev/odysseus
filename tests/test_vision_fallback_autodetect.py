"""Vision fallbacks must be tried when the primary model fails to resolve.

Regression test for the Auto-detect dead-end: with Settings → Vision → Model
left on "Auto-detect" (vision_model == "") and none of the hardcoded
auto-detect candidates installed, analyze_image_with_vl_result returned the
"[No vision model configured]" placeholder BEFORE the user's configured
fallback rows (vision_model_fallbacks) were ever consulted — so the obvious
UI setup (Auto-detect + one fallback) silently disabled vision.
"""

import pytest

from src import document_processor
from src.document_processor import analyze_image_with_vl_result


@pytest.fixture
def tiny_image(tmp_path):
    p = tmp_path / "img.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    return str(p)


def _settings(vision_model=""):
    return {"vision_enabled": True, "vision_model": vision_model}


def test_fallback_used_when_autodetect_finds_nothing(monkeypatch, tiny_image):
    """Primary resolution fails (Auto-detect, nothing installed) but a
    fallback row is configured → the fallback must answer."""
    monkeypatch.setattr(document_processor, "_load_vl_settings", lambda: _settings(""))
    monkeypatch.setattr(
        document_processor, "_resolve_vl_model",
        lambda configured, owner=None: (_ for _ in ()).throw(ValueError("no model")),
    )
    import src.endpoint_resolver as endpoint_resolver
    monkeypatch.setattr(
        endpoint_resolver, "resolve_vision_fallback_candidates",
        lambda owner=None: [("http://127.0.0.1:11434/v1", "qwen3.5:9b", {})],
    )
    monkeypatch.setattr(
        document_processor, "llm_call",
        lambda url, model, messages, headers=None, timeout=None: "a grandma reading",
    )

    result = analyze_image_with_vl_result(tiny_image)

    assert result["text"] == "a grandma reading"
    assert result["model"] == "qwen3.5:9b"


def test_placeholder_only_when_no_candidates_at_all(monkeypatch, tiny_image):
    monkeypatch.setattr(document_processor, "_load_vl_settings", lambda: _settings(""))
    monkeypatch.setattr(
        document_processor, "_resolve_vl_model",
        lambda configured, owner=None: (_ for _ in ()).throw(ValueError("no model")),
    )
    import src.endpoint_resolver as endpoint_resolver
    monkeypatch.setattr(
        endpoint_resolver, "resolve_vision_fallback_candidates",
        lambda owner=None: [],
    )

    result = analyze_image_with_vl_result(tiny_image)

    assert result["text"].startswith("[No vision model configured")


def test_primary_still_preferred_over_fallbacks(monkeypatch, tiny_image):
    monkeypatch.setattr(document_processor, "_load_vl_settings", lambda: _settings("primary-vl"))
    monkeypatch.setattr(
        document_processor, "_resolve_vl_model",
        lambda configured, owner=None: ("http://primary/v1", "primary-vl", {}),
    )
    import src.endpoint_resolver as endpoint_resolver
    monkeypatch.setattr(
        endpoint_resolver, "resolve_vision_fallback_candidates",
        lambda owner=None: [("http://fallback/v1", "fallback-vl", {})],
    )

    seen = []

    def fake_llm_call(url, model, messages, headers=None, timeout=None):
        seen.append(model)
        return "described"

    monkeypatch.setattr(document_processor, "llm_call", fake_llm_call)

    result = analyze_image_with_vl_result(tiny_image)

    assert seen == ["primary-vl"]
    assert result["model"] == "primary-vl"


def test_downed_primary_falls_through_to_fallback(monkeypatch, tiny_image):
    """Primary resolves but its endpoint errors at call time → next candidate."""
    monkeypatch.setattr(document_processor, "_load_vl_settings", lambda: _settings("primary-vl"))
    monkeypatch.setattr(
        document_processor, "_resolve_vl_model",
        lambda configured, owner=None: ("http://primary/v1", "primary-vl", {}),
    )
    import src.endpoint_resolver as endpoint_resolver
    monkeypatch.setattr(
        endpoint_resolver, "resolve_vision_fallback_candidates",
        lambda owner=None: [("http://fallback/v1", "fallback-vl", {})],
    )

    def fake_llm_call(url, model, messages, headers=None, timeout=None):
        if model == "primary-vl":
            raise RuntimeError("endpoint down")
        return "fallback description"

    monkeypatch.setattr(document_processor, "llm_call", fake_llm_call)

    result = analyze_image_with_vl_result(tiny_image)

    assert result["text"] == "fallback description"
    assert result["model"] == "fallback-vl"
