"""Regression tests for direct-local first-run setup enforcement."""

from types import SimpleNamespace

import pytest


def _request(
    *,
    client_host: str,
    request_host: str,
    headers: dict[str, str] | None = None,
):
    return SimpleNamespace(
        client=SimpleNamespace(host=client_host),
        url=SimpleNamespace(hostname=request_host),
        headers=headers or {},
    )


def test_first_run_setup_allows_direct_localhost():
    from routes.auth_routes import _request_from_loopback

    request = _request(
        client_host="127.0.0.1",
        request_host="localhost",
    )

    assert _request_from_loopback(request) is True


@pytest.mark.parametrize(
    ("client_host", "request_host", "headers"),
    [
        (
            "203.0.113.44",
            "localhost",
            {"x-forwarded-for": "127.0.0.1"},
        ),
        (
            "127.0.0.1",
            "odysseus.example.com",
            {"x-forwarded-for": "203.0.113.44"},
        ),
        (
            "127.0.0.1",
            "localhost",
            {"forwarded": "for=203.0.113.44;proto=https"},
        ),
        (
            "127.0.0.1",
            "localhost",
            {"x-real-ip": "203.0.113.44"},
        ),
    ],
)
def test_first_run_setup_rejects_remote_or_proxied_requests(
    client_host,
    request_host,
    headers,
):
    from routes.auth_routes import _request_from_loopback

    request = _request(
        client_host=client_host,
        request_host=request_host,
        headers=headers,
    )

    assert _request_from_loopback(request) is False
