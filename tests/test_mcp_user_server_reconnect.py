"""Regression tests for issue #1789: a user-added MCP server whose session has
died (e.g. the asyncio task that created its stdio session has exited) must be
auto-reconnected on the next tool call, the way builtin servers already are.

call_tool used to gate the reconnect on is_builtin(), so user-added servers
kept failing every call until an app restart even though the integration page
showed them as connected.
"""
import asyncio
import time
from types import SimpleNamespace

import src.mcp_manager as mcp_manager_module
from src.mcp_manager import McpManager


class _DeadSession:
    """Session whose transport has died — every call raises."""

    async def call_tool(self, tool_name, arguments):
        raise RuntimeError("session closed (stdio task exited)")


class _LiveSession:
    def __init__(self):
        self.calls = []

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return SimpleNamespace(content=[SimpleNamespace(text="pong")], isError=False)


def _register_user_server(mgr, server_id="usersrv"):
    """Populate internals as connect_server would after a successful connect."""
    mgr._sessions[server_id] = _DeadSession()
    mgr._connections[server_id] = {"status": "connected", "name": server_id}
    # Tool schemas drive replay safety: only read-only/idempotent tools may be
    # replayed after a reconnect (a transport error can arrive after the write
    # committed, so replaying a mutating call can duplicate the action).
    mgr._tools[server_id] = [
        {"name": "list_items", "annotations": {"readOnlyHint": True}},
        {"name": "set_label", "annotations": {"idempotentHint": True}},
        {"name": "ping", "annotations": {"readOnlyHint": True}},
        {"name": "create_issue"},        # unannotated mutation
        {"name": "get_or_create_issue"}, # reads like a getter, but mutates
    ]
    mgr._connect_params[server_id] = {
        "name": server_id,
        "transport": "stdio",
        "command": "some-cmd",
        "args": [],
        "env": {},
        "url": None,
    }


def test_user_server_reconnects_and_retries():
    mgr = McpManager()
    _register_user_server(mgr)
    live = _LiveSession()

    async def fake_connect(server_id, **params):
        mgr._sessions[server_id] = live
        mgr._connections[server_id] = {"status": "connected", "name": params.get("name")}
        return True

    mgr.connect_server = fake_connect  # type: ignore[method-assign]

    result = asyncio.run(mgr.call_tool("mcp__usersrv__ping", {"x": 1}))
    assert result.get("exit_code") == 0
    assert result.get("stdout") == "pong"
    assert live.calls == [("ping", {"x": 1})]


def test_reconnect_uses_stored_connect_params():
    mgr = McpManager()
    _register_user_server(mgr)
    seen = {}

    async def fake_connect(server_id, **params):
        seen.update(params, server_id=server_id)
        mgr._sessions[server_id] = _LiveSession()
        return True

    mgr.connect_server = fake_connect  # type: ignore[method-assign]

    asyncio.run(mgr.call_tool("mcp__usersrv__ping", {}))
    assert seen["server_id"] == "usersrv"
    assert seen["transport"] == "stdio"
    assert seen["command"] == "some-cmd"


def test_missing_params_fails_gracefully():
    mgr = McpManager()
    # Session present but no stored connect params (never went through
    # connect_server) — must return an error dict, not crash.
    mgr._sessions["usersrv"] = _DeadSession()
    mgr._connections["usersrv"] = {"status": "connected", "name": "usersrv"}

    result = asyncio.run(mgr.call_tool("mcp__usersrv__ping", {}))
    assert result.get("exit_code") == 1
    assert "reconnect failed" in result.get("error", "")


def test_builtin_path_unchanged():
    mgr = McpManager()
    mgr._sessions["builtin_x"] = _DeadSession()
    # Replay safety applies to builtins too — declare the tool read-only so
    # this test exercises the reconnect path, not the replay refusal.
    mgr._tools["builtin_x"] = [{"name": "ping", "annotations": {"readOnlyHint": True}}]
    called = {}

    async def fake_reconnect_builtin(server_id):
        called["id"] = server_id
        mgr._sessions[server_id] = _LiveSession()
        return True

    mgr._reconnect_builtin = fake_reconnect_builtin  # type: ignore[method-assign]

    result = asyncio.run(mgr.call_tool("mcp__builtin_x__ping", {}))
    assert called["id"] == "builtin_x"
    assert result.get("exit_code") == 0


