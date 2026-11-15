"""Diagnostics routes — /api/db/stats, /api/rag/stats, /api/test/youtube, /api/test-research."""

import logging
import os
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, Form, Request

from services.youtube.youtube_handler import extract_youtube_id, extract_transcript_async
from core.constants import DEFAULT_HOST, DATA_DIR
from core.middleware import require_admin

logger = logging.getLogger(__name__)



def audit_administrative_security(
    *,
    auth_is_disabled: bool,
    admin_user_count: int,
    total_user_count: int,
    api_tokens: list[dict],
    mcp_servers: list[dict],
    webhooks: list[dict],
    admin_only_task_actions: list[str],
    shell_tool_enabled: bool,
) -> Dict[str, Any]:
    """Evaluate privileged capabilities, token posture, and sensitive integration boundaries."""
    findings: list[dict] = []

    if auth_is_disabled:
        findings.append({
            "severity": "high",
            "code": "auth_disabled",
            "message": "Authentication is disabled (AUTH_ENABLED=false); administrative endpoints do not require login.",
            "remediation": "Enable AUTH_ENABLED=true whenever Odysseus is reachable beyond localhost.",
        })
    elif admin_user_count == 0:
        findings.append({
            "severity": "high",
            "code": "no_admin_account",
            "message": "Authentication is enabled but zero admin accounts exist.",
            "remediation": "Bootstrap or promote an administrator account so privileged management remains accessible.",
        })

    active_tokens = [t for t in api_tokens if not t.get("revoked")]
    non_expiring_tokens = [t for t in active_tokens if not t.get("expires_at")]
    wildcard_tokens = [
        t for t in active_tokens
        if "*" in (t.get("scopes") or []) or "admin" in (t.get("scopes") or [])
    ]

    if non_expiring_tokens:
        findings.append({
            "severity": "medium",
            "code": "non_expiring_api_tokens",
            "message": f"{len(non_expiring_tokens)} active API token(s) have no expiration timestamp.",
            "remediation": "Set explicit expires_at lifetimes on delegated API tokens and rotate long-lived keys.",
        })
    if wildcard_tokens:
        findings.append({
            "severity": "medium",
            "code": "broad_scope_api_tokens",
            "message": f"{len(wildcard_tokens)} active API token(s) carry wildcard or admin scopes.",
            "remediation": "Scope delegated API tokens strictly to required domains (e.g., chat, documents:read).",
        })

    enabled_mcp = [m for m in mcp_servers if m.get("enabled", True)]
    stdio_mcp = [m for m in enabled_mcp if (m.get("transport") or "stdio").lower() == "stdio"]
    if stdio_mcp:
        findings.append({
            "severity": "info",
            "code": "stdio_mcp_servers_active",
            "message": f"{len(stdio_mcp)} enabled MCP server(s) execute local subprocesses over stdio.",
            "remediation": "Ensure stdio MCP server commands and arguments are admin-reviewed and confined.",
        })

    enabled_webhooks = [w for w in webhooks if w.get("enabled", True)]
    unsigned_webhooks = [w for w in enabled_webhooks if not (w.get("secret") or "").strip()]
    if unsigned_webhooks:
        findings.append({
            "severity": "medium",
            "code": "unsigned_webhooks",
            "message": f"{len(unsigned_webhooks)} enabled webhook(s) do not configure an HMAC signing secret.",
            "remediation": "Configure a signing secret on outbound/inbound webhooks to prevent spoofed triggers.",
        })

    high_count = sum(1 for f in findings if f["severity"] == "high")
    medium_count = sum(1 for f in findings if f["severity"] == "medium")

    return {
        "ok": high_count == 0,
        "summary": {
            "high_findings": high_count,
            "medium_findings": medium_count,
            "total_findings": len(findings),
        },
        "boundaries": {
            "auth": {
                "auth_enabled": not auth_is_disabled,
                "total_users": total_user_count,
                "admin_users": admin_user_count,
            },
            "delegated_tokens": {
                "active_count": len(active_tokens),
                "non_expiring_count": len(non_expiring_tokens),
                "broad_scope_count": len(wildcard_tokens),
            },
            "privileged_execution": {
                "shell_tool_enabled": bool(shell_tool_enabled),
                "admin_only_task_actions": sorted(admin_only_task_actions),
            },
            "integrations": {
                "mcp_enabled_count": len(enabled_mcp),
                "mcp_stdio_count": len(stdio_mcp),
                "webhooks_enabled_count": len(enabled_webhooks),
                "webhooks_unsigned_count": len(unsigned_webhooks),
            },
        },
        "findings": findings,
    }


