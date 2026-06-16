# Writing an Odysseus Plugin

An Odysseus plugin is a pip-installable Python package that extends the app via a thin `register(host)` contract.

## What is a plugin

Plugins run **in-process** — the same trust model as any `pip install` dependency. The manifest (`odysseus-plugin.json`) is transparency, not a sandbox: it shows the author, source link, and declared capabilities so an operator can make an informed install decision.

## Manifest (`odysseus-plugin.json`)

```json
{
  "$schema": "https://odysseus.dev/odysseus-plugin.schema.json",
  "name": "my-plugin",
  "version": "1.0.0",
  "entry_point": "my_plugin:register",
  "odysseus_compat": ">=0.1.0",
  "description": "What my plugin does",
  "author": "You",
  "homepage": "https://example.com",
  "repository": "https://github.com/you/my-plugin",
  "license": "MIT",
  "capabilities": ["tools", "settings", "ui"],
  "frontend": "static/frontend.js",
  "styles": ["static/style.css"]
}
```

### Required fields

- `name` — kebab-case identifier
- `version` — SemVer (`1.0.0`)
- `entry_point` — dotted path to `register(host)` callable
- `odysseus_compat` — version range of Odysseus this plugin supports
- `description` — short human-readable description
- `author` — author or organization
- `capabilities` — list of required capabilities

### Capabilities

**Ordinary** (self-serve):
- `tools` — register agent tools
- `routes` — add FastAPI routes
- `ui` — contribute frontend nav items, panels, settings tabs
- `settings` — add settings sections
- `provider` — register LLM/search providers

**Privileged** (require explicit operator opt-in, flagged loudly):
- `manage_plugins` — toggle other plugins, mount admin routes

## `register(host)`

```python
def register(host):
    host.add_router(router, admin=False)   # needs "routes"
    host.add_static("/static/my", "./static")
    host.register_provider("my_provider", MyProvider)  # needs "provider"
    host.add_settings_section("my", "My Plugin", render_fn)  # needs "settings"
    host.add_tool("my_tool", schema, fn)   # needs "tools"
```

The host facade gates every call by the plugin's declared capabilities. A plugin without `routes` that calls `add_router()` raises `PermissionError`.

## Frontend

If your plugin declares the `ui` capability and a `frontend` file, Odysseus loads it as a regular script in the main page (no iframe).

```javascript
window.__odysseusPluginHost.registerSettingsTab({
  id: "my",
  label: "My Plugin",
  render: () => `<p>Hello from my plugin!</p>`,
});
```

## Trust model

- Plugins are pip-installed server-side Python.
- The manifest is transparency: author, source, capabilities.
- It does **not** isolate a hostile plugin.
- Privileged capabilities (`manage_plugins`) require explicit operator opt-in.

## Plugin vs MCP server vs skill

- **MCP server** — external tool-only process (good for WhatsApp, etc.)
- **Plugin** — in-process Python that adds routes/settings/providers/UI
- **Skill** — prompt/behavior only

## Reference plugin

See `plugins/hello-odysseus/` for a minimal working example.
