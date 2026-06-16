"""
tool_implementations.py

Extracted tool implementation functions (do_* and helpers) from agent_tools.py.
These handle the actual execution logic for each tool type.
"""

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from src.constants import MAX_READ_CHARS, DEEP_RESEARCH_DIR, VAULT_FILE
from src.tool_utils import get_mcp_manager
from core.constants import internal_api_base

# System-domain tools were extracted to src/tools/system.py (slice 1,
# #4082/#4071); the admin manage_* tools live in src/agent_tools/admin_tools
# after the upstream registry migration (#3629). Re-imported here so this
# module stays a working facade.
from src.tools.system import (  # noqa: F401
    do_manage_skills, _skill_dump, do_manage_tasks,
    do_api_call, do_app_api,
    _APP_API_BLOCKLIST_PREFIXES, _APP_API_BLOCKLIST_METHOD_PATH,
)
from src.agent_tools.admin_tools import (  # noqa: F401
    do_manage_endpoints, do_manage_mcp, do_manage_webhooks,
    do_manage_tokens, do_manage_settings,
    _MCP_DENIED_COMMANDS, _validate_mcp_command,
)
# Cookbook (model serving) domain extracted to src/tools/cookbook.py
# (slice 1, #4082/#4071). Re-imported here so this module stays a working
# facade. `_internal_headers` / `_INTERNAL_BASE` stay in this file and are
# pulled back function-locally inside cookbook.py.
from src.tools.cookbook import (  # noqa: F401
    do_download_model, do_serve_model, do_list_served_models,
    do_stop_served_model, do_tail_serve_output, do_list_downloads,
    do_cancel_download, do_search_hf_models, do_adopt_served_model,
    do_list_cookbook_servers, do_list_serve_presets, do_serve_preset,
    do_list_cached_models,
    _cookbook_servers, _resolve_cookbook_host, _cookbook_env_for_host,
    _infer_serve_port, _infer_serve_host, _ensure_served_endpoint,
    _cookbook_register_task, _cookbook_apply_retry_suggestion,
    _scan_running_model_processes, _cookbook_kill_session,
    _MODEL_PROCESS_PATTERNS,
)
# Search domain extracted to src/tools/search.py (slice 1, #4082/#4071).
# Re-imported here so this module stays a working facade.
from src.tools.search import do_search_chats  # noqa: F401
# Notes domain extracted to src/tools/notes.py (slice 1, #4082/#4071).
from src.tools.notes import do_manage_notes  # noqa: F401
# Calendar domain extracted to src/tools/calendar.py (slice 1, #4082/#4071).
from src.tools.calendar import do_manage_calendar  # noqa: F401
# Image domain extracted to src/tools/image.py (slice 1, #4082/#4071).
from src.tools.image import do_edit_image  # noqa: F401

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Active email state
# ---------------------------------------------------------------------------

# When the user has an email reader window open, the frontend tells the
# backend about it on each chat submit. Email tools can resolve "this email"
# without guessing a UID. Cleared between requests by chat_routes.
_active_email_ref: Optional[Dict[str, str]] = None


def set_active_email(uid: Optional[str], folder: Optional[str] = None, account: Optional[str] = None,
                     subject: Optional[str] = None, sender: Optional[str] = None) -> None:
    """Stash the email currently open in the UI. None clears it."""
    global _active_email_ref
    if not uid:
        _active_email_ref = None
        return
    _active_email_ref = {
        "uid": str(uid),
        "folder": str(folder or "INBOX"),
        "account": str(account or ""),
        "subject": str(subject or ""),
        "from": str(sender or ""),
    }


def get_active_email() -> Optional[Dict[str, str]]:
    return _active_email_ref


def clear_active_email() -> None:
    global _active_email_ref
    _active_email_ref = None

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_tool_args(content):
    """Parse a tool-call argument blob.

    Accepts either a JSON string or an already-decoded dict. Unwraps the
    common `{"body": {...}}` envelope that smaller models emit when they
    read tool descriptions like "Body is JSON: {...}" literally — they
    pass `body` as a field name rather than treating it as a noun.

    Returns a dict on success, raises ValueError on bad JSON.
    """
    if isinstance(content, str):
        try:
            args = json.loads(content) if content.strip() else {}
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(str(e))
    elif isinstance(content, dict):
        args = content
    else:
        args = {}
    # Unwrap {"body": {...}} envelope — but only if `body` is the sole key
    # and points at a dict. We don't want to clobber a legitimate `body`
    # field on tools where it's a real arg (e.g. send_email body text).
    if (
        isinstance(args, dict)
        and len(args) == 1
        and "body" in args
        and isinstance(args["body"], dict)
        and "action" in args["body"]  # extra safety: only unwrap if the inner dict looks like a tool call
    ):
        args = args["body"]
    return args


