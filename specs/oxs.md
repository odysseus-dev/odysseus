# OXS — Odysseus Extension System

Last updated: dev@local | 2026-10-07

## Scope

OXS is a local-first extension system for Odysseus. Extensions are versioned units of code that augment the running app with capabilities the core does not ship by default — WhatsApp bridges, calendar sources, agent tools, UI panels, webhooks, MCP servers, scheduled task actions, and isolated storage.

This spec owns:

- `core/extensions/` — host runtime (manifest schema, loader, registry, host API, capability gates, audit log, security checks)
- `extensions/` — bundled extensions (versioned in the repo)
- `data/extensions/` — user-installed extensions (gitignored, per-instance)
- `data/oxs/` — install state, audit log, permission grants
- `static/js/oxs_registry.js` — frontend host API (`window.__oxs`)
- `static/js/extensions.js` + `static/css/oxs_settings.css` — admin UI
- `routes/oxs_api.py` — admin REST surface (`/api/oxs/*`, `/api/ext/<id>/*`)
- SQLAlchemy models `Extension`, `ExtensionPermission`, `ExtensionAudit` in `core/database.py`
- `core/middleware.py:AUTH_EXEMPT_PATTERNS` — path exemption for inbound extension webhooks
- `app.py` — minimal wiring (`setup_oxs_routes`, `await oxs.startup_all`)

OXS does not own core functionality. Every feature that ships bundled in Odysseus (email, calendar, notes, RAG, image gen) is core code. Extensions sit beside it.

## Design Principles

OXS follows a small set of rules that govern every decision:

1. **Zero trust default.** Every extension is `disabled` until the operator explicitly enables it. No auto-enroll.
2. **Install-at-your-own-risk transparency.** Every capability the extension declares is shown to the operator before install. Privileged capabilities require explicit per-capability confirmation.
3. **Capability ≠ Permission.** A capability is *what it can do* (declared in the manifest). A permission is *where it can reach* (allowlisted in `permissions` and `privileged_permissions`). Both are required.
4. **Isolation by tier.** Safe capabilities run in-process behind capability gates. Medium capabilities run with explicit allowlists. Privileged capabilities require operator opt-in and additional runtime guards.
5. **Storage isolation.** Each extension gets its own data directory (`data/extensions/<id>/`) and its own DB tables (`<id>__<table>`). No extension touches another extension's data.
6. **Fail-closed JSON Schema.** Manifest, settings, and data schema are validated against bundled JSON Schemas. Any non-conforming manifest is rejected with a clear error.
7. **No exec at discovery.** Manifests are read with `ast.parse`-style introspection, never `import`. Imports happen only on explicit enable.
8. **Backward compatible.** No change to existing routes, no change to existing config files, no migration of existing data.

## Manifest

Every extension ships an `extension.json` at the root of its directory or ZIP.

```jsonc
{
  "id": "whats2api-bridge",          // [a-z0-9][a-z0-9_-]{0,63}
  "name": "WhatsApp via whats2api",  // human label
  "version": "1.0.0",                // semver
  "oxs_version": ">=1.0.0,<2.0.0",  // required: OXS core compat range
  "python_deps": ["httpx>=0.27"],
  "author": {"name": "...", "url": "...", "email": "..."},
  "license": "MIT",
  "repository": "https://github.com/.../...",

  "entry_point": "plugin:register",  // module:callable inside the package
  "hooks": ["on_install", "on_enable", "on_disable", "on_uninstall"],

  "capabilities": {
    "data":       {"own_tables": ["messages", "chats"]},
    "routes":     [{"prefix": "/api/ext/whats2api", "auth": "user"}],
    "tools":      [{"name": "w2a_search_messages", "description": "..."}],
    "webhooks_in": [{"path_suffix": "/wh/{token}", "events": ["messages.upsert"]}],
    "mcp_tools":  [{"server_name": "w2a", "tool_name": "search_messages"}],
    "ui_sidebar": {"id": "w2a", "icon": "chat", "label": "WhatsApp"},
    "ui_panel":   {"id": "w2a_panel", "title": "WhatsApp"},
    "ui_slash":   [{"command": "/w2a", "description": "..."}],
    "ui_settings":{"tab": "w2a", "title": "WhatsApp"},
    "static":     [{"url_prefix": "/ext-static/w2a", "dir": "ui"}],
    "events_sub":  ["w2a.message.received"],
    "events_fire": ["w2a.message.received"],
    "task_actions":[{"name": "w2a_classify", "schema": {...}}]
  },

  "permissions": {
    "network_outbound":         ["https://api.whats2api.com"],
    "filesystem_data_subpath":  ["w2a_media/"],
    "settings_read":            ["odysseus.settings.*"]
  },

  "privileged_permissions": {
    "shell_command":            false,
    "admin_routes":             false,
    "manage_other_extensions":  false,
    "network_outbound_private": false,
    "modify_core_settings":     false
  },

  "settings_schema": { /* JSON Schema → auto-generated settings form */ },
  "data_schema":     { /* JSON Schema → auto-generated DB tables */ },

  "file_hashes": {   /* mandatory SHA-256 of every shipped file */
    "plugin.py":      "sha256:...",
    "ui/panel.html":  "sha256:...",
    "extension.json": "sha256:..."
  }
}
```

