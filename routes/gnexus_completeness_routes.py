"""Routes for Juniperus / Gnexus Operations Console - completeness state.

Aggregates frontstage readiness, room availability, Ollama readiness, and the
latest governed-operation receipt into a single JSON state for the cockpit and
the verifier. Resilient: missing ledgers degrade to a clear status, never to an
endless loading state.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

try:
    from core.constants import BASE_DIR
except Exception:  # pragma: no cover
    BASE_DIR = str(Path(__file__).resolve().parents[1])

ROOMS = [
    "governance",
    "app-dock",
    "approval-desk",
    "interceptor",
    "diff-gate",
    "patch-apply",
    "verifier-loop",
    "operator-loop",
    "memory-routing",
    "live-control",
    "ollama-models",
]


def _base() -> Path:
    return Path(BASE_DIR)


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default
    return default


def _latest_receipt() -> Optional[Dict[str, Any]]:
    rdir = _base() / "data" / "gnexus" / "receipts"
    try:
        if not rdir.exists():
            return None
        files = sorted(rdir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files:
            data = _read_json(f, None)
            if isinstance(data, dict):
                data["_file"] = f.name
                return data
    except Exception:
        return None
    return None


def _room_pages() -> List[Dict[str, Any]]:
    sdir = _base() / "static" / "gnexus"
    out = []
    for slug in ROOMS:
        page = sdir / f"{slug}.html"
        out.append(
            {
                "slug": slug,
                "href": f"/gnexus/{slug}",
                "staticPageExists": page.exists(),
                # Server-side fallback guarantees a render even when False.
                "renderable": True,
            }
        )
    return out


def _completeness_state() -> Dict[str, Any]:
    data = _base() / "data" / "gnexus"
    ollama_reg = _read_json(data / "ollama" / "ollama-model-registry.json", {})
    smoke = _read_json(data / "ollama" / "ollama-smoke-test.json", {})
    operator = _read_json(data / "mission-control" / "operator-loop-state.json", {})
    proof = _read_json(data / "operator-loop" / "sandbox" / "proof-receipt.json", {})

    rooms = _room_pages()
    cockpit_exists = (_base() / "static" / "gnexus" / "index.html").exists()

    return {
        "system": "Juniperus",
        "title": "Gnexus Operations Console",
        "status": "completeness_state",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "cockpit": {"staticPageExists": cockpit_exists, "href": "/gnexus"},
        "rooms": rooms,
        "ollama": {
            "running": bool((ollama_reg.get("ollama") or {}).get("running")),
            "modelCount": ollama_reg.get("modelCount", 0),
            "endpointBaseUrl": (ollama_reg.get("endpoint") or {}).get("base_url"),
            "registeredInPicker": (ollama_reg.get("endpoint") or {}).get("registered_in_picker"),
            "smokeOk": bool(smoke.get("ok")),
        },
        "operatorLoop": {
            "hasState": bool(operator),
            "status": operator.get("status"),
        },
        "governedOperationProof": {
            "hasProof": bool(proof),
            "applied": proof.get("applyVerified"),
            "rolledBack": proof.get("rollbackVerified"),
            "status": proof.get("status"),
        },
        "latestReceipt": _latest_receipt(),
        "boundaries": {
            "humanApprovalRequired": True,
            "productionMutationLocked": True,
            "externalReads": False,
            "externalWrites": False,
            "connectorCalls": False,
            "secretsStored": False,
            "workspaceRoot": "C:\\Users\\iamcy\\CymaticsDev",
        },
    }


def setup_gnexus_completeness_routes() -> APIRouter:
    router = APIRouter(tags=["gnexus-completeness"])

    @router.get("/api/gnexus/completeness/state")
    async def completeness_state(request: Request):
        return JSONResponse(_completeness_state())

    return router
