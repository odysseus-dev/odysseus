# OXS Implementation Status

Status: complete (PR 1 + 2 + 3 + 4 + 5). All planned features shipped.

## Tests

```
tests/test_oxs_manifest.py           38 ✓   JSON Schema validation
tests/test_oxs_security.py           35 ✓   SHA-256, AST, zip-slip
tests/test_oxs_audit.py              13 ✓   Append-only log + DB mirror
tests/test_oxs_capabilities.py        26 ✓   Capability gates
tests/test_oxs_lifecycle.py           11 ✓   Hooks + decorator
tests/test_oxs_loader.py              14 ✓   Discovery (bundled, user, entry-points)
tests/test_oxs_registry.py            18 ✓   Install / enable / disable / reload / uninstall
tests/test_oxs_storage.py             30 ✓   PluginStorage + table isolation
tests/test_oxs_events.py               8 ✓   Event subscriber registry
tests/test_oxs_task_actions.py        10 ✓   BUILTIN_ACTIONS wiring
tests/test_oxs_mcp_bridge.py          10 ✓   In-process MCP tool registration
tests/test_oxs_ui_mount.py            15 ✓   StaticFiles mount/unmount
tests/test_oxs_webhooks.py            25 ✓   Token + HMAC + rate-limit + dispatch
tests/test_oxs_migrate.py             10 ✓   Schema migration runner
tests/test_oxs_registry_integration   11 ✓   End-to-end enable/disable cycle
tests/test_oxs_api.py                 16 ✓   Admin REST surface
tests/test_oxs_edge_cases.py          26 ✓   Race / collision / overflow
─────────────────────────────────────────────────────
TOTAL                                316  (340 incl. parametrized variants)
```

## Coverage (core/extensions only)

| Module | Coverage |
|---|---|
| `__init__.py` | 100% |
| `audit.py` | 84% (DB-mirror exception paths uncovered) |
| `events.py` | 100% |
| `host.py` | 97% |
| `lifecycle.py` | 92% |
| `loader.py` | 66% (entry-point path uncovered — no PyPI package in tests) |
| `manifest_schema.py` | 91% |
| `mcp_bridge.py` | 92% |
| `migrate.py` | 85% |
| `registry.py` | 67% (install_from_zip path uncovered — needs ZIP fixtures) |
| `security.py` | 95% |
| `storage.py` | 93% |
| `task_actions.py` | 94% |
| `ui_mount.py` | 91% |
| `webhooks.py` | 98% |
| **TOTAL** | **86%** |

The lower coverage areas (loader entry-points, registry install_from_zip) are paths that need external infrastructure to exercise meaningfully — a published PyPI package and a ZIP fixture. They are documented and ready to be filled in when those fixtures exist.

## Touch in core

Files modified by OXS (delta only):
- `src/constants.py` — `+EXTENSIONS_DIR`, `+DATA_EXTENSIONS_DIR`, `+OXS_STATE_DIR`, `+OXS_AUDIT_LOG`, `+OXS_PERMISSIONS_DIR`
- `core/database.py` — `+Extension`, `+ExtensionPermission`, `+ExtensionAudit` models
- `core/middleware.py` (via app.py `AUTH_EXEMPT_PATTERNS`) — `+r"^/api/ext/[^/]+/webhook/[^/]+/?$"` regex
- `app.py` — `+oxs_registry = ExtensionRegistry(...)`, `+oxs_registry.bind_app(app)`, `+app.include_router(setup_oxs_api_routes(oxs_registry))`, `+app.include_router(setup_oxs_inbound_webhooks())`
- `src/mcp_manager.py` — `+get_mcp_manager()` singleton, `+register_in_process_tool()`, `+unregister_in_process_tools()`, in-process dispatch branch in `call_tool`

Total core touch: ~80 lines across 5 files.

## What ships

Per the spec (`specs/oxs.md`):

- ✅ Manifest JSON Schema validation (fail-closed)
- ✅ SHA-256 file hash verification
- ✅ AST tripwire (rejects `exec`, `eval`, `compile`, `__import__`)
- ✅ Zip-slip / symlink / device / FIFO rejection
- ✅ Capability gates with safe / medium / privileged tiers
- ✅ Per-extension data storage with table-prefix isolation
- ✅ Inbound webhooks (token + HMAC + rate-limit)
- ✅ In-process MCP tool registration (no subprocess)
- ✅ Event subscription + firing
- ✅ Scheduled task action registration
- ✅ Static file serving via FastAPI's StaticFiles
- ✅ Lifecycle hooks (install / enable / disable / uninstall)
- ✅ Append-only audit log + DB mirror
- ✅ Admin REST API (Settings → Extensions)
- ✅ Bundled + user-installed + entry-point discovery
- ✅ 86% test coverage of `core/extensions/`

## Known gaps

Documented in `specs/oxs.md`:

- Iframe UI sandbox is opt-in (default same-origin)
- Cross-extension events use the loose `event_bus.fire_event` contract
- Hot reload loses in-flight state
- No built-in auto-update notifications
- Per-extension webhook rate limit is per-token, not per-extension
- Frontend `window.__oxs` is module-level; not request-scoped

These are deliberate tradeoffs in scope. The architecture allows tightening
each one in isolation without rewriting other layers.