import json as _json
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Blocklists for app_api — paths and method+path pairs the generic loopback
# tool refuses to call. Auth/token/user administration and host shell
# execution are too risky to route through an agent surface.
# ---------------------------------------------------------------------------

_APP_API_BLOCKLIST_PREFIXES = (
    "/api/auth",           # login/logout/password
    "/api/users",          # user CRUD
    "/api/tokens",         # api token mgmt
    "/api/admin",          # admin one-shots (wipe etc.)
    "/api/shell",          # host shell execution
    "/api/backup/restore", # destructive restore
)

# (method, prefix) pairs to refuse specifically.
_APP_API_BLOCKLIST_METHOD_PATH = (
    ("GET",    "/api/email/accounts"),
    ("POST",   "/api/cookbook/state"),
    ("DELETE", "/api/cookbook/state"),
    ("POST",   "/api/cookbook/packages/install"),
    ("POST",   "/api/cookbook/rebuild-engine"),
    ("POST",   "/api/cookbook/kill-pid"),
    ("POST",   "/api/model/download"),
    ("POST",   "/api/model/serve"),
    ("POST",   "/api/research/start"),
    ("POST",   "/api/notes"),
    ("PUT",    "/api/notes"),
    ("DELETE", "/api/notes"),
    ("POST",   "/api/calendar/events"),
    ("PUT",    "/api/calendar/events"),
    ("DELETE", "/api/calendar/events"),
)


class ApiCallTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        """Execute an API call to a registered integration."""
        from src.integrations import execute_api_call, load_integrations

        try:
            args = _json.loads(content)
        except _json.JSONDecodeError:
            # Try line-based format: integration\nmethod path\nbody
            lines = content.strip().split("\n")
            args = {"integration": lines[0].strip() if lines else ""}
            if len(lines) > 1:
                parts = lines[1].strip().split(" ", 1)
                args["method"] = parts[0] if parts else "GET"
                args["path"] = parts[1] if len(parts) > 1 else "/"
            if len(lines) > 2:
                try:
                    args["body"] = _json.loads("\n".join(lines[2:]))
                except _json.JSONDecodeError:
                    pass

        integration_name = args.get("integration", "")
        integrations = load_integrations()
        intg = next((i for i in integrations if i["id"] == integration_name
                     or i["name"].lower() == integration_name.lower()), None)
        if not intg:
            available = ", ".join(i["name"] for i in integrations if i.get("enabled", True))
            return {"error": f"No integration matching '{integration_name}'. Available: {available or 'none configured'}", "exit_code": 1}

        return await execute_api_call(
            intg["id"],
            args.get("method", "GET"),
            args.get("path", "/"),
            params=args.get("params"),
            body=args.get("body"),
            extra_headers=args.get("headers"),
        )


class AppApiTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        """Generic loopback to allowed internal Odysseus API endpoints. Lets the
        agent reach the full UI-button surface (cookbook, email, notes,
        calendar, skills, sessions, gallery, research, etc.) without us
        landing a named tool wrapper for every one.

        Args (JSON):
          action: "call" (default) | "endpoints"
          path:   "/api/cookbook/gpus"     # required for call
          method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" (default GET)
          body:   <object>                 # JSON body for POST/PUT/PATCH
          query:  <object>                 # querystring params

        The `endpoints` action returns the OpenAPI surface (method + path +
        summary) so the agent can discover what's reachable. A blocklist
        refuses sensitive auth/user/admin/shell paths and method-specific
        host-control routes to keep blast radius bounded.
        """
        import httpx
        from typing import Any, Dict, List
        from src.tool_implementations import (
            _parse_tool_args,
            _INTERNAL_BASE,
            _internal_headers,
        )

        owner = ctx.get("owner")
        try:
            args = _parse_tool_args(content) if content.strip() else {}
        except ValueError:
            return {"error": "Invalid JSON arguments", "exit_code": 1}

        action = (args.get("action") or "call").lower()
        base = _INTERNAL_BASE

        if action == "endpoints":
            # Fetch FastAPI's OpenAPI schema so the agent can discover any
            # endpoint without us pre-listing them. Filter by an optional
            # `filter` keyword (substring match on path or summary).
            kw = (args.get("filter") or "").lower()
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(f"{base}/openapi.json",
                                            headers=_internal_headers())
                    data = resp.json()
            except Exception as e:
                return {"error": f"OpenAPI fetch failed: {e}", "exit_code": 1}
            rows: List[Dict[str, Any]] = []
            for path, methods in (data.get("paths") or {}).items():
                if not isinstance(methods, dict):
                    continue
                if any(path.startswith(p) for p in _APP_API_BLOCKLIST_PREFIXES):
                    continue
                for method, op in methods.items():
                    if method.lower() not in ("get", "post", "put", "patch", "delete"):
                        continue
                    if any(method.upper() == m and path.startswith(p) for m, p in _APP_API_BLOCKLIST_METHOD_PATH):
                        continue
                    summary = (op or {}).get("summary") or (op or {}).get("description") or ""
                    if isinstance(summary, str):
                        summary = summary.strip().split("\n")[0][:140]
                    if kw and kw not in path.lower() and kw not in (summary or "").lower():
                        continue
                    rows.append({"method": method.upper(), "path": path, "summary": summary})
            rows.sort(key=lambda r: (r["path"], r["method"]))
            if not rows:
                return {"output": f"No endpoints match filter {kw!r}." if kw else "No endpoints found.", "exit_code": 0}
            lines = [f"{len(rows)} endpoint(s)" + (f" matching {kw!r}" if kw else "") + ":"]
            for r in rows[:200]:
                line = f"  {r['method']:6s} {r['path']}"
                if r["summary"]:
                    line += f"  — {r['summary']}"
                lines.append(line)
            if len(rows) > 200:
                lines.append(f"  ...({len(rows) - 200} more — filter to narrow)")
            return {"output": "\n".join(lines), "endpoints": rows, "exit_code": 0}

        # action == "call"
        path = args.get("path") or ""
        if not path:
            return {"error": "path is required (e.g. '/api/cookbook/gpus')", "exit_code": 1}
        if not path.startswith("/"):
            path = "/" + path
        if any(path.startswith(p) for p in _APP_API_BLOCKLIST_PREFIXES):
            return {"error": f"Path blocked for safety: {path}. Sensitive endpoints are off-limits via app_api.", "exit_code": 1}

        method = (args.get("method") or "GET").upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            return {"error": f"Unsupported method: {method}", "exit_code": 1}
        if any(method == m and path.startswith(p) for m, p in _APP_API_BLOCKLIST_METHOD_PATH):
            if "/api/email/accounts" in path:
                return {"error": "Don't use /api/email/accounts via app_api — it is owner-filtered in tool context and may return empty. Use the `list_email_accounts` email tool, then pass `account` to list_emails/read_email.", "exit_code": 1}
            if "/api/cookbook/packages/install" in path:
                return {"error": "Don't POST /api/cookbook/packages/install via app_api — package installation is host code execution. Use the dedicated Cookbook dependency UI/flow instead.", "exit_code": 1}
            if "/api/cookbook/rebuild-engine" in path:
                return {"error": "Don't POST /api/cookbook/rebuild-engine via app_api — engine rebuild mutates local or remote host state. Use the dedicated Cookbook UI/flow instead.", "exit_code": 1}
            if "/api/cookbook/kill-pid" in path:
                return {"error": "Don't POST /api/cookbook/kill-pid via app_api — process signalling is host control. Use the dedicated Cookbook stop/diagnostic flow instead.", "exit_code": 1}
            if "/api/model/download" in path:
                return {"error": "Don't POST /api/model/download directly — use the `download_model` tool (it resolves the server name, sets the venv env_prefix, and registers the task so it shows in the UI).", "exit_code": 1}
            if "/api/model/serve" in path:
                return {"error": "Don't POST /api/model/serve directly — use the `serve_model` or `serve_preset` tool (handles host resolution, env_prefix, and cookbook tracking).", "exit_code": 1}
            if "/api/research/start" in path:
                return {"error": "Don't POST /api/research/start directly — use the `trigger_research` tool (it surfaces the session in the Deep Research sidebar).", "exit_code": 1}
            if "/api/notes" in path:
                return {"error": "Don't hit /api/notes via app_api — use the `manage_notes` tool. It accepts natural-language due_date ('11pm today', 'tomorrow at 9am'), fires reminders from the due_date itself (no separate calendar event), and uses the caller's timezone. The raw endpoint requires ISO-UTC + a separate calendar event, both of which the agent tends to get wrong.", "exit_code": 1}
            if "/api/calendar/events" in path:
                return {"error": "Don't hit /api/calendar/events via app_api — use the `manage_calendar` tool. It handles tz-aware natural-language datetimes and reminder_minutes correctly. If the user wants a note + reminder, prefer `manage_notes` with due_date — it bundles both.", "exit_code": 1}
            return {"error": f"{method} {path} is blocked — it overwrites the whole cookbook state file. Use list_serve_presets / serve_preset / serve_model instead.", "exit_code": 1}

        body = args.get("body")
        query = args.get("query") or None
        # Pass owner so the backend impersonates the user — without this,
        # POSTs (notes, calendar, todos, ...) get owner="internal-tool"
        # and the user that asked for them can't see the result.
        headers = {**_internal_headers(owner=owner), "Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.request(
                    method, f"{base}{path}",
                    json=body if body is not None and method in ("POST", "PUT", "PATCH") else None,
                    params=query,
                    headers=headers,
                )
            # Try to parse JSON; fall back to raw text.
            try:
                payload = resp.json()
                preview = _json.dumps(payload, indent=2, default=str)
                if len(preview) > 4000:
                    preview = preview[:4000] + "\n... (truncated)"
            except Exception:
                payload = None
                preview = (resp.text or "")[:4000]
            if resp.status_code >= 400:
                return {
                    "error": f"{method} {path} -> HTTP {resp.status_code}",
                    "status_code": resp.status_code,
                    "body": preview,
                    "exit_code": 1,
                }
            return {
                "output": f"{method} {path} -> {resp.status_code}\n{preview}",
                "status_code": resp.status_code,
                "json": payload,
                "exit_code": 0,
            }
        except Exception as e:
            return {"error": f"{method} {path} failed: {e}", "exit_code": 1}