def collect_administrative_security_audit() -> Dict[str, Any]:
    """Gather live auth, token, task-policy, MCP, and webhook posture from persistent storage."""
    from src.owner_identity import auth_disabled
    from src.task_action_policy import ADMIN_ONLY_TASK_ACTIONS

    auth_is_disabled = bool(auth_disabled())
    admin_users = 0
    total_users = 0
    api_tokens: list[dict] = []
    mcp_servers: list[dict] = []
    webhooks: list[dict] = []

    try:
        from core.database import SessionLocal, User, ApiToken
        db = SessionLocal()
        try:
            users = db.query(User).all()
            total_users = len(users)
            admin_users = sum(1 for u in users if getattr(u, "is_admin", False))
            for tok in db.query(ApiToken).all():
                raw_scopes = getattr(tok, "scopes", None) or []
                if isinstance(raw_scopes, str):
                    import json as _json
                    try:
                        raw_scopes = _json.loads(raw_scopes)
                    except Exception:
                        raw_scopes = [raw_scopes]
                api_tokens.append({
                    "id": getattr(tok, "id", None),
                    "revoked": bool(getattr(tok, "revoked_at", None)),
                    "expires_at": getattr(tok, "expires_at", None),
                    "scopes": list(raw_scopes) if isinstance(raw_scopes, (list, tuple, set)) else [],
                })
        finally:
            db.close()
    except Exception as e:
        logger.debug("Security audit DB inspection skipped: %s", e)

    try:
        import json as _json
        for fname, target in (("mcp_servers.json", mcp_servers), ("webhooks.json", webhooks)):
            fpath = os.path.join(DATA_DIR, fname)
            if os.path.isfile(fpath):
                loaded = _json.loads(open(fpath, encoding="utf-8").read())
                if isinstance(loaded, list):
                    target.extend(item for item in loaded if isinstance(item, dict))
                elif isinstance(loaded, dict):
                    items = loaded.get("servers") or loaded.get("webhooks") or list(loaded.values())
                    if isinstance(items, list):
                        target.extend(item for item in items if isinstance(item, dict))
    except Exception as e:
        logger.debug("Security audit integration file inspection skipped: %s", e)

    shell_enabled = os.getenv("ENABLE_SHELL_TOOL", "").strip().lower() in {"1", "true", "yes"} or (
        os.getenv("SHELL_ENABLED", "").strip().lower() in {"1", "true", "yes"}
    )

    return audit_administrative_security(
        auth_is_disabled=auth_is_disabled,
        admin_user_count=admin_users,
        total_user_count=total_users,
        api_tokens=api_tokens,
        mcp_servers=mcp_servers,
        webhooks=webhooks,
        admin_only_task_actions=list(ADMIN_ONLY_TASK_ACTIONS),
        shell_tool_enabled=shell_enabled,
    )


def setup_diagnostics_routes(
    rag_manager,
    rag_available: bool,
    research_handler,
    memory_vector=None,
) -> APIRouter:
    router = APIRouter(tags=["diagnostics"])

    @router.get("/api/diagnostics/services")
    async def get_service_health(request: Request) -> Dict[str, Any]:
        """Consolidated degraded-state report for ChromaDB, SearXNG, email,
        ntfy, and provider endpoints. Non-intrusive probes — safe to poll."""
        require_admin(request)
        from src.service_health import collect_service_health
        return await collect_service_health(rag_manager, memory_vector)


    @router.get("/api/diagnostics/security-audit")
    async def get_security_audit(request: Request) -> Dict[str, Any]:
        """Audit privileged capabilities, token lifetimes, and sensitive integration boundaries."""
        require_admin(request)
        return collect_administrative_security_audit()

    @router.get("/api/diagnostics/logs")
    async def get_diagnostics_logs(request: Request, limit: int = 200) -> Dict[str, Any]:
        require_admin(request)
        limit = max(1, min(limit, 1000))
        try:
            log_file = os.path.join(DATA_DIR, "logs", "app.log")
            if not os.path.exists(log_file):
                return {"status": "success", "logs": []}

            # Safe tail read of the log file (max 5MB via rotation)
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            tail_lines = lines[-limit:] if len(lines) > limit else lines
            tail_lines = [line.rstrip('\r\n') for line in tail_lines]

            return {
                "status": "success",
                "logs": tail_lines
            }
        except Exception as e:
            logger.error(f"Diagnostics logs retrieval error: {e}")
            raise HTTPException(500, f"Failed to retrieve logs: {str(e)}")

    @router.get("/api/db/stats")
    async def get_database_stats(request: Request) -> Dict[str, Any]:
        require_admin(request)
        try:
            from core.database import get_detailed_stats
            return get_detailed_stats()
        except Exception as e:
            logger.error(f"DB stats error: {e}")
            raise HTTPException(500, "Failed to retrieve database statistics")

    @router.get("/api/rag/stats")
    async def get_rag_stats(request: Request) -> Dict[str, Any]:
        require_admin(request)
        if rag_available and rag_manager:
            return rag_manager.get_stats()
        return {"error": "RAG system not available"}

    @router.get("/api/test/youtube")
    async def test_youtube(request: Request, url: str) -> Dict[str, Any]:
        require_admin(request)
        try:
            video_id = extract_youtube_id(url)
            if not video_id:
                return {"error": "Invalid YouTube URL"}

            data = await extract_transcript_async(url, video_id)
            return {
                "video_id": video_id,
                "transcript_success": data.get("success", False),
                "transcript_length": len(data.get("transcript", "")) if data.get("success") else 0,
                "transcript_preview": (data.get("transcript", "")[:500] + "...")
                    if data.get("success") and len(data.get("transcript", "")) > 500
                    else data.get("transcript", ""),
                "error": data.get("error") if not data.get("success") else None,
            }
        except Exception as e:
            return {"error": str(e)}

    @router.post("/api/test-research")
    async def test_research(request: Request, query: str = Form("What is machine learning?")) -> Dict[str, Any]:
        require_admin(request)
        try:
            endpoint = f"http://{DEFAULT_HOST}:8000/v1/chat/completions"
            model = "gpt-oss-120b"
            result = await research_handler.call_research_service(query, endpoint, model)
            return {
                "status": "success",
                "query": query,
                "result_preview": result[:200] + "..." if len(result) > 200 else result,
                "result_length": len(result),
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "query": query}

    return router