# --- Review follow-ups: replay safety, lifecycle, concurrency, retryability ---


def _fake_connect(mgr, session=None, ok=True, record=None):
    """Stand-in for connect_server that installs a live session."""
    live = session or _LiveSession()

    async def fake_connect(server_id, **params):
        if record is not None:
            record.append(server_id)
        if not ok:
            return False
        mgr._sessions[server_id] = live
        mgr._connections[server_id] = {"status": "connected", "name": params.get("name")}
        mgr._session_epoch[server_id] = mgr._session_epoch.get(server_id, 0) + 1
        return True

    mgr.connect_server = fake_connect  # type: ignore[method-assign]
    return live


def test_mutating_call_is_not_replayed_after_reconnect():
    """A write whose transport died has an UNKNOWN outcome — the server may
    have committed it. Reconnect, but never silently execute it twice."""
    mgr = McpManager()
    _register_user_server(mgr)
    live = _fake_connect(mgr)

    result = asyncio.run(mgr.call_tool("mcp__usersrv__create_issue", {"title": "x"}))

    assert result.get("exit_code") == 1
    assert result.get("indeterminate") is True
    assert "UNKNOWN" in result.get("error", "")
    assert live.calls == []                      # the write was NOT repeated
    assert mgr._sessions["usersrv"] is live      # but the session was restored


def test_readonly_tool_is_replayed():
    mgr = McpManager()
    _register_user_server(mgr)
    live = _fake_connect(mgr)

    result = asyncio.run(mgr.call_tool("mcp__usersrv__list_items", {"q": 1}))

    assert result.get("exit_code") == 0
    assert live.calls == [("list_items", {"q": 1})]


def test_idempotent_hint_allows_replay():
    mgr = McpManager()
    _register_user_server(mgr)
    live = _fake_connect(mgr)

    result = asyncio.run(mgr.call_tool("mcp__usersrv__set_label", {"label": "bug"}))

    assert result.get("exit_code") == 0
    assert live.calls == [("set_label", {"label": "bug"})]


def test_unknown_tool_fails_closed():
    """No schema for the tool -> we cannot prove it is safe -> do not replay."""
    mgr = McpManager()
    _register_user_server(mgr)
    live = _fake_connect(mgr)

    result = asyncio.run(mgr.call_tool("mcp__usersrv__mystery_op", {}))

    assert result.get("indeterminate") is True
    assert live.calls == []


def test_explicit_disconnect_prevents_resurrection():
    """An admin disable/delete revokes the cached config: a late in-flight
    failure must not respawn the server from that snapshot."""
    mgr = McpManager()
    _register_user_server(mgr)
    record = []
    _fake_connect(mgr, record=record)
    dead = mgr._sessions["usersrv"]

    async def scenario():
        await mgr.disconnect_server("usersrv")      # explicit disable/delete
        mgr._sessions["usersrv"] = dead             # a stale caller still holds it
        return await mgr.call_tool("mcp__usersrv__list_items", {})

    result = asyncio.run(scenario())

    assert result.get("exit_code") == 1
    assert record == []                              # never reconnected
    assert "usersrv" not in mgr._connect_params      # config revoked


def test_stale_epoch_does_not_reconnect_over_a_newer_session():
    mgr = McpManager()
    _register_user_server(mgr)
    record = []
    live = _fake_connect(mgr, record=record)

    async def scenario():
        # Another caller already recovered the server; ours holds the old one.
        await mgr._reconnect_any("usersrv")
        stale_epoch = 0
        return await mgr._reconnect_any("usersrv", expected_epoch=stale_epoch)

    assert asyncio.run(scenario()) is True
    assert len(record) == 1                          # no second reconnect
    assert mgr._sessions["usersrv"] is live


def test_concurrent_failures_coalesce_into_one_reconnect():
    mgr = McpManager()
    _register_user_server(mgr)
    record = []

    live = _LiveSession()

    async def slow_connect(server_id, **params):
        record.append(server_id)
        await asyncio.sleep(0.05)                    # widen the race window
        mgr._sessions[server_id] = live
        mgr._session_epoch[server_id] = mgr._session_epoch.get(server_id, 0) + 1
        return True

    mgr.connect_server = slow_connect  # type: ignore[method-assign]

    async def scenario():
        return await asyncio.gather(*(
            mgr.call_tool("mcp__usersrv__list_items", {"i": i}) for i in range(5)
        ))

    results = asyncio.run(scenario())

    assert len(record) == 1                          # ONE reconnect, not five
    assert all(r.get("exit_code") == 0 for r in results)


