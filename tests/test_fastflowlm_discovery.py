"""Tests for FastFlowLM discovery and endpoint handling.

FastFlowLM (https://fastflowlm.com) is an Ollama-style local runtime for AMD
Ryzen AI NPUs that exposes an OpenAI-compatible server on default port 52625.
Because it is OpenAI-compatible, it needs no provider/endpoint special-casing —
only (1) its default port in the discovery scan list and (2) a FASTFLOWLM_URL
env override, mirroring how LM Studio (port 1234, LM_STUDIO_URL) is wired.
"""
import pytest

from src.model_discovery import ModelDiscovery
from src import endpoint_resolver
from src.endpoint_resolver import build_chat_url, build_models_url
from src.model_context import _is_local_endpoint


# ════════════════════════════════════════════════════════════
# ModelDiscovery — default scan list includes 52625
# ════════════════════════════════════════════════════════════

class TestFastFlowLMPorts:
    def test_discover_models_scans_port_52625(self, monkeypatch):
        """discover_models must include FastFlowLM's default port 52625."""
        discovery = ModelDiscovery(default_host="localhost")
        scanned_ports = []

        monkeypatch.setattr(discovery, "_check_port",
                            lambda host, port: scanned_ports.append(port))
        monkeypatch.setattr("src.model_discovery.discover_tailscale_hosts", lambda: [])
        monkeypatch.delenv("LLM_HOSTS", raising=False)

        discovery.discover_models()
        assert 52625 in scanned_ports

    def test_discover_models_scans_custom_fastflowlm_port(self, monkeypatch):
        """A non-default port in FASTFLOWLM_URL must be added to the scan targets."""
        monkeypatch.delenv("LLM_HOSTS", raising=False)
        monkeypatch.setenv("FASTFLOWLM_URL", "http://flm-box:9999")
        monkeypatch.setattr("src.model_discovery.discover_tailscale_hosts", lambda: [])
        discovery = ModelDiscovery(default_host="localhost")
        scanned = []

        monkeypatch.setattr(discovery, "_check_port",
                            lambda host, port: scanned.append((host, port)))
        discovery.discover_models()
        assert ("flm-box", 9999) in scanned


# ════════════════════════════════════════════════════════════
# _get_hosts — FASTFLOWLM_URL env var
# ════════════════════════════════════════════════════════════

class TestGetHostsFastFlowLMUrl:
    def test_fastflowlm_url_adds_host_default_branch(self, monkeypatch):
        """FASTFLOWLM_URL hostname must appear in hosts when Tailscale is absent."""
        monkeypatch.delenv("LLM_HOSTS", raising=False)
        monkeypatch.setenv("FASTFLOWLM_URL", "http://flm-box:52625")
        monkeypatch.setattr("src.model_discovery.discover_tailscale_hosts", lambda: [])
        discovery = ModelDiscovery(default_host="localhost")
        assert "flm-box" in discovery._get_hosts()

    def test_fastflowlm_url_not_set_no_extra_host(self, monkeypatch):
        """When FASTFLOWLM_URL is absent, no phantom host is added."""
        monkeypatch.delenv("LLM_HOSTS", raising=False)
        monkeypatch.delenv("FASTFLOWLM_URL", raising=False)
        monkeypatch.setattr("src.model_discovery.discover_tailscale_hosts", lambda: [])
        discovery = ModelDiscovery(default_host="localhost")
        assert "flm-box" not in discovery._get_hosts()


# ════════════════════════════════════════════════════════════
# OpenAI-compatible routing + local-endpoint treatment (regression locks:
# these document why no provider/endpoint/context changes are needed)
# ════════════════════════════════════════════════════════════

class TestFastFlowLMOpenAICompat:
    @pytest.fixture(autouse=True)
    def _stub_dns(self, monkeypatch):
        monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda u: u)

    def test_chat_url_is_openai_compatible(self):
        assert build_chat_url("http://localhost:52625/v1") == \
            "http://localhost:52625/v1/chat/completions"

    def test_models_url_is_openai_compatible(self):
        assert build_models_url("http://localhost:52625/v1") == \
            "http://localhost:52625/v1/models"

    def test_endpoint_treated_as_local(self):
        assert _is_local_endpoint("http://localhost:52625/v1/chat/completions") is True
