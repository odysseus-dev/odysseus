"""Regression tests for SSRF hardening on model endpoint routes.

Verifies that POST /model-endpoints/test, POST /model-endpoints, and
PATCH /model-endpoints/{ep_id} reject user-supplied URLs that resolve to
link-local (cloud metadata), non-HTTP schemes, or (when
MODELENDPOINT_BLOCK_PRIVATE_IPS=true) private/loopback addresses — matching
the existing SSRF guard pattern in embedding_routes.py.

A stub resolver is injected so the loopback tests never touch real DNS.
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ── Resolver stub (same pattern as tests/test_url_safety.py) ────────────────
def _resolver(mapping):
    """Create a stub resolver for deterministic DNS in tests."""
    def resolve(host):
        if host in mapping:
            return mapping[host]
        raise OSError(f"unresolvable: {host}")
    return resolve


LOCALHOST_V4 = _resolver({"localhost": ["127.0.0.1"]})
LOCALHOST_V6 = _resolver({"localhost": ["::1"]})


@pytest.fixture
def client(monkeypatch):
    """Build a TestClient with auth disabled and minimal DB stubs."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    # Stub heavy deps that model_routes imports at module level.
    # Use monkeypatch so entries are removed after the test.
    for mod in [
        "src.tls_overrides",
        "src.llm_core",
        "src.settings",
        "src.constants",
        "core.log_safety",
    ]:
        if mod not in sys.modules:
            monkeypatch.setitem(sys.modules, mod, MagicMock())

    # Provide real endpoint_resolver so the route logic works
    if "src.endpoint_resolver" not in sys.modules or isinstance(
        sys.modules["src.endpoint_resolver"], MagicMock
    ):
        er = MagicMock()
        er.normalize_base = lambda url: url
        er.resolve_url = lambda url: url
        er.build_chat_url = lambda base, **kw: base
        er.build_models_url = lambda base, **kw: base + "/v1/models"
        er.build_headers = lambda base, **kw: {}
        monkeypatch.setitem(sys.modules, "src.endpoint_resolver", er)

    # Clear cached model_routes module so it picks up our stubs
    monkeypatch.delitem(sys.modules, "routes.model_routes", raising=False)

    from fastapi.testclient import TestClient
    import routes.model_routes as mr
    from fastapi import FastAPI

    app = FastAPI()
    router = mr.setup_model_routes(model_discovery=None)
    app.include_router(router)
    app.state.auth_enabled = False
    return TestClient(app, raise_server_exceptions=False)


class TestModelEndpointSSRF:
    """SSRF protection for POST /model-endpoints/test."""

    def test_cloud_metadata_blocked(self, client):
        """Cloud metadata IP (169.254.169.254) must be rejected."""
        resp = client.post(
            "/api/model-endpoints/test",
            data={"base_url": "http://169.254.169.254/latest/meta-data/"},
        )
        assert resp.status_code == 400
        assert "Rejected" in resp.json()["detail"]

    def test_non_http_scheme_blocked(self, client):
        """Non-HTTP schemes (file://, ftp://) must be rejected."""
        resp = client.post(
            "/api/model-endpoints/test",
            data={"base_url": "file:///etc/passwd"},
        )
        assert resp.status_code == 400
        assert "Rejected" in resp.json()["detail"]

    def test_loopback_accepted_by_default(self, client):
        """Local-first: loopback is accepted when block_private=False."""
        with patch("src.url_safety._default_resolver", LOCALHOST_V4):
            resp = client.post(
                "/api/model-endpoints/test",
                data={"base_url": "http://localhost:11434/v1"},
            )
            # Guard passes; probe fails (no server) but that is not SSRF.
            assert "Rejected endpoint URL" not in str(resp.json())

    def test_loopback_rejected_in_strict_mode(self, client):
        """Strict mode: loopback is rejected when block_private=True."""
        os.environ["MODELENDPOINT_BLOCK_PRIVATE_IPS"] = "true"
        try:
            with patch("src.url_safety._default_resolver", LOCALHOST_V4):
                resp = client.post(
                    "/api/model-endpoints/test",
                    data={"base_url": "http://localhost:11434/v1"},
                )
                assert resp.status_code == 400
                assert "Rejected" in resp.json()["detail"]
        finally:
            os.environ.pop("MODELENDPOINT_BLOCK_PRIVATE_IPS", None)

    def test_strict_mode_blocks_direct_loopback_ip(self, client):
        """Strict mode rejects 127.0.0.1 passed directly (no DNS needed)."""
        os.environ["MODELENDPOINT_BLOCK_PRIVATE_IPS"] = "true"
        try:
            resp = client.post(
                "/api/model-endpoints/test",
                data={"base_url": "http://127.0.0.1:11434/v1"},
            )
            assert resp.status_code == 400
            assert "Rejected" in resp.json()["detail"]
        finally:
            os.environ.pop("MODELENDPOINT_BLOCK_PRIVATE_IPS", None)

    def test_ipv6_loopback_literal_accepted_by_default(self, client):
        """::1 is loopback, not a disallowed 'reserved' address (dfd1f7e7)."""
        resp = client.post(
            "/api/model-endpoints/test",
            data={"base_url": "http://[::1]:11434/v1"},
        )
        assert "Rejected endpoint URL" not in str(resp.json())

    def test_ipv6_loopback_localhost_accepted_by_default(self, client):
        """Dual-stack hosts whose resolver answers ::1 first must work."""
        with patch("src.url_safety._default_resolver", LOCALHOST_V6):
            resp = client.post(
                "/api/model-endpoints/test",
                data={"base_url": "http://localhost:11434/v1"},
            )
            assert "Rejected endpoint URL" not in str(resp.json())

    def test_ipv6_loopback_rejected_in_strict_mode(self, client):
        """Strict mode still rejects ::1."""
        os.environ["MODELENDPOINT_BLOCK_PRIVATE_IPS"] = "true"
        try:
            resp = client.post(
                "/api/model-endpoints/test",
                data={"base_url": "http://[::1]:11434/v1"},
            )
            assert resp.status_code == 400
            assert "Rejected" in resp.json()["detail"]
        finally:
            os.environ.pop("MODELENDPOINT_BLOCK_PRIVATE_IPS", None)


