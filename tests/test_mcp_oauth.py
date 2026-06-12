import asyncio
from src import mcp_oauth


def test_registry_resolve_returns_code_and_state():
    async def go():
        fut = mcp_oauth.register_pending("st-1")
        assert mcp_oauth.resolve_pending("st-1", "the-code") is True
        return await asyncio.wait_for(fut, timeout=1)
    code, state = asyncio.run(go())
    assert code == "the-code"
    assert state == "st-1"


def test_resolve_unknown_state_is_false():
    assert mcp_oauth.resolve_pending("nope", "x") is False


def test_register_pending_prunes_abandoned_flows():
    import time as _t

    async def go():
        mcp_oauth._pending.clear()
        mcp_oauth._pending_ts.clear()
        old = mcp_oauth.register_pending("old-state")
        # Backdate the entry past the authorization window.
        mcp_oauth._pending_ts["old-state"] = _t.monotonic() - (mcp_oauth.AUTH_WAIT_SECONDS + 1)
        # A new registration triggers a prune of the stale one.
        mcp_oauth.register_pending("new-state")
        return old

    old = asyncio.run(go())
    assert "old-state" not in mcp_oauth._pending
    assert "old-state" not in mcp_oauth._pending_ts
    assert "new-state" in mcp_oauth._pending
    assert old.cancelled()


def test_build_provider_has_odysseus_client_metadata():
    p = mcp_oauth.build_provider("srv-1", "https://example.com/mcp")
    md = p.context.client_metadata
    assert md.client_name == "Odysseus"
    assert "authorization_code" in md.grant_types
    assert "refresh_token" in md.grant_types
    assert str(md.redirect_uris[0]).rstrip("/") == mcp_oauth.REDIRECT_URI.rstrip("/")


def test_db_token_storage_round_trip():
    from mcp.shared.auth import OAuthToken

    class FakeSrv:
        oauth_tokens = None

    srv = FakeSrv()

    class FakeQuery:
        def filter(self, *a):
            return self

        def first(self):
            return srv

    class FakeSession:
        def query(self, *a):
            return FakeQuery()

        def commit(self):
            pass

        def close(self):
            pass

    storage = mcp_oauth.DbTokenStorage("srv-1", session_factory=lambda: FakeSession())

    async def go():
        await storage.set_tokens(OAuthToken(access_token="abc", token_type="Bearer"))
        return await storage.get_tokens()

    t = asyncio.run(go())
    assert t.access_token == "abc"
    assert srv.oauth_tokens is not None  # persisted as JSON