The full schema lives in `core/extensions/manifest_schema.py` and is the canonical contract. Any deviation from the schema is a hard install error.

## Discovery & Loading

OXS looks for extensions in three locations, in priority order:

1. **Bundled** — `extensions/<id>/extension.json` in the repo. Shipped extensions. Always trusted (source-controlled).
2. **User-installed** — `data/extensions/<id>/extension.json`. Operator-installed from URL or ZIP. Hashes verified.
3. **Entry-points** — Python packages exposing the `odysseus.extensions` entry-point group. Pip-installed packages. Hashes verified.

Bundled extensions are auto-discovered but still require explicit enable. User-installed and entry-point extensions follow the install flow described below.

The loader:
- Reads `extension.json` from each location using JSON Schema. No execution.
- For entry-points, inspects the package metadata. No import.
- Verifies `file_hashes` for non-bundled extensions before enabling.
- Rejects extensions whose `oxs_version` does not satisfy the running core's range.

## Capabilities & Permissions

Capabilities are declared in the manifest. Each capability corresponds to one or more `OXSHost` methods. Calling a method whose capability is not declared raises `OXSCapabilityError` and is logged.

Permissions are URL/path/resource-scoped allowlists. They are checked at call time, not declaration time.

### Capability Tiers

| Tier | Capabilities | Runtime enforcement |
|---|---|---|
| **safe** (default) | `data`, `routes`, `tools`, `webhooks_in`, `webhooks_out`, `mcp_tools`, `ui_*`, `static`, `events_sub`, `events_fire`, `task_actions`, `filesystem_plugin`, `settings_read` | `@require_capability(...)` decorator on `OXSHost` methods |
| **medium** | `network_outbound`, `filesystem_data_subpath` | Same decorator + allowlist check against declared destinations/paths |
| **privileged** | `shell_command`, `admin_routes`, `manage_other_extensions`, `network_outbound_private`, `modify_core_settings` | Same decorator + additional runtime guard (subprocess isolation, admin gate, etc.) + explicit operator confirmation at install |

### Capability Matrix (Install UI)

Before install, the admin UI renders a full capability matrix from the parsed manifest. The operator must:
- Confirm each privileged capability with a checkbox.
- See exactly which destinations the extension will reach (`network_outbound` allowlist).
- See exactly which paths it will write (`filesystem_data_subpath` allowlist).
- See every event it will subscribe to or fire.

The install cannot proceed until the operator confirms.

### Runtime Check

Every `OXSHost` method is decorated:

```python
@require_capability("tools")
def register_tool(self, name, schema, handler): ...

@require_capability("network_outbound", destinations=True)
async def http_request(self, method, url, **kw): ...
    # destinations=True → also checks url against self.allowed_destinations
```

A violation raises `OXSCapabilityError` and writes an `audit.log` entry with the violation context (extension id, method, attempted argument). The extension continues to run for other calls but the violation is visible in the audit log.

## Host API

The `OXSHost` is the object passed to `entry_point` of every extension. It is the *only* way an extension reaches the running app. Every method is capability-gated.

