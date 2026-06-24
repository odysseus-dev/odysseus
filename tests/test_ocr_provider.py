from pathlib import Path

import httpx

from src import ocr_provider


def test_ocr_provider_disabled_returns_empty(monkeypatch, tmp_path: Path):
    sample = tmp_path / "scan.pdf"
    sample.write_bytes(b"%PDF")
    monkeypatch.setattr(ocr_provider, "ocr_settings", lambda: {"ocr_enabled": False})

    assert ocr_provider.extract_ocr_text_sync(str(sample)) == ""


def test_ocr_provider_posts_contract_and_extracts_chosen_text(monkeypatch, tmp_path: Path):
    sample = tmp_path / "scan.pdf"
    sample.write_bytes(b"%PDF")
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(
            200,
            json={"chosenText": "Scanned text", "requestId": "ocr_123"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(
        ocr_provider,
        "ocr_settings",
        lambda: {
            "ocr_enabled": True,
            "ocr_service_url": "https://internal-ocr.example/v1/ocr",
            "ocr_api_key": "secret",
            "ocr_quality_mode": "quality",
            "ocr_provider": "skill-ocr",
        },
    )
    monkeypatch.setattr(ocr_provider.httpx, "post", fake_post)

    assert ocr_provider.extract_ocr_text_sync(str(sample)) == "Scanned text"
    assert calls[0][0] == "https://internal-ocr.example/v1/ocr"
    assert calls[0][1]["data"]["purpose"] == "odysseus.document"
    assert calls[0][1]["data"]["mode"] == "quality"
    assert calls[0][1]["data"]["retention"] == "none"
    assert calls[0][1]["data"]["redaction"] == "strict"
    assert calls[0][1]["headers"] == {"Authorization": "Bearer secret"}


def test_ocr_provider_extracts_line_payloads():
    assert ocr_provider._extract_text({"lines": [{"text": "Line 1"}, "Line 2"]}) == "Line 1\nLine 2"
