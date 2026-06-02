"""Ithaca anchor — local-instance readiness / integrity self-check.

Beyond ``/api/health``'s liveness ping, this confirms the self-hosted instance is
whole and at home: the database is reachable, the data directory is present and
writable, and storage is local-first. Served by ``GET /api/ready`` and suitable
for an orchestrator readiness probe (200 only when every critical check passes).

Degraded-state probes (ChromaDB, SearXNG, email, ntfy) are also included as
informational checks: they never block readiness, but expose integration health
so operators can spot misconfiguration without digging through logs.
"""

import os
import socket
import uuid
from datetime import datetime
from typing import Dict


# ── Critical checks ──────────────────────────────────────────────────────────

def _check_database(engine, sql_text) -> Dict[str, object]:
    try:
        with engine.connect() as conn:
            conn.execute(sql_text("SELECT 1"))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _check_data_dir(data_dir: str) -> Dict[str, object]:
    try:
        os.makedirs(data_dir, exist_ok=True)
        probe = os.path.join(data_dir, f".ready_probe_{uuid.uuid4().hex}")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return {"ok": True, "path": data_dir}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _check_local_first(database_url: str) -> Dict[str, object]:
    local = (
        database_url.startswith("sqlite")
        or "localhost" in database_url
        or "127.0.0.1" in database_url
    )
    return {"ok": True, "local": local}


# ── Informational integration probes ─────────────────────────────────────────
# These never fail ``ready`` — they only surface misconfiguration so operators
# can see at a glance what is reachable without grepping through logs.

def _tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    """Return True if a TCP connection to host:port succeeds within timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_chromadb() -> Dict[str, object]:
    """Probe the ChromaDB HTTP service via a TCP connect + heartbeat."""
    host = os.getenv("CHROMADB_HOST", "localhost")
    port = int(os.getenv("CHROMADB_PORT", "8100"))
    if not _tcp_reachable(host, port):
        return {"ok": False, "error": f"TCP connect failed to {host}:{port}"}
    try:
        import chromadb
        client = chromadb.HttpClient(host=host, port=port)
        client.heartbeat()
        return {"ok": True, "host": host, "port": port}
    except ImportError:
        return {"ok": False, "error": "chromadb package not installed"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def probe_searxng() -> Dict[str, object]:
    """Probe the configured SearXNG instance with a lightweight HTTP HEAD."""
    try:
        import httpx
        from src.settings import get_setting
        from src.constants import SEARXNG_INSTANCE
        url = (get_setting("search_url") or "").strip().rstrip("/") or SEARXNG_INSTANCE
        resp = httpx.head(url, timeout=5.0, follow_redirects=True)
        return {"ok": resp.status_code < 500, "url": url, "status": resp.status_code}
    except ImportError:
        return {"ok": False, "error": "httpx package not installed"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def probe_email() -> Dict[str, object]:
    """Probe email reachability by TCP-connecting to each configured IMAP host.

    Does not authenticate — only checks that the IMAP port is open. Reports
    per-account results so operators can pinpoint which mailbox is broken.
    """
    try:
        from routes.email_helpers import _list_email_accounts
    except Exception as e:
        return {"ok": False, "error": f"could not load email config: {e}"}

    try:
        accounts = _list_email_accounts()
    except Exception as e:
        return {"ok": False, "error": f"_list_email_accounts failed: {e}"}

    if not accounts:
        return {"ok": True, "configured": False}

    results = []
    any_reachable = False
    for acct in accounts:
        host = acct.get("imap_host") or ""
        port = int(acct.get("imap_port") or 993)
        label = acct.get("imap_user") or acct.get("from_address") or host
        if not host:
            results.append({"account": label, "ok": False, "error": "no imap_host"})
            continue
        reachable = _tcp_reachable(host, port)
        if reachable:
            any_reachable = True
        results.append({"account": label, "host": host, "port": port, "ok": reachable})

    return {"ok": any_reachable, "configured": True, "accounts": results}


def probe_ntfy() -> Dict[str, object]:
    """Probe the configured ntfy server with a TCP connect.

    ntfy is found via the integrations file — only the first enabled ntfy
    entry is checked since they all point at the same server.
    """
    try:
        from src.integrations import load_integrations
        integrations = load_integrations()
    except Exception as e:
        return {"ok": False, "error": f"could not load integrations: {e}"}

    ntfy_intg = next(
        (i for i in integrations
         if (i.get("preset") == "ntfy" or (i.get("name") or "").lower() == "ntfy")
         and i.get("enabled", True)
         and i.get("base_url")),
        None,
    )
    if ntfy_intg is None:
        return {"ok": True, "configured": False}

    base_url = ntfy_intg["base_url"].rstrip("/")
    try:
        import httpx
        resp = httpx.head(base_url, timeout=5.0, follow_redirects=True)
        return {"ok": resp.status_code < 500, "url": base_url, "status": resp.status_code}
    except ImportError:
        return {"ok": False, "error": "httpx package not installed"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Public API ────────────────────────────────────────────────────────────────

def check_readiness() -> Dict[str, object]:
    """Run all readiness checks and return a JSON-serialisable report.

    ``ready`` is True only when every critical check (database, data_dir) passes.
    Informational checks (chromadb, searxng, email, ntfy) are always included but
    never affect ``ready`` — they surface integration health for operators.
    """
    from core.constants import APP_VERSION, DATA_DIR
    from core.database import DATABASE_URL, engine
    from sqlalchemy import text as sql_text

    checks: Dict[str, Dict[str, object]] = {}

    # ── Critical ──
    checks["database"]   = _check_database(engine, sql_text)
    checks["data_dir"]   = _check_data_dir(DATA_DIR)
    checks["local_first"] = _check_local_first(DATABASE_URL)

    # ── Informational ──
    checks["chromadb"] = probe_chromadb()
    checks["searxng"]  = probe_searxng()
    checks["email"]    = probe_email()
    checks["ntfy"]     = probe_ntfy()

    # Only critical checks gate readiness. Informational keys are excluded.
    _critical = {"database", "data_dir", "local_first"}
    ready = all(bool(checks[k].get("ok")) for k in _critical)

    return {
        "ready": ready,
        "version": APP_VERSION,
        "checks": checks,
        "timestamp": datetime.utcnow().isoformat(),
    }
