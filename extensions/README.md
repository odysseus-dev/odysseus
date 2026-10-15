# OXS — Authoring Extensions

OXS (Odysseus Extension System) is a local-first extension host for
Odysseus. An extension is a small Python package that registers tools,
webhooks, MCP servers, event listeners, settings, and UI panels with the
running Odysseus process — without modifying core.

This guide shows how to write one.

## Quick start

The minimum viable extension looks like this:

```
extensions/
└── hello/
    ├── extension.json
    └── plugin.py
```

`extension.json`:

```json
{
  "id": "hello",
  "name": "Hello",
  "version": "1.0.0",
  "oxs_version": ">=1.0.0",
  "entry_point": "plugin:register",
  "capabilities": {},
  "hooks": ["on_enable", "on_disable"]
}
```

`plugin.py`:

```python
from core.extensions import on_enable, on_disable

def register(host):
    logger = host.get_logger()

    @on_enable
    def _enable():
        logger.info("hello enabled")

    @on_disable
    def _disable():
        logger.info("hello disabled")
```

Drop both files in `extensions/hello/` and enable from
Settings → Extensions (admin-only). The next restart picks it up; the
admin UI shows it immediately on the next page load.

## Manifest schema

The full schema lives in `core/extensions/manifest_schema.py`. Key fields:

| Field | Required | Notes |
|---|---|---|
| `id` | yes | `[a-z0-9][a-z0-9_-]{0,63}` — must be unique across all extensions |
| `name` | yes | human label, ≤ 256 chars |
| `version` | yes | semver |
| `oxs_version` | yes | PEP 440 range against the running OXS core version |
| `entry_point` | yes | `module:callable` — the callable receives `host` |
| `capabilities` | yes | map of capability name → `true` or scope dict |
| `permissions` | no | medium-tier allowlists (URLs, paths, events) |
| `privileged_permissions` | no | boolean flags requiring operator opt-in |
| `hooks` | no | list of `on_install / on_enable / on_disable / on_uninstall / on_startup / on_shutdown` |
| `settings_schema` | no | JSON Schema for the admin-side settings form |
| `data_schema` | no | per-extension tables; auto-migrated on enable |
| `file_hashes` | required for non-bundled | `sha256:<hex>` for every shipped file |

## Capabilities

The host object passed to `register(host)` exposes one method per
capability. Calling a method whose capability isn't declared in the
manifest raises `OXSCapabilityError` and writes an audit entry.

### Safe tier (auto-granted on enable)

- `data` — `host.get_storage()` returns a `PluginStorage` for own tables
- `routes` — `host.add_route(path, handler, auth="user"|"admin"|"public")`
- `tools` — `host.register_tool(name, schema, handler)`
- `webhooks_in` — `host.register_webhook(path_suffix, event_filter, handler)`
- `webhooks_out` — `host.fire_webhook(event, payload, url=None)`
- `mcp_tools` — `host.register_mcp_tool(server_name, tool_name, schema, handler)`
- `ui_sidebar`, `ui_panel`, `ui_slash`, `ui_settings` — UI surface
- `static` — `host.add_static(url_prefix, directory)`
- `events_sub`, `events_fire` — event bus integration
- `task_actions` — `host.register_task_action(name, schema, handler)`
- `filesystem_plugin` — read files inside the extension's own dir
- `settings_read` — `host.read_setting(key, default=None)`

### Medium tier (allowlist required)

- `network_outbound` — `permissions.network_outbound` is a list of URL prefixes
- `filesystem_data_subpath` — `permissions.filesystem_data_subpath` is a list of relative paths

### Privileged tier (operator must check at install)

- `shell_command` — `host.run_command(cmd, timeout=30)`
- `admin_routes` — `host.add_admin_route(path, handler)`
- `manage_other_extensions` — `host.list_other_extensions()`
- `network_outbound_private` — allow RFC1918 / loopback destinations
- `modify_core_settings` — `host.set_core_setting(key, value)`

## Lifecycle hooks

Hooks are decorated callables registered inside `register(host)`:

```python
from core.extensions import (
    on_install, on_enable, on_disable, on_uninstall,
)

def register(host):
    @on_install
    def _install():
        # one-time setup: write default settings, etc.
        ...

    @on_enable
    def _enable():
        # register routes, tools, webhooks, MCP, UI, events
        host.add_static("/ext-static/hello", "ui")
        host.subscribe_event("oxs.rescan", lambda p: host.get_logger().info("rescan"))

    @on_disable
    def _disable():
        # host automatically unwinds all registrations on disable
        # this hook runs last, only for custom cleanup
        ...

    @on_uninstall
    def _uninstall():
        # runs before the extension's data tables are dropped
        ...
```

Hook failures are isolated: one bad hook does not break other hooks or
other extensions' loads.

## Storage

