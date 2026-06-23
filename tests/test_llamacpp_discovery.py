"""Tests for llama.cpp (llama-server) local discovery: the default scan list
includes llama-server's port 8080, and `_fingerprint_provider` identifies a
llama-server via its native ``/props`` endpoint without misfiring on LM Studio,
Ollama, or plain OpenAI-compatible servers.

Companion to test_lmstudio_discovery.py; the llama.cpp fingerprint is checked
*after* the LM Studio one, so LM Studio still wins when both could match.
"""
from src.model_discovery import ModelDiscovery


class _FakeResponse:
    def __init__(self, payload, ok=True):
        self._payload = payload
        self.is_success = ok

    def json(self):
        return self._payload


# ════════════════════════════════════════════════════════════
# discover_models — scan list includes 8080 (llama-server default)
# ════════════════════════════════════════════════════════════

class TestLlamaCppScanPort:
    def test_discover_models_scans_port_8080(self, monkeypatch):
        """llama-server's default port 8080 must be among the scan targets."""
        discovery = ModelDiscovery(default_host="localhost")
        scanned_ports = []

        def fake_check_port(host, port):
            scanned_ports.append(port)
            return None

        monkeypatch.setattr(discovery, "_check_port", fake_check_port)
        monkeypatch.setattr(
            "src.model_discovery.discover_tailscale_hosts", lambda: [],
        )

        discovery.discover_models()
        assert 8080 in scanned_ports


# ════════════════════════════════════════════════════════════
# _fingerprint_provider — llama-server via /props
# ════════════════════════════════════════════════════════════

class TestLlamaCppFingerprint:
    # A representative llama-server /props payload (trimmed to the keys the
    # fingerprint relies on).
    LLAMACPP_PROPS = {
        "default_generation_settings": {"n_ctx": 4096, "temperature": 0.8},
        "total_slots": 1,
        "chat_template": "{{ messages }}",
        "model_path": "/models/gemma-4-12b-it-Q4_K_M.gguf",
    }

    def test_llamacpp_props_detected(self, monkeypatch):
        """A server that isn't LM Studio but answers /props as llama-server →
        'llamacpp'."""
        discovery = ModelDiscovery(default_host="localhost")

        def fake_get(url, timeout=None):
            if url.endswith("/api/v1/models"):
                # OpenAI-compatible shape, not the LM Studio native shape.
                return _FakeResponse({"data": [{"id": "gemma-4-12b"}]})
            if url.endswith("/props"):
                return _FakeResponse(self.LLAMACPP_PROPS)
            return _FakeResponse({}, ok=False)

        monkeypatch.setattr("src.model_discovery.httpx.get", fake_get)
        assert discovery._fingerprint_provider("localhost", 8080) == "llamacpp"

    def test_lmstudio_still_wins_when_both_match(self, monkeypatch):
        """If /api/v1/models reports the LM Studio native shape, LM Studio is
        returned even when /props would also match."""
        discovery = ModelDiscovery(default_host="localhost")
        lmstudio_native = {
            "models": [{"type": "llm", "key": "qwen3.6-27b",
                        "architecture": "qwen35", "format": "gguf"}]
        }

        def fake_get(url, timeout=None):
            if url.endswith("/api/v1/models"):
                return _FakeResponse(lmstudio_native)
            if url.endswith("/props"):
                return _FakeResponse(self.LLAMACPP_PROPS)
            return _FakeResponse({}, ok=False)

        monkeypatch.setattr("src.model_discovery.httpx.get", fake_get)
        assert discovery._fingerprint_provider("localhost", 8080) == "lmstudio"

    def test_props_without_llamacpp_keys_not_detected(self, monkeypatch):
        """A /props-style response lacking llama-server marker keys → None."""
        discovery = ModelDiscovery(default_host="localhost")

        def fake_get(url, timeout=None):
            if url.endswith("/api/v1/models"):
                return _FakeResponse({"data": []})
            if url.endswith("/props"):
                return _FakeResponse({"unrelated": "value"})
            return _FakeResponse({}, ok=False)

        monkeypatch.setattr("src.model_discovery.httpx.get", fake_get)
        assert discovery._fingerprint_provider("localhost", 8080) is None

    def test_props_unreachable_returns_none(self, monkeypatch):
        """No /api/v1/models and a failing /props → None (not an exception)."""
        discovery = ModelDiscovery(default_host="localhost")

        def fake_get(url, timeout=None):
            if url.endswith("/api/v1/models"):
                return _FakeResponse({}, ok=False)
            raise OSError("connection refused")

        monkeypatch.setattr("src.model_discovery.httpx.get", fake_get)
        assert discovery._fingerprint_provider("localhost", 8080) is None

    def test_check_port_attaches_llamacpp_provider(self, monkeypatch):
        """End-to-end: _check_port tags a discovered llama-server as 'llamacpp'."""
        discovery = ModelDiscovery(default_host="localhost")

        def fake_get(url, timeout=None):
            if url.endswith("/v1/models"):
                return _FakeResponse({"data": [{"id": "gemma-4-12b"}]})
            if url.endswith("/api/v1/models"):
                return _FakeResponse({"data": [{"id": "gemma-4-12b"}]})
            if url.endswith("/props"):
                return _FakeResponse(self.LLAMACPP_PROPS)
            return _FakeResponse({}, ok=False)

        monkeypatch.setattr("src.model_discovery.httpx.get", fake_get)
        result = discovery._check_port("localhost", 8080)
        assert result is not None
        assert result["provider"] == "llamacpp"
        assert result["models"] == ["gemma-4-12b"]


# ════════════════════════════════════════════════════════════
# Docker loopback rewrite — host.docker.internal:8080 in scan
# ════════════════════════════════════════════════════════════

class TestDockerLoopbackScan:
    def test_host_docker_internal_in_scan_hosts(self, monkeypatch):
        """When no LLM_HOSTS env override is set, host.docker.internal must be
        included in the scan host list so llama-server on the Docker host is
        discovered from inside the container."""
        monkeypatch.delenv("LLM_HOSTS", raising=False)
        monkeypatch.setattr(
            "src.model_discovery.discover_tailscale_hosts", lambda: [],
        )
        discovery = ModelDiscovery(default_host="localhost")
        hosts = discovery._get_hosts()
        assert "host.docker.internal" in hosts

    def test_discovered_endpoint_url_uses_provided_host(self, monkeypatch):
        """When host.docker.internal:8080 is probed, the returned base_url
        contains host.docker.internal — not a rewritten 127.0.0.1."""
        from src.model_discovery import ModelDiscovery as _MD

        discovery = _MD(default_host="localhost")

        def fake_get(url, timeout=None):
            if url.endswith("/v1/models") or url.endswith("/api/v1/models"):
                return _FakeResponse({"data": [{"id": "gemma-4-12b"}]})
            if url.endswith("/props"):
                return _FakeResponse({
                    "default_generation_settings": {"n_ctx": 4096},
                    "total_slots": 1,
                    "chat_template": "{{ messages }}",
                })
            return _FakeResponse({}, ok=False)

        monkeypatch.setattr("src.model_discovery.httpx.get", fake_get)
        result = discovery._check_port("host.docker.internal", 8080)
        assert result is not None
        assert "host.docker.internal" in result["url"]
        assert "127.0.0.1" not in result["url"]
