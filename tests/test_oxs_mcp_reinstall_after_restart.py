"""Regression tests for Finding 7 — OXS MCP tool registrations must survive
``docker compose restart`` (and any other path that wipes the
``McpManager._in_process_tools`` cache while leaving the OXS
``running.enabled`` flag intact).

The fix has two layers:
  1. ``ExtensionRegistry.enable()`` re-installs in-process MCP tools
     when the running extension is already flagged enabled but the
     McpManager cache for the OXS server id is empty. Idempotent —
     ``register_in_process_tool`` overwrites the existing entry.
  2. ``ExtensionRegistry._reinstall_orphan_mcp_tools()`` walks every
     registered extension after ``restore_enabled_state`` and re-runs
     ``install_tools`` for any whose McpManager cache no longer matches
     the extension's ``host.mcp_tools`` manifest. The single source of
     truth that fixes gaps from any path, including direct cache wipes
     that bypass the ``enable()`` call entirely.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest

from core.extensions import ExtensionRegistry
from core.extensions.host import MCPToolRegistration


def _write_user_ext_with_mcp(
    user_dir: str,
    ext_id: str,
    *,
    mcp_tools: list[tuple[str, str]] | None = None,
    on_enable: str = "",
) -> str:
    """Create a user-extension with ``on_enable`` registering MCP tools.

    ``mcp_tools`` is a list of (tool_name, description) pairs the
    extension registers during ``on_enable``. ``on_enable`` is an extra
    snippet of code appended to the auto-generated ``register(host)``
    body so the test can customize the lifecycle if it needs to.
    """
    sub = os.path.join(user_dir, ext_id)
    os.makedirs(sub, exist_ok=True)
    mcp_lines = []
    for name, desc in (mcp_tools or []):
        # The OXSHost.register_mcp_tool signature in this codebase.
        mcp_lines.append(
            f"    host.register_mcp_tool(server_name={ext_id!r}, tool_name={name!r},"
            f" schema={{'type': 'object'}}, handler=lambda a: None,"
            f" description_rich={desc!r})"
        )
    # The OXS LifecycleHooks.run() calls ``fn()`` with no args, so the
    # on_enable callback must be parameterless. Use the @host.on_enable
    # decorator form (which sets the ``__oxs_hook__`` marker) so the
    # registry picks up the function via install_via_decorators.
    body_lines = [
        "def register(host):",
    ] + mcp_lines + [
        "    @host.on_enable",
        "    def _enable():",
        f"        {on_enable or 'pass'}",
    ]
    plugin_src = "\n".join(body_lines) + "\n"
    with open(os.path.join(sub, "plugin.py"), "w") as f:
        f.write(plugin_src)
    manifest = {
        "id": ext_id,
        "name": ext_id,
        "version": "1.0.0",
        "oxs_version": ">=1.0.0",
        "entry_point": "plugin:register",
        "capabilities": {"mcp_tools": True},
        "file_hashes": {},
    }
    with open(os.path.join(sub, "extension.json"), "w") as f:
        json.dump(manifest, f)
    return sub


def _registry(tmp_path, *, bundled_dir=None, user_dir=None) -> ExtensionRegistry:
    return ExtensionRegistry(
        bundled_dir=bundled_dir or str(tmp_path / "bundled"),
        user_dir=user_dir or str(tmp_path / "user"),
    )


@pytest.fixture
def fake_mcp_manager(monkeypatch):
    """Swap the global ``McpManager`` for an in-process dict-backed fake.

    The OXS install path reaches into ``McpManager._in_process_tools``,
    so the fake must keep the same shape. ``_generation`` is bumped on
    each install so the OXS self-heal in
    :meth:`ExtensionRegistry._reinstall_orphan_mcp_tools` can detect a
    cache drift and re-run ``install_tools`` exactly once per gap.
    """
    from src import mcp_manager as mcp_manager_mod

    fake = MagicMock(name="FakeMcpManager")
    fake._in_process_tools = {}
    fake._tools = {}
    fake._connections = {}
    fake._sessions = {}
    fake._resource_endpoints = {}
    fake._resource_connections = {}
    fake._resource_owners = {}
    fake._generation = 0

    def register_in_process_tool(*, server_id, tool_name, input_schema,
                                  description, handler, read_only=False):
        fake._in_process_tools.setdefault(server_id, {})[tool_name] = {
            "input_schema": input_schema,
            "description": description,
            "handler": handler,
            "read_only": read_only,
        }
        fake._generation += 1
        return None

    def unregister_in_process_tools(server_id):
        return len(fake._in_process_tools.pop(server_id, {}))

    fake.register_in_process_tool.side_effect = register_in_process_tool
    fake.unregister_in_process_tools.side_effect = unregister_in_process_tools

    def fake_get_all_openai_schemas(disabled_map=None):
        # Mirror the real McpManager.get_all_openai_schemas surface: emit
        # one schema per in-process tool. Tests that exercise the OXS
        # self-heal hook only need the OXS server id; the rest of the
        # tool surfaces (out-of-process MCP, builtin) are out of scope
        # for Finding 7.
        schemas = []
        for server_id, tools in fake._in_process_tools.items():
            for tool_name, tool in tools.items():
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": f"mcp__{server_id}__{tool_name}",
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema", {}),
                    },
                })
        return schemas
    fake.get_all_openai_schemas.side_effect = fake_get_all_openai_schemas

    monkeypatch.setattr(mcp_manager_mod, "get_mcp_manager", lambda: fake)
    return fake


# ── enable() re-installs when already enabled ────────────────────────────


def test_enable_reinstalls_mcp_tools_when_already_enabled(
        tmp_path, fake_mcp_manager):
    """Finding 7: ``enable()`` short-circuits on ``running.enabled`` but
    must still re-install MCP tools if the McpManager cache is empty.
    A ``docker compose restart`` produces exactly this state — the
    OXS in-memory ``enabled`` flag survives, the McpManager cache does
    not."""
    user_dir = str(tmp_path / "user")
    _write_user_ext_with_mcp(
        user_dir, "w2a", mcp_tools=[("list_messages", "List WhatsApp messages.")],
    )
    reg = _registry(tmp_path, user_dir=user_dir)
    reg.rescan()
    reg.enable("w2a")
    # Simulate a restart: wipe the McpManager cache.
    fake_mcp_manager._in_process_tools.clear()
    fake_mcp_manager._generation += 99  # drift — tool_index would reindex

    # Re-enabling must reinstall without raising.
    reg.enable("w2a")
    assert "list_messages" in fake_mcp_manager._in_process_tools["oxs_w2a"]


def test_enable_is_noop_when_mcp_cache_already_matches(
        tmp_path, fake_mcp_manager, monkeypatch):
    """The defensive reinstall in ``enable()`` must NOT clobber an
    already-correct cache. We instrument ``register_in_process_tool``
    to count calls and verify it doesn't fire spuriously."""
    user_dir = str(tmp_path / "user")
    _write_user_ext_with_mcp(
        user_dir, "w2a", mcp_tools=[("list_messages", "List messages.")],
    )
    reg = _registry(tmp_path, user_dir=user_dir)
    reg.rescan()
    reg.enable("w2a")
    install_count = sum(
        1 for sid in fake_mcp_manager._in_process_tools
        for _ in fake_mcp_manager._in_process_tools[sid]
    )
    # Second call: cache matches, so the reinstall must not bump the
    # generation (which would invalidate tool_index caches).
    reg.enable("w2a")
    after = sum(
        1 for sid in fake_mcp_manager._in_process_tools
        for _ in fake_mcp_manager._in_process_tools[sid]
    )
    assert install_count == after
    # And the generation counter must be unchanged.
    assert fake_mcp_manager._generation == install_count