def test_failed_reconnect_can_still_recover_later(monkeypatch):
    """A transient reconnect failure must not strand the server until a
    manual reconnect: the next call retries from the cached config."""
    mgr = McpManager()
    _register_user_server(mgr)
    monkeypatch.setattr(mcp_manager_module, "_MCP_RECONNECT_COOLDOWN", 0.0)
    attempts = []
    live = _LiveSession()

    async def flaky_connect(server_id, **params):
        attempts.append(server_id)
        if len(attempts) == 1:
            return False                             # first recovery fails
        mgr._sessions[server_id] = live
        mgr._session_epoch[server_id] = mgr._session_epoch.get(server_id, 0) + 1
        return True

    mgr.connect_server = flaky_connect  # type: ignore[method-assign]

    async def scenario():
        first = await mgr.call_tool("mcp__usersrv__list_items", {})
        second = await mgr.call_tool("mcp__usersrv__list_items", {})
        return first, second

    first, second = asyncio.run(scenario())

    assert first.get("exit_code") == 1               # reconnect failed
    assert "usersrv" in mgr._connect_params          # config preserved
    assert second.get("exit_code") == 0              # later call recovers
    assert len(attempts) == 2


def test_reconnect_timeout_is_bounded_and_cleans_up(monkeypatch):
    """A stalled transport must not block callers forever, and whatever the
    cancelled connect left behind must be closed."""
    mgr = McpManager()
    _register_user_server(mgr)
    monkeypatch.setattr(mcp_manager_module, "_MCP_RECONNECT_TIMEOUT", 0.05)
    closed = []

    async def hanging_connect(server_id, **params):
        await asyncio.sleep(10)
        return True

    real_disconnect = mgr.disconnect_server

    async def spy_disconnect(server_id, *, revoke_params=True):
        closed.append((server_id, revoke_params))
        return await real_disconnect(server_id, revoke_params=revoke_params)

    mgr.connect_server = hanging_connect  # type: ignore[method-assign]
    mgr.disconnect_server = spy_disconnect  # type: ignore[method-assign]

    started = time.monotonic()
    result = asyncio.run(mgr.call_tool("mcp__usersrv__list_items", {}))
    elapsed = time.monotonic() - started

    assert result.get("exit_code") == 1
    # bounded: nowhere near the 10s the connect would have taken
    assert elapsed < 1.0, f"reconnect was not bounded (took {elapsed:.2f}s)"
    # cleanup ran and kept the config so a later call can retry
    assert closed and all(rev is False for _, rev in closed)
    assert "usersrv" in mgr._connect_params


def test_name_heuristic_does_not_authorize_replay():
    """`get_or_create_*` reads like a getter but mutates. A guess is not a
    contract: only the server's own annotations may authorize a replay."""
    mgr = McpManager()
    _register_user_server(mgr)
    live = _fake_connect(mgr)

    result = asyncio.run(mgr.call_tool("mcp__usersrv__get_or_create_issue", {"t": "x"}))

    assert result.get("indeterminate") is True
    assert live.calls == []


def test_annotations_object_is_honoured():
    """Servers may send annotations as a model, not a dict."""
    assert McpManager._replay_is_safe(
        {"name": "x", "annotations": SimpleNamespace(readOnlyHint=True)}
    ) is True
    assert McpManager._replay_is_safe(
        {"name": "x", "annotations": SimpleNamespace(readOnlyHint=False, idempotentHint=None)}
    ) is False
    assert McpManager._replay_is_safe({"name": "x"}) is False
    assert McpManager._replay_is_safe(None) is False


