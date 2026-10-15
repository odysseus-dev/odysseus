"""Tests for OXS capability gates (OXSHost, decorators)."""

from __future__ import annotations

import pytest

from core.extensions import (
    OXSCapabilityError,
    OXSHost,
    require_capability,
    require_privileged,
)


def _make_host(
    declared: dict[str, bool] | None = None,
    granted_priv: dict[str, bool] | None = None,
    ext_id: str = "test",
    tmp_path=None,
) -> OXSHost:
    return OXSHost(
        extension_id=ext_id,
        extension_version="1.0.0",
        declared_capabilities=declared or {},
        granted_privileged=granted_priv or {},
        data_path=str(tmp_path) if tmp_path else "/tmp",
        plugin_path=str(tmp_path) if tmp_path else "/tmp",
    )


# ── Safe capability registration ──────────────────────────────────────────


def test_register_tool_records_when_declared(tmp_path):
    host = _make_host(declared={"tools": True}, tmp_path=tmp_path)
    host.register_tool("hello", {"type": "object"}, lambda a, c: "hi")
    assert len(host.tools) == 1
    assert host.tools[0].name == "hello"


def test_register_tool_raises_when_undeclared(tmp_path):
    host = _make_host(declared={}, tmp_path=tmp_path)
    with pytest.raises(OXSCapabilityError) as exc:
        host.register_tool("hello", {"type": "object"}, lambda a, c: "hi")
    assert exc.value.capability == "tools"
    assert exc.value.method == "register_tool"


def test_register_tool_raises_when_declared_false(tmp_path):
    """Even with the key present, False means deny."""
    host = _make_host(declared={"tools": False}, tmp_path=tmp_path)
    with pytest.raises(OXSCapabilityError):
        host.register_tool("hello", {"type": "object"}, lambda a, c: "hi")


def test_add_route_records_with_auth(tmp_path):
    host = _make_host(declared={"routes": True}, tmp_path=tmp_path)
    host.add_route("/api/ext/x/y", lambda: "ok", auth="user")
    assert len(host.routes) == 1
    assert host.routes[0].auth == "user"


def test_add_route_rejects_bad_auth_value(tmp_path):
    host = _make_host(declared={"routes": True}, tmp_path=tmp_path)
    with pytest.raises(ValueError):
        host.add_route("/x", lambda: None, auth="nonsense")


def test_add_route_default_methods_is_get(tmp_path):
    host = _make_host(declared={"routes": True}, tmp_path=tmp_path)
    host.add_route("/x", lambda: None)
    assert host.routes[0].methods == ("GET",)


def test_add_static_records_url_prefix(tmp_path):
    host = _make_host(declared={"static": True}, tmp_path=tmp_path)
    host.add_static("/ext/x/", "ui")
    assert host.statics[0].url_prefix == "/ext/x/"
    assert host.statics[0].directory == "ui"


def test_subscribe_event_records(tmp_path):
    host = _make_host(declared={"events_sub": True}, tmp_path=tmp_path)
    handler = lambda p: None
    host.subscribe_event("oxs.rescan", handler)
    assert len(host.event_subs) == 1
    assert host.event_subs[0].event_name == "oxs.rescan"


def test_fire_event_records_during_enable(tmp_path):
    """Events fired during on_enable are recorded, not dispatched yet.

    PR 3 wires the dispatch; PR 2 only records so the registry can fire
    after the hook chain completes."""
    host = _make_host(declared={"events_fire": True}, tmp_path=tmp_path)
    host.fire_event("hello.greet", {"k": "v"})
    assert len(host.event_fires_during_enable) == 1
    name, payload, owner = host.event_fires_during_enable[0]
    assert name == "hello.greet"
    assert payload == {"k": "v"}


# ── Privileged gates ───────────────────────────────────────────────────────


def test_admin_route_requires_privileged_grant(tmp_path):
    host = _make_host(declared={"routes": True}, granted_priv={}, tmp_path=tmp_path)
    with pytest.raises(OXSCapabilityError) as exc:
        host.add_admin_route("/api/ext/x/admin", lambda: None)
    assert exc.value.capability == "admin_routes"


def test_admin_route_succeeds_with_grant(tmp_path):
    host = _make_host(declared={"routes": True}, granted_priv={"admin_routes": True}, tmp_path=tmp_path)
    host.add_admin_route("/api/ext/x/admin", lambda: None)
    assert host.routes[0].auth == "admin"


def test_priv_grant_alone_does_not_imply_declared(tmp_path):
    """Operator granted ``admin_routes`` but extension did not declare ``routes``.

    In practice the manifest parser would have already rejected this, but
    the host should still enforce: privileged routes also need the
    underlying safe capability. Both gates must be satisfied."""
    host = _make_host(
        declared={},
        granted_priv={"admin_routes": True},
        tmp_path=tmp_path,
    )
    with pytest.raises(OXSCapabilityError):
        host.add_admin_route("/api/ext/x/admin", lambda: None)


