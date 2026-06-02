"""Tests for LM Studio model discovery: port scanning, env host scanning,
and native-API provider fingerprinting."""
from src.model_discovery import ModelDiscovery


class _FakeResponse:
    def __init__(self, payload, ok=True):
        self._payload = payload
        self.is_success = ok

    def json(self):
        return self._payload


# ════════════════════════════════════════════════════════════
# ModelDiscovery — ports list includes 1234
# ════════════════════════════════════════════════════════════

class TestModelDiscoveryPorts:
    def test_discover_models_scans_port_1234(self, monkeypatch):
        """discover_models must include port 1234 among the scan targets."""
        discovery = ModelDiscovery(default_host="localhost")
        scanned_ports = []

        def fake_check_port(host, port):
            scanned_ports.append(port)
            return None

        monkeypatch.setattr(discovery, "_check_port", fake_check_port)
        monkeypatch.setattr(
            "src.model_discovery.discover_tailscale_hosts",
            lambda: [],
        )

        discovery.discover_models()
        assert 1234 in scanned_ports

    def test_discover_models_scans_custom_lm_studio_port(self, monkeypatch):
        """A non-default port in LM_STUDIO_URL must be added to the scan targets."""
        monkeypatch.delenv("LLM_HOSTS", raising=False)
        monkeypatch.setenv("LM_STUDIO_URL", "http://my-lm-box:5000")
        monkeypatch.setattr(
            "src.model_discovery.discover_tailscale_hosts", lambda: [],
        )
        discovery = ModelDiscovery(default_host="localhost")
        scanned = []

        def fake_check_port(host, port):
            scanned.append((host, port))
            return None

        monkeypatch.setattr(discovery, "_check_port", fake_check_port)
        discovery.discover_models()
        assert ("my-lm-box", 5000) in scanned


# ════════════════════════════════════════════════════════════
# _fingerprint_provider — native API identification
# ════════════════════════════════════════════════════════════

class TestFingerprintProvider:
    LMSTUDIO_NATIVE = {
        "models": [
            {"type": "llm", "key": "qwen3.6-27b", "architecture": "qwen35",
             "quantization": {"name": "Q5_K_XL"}, "format": "gguf"},
        ]
    }

    def test_lmstudio_native_format_detected(self, monkeypatch):
        discovery = ModelDiscovery(default_host="localhost")
        monkeypatch.setattr(
            "src.model_discovery.httpx.get",
            lambda url, timeout=None: _FakeResponse(self.LMSTUDIO_NATIVE),
        )
        assert discovery._fingerprint_provider("localhost", 1234) == "lmstudio"

    def test_lmstudio_detected_on_nonstandard_port(self, monkeypatch):
        discovery = ModelDiscovery(default_host="localhost")
        monkeypatch.setattr(
            "src.model_discovery.httpx.get",
            lambda url, timeout=None: _FakeResponse(self.LMSTUDIO_NATIVE),
        )
        assert discovery._fingerprint_provider("localhost", 8080) == "lmstudio"

    def test_openai_compatible_server_not_lmstudio(self, monkeypatch):
        discovery = ModelDiscovery(default_host="localhost")
        monkeypatch.setattr(
            "src.model_discovery.httpx.get",
            lambda url, timeout=None: _FakeResponse({"data": [{"id": "gpt-4o"}]}, ok=False),
        )
        assert discovery._fingerprint_provider("localhost", 8000) is None

    def test_ollama_tags_shape_not_lmstudio(self, monkeypatch):
        discovery = ModelDiscovery(default_host="localhost")
        ollama_shape = {"models": [{"name": "llama3", "modified_at": "x", "size": 1}]}
        monkeypatch.setattr(
            "src.model_discovery.httpx.get",
            lambda url, timeout=None: _FakeResponse(ollama_shape),
        )
        assert discovery._fingerprint_provider("localhost", 11434) is None

    def test_unreachable_returns_none(self, monkeypatch):
        discovery = ModelDiscovery(default_host="localhost")
        def boom(url, timeout=None):
            raise OSError("connection refused")
        monkeypatch.setattr("src.model_discovery.httpx.get", boom)
        assert discovery._fingerprint_provider("localhost", 1234) is None

    def test_check_port_attaches_provider(self, monkeypatch):
        discovery = ModelDiscovery(default_host="localhost")

        def fake_get(url, timeout=None):
            if url.endswith("/api/v1/models"):
                return _FakeResponse(self.LMSTUDIO_NATIVE)
            return _FakeResponse({"data": [{"id": "qwen3.6-27b"}]})

        monkeypatch.setattr("src.model_discovery.httpx.get", fake_get)
        result = discovery._check_port("localhost", 1234)
        assert result is not None
        assert result["provider"] == "lmstudio"
        assert result["models"] == ["qwen3.6-27b"]


