"""Tests for the GitHub Copilot provider integration (src/copilot.py + wiring)."""
import types
import pytest

from src import copilot


# ── Provider detection ─────────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://api.githubcopilot.com", True),
    ("https://api.githubcopilot.com/chat/completions", True),
    ("https://copilot-api.acme.ghe.com", True),
    ("https://sub.githubcopilot.com", True),
    ("https://api.openai.com/v1", False),
    ("https://githubcopilot.com.evil.test", False),  # lookalike host
    ("", False),
    (None, False),
])
def test_is_copilot_base(url, expected):
    assert copilot.is_copilot_base(url) is expected


def test_detect_provider_copilot():
    from src.llm_core import _detect_provider
    assert _detect_provider("https://api.githubcopilot.com") == "copilot"
    assert _detect_provider("https://copilot-api.acme.ghe.com") == "copilot"
    # lookalike must not be classified as copilot
    assert _detect_provider("https://githubcopilot.com.evil.test") == "openai"


def test_enterprise_base():
    assert copilot.enterprise_base(None) == "https://api.githubcopilot.com"
    assert copilot.enterprise_base("https://acme.ghe.com/") == "https://copilot-api.acme.ghe.com"
    assert copilot.enterprise_base("acme.ghe.com") == "https://copilot-api.acme.ghe.com"


# ── Headers ────────────────────────────────────────────────────────────────

def test_copilot_headers_core():
    h = copilot.copilot_headers("TOK")
    assert h["Authorization"] == "Bearer TOK"
    assert h["X-GitHub-Api-Version"] == copilot.COPILOT_API_VERSION
    assert h["Openai-Intent"] == "conversation-edits"
    assert h["Copilot-Integration-Id"]
    assert h["x-initiator"] == "user"
    assert "Copilot-Vision-Request" not in h


def test_copilot_headers_agent_vision():
    h = copilot.copilot_headers("TOK", agent=True, vision=True)
    assert h["x-initiator"] == "agent"
    assert h["Copilot-Vision-Request"] == "true"


def test_copilot_headers_no_token():
    h = copilot.copilot_headers(None)
    assert "Authorization" not in h
    assert h["X-GitHub-Api-Version"] == copilot.COPILOT_API_VERSION


def test_build_headers_dispatches_to_copilot():
    from src.endpoint_resolver import build_headers
    h = build_headers("TOK", "https://api.githubcopilot.com")
    assert h["Authorization"] == "Bearer TOK"
    assert h["X-GitHub-Api-Version"] == copilot.COPILOT_API_VERSION
    # OpenAI base must stay plain bearer (no copilot headers)
    ho = build_headers("TOK", "https://api.openai.com/v1")
    assert "X-GitHub-Api-Version" not in ho


# ── Per-request flags ──────────────────────────────────────────────────────

def test_request_flags_user():
    assert copilot.request_flags([{"role": "user", "content": "hi"}]) == (False, False)


def test_request_flags_agent_when_tool_last():
    msgs = [{"role": "user", "content": "hi"}, {"role": "tool", "content": "x"}]
    assert copilot.request_flags(msgs) == (True, False)


def test_request_flags_vision():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "data:..."}},
    ]}]
    agent, vision = copilot.request_flags(msgs)
    assert vision is True


def test_request_flags_non_dict_last_message_does_not_crash():
    # A client can send a bare-string (non-dict) last element; before the
    # isinstance guard this raised AttributeError on last.get("role").
    assert copilot.request_flags(["hi"]) == (False, False)
    assert copilot.request_flags([{"role": "user"}, "trailing"]) == (False, False)


def test_request_flags_empty_and_none():
    assert copilot.request_flags([]) == (False, False)
    assert copilot.request_flags(None) == (False, False)


def test_apply_request_headers_mutates():
    h = {"X-GitHub-Api-Version": "v"}
    copilot.apply_request_headers(h, [{"role": "tool", "content": "x"}])
    assert h["x-initiator"] == "agent"


# ── Model discovery ────────────────────────────────────────────────────────

def _fake_response(payload):
    r = types.SimpleNamespace()
    r.json = lambda: payload
    r.raise_for_status = lambda: None
    return r


def test_fetch_models_filters_picker(monkeypatch):
    payload = {"data": [
        {"id": "gpt-4o", "model_picker_enabled": True,
         "capabilities": {"supports": {"tool_calls": True, "vision": True}}},
        {"id": "internal-embed", "model_picker_enabled": False,
         "capabilities": {"supports": {"tool_calls": False}}},
        {"id": "claude-3.5", "model_picker_enabled": True,
         "capabilities": {"supports": {"tool_calls": True}}},
    ]}
    monkeypatch.setattr(copilot.httpx, "get", lambda *a, **k: _fake_response(payload))
    models = copilot.fetch_models("https://api.githubcopilot.com", "TOK")
    ids = {m["id"] for m in models}
    assert ids == {"gpt-4o", "claude-3.5"}
    gpt = next(m for m in models if m["id"] == "gpt-4o")
    assert gpt["tool_calls"] is True and gpt["vision"] is True


def test_fetch_models_fallback_when_no_picker(monkeypatch):
    payload = {"data": [
        {"id": "m1", "capabilities": {"supports": {}}},
        {"id": "m2", "capabilities": {"supports": {}}},
    ]}
    monkeypatch.setattr(copilot.httpx, "get", lambda *a, **k: _fake_response(payload))
    models = copilot.fetch_models("https://api.githubcopilot.com", "TOK")
    assert {m["id"] for m in models} == {"m1", "m2"}


# ── Device flow ────────────────────────────────────────────────────────────

def test_request_device_code(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _fake_response({"device_code": "DC", "user_code": "ABCD-1234",
                               "verification_uri": "https://github.com/login/device",
                               "interval": 5, "expires_in": 900})

    monkeypatch.setattr(copilot.httpx, "post", fake_post)
    data = copilot.request_device_code()
    assert data["device_code"] == "DC"
    assert captured["url"] == "https://github.com/login/device/code"
    assert captured["json"]["client_id"] == copilot.COPILOT_CLIENT_ID
    assert captured["json"]["scope"] == "read:user"


def test_poll_access_token(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _fake_response({"access_token": "GHTOKEN"})

    monkeypatch.setattr(copilot.httpx, "post", fake_post)
    data = copilot.poll_access_token("github.com", "DC")
    assert data["access_token"] == "GHTOKEN"
    assert captured["json"]["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"
    assert captured["json"]["device_code"] == "DC"


def test_agent_loop_host_allowlisted():
    from src.agent_loop import _API_HOSTS
    assert "api.githubcopilot.com" in _API_HOSTS