# ── Cookbook tools ──

# In-process loopback base for agent tools that call Odysseus's own API
# (cookbook state, model serve, gallery, email, calendar). We ride the
# per-process internal token so require_admin lets us through. See
# core/middleware.py. Resolution (override / APP_PORT / 7000) lives in
# core.constants.internal_api_base().
_INTERNAL_BASE = internal_api_base()


def _internal_headers(owner: Optional[str] = None) -> Dict[str, str]:
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
    headers = {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}
    if owner:
        headers["X-Odysseus-Owner"] = owner
    return headers


# ── Research tools ──

async def do_manage_research(content: str, owner: Optional[str] = None) -> Dict:
    """List, read/open, or delete saved deep-research results from the Library.
    Args (JSON): {"action": "list|read|delete", "id": "<id>", "search": "..."}.
    Research is stored as data/deep_research/<id>.json (query, summary, sources)."""
    import json as _json
    from pathlib import Path as _Path
    try:
        args = _parse_tool_args(content) if content.strip().startswith("{") else {}
    except ValueError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    action = (args.get("action") or "list").lower()
    rid = (args.get("id") or args.get("session_id") or args.get("research_id") or "").strip()
    data_dir = _Path(DEEP_RESEARCH_DIR)

    # SECURITY: the research id is interpolated straight into a filesystem
    # path (data/deep_research/<rid>.json) for read AND delete. Without this
    # gate an agent-supplied id like "../settings" or "../../etc/passwd"
    # escapes the research dir — reading exfiltrates arbitrary *.json into
    # chat, deleting unlinks arbitrary *.json on disk. Allow only a bare
    # token (research session ids are hex/uuid/slug — no separators).
    if rid and not re.fullmatch(r"[A-Za-z0-9_-]+", rid):
        return {"error": "Invalid research id."}

    def _load(p):
        try:
            return _json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    if action in ("read", "open", "view", "get"):
        if not rid:
            return {"error": "Provide the research id (from action='list')."}
        p = data_dir / f"{rid}.json"
        if not p.exists():
            return {"error": f"Research '{rid}' not found."}
        d = _load(p) or {}
        summary = d.get("result") or d.get("raw_report") or d.get("summary") or d.get("report") or "(no report body)"
        srcs = d.get("sources", []) or []
        out = f"# {d.get('query', '(untitled)')}\n\n{summary}"
        if srcs:
            out += "\n\nSources:\n" + "\n".join(
                f"- {s.get('title') or s.get('url', '')}: {s.get('url', '')}" for s in srcs[:30]
            )
        return {"output": out[:16000], "exit_code": 0}

    if action == "delete":
        if not rid:
            return {"error": "Provide the research id to delete (from action='list')."}
        p = data_dir / f"{rid}.json"
        if p.exists():
            try:
                p.unlink()
            except Exception as e:
                return {"error": f"Failed to delete: {e}"}
            return {"output": f"Deleted research '{rid}'.", "exit_code": 0}
        return {"error": f"Research '{rid}' not found."}

    # default: list — clickable [query](#research-<id>) rows, most-recent first
    search = (args.get("search") or "").lower()
    items = []
    if data_dir.exists():
        for p in data_dir.glob("*.json"):
            d = _load(p)
            if not d:
                continue
            q = d.get("query", "")
            if search and search not in q.lower():
                continue
            items.append((d.get("completed_at", 0) or 0, p.stem, q, len(d.get("sources", []) or [])))
    items.sort(reverse=True)
    if not items:
        return {"output": "No research found in the library." + (f" (search: {search})" if search else ""), "exit_code": 0}
    rows = "\n".join(f"- [{q or '(untitled)'}](#research-{sid}) — {n} sources" for _, sid, q, n in items[:50])
    return {"output": f"Research library ({len(items)} item{'s' if len(items) != 1 else ''}):\n{rows}", "exit_code": 0}


