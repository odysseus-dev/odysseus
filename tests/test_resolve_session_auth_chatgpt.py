"""resolve_session_auth must not persist the ChatGPT Subscription bearer.

The ChatGPT Subscription access token is a short-lived OAuth bearer re-resolved
(and refreshed) on every request. resolve_session_auth() may set it on the
in-memory session for the current request, but it must never write it back into
the sessions table — otherwise the live token sits at rest as
"Authorization: Bearer ...". Only the encrypted refresh token in
ProviderAuthSession is allowed to persist.
"""

import types

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import routes.chat_helpers as chat_helpers
import src.endpoint_resolver as endpoint_resolver
from core.database import Base, ModelEndpoint, Session as DbSession

_CODEX_BASE = "https://chatgpt.com/backend-api/codex"


def _mem_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    # Match production SessionLocal (core.database) which is autoflush=False.
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(chat_helpers, "SessionLocal", TestSessionLocal)
    return TestSessionLocal


def test_chatgpt_subscription_auth_is_not_written_to_sessions_table(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)
    db = TestSessionLocal()
    try:
        db.add(ModelEndpoint(
            id="ep1", name="ChatGPT Subscription", base_url=_CODEX_BASE,
            provider_auth_id="auth1", owner="alice", is_enabled=True, api_key=None,
        ))
        db.add(DbSession(
            id="sess1", name="chat", endpoint_url=_CODEX_BASE,
            model="gpt-5.1-codex", owner="alice", headers={},
        ))
        db.commit()
    finally:
        db.close()

    # A live access token is resolved at request time.
    monkeypatch.setattr(
        endpoint_resolver, "resolve_endpoint_runtime",
        lambda ep, owner=None: (_CODEX_BASE, "live-access-token"),
    )

    sess = types.SimpleNamespace(
        id="sess1", endpoint_url=_CODEX_BASE, model="gpt-5.1-codex",
        owner="alice", headers={},
    )
    chat_helpers.resolve_session_auth(sess, "sess1", owner="alice")

    # In-memory session got request-local auth for this request...
    assert any(k.lower() == "authorization" for k in sess.headers)
    assert sess.headers["Authorization"] == "Bearer live-access-token"

    # ...but the DB row must NOT have the bearer persisted.
    db = TestSessionLocal()
    try:
        row = db.query(DbSession).filter(DbSession.id == "sess1").first()
        stored = row.headers or {}
        assert not any(k.lower() == "authorization" for k in stored), (
            f"ChatGPT bearer leaked into sessions table: {stored}"
        )
    finally:
        db.close()


def test_non_subscription_auth_is_still_persisted_to_sessions_table(monkeypatch):
    """The early-return must be scoped to ChatGPT Subscription only.

    Ordinary endpoints rely on resolve_session_auth() persisting the resolved
    headers into the sessions table so they aren't re-resolved on every request.
    If the is_chatgpt_subscription guard ever widened, this would silently break;
    this test pins the persistence path as still reached for normal endpoints.
    """
    base = "https://api.example.com/v1"
    TestSessionLocal = _mem_db(monkeypatch)
    db = TestSessionLocal()
    try:
        db.add(ModelEndpoint(
            id="ep1", name="Generic", base_url=base,
            owner="alice", is_enabled=True, api_key="sk-static",
        ))
        db.add(DbSession(
            id="sess1", name="chat", endpoint_url=base,
            model="gpt-x", owner="alice", headers={},
        ))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        endpoint_resolver, "resolve_endpoint_runtime",
        lambda ep, owner=None: (base, "sk-static"),
    )

    sess = types.SimpleNamespace(
        id="sess1", endpoint_url=base, model="gpt-x", owner="alice", headers={},
    )
    chat_helpers.resolve_session_auth(sess, "sess1", owner="alice")

    # In-memory session got auth...
    assert any(k.lower() in ("authorization", "x-api-key") for k in sess.headers)

    # ...AND it was persisted to the DB row (the normal, non-subscription path).
    db = TestSessionLocal()
    try:
        row = db.query(DbSession).filter(DbSession.id == "sess1").first()
        stored = row.headers or {}
        assert any(k.lower() in ("authorization", "x-api-key") for k in stored), (
            f"non-subscription auth was not persisted: {stored}"
        )
    finally:
        db.close()


