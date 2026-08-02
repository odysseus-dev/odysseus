"""Authenticated, metadata-only boundary for a local PDV adapter."""

import hashlib
import json
import os
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
import httpx

from core.middleware import require_admin
from src.runtime_paths import get_app_root


SNAPSHOT_NAME = "PDV_UPSTREAM_SNAPSHOT.json"
SOURCE_ARCHIVE_RELATIVE = Path("data/pdv-integration-v1/source/odysseus-corresponding-source.zip")
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


async def _default_integration_probe(execution_os_url: str, adapter_key: str) -> Dict[str, bool]:
    """Prove both the live governed bridge and its registered MCP child."""
    from src.tool_utils import get_mcp_manager

    manager = get_mcp_manager()
    status = manager.get_server_status("pdv_control") if manager is not None else {}
    mcp_connected = status.get("status") == "connected" and int(status.get("tool_count") or 0) >= 7
    async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.0)) as client:
        response = await client.post(
            f"{execution_os_url.rstrip('/')}/v1/integrations/odysseus/mcp/invoke",
            headers={"X-PDV-Odysseus-Key": adapter_key},
            json={"server_id": "pdv-control", "tool_name": "health.status", "arguments": {}},
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    provenance = payload.get("provenance") if isinstance(payload, dict) else None
    bridge_verified = bool(
        response.status_code == 200
        and payload.get("allowed") is True
        and isinstance(provenance, dict)
        and provenance.get("source_system") == "pdv-execution-os"
        and provenance.get("tool_name") == "health.status"
    )
    return {
        "executionOsReachable": bridge_verified,
        "pdvControlMcpConnected": mcp_connected,
        "pdvControlBridgeVerified": bridge_verified,
    }


def _require_pdv_reader(request: Request) -> None:
    """Require an admin session or a narrowly scoped, admin-owned API token."""
    if not getattr(request.state, "api_token", False):
        require_admin(request)
        return
    owner = getattr(request.state, "api_token_owner", None)
    scopes = set(getattr(request.state, "api_token_scopes", ()) or ())
    auth_manager = getattr(request.app.state, "auth_manager", None)
    if not owner or "pdv:read" not in scopes or not auth_manager or not auth_manager.is_admin(owner):
        raise HTTPException(403, "PDV owner token with pdv:read scope required")


def _source_archive(root: Path) -> tuple[Path, Dict[str, object]]:
    archive = (root / SOURCE_ARCHIVE_RELATIVE).resolve()
    archive.relative_to(root)
    receipt_path = archive.with_suffix(archive.suffix + ".json")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        expected = receipt["archiveSha256"]
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        raise HTTPException(503, "Corresponding source archive unavailable")
    if not isinstance(expected, str) or expected != actual:
        raise HTTPException(503, "Corresponding source archive verification failed")
    return archive, receipt


def _load_source_metadata(repository_root: Path) -> Dict[str, object]:
    path = repository_root / SNAPSHOT_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    allowed = {
        "canonicalRepository",
        "upstreamCommit",
        "upstreamBranch",
        "integrationBranch",
        "license",
    }
    return {key: value for key, value in payload.items() if key in allowed}


def _default_runtime_state() -> Dict[str, object]:
    """Return non-secret configured model metadata; run fields stay unknown.

    Odysseus has task-specific run records, but no single process-wide current
    run or correlation ID. Reporting UNKNOWN/null is more truthful than choosing
    an unrelated task run.
    """
    provider = None
    model = None
    try:
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import _detect_provider

        endpoint, model, _headers = resolve_endpoint("default")
        if endpoint:
            provider = _detect_provider(endpoint) or None
    except Exception:
        pass
    return {
        "provider": provider,
        "model": model,
        "currentRunStatus": "UNKNOWN",
        "taskCorrelationId": None,
        "failureMessage": None,
    }


def _default_capability_state(request: Request, ready: bool) -> Dict[str, str]:
    core_state = "AVAILABLE" if ready else "DEGRADED"
    email_state = "AUTH_REQUIRED"
    try:
        from core.database import EmailAccount, get_db_session

        owner = getattr(request.state, "api_token_owner", None) or getattr(request.state, "current_user", None)
        with get_db_session() as db:
            query = db.query(EmailAccount)
            if owner:
                query = query.filter(EmailAccount.owner == owner)
            if query.first() is not None:
                email_state = core_state
    except Exception:
        pass
    return {
        "chat": core_state,
        "agents": core_state,
        "mcp": core_state,
        "memory": core_state,
        "research": core_state,
        "documents": core_state,
        "notes": core_state,
        "tasks": core_state,
        "scheduler": core_state,
        "providers": core_state,
        "history": core_state,
        "calendar": core_state,
        "email": email_state,
    }


def setup_pdv_routes(
    repository_root: Optional[Path] = None,
    readiness_checker: Optional[Callable[[], Dict[str, object]]] = None,
    runtime_state_provider: Optional[Callable[[], Dict[str, object]]] = None,
    capability_state_provider: Optional[Callable[[Request, bool], Dict[str, str]]] = None,
    integration_probe: Optional[Callable[[str, str], Awaitable[Dict[str, bool]]]] = None,
) -> APIRouter:
    """Create the PDV adapter router without adding an authentication bypass."""
    router = APIRouter(prefix="/api/pdv", tags=["pdv"])
    root = Path(repository_root or get_app_root()).resolve()

    if readiness_checker is None:
        from src.readiness import check_readiness

        readiness_checker = check_readiness
    runtime_state_provider = runtime_state_provider or _default_runtime_state
    capability_state_provider = capability_state_provider or _default_capability_state
    integration_probe = integration_probe or _default_integration_probe

    @router.get("/health")
    async def pdv_health(request: Request):
        _require_pdv_reader(request)
        readiness = readiness_checker()
        bind_host = (os.getenv("APP_BIND") or os.getenv("ODYSSEUS_HOST") or "127.0.0.1").strip().lower()
        loopback = bind_host in _LOOPBACK_HOSTS
        key_ref = (os.getenv("ODYSSEUS_PDV_ADAPTER_KEY_FILE") or "").strip()
        key_readable = False
        key_text = ""
        if key_ref and Path(key_ref).is_file():
            try:
                key_text = Path(key_ref).read_text(encoding="ascii").strip()
                key_readable = len(key_text) == 64 and all(character in "0123456789abcdef" for character in key_text)
            except (OSError, UnicodeError):
                key_readable = False
        source_metadata = _load_source_metadata(root)
        source_ok = bool(source_metadata.get("upstreamCommit") and source_metadata.get("license"))
        source_archive_ok = True
        source_archive_sha256 = None
        try:
            _archive, source_receipt = _source_archive(root)
            source_archive_sha256 = source_receipt.get("archiveSha256")
        except HTTPException:
            source_archive_ok = False
        try:
            raw_runtime = runtime_state_provider() or {}
        except Exception:
            raw_runtime = {}
        runtime = {
            "provider": raw_runtime.get("provider"),
            "model": raw_runtime.get("model"),
            "currentRunStatus": raw_runtime.get("currentRunStatus") or "UNKNOWN",
            "taskCorrelationId": raw_runtime.get("taskCorrelationId"),
            "failureMessage": raw_runtime.get("failureMessage"),
        }
        execution_os_url = (os.getenv("PDV_EXECUTION_OS_URL") or "").strip()
        parsed_execution_os = urlparse(execution_os_url)
        execution_os_configured = (
            parsed_execution_os.scheme == "http"
            and parsed_execution_os.hostname in _LOOPBACK_HOSTS
            and parsed_execution_os.port is not None
            and not parsed_execution_os.username
            and not parsed_execution_os.password
        )
        integration_state = {
            "executionOsReachable": False,
            "pdvControlMcpConnected": False,
            "pdvControlBridgeVerified": False,
        }
        if execution_os_configured and key_readable:
            try:
                observed = await integration_probe(execution_os_url, key_text)
                integration_state = {
                    name: observed.get(name) is True for name in integration_state
                }
            except Exception:
                pass
        provider_guard_required = os.getenv("PDV_PROVIDER_GUARD_REQUIRED", "false").lower() == "true"
        key_acl_restricted = os.getenv("PDV_ADAPTER_KEY_ACL_VERIFIED", "false").lower() == "true"
        ready = bool(readiness.get("ready")) and loopback and key_readable and key_acl_restricted and execution_os_configured and all(integration_state.values()) and provider_guard_required and source_ok and source_archive_ok
        capabilities = capability_state_provider(request, ready)
        payload = {
            **readiness,
            "ready": ready,
            "boundary": {
                "loopback": loopback,
                "adapterKeyReferenceConfigured": bool(key_ref),
                "adapterKeyReadable": key_readable,
                "adapterKeyAclRestricted": key_acl_restricted,
                "executionOsConfigured": execution_os_configured,
                **integration_state,
                "providerGuardRequired": provider_guard_required,
            },
            "sourceMetadataAvailable": source_ok,
            "sourceArchiveAvailable": source_archive_ok,
            "sourceArchiveSha256": source_archive_sha256,
            "runtime": runtime,
            "capabilities": capabilities,
        }
        return JSONResponse(status_code=200 if ready else 503, content=payload)

    @router.get("/source")
    async def pdv_source(request: Request):
        _require_pdv_reader(request)
        metadata = _load_source_metadata(root)
        if not metadata:
            return JSONResponse(
                status_code=503,
                content={"available": False, "correspondingSourceRequired": True},
            )
        archive_available = True
        archive_sha256 = None
        try:
            _archive, receipt = _source_archive(root)
            archive_sha256 = receipt.get("archiveSha256")
        except HTTPException:
            archive_available = False
        return {
            **metadata,
            "available": True,
            "modifiedSource": True,
            "correspondingSourceRequired": True,
            "sourceEndpoint": "/api/pdv/source",
            "sourceArchiveEndpoint": "/api/pdv/source/archive",
            "sourceArchiveAvailable": archive_available,
            "sourceArchiveSha256": archive_sha256,
        }

    @router.get("/source/archive")
    async def pdv_source_archive(request: Request):
        _require_pdv_reader(request)
        archive, receipt = _source_archive(root)
        return FileResponse(
            archive,
            media_type="application/zip",
            filename="odysseus-corresponding-source.zip",
            headers={"Digest": f"sha-256={receipt['archiveSha256']}"},
        )

    return router
