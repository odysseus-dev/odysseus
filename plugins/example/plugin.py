"""Example Odysseus plugin — a copy-me template.

Shows what a feature plugin commonly does: mount an HTTP route, run a background
service that's cleaned up on disable, and serve a small UI page that matches the
interface (via the `ui` manifest field + the shared theme layer). Delete what you
don't need.

Drop this folder in `plugins/`, restart (or hit Settings -> Plugins -> Rescan),
and it appears with a toggle (and an "Open" button, thanks to `ui`). See
`plugins/GUIDE.md` for conventions, theming, and gotchas.
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

PLUGIN = {
    "name": "Example",
    "version": "1.0.0",
    "author": "Odysseus",
    "description": "Copy-me template: a route, a background service, and a themed UI page.",
    "category": "Examples",
    "permission": "admin",
    # `ui.open` gives the plugin an "Open" button on its Settings -> Plugins card.
    "ui": {"open": "/api/plugins/example/app", "label": "Open"},
}

# Back-to-the-interface chevron for the themed header.
_CHEVRON = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" '
            'stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>')


def _app_html(nonce: str) -> str:
    # Link the shared theme layer (served from /static) so the page picks up the
    # user's active theme + customization before first paint — no flash. Then use
    # the od-* component classes. Inline scripts need the request CSP nonce.
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Example</title>
<link rel="stylesheet" href="/static/plugin-theme.css">
<script src="/static/js/plugin-theme.js"></script>
</head><body>
<header class="od-header">
  <a class="brand" href="/" title="Back to Odysseus">{_CHEVRON}<span>Odysseus</span></a>
  <span class="od-title">Example</span>
</header>
<div class="od-wrap">
  <h1>Example plugin</h1>
  <div class="od-card">
    <button id="hi" class="od-btn">Say hello</button>
    <div id="out" class="muted" style="margin-top:10px">click the button…</div>
  </div>
</div>
<script nonce="{nonce}">
document.getElementById("hi").onclick = async () => {{
  const out = document.getElementById("out");
  try {{
    const r = await fetch("/api/plugins/example/hello", {{ credentials: "same-origin" }});
    out.textContent = (await r.json()).message;
  }} catch (e) {{ out.innerHTML = '<span class="warn">' + e.message + '</span>'; }}
}};
</script>
</body></html>"""


def setup(ctx):
    # 1) Mount a route. It's auto-removed when the plugin is disabled.
    router = APIRouter(prefix="/api/plugins/example", tags=["plugin:example"])

    @router.get("/hello")
    async def hello(request: Request):
        # Requires a logged-in session (Odysseus auth). Add
        #   from core.middleware import require_admin; require_admin(request)
        # to make it admin-only.
        return {"plugin": "example", "message": "Hello from a drop-in plugin!"}

    # 2) A themed UI page (the "Open" button targets this).
    @router.get("/app")
    async def app_page(request: Request):
        return HTMLResponse(_app_html(getattr(request.state, "csp_nonce", "")))

    ctx.add_router(router)

    # 3) Register a background service. start() runs now; stop() runs on
    #    disable/shutdown. Replace with real work (a thread, a poller, …).
    def start():
        ctx.logger.info("example service started")

    def stop():
        ctx.logger.info("example service stopped")

    ctx.add_service(start=start, stop=stop)

    ctx.logger.info("example plugin ready  ->  /api/plugins/example/app")


# Optional. Most plugins don't need this — add_service(stop=) and on_teardown()
# already cover cleanup. Defined here just to show the hook exists.
def teardown(ctx):
    ctx.logger.info("example plugin torn down")