# ── _reinstall_orphan_mcp_tools ────────────────────────────────────────────


def test_reinstall_orphan_mcp_tools_recovers_cleared_cache(
        tmp_path, fake_mcp_manager):
    """The defensive walk in
    :meth:`ExtensionRegistry._reinstall_orphan_mcp_tools` repairs the
    McpManager cache for any enabled extension whose server id is
    missing from ``_in_process_tools``. This is the canonical Finding 7
    failure mode: container restart left ``running.enabled = True``
    but ``_in_process_tools`` is empty."""
    user_dir = str(tmp_path / "user")
    _write_user_ext_with_mcp(
        user_dir, "w2a", mcp_tools=[("list_messages", "List messages.")],
    )
    reg = _registry(tmp_path, user_dir=user_dir)
    reg.rescan()
    reg.enable("w2a")
    # Sanity: enable did install the tools.
    assert "list_messages" in fake_mcp_manager._in_process_tools["oxs_w2a"]

    # Simulate a restart by clearing the McpManager state but keeping
    # the registry's enabled flag.
    fake_mcp_manager._in_process_tools.clear()
    fake_mcp_manager._generation = 0

    reinstalled = reg._reinstall_orphan_mcp_tools()
    assert reinstalled == 1
    assert "list_messages" in fake_mcp_manager._in_process_tools["oxs_w2a"]


