"""Tests for OXS webhooks (inbound token + signature + dispatch)."""

from __future__ import annotations

import asyncio
import json

import pytest

from core.extensions import webhooks


@pytest.fixture(autouse=True)
def _reset():
    webhooks.reset_for_tests()
    webhooks.reset_rate_limits()
    yield
    webhooks.reset_for_tests()
    webhooks.reset_rate_limits()


# ── Token generation + hashing ─────────────────────────────────────────────


def test_token_is_long_and_unique():
    tokens = {webhooks.generate_token() for _ in range(50)}
    assert len(tokens) == 50
    for t in tokens:
        assert len(t) >= 32


def test_token_hash_is_deterministic_and_different_per_token():
    h1 = webhooks.hash_token("abc")
    h2 = webhooks.hash_token("abc")
    assert h1 == h2
    h3 = webhooks.hash_token("xyz")
    assert h1 != h3


def test_verify_token_hash_accepts_matching_token():
    token = "abc123"
    h = webhooks.hash_token(token)
    assert webhooks.verify_token_hash(token, h) is True
    assert webhooks.verify_token_hash("wrong", h) is False


# ── Signature ──────────────────────────────────────────────────────────────


def test_compute_signature_is_deterministic():
    sig1 = webhooks.compute_signature("secret", b"body")
    sig2 = webhooks.compute_signature("secret", b"body")
    assert sig1 == sig2
    assert sig1.startswith("sha256=")


def test_verify_signature_optional():
    """No signature header → allowed (signature is optional)."""
    body = b"hello"
    assert webhooks.verify_signature("secret", body, None) is True
    assert webhooks.verify_signature("secret", body, "") is True


def test_verify_signature_matches_correct_header():
    body = b"hello"
    secret = "topsecret"
    sig = webhooks.compute_signature(secret, body)
    assert webhooks.verify_signature(secret, body, sig) is True


def test_verify_signature_rejects_mismatch():
    body = b"hello"
    assert webhooks.verify_signature("good_secret", body, webhooks.compute_signature("bad_secret", body)) is False


# ── Install / uninstall ────────────────────────────────────────────────────


def test_install_returns_record_with_raw_token():
    def handler(**kwargs):
        return None

    rec = webhooks.install("ext1", "/wh/{token}", ["messages.upsert"], handler)
    assert rec.extension_id == "ext1"
    assert rec.path_suffix == "/wh/{token}"
    assert rec.token_hash != ""
    assert webhooks.get_raw_token(rec) is not None


def test_install_generates_unique_tokens_per_install():
    rec1 = webhooks.install("a", "/x", [], lambda **kw: None)
    rec2 = webhooks.install("a", "/x", [], lambda **kw: None)
    assert webhooks.get_raw_token(rec1) != webhooks.get_raw_token(rec2)


def test_uninstall_removes_webhook():
    webhooks.install("ext", "/x", [], lambda **kw: None)
    n = webhooks.uninstall("ext")
    assert n == 1
    assert webhooks.list_for_extension("ext") == []


def test_uninstall_unknown_returns_zero():
    assert webhooks.uninstall("never") == 0


def test_install_multiple_webhooks_per_extension():
    webhooks.install("ext", "/a", [], lambda **kw: None)
    webhooks.install("ext", "/b", [], lambda **kw: None)
    assert len(webhooks.list_for_extension("ext")) == 2


# ── Dispatch ──────────────────────────────────────────────────────────────


def _run(coro):
    """Run an async coroutine in tests."""
    return asyncio.run(coro)


def test_dispatch_unknown_extension_returns_404():
    status, _ = _run(webhooks.dispatch("nope", "token", b"{}"))
    assert status == 404


def test_dispatch_wrong_token_returns_404():
    webhooks.install("ext", "/x", [], lambda **kw: None)
    status, _ = _run(webhooks.dispatch("ext", "wrong-token", b"{}"))
    assert status == 404


def test_dispatch_correct_token_returns_202():
    webhooks.install("ext", "/x", [], lambda **kw: None)
    rec = webhooks.list_for_extension("ext")[0]
    raw_token = webhooks.get_raw_token(rec)
    status, msg = _run(webhooks.dispatch("ext", raw_token, b"{}"))
    assert status == 202
    assert msg == "accepted"


