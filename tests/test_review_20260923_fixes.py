"""Regressions for the 2026-09-23 review fixes.

Two findings, both about bounding work the server does on someone else's behalf:

* the scholarly metadata lookups called two hardcoded third-party endpoints with
  a stale hand-written User-Agent, no outbound-URL policy, and a full timeout per
  hop, so one query could hold a user-facing search open for the sum of all three;
* editor drafts were only size-checked after the body had been parsed and
  re-serialised, so the ceiling rejected an allocation it had already paid for.

Each test fails on the pre-fix tree.
"""

import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.search import core as search_core
from src.constants import (
    APP_VERSION,
    ARXIV_API_URL,
    OPENALEX_API_URL,
    SCHOLARLY_LOOKUP_TIMEOUT,
)
from src.upload_limits import EDITOR_DRAFT_MAX_BYTES


# --------------------------------------------------------------------------
# ODY-R09 — scholarly lookups
# --------------------------------------------------------------------------


def test_scholarly_endpoints_use_configured_constants(monkeypatch):
    """Call sites must route through configured endpoints, not hardcoded URLs."""
    requested_urls = []

    def fake_scholarly_api_get(url: str, params: dict):
        requested_urls.append(url)
        if "arxiv" in url:
            class FakeArxivResponse:
                text = "<feed xmlns='http://www.w3.org/2005/Atom'></feed>"

            return FakeArxivResponse()
        elif "openalex" in url:
            class FakeOpenAlexResponse:
                def json(self):
                    return {"results": []}

            return FakeOpenAlexResponse()
        return None

    monkeypatch.setattr(search_core, "_scholarly_api_get", fake_scholarly_api_get)

    custom_arxiv = "https://custom.arxiv.test/api/query"
    custom_openalex = "https://custom.openalex.test/works"
    monkeypatch.setattr(search_core, "ARXIV_API_URL", custom_arxiv)
    monkeypatch.setattr(search_core, "OPENALEX_API_URL", custom_openalex)

    search_core._arxiv_title_results("Attention Is All You Need")
    search_core._openalex_title_results("Attention Is All You Need")

    assert custom_arxiv in requested_urls
    assert custom_openalex in requested_urls


def test_user_agent_tracks_app_version():
    """A hand-written version string drifts; APP_VERSION cannot."""
    agent = search_core._scholarly_user_agent()
    assert APP_VERSION in agent
    # The pre-fix tree hardcoded 0.20 while APP_VERSION was already 1.0.3.
    assert "Odysseus/0.20 " not in agent


def test_outbound_policy_rejection_skips_the_request(monkeypatch):
    """A URL the outbound policy refuses must never reach httpx."""
    calls = []
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: calls.append(a) or pytest.fail("request sent")
    )
    monkeypatch.setattr(
        "src.url_safety.check_outbound_url", lambda url, **kw: (False, "blocked")
    )

    assert search_core._scholarly_api_get(ARXIV_API_URL, {}) is None
    assert calls == []


def test_redirect_to_prohibited_destination_is_blocked_and_never_requested(monkeypatch):
    """An allowed initial URL must not be permitted to redirect into a prohibited destination."""
    from src.url_safety import check_outbound_url as real_check

    called_urls = []

    def fake_get(url, **kwargs):
        called_urls.append(url)
        req = httpx.Request("GET", url)
        return httpx.Response(
            302,
            headers={"Location": "http://127.0.0.1:8080/internal-admin"},
            request=req,
        )

    def mock_check(url, **kwargs):
        if url == OPENALEX_API_URL:
            return (True, "")
        return real_check(url, **kwargs)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr("src.url_safety.check_outbound_url", mock_check)

    result = search_core._scholarly_api_get(OPENALEX_API_URL, {})

    assert result is None
    # Only the initial allowed URL was contacted; the prohibited redirect destination was never requested
    assert called_urls == [OPENALEX_API_URL]


