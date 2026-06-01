from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

try:
    from core.constants import BASE_DIR
except Exception:  # pragma: no cover
    BASE_DIR = Path(__file__).resolve().parents[1]

ROOMS = {
    "governance": "Governance",
    "app-dock": "App Dock",
    "approval-desk": "Approval Desk",
    "interceptor": "Shell/File Interceptor",
    "diff-gate": "Diff Gate",
    "patch-apply": "Patch Apply",
    "verifier-loop": "Verifier Loop",
    "operator-loop": "Operator Loop",
    "memory-routing": "Memory Routing",
    "live-control": "Live Control",
    "ollama-models": "Local Ollama Models",
}


def _base_dir() -> Path:
    return Path(BASE_DIR) if not isinstance(BASE_DIR, Path) else BASE_DIR


def _static_page(name: str) -> Path:
    return _base_dir() / "static" / "gnexus" / name


def _safe_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default
    return default


def _data_root() -> Path:
    return _base_dir() / "data" / "gnexus"


def _state_payload() -> Dict[str, Any]:
    data = _data_root()
    live = _safe_json(data / "mission-control" / "live-control-state.json", {})
    frontstage = _safe_json(data / "mission-control" / "frontstage-completeness-state.json", {})
    return {
        "system": "Juniperus",
        "title": "Gnexus Operations Console",
        "status": "frontstage_ready",
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "routes": [{"slug": slug, "title": title, "href": f"/gnexus/{slug}"} for slug, title in ROOMS.items()],
        "liveControl": live,
        "frontstage": frontstage,
        "boundaries": {
            "humanApprovalRequired": True,
            "productionMutationLocked": True,
            "externalReads": False,
            "externalWrites": False,
            "connectorCalls": False,
            "secretsStored": False,
        },
    }


def _fallback_cockpit_html() -> str:
    links = "".join(
        '<a class="card" href="/gnexus/{slug}"><h3>{title}</h3></a>'.format(slug=slug, title=title)
        for slug, title in ROOMS.items()
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Juniperus - Gnexus Operations Console</title>'
        '<link rel="stylesheet" href="/static/gnexus/gnexus-core.css"></head>'
        '<body><main class="wrap"><section class="top"><div>'
        '<div class="brand">Juniperus</div><h1 class="title">Gnexus Operations Console</h1>'
        '<p class="sub">Server-rendered fallback cockpit. Static cockpit page was not found, '
        'but the console is still navigable.</p></div>'
        '<div class="pill">Human approval required</div></section>'
        '<section class="grid">' + links + '</section>'
        '<div class="footer">Juniperus / Gnexus Operations Console - resilient fallback render.</div>'
        '</main></body></html>'
    )


def _fallback_room_html(slug: str, title: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>{title} - Juniperus</title>'
        '<link rel="stylesheet" href="/static/gnexus/gnexus-core.css"></head>'
        '<body><main class="wrap"><section class="top"><div>'
        '<div class="brand">Juniperus / Gnexus Operations Console</div>'
        '<h1 class="title">{title}</h1>'
        '<p class="sub">This room is reachable. Its static page was not found, so a usable '
        'fallback shell is rendered instead of an endless loading state.</p></div>'
        '<a class="pill" href="/gnexus">Back to cockpit</a></section>'
        '<section class="status"><div class="metric"><b>Room</b><span>{title}</span></div>'
        '<div class="metric"><b>State</b><span>Renderable fallback active</span></div>'
        '<div class="metric"><b>Approval</b><span>Required for mutation</span></div>'
        '<div class="metric"><b>Boundary</b><span>External writes disabled</span></div></section>'
        '<div class="footer">No endless loading. Slug: {slug}</div>'
        '</main></body></html>'
    ).format(title=title, slug=slug)


def setup_gnexus_frontstage_routes() -> APIRouter:
    router = APIRouter(tags=["gnexus-frontstage"])

    @router.get("/gnexus", include_in_schema=False)
    async def gnexus_home():
        page = _static_page("index.html")
        if not page.exists():
            # Resilient fallback: never leave the cockpit on a hard 404.
            return HTMLResponse(_fallback_cockpit_html(), media_type="text/html")
        return FileResponse(str(page), media_type="text/html")

    @router.get("/gnexus/{room}", include_in_schema=False)
    async def gnexus_room(room: str):
        if room not in ROOMS:
            raise HTTPException(status_code=404, detail="Unknown Gnexus room")
        page = _static_page(f"{room}.html")
        if not page.exists():
            # Resilient fallback: render a usable room shell instead of looping
            # on the cockpit (which would look like an endless redirect).
            return HTMLResponse(_fallback_room_html(room, ROOMS[room]), media_type="text/html")
        return FileResponse(str(page), media_type="text/html")

    @router.get("/api/gnexus/frontstage/state")
    async def gnexus_frontstage_state():
        return JSONResponse(_state_payload())

    return router