```python
class OXSHost:
    # ── Identity ─────────────────────────────────
    extension_id: str
    extension_version: str

    def get_logger(self) -> logging.Logger
    def get_settings(self) -> SettingsNamespace
    def get_data_path(self, subdir: str = "") -> Path
    def get_plugin_path(self) -> Path

    # ── Data ──────────────────────────────────────
    @require_capability("data")
    def get_storage(self) -> PluginStorage

    # ── Routes ────────────────────────────────────
    @require_capability("routes")
    def add_route(self, path, handler, auth="user", methods=("GET",))

    @require_privileged("admin_routes")
    def add_admin_route(self, path, handler, methods=("GET",))

    # ── Agent tools ───────────────────────────────
    @require_capability("tools")
    def register_tool(self, name, schema, handler)

    # ── Webhooks ──────────────────────────────────
    @require_capability("webhooks_in")
    def register_webhook(self, path_suffix, event_filter, handler, auth_token=None)

    @require_capability("webhooks_out")
    async def fire_webhook(self, event, payload, url=None)

    # ── MCP bridge ────────────────────────────────
    @require_capability("mcp_tools")
    def register_mcp_tool(self, server_name, tool_name, schema, handler)

    # ── UI ────────────────────────────────────────
    @require_capability("ui_sidebar")
    def add_sidebar_item(self, id, icon, label, panel_id=None)

    @require_capability("ui_panel")
    def add_panel(self, id, title, source_url=None, html=None)

    @require_capability("ui_slash")
    def register_slash_command(self, command, description, handler)

    @require_capability("ui_settings")
    def add_settings_tab(self, tab, title, source_url)

    @require_capability("static")
    def add_static(self, url_prefix, dir)

    # ── Events ────────────────────────────────────
    @require_capability("events_sub")
    def subscribe_event(self, event_name, handler)

    @require_capability("events_fire")
    def fire_event(self, event_name, payload, owner=None)

    # ── Scheduled tasks ───────────────────────────
    @require_capability("task_actions")
    def register_task_action(self, name, schema, handler)

    # ── Network ───────────────────────────────────
    @require_capability("network_outbound", destinations=True)
    async def http_request(self, method, url, **kw)

    # ── Filesystem ────────────────────────────────
    @require_capability("filesystem_plugin")
    def read_plugin_file(self, relpath) -> bytes

    @require_capability("filesystem_data_subpath")
    def write_data_file(self, relpath, data) -> None

    # ── Privileged ────────────────────────────────
    @require_privileged("shell_command")
    async def run_command(self, cmd, timeout=30)

    @require_privileged("manage_other_extensions")
    def list_other_extensions(self) -> list

    @require_privileged("modify_core_settings")
    def set_core_setting(self, key, value)
```

`OXSHost` is created fresh on each enable cycle. It is not shared across extensions. Capability gates and allowlists are scoped per-host, so extensions cannot affect each other's gates.

## Storage Isolation

Each extension gets two isolated storage areas:

### Filesystem

- `extensions/<id>/` (bundled) or `data/extensions/<id>/` (user-installed) — extension code + read-only assets
- `data/extensions/<id>/<subpath>/` for files declared in `filesystem_data_subpath` permission. Outside this prefix is rejected.

### Database

Tables declared in `data_schema` are auto-created on enable and dropped on uninstall. Table names are prefixed with `<id>__` to avoid collisions. Row-level access through `PluginStorage` is bound to the prefix — an extension cannot `SELECT` from another extension's tables.

```python
storage = host.get_storage()

# Create (auto-migrated from data_schema on enable)
storage.insert("messages", id="abc", chat_jid="...", ts=1234)

# Query
for row in storage.query("messages").where(text__contains="hello"):
    print(row)

# Table-level operations
storage.drop_table("messages")  # only own tables
```

`data_schema` is a JSON Schema-like dict that drives `CREATE TABLE IF NOT EXISTS`:

```jsonc
{
  "messages": {
    "columns": {
      "id":           "TEXT PRIMARY KEY",
      "chat_jid":     "TEXT NOT NULL",
      "sender_jid":   "TEXT",
      "text":         "TEXT",
      "ts":           "INTEGER NOT NULL",
      "from_me":      "INTEGER DEFAULT 0",
      "raw":          "TEXT"
    },
    "indexes": [["chat_jid", "ts"], ["sender_jid"]],
    "unique":  [["id"]]
  }
}
```

`PluginStorage.execute_raw(sql, params)` only accepts `SELECT` statements restricted to own tables. Any other DDL/DML requires the `data` capability and the table-prefix guard.

## Webhooks

### Inbound (world → extension)

Pattern, exempted from session auth via `core/middleware.py:AUTH_EXEMPT_PATTERNS`:

```
POST /api/ext/{extension_id}/webhook/{token}
Body: any JSON
Headers: x-{extension_id}-signature: <hmac sha256, optional>
```

