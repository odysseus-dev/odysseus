"""llama.cpp slot-affinity fields must never reach cloud providers (#3793).

_apply_local_cache_affinity adds session_id + cache_prompt to outgoing
payloads for KV-cache slot affinity (#2927). The old gate treated any unknown
OpenAI-compatible host as self-hosted, so strict cloud APIs added as custom
endpoints (Mistral at api.mistral.ai) received the extra fields and rejected
every request with 422 extra_forbidden. Self-hosted now also requires the
endpoint to resolve as local: loopback/private/tailscale host, or endpoint
kind explicitly configured as "local".
"""
import pytest

import src.llm_core as llm_core
import src.model_context as model_context


def _affinity_fields(url, monkeypatch, kind=None):
    monkeypatch.setattr(model_context, "_configured_endpoint_kind", lambda _u: kind)
    payload = {}
    llm_core._apply_local_cache_affinity(payload, url, "sess-123")
    return payload


def test_mistral_cloud_api_gets_no_affinity_fields(monkeypatch):
    # The #3793 repro: Mistral rejects unknown body fields with 422.
    payload = _affinity_fields("https://api.mistral.ai/v1", monkeypatch)
    assert payload == {}


def test_openai_api_gets_no_affinity_fields(monkeypatch):
    payload = _affinity_fields("https://api.openai.com/v1", monkeypatch)
    assert payload == {}


def test_unknown_public_host_gets_no_affinity_fields(monkeypatch):
    # Any strict cloud provider added as a custom endpoint, not just Mistral.
    payload = _affinity_fields("https://llm.example-cloud.com/v1", monkeypatch)
    assert payload == {}


@pytest.mark.parametrize("url", [
    "https://10.example-cloud.com/v1",
    "https://172.16.example-cloud.com/v1",
    "https://192.168.example-cloud.com/v1",
])
def test_private_prefix_dns_host_gets_no_affinity_fields(monkeypatch, url):
    payload = _affinity_fields(url, monkeypatch)
    assert payload == {}


def test_localhost_server_gets_affinity_fields(monkeypatch):
    payload = _affinity_fields("http://localhost:8080/v1", monkeypatch)
    assert payload == {"session_id": "sess-123", "cache_prompt": True}


def test_private_lan_server_gets_affinity_fields(monkeypatch):
    payload = _affinity_fields("http://192.168.1.50:8000/v1", monkeypatch)
    assert payload == {"session_id": "sess-123", "cache_prompt": True}


def test_public_host_with_local_kind_override_gets_affinity_fields(monkeypatch):
    # Escape hatch: a self-hosted llama.cpp exposed via a tunnel keeps the
    # slot-affinity hint when its endpoint kind is configured as "local".
    payload = _affinity_fields("https://my-llama.example.com/v1", monkeypatch, kind="local")
    assert payload == {"session_id": "sess-123", "cache_prompt": True}


def test_no_session_id_is_a_noop(monkeypatch):
    monkeypatch.setattr(model_context, "_configured_endpoint_kind", lambda _u: None)
    payload = {}
    llm_core._apply_local_cache_affinity(payload, "http://localhost:8080/v1", None)
    assert payload == {}


# Cloud-host sweep absorbed from #3839 (credit: Shabablinchikow) - every cloud
# API that falls through provider detection to the OpenAI-compatible default
# must stay clean, not just the Mistral host from the original report.
@pytest.mark.parametrize("url", [
    "https://api.mistral.ai/v1/chat/completions",
    "https://api.deepseek.com/v1/chat/completions",
    "https://api.x.ai/v1/chat/completions",
    "https://api.together.xyz/v1/chat/completions",
    "https://api.fireworks.ai/inference/v1/chat/completions",
    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
])
def test_cloud_openai_compatible_hosts_get_no_affinity_fields(monkeypatch, url):
    assert _affinity_fields(url, monkeypatch) == {}


# Tailscale CGNAT boundaries (review finding on #3945): only 100.64.0.0/10 is
# Tailscale; the rest of 100.0.0.0/8 contains public ranges, and a strict
# provider addressed by one must not receive the llama.cpp extras.
def test_host_just_below_cgnat_gets_no_affinity_fields(monkeypatch):
    assert _affinity_fields("http://100.63.255.255/v1", monkeypatch) == {}


def test_host_just_above_cgnat_gets_no_affinity_fields(monkeypatch):
    assert _affinity_fields("http://100.128.0.1/v1", monkeypatch) == {}


@pytest.mark.parametrize("host", ["100.64.0.1", "100.100.50.2", "100.127.255.254"])
def test_hosts_inside_cgnat_get_affinity_fields(monkeypatch, host):
    payload = _affinity_fields(f"http://{host}:8080/v1", monkeypatch)
    assert payload == {"session_id": "sess-123", "cache_prompt": True}