def test_redirect_to_link_local_metadata_is_blocked_and_never_requested(monkeypatch):
    """Redirects to cloud metadata or link-local addresses must be refused before connection."""
    from src.url_safety import check_outbound_url as real_check

    called_urls = []

    def fake_get(url, **kwargs):
        called_urls.append(url)
        req = httpx.Request("GET", url)
        return httpx.Response(
            301,
            headers={"Location": "http://169.254.169.254/latest/meta-data"},
            request=req,
        )

    def mock_check(url, **kwargs):
        if url == ARXIV_API_URL:
            return (True, "")
        return real_check(url, **kwargs)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr("src.url_safety.check_outbound_url", mock_check)

    result = search_core._scholarly_api_get(ARXIV_API_URL, {})

    assert result is None
    assert called_urls == [ARXIV_API_URL]


def test_allowed_redirect_is_followed_safely(monkeypatch):
    """A safe redirect destination passing outbound checks is followed to completion."""
    called_urls = []
    canonical_url = "https://api.openalex.org/canonical-works"

    def fake_get(url, **kwargs):
        called_urls.append(url)
        req = httpx.Request("GET", url)
        if url == OPENALEX_API_URL:
            return httpx.Response(301, headers={"Location": "/canonical-works"}, request=req)
        return httpx.Response(200, json={"results": []}, request=req)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr("src.url_safety.check_outbound_url", lambda url, **kw: (True, ""))

    result = search_core._scholarly_api_get(OPENALEX_API_URL, {})

    assert result is not None
    assert result.status_code == 200
    assert called_urls == [OPENALEX_API_URL, canonical_url]


def test_redirect_limit_is_bounded(monkeypatch):
    """Redirects exceeding MAX_SCHOLARLY_REDIRECTS must fail safely without looping."""
    called_urls = []

    def fake_get(url, **kwargs):
        called_urls.append(url)
        req = httpx.Request("GET", url)
        return httpx.Response(302, headers={"Location": f"{url}/next"}, request=req)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr("src.url_safety.check_outbound_url", lambda url, **kw: (True, ""))

    result = search_core._scholarly_api_get("https://export.arxiv.org/api/query", {})

    assert result is None
    assert len(called_urls) == search_core.MAX_SCHOLARLY_REDIRECTS + 1


def test_redirect_does_not_replay_query_parameters_to_the_new_destination(monkeypatch):
    """The original query must not be re-sent to a redirect target.

    The params belong to the endpoint that was asked for. Replaying them across
    a redirect would hand the search terms to whatever host the redirect names.
    """
    calls = []

    def fake_get(url, **kwargs):
        params = kwargs.get("params")
        calls.append((url, params))
        # Mirror real httpx: response.url carries the query that was sent.
        req = httpx.Request("GET", httpx.URL(url, params=params or {}))
        if len(calls) == 1:
            return httpx.Response(
                302, headers={"Location": "https://redirect.openalex.test/v2"}, request=req
            )
        return httpx.Response(200, json={"results": []}, request=req)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr("src.url_safety.check_outbound_url", lambda url, **kw: (True, ""))

    result = search_core._scholarly_api_get(
        OPENALEX_API_URL, {"search": "confidential-title"}
    )

    assert result is not None
    assert len(calls) == 2
    second_url, second_params = calls[1]
    assert second_url == "https://redirect.openalex.test/v2"
    assert second_params is None
    assert "confidential-title" not in second_url