# ── Decorators ─────────────────────────────────────────────────────────────


def test_require_capability_decorator():
    """Sanity-check that the decorator mechanism produces a callable wrapper."""

    class FakeHost:
        def __init__(self):
            self.extension_id = "fake"
            self._declared_capabilities = {"tools": True}
            self._granted_privileged = {}
            self.violations = []

        def _record_violation(self, cap, method):
            self.violations.append((cap, method))

    @require_capability("tools")
    def do_thing(host):
        return "did it"

    h = FakeHost()
    assert do_thing(h) == "did it"


def test_require_capability_decorator_blocks_undeclared():
    class FakeHost:
        def __init__(self):
            self.extension_id = "fake"
            self._declared_capabilities = {}
            self._granted_privileged = {}
            self.violations = []

        def _record_violation(self, cap, method):
            self.violations.append((cap, method))

    @require_capability("tools")
    def do_thing(host):
        return "should not reach"

    h = FakeHost()
    with pytest.raises(OXSCapabilityError):
        do_thing(h)
    assert h.violations == [("tools", "do_thing")]


def test_require_privileged_decorator_blocks_when_no_grant():
    class FakeHost:
        def __init__(self):
            self.extension_id = "fake"
            self._declared_capabilities = {}
            self._granted_privileged = {}
            self.violations = []

        def _record_violation(self, cap, method):
            self.violations.append((cap, method))

    @require_privileged("shell_command")
    def do_thing(host):
        return "should not reach"

    h = FakeHost()
    with pytest.raises(OXSCapabilityError):
        do_thing(h)
    assert ("shell_command", "do_thing") in h.violations


# ── Audit hook ─────────────────────────────────────────────────────────────


def test_capability_violation_audited(tmp_path, monkeypatch):
    """Capability violation should append an audit entry."""
    import core.extensions.audit as audit_mod
    log_path = tmp_path / "audit.log"
    monkeypatch.setattr(audit_mod, "OXS_AUDIT_LOG", str(log_path))
    monkeypatch.setattr(audit_mod, "_DEFAULT_SINK",
                        audit_mod.AuditSink(log_path=str(log_path),
                                            db_session_factory=None))

    host = _make_host(declared={}, tmp_path=tmp_path)
    with pytest.raises(OXSCapabilityError):
        host.register_tool("x", {}, lambda a, c: None)

    events = audit_mod.audit_tail(10)
    violation_events = [e for e in events if e["event"] == "extension.capability.violation"]
    assert len(violation_events) >= 1
    assert violation_events[0]["extension_id"] == "test"
    assert violation_events[0]["payload"]["capability"] == "tools"


# ── Identity ───────────────────────────────────────────────────────────────


def test_host_identity_properties(tmp_path):
    host = _make_host(ext_id="my-ext", tmp_path=tmp_path)
    assert host.id == "my-ext"
    assert host.version == "1.0.0"


def test_host_logger_is_scoped(tmp_path):
    host = _make_host(ext_id="my-ext", tmp_path=tmp_path)
    logger = host.get_logger()
    assert logger.name == "oxs.my-ext"


def test_host_paths(tmp_path):
    host = _make_host(tmp_path=tmp_path)
    assert host.get_plugin_path() == str(tmp_path)
    assert host.get_data_path() == str(tmp_path)
    assert host.get_data_path("sub") == str(tmp_path / "sub")


def test_settings_namespace_get_set(tmp_path):
    host = _make_host(tmp_path=tmp_path)
    host.settings.set("token", "abc")
    assert host.settings.get("token") == "abc"
    assert host.settings.get("missing", "default") == "default"


# ── Edge cases ─────────────────────────────────────────────────────────────


def test_invalid_route_auth_value_does_not_consume_violation(tmp_path):
    """A ValueError raised before the gate is NOT a capability violation.

    Currently add_route validates ``auth`` BEFORE the gate. So a bad auth
    value raises ValueError, not OXSCapabilityError. Document that here.
    """
    host = _make_host(declared={"routes": True}, tmp_path=tmp_path)
    with pytest.raises(ValueError):
        host.add_route("/x", lambda: None, auth="invalid")
    assert host.violations == []


def test_multiple_violations_accumulate(tmp_path, monkeypatch):
    """Each violation should be recorded separately."""
    import core.extensions.audit as audit_mod
    log_path = tmp_path / "audit.log"
    monkeypatch.setattr(audit_mod, "OXS_AUDIT_LOG", str(log_path))
    monkeypatch.setattr(audit_mod, "_DEFAULT_SINK",
                        audit_mod.AuditSink(log_path=str(log_path),
                                            db_session_factory=None))
    host = _make_host(declared={}, tmp_path=tmp_path)

    for i in range(5):
        with pytest.raises(OXSCapabilityError):
            host.register_tool(f"t{i}", {}, lambda a, c: None)
    assert len(host.violations) == 5