def test_no_session_calls_coalesce_into_one_reconnect():
    """Recovery from a *missing* session must be single-flight too: without
    that, each concurrent caller tears down the session a peer just restored
    and reports a false 'unknown outcome'."""
    mgr = McpManager()
    _register_user_server(mgr)
    del mgr._sessions["usersrv"]                 # nothing connected at all
    record = []
    live = _LiveSession()

    async def slow_connect(server_id, **params):
        record.append(server_id)
        await asyncio.sleep(0.05)
        mgr._sessions[server_id] = live
        mgr._session_epoch[server_id] = mgr._session_epoch.get(server_id, 0) + 1
        return True

    mgr.connect_server = slow_connect  # type: ignore[method-assign]

    async def scenario():
        return await asyncio.gather(*(
            mgr.call_tool("mcp__usersrv__list_items", {"i": i}) for i in range(5)
        ))

    results = asyncio.run(scenario())

    assert len(record) == 1, f"spawned {len(record)} reconnects for 5 concurrent calls"
    assert all(r.get("exit_code") == 0 for r in results)
    assert not any(r.get("indeterminate") for r in results)


def test_explicit_disconnect_prevents_builtin_resurrection():
    """Builtins carry no user params to revoke, so lifecycle state must gate
    them too — otherwise shutdown's disconnect_all() races an in-flight call
    into respawning an orphaned subprocess."""
    mgr = McpManager()
    mgr._sessions["builtin_x"] = _DeadSession()
    mgr._tools["builtin_x"] = [{"name": "ping", "annotations": {"readOnlyHint": True}}]
    respawned = []

    async def fake_reconnect_builtin(server_id):
        respawned.append(server_id)
        mgr._sessions[server_id] = _LiveSession()
        return True

    mgr._reconnect_builtin = fake_reconnect_builtin  # type: ignore[method-assign]
    mgr.is_builtin = lambda sid: sid.startswith("builtin_")  # type: ignore[method-assign]

    async def scenario():
        dead = mgr._sessions["builtin_x"]
        await mgr.disconnect_server("builtin_x")     # explicit shutdown/disable
        mgr._sessions["builtin_x"] = dead            # stale in-flight caller
        return await mgr.call_tool("mcp__builtin_x__ping", {})

    result = asyncio.run(scenario())

    assert respawned == [], f"respawned {respawned} after an explicit disconnect"
    assert result.get("exit_code") == 1


def test_failed_reconnect_enters_cooldown():
    """A permanently dead server must not make every tool call pay the full
    reconnect timeout."""
    mgr = McpManager()
    _register_user_server(mgr)
    attempts = []

    async def failing_connect(server_id, **params):
        attempts.append(server_id)
        return False

    mgr.connect_server = failing_connect  # type: ignore[method-assign]

    async def scenario():
        for _ in range(4):
            await mgr.call_tool("mcp__usersrv__list_items", {})

    asyncio.run(scenario())

    assert len(attempts) == 1, f"hammered the dead server {len(attempts)} times"


def test_reenabling_after_a_failed_connect_stays_recoverable():
    """Re-enabling a server is an intent, not an outcome: if its first connect
    attempt fails, automatic recovery must not stay locked out forever."""
    mgr = McpManager()
    _register_user_server(mgr)
    live = _LiveSession()
    attempt = {"n": 0}

    async def flaky_connect_stdio(server_id, name, command, args, env):
        attempt["n"] += 1
        if attempt["n"] == 1:
            return False                       # cold start fails
        mgr._sessions[server_id] = live
        return True

    mgr._connect_stdio = flaky_connect_stdio  # type: ignore[method-assign]

    async def scenario():
        await mgr.disconnect_server("usersrv")          # explicit disable
        assert "usersrv" in mgr._revoked
        # user re-enables it; the first attempt fails
        first = await mgr.connect_server(
            server_id="usersrv", name="usersrv", transport="stdio",
            command="some-cmd", args=[], env={},
        )
        # recovery must now be allowed again
        return first, await mgr._reconnect_any("usersrv")

    first, recovered = asyncio.run(scenario())

    assert first is False
    assert recovered is True, "server stayed permanently revoked after a failed re-enable"
    assert "usersrv" not in mgr._revoked


def test_contradictory_annotations_fail_closed():
    """readOnlyHint + destructiveHint is malformed — do not replay unless the
    server also promises idempotency."""
    assert McpManager._replay_is_safe(
        {"name": "x", "annotations": {"readOnlyHint": True, "destructiveHint": True}}
    ) is False
    assert McpManager._replay_is_safe(
        {"name": "x", "annotations": {"destructiveHint": True, "idempotentHint": True}}
    ) is True
    assert McpManager._replay_is_safe(
        {"name": "x", "annotations": {"readOnlyHint": True}}
    ) is True


