"""Tests for the ChatGPT-subscription (codex) OAuth backend.

Covers the device-code protocol service, the `auth.resolve` OAuth branch
(refresh-on-expiry, single-flight, error->status mapping), and the connect/
status/cancel/disconnect/import routes. All network I/O is mocked by injecting an
`httpx.MockTransport` at the `codex_oauth._new_async_client` seam (a built-in httpx
primitive — no network-mock dependency); the DB is a throwaway sqlite rebind. No
real tokens, no real OpenAI calls.
"""
import asyncio
import base64
import json
import sys
import time

import httpx
import pytest

# --- Real-module isolation -------------------------------------------------
# In the full suite, earlier-collected files (test_agent_loop.py) stub
# `sqlalchemy` and `core.database` as MagicMock in sys.modules at collection time
# and never restore them. This file needs the REAL sqlalchemy + DB models. So:
# snapshot whatever stubs are present, swap in the real modules just long enough
# to bind our globals (db, routes_mod, create_engine, sessionmaker), then restore
# the snapshot byte-for-byte — later-collected files still see the stubs they
# expect (no cascade). Our bound names keep pointing at the real module objects;
# the oauth_db fixture re-points sys.modules['core.database'] at the real module
# per-test, for oauth_store's lazy `from core.database import ...`.
_iso_keys = ["core.database", "routes.codex_oauth_routes"] + [
    m for m in list(sys.modules) if m == "sqlalchemy" or m.startswith("sqlalchemy.")
]
_iso_snapshot = {k: sys.modules.get(k) for k in dict.fromkeys(_iso_keys)}
for _k in _iso_snapshot:
    sys.modules.pop(_k, None)
try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import core.database as db
    import routes.codex_oauth_routes as routes_mod
finally:
    for _k in [m for m in list(sys.modules) if m == "sqlalchemy" or m.startswith("sqlalchemy.")]:
        sys.modules.pop(_k, None)
    sys.modules.pop("core.database", None)
    sys.modules.pop("routes.codex_oauth_routes", None)
    for _k, _v in _iso_snapshot.items():
        if _v is not None:
            sys.modules[_k] = _v

import src.providers.auth.codex_oauth as cx
import src.providers.auth.oauth_store as store
from src.providers import auth
from src.providers.endpoint_ref import EndpointRef
from src.providers.spec import ProviderSpec

CODEX_SPEC = ProviderSpec(
    id="openai-codex", label="ChatGPT", transport="codex_responses", auth_type="oauth",
    url_matchers=(), default_chat_path="/responses", model_list_mode="static",
)


class _MockHTTP:
    """Routes httpx traffic to queued responses via ``httpx.MockTransport`` — a
    built-in httpx primitive, so these tests carry no respx/network-mock dep.

    Captures every outgoing request; per-URL queues allow sequencing (mirrors
    respx's ``side_effect=[...]``). A single queued response repeats (``return_value=``)."""

    def __init__(self):
        self.calls = []          # list[httpx.Request], in send order
        self._routes = {}        # str(url) -> list[httpx.Response]

    def route(self, url, *responses):
        self._routes.setdefault(str(url), []).extend(responses)
        return self

    def _handle(self, request):
        self.calls.append(request)
        queue = self._routes.get(str(request.url))
        if not queue:
            return httpx.Response(500, json={"error": f"unrouted {request.url}"})
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def new_client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def count(self, url):
        return sum(1 for r in self.calls if str(r.url) == str(url))


@pytest.fixture
def codex_http(monkeypatch):
    """Inject a MockTransport client at codex_oauth's single client-construction
    seam (`_new_async_client`) — every device-code/refresh call funnels through it,
    whether issued directly, via `auth.resolve`, or through the FastAPI routes."""
    mock = _MockHTTP()
    monkeypatch.setattr(cx, "_new_async_client", mock.new_client)
    return mock


