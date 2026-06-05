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
    print("  odysseus_api.py cookbook tasks", file=sys.stderr)
    print("  odysseus_api.py cookbook servers", file=sys.stderr)
    print("  odysseus_api.py cookbook cached [HOST]", file=sys.stderr)
    print("  odysseus_api.py cookbook presets", file=sys.stderr)
    print("  odysseus_api.py cookbook output SESSION_ID [tail]", file=sys.stderr)
    print("  odysseus_api.py cookbook serve REPO_ID 'CMD' [REMOTE_HOST]", file=sys.stderr)
    print("  odysseus_api.py cookbook preset NAME", file=sys.stderr)
    print("  odysseus_api.py cookbook adopt SESSION_ID MODEL [HOST] [PORT]", file=sys.stderr)
    print("  odysseus_api.py cookbook stop SESSION_ID", file=sys.stderr)
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
    elif command == "cookbook":
        if len(sys.argv) < 3:
            return _usage()
        action = sys.argv[2].lower()
        if action == "tasks":
            method = "GET"
            path = "/api/codex/cookbook/tasks"
            body = None
        elif action == "servers":
            method = "GET"
            path = "/api/codex/cookbook/servers"
            body = None
        elif action == "output" and len(sys.argv) >= 4:
            method = "GET"
            sid = sys.argv[3]
            tail = sys.argv[4] if len(sys.argv) >= 5 else "400"
            path = f"/api/codex/cookbook/output/{sid}?tail={tail}"
            body = None
        elif action == "cached":
            method = "GET"
            if len(sys.argv) >= 4:
                from urllib.parse import quote
                path = f"/api/codex/cookbook/cached?host={quote(sys.argv[3])}"
            else:
                path = "/api/codex/cookbook/cached"
            body = None
        elif action == "presets":
            method = "GET"
            path = "/api/codex/cookbook/presets"
            body = None
        elif action == "preset" and len(sys.argv) >= 4:
            from urllib.parse import quote
            method = "POST"
            path = f"/api/codex/cookbook/preset/{quote(sys.argv[3])}"
            body = None
        elif action == "adopt" and len(sys.argv) >= 5:
            method = "POST"
            path = "/api/codex/cookbook/adopt"
            payload = {"tmux_session": sys.argv[3], "model": sys.argv[4]}
            if len(sys.argv) >= 6: payload["host"] = sys.argv[5]
            if len(sys.argv) >= 7: payload["port"] = int(sys.argv[6])
            body = json.dumps(payload)
        elif action == "serve" and len(sys.argv) >= 5:
            method = "POST"
            path = "/api/codex/cookbook/serve"
            payload = {"repo_id": sys.argv[3], "cmd": sys.argv[4]}
            if len(sys.argv) >= 6:
                payload["remote_host"] = sys.argv[5]
            body = json.dumps(payload)
        elif action == "stop" and len(sys.argv) >= 4:
            method = "POST"
            path = f"/api/codex/cookbook/stop/{sys.argv[3]}"
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
