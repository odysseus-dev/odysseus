"""Homelab operations routes (Phase 1: Read-only)."""

import json
import logging
import os
import subprocess
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, HTTPException, Request

from src.auth_helpers import require_user

logger = logging.getLogger(__name__)

HOMELAB_READ_SCOPES = {"homelab:read"}

def _scope_owner(request: Request, allowed: set[str]) -> str:
    if getattr(request.state, "api_token", False):
        scopes = set(getattr(request.state, "api_token_scopes", []) or [])
        if not scopes.intersection(allowed):
            required = " or ".join(sorted(allowed))
            raise HTTPException(403, f"API token missing required scope: {required}")
        owner = getattr(request.state, "api_token_owner", None)
        if not owner:
            raise HTTPException(403, "API token has no owner")
        return owner
    return require_user(request)

def _load_services() -> List[Dict[str, Any]]:
    config_path = os.path.join("config", "homelab_services.json")
    if not os.path.exists(config_path):
        return []
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("services", [])
    except Exception as e:
        logger.error(f"Failed to load homelab services config: {e}")
        return []

async def _check_service_health(service: dict) -> dict:
    status = {"name": service.get("name"), "status": "unknown"}
    
    # Check container if configured
    container = service.get("container")
    if container:
        try:
            # shell=False to prevent arbitrary shell execution
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Status}}", container],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0:
                status["container_status"] = result.stdout.strip()
                if status["container_status"] == "running":
                    status["status"] = "ok"
                else:
                    status["status"] = "error"
            else:
                status["container_status"] = "not_found"
                status["status"] = "error"
        except Exception as e:
            status["container_status"] = "check_failed"
            status["status"] = "error"

    # Check HTTP if configured
    health_url = service.get("health_url") or service.get("url")
    if health_url:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(health_url)
                status["http_status"] = resp.status_code
                if resp.status_code < 400:
                    status["status"] = "ok" if status.get("status") != "error" else "degraded"
                else:
                    status["status"] = "error"
        except Exception as e:
            status["http_status"] = "unreachable"
            status["status"] = "error"

    if status["status"] == "unknown" and not container and not health_url:
         status["status"] = "ok"

    return status

from src.event_store import EventStore

EVENTS_WRITE_SCOPES = {"events:write"}

def _has_scope(request: Request, allowed: set[str]) -> bool:
    if not getattr(request.state, "api_token", False):
        return True
    scopes = set(getattr(request.state, "api_token_scopes", []) or [])
    return bool(scopes.intersection(allowed))

def setup_homelab_routes() -> APIRouter:
    router = APIRouter(prefix="/api/homelab", tags=["homelab"])

    @router.get("/services")
    async def list_services(request: Request):
        _scope_owner(request, HOMELAB_READ_SCOPES)
        services = _load_services()
        return {"status": "ok", "services": services}

    @router.get("/services/{name}")
    async def get_service(request: Request, name: str):
        _scope_owner(request, HOMELAB_READ_SCOPES)
        services = _load_services()
        for srv in services:
            if srv.get("name") == name:
                return {"status": "ok", "service": srv}
        raise HTTPException(404, "Service not found")

    @router.get("/health")
    async def homelab_health(request: Request, record_events: bool = False):
        owner = _scope_owner(request, HOMELAB_READ_SCOPES)
        
        if record_events and not _has_scope(request, EVENTS_WRITE_SCOPES):
            raise HTTPException(403, "API token missing required scope: events:write")
            
        services = _load_services()
        
        health_results = []
        event_store = EventStore() if record_events else None

        for srv in services:
            res = await _check_service_health(srv)
            health_results.append(res)
            
            if record_events and res.get("status") in ("error", "degraded"):
                dedupe_key = f"homelab:{srv['name']}:health"
                severity = "critical" if res.get("status") == "error" else "warning"
                    
                title = f"{srv.get('display_name', srv['name'])} is {res['status']}"
                summary = f"Container: {res.get('container_status', 'N/A')}, HTTP: {res.get('http_status', 'N/A')}"
                
                try:
                    event_store.record_event(
                        source="homelab_health",
                        service=srv["name"],
                        severity=severity,
                        title=title,
                        summary=summary,
                        dedupe_key=dedupe_key,
                        owner=owner,
                        metadata=res
                    )
                except Exception as e:
                    raise HTTPException(500, f"Failed to record event: {e}")
            
        overall_status = "ok"
        if any(r.get("status") == "error" for r in health_results):
            overall_status = "error"
        elif any(r.get("status") == "degraded" for r in health_results):
            overall_status = "degraded"
            
        return {
            "status": overall_status,
            "services": health_results
        }

    return router
