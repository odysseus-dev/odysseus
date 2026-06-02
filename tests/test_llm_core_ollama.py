"""Regression tests for native Ollama Cloud provider handling."""
import httpx

from src import llm_core


def test_detects_ollama_cloud_native_provider():
    assert llm_core._detect_provider("https://ollama.com/api") == "ollama"
    assert llm_core._detect_provider("https://ollama.com/api/chat") == "ollama"


def test_detects_venice_provider():
    assert llm_core._detect_provider("https://api.venice.ai/api/v1") == "venice"
    assert llm_core._detect_provider("https://api.venice.ai/api/v1/chat/completions") == "venice"
    assert llm_core._provider_label("https://api.venice.ai/api/v1") == "Venice"


def _capture_chat_call(monkeypatch, url):
    """Run a sync llm_call against `url` with httpx.post stubbed; return the captured request."""
    seen = {}

    def fake_post(target, headers=None, json=None, timeout=None):
        seen["url"] = target
        seen["headers"] = headers
        seen["json"] = json
        request = httpx.Request("POST", target)
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": "OK"}}]},
        )

    monkeypatch.setattr(llm_core.httpx, "post", fake_post)
    # Avoid the cross-call response cache short-circuiting the second request.
    llm_core._response_cache.clear()
    result = llm_core.llm_call(
        url,
        "venice-uncensored",
        [{"role": "user", "content": "Say OK"}],
        temperature=0.2,
        max_tokens=7,
        headers={"Authorization": "Bearer venice-key"},
        timeout=11,
    )
    return result, seen


def test_venice_posts_openai_style_chat_payload(monkeypatch):
    """Venice uses the standard OpenAI-compatible chat payload + Bearer auth."""
    result, seen = _capture_chat_call(monkeypatch, "https://api.venice.ai/api/v1/chat/completions")
    assert result == "OK"
    # Posted verbatim to the given OpenAI-style chat URL (no Anthropic/Ollama rewrite)
    assert seen["url"] == "https://api.venice.ai/api/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer venice-key"
    assert seen["json"]["model"] == "venice-uncensored"
    assert seen["json"]["messages"] == [{"role": "user", "content": "Say OK"}]
    assert seen["json"]["max_tokens"] == 7
    # No native-provider fields leak in
    assert "options" not in seen["json"]


def test_venice_chat_payload_matches_generic_openai_compatible(monkeypatch):
    """Venice must not diverge from any other OpenAI-compatible endpoint.

    Builds the payload for Venice and for a generic self-hosted OpenAI-style
    server and asserts the request bodies are identical (same code path)."""
    _, venice = _capture_chat_call(monkeypatch, "https://api.venice.ai/api/v1/chat/completions")
    _, generic = _capture_chat_call(monkeypatch, "http://localhost:8000/v1/chat/completions")
    assert venice["json"] == generic["json"]
    assert venice["headers"] == generic["headers"]


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