def test_relative_redirect_resolves_against_the_responding_url(monkeypatch):
    """A relative Location resolves against the URL that answered, not the origin."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        req = httpx.Request("GET", httpx.URL(url, params=kwargs.get("params") or {}))
        if len(calls) == 1:
            return httpx.Response(
                302, headers={"Location": "https://mirror.arxiv.test/api/v1/query"}, request=req
            )
        if len(calls) == 2:
            # Relative hop: must resolve against mirror.arxiv.test, not arxiv.
            return httpx.Response(302, headers={"Location": "../v2/query"}, request=req)
        return httpx.Response(200, text="<feed/>", request=req)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr("src.url_safety.check_outbound_url", lambda url, **kw: (True, ""))

    result = search_core._scholarly_api_get(ARXIV_API_URL, {"search_query": "x"})

    assert result is not None
    assert calls[2] == "https://mirror.arxiv.test/api/v2/query"


def test_http_error_status_degrades_to_no_results(monkeypatch):
    """A 500 from a scholarly API must degrade to no results, not raise."""

    def fake_get(url, **kwargs):
        req = httpx.Request("GET", url)
        return httpx.Response(500, text="upstream exploded", request=req)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr("src.url_safety.check_outbound_url", lambda url, **kw: (True, ""))

    assert search_core._arxiv_title_results("Attention Is All You Need") == []
    assert search_core._openalex_title_results("Attention Is All You Need") == []


def test_exhausted_budget_skips_the_request(monkeypatch):
    """Once the chain's budget is spent, later hops are skipped, not retried."""
    calls = []
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: calls.append(a) or pytest.fail("request sent")
    )
    monkeypatch.setattr(
        "src.url_safety.check_outbound_url", lambda url, **kw: (True, "")
    )

    token = search_core._scholarly_deadline.set(time.monotonic() - 1)
    try:
        assert search_core._scholarly_api_get(OPENALEX_API_URL, {}) is None
    finally:
        search_core._scholarly_deadline.reset(token)
    assert calls == []


def test_remaining_budget_caps_the_per_request_timeout(monkeypatch):
    """A hop cannot wait longer than the budget the chain has left."""
    seen = {}

    class _Response:
        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return _Response()

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(
        "src.url_safety.check_outbound_url", lambda url, **kw: (True, "")
    )

    token = search_core._scholarly_deadline.set(time.monotonic() + 2)
    try:
        search_core._scholarly_api_get(ARXIV_API_URL, {})
    finally:
        search_core._scholarly_deadline.reset(token)

    assert seen["timeout"] <= 2.0
    assert seen["timeout"] < SCHOLARLY_LOOKUP_TIMEOUT


def test_budget_is_shared_across_the_whole_chain(monkeypatch):
    """Both hops of one lookup draw on a single deadline."""
    observed = []

    monkeypatch.setattr(
        "src.url_safety.check_outbound_url", lambda url, **kw: (True, "")
    )
    monkeypatch.setattr(
        search_core,
        "_openalex_title_results",
        lambda title, count=3: observed.append(search_core._scholarly_deadline.get())
        or [],
    )
    monkeypatch.setattr(
        search_core,
        "_arxiv_title_results",
        lambda title, count=3: observed.append(search_core._scholarly_deadline.get())
        or [],
    )

    search_core._direct_scholarly_title_results("a paper title")

    assert len(observed) == 2
    assert observed[0] is not None
    assert observed[0] == observed[1]


def test_budget_does_not_leak_out_of_the_chain():
    """The deadline is scoped to the lookup, not left set on the context."""
    assert search_core._scholarly_deadline.get() is None
    with search_core._scholarly_budget():
        assert search_core._scholarly_deadline.get() is not None
    assert search_core._scholarly_deadline.get() is None


# --------------------------------------------------------------------------
# ODY-R11 — editor draft size ceiling
# --------------------------------------------------------------------------


def _draft_client() -> TestClient:
    from routes.editor_draft_routes import setup_editor_draft_routes

    app = FastAPI()
    app.include_router(setup_editor_draft_routes())
    return TestClient(app)


def test_oversized_declared_body_is_refused_before_it_is_parsed():
    """An over-ceiling Content-Length is rejected without reading the payload."""
    client = _draft_client()
    response = client.post(
        "/api/editor-drafts",
        content=b"{}",
        headers={
            "content-type": "application/json",
            "content-length": str(EDITOR_DRAFT_MAX_BYTES + 1),
        },
    )
    assert response.status_code == 413
    assert "safety limit" in response.text


def test_oversized_declared_body_guard_runs_before_body_consumption():
    """An oversized Content-Length must reject the request without reading or consuming the body."""
    body_consumed = False

    def body_stream():
        nonlocal body_consumed
        body_consumed = True
        yield b'{"layers": []}'

    client = _draft_client()
    response = client.post(
        "/api/editor-drafts",
        content=body_stream(),
        headers={
            "content-type": "application/json",
            "content-length": str(EDITOR_DRAFT_MAX_BYTES + 1),
        },
    )
    assert response.status_code == 413
    assert "safety limit" in response.text
    # Proves the body stream was never read or consumed before rejection
    assert body_consumed is False


