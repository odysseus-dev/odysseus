from pathlib import Path

from src import ai_interaction
from src import document_processor as dp


ROOT = Path(__file__).resolve().parents[1]


def test_configured_vision_model_resolution_passes_owner(monkeypatch):
    seen = []

    def fake_resolve_model(spec, owner=None):
        seen.append((spec, owner))
        return ("http://example.test/chat/completions", spec, {"Authorization": "Bearer token"})

    monkeypatch.setattr(ai_interaction, "_resolve_model", fake_resolve_model)

    assert dp._resolve_vl_model("gpt-4o", owner="alice") == (
        "http://example.test/chat/completions",
        "gpt-4o",
        {"Authorization": "Bearer token"},
    )
    assert seen == [("gpt-4o", "alice")]


def test_auto_detected_vision_model_resolution_passes_owner(monkeypatch):
    seen = []

    def fake_resolve_model(spec, owner=None):
        seen.append((spec, owner))
        if spec == "llava":
            return ("http://example.test/chat/completions", spec, {})
        raise ValueError("not available")

    monkeypatch.setattr(ai_interaction, "_resolve_model", fake_resolve_model)

    assert dp._resolve_vl_model("", owner="alice") == (
        "http://example.test/chat/completions",
        "llava",
        {},
    )
    assert seen
    assert all(owner == "alice" for _spec, owner in seen)


def test_vision_analysis_uses_owner_scoped_primary_and_fallback(monkeypatch, tmp_path):
    seen = {}

    def fake_resolve_vl_model(configured, owner=None):
        seen["primary"] = (configured, owner)
        return ("http://primary.test/chat/completions", "vision-primary", {"X-Test": "1"})

    def fake_fallbacks(owner=None):
        seen["fallback_owner"] = owner
        return []

    def fake_llm_call(url, model, messages, headers=None, timeout=None):
        seen["llm"] = (url, model, headers, timeout, messages)
        return "description"

    monkeypatch.setattr(dp, "_load_vl_settings", lambda: {"vision_enabled": True, "vision_model": "gpt-4o"})
    monkeypatch.setattr(dp, "_resolve_vl_model", fake_resolve_vl_model)
    monkeypatch.setattr(dp, "llm_call", fake_llm_call)

    from src import endpoint_resolver

    monkeypatch.setattr(endpoint_resolver, "resolve_vision_fallback_candidates", fake_fallbacks)

    image = tmp_path / "image.png"
    image.write_bytes(b"not-a-real-png-but-base64-is-enough")

    assert dp.analyze_image_with_vl_result(str(image), owner="alice") == {
        "text": "description",
        "model": "vision-primary",
    }
    assert seen["primary"] == ("gpt-4o", "alice")
    assert seen["fallback_owner"] == "alice"
    assert seen["llm"][:4] == (
        "http://primary.test/chat/completions",
        "vision-primary",
        {"X-Test": "1"},
        120,
    )


def test_request_vision_call_sites_pass_owner():
    chat_source = (ROOT / "src" / "chat_handler.py").read_text()
    processor_source = (ROOT / "src" / "document_processor.py").read_text()
    upload_source = (ROOT / "routes" / "upload_routes.py").read_text()
    document_source = (ROOT / "routes" / "document_routes.py").read_text()
    gallery_source = (ROOT / "routes" / "gallery" / "gallery_routes.py").read_text()
    memory_source = (ROOT / "routes" / "memory_routes.py").read_text()

    assert 'analyze_image_with_vl_result(file_info["path"], owner=owner)' in chat_source
    assert "analyze_image_with_vl(path, owner=current_user)" in upload_source
    assert "_process_pdf(path, owner=owner)" in processor_source
    assert "_process_pdf(pdf_path, owner=user)" in document_source
    assert "_resolve_vl_model(vl_model, owner=user)" in document_source
    assert "_resolve_vl_model(configured, owner=user)" in gallery_source
    assert "_process_pdf(tmp_path, owner=_owner(request))" in memory_source
