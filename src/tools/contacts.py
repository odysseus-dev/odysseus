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
        all_contacts = await asyncio.to_thread(cc._fetch_contacts, False, owner)
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
        if action in ("list", "search", "find"):
            rows = await asyncio.to_thread(cc._fetch_contacts, True, owner)
            query = str(args.get("query") or args.get("name") or args.get("email") or "").strip().lower()
            if action in ("search", "find") and query:
                rows = [
                    c for c in rows
                    if query in str(c.get("name") or "").lower()
                    or query in " ".join(c.get("emails") or []).lower()
                    or query in " ".join(c.get("phones") or []).lower()
                ]
            if not rows:
                return {"output": "No contacts.", "exit_code": 0}
            visible_rows = rows if action in ("search", "find") else rows[:20]
            if len(visible_rows) < len(rows):
                lines = [f"Showing {len(visible_rows)} of {len(rows)} contacts:"]
            else:
                lines = [f"{len(rows)} contacts:"]
            for c in visible_rows:
                em = ", ".join(c.get("emails") or [])
                lines.append(f"- {c.get('name') or '(no name)'} <{em}>  [uid={c.get('uid','')}]")
                if c.get('phones'):
                    lines.append('  Phone: ' + ', '.join(c['phones']))
                if c.get('address'):
                    lines.append('  Address: ' + str(c['address']))
            if len(visible_rows) < len(rows):
                lines.append(
                    f"- ...and {len(rows) - len(visible_rows)} more; "
                    "search by name for an exact match"
                )
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
            existing = await asyncio.to_thread(cc._fetch_contacts, False, owner)
            for c in existing:
                if email and email.lower() in [e.lower() for e in c.get("emails", [])]:
                    return {"output": f"{email} is already a contact ({c.get('name','')}).", "exit_code": 0}
                if phones and any(p in (c.get("phones") or []) for p in phones):
                    return {"output": f"{phones[0]} is already a contact ({c.get('name','')}).", "exit_code": 0}
            ok = await asyncio.to_thread(cc._create_contact, name, email, address, phones, owner)
            detail = email or ", ".join(phones) or address
            return {"output": f"{'Added' if ok else 'Failed to add'} {name} ({detail}).", "exit_code": 0 if ok else 1}

        if action in ("update", "edit"):
            uid = (args.get("uid") or "").strip()
            name = (args.get("name") or "").strip()
            existing = await asyncio.to_thread(cc._fetch_contacts, True, owner)
            if not uid and name:
                matches = [c for c in existing if str(c.get("name") or "").strip().lower() == name.lower()]
                if len(matches) == 1:
                    uid = str(matches[0].get("uid") or "")
            if not uid:
                return {"error": "uid is required for update (use action=list to find it)", "exit_code": 1}
            current = next((c for c in existing if c.get('uid') == uid), None)
            if current is None:
                return {"error": "Contact not found", "exit_code": 1}
            if not {'name', 'emails', 'email', 'phones', 'address'}.intersection(args):
                return {"error": "Provide a name, emails, phones, or address to update", "exit_code": 1}
            # Tool updates are patches; the storage helper rewrites the whole
            # contact. Omitted fields must survive that conversion unchanged.
            name = name if 'name' in args else current.get('name', '')
            if 'emails' in args:
                emails = args['emails']
            elif 'email' in args:
                emails = [args['email']]
            else:
                emails = current.get('emails', [])
            emails = [e.strip() for e in (emails or []) if e and e.strip()]
            phones = args['phones'] if 'phones' in args else current.get('phones', [])
            phones = [p.strip() for p in (phones or []) if p and p.strip()]
            address = (args.get('address') or '').strip() if 'address' in args else current.get('address', '')
            ok = await asyncio.to_thread(cc._update_contact, uid, name, emails, phones, address, owner)
            return {"output": "Contact updated." if ok else "Update failed.", "exit_code": 0 if ok else 1}

        if action == "delete":
            uid = (args.get("uid") or "").strip()
            name = (args.get("name") or "").strip()
            if not uid and name:
                matches = await asyncio.to_thread(cc._fetch_contacts, True, owner)
                matches = [c for c in matches if str(c.get("name") or "").strip().lower() == name.lower()]
                if len(matches) == 1:
                    uid = str(matches[0].get("uid") or "")
            if not uid:
                return {"error": "uid is required for delete (use action=list to find it)", "exit_code": 1}
            ok = await asyncio.to_thread(cc._delete_contact, uid, owner)
            return {"output": "Contact deleted." if ok else "Delete failed.", "exit_code": 0 if ok else 1}

        return {"error": f"Unknown action '{action}'. Use list, add, update, or delete.", "exit_code": 1}
    except Exception as e:
        return {"error": f"Contact operation failed: {e}", "exit_code": 1}