class TestModelEndpointCreateSSRF:
    """SSRF protection for POST /model-endpoints."""

    def test_cloud_metadata_blocked(self, client):
        """Cloud metadata IP must be rejected on endpoint creation."""
        resp = client.post(
            "/api/model-endpoints",
            data={"base_url": "http://169.254.169.254/latest/meta-data/"},
        )
        assert resp.status_code == 400
        assert "Rejected" in resp.json()["detail"]

    def test_non_http_scheme_blocked(self, client):
        """Non-HTTP schemes must be rejected on endpoint creation."""
        resp = client.post(
            "/api/model-endpoints",
            data={"base_url": "gopher://evil:6379/secret"},
        )
        assert resp.status_code == 400
        assert "Rejected" in resp.json()["detail"]

    def test_loopback_accepted_by_default(self, client):
        """Local-first: loopback is accepted when block_private=False."""
        with patch("src.url_safety._default_resolver", LOCALHOST_V4):
            resp = client.post(
                "/api/model-endpoints",
                data={"base_url": "http://localhost:11434/v1"},
            )
            # Guard passes; route may 500 on DB/probe but never 400 SSRF.
            assert resp.status_code != 400
            body = resp.text if resp.status_code == 500 else str(resp.json())
            assert "Rejected endpoint URL" not in body

    def test_loopback_rejected_in_strict_mode(self, client):
        """Strict mode: loopback is rejected when block_private=True."""
        os.environ["MODELENDPOINT_BLOCK_PRIVATE_IPS"] = "true"
        try:
            with patch("src.url_safety._default_resolver", LOCALHOST_V4):
                resp = client.post(
                    "/api/model-endpoints",
                    data={"base_url": "http://localhost:11434/v1"},
                )
                assert resp.status_code == 400
                assert "Rejected" in resp.json()["detail"]
        finally:
            os.environ.pop("MODELENDPOINT_BLOCK_PRIVATE_IPS", None)


class TestModelEndpointPatchSSRF:
    """SSRF protection for PATCH /api/model-endpoints/{ep_id} (base_url).

    The update path rewrites the stored base_url, which later fetches probe,
    list models and serve chat through — it must pass the same guard as the
    POST routes instead of bypassing it.
    """

    def _install_fake_endpoint(self, monkeypatch, base_url="http://localhost:9999/v1"):
        import routes.model_routes as mr

        ep = SimpleNamespace(
            id="ep1",
            name="EP",
            base_url=base_url,
            api_key=None,
            is_enabled=True,
            owner=None,
            hidden_models=None,
            cached_models=None,
            pinned_models=None,
            model_type="llm",
            supports_tools=None,
            endpoint_kind="auto",
            model_refresh_mode="auto",
            model_refresh_interval=None,
            model_refresh_timeout=None,
            created_at=None,
            updated_at=None,
        )

        class _FakeDb:
            def query(self, _model):
                return self

            def filter(self, *_args, **_kwargs):
                return self

            def first(self):
                return ep

            def commit(self):
                pass

            def close(self):
                pass

        monkeypatch.setattr(mr, "SessionLocal", lambda: _FakeDb())
        monkeypatch.setattr(mr, "require_admin", lambda request: None)
        monkeypatch.setattr(mr, "_chatgpt_endpoint_visible", lambda ep_, request: True)
        return ep

    def test_patch_cloud_metadata_blocked(self, client, monkeypatch):
        """PATCH must reject the cloud metadata URL, not store it."""
        ep = self._install_fake_endpoint(monkeypatch)
        resp = client.patch(
            "/api/model-endpoints/ep1",
            json={"base_url": "http://169.254.169.254/latest/meta-data"},
        )
        assert resp.status_code == 400
        assert "Rejected endpoint URL" in resp.json()["detail"]
        assert ep.base_url == "http://localhost:9999/v1"

    def test_patch_non_http_scheme_blocked(self, client, monkeypatch):
        """PATCH must reject non-HTTP schemes."""
        ep = self._install_fake_endpoint(monkeypatch)
        resp = client.patch(
            "/api/model-endpoints/ep1",
            json={"base_url": "file:///etc/passwd"},
        )
        assert resp.status_code == 400
        assert "Rejected endpoint URL" in resp.json()["detail"]
        assert ep.base_url == "http://localhost:9999/v1"

    def test_patch_valid_url_accepted(self, client, monkeypatch):
        """Control: a legitimate local endpoint URL still updates fine."""
        ep = self._install_fake_endpoint(monkeypatch)
        resp = client.patch(
            "/api/model-endpoints/ep1",
            json={"base_url": "http://127.0.0.1:11434/v1"},
        )
        assert resp.status_code == 200
        assert ep.base_url == "http://127.0.0.1:11434/v1"
