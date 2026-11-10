"""Ithaca anchor — local-instance readiness / integrity self-check.

Beyond ``/api/health``'s liveness ping, this confirms the self-hosted instance is
whole and at home: the database is reachable, the data directory is present and
writable, and storage is local-first. Served by ``GET /api/ready`` and suitable
for an orchestrator readiness probe (200 only when every critical check passes).
"""

import logging
import os
import uuid
from datetime import datetime
from typing import Dict

logger = logging.getLogger(__name__)


def check_readiness() -> Dict[str, object]:
    """Run the readiness checks and return a JSON-serialisable report.

    ``ready`` is True only when every critical check (database, data_dir) passes.
    Harness deployments can also require semantic tool selection by setting
    ``ODYSSEUS_REQUIRE_TOOL_INDEX_READY=1``.
    ``local_first`` is informational — a remote database is a valid deployment, so
    it never fails readiness, it only reports whether storage stays on this host.
    """
    from core.constants import APP_VERSION, DATA_DIR
    from core.database import DATABASE_URL, engine
    from sqlalchemy import text as sql_text

    checks: Dict[str, Dict[str, object]] = {}

    # Database reachable — the simplest honest probe that the engine is live.
    try:
        with engine.connect() as conn:
            conn.execute(sql_text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as e:
        # The raw driver error can carry the DB host/user/path; keep it in the
        # server log and give the client only the exception type.
        logger.warning("Readiness database check failed: %s", e)
        checks["database"] = {"ok": False, "error_type": type(e).__name__}

    # Data directory present and writable — home must be able to hold its own data.
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        probe = os.path.join(DATA_DIR, f".ready_probe_{uuid.uuid4().hex}")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        checks["data_dir"] = {"ok": True, "path": DATA_DIR}
    except Exception as e:
        logger.warning("Readiness data_dir check failed: %s", e)
        checks["data_dir"] = {"ok": False, "error_type": type(e).__name__}

    # Local-first: storage stays on the home machine (informational, never fatal).
    local_first = (
        DATABASE_URL.startswith("sqlite")
        or "localhost" in DATABASE_URL
        or "127.0.0.1" in DATABASE_URL
    )
    checks["local_first"] = {"ok": True, "local": local_first}

    # ToolIndex is visible on every readiness response but only gates startup
    # when the deployment requires the semantic agent surface. Product installs
    # can remain available with deterministic tool-selection fallback.
    require_tool_index = str(
        os.environ.get("ODYSSEUS_REQUIRE_TOOL_INDEX_READY", "")
    ).strip().lower() in {"1", "true", "yes", "on"}
    try:
        from src.tool_index import get_tool_index_status, tool_index_prewarm_enabled

        tool_index = get_tool_index_status()
        tool_index["prewarm_enabled"] = tool_index_prewarm_enabled()
    except Exception as e:
        tool_index = {
            "state": "unavailable",
            "ready": False,
            "error_type": type(e).__name__,
            "prewarm_enabled": False,
        }
    tool_index["ok"] = bool(tool_index.get("ready"))
    tool_index["critical"] = require_tool_index
    checks["tool_index"] = tool_index

    critical_names = ["database", "data_dir"]
    if require_tool_index:
        critical_names.append("tool_index")
    ready = all(bool(checks[name].get("ok")) for name in critical_names)
    return {
        "ready": ready,
        "version": APP_VERSION,
        "checks": checks,
        "timestamp": datetime.utcnow().isoformat(),
    }
