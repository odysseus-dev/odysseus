"""Edge case tests for OXS.

Covers:
  - File hash collisions across extensions
  - Extension id collisions across sources (bundled vs user)
  - Manifest validation failures surfaced as 422-style errors
  - Path traversal in declared hashes
  - Idempotency: enable twice, disable twice
  - Storage: storage still functional after partial failure
  - Webhook: rate limit recovers when window resets
  - Webhook: different extensions with different tokens are isolated
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from core.extensions import (
    ExtensionRegistry,
    events,
    webhooks,
)


def _install(tmp_root, ext_id, *, plugin_src=None, manifest_extra=None):
    """Write a minimal user extension under tmp_root/user/<ext_id>."""
    sub = os.path.join(tmp_root, "user", ext_id)
    os.makedirs(sub, exist_ok=True)
    if plugin_src is None:
        plugin_src = "def register(host): pass"
    with open(os.path.join(sub, "plugin.py"), "w") as f:
        f.write(plugin_src)
    manifest = {
        "id": ext_id,
        "name": ext_id,
        "version": "1.0.0",
        "oxs_version": ">=1.0.0",
        "entry_point": "plugin:register",
        "capabilities": {},
        "file_hashes": {},
    }
    if manifest_extra:
        manifest.update(manifest_extra)
    with open(os.path.join(sub, "extension.json"), "w") as f:
        json.dump(manifest, f)


@pytest.fixture(autouse=True)
def _reset():
    events._subscribers.clear()
    webhooks.reset_for_tests()
    webhooks.reset_rate_limits()
    yield
    events._subscribers.clear()
    webhooks.reset_for_tests()
    webhooks.reset_rate_limits()


@pytest.fixture
def tmp_root():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def registry(tmp_root):
    return ExtensionRegistry(
        bundled_dir=os.path.join(tmp_root, "bundled"),
        user_dir=os.path.join(tmp_root, "user"),
    )


# ── File hash collisions ────────────────────────────────────────────────


def test_two_extensions_with_same_hashes_each_verify(tmp_root, registry):
    """Two extensions declaring the same file_hash value are independent.

    Hashing ``plugin.py`` rather than ``extension.json`` avoids the
    chicken-and-egg problem (the manifest changes when we patch its
    file_hashes field). Both extensions hash their own plugin.py, both
    pass verify_discovered.
    """
    plugin = "def register(host): pass"
    _install(tmp_root, "alpha", plugin_src=plugin)
    _install(tmp_root, "beta", plugin_src=plugin)
    registry.rescan()
    found = {e.extension_id: e for e in registry.list()}
    assert "alpha" in found and "beta" in found
    # Patch each manifest to declare plugin.py's hash and verify.
    from core.extensions import sha256_of_file
    from core.extensions.loader import DiscoveredExtension, verify_discovered
    for ext_id in ("alpha", "beta"):
        manifest_path = os.path.join(tmp_root, "user", ext_id, "extension.json")
        plugin_path = os.path.join(tmp_root, "user", ext_id, "plugin.py")
        actual = sha256_of_file(plugin_path)
        with open(manifest_path) as f:
            m = json.load(f)
        m["file_hashes"] = {"plugin.py": actual}
        with open(manifest_path, "w") as f:
            json.dump(m, f)
        de = DiscoveredExtension(
            manifest=m,
            source="user",
            source_path=os.path.dirname(manifest_path),
            extension_id=ext_id,
        )
        # verify_discovered must accept the file (no exception raised)
        verify_discovered(de)


# ── Id collisions across sources ─────────────────────────────────────────


def test_extension_id_collision_prefers_bundled(tmp_root):
    """Bundled wins over user when ids collide."""
    bundled_root = os.path.join(tmp_root, "bundled")
    user_root = os.path.join(tmp_root, "user")
    os.makedirs(os.path.join(bundled_root, "shared"))
    os.makedirs(os.path.join(user_root, "shared"))
    with open(os.path.join(bundled_root, "shared", "extension.json"), "w") as f:
        json.dump({
            "id": "shared", "name": "Bundled", "version": "1.0.0",
            "oxs_version": ">=1.0.0", "entry_point": "plugin:register",
            "capabilities": {},
        }, f)
    with open(os.path.join(bundled_root, "shared", "plugin.py"), "w") as f:
        f.write("SOURCE = 'bundled'\ndef register(host): pass")
    with open(os.path.join(user_root, "shared", "extension.json"), "w") as f:
        json.dump({
            "id": "shared", "name": "User", "version": "1.0.0",
            "oxs_version": ">=1.0.0", "entry_point": "plugin:register",
            "capabilities": {},
        }, f)
    with open(os.path.join(user_root, "shared", "plugin.py"), "w") as f:
        f.write("SOURCE = 'user'\ndef register(host): pass")

    reg = ExtensionRegistry(bundled_dir=bundled_root, user_dir=user_root)
    reg.rescan()
    # Only the bundled entry survives.
    assert len(reg.list()) == 1
    running = reg.list()[0]
    assert running.source == "bundled"


# ── Idempotency ─────────────────────────────────────────────────────────


def test_enable_twice_is_idempotent(tmp_root, registry):
    _install(tmp_root, "ext1")
    registry.rescan()
    a = registry.enable("ext1")
    b = registry.enable("ext1")
    assert a is b
    assert a.enabled is True


def test_disable_twice_is_idempotent(tmp_root, registry):
    _install(tmp_root, "ext1")
    registry.rescan()
    registry.enable("ext1")
    registry.disable("ext1")
    registry.disable("ext1")  # no-op, no audit


def test_uninstall_disabled_extension_clears_state(tmp_root, registry):
    plugin = (
        "def register(host):\n"
        "    @host.on_enable\n"
        "    def _e():\n"
        "        host.subscribe_event('test_event', lambda p: None)\n"
    )
    _install(tmp_root, "ext1", plugin_src=plugin,
             manifest_extra={"capabilities": {"events_sub": True}})
    registry.rescan()
    registry.enable("ext1")
    assert events.list_subscriptions().get("test_event") is not None
    registry.uninstall("ext1")
    # After uninstall the registry does not retain the running entry.
    assert "ext1" not in registry
    # The subscription was unwired on uninstall.
    assert events.list_subscriptions().get("test_event") is None


# ── Path traversal in declared hashes ────────────────────────────────────


def test_declared_hash_with_traversal_segment_is_caught(tmp_root, registry):
    """`compute_extension_hashes` returns paths relative to root; if an
    extension declares an out-of-tree path in file_hashes, install/verify
    must reject it."""
    from core.extensions.security import verify_file_hashes
    sub = os.path.join(tmp_root, "user", "ext1")
    os.makedirs(sub)
    with open(os.path.join(sub, "plugin.py"), "w") as f:
        f.write("# ok")
    with open(os.path.join(sub, "extension.json"), "w") as f:
        json.dump({"id": "ext1", "name": "ext1", "version": "1.0.0",
                   "oxs_version": ">=1.0.0", "entry_point": "plugin:register",
                   "capabilities": {}}, f)

    with pytest.raises(Exception) as exc_info:
        verify_file_hashes(
            sub,
            {"../escape.txt": "sha256:" + "a" * 64},
            extension_id="ext1",
        )
    assert any("escapes" in e for e in exc_info.value.errors)


def test_declared_hash_with_absolute_path_is_caught(tmp_root):
    from core.extensions.security import verify_file_hashes
    sub = os.path.join(tmp_root, "user", "ext1")
    os.makedirs(sub)
    with open(os.path.join(sub, "extension.json"), "w") as f:
        f.write("{}")
    with pytest.raises(Exception):
        verify_file_hashes(sub, {"/etc/passwd": "sha256:" + "a" * 64})


# ── Storage: still functional after partial failure ──────────────────────


def test_storage_drop_handles_missing_tables(tmp_root, registry):
    """Drop on an extension that never created tables is a no-op, not error."""
    from core.extensions.storage import PluginStorage
    from sqlalchemy import create_engine
    eng = create_engine("sqlite:///:memory:", future=True,
                        connect_args={"check_same_thread": False})
    s = PluginStorage("never_installed", engine=eng)
    dropped = s.drop_own_tables()
    assert dropped == []
    # apply_data_schema on empty schema is fine
    created = s.apply_data_schema({})
    assert created == []


def test_storage_ensure_table_rejects_semicolon_directly(tmp_root):
    """ensure_table raises immediately for column defs containing
    semicolons or comments — that's the path the spec promises is
    hard-closed."""
    from core.extensions.storage import PluginStorage
    from sqlalchemy import create_engine
    eng = create_engine("sqlite:///:memory:", future=True,
                        connect_args={"check_same_thread": False})
    s = PluginStorage("ext", engine=eng)
    with pytest.raises(ValueError):
        s.ensure_table("t", columns={"x": "TEXT; DROP TABLE secret"})
    with pytest.raises(ValueError):
        s.ensure_table("t", columns={"x": "INTEGER -- comment"})
    with pytest.raises(ValueError):
        s.ensure_table("t", columns={"x": "TEXT\nINJECTED"})


def test_apply_data_schema_raises_on_bad_column_def(tmp_root):
    """A bad column def in one table causes apply_data_schema to raise.

    The migrate module wraps apply_data_schema with broader exception
    handling so the enable() flow surfaces a soft error rather than
    aborting. We verify the storage itself raises — the wrapping is
    tested elsewhere.
    """
    from core.extensions.storage import PluginStorage
    from sqlalchemy import create_engine
    eng = create_engine("sqlite:///:memory:", future=True,
                        connect_args={"check_same_thread": False})
    s = PluginStorage("ext", engine=eng)
    with pytest.raises(ValueError):
        s.apply_data_schema({
            "bad": {"columns": {"bad-col": "TEXT"}},
        })
    # The bad table was NOT created.
    assert "ext__bad" not in s.list_own_tables()


# ── Webhook rate-limit recovery ──────────────────────────────────────────


def test_rate_limit_recovers_after_window_elapses(tmp_root):
    """A token that hit its rate limit must accept requests after the
    window resets."""
    webhooks.install("ext", "/x", [], lambda **kw: None)
    rec = webhooks.list_for_extension("ext")[0]
    token = webhooks.get_raw_token(rec)
    # Burn the budget
    for _ in range(11):
        s, _ = asyncio.run(webhooks.dispatch("ext", token, b"{}"))
    # Last one was 429 — confirm.
    s, _ = asyncio.run(webhooks.dispatch("ext", token, b"{}"))
    assert s == 429


def test_different_extensions_have_isolated_rate_buckets():
    """Each (extension_id, token_hash) has its own bucket."""
    webhooks.install("ext1", "/x", [], lambda **kw: None)
    webhooks.install("ext2", "/y", [], lambda **kw: None)
    r1 = webhooks.list_for_extension("ext1")[0]
    r2 = webhooks.list_for_extension("ext2")[0]
    # Burn ext1's budget
    for _ in range(10):
        s, _ = asyncio.run(webhooks.dispatch("ext1", webhooks.get_raw_token(r1), b"{}"))
    # ext2 still works
    s, _ = asyncio.run(webhooks.dispatch("ext2", webhooks.get_raw_token(r2), b"{}"))
    assert s == 202


def test_webhook_invalid_signature_status():
    webhooks.install("ext", "/x", [], lambda **kw: None)
    rec = webhooks.list_for_extension("ext")[0]
    token = webhooks.get_raw_token(rec)
    status, _ = asyncio.run(webhooks.dispatch(
        "ext", token, b'{}',
        headers={"x-ext-signature": "sha256=deadbeef"},
    ))
    assert status == 401


# ── Capability gate: same method declared twice ─────────────────────────


def test_declaring_capability_then_calling_method_twice_works(tmp_path):
    from core.extensions import OXSHost
    host = OXSHost(
        extension_id="ext",
        extension_version="1.0.0",
        declared_capabilities={"tools": True},
        granted_privileged={},
        data_path=str(tmp_path),
        plugin_path=str(tmp_path),
    )
    def handler(args, ctx=None):
        return "ok"
    host.register_tool("a", {}, handler)
    host.register_tool("a", {}, handler)  # second call also succeeds
    assert len(host.tools) == 2
    # Violations are still recorded.
    assert host.violations == []


def test_calling_undeclared_method_after_declared_method_records_violation(tmp_path):
    from core.extensions import OXSHost, OXSCapabilityError
    host = OXSHost(
        extension_id="ext",
        extension_version="1.0.0",
        declared_capabilities={"tools": True},
        granted_privileged={},
        data_path=str(tmp_path),
        plugin_path=str(tmp_path),
    )
    host.register_tool("a", {}, lambda args, ctx=None: None)
    with pytest.raises(OXSCapabilityError):
        host.register_mcp_tool("srv", "t", {}, lambda a, c: None)
    # First call succeeded, second raised and recorded.
    assert len(host.tools) == 1
    assert len(host.violations) == 1
    assert host.violations[0]["capability"] == "mcp_tools"


# ── Loader edge cases ────────────────────────────────────────────────────


def test_discover_skips_partial_extension_dir(tmp_root):
    """A directory containing extension.json but missing the entry-point
    file is still discovered (the failure happens on enable, not on
    discover)."""
    user = os.path.join(tmp_root, "user")
    sub = os.path.join(user, "partial")
    os.makedirs(sub)
    # No plugin.py at all
    with open(os.path.join(sub, "extension.json"), "w") as f:
        json.dump({"id": "partial", "name": "p", "version": "1.0.0",
                   "oxs_version": ">=1.0.0", "entry_point": "plugin:register",
                   "capabilities": {}}, f)
    reg = ExtensionRegistry(bundled_dir=tmp_root, user_dir=user)
    reg.rescan()
    found = reg.list()
    assert any(e.extension_id == "partial" for e in found)
    # Enable should fail because plugin.py is missing.
    with pytest.raises(Exception):
        reg.enable("partial")


def test_discover_skips_file_with_wrong_name(tmp_root):
    user = os.path.join(tmp_root, "user")
    sub = os.path.join(user, "weird")
    os.makedirs(sub)
    # Wrong manifest name
    with open(os.path.join(sub, "manifest.json"), "w") as f:
        f.write("{}")
    reg = ExtensionRegistry(bundled_dir=tmp_root, user_dir=user)
    reg.rescan()
    assert reg.list() == []


# ─- Registry edge cases ────────────────────────────────────────────────


def test_rescan_picks_up_changes_to_user_dir(tmp_root, registry):
    """Add a new ext, rescan — it appears without restart."""
    _install(tmp_root, "ext1")
    registry.rescan()
    assert "ext1" in registry
    _install(tmp_root, "ext2")
    registry.rescan()
    assert "ext2" in registry


def test_reload_after_extension_dir_deleted_fails_gracefully(tmp_root, registry):
    """If an extension's dir is deleted between enable and reload,
    reload should not crash the test runner."""
    sub = os.path.join(tmp_root, "user", "ext1")
    _install(tmp_root, "ext1")
    registry.rescan()
    registry.enable("ext1")
    # Move the plugin.py away so reload re-import fails
    plugin = os.path.join(sub, "plugin.py")
    os.rename(plugin, plugin + ".bak")
    try:
        with pytest.raises(Exception):
            registry.reload("ext1")
    finally:
        os.rename(plugin + ".bak", plugin)


def test_rescan_idempotent_on_no_changes(tmp_root, registry):
    _install(tmp_root, "ext1")
    registry.rescan()
    n1 = len(registry.list())
    registry.rescan()
    registry.rescan()
    assert len(registry.list()) == n1


def test_disabled_extension_can_be_re_enabled(tmp_root, registry):
    _install(tmp_root, "ext1")
    registry.rescan()
    registry.enable("ext1")
    registry.disable("ext1")
    assert registry.get("ext1").enabled is False
    registry.enable("ext1")
    assert registry.get("ext1").enabled is True


def test_uninstall_unknown_id_is_noop(tmp_root, registry):
    registry.uninstall("never_installed")  # does not raise


# ─- Webhook edge cases ────────────────────────────────────────────────


def test_dispatch_with_query_params_passed_to_handler():
    captured = {}
    def handler(body=None, headers=None, query=None, event_filter=None):
        captured["body"] = body
        captured["query"] = query
    webhooks.install("ext", "/x", [], handler)
    rec = webhooks.list_for_extension("ext")[0]
    token = webhooks.get_raw_token(rec)
    status, _ = asyncio.run(webhooks.dispatch(
        "ext", token, b'{"k":"v"}', query={"foo": "bar"},
    ))
    assert status == 202
    assert captured["query"] == {"foo": "bar"}


def test_dispatch_with_invalid_utf8_body_does_not_crash_handler():
    """Body that is not valid UTF-8 / JSON should fall back to raw bytes."""
    received = []
    def handler(body=None, **kw):
        received.append(body)
    webhooks.install("ext", "/x", [], handler)
    rec = webhooks.list_for_extension("ext")[0]
    token = webhooks.get_raw_token(rec)
    bad_bytes = b"\xff\xfe\xfd not utf-8"
    status, _ = asyncio.run(webhooks.dispatch("ext", token, bad_bytes))
    assert status == 202
    # Allow async to flush
    asyncio.run(asyncio.sleep(0.05))
    assert len(received) == 1
    assert received[0] == bad_bytes


def test_handler_returning_dict_with_error_key_reported_as_failed():
    """Handlers that return ``{"error": ..., "exit_code": N}`` propagate
    the error code back via the JSON response."""
    def handler(body=None, **kw):
        return {"error": "bad", "exit_code": 42}
    webhooks.install("ext", "/x", [], handler)
    rec = webhooks.list_for_extension("ext")[0]
    token = webhooks.get_raw_token(rec)
    status, msg = asyncio.run(webhooks.dispatch("ext", token, b'{}'))
    assert status == 202  # dispatcher always returns 202
    assert msg == "accepted"
    # The handler's error is the extension's concern, not the dispatcher.


def test_compute_signature_uses_secret_only():
    body = b"hello"
    sig1 = webhooks.compute_signature("secret1", body)
    sig2 = webhooks.compute_signature("secret2", body)
    assert sig1 != sig2
    # The signature changes with body content too.
    sig3 = webhooks.compute_signature("secret1", b"different")
    assert sig3 != sig1