# ════════════════════════════════════════════════════════════
# _get_hosts — LM_STUDIO_URL env var
# ════════════════════════════════════════════════════════════

class TestGetHostsLmStudioUrl:
    def test_lm_studio_url_adds_host_default_branch(self, monkeypatch):
        """LM_STUDIO_URL hostname must appear in hosts when Tailscale is absent."""
        monkeypatch.delenv("LLM_HOSTS", raising=False)
        monkeypatch.setenv("LM_STUDIO_URL", "http://my-lm-box:1234")
        monkeypatch.setattr(
            "src.model_discovery.discover_tailscale_hosts",
            lambda: [],
        )
        discovery = ModelDiscovery(default_host="localhost")
        hosts = discovery._get_hosts()
        assert "my-lm-box" in hosts

    def test_lm_studio_url_adds_host_tailscale_branch(self, monkeypatch):
        """LM_STUDIO_URL hostname must also appear when Tailscale hosts are present."""
        monkeypatch.delenv("LLM_HOSTS", raising=False)
        monkeypatch.setenv("LM_STUDIO_URL", "http://my-lm-box:1234")
        monkeypatch.setattr(
            "src.model_discovery.discover_tailscale_hosts",
            lambda: ["100.64.0.1"],
        )
        discovery = ModelDiscovery(default_host="localhost")
        hosts = discovery._get_hosts()
        assert "my-lm-box" in hosts

    def test_lm_studio_url_adds_host_llm_hosts_branch(self, monkeypatch):
        """LM_STUDIO_URL hostname must also appear when LLM_HOSTS is set."""
        monkeypatch.setenv("LLM_HOSTS", "10.0.0.5")
        monkeypatch.setenv("LM_STUDIO_URL", "http://my-lm-box:1234")
        discovery = ModelDiscovery(default_host="localhost")
        hosts = discovery._get_hosts()
        assert "my-lm-box" in hosts

    def test_lm_studio_url_no_duplicate(self, monkeypatch):
        """If the hostname is already in the list it should not be added twice."""
        monkeypatch.delenv("LLM_HOSTS", raising=False)
        monkeypatch.setenv("LM_STUDIO_URL", "http://localhost:1234")
        monkeypatch.setattr(
            "src.model_discovery.discover_tailscale_hosts",
            lambda: [],
        )
        discovery = ModelDiscovery(default_host="localhost")
        hosts = discovery._get_hosts()
        assert hosts.count("localhost") == 1

    def test_lm_studio_url_not_set_no_extra_host(self, monkeypatch):
        """When LM_STUDIO_URL is absent, no phantom host is added."""
        monkeypatch.delenv("LLM_HOSTS", raising=False)
        monkeypatch.delenv("LM_STUDIO_URL", raising=False)
        monkeypatch.setattr(
            "src.model_discovery.discover_tailscale_hosts",
            lambda: [],
        )
        discovery = ModelDiscovery(default_host="localhost")
        hosts = discovery._get_hosts()
        # Only localhost + host.docker.internal expected
        assert "my-lm-box" not in hosts
