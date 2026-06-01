"""Example Odysseus plugin — a minimal, copy-me template.

Shows the two things a feature plugin commonly does: mount an HTTP route and run
a background service that's cleaned up on disable. Delete what you don't need.

Drop this folder in `plugins/`, restart (or hit Settings -> Plugins -> Rescan),
and it appears with a toggle.
"""
from fastapi import APIRouter, Request

PLUGIN = {
    "name": "Example",
    "version": "1.0.0",
    "author": "Odysseus",
    "description": "Minimal plugin template: a route + a background service.",
    "category": "Examples",
    "permission": "admin",
}


def setup(ctx):
    # 1) Mount a route. It's auto-removed when the plugin is disabled.
    router = APIRouter(prefix="/api/plugins/example", tags=["plugin:example"])

    @router.get("/hello")
    async def hello(request: Request):
        # Requires a logged-in session (Odysseus auth). Add
        #   from core.middleware import require_admin; require_admin(request)
        # to make it admin-only.
        return {"plugin": "example", "message": "Hello from a drop-in plugin!"}

    ctx.add_router(router)

    # 2) Register a background service. start() runs now; stop() runs on
    #    disable/shutdown. Replace with real work (a thread, a poller, …).
    def start():
        ctx.logger.info("example service started")

    def stop():
        ctx.logger.info("example service stopped")

    ctx.add_service(start=start, stop=stop)

    ctx.logger.info("example plugin ready  ->  GET /api/plugins/example/hello")


# Optional. Most plugins don't need this — add_service(stop=) and on_teardown()
# already cover cleanup. Defined here just to show the hook exists.
def teardown(ctx):
    ctx.logger.info("example plugin torn down")
