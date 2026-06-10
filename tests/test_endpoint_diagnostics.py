"""Tests for the generic, loopback-only endpoint diagnostics feature.

The admin-only diagnostics route fetches a read-only section from a *loopback*
endpoint, but only one of the sections that endpoint itself registered in its
validated `diagnostics_paths` map. These tests pin the two safety gates:

  1. `_parse_diagnostics_paths` — the SSRF gate. A stored/submitted map can only
     ever yield same-origin absolute paths; anything that could redirect the
     fetch to another origin (scheme, protocol-relative, query/fragment) or
     escape the path is dropped.
  2. `_endpoint_supports_diagnostics` — diagnostics are offered only for loopback
     endpoints that registered at least one section.

Plus a source-level guard that the route keeps its SSRF hardening
(`trust_env=False`, `follow_redirects=False`) and validates the requested
section against the endpoint's own map rather than a global list.
"""
from pathlib import Path
import sys
from types import SimpleNamespace

_endpoint_resolver = sys.modules.get("src.endpoint_resolver")
if _endpoint_resolver is not None and not getattr(_endpoint_resolver, "__file__", None):
    sys.modules.pop("src.endpoint_resolver", None)
    sys.modules.pop("routes.model_routes", None)

from routes.model_routes import (
    _parse_diagnostics_paths,
    _endpoint_diagnostics,
    _endpoint_diagnostics_sections,
    _endpoint_supports_diagnostics,
    _fetch_endpoint_diagnostic_payload,
)


def test_parse_accepts_valid_map_and_json_string():
    assert _parse_diagnostics_paths({"health": "/health", "doctor": "/api/doctor"}) == {
        "health": "/health",
        "doctor": "/api/doctor",
    }
    assert _parse_diagnostics_paths('{"health": "/health"}') == {"health": "/health"}


def test_parse_rejects_origin_escapes():
    # Each of these could redirect the admin fetch off the loopback origin or
    # escape the path; all must be dropped.
    assert _parse_diagnostics_paths({"x": "http://evil.example/"}) == {}
    assert _parse_diagnostics_paths({"x": "https://evil.example/p"}) == {}
    assert _parse_diagnostics_paths({"x": "//evil.example/"}) == {}
    assert _parse_diagnostics_paths({"x": "/health?redir=1"}) == {}
    assert _parse_diagnostics_paths({"x": "/health#frag"}) == {}
    assert _parse_diagnostics_paths({"x": "/a\\b"}) == {}
    assert _parse_diagnostics_paths({"x": "health"}) == {}  # no leading slash


def test_parse_rejects_bad_section_names_and_nondict():
    assert _parse_diagnostics_paths({"BAD NAME": "/x"}) == {}
    assert _parse_diagnostics_paths({"a" * 33: "/x"}) == {}
    assert _parse_diagnostics_paths(["/health"]) == {}
    assert _parse_diagnostics_paths("not json") == {}
    assert _parse_diagnostics_paths("") == {}
    assert _parse_diagnostics_paths(None) == {}


def test_parse_caps_section_count():
    big = {f"s{i}": f"/p{i}" for i in range(40)}
    assert len(_parse_diagnostics_paths(big)) == 16


def test_supports_diagnostics_requires_loopback_and_config():
    loopback_cfg = SimpleNamespace(
        base_url="http://127.0.0.1:8000/v1",
        diagnostics_paths='{"health": "/health"}',
    )
    loopback_none = SimpleNamespace(base_url="http://127.0.0.1:8000/v1", diagnostics_paths=None)
    remote_cfg = SimpleNamespace(
        base_url="http://10.0.0.5:8000/v1",
        diagnostics_paths='{"health": "/health"}',
    )

    assert _endpoint_supports_diagnostics(loopback_cfg) is True
    assert _endpoint_supports_diagnostics(loopback_none) is False
    # A non-loopback endpoint is never eligible, even with diagnostics configured.
    assert _endpoint_supports_diagnostics(remote_cfg) is False
    assert _endpoint_diagnostics(loopback_cfg) == {"health": "/health"}
    assert _endpoint_diagnostics_sections(loopback_cfg) == ["health"]
    assert _endpoint_diagnostics_sections(remote_cfg) == []


def test_supports_diagnostics_survives_docker_loopback_rewrite():
    # _rewrite_loopback_for_docker remaps a loopback wrapper URL to
    # host.docker.internal and STORES that host, so diagnostics eligibility must
    # follow the rewrite — otherwise an admin's configured diagnostics silently
    # disappear in Docker. host.docker.internal is the local Docker host, never an
    # arbitrary external host, and the route is admin-only.
    docker_cfg = SimpleNamespace(
        base_url="http://host.docker.internal:1234/v1",
        diagnostics_paths='{"health": "/health"}',
    )
    docker_none = SimpleNamespace(
        base_url="http://host.docker.internal:1234/v1", diagnostics_paths=None
    )
    assert _endpoint_supports_diagnostics(docker_cfg) is True
    assert _endpoint_supports_diagnostics(docker_none) is False
    assert _endpoint_diagnostics_sections(docker_cfg) == ["health"]


def test_diagnostic_fetch_caps_response_body(monkeypatch):
    from routes import model_routes

    captured = {}

    class _Resp:
        status_code = 200
        encoding = "utf-8"

        def iter_bytes(self):
            yield b"a" * 10_000
            yield b"b" * 15_000

    class _Stream:
        def __enter__(self):
            return _Resp()

        def __exit__(self, *args):
            return False

    def _fake_stream(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return _Stream()

    monkeypatch.setattr(model_routes.httpx, "stream", _fake_stream)

    payload, truncated = _fetch_endpoint_diagnostic_payload(
        "http://127.0.0.1:8000/diag",
        {"Authorization": "Bearer local"},
    )

    assert len(payload) == 20_000
    assert payload == ("a" * 10_000) + ("b" * 10_000)
    assert truncated is True
    assert captured["follow_redirects"] is False
    assert captured["trust_env"] is False


def test_route_keeps_ssrf_hardening_and_per_endpoint_sections():
    src = Path(__file__).resolve().parents[1] / "routes" / "model_routes.py"
    body = src.read_text(encoding="utf-8").split("def get_endpoint_diagnostics", 1)[1]
    body = body.split("@router.", 1)[0]
    assert "require_admin(request)" in body
    assert "_fetch_endpoint_diagnostic_payload" in body
    # Section is validated against this endpoint's own configured map.
    assert "_endpoint_supports_diagnostics(ep_view)" in body
    assert "diagnostics.get(section)" in body
    src = src.read_text(encoding="utf-8")
    assert "trust_env=False" in src
    assert "follow_redirects=False" in src
