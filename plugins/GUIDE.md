# Plugin developer guide

A companion to **`plugins/README.md`** (the reference for the manifest + `ctx`
API). This covers the conventions and gotchas for building a plugin that looks
and behaves like a native part of Odysseus.

Copy **`plugins/example/`** as your starting point — it's a working scaffold with
a route, a background service, and a themed UI page.

---

## 1. Give your plugin a UI (the `ui` manifest field)

A plugin's hook into the frontend is the **`ui`** manifest entry:

```python
PLUGIN = { ..., "ui": {"open": "/api/plugins/<your-id>/app", "label": "Open"} }
```

This renders an **Open** button on the plugin's card in **Settings → Plugins**
(next to the enable toggle) that opens your page in a new tab. Serve the page
from a route; serve any of your own static files from a `/web/{asset}` route, and
validate the name (`^[A-Za-z0-9_.-]+$`, reject leading `.`) to block traversal.

---

## 2. Visual parity + user theme

Odysseus themes via CSS variables (`--bg --fg --panel --border --red` + advanced
`--input-bg --send-btn-bg --brand-color …`) and persists the active theme to
`localStorage['odysseus-theme']` (+ `/api/prefs/theme`). A **shared theme layer**
ships as a core static asset — link it from your page `<head>`:

```html
<link rel="stylesheet" href="/static/plugin-theme.css">
<script src="/static/js/plugin-theme.js"></script>   <!-- blocking in <head> = no flash -->
```

- `plugin-theme.js` applies the user's colors/font/density **before first paint**
  (reads the same `localStorage` the theme picker writes; `/api/prefs/theme`
  fallback; live-follows changes via the `storage` event).
- `plugin-theme.css` gives you `od-wrap`, `od-card`, `od-btn` (`.ghost`),
  `od-input`, `od-header` (the "‹ Odysseus" back bar), `badge`, `bar`, `muted`,
  `ok`/`warn` — all on the theme vars, so your page recolors with any preset *or*
  custom theme automatically.

Always include the **`od-header`** (or a link to `/`) so users can return to the
main interface — your page opens in a new tab.

> A plugin shipped in its own repo can either link `/static/plugin-theme.css`
> (recommended — it installs into an Odysseus that has it) or vendor a copy under
> its `web/` for full self-containment.

---

## 3. Auth model

Every `/api/*` request passes the global auth middleware before reaching your
route:

- In routes, call `require_admin(request)` for admin-only endpoints; omit it for
  any-logged-in-user endpoints. Don't roll your own auth.
- External clients authenticate with an **Odysseus API token**:
  `Authorization: Bearer ody_…` (Settings → API Tokens).
- `LOCALHOST_BYPASS=true` lets same-machine clients skip auth at the middleware,
  **but `require_admin` does NOT honor it** (it needs a real admin session, the
  internal-tool token, or `AUTH_ENABLED=false`). Use a token for admin routes.
- Your routes must be mounted under **`/api/plugins/<id>/`** (enforced by
  `add_router`) — otherwise a plugin could shadow an auth-exempt path and expose
  an unauthenticated endpoint.
- The global gate only checks *logged-in*; the manifest **`permission` field is
  advisory** (it controls who may toggle the plugin, not your routes). Gate any
  admin-only route yourself with `require_admin`.
- `/static` is auth-exempt, so the theme assets load on any page.

---

## 4. Heavy deps & external binaries

- **Never import heavy deps at module top.** Discovery parses the manifest via AST
  (no import), but *loading* runs `setup` + top-level imports — a missing
  top-level `import torch` turns the plugin into an error card. Import lazily
  inside handlers and degrade with a clear message when absent.
- **Don't bundle large models/weights.** Point users at the source and download
  on demand into `ctx.data_dir` (verify a sha256 when you can).
- **External CLIs may be off the server's PATH** (e.g. a `pip install --user`
  Scripts dir). Resolve robustly: an env override, then PATH, then probe the
  interpreter's Scripts dir + `site.getuserbase()`. Shelling out also decouples
  you from the server's Python version.

---

## 5. CSP for plugin pages

Odysseus ships a strict Content-Security-Policy:

- **Scripts:** `'self'`, `https://cdn.jsdelivr.net`, or inline-with-nonce
  (`<script nonce="{request.state.csp_nonce}">`). No other inline script.
- **No blob/data web workers** — `worker-src` falls back to `default-src 'self'`,
  so libraries that spawn workers from `blob:` URLs are blocked. Serve a worker
  from a same-origin URL, or go worker-free.
- Inline `<style>`/`style=""` is allowed; fetch/XHR is same-origin only.
- Pages are framed-denied (`frame-ancestors 'none'`) — open in a new tab rather
  than embedding a plugin page in an in-app iframe.

---

## 6. Gotcha: import order when reusing the tool subsystem

If your plugin imports from `src.tool_schemas` / `src.tool_execution` /
`src.tool_implementations`, **import `src.agent_tools` first** (the facade) —
importing `src.tool_schemas` directly triggers a `tool_schemas ↔ agent_tools`
circular import. Note `ctx.register_tool` is a no-op in builds without
`src/tool_registry.py` (logs a warning and skips) — expose functionality via
routes unless you've confirmed the registry is present.

---

## 7. Test without booting the whole app

Use the real `PluginManager` + FastAPI `TestClient` against a temp dir (see
`tests/test_plugin_system.py`):

```python
os.environ["ODYSSEUS_PLUGINS_DIR"] = "<dir with your plugin>"
os.environ["ODYSSEUS_DATA_DIR"]   = "<temp>"
os.environ["AUTH_ENABLED"]        = "false"   # require_admin() passes → routes reachable
mgr = PluginManager(app=FastAPI(), directory=...); mgr.load_enabled(app)
```

`DATABASE_URL` defaults to cwd-relative `./data/app.db` — run from a checkout
with a `data/` dir or set `DATABASE_URL=sqlite:///<temp>/app.db`. Run and state
the checks: `python -m py_compile plugin.py`, `node --check web/*.js`.

---

## 8. Distribution

A plugin is a folder, so it ships as its **own git repo** (the
[Cloudflare Tunnel](https://github.com/kanaru-dev/odysseus-plugin-cloudflare-tunnel),
[MCP Server](https://github.com/kanaru-dev/odysseus-plugin-mcp-server), and
[Image → Splat](https://github.com/kanaru-dev/odysseus-plugin-image-splat)
plugins are the references). Publish a release zip (files at the archive root)
with its `sha256`, then open a PR adding an entry to
[`plugins/registry.json`](registry.json). Users then install it in one click from
**Settings → Plugins → Browse**. Keep secrets out of the plugin — read them from
`ctx.data_dir` config or Odysseus settings.