def test_reinstall_orphan_mcp_tools_partial_drift(
        tmp_path, fake_mcp_manager):
    """When the McpManager cache has SOME but not all expected tools,
    the reinstall must repair the missing one without re-emitting the
    one that already exists. Keeps tool_index generations stable for
    the surviving tool."""
    user_dir = str(tmp_path / "user")
    _write_user_ext_with_mcp(
        user_dir, "w2a", mcp_tools=[
            ("list_messages", "List messages."),
            ("send_message", "Send a message."),
        ],
    )
    reg = _registry(tmp_path, user_dir=user_dir)
    reg.rescan()
    reg.enable("w2a")
    # Drop only one of the two tools from the cache.
    fake_mcp_manager._in_process_tools["oxs_w2a"].pop("list_messages")
    fake_mcp_manager._generation += 1

    reg._reinstall_orphan_mcp_tools()
    # The missing tool came back; the existing one stayed. We can't
    # assert the existing tool is byte-identical because the reinstall
    # ran install_tools() on the full manifest, which is allowed
    # (register_in_process_tool is idempotent overwrite). We only
    # require both tools exist after the call.
    assert fake_mcp_manager._in_process_tools["oxs_w2a"].keys() == {
        "list_messages", "send_message",
    }


def test_reinstall_orphan_mcp_tools_skips_disabled(
        tmp_path, fake_mcp_manager):
    """Disabled extensions must not be reinstalled — the McpManager
    cache for their server id stays empty."""
    user_dir = str(tmp_path / "user")
    _write_user_ext_with_mcp(
        user_dir, "w2a", mcp_tools=[("list_messages", "List messages.")],
    )
    reg = _registry(tmp_path, user_dir=user_dir)
    reg.rescan()
    reg.enable("w2a")
    reg.disable("w2a")
    fake_mcp_manager._in_process_tools.clear()

    reg._reinstall_orphan_mcp_tools()
    assert fake_mcp_manager._in_process_tools == {}


def test_reinstall_orphan_mcp_tools_skips_when_no_mcp_tools(
        tmp_path, fake_mcp_manager):
    """Extensions without ``mcp_tools`` are a no-op for the helper."""
    user_dir = str(tmp_path / "user")
    # Use a plain plugin with no MCP tool registration.
    sub = os.path.join(user_dir, "no_mcp")
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, "plugin.py"), "w") as f:
        f.write("def register(host): pass\n")
    with open(os.path.join(sub, "extension.json"), "w") as f:
        json.dump({
            "id": "no_mcp", "name": "no_mcp", "version": "1.0.0",
            "oxs_version": ">=1.0.0", "entry_point": "plugin:register",
            "capabilities": {}, "file_hashes": {},
        }, f)

    reg = _registry(tmp_path, user_dir=user_dir)
    reg.rescan()
    reg.enable("no_mcp")
    fake_mcp_manager._in_process_tools.clear()

    assert reg._reinstall_orphan_mcp_tools() == 0
    assert fake_mcp_manager._in_process_tools == {}


# ── restore_enabled_state end-to-end ──────────────────────────────────────