# --- Transport coverage: the tool table feeds the replay decision, so every
# --- transport must preserve the server's annotations (stdio/SSE/HTTP).


class _FakeTool:
    def __init__(self, name, annotations):
        self.name = name
        self.description = "d"
        self.inputSchema = {"type": "object"}
        self.annotations = annotations


def _stub_mcp_transport(monkeypatch, tools):
    """Make every transport's session.list_tools() return `tools`."""
    class _Session:
        def __init__(self, *a, **k):
            pass

        async def initialize(self):
            return None

        async def list_tools(self):
            return SimpleNamespace(tools=tools)

    class _Transport:
        """SSE yields (read, write); Streamable HTTP yields a third element."""
        def __init__(self, arity):
            self.arity = arity

    class _Stack:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def aclose(self):
            return None

        async def enter_async_context(self, cm):
            if isinstance(cm, _Transport):
                return tuple([None] * cm.arity)
            return cm

    import contextlib
    import mcp
    import mcp.client.sse as sse_mod
    import mcp.client.streamable_http as http_mod
    monkeypatch.setattr(mcp, "ClientSession", _Session, raising=False)
    monkeypatch.setattr(contextlib, "AsyncExitStack", _Stack, raising=False)
    monkeypatch.setattr(sse_mod, "sse_client", lambda *a, **k: _Transport(2), raising=False)
    monkeypatch.setattr(http_mod, "streamablehttp_client", lambda *a, **k: _Transport(3), raising=False)


def test_sse_transport_preserves_tool_annotations(monkeypatch):
    mgr = McpManager()
    _stub_mcp_transport(monkeypatch, [_FakeTool("list_things", {"readOnlyHint": True})])

    ok = asyncio.run(mgr._connect_sse("sse1", "sse1", "https://example.invalid/sse"))

    assert ok is True
    tool = mgr._find_tool_schema("sse1", "list_things")
    assert tool is not None and tool.get("annotations") == {"readOnlyHint": True}
    assert McpManager._replay_is_safe(tool) is True


def test_http_transport_preserves_tool_annotations(monkeypatch):
    """Regression: the Streamable-HTTP path used to drop `annotations`, which
    silently downgraded every HTTP tool to 'unknown' for replay safety."""
    mgr = McpManager()
    _stub_mcp_transport(monkeypatch, [_FakeTool("list_things", {"readOnlyHint": True})])

    ok = asyncio.run(mgr._connect_http("http1", "http1", "https://example.invalid/mcp"))

    assert ok is True
    tool = mgr._find_tool_schema("http1", "list_things")
    assert tool is not None and tool.get("annotations") == {"readOnlyHint": True}
    assert McpManager._replay_is_safe(tool) is True


def test_unknown_server_id_allocates_no_recovery_state():
    """Tool names are model-supplied: an invented server id must not create
    per-server locks or cooldown entries."""
    mgr = McpManager()

    result = asyncio.run(mgr.call_tool("mcp__made_up_id__do_thing", {}))

    assert result.get("exit_code") == 1
    assert mgr._reconnect_locks == {}
    assert mgr._reconnect_failed_at == {}


def test_readonly_tool_after_reconnect_is_not_reported_unknown():
    """The tool table is empty while the session is gone; once the reconnect
    repopulates it, an annotated read-only tool must not be mislabelled."""
    mgr = McpManager()
    _register_user_server(mgr)
    del mgr._sessions["usersrv"]
    mgr._tools.pop("usersrv")                     # nothing known yet
    calls = []

    class _FailsOnce:
        async def call_tool(self, tool_name, arguments):
            calls.append(tool_name)
            if len(calls) == 1:
                raise RuntimeError("session closed")
            return SimpleNamespace(content=[SimpleNamespace(text="pong")], isError=False)

    session = _FailsOnce()

    async def fake_connect(server_id, **params):
        mgr._sessions[server_id] = session
        mgr._tools[server_id] = [{"name": "ping", "annotations": {"readOnlyHint": True}}]
        mgr._session_epoch[server_id] = mgr._session_epoch.get(server_id, 0) + 1
        return True

    mgr.connect_server = fake_connect  # type: ignore[method-assign]

    result = asyncio.run(mgr.call_tool("mcp__usersrv__ping", {}))

    assert result.get("indeterminate") is not True, "annotated read-only tool reported as unknown"
    assert result.get("exit_code") == 0