- `token` is generated by OXS on install (or supplied by the operator). Stored hashed (bcrypt).
- Path validation enforces `<extension_id>/<token>` match for the installed extension.
- HMAC signature is verified in constant time when supplied.
- Handler is dispatched via `asyncio.create_task`. The endpoint returns `202 Accepted` immediately.
- Failures (validation, handler exception) write to `audit.log` and return `4xx`.
- Rate-limit per token: 10 req/s default, configurable via `settings_schema`.

### Outbound (extension → world)

`host.fire_webhook(event, payload, url=None)`:

- If `url` is None: dispatches to all admin-configured webhooks subscribed to this event.
- If `url` is supplied: must match the extension's `permissions.network_outbound` allowlist. Private IP ranges require `privileged_permissions.network_outbound_private = true`.
- HMAC signing via `OXS_WEBHOOK_SECRET` env var when set.

## MCP Bridge

Extensions can register in-process MCP tools without spawning subprocesses:

```python
host.register_mcp_tool(
    server_name="w2a",
    tool_name="search_messages",
    schema={"type": "object", "properties": {...}, "required": [...]},
    handler=lambda args, ctx: ...
)
```

OXS creates or updates an `McpServer` row with `transport="in-process"` and registers the tool with the existing `McpManager`. The tool appears to the agent alongside built-in tools. Dispatch is in-process; no pipe overhead.

Extensions may also run a subprocess-based MCP server if they declare `python_deps` and `entry_point`. That requires `privileged_permissions.shell_command = true` and is the only path that touches subprocesses.

## Frontend

`window.__oxs` is the browser-side host API. It is loaded once by the core app and exposes:

```js
window.__oxs = {
  // Sidebar
  registerSidebarItem({id, icon, label, panelId}),
  // Panels
  registerPanel({id, title, sourceUrl, html}),
  // Settings tabs
  registerSettingsTab({tab, title, sourceUrl}),
  // Slash commands
  registerSlashCommand({command, description, handler}),
  // Static
  loadScript(url), loadStyle(url),
  // Modal/toast (reuses core)
  showToast, showError, styledConfirm,
  // Settings
  getSetting(key), setSetting(key, value),
  // Events (WebSocket channel)
  on(event, handler), fire(event, payload)
}
```

Static assets are served at `/ext-static/<id>/...` with CSP `default-src 'self'`. Iframe sandbox is opt-in per extension via the `ui_sandbox` capability (default off). `localStorage`/`sessionStorage` are namespaced to `oxs.<id>.*` — extensions cannot read other extensions' storage.

## Lifecycle Hooks

```python
def register(host: OXSHost):
    @host.on_install
    def _install():
        # one-time setup (data dir, settings defaults)

    @host.on_enable
    def _enable():
        # register routes, tools, mcp_tools, ui, webhooks, events
        # PluginStorage.create_default_tables() runs automatically before this

    @host.on_disable
    def _disable():
        # host auto-unregisters everything; override only for custom cleanup

    @host.on_uninstall
    def _uninstall():
        # custom cleanup; tables dropped automatically after this hook
```

Host guarantees that all registrations made during `on_enable` are torn down on `on_disable`. Crashes during any hook are caught, logged to `audit.log`, and do not prevent other extensions from starting.

## Install Flow

1. **Source**: upload ZIP, paste repo URL, choose bundled extension, or install pip package (entry-point).
2. **Validation**: JSON Schema, SHA-256 file_hashes, zip-slip/symlink check, AST parse of `entry_point` (no exec).
3. **Capability preview**: admin UI renders the full capability matrix (see above).
4. **Operator confirms**: each privileged capability checked individually.
5. **Install**: copy to `data/extensions/<id>/`, write `data/oxs/permissions/<id>.json`, append audit entry.
6. **Disabled by default**: extension is installed but not loaded. Operator enables from the admin UI.

Reload is supported — it re-imports the entry-point and re-runs `on_enable` without restart. Hot uninstall is supported but the running server still uses the previously loaded code until the next reload.

## Security