def test_chatgpt_subscription_clears_previously_persisted_bearer(monkeypatch):
    """A bearer left at rest by an older code path is stripped on next resolve."""
    TestSessionLocal = _mem_db(monkeypatch)
    db = TestSessionLocal()
    try:
        db.add(ModelEndpoint(
            id="ep1", name="ChatGPT Subscription", base_url=_CODEX_BASE,
            provider_auth_id="auth1", owner="alice", is_enabled=True, api_key=None,
        ))
        # Simulate the leak: a stale bearer already sitting in the sessions table.
        db.add(DbSession(
            id="sess1", name="chat", endpoint_url=_CODEX_BASE,
            model="gpt-5.1-codex", owner="alice",
            headers={"Authorization": "Bearer stale-leaked-token"},
        ))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        endpoint_resolver,
        "resolve_endpoint_runtime",
        lambda ep, owner=None: (_CODEX_BASE, "live-access-token"),
    )

    sess = types.SimpleNamespace(
        id="sess1", endpoint_url=_CODEX_BASE, model="gpt-5.1-codex",
        owner="alice", headers={},
    )
    chat_helpers.resolve_session_auth(sess, "sess1", owner="alice")

    # The stale bearer must have been stripped from the DB row.
    db = TestSessionLocal()
    try:
        row = db.query(DbSession).filter(DbSession.id == "sess1").first()
        stored = row.headers or {}
        assert not any(k.lower() == "authorization" for k in stored), (
            f"stale ChatGPT bearer was not cleared: {stored}"
        )
    finally:
        db.close()


# ── Multi-account: exact endpoint → exact auth session ──────────────────────

def _seed_two_accounts(TestSessionLocal, owner="alice"):
    import datetime as _dt
    db = TestSessionLocal()
    try:
        older = _dt.datetime(2026, 1, 1)
        newer = _dt.datetime(2026, 6, 1)
        db.add(ModelEndpoint(
            id="ep-a", name="ChatGPT · codex00", base_url=_CODEX_BASE, provider_auth_id="auth-a",
            owner=owner, is_enabled=True, api_key=None, created_at=older, updated_at=older,
        ))
        db.add(ModelEndpoint(
            id="ep-b", name="ChatGPT · codex01", base_url=_CODEX_BASE, provider_auth_id="auth-b",
            owner=owner, is_enabled=True, api_key=None, created_at=newer, updated_at=newer,
        ))
        db.commit()
    finally:
        db.close()


def _patch_runtime(monkeypatch, seen):
    def fake(ep, owner=None):
        seen.append((ep.id, ep.provider_auth_id, owner))
        return (_CODEX_BASE, f"bearer-for-{ep.provider_auth_id}")

    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint_runtime", fake)


def test_bound_session_uses_exactly_its_own_account_even_when_urls_match(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)
    _seed_two_accounts(TestSessionLocal)
    db = TestSessionLocal()
    try:
        db.add(DbSession(id="sess-b", name="chat", endpoint_url=_CODEX_BASE + "/responses",
                         model="gpt-5.5", owner="alice", headers={}, endpoint_id="ep-b"))
        db.commit()
    finally:
        db.close()
    seen = []
    _patch_runtime(monkeypatch, seen)

    sess = types.SimpleNamespace(id="sess-b", endpoint_url=_CODEX_BASE + "/responses", model="gpt-5.5",
                                 owner="alice", headers={}, endpoint_id="ep-b")
    chat_helpers.resolve_session_auth(sess, "sess-b", owner="alice")

    # Account B (the newer row) is used because the session is bound to ep-b,
    # even though ep-a shares the same URL and would otherwise sort first.
    assert seen == [("ep-b", "auth-b", "alice")]
    assert sess.headers["Authorization"] == "Bearer bearer-for-auth-b"
    db = TestSessionLocal()
    try:
        row = db.query(DbSession).filter(DbSession.id == "sess-b").first()
        assert row.endpoint_id == "ep-b"
        assert not any(k.lower() == "authorization" for k in (row.headers or {}))
    finally:
        db.close()


def test_legacy_unbound_session_picks_oldest_account_and_gets_bound(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)
    _seed_two_accounts(TestSessionLocal)
    db = TestSessionLocal()
    try:
        db.add(DbSession(id="sess-legacy", name="chat", endpoint_url=_CODEX_BASE,
                         model="gpt-5.5", owner="alice", headers={}))
        db.commit()
    finally:
        db.close()
    seen = []
    _patch_runtime(monkeypatch, seen)
    sess = types.SimpleNamespace(id="sess-legacy", endpoint_url=_CODEX_BASE, model="gpt-5.5",
                                 owner="alice", headers={}, endpoint_id=None)
    chat_helpers.resolve_session_auth(sess, "sess-legacy", owner="alice")
    # Deterministic: the oldest endpoint is the one that existed when the
    # legacy session was created; the choice is persisted so it never drifts.
    assert seen == [("ep-a", "auth-a", "alice")]
    assert sess.endpoint_id == "ep-a"
    db = TestSessionLocal()
    try:
        row = db.query(DbSession).filter(DbSession.id == "sess-legacy").first()
        assert row.endpoint_id == "ep-a"
    finally:
        db.close()

    # A second resolve honours the persisted binding (no re-derivation drift).
    seen.clear()
    sess2 = types.SimpleNamespace(id="sess-legacy", endpoint_url=_CODEX_BASE, model="gpt-5.5",
                                  owner="alice", headers={}, endpoint_id="ep-a")
    chat_helpers.resolve_session_auth(sess2, "sess-legacy", owner="alice")
    assert seen == [("ep-a", "auth-a", "alice")]


