#!/usr/bin/env python3
"""Small Odysseus scoped API helper for Codex terminal sessions."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def _usage() -> int:
    print("usage:", file=sys.stderr)
    print("  odysseus_api.py capabilities", file=sys.stderr)
    print("  odysseus_api.py todos list", file=sys.stderr)
    print("  odysseus_api.py todos add TITLE", file=sys.stderr)
    print("  odysseus_api.py emails list [limit]", file=sys.stderr)
    print("  odysseus_api.py emails read UID", file=sys.stderr)
    print("  odysseus_api.py METHOD /api/codex/path [json-body]", file=sys.stderr)
    return 2


def _config() -> tuple[str, str] | None:
    base_url = os.environ.get("ODYSSEUS_URL", "").strip().rstrip("/")
    token = os.environ.get("ODYSSEUS_API_TOKEN", "").strip()
    missing = []
    if not base_url:
        missing.append("ODYSSEUS_URL")
    if not token:
        missing.append("ODYSSEUS_API_TOKEN")
    if missing:
        print(f"missing {', '.join(missing)}; create a Codex Agent token in Odysseus Settings", file=sys.stderr)
        return None
    return base_url, token


def main() -> int:
    if len(sys.argv) < 2:
        return _usage()

    command = sys.argv[1].lower()
    if command == "capabilities":
        method = "GET"
        path = "/api/codex/capabilities"
        body = None
    elif command == "todos":
        if len(sys.argv) < 3:
            return _usage()
        action = sys.argv[2].lower()
        path = "/api/codex/todos"
        if action == "list":
            method = "GET"
            body = None
        elif action == "add" and len(sys.argv) >= 4:
            method = "POST"
            body = json.dumps({"action": "add", "title": " ".join(sys.argv[3:])})
        else:
            return _usage()
    elif command == "emails":
        if len(sys.argv) < 3:
            return _usage()
        action = sys.argv[2].lower()
        if action == "list":
            method = "GET"
            limit = sys.argv[3] if len(sys.argv) >= 4 else "10"
            path = f"/api/codex/emails?folder=INBOX&limit={limit}&offset=0&filter=all"
            body = None
        elif action == "read" and len(sys.argv) >= 4:
            method = "GET"
            path = f"/api/codex/emails/{sys.argv[3]}"
            body = None
        else:
            return _usage()
    else:
        if len(sys.argv) < 3:
            return _usage()
        method = sys.argv[1].upper()
        path = sys.argv[2]
        body = sys.argv[3] if len(sys.argv) > 3 else None

    if not path.startswith("/"):
        path = "/" + path
    if not path.startswith("/api/codex/"):
        print("refusing non-/api/codex path; use scoped Odysseus integration endpoints only", file=sys.stderr)
        return 2

    config = _config()
    if config is None:
        return 2
    base_url, token = config

    data = None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    if body is not None:
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            print(f"invalid json body: {exc}", file=sys.stderr)
            return 2
        data = json.dumps(parsed).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(base_url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            print(resp.read().decode("utf-8"))
            return 0
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        print(text or f"HTTP {exc.code}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
