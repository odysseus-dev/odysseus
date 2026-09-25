"""Catalog pick must not pin openai/auto or gpt-6-astra.

DoD for session-tree telemetry: the stability Native pipe must POST Odysseus
``/api/session`` + ``/api/chat_stream`` (phone path), not Agent Server directly.
"""

from pathlib import Path
import importlib.util
import sys

import pytest


def _load_bootstrap():
    path = Path("deploy/openhands/bootstrap_native_llm.py")
    spec = importlib.util.spec_from_file_location("bootstrap_native_llm", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_probe():
    path = Path("scripts/overlay_native_chat_probe.py")
    spec = importlib.util.spec_from_file_location("overlay_native_chat_probe", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_pick_prefers_cx_gpt_55_over_astra():
    mod = _load_bootstrap()
    ids = ["cx/gpt-6-astra", "cx/gpt-5.5", "cx/gpt-5.5-review"]
    assert mod._litellm_id_from_catalog(ids) == "openai/cx/gpt-5.5"


def test_pick_skips_review_and_astra():
    mod = _load_bootstrap()
    ids = ["cx/gpt-6-astra", "cx/gpt-5.5-review", "cx/gpt-5.4"]
    assert mod._litellm_id_from_catalog(ids) == "openai/cx/gpt-5.4"


def test_pick_empty_catalog_uses_pinned_default():
    mod = _load_bootstrap()
    assert mod._litellm_id_from_catalog([]) == "openai/cx/gpt-5.5"


def test_overlay_native_chat_probe_script_exists():
    script = Path("scripts/overlay_native_chat_probe.py")
    text = script.read_text(encoding="utf-8")
    assert script.is_file()
    assert "cx/gpt-5.5" in text
    assert "gpt-6-astra" in text
    assert "openhands-agent-server:8000" in text
    assert "9router:20128" in text
    assert "native-llm-api-key" in text
    assert "def run_odysseus_native_pipe" in text
    assert "/api/session" in text
    assert "/api/chat_stream" in text
    assert "X-Odysseus-Internal-Token" in text
    assert "traceparent" in text or "inject" in text
    wrapper = Path("scripts/run_overlay_native_chat_probe.sh")
    wrap = wrapper.read_text(encoding="utf-8")
    assert wrapper.is_file()
    assert "orchestration-vm" in wrap
    assert "overlay_native_chat_probe.py" in wrap


def test_run_odysseus_native_pipe_posts_session_and_chat_stream(monkeypatch):
    """Phone path: Odysseus session create + chat_stream SSE, never Agent create."""
    mod = _load_probe()
    calls = []

    def fake_form(url, *, fields=None, headers=None, timeout=30):
        calls.append(("form", url, dict(fields or {}), dict(headers or {})))
        if url.endswith("/api/session"):
            return 200, {"id": "ody-sess-1", "model": "automatic"}
        raise AssertionError(f"unexpected form url {url}")

    def fake_sse(url, *, fields=None, headers=None, timeout=120):
        calls.append(("sse", url, dict(fields or {}), dict(headers or {})))
        assert url.endswith("/api/chat_stream")
        return 200, [
            {"type": "execution", "conversation_id": "oh-conv-9"},
            {"delta": "Hi"},
        ]

    monkeypatch.setenv("ODYSSEUS_INTERNAL_TOKEN", "probe-token")
    monkeypatch.setattr(mod, "_http_form", fake_form)
    monkeypatch.setattr(mod, "_http_sse_events", fake_sse)

    report = mod.run_odysseus_native_pipe()
    assert report["ok"] is True
    assert report["session_id"] == "ody-sess-1"
    assert report.get("openhands_conversation_id") == "oh-conv-9"
    assert [c[0] for c in calls] == ["form", "sse"]
    assert calls[0][1].endswith("/api/session")
    assert calls[0][2]["model"] == "automatic"
    assert calls[1][2]["session"] == "ody-sess-1"
    assert calls[1][2]["selected_model"] == "automatic"
    assert calls[1][2]["agent_profile_id"] == "odysseus"
    assert "openhands-agent-server" not in calls[0][1]
    assert "openhands-agent-server" not in calls[1][1]
    for _kind, _url, _fields, headers in calls:
        assert headers.get("X-Odysseus-Internal-Token") == "probe-token"


def test_run_odysseus_native_pipe_requires_internal_token(monkeypatch):
    mod = _load_probe()
    monkeypatch.delenv("ODYSSEUS_INTERNAL_TOKEN", raising=False)
    report = mod.run_odysseus_native_pipe()
    assert report["ok"] is False
    assert "ODYSSEUS_INTERNAL_TOKEN" in (report.get("error") or "")


def test_compose_passes_internal_token_to_odysseus():
    overlay = Path("docker-compose.openhands.yml").read_text(encoding="utf-8")
    assert "ODYSSEUS_INTERNAL_TOKEN=${ODYSSEUS_INTERNAL_TOKEN:-}" in overlay