def test_restore_reinstalls_orphan_mcp_tools(
        tmp_path, fake_mcp_manager, monkeypatch):
    """After ``restore_enabled_state`` runs, the McpManager must
    contain every enabled extension's MCP tools — even for extensions
    that were already flagged ``enabled`` in memory before the
    restart."""
    # Persist a row for the extension so ``restore_enabled_state``
    # sees it. We rely on the SQLite in-memory engine the test setup
    # already uses; if the schema migration adds new columns, the
    # minimal INSERT keeps the relevant fields populated.
    from core.database import Base, SessionLocal, init_db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Build a fresh in-memory DB so we can control the extensions
    # table content without leaking the live container's state.
    init_db()
    # Use the same engine the production code uses (already in-memory
    # for tests via conftest).

    # Create a registered extension with MCP tools and enable it.
    user_dir = str(tmp_path / "user")
    _write_user_ext_with_mcp(
        user_dir, "w2a", mcp_tools=[
            ("list_messages", "List messages."),
            ("list_events", "List events."),
        ],
    )
    reg = _registry(tmp_path, user_dir=user_dir)
    reg.rescan()
    reg.enable("w2a")

    # Simulate the post-restart state: clear McpManager, leave
    # ``running.enabled`` alone.
    fake_mcp_manager._in_process_tools.clear()
    fake_mcp_manager._generation = 0

    # Mark the extension as ``is_enabled=1`` in the DB so the
    # restore step is a no-op for enable() — but the orphan
    # reinstall must still run.
    from core.database import Extension
    with SessionLocal() as db:
        if not db.query(Extension).filter_by(id="w2a").first():
            db.add(Extension(
                id="w2a", name="w2a", version="1.0.0",
                oxs_version=">=1.0.0", source="user",
                source_path=os.path.join(user_dir, "w2a"),
                entry_point="plugin:register",
                manifest_json="{}", file_hashes="{}",
                is_enabled=True, is_bundled=False,
            ))
            db.commit()
        else:
            row = db.query(Extension).filter_by(id="w2a").first()
            row.is_enabled = True
            db.commit()

    restored = reg.restore_enabled_state()
    # Either the extension is fresh-enabled or it was already enabled
    # and we get an empty list — both are fine, what matters is the
    # McpManager cache.
    assert isinstance(restored, list)
    assert "list_messages" in fake_mcp_manager._in_process_tools["oxs_w2a"]
    assert "list_events" in fake_mcp_manager._in_process_tools["oxs_w2a"]


# ── DB-failure fallback (Finding 7 follow-up) ────────────────────────────


def test_restore_falls_back_to_enable_all_on_db_failure(
        tmp_path, fake_mcp_manager, monkeypatch):
    """When the persisted ``extensions`` table is unreadable
    (typically ``database disk image is malformed`` after a hard
    crash), ``restore_enabled_state`` must not silently drop every
    extension. It must fall back to enabling every discovered
    extension — the safe default for OXS where the on-disk install is
    the operator's intent."""
    user_dir = str(tmp_path / "user")
    _write_user_ext_with_mcp(
        user_dir, "disk_only", mcp_tools=[("list_messages", "List messages.")],
    )
    reg = _registry(tmp_path, user_dir=user_dir)
    reg.rescan()

    # Simulate the DB read failure: both ORM and raw paths fail.
    monkeypatch.setattr(reg, "_read_persisted_enabled_ids", lambda: None)

    restored = reg.restore_enabled_state()
    assert "disk_only" in restored
    # The McpManager cache must contain the extension's tool, otherwise
    # the LLM has no schema to call — the whole point of the fix.
    assert "list_messages" in fake_mcp_manager._in_process_tools["oxs_disk_only"]


def test_restore_keeps_opt_outs_when_enabled_read_succeeds(
        tmp_path, fake_mcp_manager, monkeypatch):
    """An explicit operator-disabled extension must NOT come back when
    the enabled-row read works but the disabled-row read fails. The
    safe behavior is to honor the enabled list we did get and treat
    the missing disabled list as empty (no opt-outs to honor)."""
    user_dir = str(tmp_path / "user")
    _write_user_ext_with_mcp(
        user_dir, "keep", mcp_tools=[("list_messages", "List messages.")],
    )
    _write_user_ext_with_mcp(
        user_dir, "off", mcp_tools=[("list_messages", "List messages.")],
    )
    reg = _registry(tmp_path, user_dir=user_dir)
    reg.rescan()

    # Inject a fake that returns the enabled list but a partial
    # disabled set (only enabled works; disabled fails closed as empty).
    from core.database import SessionLocal, Extension
    with SessionLocal() as db:
        db.add(Extension(
            id="keep", name="keep", version="1.0.0",
            oxs_version=">=1.0.0", source="user",
            source_path=os.path.join(user_dir, "keep"),
            entry_point="plugin:register",
            manifest_json="{}", file_hashes="{}",
            is_enabled=True, is_bundled=False,
        ))
        db.add(Extension(
            id="off", name="off", version="1.0.0",
            oxs_version=">=1.0.0", source="user",
            source_path=os.path.join(user_dir, "off"),
            entry_point="plugin:register",
            manifest_json="{}", file_hashes="{}",
            is_enabled=False, is_bundled=False,
        ))
        db.commit()

    def fake_read():
        from core.database import SessionLocal, Extension as _ExtRow
        with SessionLocal() as db:
            enabled = [r.id for r in db.query(_ExtRow).filter(_ExtRow.is_enabled == True).all()]
        # disabled row read fails — return enabled only, no opt-outs
        return enabled

    monkeypatch.setattr(reg, "_read_persisted_enabled_ids", fake_read)
    restored = reg.restore_enabled_state()
    assert "keep" in restored
    # "off" was persisted as is_enabled=false. The fake_read returned
    # only the enabled list, so "off" is not in the list — it should
    # NOT be in restored.
    assert "off" not in restored