def _jwt(exp_offset=3600, account_id="acct-xyz"):
    """A minimal unsigned JWT carrying `exp` + the chatgpt_account_id claim."""
    hdr = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = {"exp": int(time.time()) + exp_offset, cx.JWT_AUTH_CLAIM: {"chatgpt_account_id": account_id}}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{hdr}.{body}.sig"


@pytest.fixture
def oauth_db(tmp_path, monkeypatch):
    """Throwaway sqlite bound into core.database (+ the route module's imported
    SessionLocal). monkeypatch restores the originals after each test."""
    engine = create_engine(f"sqlite:///{tmp_path}/codex.db", connect_args={"check_same_thread": False})
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db.Base.metadata.create_all(bind=engine)
    # Re-point sys.modules so oauth_store's lazy `from core.database import ...`
    # resolves to the real module (the module top restored a MagicMock stub there
    # in the full suite). monkeypatch restores it after each test.
    monkeypatch.setitem(sys.modules, "core.database", db)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "SessionLocal", TestSession)
    monkeypatch.setattr(routes_mod, "SessionLocal", TestSession)
    store._refresh_locks.clear()
    yield TestSession
    engine.dispose()


def _seed_token(session_factory, endpoint_id, access, refresh, expires_at, *, status="active", owner="alice"):
    s = session_factory()
    try:
        import uuid
        if not s.get(db.ModelEndpoint, endpoint_id):
            s.add(db.ModelEndpoint(id=endpoint_id, name="codex", base_url=cx.BASE_URL,
                                   owner=owner, is_enabled=True))
        s.add(db.EndpointOAuthToken(id=uuid.uuid4().hex, endpoint_id=endpoint_id, owner=owner,
              provider_id="openai-codex", access_token=access, refresh_token=refresh,
              account_id="acct-xyz", expires_at=expires_at, status=status))
        s.commit()
    finally:
        s.close()


def _ref(endpoint_id, owner="alice"):
    return EndpointRef(url=cx.BASE_URL + "/responses", model="gpt-5.4", endpoint_id=endpoint_id,
                       owner=owner, provider_id="openai-codex", auth_type="oauth")


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _delta(seconds):
    from datetime import timedelta
    return timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# Service: device-code + refresh protocol
# ---------------------------------------------------------------------------

