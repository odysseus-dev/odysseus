"""Honeypot decoy paths for the guard-core perimeter.

Registers a set of well-known scanner/bot probe paths that always return a
plausible 404. Their real value is upstream: with the perimeter enabled, the WAF
``recon`` / ``sensitive_file`` / ``cms_probing`` categories fire on these URL
paths and auto-ban the source IP per ``threat_ban_config`` once the perimeter is
in active (non-passive) mode. Registered only when the perimeter is enabled, so
Odysseus's default route surface is unchanged.

All decoys live outside the ``/api`` namespace to guarantee they can never shadow
a real Odysseus route.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

_DECOY_PATHS = [
    "/.env",
    "/.env.local",
    "/.env.production",
    "/.git/config",
    "/.aws/credentials",
    "/.ssh/id_rsa",
    "/.DS_Store",
    "/config.json",
    "/wp-login.php",
    "/wp-admin/admin-ajax.php",
    "/xmlrpc.php",
    "/phpmyadmin",
    "/server-status",
    "/actuator/health",
]


def setup_honeypot_routes(app: FastAPI) -> None:
    async def _trap() -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    for path in _DECOY_PATHS:
        app.add_api_route(path, _trap, methods=["GET"], include_in_schema=False)
