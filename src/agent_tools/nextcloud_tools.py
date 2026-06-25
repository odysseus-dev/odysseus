"""Agent tools for Nextcloud Files (read-only).

``nextcloud_list`` lists the children of a path on the user's Nextcloud, and
``nextcloud_read_file`` reads a text file into the agent's context. Both resolve
the owner from the tool ``ctx`` and use that owner's first configured Nextcloud
account (or a specific account id when provided). Credentials are never put in
tool output.
"""

import asyncio
import json
import logging
from typing import Optional, Tuple

from src.constants import NEXTCLOUD_MAX_READ_CHARS
from src.nextcloud_client import NextcloudError

logger = logging.getLogger(__name__)


def _parse_args(content: str) -> dict:
    """Accept either a JSON object or a bare path string as the tool content."""
    text = (content or "").strip()
    if text.startswith("{"):
        try:
            args = json.loads(text)
            if isinstance(args, dict):
                return args
        except (json.JSONDecodeError, TypeError):
            pass
    return {"path": text}


def _resolve_client(ctx: dict, account_id: str = ""):
    """Return (NextcloudClient, label) for the owner's chosen account, or an error string."""
    from routes.nextcloud_routes import _client_for, _find_account, _load_accounts

    owner = ctx.get("owner") or None
    accounts = _load_accounts(owner)
    if not accounts:
        return None, "", "No Nextcloud account is configured. Ask the user to add one in Settings."
    if account_id:
        try:
            account = _find_account(owner, account_id)
        except Exception:
            return None, "", f"No Nextcloud account with id '{account_id}'."
    else:
        account = accounts[0]
    label = account.get("label") or account.get("username") or "Nextcloud"
    try:
        client = _client_for(account)
    except Exception as e:
        return None, "", f"Nextcloud account is misconfigured: {e}"
    return client, label, None


def _human_size(n: Optional[int]) -> str:
    if n is None:
        return "-"
    try:
        f = float(n)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{int(f)} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{int(f)} TB"


class NextcloudListTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        args = _parse_args(content)
        path = str(args.get("path") or "").strip()
        account_id = str(args.get("account") or "").strip()
        client, label, err = _resolve_client(ctx, account_id)
        if err:
            return {"error": f"nextcloud_list: {err}", "exit_code": 1}
        try:
            entries = await asyncio.to_thread(client.list_dir, path)
        except NextcloudError as e:
            return {"error": f"nextcloud_list: {e}", "exit_code": 1}
        except ValueError as e:
            return {"error": f"nextcloud_list: {e}", "exit_code": 1}
        if not entries:
            return {"output": f"nextcloud: {label} — '{path or '/'}' is empty.", "exit_code": 0}
        dirs = sorted([e for e in entries if e.get("is_dir")], key=lambda e: e["name"].lower())
        files = sorted([e for e in entries if not e.get("is_dir")], key=lambda e: e["name"].lower())
        lines = [f"nextcloud: {label} — {path or '/'}"]
        for e in dirs:
            mod = e.get("modified") or ""
            lines.append(f"  {e['name']}/                {mod}")
        for e in files:
            mod = e.get("modified") or ""
            ctype = e.get("content_type") or ""
            lines.append(f"  {e['name']}   {_human_size(e.get('size'))}   {ctype}   {mod}".rstrip())
        return {"output": "\n".join(lines), "exit_code": 0}


class NextcloudReadFileTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        args = _parse_args(content)
        path = str(args.get("path") or "").strip()
        account_id = str(args.get("account") or "").strip()
        if not path:
            return {"error": "nextcloud_read_file: path required", "exit_code": 1}
        client, label, err = _resolve_client(ctx, account_id)
        if err:
            return {"error": f"nextcloud_read_file: {err}", "exit_code": 1}
        try:
            content_bytes, content_type = await asyncio.to_thread(
                client.get_file, path, NEXTCLOUD_MAX_READ_CHARS * 4
            )
        except NextcloudError as e:
            return {"error": f"nextcloud_read_file: {e}", "exit_code": 1}
        except ValueError as e:
            return {"error": f"nextcloud_read_file: {e}", "exit_code": 1}
        text = content_bytes.decode("utf-8", errors="replace")
        if len(text) > NEXTCLOUD_MAX_READ_CHARS:
            text = text[:NEXTCLOUD_MAX_READ_CHARS] + f"\n... [truncated at {NEXTCLOUD_MAX_READ_CHARS} chars]"
        header = f"[nextcloud:{label}] {path}"
        return {"output": f"{header}\n{text}", "exit_code": 0}