class TestCodexOAuthService:
    async def test_device_code_request(self, codex_http):
        codex_http.route(cx.DEVICE_USERCODE_URL, httpx.Response(200, json={
            "user_code": "WXYZ-1234", "device_auth_id": "dev-abc", "interval": 7, "expires_in": 600}))
        start = await cx.request_device_code()
        assert start.user_code == "WXYZ-1234" and start.device_auth_id == "dev-abc"
        assert start.interval == 7 and start.verification_uri == cx.DEVICE_VERIFICATION_URI
        assert start.expires_at is not None

    async def test_device_code_interval_clamped(self, codex_http):
        codex_http.route(cx.DEVICE_USERCODE_URL, httpx.Response(200, json={
            "user_code": "U", "device_auth_id": "d", "interval": 1}))
        start = await cx.request_device_code()
        assert start.interval == cx.MIN_POLL_INTERVAL_SECONDS  # 1 -> clamped to 3

    async def test_poll_pending_then_success(self, codex_http):
        codex_http.route(
            cx.DEVICE_TOKEN_URL,
            httpx.Response(403, json={}),
            httpx.Response(200, json={"authorization_code": "ac", "code_verifier": "cv"}),
        )
        assert await cx.poll_device_token("d", "U") is None
        res = await cx.poll_device_token("d", "U")
        assert res.authorization_code == "ac" and res.code_verifier == "cv"

    async def test_exchange_and_account_id(self, codex_http):
        access = _jwt()
        codex_http.route(cx.TOKEN_URL, httpx.Response(200, json={
            "access_token": access, "refresh_token": "r1"}))
        ts = await cx.exchange_device_code("ac", "cv")
        assert ts.access_token == access and ts.refresh_token == "r1"
        assert ts.account_id == "acct-xyz" and ts.expires_at is not None

    async def test_refresh_rotates_when_new_token_returned(self, codex_http):
        codex_http.route(cx.TOKEN_URL, httpx.Response(200, json={
            "access_token": _jwt(), "refresh_token": "r2"}))
        ts = await cx.refresh_tokens("r1")
        assert ts.refresh_token == "r2"

    async def test_refresh_keeps_prior_when_no_new_token(self, codex_http):
        codex_http.route(cx.TOKEN_URL, httpx.Response(200, json={"access_token": _jwt()}))
        ts = await cx.refresh_tokens("r-keep")
        assert ts.refresh_token == "r-keep"

    async def test_refresh_429_is_quota_no_relogin(self, codex_http):
        codex_http.route(cx.TOKEN_URL, httpx.Response(429, json={}))
        with pytest.raises(cx.CodexAuthError) as ei:
            await cx.refresh_tokens("r")
        assert ei.value.code == "quota" and ei.value.relogin_required is False

    async def test_refresh_invalid_grant_requires_relogin(self, codex_http):
        codex_http.route(cx.TOKEN_URL, httpx.Response(400, json={"error": "invalid_grant"}))
        with pytest.raises(cx.CodexAuthError) as ei:
            await cx.refresh_tokens("r")
        assert ei.value.code == "invalid_grant" and ei.value.relogin_required is True

    def test_expiry_helpers(self):
        assert cx.access_token_is_expiring(_jwt(exp_offset=60)) is True
        assert cx.access_token_is_expiring(_jwt(exp_offset=3600)) is False
        assert cx.extract_account_id(_jwt(account_id="abc")) == "abc"
        assert cx.extract_account_id("not-a-jwt") is None


# ---------------------------------------------------------------------------
# auth.resolve OAuth branch
# ---------------------------------------------------------------------------