def test_bound_session_never_borrows_a_sibling_account_when_its_endpoint_is_gone(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)
    _seed_two_accounts(TestSessionLocal)
    db = TestSessionLocal()
    try:
        # Two more siblings share the URL, but the bound endpoint is disabled.
        ep_b = db.query(ModelEndpoint).filter(ModelEndpoint.id == "ep-b").first()
        ep_b.is_enabled = False
        db.add(ModelEndpoint(id="ep-c", name="ChatGPT · codex02", base_url=_CODEX_BASE, provider_auth_id="auth-c",
                             owner="alice", is_enabled=True, api_key=None))
        db.add(DbSession(id="sess-b", name="chat", endpoint_url=_CODEX_BASE, model="gpt-5.5",
                         owner="alice", headers={}, endpoint_id="ep-b"))
        db.commit()
    finally:
        db.close()
    seen = []
    _patch_runtime(monkeypatch, seen)
    sess = types.SimpleNamespace(id="sess-b", endpoint_url=_CODEX_BASE, model="gpt-5.5",
                                 owner="alice", headers={}, endpoint_id="ep-b")
    chat_helpers.resolve_session_auth(sess, "sess-b", owner="alice")
    # Ambiguous → no silent account switch, no bearer resolved.
    assert seen == []
    assert not any(k.lower() == "authorization" for k in sess.headers)


def test_other_owner_cannot_resolve_through_alices_accounts(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)
    _seed_two_accounts(TestSessionLocal, owner="alice")
    db = TestSessionLocal()
    try:
        db.add(DbSession(id="sess-m", name="chat", endpoint_url=_CODEX_BASE, model="gpt-5.5",
                         owner="mallory", headers={}, endpoint_id="ep-a"))
        db.commit()
    finally:
        db.close()
    seen = []
    _patch_runtime(monkeypatch, seen)
    sess = types.SimpleNamespace(id="sess-m", endpoint_url=_CODEX_BASE, model="gpt-5.5",
                                 owner="mallory", headers={}, endpoint_id="ep-a")
    chat_helpers.resolve_session_auth(sess, "sess-m", owner="mallory")
    assert seen == []
    assert sess.headers == {}


def test_route_descriptors_distinguish_same_model_on_two_accounts(monkeypatch):
    """Same base URL + same model on A and B are two routes, not duplicates."""
    TestSessionLocal = _mem_db(monkeypatch)
    monkeypatch.setattr(endpoint_resolver, "SessionLocal", TestSessionLocal)
    _seed_two_accounts(TestSessionLocal)
    db = TestSessionLocal()
    try:
        import json as _json
        for ep in db.query(ModelEndpoint).all():
            ep.cached_models = _json.dumps(["gpt-5.5"])
        db.commit()
    finally:
        db.close()
    seen = []
    _patch_runtime(monkeypatch, seen)

    route_a = endpoint_resolver.resolve_endpoint_by_id("ep-a", "gpt-5.5", owner="alice", require_exact_model=True)
    route_b = endpoint_resolver.resolve_endpoint_by_id("ep-b", "gpt-5.5", owner="alice", require_exact_model=True)
    assert route_a is not None and route_b is not None
    assert route_a[0] == route_b[0] and route_a[1] == route_b[1]  # same URL + model
    assert route_a[2]["Authorization"] != route_b[2]["Authorization"]  # different accounts
    assert route_a != route_b

    desc_a = endpoint_resolver.resolve_route_descriptor_by_id("ep-a", route_a[0], "gpt-5.5", route_a[2], owner="alice")
    desc_b = endpoint_resolver.resolve_route_descriptor_by_id("ep-b", route_b[0], "gpt-5.5", route_b[2], owner="alice")
    assert desc_a["endpoint_id"] == "ep-a" and desc_a["endpoint_label"] == "ChatGPT · codex00"
    assert desc_b["endpoint_id"] == "ep-b" and desc_b["endpoint_label"] == "ChatGPT · codex01"
    # Provenance for B's route must not be attributed to A.
    assert endpoint_resolver.resolve_route_descriptor_by_id("ep-a", route_b[0], "gpt-5.5", route_b[2], owner="alice") is None
    for desc in (desc_a, desc_b):
        assert "Authorization" not in str(desc) and "bearer-for" not in str(desc)