async def do_trigger_research(content: str, owner: Optional[str] = None) -> Dict:
    """Start a live deep-research job that appears in the Deep Research
    sidebar. Hits /api/research/start (the same path the sidebar's
    'Research' button uses) so the session is discoverable + streamable
    there, rather than creating a scheduled task that never surfaces."""
    import httpx
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    topic = args.get("topic", "") or args.get("query", "")
    if not topic:
        return {"error": "topic (or query) is required", "exit_code": 1}
    payload: Dict[str, Any] = {"query": topic}
    # Optional knobs the research panel supports.
    if args.get("max_rounds") is not None:
        try: payload["max_rounds"] = int(args["max_rounds"])
        except (ValueError, TypeError): pass
    if args.get("max_time") is not None:
        try: payload["max_time"] = int(args["max_time"])
        except (ValueError, TypeError): pass
    if args.get("category"):
        payload["category"] = args["category"]
    if args.get("search_provider"):
        payload["search_provider"] = args["search_provider"]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_INTERNAL_BASE}/api/research/start",
                                     json=payload, headers=_internal_headers(owner))
        if resp.status_code >= 400:
            return {"error": f"research/start returned HTTP {resp.status_code}: {resp.text[:200]}", "exit_code": 1}
        data = resp.json()
        sid = data.get("session_id", "?")
        return {
            "output": (
                f"Deep research started: [{topic}](#research-{sid}). "
                "Click to open the Deep Research sidebar and watch progress / read the report."
            ),
            "session_id": sid,
            "anchor": f"[{topic}](#research-{sid})",
            # UI hint so the frontend can open/refresh the research panel.
            "ui_event": "research_started",
            "research_session_id": sid,
            "exit_code": 0,
        }
    except Exception as e:
        return {"error": str(e), "exit_code": 1}


# ── Contact tools ──

async def do_resolve_contact(content: str, owner: Optional[str] = None) -> Dict:
    """Look up a contact by name. Searches: CardDAV -> email history -> memory."""
    import httpx
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    name = args.get("name", "")
    if not name:
        return {"error": "name is required", "exit_code": 1}

    contacts = {}  # email_or_phone -> {name, source, phone?}

    # 1. CardDAV (Radicale) — structured contacts. Call in-process: a
    # server-side httpx GET to /api/contacts/search carries no session
    # cookie and would 401 under require_user.
    try:
        import asyncio
        from routes import contacts_routes as cc
        all_contacts = await asyncio.to_thread(cc._fetch_contacts)
        q = name.lower()
        for c in (all_contacts or []):
            hay_name = (c.get("name") or "").lower()
            match = q in hay_name or any(q in (e or "").lower() for e in c.get("emails", []))
            if not match:
                continue
            has_email = False
            for email in (c.get("emails") or []):
                email = (email or "").strip().lower()
                if email and "@" in email:
                    contacts[email] = {"name": c.get("name") or email, "source": "contacts"}
                    has_email = True
            # Fall back to phone numbers when the contact has no email address
            if not has_email:
                for phone in (c.get("phones") or []):
                    phone = (phone or "").strip()
                    if phone:
                        contacts[phone] = {"name": c.get("name") or phone, "source": "contacts", "phone": phone}
    except Exception:
        pass

    async with httpx.AsyncClient(timeout=30) as client:
        # 2. Email history (sent/received)
        try:
            resp = await client.get(f"{_INTERNAL_BASE}/api/email/resolve-contact", params={"name": name})
            if resp.status_code == 200:
                for c in (resp.json().get("contacts") or []):
                    email = (c.get("email") or "").strip().lower()
                    if email and email not in contacts:
                        contacts[email] = {"name": c.get("name") or email, "source": "email history"}
        except Exception:
            pass

    if not contacts:
        return {"output": f"No contacts found matching '{name}'.", "exit_code": 0}

    lines = [f"Contacts matching '{name}':"]
    for key, info in contacts.items():
        if info.get("phone"):
            lines.append(f"- {info['name']} — phone: {info['phone']} ({info['source']})")
        else:
            lines.append(f"- {info['name']} <{key}> ({info['source']})")
    return {"output": "\n".join(lines), "exit_code": 0}