Declare tables in `data_schema`:

```json
{
  "data_schema": {
    "messages": {
      "columns": {
        "id": "TEXT PRIMARY KEY",
        "chat_jid": "TEXT NOT NULL",
        "text": "TEXT",
        "ts": "INTEGER NOT NULL"
      },
      "indexes": [["chat_jid", "ts"]],
      "unique": [["id"]]
    }
  }
}
```

Tables become `<extension_id>__<name>` in the SQLite DB — for example,
`hello__messages`. Tables are auto-migrated on enable. Storage:

```python
@on_enable
def _enable():
    storage = host.get_storage()
    storage.insert("messages", id="abc", text="hi", ts=1234)
    for row in storage.execute_raw("SELECT * FROM hello__messages"):
        host.get_logger().info(row)
```

`execute_raw` is restricted to `SELECT` and only the extension's own
tables (the `<ext_id>__` prefix wall). Cross-extension queries are
rejected.

## Webhooks

Declare `webhooks_in` in capabilities and register at enable time:

```python
@on_enable
def _enable():
    def _on_msg(body, headers, query, event_filter):
        # body is the parsed JSON (or raw bytes if invalid JSON)
        host.get_logger().info("got webhook", body)

    host.register_webhook(
        path_suffix="/wh/{token}",
        event_filter=["messages.upsert"],
        handler=_on_msg,
    )
```

On install, OXS generates a 32-byte random token. The handler is
invoked asynchronously when a request hits:

```
POST /api/ext/<extension_id>/webhook/<token>
```

Optional `X-<extension_id>-Signature: sha256=<hmac>` is verified against
a per-extension HMAC secret. Rate limit is 10 req/s per token.

The admin UI shows the raw token exactly once at install. Operators
should copy it to their secret store immediately.

## MCP tools

Add tools to the agent's toolbelt in `register_mcp_tool`:

```python
@on_enable
def _enable():
    host.register_mcp_tool(
        "my_server", "search",
        schema={
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
        handler=lambda args, ctx: {"results": [...]},
    )
```

After enable, the agent can call the tool as
`mcp__oxs_<extension_id>__search`. Handlers can be sync or async.

## Tasks

```python
async def my_action(owner, prompt="", **kwargs):
    return ("done", True)

@on_enable
def _enable():
    host.register_task_action("my_action", {"type": "object"}, my_action)
```

Operators schedule tasks that invoke the action by name in the admin UI.
The action name is namespaced to `<extension_id>` so collisions are
impossible.

## Events

```python
@on_enable
def _enable():
    def _on(p):
        host.get_logger().info("got", p)
    host.subscribe_event("oxs.rescan", _on)
```

Extensions can also fire their own events:

```python
host.fire_event("hello.greet", {"msg": "hi"})
```

Extensions that share an event name see each other's events.

## Permissions and secrets

The host provides a per-extension settings namespace backed by
`data/oxs/permissions/<id>.json`:

```python
settings = host.get_settings()
token = settings.get("api_token", "")
```

The Settings admin UI auto-renders a form from `settings_schema`.

For secrets the extension manages itself (e.g. an OAuth refresh token),
use `filesystem_data_subpath` capability and write to a private
subdirectory under `data/extensions/<id>/`.

## Distribution

Three locations work:

| Source | Location | Hashes required | Use case |
|---|---|---|---|
| Bundled | `extensions/<id>/extension.json` | No (source-controlled) | Shipped with Odysseus |
| User-installed | `data/extensions/<id>/extension.json` | Yes | One-off installs from the admin UI |
| Entry-point | pip package with `odysseus.extensions` group | No (pip verifies) | Marketplace-style distribution |

For bundled extensions, edit `extensions/<id>/` and submit a PR. For
user-installed, ZIPs are validated by `security.verify_zip_safe`
(zip-slip, symlink, device, FIFO rejection) and SHA-256-checked against
`file_hashes` on every enable.

## Trust model

OXS extensions run **in-process** with the same privileges as the
Odysseus host Python process. There is no per-extension sandbox beyond:

- Capability gates (every host method checks)
- Path traversal rejection (read_plugin_file, add_static, execute_raw)
- Zip-slip / symlink rejection (verify_zip_safe)
- AST tripwire (rejects exec / eval / __import__ in plugin.py)
- Rate limit on webhooks
- Per-token URL with 32-byte secret

If you need a stronger sandbox (e.g. for untrusted-code marketplaces),
run Odysseus in a container. The current trust model assumes the operator
audits extensions before installing them.

## Examples

The bundled `extensions/hello-odysseus/` is a complete reference:

- declares `static`, `events_sub`, `events_fire`, `settings_read`
- registers a static panel, an event subscription, and fires a
  `hello.greet` event on enable
- uses `host.get_logger()` with a per-extension logger name