class TestResolveOAuth:
    async def test_fresh_token_passthrough(self, oauth_db):
        access = _jwt(exp_offset=3600)
        _seed_token(oauth_db, "ep-fresh", access, "r", _utcnow() + _delta(3600))
        cred = await auth.resolve(_ref("ep-fresh"), CODEX_SPEC)
        assert cred.headers["Authorization"] == f"Bearer {access}"
        assert cred.headers["chatgpt-account-id"] == "acct-xyz"

    async def test_refresh_on_expiry_persists_rotation(self, oauth_db, codex_http):
        new = _jwt(exp_offset=7200)
        _seed_token(oauth_db, "ep-exp", _jwt(exp_offset=30), "r-old", _utcnow() - _delta(10))
        codex_http.route(cx.TOKEN_URL, httpx.Response(200, json={
            "access_token": new, "refresh_token": "r-new"}))
        cred = await auth.resolve(_ref("ep-exp"), CODEX_SPEC)
        assert cred.headers["Authorization"] == f"Bearer {new}"
        s = oauth_db()
        try:
            row = s.query(db.EndpointOAuthToken).filter_by(endpoint_id="ep-exp").first()
            assert row.access_token == new and row.refresh_token == "r-new"
            assert row.status == "active" and row.last_refresh_at is not None
        finally:
            s.close()

    async def test_single_flight_refresh(self, oauth_db, codex_http):
        _seed_token(oauth_db, "ep-sf", _jwt(exp_offset=10), "r", _utcnow() - _delta(5))
        codex_http.route(cx.TOKEN_URL, httpx.Response(200, json={
            "access_token": _jwt(exp_offset=7200), "refresh_token": "r-sf2"}))
        results = await asyncio.gather(*[auth.resolve(_ref("ep-sf"), CODEX_SPEC) for _ in range(5)])
        assert codex_http.count(cx.TOKEN_URL) == 1       # 5 concurrent resolves -> 1 refresh
        assert all(c.headers["Authorization"].startswith("Bearer ") for c in results)

    async def test_needs_relogin_status_short_circuits_401(self, oauth_db):
        _seed_token(oauth_db, "ep-relog", _jwt(), "r", _utcnow() + _delta(3600), status="needs_relogin")
        with pytest.raises(Exception) as ei:
            await auth.resolve(_ref("ep-relog"), CODEX_SPEC)
        assert getattr(ei.value, "status_code", None) == 401

    async def test_quota_maps_to_429_and_persists_status(self, oauth_db, codex_http):
        _seed_token(oauth_db, "ep-q", _jwt(exp_offset=10), "r", _utcnow() - _delta(5))
        codex_http.route(cx.TOKEN_URL, httpx.Response(429, json={}))
        with pytest.raises(Exception) as ei:
            await auth.resolve(_ref("ep-q"), CODEX_SPEC)
        assert getattr(ei.value, "status_code", None) == 429
        s = oauth_db()
        try:
            assert s.query(db.EndpointOAuthToken).filter_by(endpoint_id="ep-q").first().status == "quota"
        finally:
            s.close()

    async def test_invalid_grant_maps_to_401_and_needs_relogin(self, oauth_db, codex_http):
        _seed_token(oauth_db, "ep-bad", _jwt(exp_offset=10), "r", _utcnow() - _delta(5))
        codex_http.route(cx.TOKEN_URL, httpx.Response(400, json={"error": "invalid_grant"}))
        with pytest.raises(Exception) as ei:
            await auth.resolve(_ref("ep-bad"), CODEX_SPEC)
        assert getattr(ei.value, "status_code", None) == 401
        s = oauth_db()
        try:
            assert s.query(db.EndpointOAuthToken).filter_by(endpoint_id="ep-bad").first().status == "needs_relogin"
        finally:
            s.close()

    async def test_not_connected_is_401(self, oauth_db):
        with pytest.raises(Exception) as ei:
            await auth.resolve(_ref("ep-missing"), CODEX_SPEC)
        assert getattr(ei.value, "status_code", None) == 401

    def test_sync_oauth_still_501(self, oauth_db):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            auth.resolve_sync(_ref("ep-any"), CODEX_SPEC)
        assert ei.value.status_code == 501


# ---------------------------------------------------------------------------
# Connect / status / cancel / disconnect / import routes
# ---------------------------------------------------------------------------

def _client(user="alice"):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()

    @app.middleware("http")
    async def _set_user(request, call_next):
        request.state.current_user = user
        return await call_next(request)

    app.include_router(routes_mod.setup_codex_oauth_routes())
    return TestClient(app)


