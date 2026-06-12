#!/usr/bin/env python3
"""Runtime acceptance checks for the Tier-B sandbox runner.

Assumes a runner is already listening on $SANDBOX_RUNNER_SOCK (the CI job starts
one with the hardened `docker run` flags). Drives it through src.sandbox_client
and asserts the isolation guarantees actually hold at runtime — exec works, but
network egress, the app data volume, and env secrets are all unreachable from
inside the sandbox. Exits non-zero on any failure.

See docs/security/bash-python-sandbox-scoping.md (Tier B, §5 acceptance).
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.sandbox_client import run_in_sandbox  # noqa: E402

_RESULTS: list[tuple[str, bool, str]] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


async def main() -> None:
    r = await run_in_sandbox("bash", "echo hello-ci")
    _check("bash exec works", r.get("exit_code") == 0 and "hello-ci" in r.get("stdout", ""),
           repr(r.get("stdout")))

    r = await run_in_sandbox("python", "print(6*7)")
    _check("python exec works", r.get("stdout", "").strip() == "42", repr(r.get("stdout")))

    # Network egress must be blocked (network_mode:none). urllib (stdlib) avoids
    # depending on curl being installed in the image.
    net = (
        "import urllib.request\n"
        "try:\n"
        "    urllib.request.urlopen('https://example.com', timeout=5)\n"
        "    print('OPEN')\n"
        "except Exception:\n"
        "    print('BLOCKED')\n"
    )
    r = await run_in_sandbox("python", net, timeout=20)
    _check("network egress blocked", r.get("stdout", "").strip() == "BLOCKED", repr(r.get("stdout")))

    # The app data volume must NOT be mounted in the sandbox.
    r = await run_in_sandbox("bash", "ls /app/data >/dev/null 2>&1 && echo PRESENT || echo ABSENT")
    _check("app data volume not mounted", r.get("stdout", "").strip() == "ABSENT", repr(r.get("stdout")))

    # A secret in the RUNNER's env must not reach the executed child (env scrub).
    r = await run_in_sandbox("python", "import os; print(os.environ.get('FAKE_SECRET_TOKEN', 'ABSENT'))")
    _check("env secret not leaked to child", r.get("stdout", "").strip() == "ABSENT", repr(r.get("stdout")))

    # The runner must execute as a non-root user (defense-in-depth).
    r = await run_in_sandbox("bash", "id -u")
    _check("runner is non-root", r.get("stdout", "").strip() not in ("0", ""), repr(r.get("stdout")))


if __name__ == "__main__":
    asyncio.run(main())
    failed = [n for n, ok, _ in _RESULTS if not ok]
    if failed:
        print(f"\nFAILED: {len(failed)} check(s): {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)
    print(f"\nAll {len(_RESULTS)} sandbox acceptance checks passed.")