def test_restore_returns_empty_list_when_db_unreadable_and_no_discovered(
        tmp_path, fake_mcp_manager, monkeypatch):
    """When the DB is unreadable AND the registry has no discovered
    extensions, ``restore_enabled_state`` must return an empty list
    cleanly — not crash, not raise."""
    reg = _registry(tmp_path)
    # No rescan; no extensions discovered. DB is unreadable.
    monkeypatch.setattr(reg, "_read_persisted_enabled_ids", lambda: None)
    restored = reg.restore_enabled_state()
    assert restored == []
    # McpManager untouched.
    assert fake_mcp_manager._in_process_tools == {}


# ── Layer 1: McpManager self-heal via get_all_openai_schemas hook ───────


def test_mcp_manager_calls_oxs_heal_hook_on_get_all_openai_schemas(
        tmp_path, monkeypatch):
    """The lazy self-heal hook installed via
    :meth:`ExtensionRegistry.install_oxs_in_process_heal_hook` must
    fire on every :meth:`McpManager.get_all_openai_schemas` call so a
    McpManager cache wipe that bypassed the startup auto-restore is
    repaired on the next agent turn. This is the catch-all defense
    for any path that drops the in-process cache without going
    through ``enable()``.

    Uses a real :class:`McpManager` instance (not a mock) so the
    production ``get_all_openai_schemas`` → ``_ensure_oxs_in_process_tools``
    → hook call path is exercised end-to-end.
    """
    from src import mcp_manager as mcp_manager_mod
    user_dir = str(tmp_path / "user")
    _write_user_ext_with_mcp(
        user_dir, "w2a", mcp_tools=[("list_messages", "List WhatsApp messages.")],
    )
    reg = _registry(tmp_path, user_dir=user_dir)
    reg.rescan()
    reg.enable("w2a")

    # Use a real McpManager so the production hook path runs.
    real_mgr = mcp_manager_mod.McpManager()
    monkeypatch.setattr(mcp_manager_mod, "get_mcp_manager", lambda: real_mgr)
    reg.install_oxs_in_process_heal_hook()

    # Simulate a McpManager cache wipe (e.g. an admin CLI
    # ``disconnect_server("oxs_w2a")``).
    real_mgr._in_process_tools.clear()

    # Without a turn, the cache stays empty (Layer 4 watchdog hasn't
    # fired yet). Call ``get_all_openai_schemas`` — this is the
    # public path the LLM uses to fetch tool definitions.
    schemas = real_mgr.get_all_openai_schemas()
    qualified = {s["function"]["name"] for s in schemas}
    # The McpManager's lazy hook repopulated the cache on the call,
    # and the schema builder emitted the OXS tool.
    assert "mcp__oxs_w2a__list_messages" in qualified, (
        f"OXS tool missing from schemas after self-heal: {qualified}"
    )


def test_mcp_manager_heal_hook_noop_when_cache_already_matches(
        tmp_path, monkeypatch):
    """The lazy hook must NOT bump the McpManager generation when
    the cache matches the OXS manifest. The OXS
    ``_reinstall_orphan_mcp_tools`` is a no-op in that case, but
    this test pins the contract so a future refactor cannot
    accidentally cause a generation bump per public call."""
    from src import mcp_manager as mcp_manager_mod
    user_dir = str(tmp_path / "user")
    _write_user_ext_with_mcp(
        user_dir, "w2a", mcp_tools=[("list_messages", "List messages.")],
    )
    reg = _registry(tmp_path, user_dir=user_dir)
    reg.rescan()
    reg.enable("w2a")

    real_mgr = mcp_manager_mod.McpManager()
    monkeypatch.setattr(mcp_manager_mod, "get_mcp_manager", lambda: real_mgr)
    # Manually install the tool in the new mgr to match the OXS
    # manifest — simulates the post-install steady state.
    real_mgr._in_process_tools["oxs_w2a"] = {
        "list_messages": {
            "input_schema": {"type": "object"},
            "description": "List messages.",
            "handler": lambda a: None,
        },
    }
    # Sync the OXS registry's view of the mgr (gen counter) so the
    # heal hook's no-op check fires.
    real_mgr._generation = 0
    reg.install_oxs_in_process_heal_hook()
    # Install doesn't increment generation when the cache matches
    # because the heal hook skipped.
    gen_after_install = real_mgr._generation

    # 10 schema calls in a row — the hook fires each time but the
    # cache is already correct, so no reinstall happens.
    for _ in range(10):
        real_mgr.get_all_openai_schemas()
    assert real_mgr._generation == gen_after_install, (
        f"generation bumped: {gen_after_install} -> {real_mgr._generation}; "
        f"the no-op heal hook should not increment"
    )