class TestConnectRoutes:
    def test_connect_creates_pending_without_leaking_secrets(self, oauth_db, monkeypatch, codex_http):
        monkeypatch.setattr(routes_mod, "_spawn", lambda coro: coro.close())
        codex_http.route(cx.DEVICE_USERCODE_URL, httpx.Response(200, json={
            "user_code": "WXYZ-1234", "device_auth_id": "dev-secret", "interval": 5, "expires_in": 900}))
        body = _client().post("/api/providers/openai-codex/connect").json()
        assert body["user_code"] == "WXYZ-1234" and body["verification_uri"].endswith("/codex/device")
        blob = json.dumps(body)
        assert "dev-secret" not in blob and "access_token" not in blob and "refresh_token" not in blob
        s = oauth_db()
        try:
            ep = s.get(db.ModelEndpoint, body["endpoint_id"])
            at = s.get(db.CodexLoginAttempt, body["attempt_id"])
            assert ep.is_enabled is False and ep.owner == "alice" and ep.base_url == cx.BASE_URL
            assert at.status == "pending" and at.device_auth_id == "dev-secret"
        finally:
            s.close()

    def test_poll_loop_authorizes_and_enables(self, oauth_db, monkeypatch, codex_http):
        monkeypatch.setattr(routes_mod, "_spawn", lambda coro: coro.close())
        access = _jwt()
        codex_http.route(cx.DEVICE_USERCODE_URL, httpx.Response(200, json={
            "user_code": "U", "device_auth_id": "d", "interval": 5, "expires_in": 900}))
        body = _client().post("/api/providers/openai-codex/connect").json()
        aid, eid = body["attempt_id"], body["endpoint_id"]
        s = oauth_db(); s.get(db.CodexLoginAttempt, aid).interval = 0; s.commit(); s.close()
        codex_http.route(
            cx.DEVICE_TOKEN_URL,
            httpx.Response(403, json={}),
            httpx.Response(200, json={"authorization_code": "ac", "code_verifier": "cv"}),
        )
        codex_http.route(cx.TOKEN_URL, httpx.Response(200, json={
            "access_token": access, "refresh_token": "r-1"}))
        asyncio.run(routes_mod._run_login_poll(aid))
        s = oauth_db()
        try:
            assert s.get(db.CodexLoginAttempt, aid).status == "authorized"
            assert s.get(db.ModelEndpoint, eid).is_enabled is True
            tok = s.query(db.EndpointOAuthToken).filter_by(endpoint_id=eid).first()
            assert tok.access_token == access and tok.refresh_token == "r-1" and tok.status == "active"
        finally:
            s.close()

    def test_status_summary_is_state_only(self, oauth_db):
        _seed_token(oauth_db, "ep-1", _jwt(), "r", _utcnow() + _delta(3600))
        summ = _client().get("/api/providers/openai-codex/status").json()
        assert len(summ["connected"]) == 1 and summ["connected"][0]["status"] == "active"
        blob = json.dumps(summ)
        assert "acct-xyz" not in blob and "access_token" not in blob

    def test_disconnect_purges_token_and_endpoint(self, oauth_db):
        _seed_token(oauth_db, "ep-d", _jwt(), "r", _utcnow() + _delta(3600))
        assert _client().post("/api/providers/openai-codex/disconnect/ep-d").json()["ok"] is True
        s = oauth_db()
        try:
            assert s.get(db.ModelEndpoint, "ep-d") is None
            assert s.query(db.EndpointOAuthToken).filter_by(endpoint_id="ep-d").first() is None
        finally:
            s.close()

    def test_cancel_deletes_endpoint_but_keeps_attempt(self, oauth_db, monkeypatch, codex_http):
        monkeypatch.setattr(routes_mod, "_spawn", lambda coro: coro.close())
        codex_http.route(cx.DEVICE_USERCODE_URL, httpx.Response(200, json={
            "user_code": "U", "device_auth_id": "d", "interval": 5, "expires_in": 900}))
        body = _client().post("/api/providers/openai-codex/connect").json()
        aid, eid = body["attempt_id"], body["endpoint_id"]
        assert _client().post(f"/api/providers/openai-codex/connect/{aid}/cancel").json()["status"] == "cancelled"
        s = oauth_db()
        try:
            assert s.get(db.ModelEndpoint, eid) is None              # pending endpoint purged
            surv = s.get(db.CodexLoginAttempt, aid)
            assert surv.status == "cancelled" and surv.endpoint_id is None   # log row kept, detached
        finally:
            s.close()

    def test_owner_isolation_404(self, oauth_db, monkeypatch, codex_http):
        monkeypatch.setattr(routes_mod, "_spawn", lambda coro: coro.close())
        codex_http.route(cx.DEVICE_USERCODE_URL, httpx.Response(200, json={
            "user_code": "U", "device_auth_id": "d", "interval": 5, "expires_in": 900}))
        body = _client("alice").post("/api/providers/openai-codex/connect").json()
        resp = _client("bob").get(f"/api/providers/openai-codex/connect/{body['attempt_id']}")
        assert resp.status_code == 404
