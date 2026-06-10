"""Keep the LM Studio (Windows host) endpoint in sync with the current host IP.

Under WSL2 NAT networking the Windows host is reached via the default-gateway
IP of eth0, which can change between reboots. This module probes the likely
host addresses, finds a reachable LM Studio server and upserts a stable
ModelEndpoint row pointing at it. When the IP changed, full chat URLs stored
on scheduled tasks (task.endpoint_url embeds the host) are rewritten too, so
unfinished tasks keep working after a reboot.

Called from app startup (non-critical, best-effort) and from
scripts/sync_lmstudio_endpoint.py for manual runs.
"""

import logging
import os
import re
import subprocess
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

LMSTUDIO_PORT = int(os.environ.get("LMSTUDIO_PORT", "1234"))
ENDPOINT_ID = "lmstudio-host"
ENDPOINT_NAME = "LM Studio (Windows host)"
PROBE_TIMEOUT = 5.0


def _candidate_ips() -> list[str]:
    """Host IPs to probe, most likely first.

    Order: default gateway (WSL2 NAT = Windows vEthernet adapter),
    resolv.conf nameserver (older WSL setups), localhost (mirrored mode).
    """
    ips: list[str] = []
    try:
        out = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        m = re.search(r"default via (\S+)", out)
        if m:
            ips.append(m.group(1))
    except Exception:
        pass
    try:
        with open("/etc/resolv.conf") as f:
            for line in f:
                if line.startswith("nameserver"):
                    ips.append(line.split()[1])
    except Exception:
        pass
    ips.append("localhost")
    seen: set[str] = set()
    return [ip for ip in ips if not (ip in seen or seen.add(ip))]


async def _probe(ip: str) -> bool:
    url = f"http://{ip}:{LMSTUDIO_PORT}/v1/models"
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            resp = await client.get(url)
        return resp.status_code == 200
    except Exception:
        return False


async def find_lmstudio_base() -> Optional[str]:
    """Return the reachable LM Studio base URL (".../v1") or None."""
    from core.database import SessionLocal, ModelEndpoint

    candidates = _candidate_ips()
    # The host stored in the DB row may still be valid — try it too (last,
    # after the freshly-detected addresses).
    db = SessionLocal()
    try:
        ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == ENDPOINT_ID).first()
        if ep and ep.base_url:
            m = re.match(r"https?://([^:/]+)", ep.base_url)
            if m and m.group(1) not in candidates:
                candidates.append(m.group(1))
    finally:
        db.close()

    for ip in candidates:
        if await _probe(ip):
            return f"http://{ip}:{LMSTUDIO_PORT}/v1"
    return None


async def sync_lmstudio_endpoint() -> Optional[str]:
    """Upsert the LM Studio endpoint row to the currently reachable host IP.

    Returns the active base URL, or None when LM Studio is unreachable.
    supports_tools is set to False only on row creation: qwen3.5/3.6 tunes
    emit a single native tool_call token and stop when given tool schemas,
    so the fenced-block text path must be used. A later manual toggle in the
    endpoint settings UI is preserved on subsequent syncs.
    """
    from core.database import SessionLocal, ModelEndpoint, ScheduledTask

    base = await find_lmstudio_base()
    if not base:
        return None

    db = SessionLocal()
    try:
        ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == ENDPOINT_ID).first()
        if ep is None:
            ep = ModelEndpoint(
                id=ENDPOINT_ID,
                name=ENDPOINT_NAME,
                base_url=base,
                is_enabled=True,
                model_type="llm",
                supports_tools=False,
                owner=None,
            )
            db.add(ep)
            db.commit()
            logger.info(f"LM Studio endpoint created: {base}")
        elif ep.base_url != base:
            old_base = (ep.base_url or "").rstrip("/")
            ep.base_url = base
            # Task endpoint_url stores the full chat URL with the old host
            # baked in — rewrite so resumed tasks hit the live server.
            rewritten = 0
            if old_base:
                tasks = db.query(ScheduledTask).filter(
                    ScheduledTask.endpoint_url.like(old_base + "%")
                ).all()
                for t in tasks:
                    t.endpoint_url = base + t.endpoint_url[len(old_base):].removeprefix("/v1")
                    rewritten += 1
            db.commit()
            logger.info(
                f"LM Studio endpoint moved {old_base} -> {base}"
                + (f", rewrote {rewritten} task URL(s)" if rewritten else "")
            )
    finally:
        db.close()
    return base
