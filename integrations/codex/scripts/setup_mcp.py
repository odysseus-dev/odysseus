#!/usr/bin/env python3
"""Register Odysseus as a native Codex MCP server."""

import json
import os
import shutil
import subprocess
from urllib.parse import urlsplit


SERVER_NAME = "odysseus"
TOKEN_ENV_VAR = "ODYSSEUS_API_TOKEN"


def _endpoint(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("ODYSSEUS_URL must be an absolute http(s) URL")
    return f"{base}/api/codex/mcp/"


def _run(runner, args):
    return runner(args, capture_output=True, text=True, check=False)


def configure(*, base_url: str, runner=subprocess.run) -> str:
    url = _endpoint(base_url)
    current = _run(runner, ["codex", "mcp", "get", SERVER_NAME, "--json"])
    if current.returncode == 0:
        try:
            transport = json.loads(current.stdout).get("transport") or {}
        except (json.JSONDecodeError, AttributeError):
            transport = {}
        if (
            transport.get("type") in {"streamable_http", "http"}
            and transport.get("url") == url
            and transport.get("bearer_token_env_var") == TOKEN_ENV_VAR
        ):
            return "already configured"
        removed = _run(runner, ["codex", "mcp", "remove", SERVER_NAME])
        if removed.returncode != 0:
            raise RuntimeError("Unable to replace the existing Odysseus MCP configuration")

    added = _run(runner, [
        "codex",
        "mcp",
        "add",
        SERVER_NAME,
        "--url",
        url,
        "--bearer-token-env-var",
        TOKEN_ENV_VAR,
    ])
    if added.returncode != 0:
        raise RuntimeError("Unable to register the Odysseus MCP server")
    return "configured"


def main() -> int:
    if not shutil.which("codex"):
        raise SystemExit("codex executable not found on PATH")
    base_url = os.environ.get("ODYSSEUS_URL", "")
    if not base_url:
        raise SystemExit("ODYSSEUS_URL is required")
    if not os.environ.get(TOKEN_ENV_VAR):
        raise SystemExit(f"{TOKEN_ENV_VAR} is required")
    try:
        status = configure(base_url=base_url)
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Odysseus MCP {status}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