def test_oversized_declared_body_guard_runs_before_body_consumption_on_put():
    """Update route also rejects oversized Content-Length without consuming body."""
    body_consumed = False

    def body_stream():
        nonlocal body_consumed
        body_consumed = True
        yield b'{"layers": []}'

    client = _draft_client()
    response = client.put(
        "/api/editor-drafts/some-draft-id",
        content=body_stream(),
        headers={
            "content-type": "application/json",
            "content-length": str(EDITOR_DRAFT_MAX_BYTES + 1),
        },
    )
    assert response.status_code == 413
    assert body_consumed is False


def test_oversized_declared_body_rejects_without_json_parsing():
    """Even malformed or invalid JSON is rejected with 413 rather than 422 if Content-Length exceeds ceiling."""
    client = _draft_client()
    response = client.post(
        "/api/editor-drafts",
        content=b"this is completely invalid json {[[",
        headers={
            "content-type": "application/json",
            "content-length": str(EDITOR_DRAFT_MAX_BYTES + 1),
        },
    )
    assert response.status_code == 413


def test_update_route_carries_the_same_guard():
    client = _draft_client()
    response = client.put(
        "/api/editor-drafts/whatever",
        content=b"{}",
        headers={
            "content-type": "application/json",
            "content-length": str(EDITOR_DRAFT_MAX_BYTES + 1),
        },
    )
    assert response.status_code == 413


def test_absent_content_length_still_reaches_the_exact_check():
    """The header guard is an optimisation; it must not become the only check."""
    from routes.editor_draft_routes import _dump_payload, reject_oversized_draft_body

    class _NoLengthRequest:
        headers: dict = {}

    # No header: the guard abstains rather than rejecting or accepting outright.
    assert reject_oversized_draft_body(_NoLengthRequest()) is None

    # The authoritative byte count still refuses an over-ceiling payload.
    with pytest.raises(Exception) as excinfo:
        _dump_payload({"blob": "x" * (EDITOR_DRAFT_MAX_BYTES + 1)})
    assert getattr(excinfo.value, "status_code", None) == 413


def test_malformed_content_length_does_not_crash_the_route():
    from routes.editor_draft_routes import reject_oversized_draft_body

    class _BadLengthRequest:
        headers = {"content-length": "not-a-number"}

    assert reject_oversized_draft_body(_BadLengthRequest()) is None


def test_ordinary_draft_is_unaffected():
    """The guard must not change behaviour for normal payloads."""
    from routes.editor_draft_routes import _dump_payload

    assert _dump_payload({"layers": []}) == '{"layers":[]}'


def test_route_class_guard_applies_only_to_body_bearing_methods():
    """The route class must not change GET/DELETE semantics.

    ``EditorDraftRoute`` is attached to the whole editor-draft router, so the
    bodyless routes run through it too. They must reach their handler
    untouched — the early ceiling belongs to the methods that carry a draft.
    """
    from fastapi import APIRouter

    from routes.editor_draft_routes import EditorDraftRoute

    router = APIRouter(route_class=EditorDraftRoute)

    @router.get("/probe")
    async def _get_probe():
        return {"reached": "get"}

    @router.delete("/probe")
    async def _delete_probe():
        return {"reached": "delete"}

    @router.post("/probe")
    async def _post_probe():
        return {"reached": "post"}

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    oversized = {
        "content-type": "application/json",
        "content-length": str(EDITOR_DRAFT_MAX_BYTES + 1),
    }

    # Bodyless methods are unaffected even when a bogus huge length is declared.
    for method, expected in (("GET", "get"), ("DELETE", "delete")):
        response = client.request(method, "/probe", content=b"{}", headers=oversized)
        assert response.status_code == 200, (method, response.text)
        assert response.json() == {"reached": expected}

    # The same declaration on the body-bearing method is refused.
    assert client.post("/probe", content=b"{}", headers=oversized).status_code == 413

    # ...and an ordinary POST still reaches the handler.
    ordinary = client.post("/probe", json={"layers": []})
    assert ordinary.status_code == 200
    assert ordinary.json() == {"reached": "post"}
