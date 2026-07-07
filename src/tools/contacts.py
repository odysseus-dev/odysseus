"""Contacts-domain tool implementations.

Extracted from tool_implementations.py as part of slice 1 (#4082/#4071).
Holds the resolve_contact and manage_contact (CardDAV CRUD) tools.
``src.tool_implementations`` re-exports these for backward compatibility.
``_INTERNAL_BASE`` still lives in tool_implementations.py and is pulled
back function-locally where needed.
"""
from typing import Dict, Optional

from src.tools._common import _parse_tool_args


async def do_resolve_contact(content: str, owner: Optional[str] = None) -> Dict:
    """Look up a contact by name. Searches: CardDAV -> email history -> memory."""
    import httpx
    from src.tool_implementations import _INTERNAL_BASE  # shared constant, still lives in the facade
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
            phones = [str(p or "").strip() for p in (args.get("phones") or []) if str(p or "").strip()]
            phone = (args.get("phone") or "").strip()
            if phone and phone not in phones:
                phones.insert(0, phone)
            address = (args.get("address") or "").strip()
            name = (args.get("name") or "").strip()
            if not name and email:
                name = email.split("@")[0]
            if not name and not email and not phones and not address:
                return {"error": "name plus email, phone, or address is required for add", "exit_code": 1}
            if not name:
                name = email.split("@")[0] if email else (phones[0] if phones else "Contact")
            # Dedupe by email or phone (same as the /add route).
            existing = await asyncio.to_thread(cc._fetch_contacts)
            for c in existing:
                if email and email.lower() in [e.lower() for e in c.get("emails", [])]:
                    return {"output": f"{email} is already a contact ({c.get('name','')}).", "exit_code": 0}
                if phones and any(p in (c.get("phones") or []) for p in phones):
                    return {"output": f"{phones[0]} is already a contact ({c.get('name','')}).", "exit_code": 0}
            ok = await asyncio.to_thread(cc._create_contact, name, email, address, phones)
            detail = email or ", ".join(phones) or address
            return {"output": f"{'Added' if ok else 'Failed to add'} {name} ({detail}).", "exit_code": 0 if ok else 1}

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
            address = (args.get("address") or "").strip()
            if not name and not emails and not phones and not address:
                return {"error": "Provide a name, emails, phones, or address to update", "exit_code": 1}
            if not name and emails:
                name = emails[0].split("@")[0]
            ok = await asyncio.to_thread(cc._update_contact, uid, name, emails, phones, address)
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
