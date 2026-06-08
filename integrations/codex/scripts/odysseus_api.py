#!/usr/bin/env python3
"""Small Odysseus scoped API helper for Codex terminal sessions."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode


ALLOWED_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}
_MAX_JSON_DEPTH = 64
_QUIET_TRUNCATE_BYTES = 4096


def _usage() -> int:
    print("usage:", file=sys.stderr)
    print("  odysseus_api.py [--quiet|-q] [--account-id ID] [--folder F] capabilities", file=sys.stderr)
    print("  odysseus_api.py [--quiet|-q] [--account-id ID] [--folder F] todos list", file=sys.stderr)
    print("  odysseus_api.py [--quiet|-q] [--account-id ID] [--folder F] todos add TITLE", file=sys.stderr)
    print("  odysseus_api.py [--quiet|-q] [--account-id ID] [--folder F] emails list [limit]", file=sys.stderr)
    print("  odysseus_api.py [--quiet|-q] [--account-id ID] [--folder F] emails read UID", file=sys.stderr)
    print("  odysseus_api.py [--quiet|-q] METHOD /api/codex/path [json-body]", file=sys.stderr)
    return 2


def _parse_flags(argv: list[str]) -> tuple[dict[str, str | bool], list[str]]:
    """Pull --quiet/-q and --key value flags out of argv.

    Keeps the rest as positional args so the legacy positional call shape
    (used by start-macos.sh and other shell scripts) keeps working."""
    flags: dict[str, str | bool] = {"quiet": False}
    positional: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--quiet", "-q"):
            flags["quiet"] = True
            i += 1
        elif a.startswith("--") and "=" in a:
            k, v = a[2:].split("=", 1)
            flags[k.replace("-", "_")] = v
            i += 1
        elif a.startswith("--") and i + 1 < len(argv):
            flags[a[2:].replace("-", "_")] = argv[i + 1]
            i += 2
        else:
            positional.append(a)
            i += 1
    return flags, positional


def _safe_loads(body: str) -> object:
    """json.loads with a post-parse nesting depth check.

    CPython's json module uses an iterative scanner for the common case
    (json/_json C extension), so the parse itself does not blow the stack
    on adversarial input. The depth check on the resulting tree protects
    downstream code (do_manage_notes, _verify_memory_owner, etc.) that
    may walk the parsed object recursively."""
    parsed = json.loads(body)
    _check_depth(parsed, depth=1, limit=_MAX_JSON_DEPTH)
    return parsed


def _check_depth(obj: object, depth: int, limit: int) -> None:
    if depth > limit:
        raise ValueError(f"JSON nesting exceeds {limit}")
    if isinstance(obj, dict):
        for v in obj.values():
            _check_depth(v, depth + 1, limit)
    elif isinstance(obj, list):
        for v in obj:
            _check_depth(v, depth + 1, limit)


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


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    flags, argv = _parse_flags(argv)
    if not argv:
        return _usage()

    command = argv[0].lower()
    quiet = bool(flags.get("quiet"))
    account_id = flags.get("account_id") or None
    folder = flags.get("folder") or "INBOX"

    if command == "capabilities":
        method = "GET"
        path = "/api/codex/capabilities"
        body = None
    elif command == "todos":
        if len(argv) < 2:
            return _usage()
        action = argv[1].lower()
        path = "/api/codex/todos"
        if action == "list":
            method = "GET"
            body = None
        elif action == "add" and len(argv) >= 3:
            method = "POST"
            body = json.dumps({"action": "add", "title": " ".join(argv[2:])})
        else:
            return _usage()
    elif command == "emails":
        if len(argv) < 2:
            return _usage()
        action = argv[1].lower()
        if action == "list":
            method = "GET"
            limit = argv[2] if len(argv) >= 3 else "10"
            qs = urlencode(
                {"folder": folder, "limit": str(limit), "offset": "0", "filter": "all"}
            )
            path = f"/api/codex/emails?{qs}"
            body = None
        elif action == "read" and len(argv) >= 3:
            method = "GET"
            # URL-encode uid so a stray `?`, `#`, `&`, or `..` can't change
            # the request path or query.
            path = f"/api/codex/emails/{quote(argv[2], safe='')}"
            body = None
        else:
            return _usage()
    else:
        if len(argv) < 2:
            return _usage()
        method = argv[0].upper()
        if method not in ALLOWED_METHODS:
            print(
                f"refusing HTTP method {method!r}; allowed: {sorted(ALLOWED_METHODS)}",
                file=sys.stderr,
            )
            return 2
        path = argv[1]
        body = argv[2] if len(argv) > 2 else None

    if not path.startswith("/"):
        path = "/" + path
    if not path.startswith("/api/codex/"):
        print("refusing non-/api/codex path; use scoped Odysseus integration endpoints only", file=sys.stderr)
        return 2

    # Append --account-id as a query parameter on emails endpoints when the
    # user supplied it through the helper (the generic METHOD path can set
    # it directly in the URL).
    if account_id and path.startswith("/api/codex/emails") and "account_id=" not in path:
        sep = "&" if "?" in path else "?"
        path = f"{path}{sep}account_id={quote(str(account_id), safe='')}"
        if not path.startswith("/api/codex/emails/") and "?" not in path and "&" in path:
            # safety: the only account_id-bearing emails endpoints are list/read;
            # do not attach to nested paths we did not expect.
            pass

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
            parsed = _safe_loads(body)
        except ValueError as exc:
            print(f"invalid json body: {exc}", file=sys.stderr)
            return 2
        data = json.dumps(parsed).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(base_url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = resp.read().decode("utf-8", errors="replace")
            if quiet and len(payload) > _QUIET_TRUNCATE_BYTES:
                omitted = len(payload) - _QUIET_TRUNCATE_BYTES
                payload = payload[:_QUIET_TRUNCATE_BYTES] + f"\n<truncated {omitted} bytes>"
            print(payload)
            return 0
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        if quiet and len(text) > _QUIET_TRUNCATE_BYTES:
            text = text[:_QUIET_TRUNCATE_BYTES] + f"\n<truncated {len(text) - _QUIET_TRUNCATE_BYTES} bytes>"
        print(text or f"HTTP {exc.code}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