async def do_manage_contact(content: str, owner: Optional[str] = None) -> Dict:
    """Add / update / delete / list CardDAV contacts. Calls the contacts
    helpers IN-PROCESS rather than over HTTP — a server-side httpx call to
    /api/contacts/* carries no session cookie and would be rejected by
    require_user (401), so the tool would see zero contacts even though
    the browser-side UI works fine."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    action = (args.get("action") or "").strip().lower()
    try:
        from routes import contacts_routes as cc
    except Exception as e:
        return {"error": f"Contacts module unavailable: {e}", "exit_code": 1}
    # The contacts helpers are sync (httpx blocking calls to CardDAV) — run
    # them in a thread so we don't block the event loop.
    import asyncio
    try:
        if action == "list":
            rows = await asyncio.to_thread(cc._fetch_contacts, True)
            if not rows:
                return {"output": "No contacts.", "exit_code": 0}
            lines = [f"{len(rows)} contacts:"]
            for c in rows:
                em = ", ".join(c.get("emails") or [])
                lines.append(f"- {c.get('name') or '(no name)'} <{em}>  [uid={c.get('uid','')}]")
            return {"output": "\n".join(lines), "exit_code": 0}

        if action == "add":
            email = (args.get("email") or "").strip()
            if not email:
                return {"error": "email is required for add", "exit_code": 1}
            name = (args.get("name") or "").strip() or email.split("@")[0]
            # Dedupe by email (same as the /add route).
            existing = await asyncio.to_thread(cc._fetch_contacts)
            for c in existing:
                if email.lower() in [e.lower() for e in c.get("emails", [])]:
                    return {"output": f"{email} is already a contact ({c.get('name','')}).", "exit_code": 0}
            ok = await asyncio.to_thread(cc._create_contact, name, email)
            return {"output": f"{'Added' if ok else 'Failed to add'} {name} <{email}>.", "exit_code": 0 if ok else 1}

        if action in ("update", "edit"):
            uid = (args.get("uid") or "").strip()
            if not uid:
                return {"error": "uid is required for update (use action=list to find it)", "exit_code": 1}
            name = (args.get("name") or "").strip()
            emails = args.get("emails")
            if emails is None and args.get("email"):
                emails = [args["email"]]
            emails = [e.strip() for e in (emails or []) if e and e.strip()]
            phones = [p.strip() for p in (args.get("phones") or []) if p and p.strip()]
            if not name and not emails:
                return {"error": "Provide a name or emails to update", "exit_code": 1}
            if not name and emails:
                name = emails[0].split("@")[0]
            ok = await asyncio.to_thread(cc._update_contact, uid, name, emails, phones)
            return {"output": "Contact updated." if ok else "Update failed.", "exit_code": 0 if ok else 1}

        if action == "delete":
            uid = (args.get("uid") or "").strip()
            if not uid:
                return {"error": "uid is required for delete (use action=list to find it)", "exit_code": 1}
            ok = await asyncio.to_thread(cc._delete_contact, uid)
            return {"output": "Contact deleted." if ok else "Delete failed.", "exit_code": 0 if ok else 1}

        return {"error": f"Unknown action '{action}'. Use list, add, update, or delete.", "exit_code": 1}
    except Exception as e:
        return {"error": f"Contact operation failed: {e}", "exit_code": 1}


# ── Vaultwarden / Bitwarden CLI tools ──

def _load_vault_config() -> Dict:
    """Load Vaultwarden config from data/vault.json."""
    from pathlib import Path
    p = Path(VAULT_FILE)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


async def _run_bw(args: list, session: Optional[str] = None, input_text: Optional[str] = None) -> tuple:
    """Run a bw CLI command with optional session + stdin. Returns (stdout, stderr, returncode)."""
    import asyncio
    env = {}
    import os as _os
    env.update(_os.environ)
    if session:
        env["BW_SESSION"] = session

    proc = await asyncio.create_subprocess_exec(
        "bw", *args,
        stdin=asyncio.subprocess.PIPE if input_text else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate(input=input_text.encode() if input_text else None)
    return stdout.decode(errors="replace").strip(), stderr.decode(errors="replace").strip(), proc.returncode


async def do_vault_search(content: str, owner: Optional[str] = None) -> Dict:
    """Search the vault by keyword. Returns matching item names + URLs, NO passwords."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    query = args.get("query", "").strip()
    if not query:
        return {"error": "query is required", "exit_code": 1}

    cfg = _load_vault_config()
    session = cfg.get("session")
    if not session:
        return {"error": "Vault is locked. Run vault_unlock or provide session key in settings.", "exit_code": 1}

    stdout, stderr, rc = await _run_bw(["list", "items", "--search", query], session=session)
    if rc != 0:
        return {"error": f"bw failed: {stderr[:300]}", "exit_code": 1}

    try:
        items = json.loads(stdout)
    except json.JSONDecodeError:
        return {"error": "Failed to parse bw output", "exit_code": 1}

    if not items:
        return {"output": f"No vault items match '{query}'.", "exit_code": 0}

    lines = [f"Found {len(items)} item(s) matching '{query}':"]
    for it in items[:20]:
        item_id = it.get("id", "?")
        name = it.get("name", "?")
        login = it.get("login") or {}
        username = login.get("username", "")
        uris = login.get("uris") or []
        url = uris[0].get("uri", "") if uris else ""
        parts = [f"[{item_id[:8]}] {name}"]
        if username:
            parts.append(f"user: {username}")
        if url:
            parts.append(f"url: {url}")
        lines.append("- " + " · ".join(parts))
    lines.append("\nUse vault_get(item_id, reason) to retrieve the password.")
    return {"output": "\n".join(lines), "exit_code": 0}


