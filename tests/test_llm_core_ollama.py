"""Regression tests for native Ollama Cloud provider handling."""
import httpx

from src import llm_core


def test_detects_ollama_cloud_native_provider():
    assert llm_core._detect_provider("https://ollama.com/api") == "ollama"
    assert llm_core._detect_provider("https://ollama.com/api/chat") == "ollama"


def test_llm_call_posts_native_ollama_payload(monkeypatch):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        seen["json"] = json
        seen["timeout"] = timeout
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={"message": {"content": "OK"}, "done": True},
        )

    monkeypatch.setattr(llm_core.httpx, "post", fake_post)

    result = llm_core.llm_call(
        "https://ollama.com/api",
        "gpt-oss:120b-test",
        [{"role": "user", "content": "Say OK"}],
        temperature=0.2,
        max_tokens=7,
        headers={"Authorization": "Bearer ollama-key"},
        timeout=11,
    )

    assert result == "OK"
    assert seen["url"] == "https://ollama.com/api/chat"
    assert seen["headers"]["Authorization"] == "Bearer ollama-key"
    assert seen["json"]["stream"] is False
    assert seen["json"]["options"] == {"temperature": 0.2, "num_predict": 7}
