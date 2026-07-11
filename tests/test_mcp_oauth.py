import asyncio
import json
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


def _fake_storage(oauth_tokens):
    class FakeSrv:
        pass

    srv = FakeSrv()
    srv.oauth_tokens = oauth_tokens

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

    return srv, mcp_oauth.DbTokenStorage("srv-1", session_factory=lambda: FakeSession())


def test_load_falls_back_to_empty_dict_for_non_dict_json():
    # A corrupted/migrated oauth_tokens column holding a JSON array, not an
    # object, must not crash _load()'s callers with AttributeError.
    _srv, storage = _fake_storage('["stale", "data"]')
    assert storage._load() == {}


def test_get_tokens_returns_none_for_non_dict_oauth_tokens():
    _srv, storage = _fake_storage("42")

    async def go():
        return await storage.get_tokens()

    assert asyncio.run(go()) is None


def test_update_recovers_from_non_dict_oauth_tokens():
    # _update() must not raise TypeError trying to item-assign into a list.
    srv, storage = _fake_storage('["stale", "data"]')
    storage._update("tokens", {"access_token": "new"})
    assert json.loads(srv.oauth_tokens) == {"tokens": {"access_token": "new"}}
