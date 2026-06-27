#!/usr/bin/env python3
"""Validate .env settings for multi-machine Odysseus (Tailscale + Radicale)."""

from __future__ import annotations

import argparse
import ipaddress
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]

_TAILSCALE_NETWORK = ipaddress.ip_network("100.64.0.0/10")


@dataclass(frozen=True)
class EnvCheck:
    name: str
    ok: bool
    detail: str
    required: bool = True


def is_tailscale_ip(value: str) -> bool:
    try:
        addr = ipaddress.ip_address(value.strip())
    except ValueError:
        return False
    return addr in _TAILSCALE_NETWORK


def extract_host(raw_url: str) -> str | None:
    parsed = urlparse((raw_url or "").strip())
    return parsed.hostname


def parse_llm_hosts(raw: str) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _host_needs_private_caldav_flag(host: str | None) -> bool:
    if not host:
        return False
    if is_tailscale_ip(host):
        return True
    try:
        addr = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return addr.is_private


def check_multi_machine_env(env: Mapping[str, str]) -> list[EnvCheck]:
    checks: list[EnvCheck] = []

    llm_hosts = parse_llm_hosts(env.get("LLM_HOSTS", ""))
    checks.append(
        EnvCheck(
            name="llm_hosts",
            ok=bool(llm_hosts),
            detail="LLM_HOSTS is set for remote model discovery"
            if llm_hosts
            else "LLM_HOSTS is empty — add Tailscale hostnames of other machines (recommended)",
            required=False,
        )
    )

    inference_set = any(
        (env.get(key) or "").strip()
        for key in ("LM_STUDIO_URL", "OLLAMA_BASE_URL", "OLLAMA_URL")
    )
    checks.append(
        EnvCheck(
            name="local_inference",
            ok=inference_set,
            detail="Host inference URL configured (LM_STUDIO_URL or OLLAMA_BASE_URL)"
            if inference_set
            else "Set LM_STUDIO_URL or OLLAMA_BASE_URL for Docker/native host inference",
            required=False,
        )
    )

    radicale_url = (env.get("RADICALE_URL") or env.get("CALDAV_URL") or "").strip()
    host = extract_host(radicale_url)
    needs_private = _host_needs_private_caldav_flag(host)
    private_ok = _truthy(env.get("ODYSSEUS_ALLOW_PRIVATE_CALDAV"))
    if needs_private:
        checks.append(
            EnvCheck(
                name="private_caldav",
                ok=private_ok,
                detail="ODYSSEUS_ALLOW_PRIVATE_CALDAV=1 (required for Tailscale/private Radicale URLs)"
                if private_ok
                else "Private CalDAV host detected — set ODYSSEUS_ALLOW_PRIVATE_CALDAV=1",
                required=True,
            )
        )
    else:
        checks.append(
            EnvCheck(
                name="private_caldav",
                ok=True,
                detail="No private/Tailscale CalDAV URL in env (configure in Settings if needed)",
                required=False,
            )
        )

    ntfy_base = (env.get("NTFY_BASE_URL") or "").strip()
    ntfy_host = extract_host(ntfy_base)
    if ntfy_host and is_tailscale_ip(ntfy_host):
        bind = (env.get("NTFY_BIND") or "").strip()
        checks.append(
            EnvCheck(
                name="ntfy_tailscale",
                ok=bind.startswith("100."),
                detail=f"NTFY_BIND should be your Tailscale IP when NTFY_BASE_URL uses {ntfy_host}"
                if not bind.startswith("100.")
                else f"NTFY_BIND matches Tailscale pattern ({bind})",
                required=False,
            )
        )

    return checks


def load_dotenv(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.is_file():
        return data
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        data[key.strip()] = value.strip()
    return data


def run_checks(env_path: Path = REPO_ROOT / ".env") -> int:
    env = load_dotenv(env_path)
    checks = check_multi_machine_env(env)
    failed_required = False
    for check in checks:
        status = "OK" if check.ok else ("WARN" if not check.required else "FAIL")
        print(f"[{status}] {check.name}: {check.detail}")
        if check.required and not check.ok:
            failed_required = True
    return 1 if failed_required else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Odysseus multi-machine .env settings")
    parser.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env")
    args = parser.parse_args(argv)
    return run_checks(args.env_file)


if __name__ == "__main__":
    raise SystemExit(main())