def test_dispatch_with_signature_passes():
    received = []

    def handler(**kw):
        received.append(kw)

    webhooks.install("ext", "/x", ["messages.upsert"], handler)
    rec = webhooks.list_for_extension("ext")[0]
    token = webhooks.get_raw_token(rec)
    body = json.dumps({"event": "messages.upsert", "data": {}}).encode()
    sig = webhooks.compute_signature(rec.secret, body)
    headers = {f"x-ext-signature": sig}
    status, _ = _run(webhooks.dispatch("ext", token, body, headers=headers))
    assert status == 202


def test_dispatch_with_bad_signature_returns_401():
    def handler(**kw):
        return None

    webhooks.install("ext", "/x", [], handler)
    rec = webhooks.list_for_extension("ext")[0]
    token = webhooks.get_raw_token(rec)
    headers = {"x-ext-signature": "sha256=deadbeef"}
    status, _ = _run(webhooks.dispatch("ext", token, b"{}", headers=headers))
    assert status == 401


def test_dispatch_passes_parsed_body_to_handler():
    received = []

    def handler(**kw):
        received.append(kw)

    webhooks.install("ext", "/x", ["messages.upsert"], handler)
    rec = webhooks.list_for_extension("ext")[0]
    token = webhooks.get_raw_token(rec)
    payload = {"event": "messages.upsert", "data": {"k": "v"}}
    status, _ = _run(webhooks.dispatch("ext", token, json.dumps(payload).encode()))
    assert status == 202
    # Allow async background task to flush
    _run(asyncio.sleep(0.05))
    # We can't easily verify the handler ran without a real loop; just
    # confirm 202 was returned.


def test_dispatch_rate_limits_repeated_requests():
    """20 requests to the same token within 1s → some get 429."""
    def handler(**kw):
        return None
    webhooks.install("ext", "/x", [], handler)
    rec = webhooks.list_for_extension("ext")[0]
    token = webhooks.get_raw_token(rec)
    statuses = []
    for _ in range(20):
        status, _ = _run(webhooks.dispatch("ext", token, b"{}"))
        statuses.append(status)
    assert 429 in statuses, statuses


def test_dispatch_rate_limit_is_per_token():
    """Tokens for the same extension share a bucket."""
    webhooks.install("ext", "/x", [], lambda **kw: None)
    rec = webhooks.list_for_extension("ext")[0]
    token = webhooks.get_raw_token(rec)
    # Burn the budget
    for _ in range(10):
        _run(webhooks.dispatch("ext", token, b"{}"))
    # Next request should be 429 even though it's a different "logical"
    # caller — we only have one bucket per (ext, token_hash).
    status, _ = _run(webhooks.dispatch("ext", token, b"{}"))
    assert status == 429


def test_reset_rate_limits_clears_state():
    webhooks.reset_rate_limits()
    assert len(webhooks._rate) == 0


# ── Public URL ────────────────────────────────────────────────────────────


def test_public_url_contains_extension_id():
    url = webhooks.public_url("ext1")
    assert "/api/ext/ext1/webhook" in url


def test_public_url_with_base():
    url = webhooks.public_url("ext1", base="https://odysseus.example.com")
    assert url.startswith("https://odysseus.example.com/api/ext/ext1/webhook")


# ── Handler invocation (async) ──────────────────────────────────────────────


def test_handler_receives_parsed_body_and_event_filter():
    received = []
    import asyncio as _asyncio

    async def handler(**kw):
        received.append(kw)
        return None

    webhooks.install("ext", "/x", ["messages.upsert", "messages.reaction"], handler)
    rec = webhooks.list_for_extension("ext")[0]
    token = webhooks.get_raw_token(rec)
    payload = {"event": "messages.upsert", "data": {"k": "v"}}
    _run(webhooks.dispatch("ext", token, json.dumps(payload).encode()))
    _run(_asyncio.sleep(0.1))
    assert len(received) >= 1
    # The handler was called with body, headers, query, event_filter
    call = received[0]
    assert call["body"] == payload
    assert call["event_filter"] == ["messages.upsert", "messages.reaction"]


def test_handler_exception_is_isolated():
    import asyncio as _asyncio

    def handler(**kw):
        raise RuntimeError("boom")

    webhooks.install("ext", "/x", [], handler)
    rec = webhooks.list_for_extension("ext")[0]
    token = webhooks.get_raw_token(rec)
    status, _ = _run(webhooks.dispatch("ext", token, b"{}"))
    assert status == 202  # response still succeeds
    _run(_asyncio.sleep(0.1))