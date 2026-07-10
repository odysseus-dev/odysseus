"""Local-instance readiness and integrity self-check."""

import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen


def _truthy(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _http_probe(url: str, timeout: float = 2.0) -> Dict[str, object]:
    """GET an operator-configured dependency URL with a short timeout."""
    if not url:
        return {"ok": False, "configured": False, "error": "not configured"}
    try:
        request = Request(url, headers={"User-Agent": "odysseus-readiness/1"})
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            status = int(getattr(response, "status", 200))
        return {"ok": 200 <= status < 400, "configured": True, "status": status, "url": url}
    except Exception as exc:
        return {"ok": False, "configured": True, "error": str(exc), "url": url}


def check_readiness(
    *,
    skills_manager: Optional[Any] = None,
    task_scheduler: Optional[Any] = None,
    memory_vector: Optional[Any] = None,
) -> Dict[str, object]:
    """Return honest generic or Misumi deployment readiness.

    Generic installs retain the database/data-directory checks. With
    ``MISUMI_REQUIRED=1``, the household root, model backend, skills manager,
    and in-process scheduler are also critical.
    """
    from core.constants import APP_VERSION, DATA_DIR
    from core.database import DATABASE_URL, engine
    from sqlalchemy import text as sql_text

    checks: Dict[str, Dict[str, object]] = {}

    try:
        with engine.connect() as conn:
            conn.execute(sql_text("SELECT 1"))
        checks["database"] = {"ok": True, "critical": True}
    except Exception as exc:
        checks["database"] = {"ok": False, "critical": True, "error": str(exc)}

    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        probe = os.path.join(DATA_DIR, f".ready_probe_{uuid.uuid4().hex}")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
        checks["data_dir"] = {"ok": True, "critical": True, "path": DATA_DIR}
    except Exception as exc:
        checks["data_dir"] = {"ok": False, "critical": True, "error": str(exc)}

    local_first = (
        DATABASE_URL.startswith("sqlite")
        or "localhost" in DATABASE_URL
        or "127.0.0.1" in DATABASE_URL
    )
    checks["local_first"] = {"ok": True, "critical": False, "local": local_first}

    bind = (os.getenv("APP_BIND") or os.getenv("ODYSSEUS_HOST") or "127.0.0.1").strip()
    auth_enabled = _truthy(os.getenv("AUTH_ENABLED"), default=True)
    network_exposed = bind not in {"127.0.0.1", "::1", "localhost"}
    auth_ok = auth_enabled or not network_exposed
    checks["auth"] = {
        "ok": auth_ok,
        "critical": True,
        "enabled": auth_enabled,
        "bind": bind,
        **({"error": "authentication is required for a non-loopback bind"} if not auth_ok else {}),
    }

    misumi_required = _truthy(os.getenv("MISUMI_REQUIRED"), default=False)
    household_value = (
        os.getenv("MISUMI_HOUSEHOLD_ROOT")
        or os.getenv("FLAT_KNOWLEDGEBASE_ROOT")
        or os.getenv("MISUMI_SOURCE_ROOT")
        or ""
    ).strip()
    household_path = Path(household_value).expanduser() if household_value else None
    household_ok = bool(household_path and household_path.is_dir())
    checks["household_repo"] = {
        "ok": household_ok,
        "critical": misumi_required,
        "configured": bool(household_path),
        "path": str(household_path) if household_path else None,
        **({"error": "configured household repository is not reachable"} if household_path and not household_ok else {}),
    }

    skills_ok = bool(skills_manager and Path(getattr(skills_manager, "skills_root", "")).is_dir())
    skill_count = None
    if skills_ok:
        try:
            skill_count = len(skills_manager.load_all())
        except Exception:
            skills_ok = False
    checks["skills"] = {
        "ok": skills_ok,
        "critical": misumi_required,
        "available": bool(skills_manager),
        "count": skill_count,
    }

    scheduler_enabled = _truthy(os.getenv("ODYSSEUS_INPROCESS_TASKS"), default=True)
    scheduler_running = bool(task_scheduler and getattr(task_scheduler, "_running", False))
    checks["task_scheduler"] = {
        "ok": scheduler_running if scheduler_enabled else True,
        "critical": misumi_required,
        "enabled": scheduler_enabled,
        "running": scheduler_running,
    }

    vector_present = memory_vector is not None
    vector_healthy = bool(vector_present and getattr(memory_vector, "healthy", False))
    checks["vector_memory"] = {
        "ok": vector_healthy if vector_present else True,
        "critical": False,
        "enabled": vector_present,
        "healthy": vector_healthy,
    }

    model_check = _http_probe((os.getenv("MISUMI_MODEL_HEALTH_URL") or "").strip())
    model_check["critical"] = misumi_required
    checks["model_backend"] = model_check

    interface_url = (os.getenv("MISUMI_INTERFACE_HEALTH_URL") or "").strip()
    interface_check = _http_probe(interface_url) if interface_url else {
        "ok": False,
        "configured": False,
        "error": "not configured",
    }
    interface_check["critical"] = False
    checks["misumi_interface"] = interface_check

    ready = all(bool(check.get("ok")) for check in checks.values() if check.get("critical"))
    return {
        "ready": ready,
        "version": APP_VERSION,
        "checks": checks,
        "timestamp": datetime.utcnow().isoformat(),
    }