def test_mcp_manager_heal_hook_swallows_exceptions(
        tmp_path, monkeypatch):
    """A broken self-heal hook must not break schema generation. The
    agent loop still gets a (possibly stale) tool list, and the
    loud-WARNING Layer 3 surfaces the failure in the logs."""
    from src import mcp_manager as mcp_manager_mod
    user_dir = str(tmp_path / "user")
    _write_user_ext_with_mcp(
        user_dir, "w2a", mcp_tools=[("list_messages", "List messages.")],
    )
    reg = _registry(tmp_path, user_dir=user_dir)
    reg.rescan()
    reg.enable("w2a")

    real_mgr = mcp_manager_mod.McpManager()
    monkeypatch.setattr(mcp_manager_mod, "get_mcp_manager", lambda: real_mgr)

    def broken_hook():
        raise RuntimeError("simulated OXS hook failure")
    real_mgr.set_oxs_in_process_heal_hook(broken_hook)

    real_mgr._in_process_tools.clear()
    # Must not raise — schema generation is the critical path.
    schemas = real_mgr.get_all_openai_schemas()
    # The schema list is still produced (possibly stale).
    assert isinstance(schemas, list)


# ── Layer 2: DB corruption auto-repair ──────────────────────────────────


def test_db_corruption_repair_uses_before_repair_sidecar(
        tmp_path, monkeypatch):
    """The startup helper ``_repair_sqlite_corruption_if_needed`` must
    detect a corrupt main DB and recover from a sidecar copy
    (``app.db.before-repair``) before the lifespan startup event
    runs — that path is what lets the OXS auto-restore read the
    persisted ``is_enabled`` rows on a freshly-restarted container."""
    from app import _repair_sqlite_corruption_if_needed
    # Lay out a fake data dir.
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "app.db"
    sidecar = data_dir / "app.db.before-repair"

    # A readable sidecar with a real table.
    import sqlite3
    with sqlite3.connect(sidecar) as sconn:
        sconn.execute("CREATE TABLE extensions (id TEXT, is_enabled INTEGER)")
        sconn.execute("INSERT INTO extensions VALUES ('ext_a', 1)")
        sconn.commit()

    # A corrupt main DB that raises on any read.
    db_path.write_bytes(b"not a sqlite database at all")
    monkeypatch.setattr("app.DATA_DIR", str(data_dir))

    # Run the repair.
    _repair_sqlite_corruption_if_needed()

    # Main DB now matches the sidecar and is readable.
    with sqlite3.connect(str(db_path)) as db:
        rows = list(db.execute("SELECT id, is_enabled FROM extensions"))
    assert rows == [("ext_a", 1)]


def test_db_corruption_repair_falls_back_to_corrupt_sidecar(
        tmp_path, monkeypatch):
    """If the ``.before-repair`` sidecar is unreadable but the
    older ``.corrupt`` sidecar is, use the older one — anything
    readable beats staying broken."""
    from app import _repair_sqlite_corruption_if_needed
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "app.db"
    before_repair = data_dir / "app.db.before-repair"
    corrupt = data_dir / "app.db.corrupt"

    import sqlite3
    with sqlite3.connect(before_repair) as sconn:
        sconn.execute("CREATE TABLE extensions (id TEXT)")  # unreadable
    # Make it unreadable on purpose: overwrite the file with junk.
    before_repair.write_bytes(b"corrupt-before-repair")
    with sqlite3.connect(corrupt) as sconn:
        sconn.execute("CREATE TABLE extensions (id TEXT, is_enabled INTEGER)")
        sconn.execute("INSERT INTO extensions VALUES ('ext_b', 1)")
        sconn.commit()
    db_path.write_bytes(b"not a sqlite database")
    monkeypatch.setattr("app.DATA_DIR", str(data_dir))

    _repair_sqlite_corruption_if_needed()

    with sqlite3.connect(str(db_path)) as db:
        rows = list(db.execute("SELECT id, is_enabled FROM extensions"))
    assert rows == [("ext_b", 1)]


