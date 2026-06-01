# Writing Odysseus Plugins

Add a feature to Odysseus by dropping a **folder** in `plugins/` — no core edits.
A plugin can register agent tools, mount HTTP routes, and run background
services, and it can be **enabled/disabled live** from **Settings → Plugins**
(no restart). Plugins can live in their own git repos and be shared.

New here? Copy `plugins/example/` and skim **[GUIDE.md](GUIDE.md)** for theming,
auth, CSP, heavy-deps, and distribution conventions.

- [Why plugins?](#why-plugins)
- [Quick start](#quick-start)
- [Anatomy](#anatomy)
- [Manifest reference](#manifest-reference)
- [The `ctx` (PluginContext)](#the-ctx-plugincontext)
- [Worked examples](#worked-examples) — route, background service, agent tool
- [UI pages & theming](#ui-pages--theming)
- [Lifecycle & enable/disable](#lifecycle--enabledisable)
- [Per-plugin storage](#per-plugin-storage)
- [Auth & permissions](#auth--permissions)
- [Managing plugins (UI + HTTP API)](#managing-plugins)
- [Distributing a plugin](#distributing-a-plugin)
- [Troubleshooting](#troubleshooting)

## Why plugins?

The plugin system exists so Odysseus can grow **without growing the core**:

- **Less churn in main source.** A feature lives in its own folder instead of
  threading edits through core modules — smaller diffs, easier review, fewer
  merge conflicts, and a core that stays focused and maintainable.
- **Users choose what they run.** Nothing is forced on. Enable only the features
  you want — a leaner install, a smaller attack surface, and no paying (in load
  time, deps, or risk) for things you don't use.
- **A real ecosystem.** Anyone can build and share a feature as a drop-in folder /
  separate repo, without touching core or waiting on an upstream merge. Third-party
  plugins evolve on their own cadence.
- **Bundle-by-default, still modular.** Odysseus can ship selected plugins enabled
  out of the box (so they "just work") while keeping them isolated and toggleable —
  the default experience is rich, but every piece can be turned off.
- **Safe by construction.** A broken plugin is isolated (it can't crash startup),
  each one tears down cleanly, and a plugin is easy to test on its own — so adding
  or removing a feature is low-risk.

In short: keep the core small and stable, push optional/experimental/niche
functionality to the edges, and let users (and the community) compose the Odysseus
they want.

## Quick start

Copy `plugins/example/` to `plugins/my_plugin/`, edit the `PLUGIN` manifest and
`setup(ctx)`, then either restart Odysseus or hit **Settings → Plugins → Rescan**.
Your plugin appears in the panel with an on/off toggle.

## Anatomy

A plugin is discovered as either:

```
plugins/<id>/plugin.py          # a folder with a plugin.py  (preferred)
plugins/<id>_plugin.py          # …or a single file
```

`<id>` (the folder/file name) is the plugin's stable id. The module exposes:

```python
PLUGIN = { ... }                 # a manifest dict (see below)

def setup(ctx):                  # like Blender's register() — wire everything here
    ...

def teardown(ctx):               # OPTIONAL — like unregister(); usually unneeded,
    ...                          #   because add_service(stop=)/on_teardown() cover cleanup
```

## Manifest reference

`PLUGIN` is read at discovery **without executing your module** (parsed from the
AST), so keep it a plain literal dict.

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | Human-readable name shown in the panel. |
| `version` | rec. | e.g. `"1.0.0"`. |
| `author` | no | Shown in the panel. |
| `description` | rec. | One line; shown under the name. |
| `category` | no | Grouping label (e.g. `Networking`, `Tools`). Default `General`. |
| `permission` | no | Who may toggle it: `"admin"` (default) or `"user"`. |
| `requires` | no | Informational list of pip packages / external binaries. |
| `ui` | no | `{"open": "/api/plugins/<id>/page", "label": "Open"}` — adds an **Open** button on the plugin's Settings → Plugins card, linking to a page your plugin serves. |

## The `ctx` (PluginContext)

`setup(ctx)` / `teardown(ctx)` receive a `PluginContext`. **Prefer its helpers**
over touching `ctx.app` directly — the manager *tracks* what you register so it
can undo it cleanly when the plugin is disabled.

| Helper | What it does |
|---|---|
| `ctx.add_router(router)` | Mount a FastAPI `APIRouter`. Its routes are removed on disable. |
| `ctx.add_service(start=, stop=)` | Run `start()` now; run `stop()` on disable/shutdown. |
| `ctx.register_tool(spec)` | Register an agent tool (`ToolSpec`), if the tool registry is present. |
| `ctx.on_teardown(fn)` | Register any extra cleanup callable. |
| `ctx.app` | The FastAPI app (escape hatch — usually not needed). |
| `ctx.data_dir` | A per-plugin writable directory (created for you). |
| `ctx.logger` | Logger namespaced `plugin.<id>`. |

## Worked examples

### 1. A route (HTTP endpoint)

```python
from fastapi import APIRouter, Request

PLUGIN = {"name": "Echo", "version": "1.0.0", "category": "Examples"}

def setup(ctx):
    router = APIRouter(prefix="/api/plugins/echo", tags=["plugin:echo"])

    @router.post("/say")
    async def say(request: Request):
        body = await request.json()
        return {"echo": body.get("text", "")}

    ctx.add_router(router)   # auto-removed when disabled
```

The route is behind Odysseus auth (a logged-in session). Add admin-only gating
with `from core.middleware import require_admin; require_admin(request)`.

### 2. A background service

```python
import threading, time

PLUGIN = {"name": "Heartbeat", "version": "1.0.0", "category": "Examples"}

def setup(ctx):
    stop = threading.Event()
    def run():
        while not stop.is_set():
            ctx.logger.info("tick"); stop.wait(30)
    t = threading.Thread(target=run, daemon=True)
    ctx.add_service(start=t.start, stop=stop.set)   # started now; stopped on disable
```

### 3. An agent tool

```python
from src.tool_registry import ToolSpec   # available when the tool registry is installed

PLUGIN = {"name": "Word Count", "version": "1.0.0", "category": "Tools"}

async def _run(args):
    return {"output": str(len((args.get("text") or "").split())), "exit_code": 0}

def setup(ctx):
    ctx.register_tool(ToolSpec(
        name="word_count",
        description="Count the words in some text.",
        parameters={"type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"]},
        execute=_run,
        permission="user",
    ))
```

A plugin can do **any combination** of the above in one `setup(ctx)`.

## UI pages & theming

A plugin can serve a page that matches the interface. Add a `ui` entry to the
manifest (above) for an **Open** button, serve the page from a route, and link
the shared theme layer so it picks up the user's active theme + customization
(with no flash):

```html
<link rel="stylesheet" href="/static/plugin-theme.css">
<script src="/static/js/plugin-theme.js"></script>
```

That gives you `od-wrap` / `od-card` / `od-btn` / `od-input` / `od-header`
components built on the app's theme variables, so the page recolors with any
preset or custom theme. Include the `od-header` (or a link to `/`) so users can
get back to the main interface — plugin pages open in a new tab. See
[`plugins/example/`](example/plugin.py) for a working page and
**[GUIDE.md](GUIDE.md)** (theming, auth, CSP, heavy deps, distribution).

## Lifecycle & enable/disable

- **Discovery** reads the manifest via AST (no code execution), so even a plugin
  that would fail to load still appears in the panel.
- **Enabled** plugins are loaded at startup (their `setup(ctx)` runs). Disabled
  ones are not.
- Toggling in the UI calls `setup`/`teardown` **immediately** — routes appear/
  disappear and services start/stop without a restart.
- A **broken** plugin (raises on import or in `setup`) is recorded with its
  traceback, its partial `setup` is rolled back, and startup continues. The panel
  shows the error.
- Enable state persists in `<data>/plugins.json`; a newly dropped-in plugin
  defaults to **enabled**.

## Per-plugin storage

`ctx.data_dir` is a writable folder unique to your plugin
(`<data>/plugins/<id>/`) — use it for downloaded binaries, caches, or config:

```python
import os, json
def setup(ctx):
    cfg = os.path.join(ctx.data_dir, "config.json")
    state = json.load(open(cfg)) if os.path.exists(cfg) else {}
```

## Auth & permissions

Routes you mount go through Odysseus's normal auth: they require a logged-in
session, and `require_admin(request)` makes them admin-only. They are **not**
reachable without credentials — including over a Cloudflare tunnel. The manifest
`permission` field controls who may *toggle* the plugin from the panel.

## Managing plugins

**UI:** Settings → **Plugins** (admin) — list, enable/disable, Rescan.

**HTTP API** (admin-only), for tooling/automation:

| Method & path | Action |
|---|---|
| `GET /api/plugins` | List plugins with manifest + status. |
| `POST /api/plugins/<id>/enable` | Enable + load live. |
| `POST /api/plugins/<id>/disable` | Disable + unload live. |
| `POST /api/plugins/<id>/reload` | Re-read + reload. |
| `POST /api/plugins/rescan` | Pick up newly added plugins. |

Set `ODYSSEUS_PLUGINS_DIR` to load plugins from a custom directory.

## Distributing a plugin

A plugin is just a folder, so it ships as its **own git repo** (the
[Cloudflare Tunnel plugin](https://github.com/kanaru-dev/odysseus-plugin-cloudflare-tunnel)
is the reference — DokuWiki-InfoBox style). Core stays small; you release on your
own cadence without waiting on an upstream merge.

**Publish a release.** Zip the plugin's files *at the archive root* (`plugin.py`
+ assets — no wrapping folder, though a single top-level dir is auto-flattened on
install) and attach it to a tagged GitHub release, with its `sha256`:

```sh
zip -j my_plugin-1.0.0.zip plugin.py README.md LICENSE
sha256sum my_plugin-1.0.0.zip
gh release create v1.0.0 my_plugin-1.0.0.zip
```

**List it in the depot.** Open a PR to the main repo adding an entry to
[`plugins/registry.json`](registry.json) — `id`, `name`, `version`, `download`
(the release URL), and `sha256` (verified before install). Once merged, anyone
sees it under **Settings → Plugins → Browse** and installs with one click
(download → verify → extract into `plugins/<id>/` → enable).

**Other install paths.** Users can also **Add registry…** in the Browse tab to
point at your own `registry.json` (https, or http to loopback), **Install from
URL…** with a direct release zip, or just drop the folder into `plugins/` by hand.

Declare external needs in `requires` (for humans); if you import a pip package,
document it in your README. Keep secrets out of the plugin; read them from
`ctx.data_dir` config or Odysseus settings.

## Troubleshooting

- **Plugin not listed?** It needs `plugins/<id>/plugin.py` (or `<id>_plugin.py`).
  Hit **Rescan**.
- **Status “error”?** The panel shows the traceback tail; full traceback is in the
  server log (`plugin.<id>` logger). Common causes: a bad import or an exception in
  `setup`.
- **Route 401/redirect?** That's expected — routes require login. Use a session
  cookie / API token, and `require_admin` only where intended.
- **Disable didn't remove my route?** Mount routers via `ctx.add_router` (not
  `ctx.app.include_router`) so the manager can track + remove them.
