import json
from services.hwfit import hf_discovery


class _FakeResponse:
    def __init__(self, payload, link_header=None):
        self._payload = payload
        self.headers = {"Link": link_header} if link_header else {}

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_refresh_hf_collection_models_cache_attaches_gguf_sources(monkeypatch, tmp_path):
    cache_file = tmp_path / "hf_collection_models.json"
    monkeypatch.setattr(hf_discovery, "HF_COLLECTION_MODELS_CACHE", cache_file)
    monkeypatch.setattr(
        hf_discovery,
        "HF_COLLECTION_SOURCES",
        (
            {"key": "openai", "owner": "openai", "provider": "openai"},
            {"key": "qwen", "owner": "Qwen", "provider": "Qwen"},
        ),
    )
    monkeypatch.setattr(
        hf_discovery,
        "GGUF_PUBLISHERS",
        ("unsloth", "ggml-org", "bartowski", "unreachable-org"),
    )

    def fake_urlopen(req, timeout=20):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "api/collections" in url and "owner=openai" in url:
            return _FakeResponse([
                {
                    "title": "OpenAI Open Models",
                    "items": [
                        {
                            "id": "openai/gpt-oss-20b",
                            "type": "model",
                            "pipeline_tag": "text-generation",
                            "numParameters": 20_000_000_000,
                            "downloads": 50000,
                        }
                    ],
                }
            ])
        if "api/collections" in url and "owner=Qwen" in url:
            return _FakeResponse([
                {
                    "title": "Qwen3.8",
                    "items": [
                        {
                            "id": "Qwen/Qwen3.8-27B",
                            "type": "model",
                            "pipeline_tag": "text-generation",
                            "numParameters": 27_000_000_000,
                            "downloads": 40000,
                        },
                        {
                            "id": "Qwen/Qwen3.8-0.6B",
                            "type": "model",
                            "pipeline_tag": "text-generation",
                            "numParameters": 600_000_000,
                            "downloads": 10000,
                        },
                    ],
                }
            ])
        if "api/models" in url and "author=unsloth" in url:
            return _FakeResponse([
                {"id": "unsloth/gpt-oss-20b-GGUF"},
                {"id": "unsloth/Qwen3.8-27B-GGUF"},
            ])
        if "api/models" in url and "author=ggml-org" in url:
            return _FakeResponse([
                {"id": "ggml-org/gpt-oss-20b-GGUF"},
            ])
        if "api/models" in url and "author=bartowski" in url:
            return _FakeResponse([
                {"id": "bartowski/Qwen_Qwen3.8-27B-GGUF"},
            ])
        if "api/models" in url and "author=unreachable-org" in url:
            raise OSError("temporary network timeout")
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(hf_discovery.urllib.request, "urlopen", fake_urlopen)

    rows = hf_discovery.refresh_hf_collection_models_cache(force=True)
    by_name = {row["name"]: row for row in rows}

    assert by_name["openai/gpt-oss-20b"]["gguf_sources"] == [
        {"repo": "unsloth/gpt-oss-20b-GGUF", "file": "*Q4_K_M*.gguf"},
        {"repo": "ggml-org/gpt-oss-20b-GGUF", "file": "*Q4_K_M*.gguf"},
    ]
    assert by_name["Qwen/Qwen3.8-27B"]["gguf_sources"] == [
        {"repo": "unsloth/Qwen3.8-27B-GGUF", "file": "*Q4_K_M*.gguf"},
        {"repo": "bartowski/Qwen_Qwen3.8-27B-GGUF", "file": "*Q4_K_M*.gguf"},
    ]
    assert "gguf_sources" not in by_name["Qwen/Qwen3.8-0.6B"]
    assert all(
        src["repo"] != name and src["file"].endswith(".gguf")
        for name, row in by_name.items()
        for src in row.get("gguf_sources", [])
    )