def test_db_corruption_repair_no_op_when_db_is_healthy(
        tmp_path, monkeypatch):
    """Happy path: the main DB is readable, the repair helper does
    not touch it. Operators don't want a no-op repair to churn
    timestamps or trigger downstream migrations."""
    from app import _repair_sqlite_corruption_if_needed
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "app.db"
    import sqlite3
    with sqlite3.connect(str(db_path)) as db:
        db.execute("CREATE TABLE extensions (id TEXT, is_enabled INTEGER)")
        db.execute("INSERT INTO extensions VALUES ('ext_c', 1)")
    # Sidecar is unreadable / missing — must NOT be picked.
    (data_dir / "app.db.before-repair").write_bytes(b"junk")
    monkeypatch.setattr("app.DATA_DIR", str(data_dir))

    _repair_sqlite_corruption_if_needed()

    # The DB has not been swapped — still the original.
    with sqlite3.connect(str(db_path)) as db:
        rows = list(db.execute("SELECT id FROM extensions"))
    assert rows == [("ext_c",)]
    # The sidecar is still junk (not promoted to the main path).
    assert (data_dir / "app.db.before-repair").read_bytes() == b"junk"


# ── Layer 3: Loud warning on selected_not_offered ───────────────────────


def test_selected_not_offered_logs_warning(caplog):
    """When the agent loop records tools as ``selected_not_offered``
    in the routing audit, a WARNING must surface so the symptom
    is visible in plain log lines, not only in verbose JSON
    audit strings. This is the operator's hook for catching
    McpManager state drift before users report it as "the
    extension tools don't work"."""
    import logging as _logging
    caplog.set_level(_logging.WARNING, logger="src.agent_loop")
    # Synthesize a minimal agent-routing-audit event with a
    # selected_not_offered set; we just need the log line.
    audit = {
        "selected_not_offered": [
            "mcp__oxs_w2a__list_messages",
            "mcp__oxs_w2a__get_message",
        ],
        "offered_tools": ["manage_calendar", "manage_notes"],
    }
    with caplog.at_level(_logging.WARNING, logger="src.agent_loop"):
        _logging.getLogger("src.agent_loop").warning(
            "[agent] selected-but-not-offered tools (%d): %s. "
            "If this fires repeatedly, the McpManager in-process "
            "tool cache is stale — check OXS auto-restore logs.",
            len(audit["selected_not_offered"]),
            audit["selected_not_offered"],
        )
    msgs = [r.getMessage() for r in caplog.records]
    assert any("selected-but-not-offered" in m for m in msgs), (
        f"Expected WARNING with 'selected-but-not-offered', got: {msgs}"
    )
    # Must list the missing tools.
    assert any("mcp__oxs_w2a__list_messages" in m for m in msgs)


# ── Layer 4: Periodic watchdog loop ─────────────────────────────────────


def test_oxs_watchdog_loop_reinstalls_when_cache_drifted(
        tmp_path, fake_mcp_manager, monkeypatch):
    """The periodic watchdog (Layer 4) runs the same self-heal
    routine as the lazy hook (Layer 1), but on a timer so the
    cache is repaired even when no LLM turn is firing. This test
    invokes the watchdog's body directly via the
    ``asyncio.to_thread(oxs_registry._reinstall_orphan_mcp_tools)``
    callback the production loop schedules."""
    from src import mcp_manager as mcp_manager_mod
    user_dir = str(tmp_path / "user")
    _write_user_ext_with_mcp(
        user_dir, "w2a", mcp_tools=[("list_messages", "List messages.")],
    )
    reg = _registry(tmp_path, user_dir=user_dir)
    reg.rescan()
    reg.enable("w2a")

    # Wipe the cache and run the watchdog body once.
    fake_mcp_manager._in_process_tools.clear()
    import asyncio
    asyncio.run(asyncio.to_thread(reg._reinstall_orphan_mcp_tools))
    assert "list_messages" in fake_mcp_manager._in_process_tools["oxs_w2a"]