- **File hashes**: SHA-256 mandatory for non-bundled extensions. Verified on install and on enable.
- **No exec at discovery**: `ast.parse`-style introspection only. Imports happen only on explicit enable.
- **Subprocess isolation**: extensions with `shell_command = true` run subprocesses via `asyncio.create_subprocess_exec` with timeout, minimal env, cwd = plugin path. No shell invocation. No stdin. Output captured.
- **Path traversal**: `Path.resolve().is_relative_to(plugin_path)` check on every filesystem read.
- **SSRF**: `network_outbound` enforces the extension's allowlist. Private IP ranges require explicit opt-in via `privileged_permissions.network_outbound_private`. Reuses `src/url_safety._PRIVATE_NETWORKS`.
- **Token storage**: bcrypt hash for webhook tokens, constant-time compare.
- **Capability-checked decorator**: every `OXSHost` method passes through `@require_capability(...)` or `@require_privileged(...)`. No bypass paths.
- **Crash isolation**: try/except around every hook and registration. Failures are logged, never propagated.
- **Per-extension logger**: prefix `<extension_id>` in every log line.
- **Audit log**: append-only `data/oxs/audit.log`, JSON lines. Includes install, enable, disable, uninstall, capability violations, hook failures, webhook events.

## Admin UI

Settings → Extensions (admin-only):

- **List**: icon + name + version + status (enabled/disabled/error) + capability count + last activity + size on disk + row count
- **Install**: three buttons — From URL, From ZIP, From bundled
- **Detail**: full capability matrix, permission allowlists, hooks list, source/last commit hash, audit log entries for this extension
- **Actions**: Enable, Disable, Reload, Uninstall (each with confirmation)
- **Audit log viewer**: last 100 entries, filterable by extension id
- **Storage**: total disk usage, per-table row count, "Reveal in Finder/Explorer" link
- **Export capability report**: dumps the capability matrix as JSON for compliance review

## Migration Path

Existing in-tree features can migrate to extensions over time without breaking compatibility:

1. TTS read-aloud (#4009) → `tts-readaloud` extension
2. Calendar ICS (#3759) → `calendar-ics` extension
3. Image gen server → `image-gen-extension` (already plugin-shaped)
4. WhatsApp/messaging (#4357) → `whats2api-bridge` extension (or similar)
5. Provider registries (Kagi, Fireworks, Perplexity) → `register_provider` calls
6. MemMachine/TRACE (#3730) → `memmachine` extension
7. Atlas/Obsidian-style vault (#4435) → `atlas-vault` extension

Each migration is one PR with a compat shim that delegates to the extension when enabled and falls back to in-tree code otherwise.

## Testing Notes

PR 1 ships tests for:
- `test_oxs_manifest.py` — JSON Schema validation, missing fields, type errors, file_hashes format
- `test_oxs_security.py` — SHA-256 verify, AST parse without exec, zip-slip detection, symlink rejection
- `test_oxs_audit.py` — append-only log, JSON line format, rotation-safe

Subsequent PRs add:
- `test_oxs_capabilities.py` — capability gate raises `OXSCapabilityError`, audit entry written
- `test_oxs_storage.py` — table prefix isolation, prefix guard rejects cross-table queries
- `test_oxs_webhooks.py` — token validation, HMAC verify, rate-limit, 202 response
- `test_oxs_mcp_bridge.py` — in-process tool appears in `McpManager`, dispatch returns handler result
- `test_oxs_install_flow.py` — end-to-end install from ZIP, capability confirmation gate, enable/disable cycle

## Current Gaps

- Iframe UI sandbox is opt-in. The default same-origin path assumes extensions are operator-trusted (pip-equivalent trust model).
- Inter-extension events use `event_bus.fire_event` with no typed contract. Cross-extension coordination is best-effort.
- Hot reload loses in-flight state. Extension must re-acquire state via `on_enable`.
- Auto-update notifications: extensions must check upstream themselves or rely on the operator re-installing.
- Per-extension rate limit for webhooks is shared globally per token, not per-extension — operators who need finer control must install their own limiter.
- Frontend ``window.__oxs`` is a JS module-level singleton; it cannot be reset per-request in a single-page-app context.

## Security And Provenance

- Every extension declares its `repository` URL. Operator can audit source before install.
- `file_hashes` is mandatory for non-bundled extensions. Tampering between install and enable fails the SHA-256 check.
- Audit log records every install/enable/disable/uninstall with timestamp, actor, and capability set granted.
- Privileged capability changes after install require re-confirmation and re-audit.
- Default-deny: an extension that lacks a capability declaration cannot acquire it at runtime.
- Column definitions in `data_schema` are rejected if they contain `;`, `--`, `/*`, `*/`, or newlines (SQL injection tripwire — DDL only, no DDL is ever accepted via `execute_raw`).
- Webhook tokens are 32 bytes from `secrets.token_urlsafe`. Storage uses a SHA-256 with a static pepper for tamper resistance.