async def do_vault_get(content: str, owner: Optional[str] = None) -> Dict:
    """Retrieve a full vault entry (including password) by item ID. Logs access to assistant chat."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    item_id = args.get("item_id", "").strip()
    reason = args.get("reason", "").strip()
    if not item_id:
        return {"error": "item_id is required", "exit_code": 1}
    if not reason:
        return {"error": "reason is required — explain WHY you need this password", "exit_code": 1}

    cfg = _load_vault_config()
    session = cfg.get("session")
    if not session:
        return {"error": "Vault is locked. Unlock first.", "exit_code": 1}

    stdout, stderr, rc = await _run_bw(["get", "item", item_id], session=session)
    if rc != 0:
        return {"error": f"bw failed: {stderr[:300]}", "exit_code": 1}

    try:
        item = json.loads(stdout)
    except json.JSONDecodeError:
        return {"error": "Failed to parse bw output", "exit_code": 1}

    login = item.get("login") or {}
    name = item.get("name", "?")

    # Audit log to assistant chat
    try:
        from src.assistant_log import log_to_assistant
        if owner:
            log_to_assistant(
                owner,
                f"Retrieved password for **{name}** — reason: {reason}",
                category="Vault",
            )
    except Exception:
        pass

    output = [
        f"Vault item: {name}",
        f"Username: {login.get('username', '(none)')}",
        f"Password: {login.get('password', '(none)')}",
    ]
    if login.get("totp"):
        output.append(f"TOTP secret: {login['totp']}")
    uris = login.get("uris") or []
    if uris:
        output.append("URLs: " + ", ".join(u.get("uri", "") for u in uris))
    if item.get("notes"):
        output.append(f"Notes: {item['notes']}")

    return {"output": "\n".join(output), "exit_code": 0}


async def do_vault_unlock(content: str, owner: Optional[str] = None) -> Dict:
    """Unlock the vault using a master password. Stores the resulting session key."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    master_password = args.get("master_password", "")
    if not master_password:
        return {"error": "master_password is required", "exit_code": 1}

    # Do not pass the master password as an argv element. Local process lists
    # can expose argv to other users; stdin keeps the secret out of `ps`.
    stdout, stderr, rc = await _run_bw(["unlock", "--raw"], input_text=master_password + "\n")
    if rc != 0:
        return {"error": f"Unlock failed: {stderr[:300]}", "exit_code": 1}

    session = stdout.strip()
    if not session:
        return {"error": "bw returned empty session", "exit_code": 1}

    # Save session to vault.json
    from pathlib import Path
    p = Path(VAULT_FILE)
    cfg = {}
    if p.exists():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    cfg["session"] = session
    from datetime import datetime as _dt
    cfg["unlocked_at"] = _dt.utcnow().isoformat()
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        import os as _os
        _os.chmod(str(p), 0o600)
    except Exception:
        pass

    return {"output": "Vault unlocked. Session saved.", "exit_code": 0}
