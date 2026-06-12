#!/usr/bin/env python3
"""Static guard: the `sandbox` service must keep its isolation flags.

Parses a rendered compose file (`docker compose config > file`) and fails if the
sandbox service has dropped any hardening control or gained a sensitive mount /
secret env. This catches config regressions even when the runtime Docker test is
skipped. Usage: check_sandbox_compose.py <rendered-compose.yml>
"""
from __future__ import annotations

import sys

import yaml

SENSITIVE_MOUNT_HINTS = ("/app/data", "data", ".ssh", ".env", "secret", "huggingface", "/app/logs")
SECRET_ENV_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}")
    sys.exit(1)


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: check_sandbox_compose.py <rendered-compose.yml>")
    with open(sys.argv[1]) as f:
        cfg = yaml.safe_load(f)

    services = cfg.get("services", {})
    sb = services.get("sandbox")
    if not sb:
        fail("no `sandbox` service in compose")

    # 1. No network.
    if str(sb.get("network_mode", "")).lower() != "none":
        fail(f"sandbox network_mode must be 'none', got {sb.get('network_mode')!r}")

    # 2. Read-only rootfs.
    if sb.get("read_only") is not True:
        fail("sandbox read_only must be true")

    # 3. All capabilities dropped.
    cap_drop = sb.get("cap_drop") or []
    if "ALL" not in [str(c).upper() for c in cap_drop]:
        fail(f"sandbox cap_drop must include ALL, got {cap_drop!r}")

    # 4. no-new-privileges.
    secopt = [str(s).lower() for s in (sb.get("security_opt") or [])]
    if not any("no-new-privileges:true" in s for s in secopt):
        fail(f"sandbox security_opt must include no-new-privileges:true, got {secopt!r}")

    # 5. Only the IPC volume — no data / ssh / secret mounts.
    for vol in sb.get("volumes") or []:
        # Rendered form is a dict {source, target, type, ...} or a "src:dst" string.
        src = vol.get("source", "") if isinstance(vol, dict) else str(vol).split(":")[0]
        tgt = vol.get("target", "") if isinstance(vol, dict) else (str(vol).split(":") + [""])[1]
        if src == "sandbox-ipc" or tgt == "/ipc":
            continue
        low = f"{src} {tgt}".lower()
        if any(h in low for h in SENSITIVE_MOUNT_HINTS):
            fail(f"sandbox has a sensitive mount: {vol!r}")
        fail(f"sandbox has an unexpected mount (only sandbox-ipc allowed): {vol!r}")

    # 6. No secret-looking env vars handed to the sandbox.
    env = sb.get("environment") or {}
    names = env.keys() if isinstance(env, dict) else [str(e).split("=")[0] for e in env]
    for name in names:
        if any(h in name.upper() for h in SECRET_ENV_HINTS):
            fail(f"sandbox must not receive secret env {name!r}")

    # 7. The app must be wired to dispatch into the sandbox.
    ody = services.get("odysseus", {})
    ody_env = ody.get("environment") or {}
    ody_names = ody_env.keys() if isinstance(ody_env, dict) else [str(e).split("=")[0] for e in ody_env]
    if "SANDBOX_RUNNER_SOCK" not in ody_names:
        fail("odysseus must set SANDBOX_RUNNER_SOCK to use the sandbox")

    print("[PASS] sandbox isolation flags intact (network none, read-only, cap_drop ALL, "
          "no-new-privileges, no sensitive mounts/secrets, app wired)")


if __name__ == "__main__":
    main()
