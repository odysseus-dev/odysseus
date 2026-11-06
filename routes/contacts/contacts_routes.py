"""
contacts_routes.py

CardDAV contacts integration. Reads from local Radicale, supports
search and adding new contacts.
"""

import asyncio
import re
import logging
import threading
import uuid
import json
import csv
import io
import os
import inspect
import httpx
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin, urlparse, urlunparse

from core.log_safety import redact_url
from fastapi import APIRouter, Query, Depends, Request, Response, HTTPException
from typing import List, Dict, Optional

from core.middleware import require_admin
from src.auth_helpers import effective_user
from src.url_safety import check_outbound_url

logger = logging.getLogger(__name__)

from src.constants import DATA_DIR as _DATA_DIR, SETTINGS_FILE as _SETTINGS_FILE, CONTACTS_FILE as _CONTACTS_FILE
DATA_DIR = Path(_DATA_DIR)
SETTINGS_FILE = Path(_SETTINGS_FILE)
LOCAL_CONTACTS_FILE = Path(_CONTACTS_FILE)


def _load_settings():
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {}


def _save_settings(settings):
    from core.atomic_io import atomic_write_json
    atomic_write_json(str(SETTINGS_FILE), settings, indent=2)


def _get_carddav_config():
    import os
    settings = _load_settings()
    password = settings.get("carddav_password", os.environ.get("CARDDAV_PASSWORD", ""))
    if password and "carddav_password" in settings:
        from src.secret_storage import decrypt
        password = decrypt(password)
    return {
        "url": settings.get("carddav_url", os.environ.get("CARDDAV_URL", "")),
        "username": settings.get("carddav_username", os.environ.get("CARDDAV_USERNAME", "")),
        "password": password,
    }


def _carddav_configured(cfg: Optional[Dict] = None) -> bool:
    cfg = cfg or _get_carddav_config()
    return bool((cfg.get("url") or "").strip())


def _validate_carddav_url(url: str) -> str:
    cleaned = (url if isinstance(url, str) else "").strip().rstrip("/")
    ok, reason = check_outbound_url(
        cleaned,
        block_private=os.getenv("CARDDAV_BLOCK_PRIVATE_IPS", "false").lower() == "true",
    )
    if not ok:
        raise ValueError(f"Rejected CardDAV URL: {reason}")
    return cleaned


def _carddav_base_url(cfg: Dict) -> str:
    return _validate_carddav_url(cfg.get("url") or "")


def _normalize_contact(contact: Dict) -> Dict:
    emails = []
    for e in contact.get("emails") or ([] if not contact.get("email") else [contact.get("email")]):
        e = str(e or "").strip()
        if e and e not in emails:
            emails.append(e)
    phones = []
    for p in contact.get("phones") or ([] if not contact.get("phone") else [contact.get("phone")]):
        p = str(p or "").strip()
        if p and p not in phones:
            phones.append(p)
    name = str(contact.get("name") or "").strip()
    if not name and emails:
        name = emails[0].split("@")[0]
    address = str(contact.get("address") or "").strip()
    out = {
        "uid": str(contact.get("uid") or uuid.uuid4()),
        "name": name,
        "emails": emails,
        "phones": phones,
        "address": address,
    }
    owner = str(contact.get("owner") or "").strip()
    if owner:
        out["owner"] = owner
    return out


def _contact_visible_to_owner(contact: Dict, owner: Optional[str]) -> bool:
    owner = str(owner or "").strip()
    row_owner = str(contact.get("owner") or "").strip()
    if owner:
        if row_owner:
            return row_owner == owner
        return not owner.startswith("sft_")
    return True


def _load_local_contacts(owner: Optional[str] = None) -> List[Dict]:
    try:
        if not LOCAL_CONTACTS_FILE.exists():
            return []
        data = json.loads(LOCAL_CONTACTS_FILE.read_text(encoding="utf-8"))
        rows = data.get("contacts", data) if isinstance(data, dict) else data
        contacts = [_normalize_contact(c) for c in (rows or []) if isinstance(c, dict)]
        return [c for c in contacts if _contact_visible_to_owner(c, owner)]
    except Exception as e:
        logger.error(f"Failed to load local contacts: {e}")
        return []


def _save_local_contacts(contacts: List[Dict]) -> None:
    from core.atomic_io import atomic_write_json
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(str(LOCAL_CONTACTS_FILE), {"contacts": [_normalize_contact(c) for c in contacts]}, indent=2)
    _contact_cache["contacts"] = [_normalize_contact(c) for c in contacts]
    _contact_cache["by_owner"] = {}
    _contact_cache["fetched_at"] = datetime.utcnow()
    _contact_cache["failed_at"] = None


# ── vCard parsing ──

def _vunesc(value: str) -> str:
    """Reverse _vesc() — turn escaped vCard text back into the raw value.
    Order matters: handle \\n/\\, /\\; first, backslash-unescape last."""
    if not value:
        return value
    out = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt in ("n", "N"):
                out.append("\n")
            elif nxt in (",", ";", "\\"):
                out.append(nxt)
            else:
                out.append(nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _vcard_property(line: str) -> tuple[str, str]:
    """Read a property name/value without splitting a quoted parameter's colon."""
    quoted = False
    for index, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == ":" and not quoted:
            name = line[:index].split(";", 1)[0].rsplit(".", 1)[-1].upper()
            return name, line[index + 1:]
    return "", ""


def _vcard_blocks(text: str) -> list[list[str]]:
    """Split complete cards, unfolding lines and matching delimiters case-insensitively."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"\n[ \t]", "", raw)
    blocks = []
    current = None
    for line in raw.split("\n"):
        name, value = _vcard_property(line)
        if name == "BEGIN" and value.upper() == "VCARD":
            current = [line]
        elif current is not None:
            current.append(line)
            if name == "END" and value.upper() == "VCARD":
                blocks.append(current)
                current = None
    return blocks


def _vcard_components(value: str) -> list[str]:
    """Split a structured value on unescaped semicolons, then unescape its fields."""
    fields, current = [], []
    escaped = False
    for char in value:
        if char == ";" and not escaped:
            fields.append(_vunesc("".join(current)).strip())
            current = []
        else:
            current.append(char)
        escaped = char == "\\" and not escaped
    fields.append(_vunesc("".join(current)).strip())
    return fields


def _parse_vcards(text: str) -> List[Dict]:
    """Parse supported fields without losing card boundaries or escaped characters."""
    contacts = []
    for block in _vcard_blocks(text):
        contact = {"name": "", "emails": [], "phones": [], "uid": "", "address": ""}
        for line in block:
            name, value = _vcard_property(line)
            if name == "FN":
                contact["name"] = _vunesc(value)
            elif name in ("EMAIL", "TEL"):
                key = "emails" if name == "EMAIL" else "phones"
                item = _vunesc(value)
                if item and item not in contact[key]:
                    contact[key].append(item)
            elif name == "ADR":
                contact["address"] = ", ".join(p for p in _vcard_components(value) if p)
            elif name == "UID":
                contact["uid"] = _vunesc(value)
        if any(contact[key] for key in ("name", "emails", "phones", "address")):
            contacts.append(contact)
    return contacts


def _vesc(value: str) -> str:
    """Escape a vCard property VALUE per RFC 6350 §3.4: backslash, comma,
    semicolon, and newlines. Without this, a name like 'Sekisui House,Ltd'
    or any value containing a newline produces a malformed vCard (broken
    N/FN fields) or could inject arbitrary properties."""
    return (
        (value or "")
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def _build_vcard(name: str, email: str, uid: Optional[str] = None,
                 emails: Optional[List[str]] = None,
                 phones: Optional[List[str]] = None,
                 address: Optional[str] = None) -> str:
    """Build a vCard. Accepts either a single `email` (legacy callers) or
    full `emails`/`phones` lists (edit path). The first email is marked
    PREF=1. All values are RFC-6350-escaped."""
    if not uid:
        uid = str(uuid.uuid4())
    # Normalize email lists — `email` arg is a convenience for single-email
    # creation; `emails` (if given) is authoritative.
    email_list = [e.strip() for e in (emails if emails is not None else ([email] if email else [])) if e and e.strip()]
    phone_list = [p.strip() for p in (phones or []) if p and p.strip()]
    # Try to split name into first/last
    parts = name.strip().split()
    if len(parts) >= 2:
        first = parts[0]
        last = " ".join(parts[1:])
    else:
        first = name
        last = ""
    # N field is structured (5 components separated by ';') — escape each
    # component individually so a comma in the name doesn't split it.
    n_field = f"{_vesc(last)};{_vesc(first)};;;"
    lines = [
        "BEGIN:VCARD",
        "VERSION:4.0",
        f"UID:{_vesc(uid)}",
        f"FN:{_vesc(name)}",
        f"N:{n_field}",
    ]
    for i, em in enumerate(email_list):
        # First email is the preferred one.
        lines.append(f"EMAIL;PREF=1:{_vesc(em)}" if i == 0 else f"EMAIL:{_vesc(em)}")
    for ph in phone_list:
        lines.append(f"TEL:{_vesc(ph)}")
    # Address: stuff the whole human-readable string into the street
    # component of ADR. vCard ADR has 7 semicolon-separated components:
    # post-office-box;extended-address;street;locality;region;postal-code;country.
    addr = (address or "").strip()
    if addr:
        lines.append(f"ADR:;;{_vesc(addr)};;;;")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


def _edit_vcard(raw: str, uid: str, name: str, emails: List[str], phones: List[str],
                address: Optional[str]) -> str:
    """Replace edited properties only; retain metadata and unchanged parameters."""
    blocks = _vcard_blocks(raw)
    parsed = _parse_vcards(raw)
    if len(blocks) != 1 or len(parsed) != 1 or parsed[0]["uid"] != uid:
        raise ValueError("Existing vCard does not match the contact being edited")
    previous = parsed[0]
    changed = set()
    if previous["name"] != name:
        changed.update(("FN", "N"))
    if previous["emails"] != emails:
        changed.add("EMAIL")
    if previous["phones"] != phones:
        changed.add("TEL")
    if address is not None and previous["address"] != address:
        changed.add("ADR")
    replacement = _vcard_blocks(_build_vcard(name, "", uid, emails, phones, address))[0]
    lines = [line for line in blocks[0][:-1] if _vcard_property(line)[0] not in changed]
    lines.extend(line for line in replacement if _vcard_property(line)[0] in changed)
    lines.append(blocks[0][-1])
    return "\r\n".join(lines) + "\r\n"


# ── In-memory cache ──

_CONTACT_CACHE_TTL_SECONDS = 60
_CONTACT_FAILURE_BACKOFF_SECONDS = 120
_CARDDAV_TIMEOUT = httpx.Timeout(5.0, connect=2.0)

# CardDAV can be unavailable for a while. Keep the UI responsive by serving
# the last known result (or an empty list on first use) while a single worker
# attempts a refresh in the background.
_contact_cache = {
    "contacts": [],
    "fetched_at": None,
    "failed_at": None,
    "by_owner": {},
}
_contact_fetch_lock = threading.Lock()


def _cached_contacts(owner_key: str) -> List[Dict]:
    cached = (_contact_cache.get("by_owner") or {}).get(owner_key) or {}
    if owner_key and cached:
        return cached.get("contacts") or []
    return _contact_cache.get("contacts") or []


def _mark_contact_fetch_failure(owner_key: str) -> List[Dict]:
    now = datetime.utcnow()
    stale_contacts = _cached_contacts(owner_key)
    _contact_cache["failed_at"] = now
    if owner_key:
        _contact_cache.setdefault("by_owner", {})[owner_key] = {
            "contacts": stale_contacts,
            "fetched_at": now,
        }
    else:
        _contact_cache["fetched_at"] = now
    return stale_contacts


def _contact_sync_status() -> Dict[str, str]:
    """Return a safe, user-facing summary for contact autocomplete clients."""
    if not _carddav_configured():
        return {"state": "local", "message": "No contact sync is configured."}
    if _contact_fetch_lock.locked():
        return {"state": "syncing", "message": "Syncing contacts..."}
    failed_at = _contact_cache.get("failed_at")
    if failed_at:
        age = (datetime.utcnow() - failed_at).total_seconds()
        if age < _CONTACT_FAILURE_BACKOFF_SECONDS:
            return {
                "state": "unavailable",
                "message": "Contacts sync is unavailable. Try again later.",
            }
    return {"state": "ready", "message": ""}


def _abs_url(href: str) -> str:
    """Combine a multistatus <href> (an absolute path like
    /user/contacts/x.vcf) with the configured CardDAV server origin so we
    get a fully-qualified URL to PUT/DELETE. Absolute hrefs are accepted only
    for the configured origin; a cross-origin href is treated as a path on the
    configured server so a malicious CardDAV response cannot redirect later
    writes/deletes to cloud metadata or another host."""
    cfg = _get_carddav_config()
    base = _carddav_base_url(cfg)
    base_p = urlparse(base)
    joined = urljoin(base.rstrip("/") + "/", href or "")
    joined_p = urlparse(joined)
    if (joined_p.scheme, joined_p.netloc) != (base_p.scheme, base_p.netloc):
        joined = urlunparse((base_p.scheme, base_p.netloc, joined_p.path or "/", "", joined_p.query, ""))
    return _validate_carddav_url(joined)


# CardDAV REPORT body — pull every card's etag + raw vCard in ONE request,
# alongside the resource href. Lets us map each contact's UID to the real
# server resource path (which is NOT always <uid>.vcf for contacts created
# by other clients).
_ADDRESSBOOK_QUERY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<C:addressbook-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav">'
    '<D:prop><D:getetag/><C:address-data/></D:prop>'
    '<C:filter/>'
    '</C:addressbook-query>'
)


def _fetch_via_report(cfg, auth):
    """Try a CardDAV REPORT addressbook-query — returns contacts WITH an
    `href` field, or None if the server doesn't support it / errors."""
    from defusedxml import ElementTree as ET
    try:
        r = httpx.request(
            "REPORT", cfg["url"],
            content=_ADDRESSBOOK_QUERY.encode("utf-8"),
            headers={"Content-Type": "application/xml; charset=utf-8", "Depth": "1"},
            auth=auth, timeout=_CARDDAV_TIMEOUT,
        )
        if r.status_code not in (207, 200):
            return None
        root = ET.fromstring(r.text)
        ns = {"D": "DAV:", "C": "urn:ietf:params:xml:ns:carddav"}
        out = []
        for resp in root.findall("D:response", ns):
            href_el = resp.find("D:href", ns)
            data_el = resp.find(".//C:address-data", ns)
            if href_el is None or data_el is None or not (data_el.text or "").strip():
                continue
            parsed = _parse_vcards(data_el.text)
            if not parsed:
                continue
            c = parsed[0]
            c["href"] = href_el.text.strip()
            out.append(c)
        # If the REPORT parsed to ZERO contacts, don't trust it — some
        # CardDAV servers treat an empty <filter/> as "match nothing" and
        # return a valid-but-empty 207. Return None so the caller falls
        # back to the plain GET (which lists everything). A genuinely empty
        # address book just costs one extra GET that also returns nothing.
        if not out:
            return None
        return out
    except Exception as e:
        logger.warning(f"CardDAV REPORT failed, falling back to GET: {e}")
        return None


def _fetch_contacts(force=False, owner: Optional[str] = None):
    """Fetch all contacts. Uses CardDAV when configured, otherwise local JSON."""
    owner_key = str(owner or "").strip()
    by_owner = _contact_cache.setdefault("by_owner", {})
    if owner_key and not force and owner_key in by_owner:
        cached = by_owner.get(owner_key) or {}
        fetched_at = cached.get("fetched_at")
        if fetched_at:
            age = (datetime.utcnow() - fetched_at).total_seconds()
            if age < _CONTACT_CACHE_TTL_SECONDS:
                return cached.get("contacts") or []

    if not owner_key and not force and _contact_cache["fetched_at"]:
        age = (datetime.utcnow() - _contact_cache["fetched_at"]).total_seconds()
        if age < _CONTACT_CACHE_TTL_SECONDS:
            return _contact_cache["contacts"]

    failed_at = _contact_cache.get("failed_at")
    if not force and failed_at:
        failure_age = (datetime.utcnow() - failed_at).total_seconds()
        if failure_age < _CONTACT_FAILURE_BACKOFF_SECONDS:
            return _cached_contacts(owner_key)

    # SFT users must not see the operator's personal/CardDAV contact book.
    # Their training contacts are seeded as owner-scoped local rows.
    if owner_key.startswith("sft_"):
        contacts = _load_local_contacts(owner_key)
        by_owner[owner_key] = {"contacts": contacts, "fetched_at": datetime.utcnow()}
        return contacts

    cfg = _get_carddav_config()
    if not _carddav_configured(cfg):
        contacts = _load_local_contacts(owner_key or None)
        if owner_key:
            by_owner[owner_key] = {"contacts": contacts, "fetched_at": datetime.utcnow()}
        else:
            _contact_cache["contacts"] = contacts
            _contact_cache["fetched_at"] = datetime.utcnow()
        return contacts

    # Do not let a burst of typeahead requests start parallel CardDAV timeouts.
    # A caller that arrives during a refresh gets the most recent cache instead.
    if not _contact_fetch_lock.acquire(blocking=False):
        return _cached_contacts(owner_key)

    try:
        cfg["url"] = _carddav_base_url(cfg)
        auth = None
        if cfg["username"]:
            auth = (cfg["username"], cfg["password"])
        # Preferred path: REPORT gives us hrefs for reliable edit/delete.
        contacts = _fetch_via_report(cfg, auth)
        if contacts is None:
            # Fallback: plain GET, concatenated vCards, no hrefs.
            r = httpx.get(cfg["url"], auth=auth, timeout=_CARDDAV_TIMEOUT)
            if r.status_code != 200:
                logger.warning(f"CardDAV returned {r.status_code}")
                return _mark_contact_fetch_failure(owner_key)
            contacts = _parse_vcards(r.text)
        fetched_at = datetime.utcnow()
        _contact_cache["contacts"] = contacts
        _contact_cache["fetched_at"] = fetched_at
        _contact_cache["failed_at"] = None
        if owner_key:
            by_owner[owner_key] = {"contacts": contacts, "fetched_at": fetched_at}
        return contacts
    except Exception as e:
        logger.error(f"Failed to fetch contacts: {e}")
        return _mark_contact_fetch_failure(owner_key)
    finally:
        _contact_fetch_lock.release()


def _resolve_resource_url(uid: str) -> str:
    """Map a contact UID to its real CardDAV resource URL. Uses the href
    captured during fetch when available (handles contacts whose filename
    != UID); falls back to the <uid>.vcf guess for app-created contacts or
    when no href is known."""
    def _lookup():
        for c in _contact_cache.get("contacts", []):
            if c.get("uid") == uid and c.get("href"):
                return _abs_url(c["href"])
        return None
    found = _lookup()
    if found:
        return found
    # Not in cache (or no href) — refresh once and retry before guessing.
    try:
        _fetch_contacts(force=True)
    except Exception:
        pass
    return _lookup() or _vcard_url(uid)


def _create_contact(name: str, email: str = "", address: str = "", phones: Optional[List[str]] = None, owner: Optional[str] = None) -> bool:
    """Add a new contact via CardDAV or local contacts."""
    email = (email or "").strip()
    phone_list = [str(p or "").strip() for p in (phones or []) if str(p or "").strip()]
    cfg = _get_carddav_config()
    owner_key = str(owner or "").strip()
    if owner_key.startswith("sft_") or not _carddav_configured(cfg):
        contacts = _load_local_contacts()
        email_l = email.lower()
        for c in contacts:
            if owner_key and not _contact_visible_to_owner(c, owner_key):
                continue
            if email_l and email_l in [e.lower() for e in c.get("emails", [])]:
                return True
            if phone_list and any(p in (c.get("phones") or []) for p in phone_list):
                return True
        row = {
            "name": name,
            "emails": [email] if email else [],
            "phones": phone_list,
            "address": address,
        }
        if owner_key:
            row["owner"] = owner_key
        contacts.append(_normalize_contact(row))
        _save_local_contacts(contacts)
        return True

    contact_uid = str(uuid.uuid4())
    vcard = _build_vcard(name, email, contact_uid, address=address, phones=phone_list)
    try:
        url = _carddav_base_url(cfg) + "/" + contact_uid + ".vcf"
        auth = None
        if cfg["username"]:
            auth = (cfg["username"], cfg["password"])
        r = httpx.put(
            url,
            data=vcard.encode("utf-8"),
            headers={"Content-Type": "text/vcard; charset=utf-8"},
            auth=auth,
            timeout=10,
        )
        if r.status_code in (200, 201, 204):
            # Invalidate cache
            _contact_cache["fetched_at"] = None
            return True
        logger.warning(f"CardDAV PUT returned {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        logger.error(f"Failed to create contact: {e}")
        return False


def _vcard_url(uid: str) -> str:
    """The CardDAV resource URL for a given contact UID. The uid is URL-
    encoded so a value containing '/', '..' or other path chars can't
    escape the collection and target an arbitrary CardDAV resource."""
    from urllib.parse import quote
    cfg = _get_carddav_config()
    return _carddav_base_url(cfg) + "/" + quote(uid, safe="") + ".vcf"


def _import_vcards(text: str) -> Dict:
    """Import a (possibly multi-card) .vcf blob. Each card is PUT to the
    CardDAV server PRESERVING its full original content (ADR/ORG/photo/
    etc.) — we don't rebuild it, just ensure it has VERSION + UID and
    normalize line endings. Returns {imported, failed, total}."""
    from urllib.parse import quote
    cfg = _get_carddav_config()
    if not cfg.get("url"):
        parsed = _parse_vcards(text)
        contacts = _load_local_contacts()
        existing = {
            e.lower()
            for c in contacts
            for e in (c.get("emails") or [])
            if e
        }
        existing_uids = {c.get("uid") for c in contacts if c.get("uid")}
        imported = 0
        for c in parsed:
            emails = [e for e in (c.get("emails") or []) if e]
            if c.get("uid") in existing_uids or any(e.lower() in existing for e in emails):
                continue
            contacts.append(_normalize_contact(c))
            if c.get("uid"):
                existing_uids.add(c["uid"])
            for e in emails:
                existing.add(e.lower())
            imported += 1
        if imported:
            _save_local_contacts(contacts)
        return {"imported": imported, "failed": 0, "total": len(parsed)}
    try:
        base_url = _carddav_base_url(cfg)
    except ValueError as e:
        logger.warning("CardDAV import URL rejected: %s", e)
        return {"imported": 0, "failed": 0, "total": 0, "error": str(e)}
    auth = (cfg["username"], cfg["password"]) if cfg["username"] else None
    blocks = _vcard_blocks(text)
    imported = 0
    failed = 0
    for block in blocks:
        # Extract or assign a UID.
        properties = dict(_vcard_property(line) for line in block)
        stored_uid = _vunesc(properties.get("UID", "")).strip()
        uid = stored_uid or str(uuid.uuid4())
        if not properties.get("VERSION"):
            block.insert(1, "VERSION:4.0")
        if not stored_uid:
            block = [line for line in block if _vcard_property(line)[0] != "UID"]
            block.insert(2, f"UID:{_vesc(uid)}")
        vcard = "\r\n".join(block) + "\r\n"
        url = base_url + "/" + quote(uid, safe="") + ".vcf"
        try:
            r = httpx.put(
                url, data=vcard.encode("utf-8"),
                headers={"Content-Type": "text/vcard; charset=utf-8"},
                auth=auth, timeout=15,
            )
            if r.status_code in (200, 201, 204):
                imported += 1
            else:
                failed += 1
                logger.warning(f"Import PUT {uid} returned {r.status_code}: {r.text[:120]}")
        except Exception as e:
            failed += 1
            logger.error(f"Import PUT {uid} failed: {e}")
    if imported:
        _contact_cache["fetched_at"] = None
    return {"imported": imported, "failed": failed, "total": len(blocks)}


def _import_csv_contacts(text: str) -> Dict:
    """Read lossless contact exports and legacy name/email/phone CSV files."""
    raw = (text or "").strip()
    if not raw:
        return {"imported": 0, "failed": 0, "total": 0, "error": "No CSV data found"}
    try:
        dialect = csv.Sniffer().sniff(raw[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(raw), dialect=dialect))
    aliases = {
        "name": ("name", "full name", "full_name", "display name", "display_name", "fn"),
        "emails": ("emails", "email", "email address", "email_address", "e-mail", "mail"),
        "phones": ("phones", "phone", "telephone", "tel"),
        "address": ("address", "postal address", "postal_address"),
        "uid": ("uid",),
    }
    known = {label for labels in aliases.values() for label in labels}
    headers = [cell.strip().lower() for cell in rows[0]]
    has_header = any(cell in known for cell in headers)
    # A contact's name can itself be an alias such as "Phone" or "Mail".
    # Recognize data in the documented headerless email/phone positions before
    # mistaking those contact values for column names.
    legacy_email = headers[1] if len(headers) > 1 else ""
    legacy_phone = headers[2] if len(headers) > 2 else ""
    # Phone values may include extension labels; supported header aliases do
    # not contain digits, so formatting must not restrict this data signal.
    phone_number = any(char.isdigit() for char in legacy_phone)
    if "@" in legacy_email or phone_number:
        has_header = False

    def values(cell):
        if not cell:
            return []
        if cell.startswith("["):
            parsed = json.loads(cell)
            if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
                raise ValueError("Expected a list of strings")
            return [item.strip() for item in parsed if item.strip()]
        return [cell]

    candidates = []
    failed = total = 0
    for row in rows[1:] if has_header else rows:
        if not any(cell.strip() for cell in row):
            continue
        total += 1
        try:
            if has_header:
                if len(row) > len(headers):
                    raise ValueError("Too many CSV columns")
                fields = dict(zip(headers, (cell.strip() for cell in row)))
                data = {key: next((fields[label] for label in labels if fields.get(label)), "")
                        for key, labels in aliases.items()}
            else:
                data = dict(zip(("name", "emails", "phones"), (cell.strip() for cell in row)))
            data["emails"] = values(data.get("emails", ""))
            data["phones"] = values(data.get("phones", ""))
            if not any(data.get(key) for key in ("name", "emails", "phones", "address")):
                raise ValueError("No contact data")
            candidates.append(_normalize_contact(data))
        except (ValueError, TypeError):
            failed += 1

    existing = _fetch_contacts()
    uids = {c.get("uid") for c in existing if c.get("uid")}
    emails = {email.lower() for c in existing for email in c.get("emails", [])}
    incoming = []
    for contact in candidates:
        if contact["uid"] in uids or any(email.lower() in emails for email in contact["emails"]):
            continue
        incoming.append(contact)
        uids.add(contact["uid"])
        emails.update(email.lower() for email in contact["emails"])
    if not incoming:
        return {"imported": 0, "failed": failed, "total": total}
    result = _import_vcards(_contacts_to_vcf(incoming))
    return {"imported": result["imported"], "failed": result["failed"] + failed, "total": total}


def _contacts_to_vcf(contacts: List[Dict]) -> str:
    return "".join(
        _build_vcard(
            c.get("name") or "",
            "",
            uid=c.get("uid") or str(uuid.uuid4()),
            emails=c.get("emails") or [],
            phones=c.get("phones") or [],
            address=c.get("address") or "",
        )
        for c in contacts
    )


def _contacts_to_csv(contacts: List[Dict]) -> str:
    """One row per contact; JSON arrays preserve list boundaries and punctuation."""
    out = io.StringIO()
    writer = csv.writer(out)
    # Retain the original leading columns for existing CSV consumers.
    writer.writerow(["name", "email", "phone", "uid", "emails", "phones", "address"])
    for c in contacts:
        writer.writerow([
            c.get("name") or "",
            (c.get("emails") or [""])[0], (c.get("phones") or [""])[0],
            c.get("uid") or "",
            json.dumps(c.get("emails") or [], ensure_ascii=False),
            json.dumps(c.get("phones") or [], ensure_ascii=False),
            c.get("address") or "",
        ])
    return out.getvalue()


def _update_contact(uid: str, name: str, emails: List[str], phones: List[str], address: Optional[str] = None, owner: Optional[str] = None) -> bool:
    """Rewrite an existing contact via CardDAV or local contacts."""
    cfg = _get_carddav_config()
    owner_key = str(owner or "").strip()
    if owner_key.startswith("sft_") or not _carddav_configured(cfg):
        contacts = _load_local_contacts()
        found = False
        out = []
        for c in contacts:
            if c.get("uid") == uid:
                if owner_key and not _contact_visible_to_owner(c, owner_key):
                    out.append(c)
                    continue
                # None means omitted; an explicit empty string clears it.
                addr = c.get("address", "") if address is None else address
                row = {"uid": uid, "name": name, "emails": emails, "phones": phones, "address": addr}
                if owner_key:
                    row["owner"] = owner_key
                out.append(_normalize_contact(row))
                found = True
            else:
                out.append(c)
        if not found:
            row = {"uid": uid, "name": name, "emails": emails, "phones": phones, "address": address}
            if owner_key:
                row["owner"] = owner_key
            out.append(_normalize_contact(row))
        _save_local_contacts(out)
        return True

    # Use the real resource href (handles externally-created contacts whose
    # filename != UID); falls back to the <uid>.vcf guess.
    try:
        url = _resolve_resource_url(uid)
        auth = (cfg["username"], cfg["password"]) if cfg["username"] else None
        original = httpx.get(url, auth=auth, timeout=10)
        if original.status_code != 200:
            logger.warning("Cannot read CardDAV contact before update: HTTP %s", original.status_code)
            return False
        vcard = _edit_vcard(original.text, uid, name, emails, phones, address)
        headers = {"Content-Type": "text/vcard; charset=utf-8"}
        if original.headers.get("ETag"):
            headers["If-Match"] = original.headers["ETag"]
        r = httpx.put(
            url,
            data=vcard.encode("utf-8"),
            headers=headers,
            auth=auth,
            timeout=10,
        )
        if r.status_code in (200, 201, 204):
            _contact_cache["fetched_at"] = None
            return True
        logger.warning(f"CardDAV update PUT returned {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        logger.error(f"Failed to update contact: {e}")
        return False


def _delete_contact(uid: str, owner: Optional[str] = None) -> bool:
    """Delete a contact via CardDAV or local contacts."""
    cfg = _get_carddav_config()
    owner_key = str(owner or "").strip()
    if owner_key.startswith("sft_") or not _carddav_configured(cfg):
        contacts = _load_local_contacts()
        remaining = [
            c for c in contacts
            if c.get("uid") != uid or (owner_key and not _contact_visible_to_owner(c, owner_key))
        ]
        _save_local_contacts(remaining)
        return True

    try:
        url = _resolve_resource_url(uid)
        auth = (cfg["username"], cfg["password"]) if cfg["username"] else None
        r = httpx.delete(url, auth=auth, timeout=10)
        if r.status_code in (200, 204, 404):
            # Invalidate cache so the next fetch sees the server truth.
            _contact_cache["fetched_at"] = None
            # Verify: force a fresh fetch and check the UID is actually gone.
            # A 404 on the guessed URL ({uid}.vcf) can mean the contact
            # lives at a different resource URL — the DELETE missed it but
            # we'd silently report success. This check catches that.
            fresh = _fetch_contacts(force=True)
            still_there = any(c.get("uid") == uid for c in fresh)
            if still_there:
                logger.warning(
                    f"CardDAV DELETE reported success for {uid} "
                    f"but UID still present after re-fetch — "
                    f"resource URL may differ from {redact_url(url)}"
                )
                return False
            if r.status_code == 404:
                logger.info(f"CardDAV DELETE 404 for {uid} — already gone")
            return True
        logger.warning(f"CardDAV DELETE returned {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        logger.error(f"Failed to delete contact: {e}")
        return False


# ── Routes ──

def setup_contacts_routes():
    router = APIRouter(prefix="/api/contacts", tags=["contacts"])

    @router.get("/list")
    async def list_contacts(request: Request, _admin: str = Depends(require_admin)):
        """List all contacts."""
        contacts = await asyncio.to_thread(_fetch_contacts, owner=effective_user(request))
        return {"contacts": contacts, "count": len(contacts), "sync": _contact_sync_status()}

    @router.get("/search")
    async def search_contacts(request: Request, q: str = Query(""), _admin: str = Depends(require_admin)):
        """Search contacts by name or email. Returns up to 10 matches."""
        contacts = await asyncio.to_thread(_fetch_contacts, owner=effective_user(request))
        if not q:
            return {"results": [], "sync": _contact_sync_status()}
        q_lower = q.lower()
        results = []
        for c in contacts:
            if q_lower in c["name"].lower():
                results.append(c)
                continue
            for em in c["emails"]:
                if q_lower in em.lower():
                    results.append(c)
                    break
        return {"results": results[:10], "sync": _contact_sync_status()}

    @router.post("/add")
    async def add_contact(data: dict, request: Request, _admin: str = Depends(require_admin)):
        """Add a new contact."""
        owner = effective_user(request)
        name = (data.get("name") or "").strip()
        email = (data.get("email") or "").strip()
        phone = (data.get("phone") or "").strip()
        phones = [str(p or "").strip() for p in (data.get("phones") or []) if str(p or "").strip()]
        if phone and phone not in phones:
            phones.insert(0, phone)
        address = (data.get("address") or "").strip()
        if not name and email:
            name = email.split("@")[0]
        if not name and not email and not phones and not address:
            return {"success": False, "error": "Name, email, phone, or address required"}
        if not name:
            name = email.split("@")[0] if email else (phones[0] if phones else "Contact")
        contacts = _fetch_contacts(owner=owner)
        for c in contacts:
            if email and email.lower() in [e.lower() for e in c.get("emails", [])]:
                return {"success": True, "message": "Already exists", "contact": c}
            if phones and any(p in (c.get("phones") or []) for p in phones):
                return {"success": True, "message": "Already exists", "contact": c}
        create_params = inspect.signature(_create_contact).parameters
        if len(create_params) >= 3:
            create_kwargs = {}
            if "phones" in create_params:
                create_kwargs["phones"] = phones
            if "owner" in create_params:
                create_kwargs["owner"] = owner
            ok = _create_contact(name, email, address, **create_kwargs)
        else:
            ok = _create_contact(name, email)
        # If a phone was provided, do an immediate update to thread it
        # through (the simple _create_contact signature only takes name +
        # email + address; phones happen via update).
        if ok and phones and "phones" not in create_params:
            try:
                fresh = _fetch_contacts(force=True, owner=owner)
                created = next((c for c in fresh if name == c.get("name") and (not email or email in c.get("emails", []))), None)
                if created:
                    _update_contact(
                        created["uid"], name,
                        created.get("emails", []),
                        phones,
                        address,
                        owner=owner,
                    )
            except Exception:
                pass
        return {"success": ok}

    @router.post("/import")
    async def import_vcf(data: dict, _admin: str = Depends(require_admin)):
        """Import contacts from .vcf or CSV. Body: {"vcf": "..."} or {"csv": "..."}."""
        # Non-text values must not reach the parsers or become contact rows.
        value = data.get("vcf") or data.get("text") or ""
        text = value if isinstance(value, str) else ""
        csv_value = data.get("csv") or ""
        csv_text = csv_value if isinstance(csv_value, str) else ""
        if text.strip():
            if "BEGIN:VCARD" not in text.upper():
                return {"success": False, "error": "No vCard data found"}
            result = _import_vcards(text)
        elif csv_text.strip():
            result = _import_csv_contacts(csv_text)
        else:
            return {"success": False, "error": "No contact data found"}
        result["success"] = result.get("imported", 0) > 0
        return result

    @router.get("/export")
    async def export_contacts(
        request: Request,
        format: str = Query("vcf", pattern="^(vcf|csv)$"),
        _admin: str = Depends(require_admin),
    ):
        """Export all contacts as vCard or CSV."""
        contacts = await asyncio.to_thread(
            _fetch_contacts,
            force=True,
            owner=effective_user(request),
        )
        if format == "csv":
            content = _contacts_to_csv(contacts)
            media_type = "text/csv; charset=utf-8"
            filename = "odysseus-contacts.csv"
        else:
            content = _contacts_to_vcf(contacts)
            media_type = "text/vcard; charset=utf-8"
            filename = "odysseus-contacts.vcf"
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/config")
    async def get_config(_admin: str = Depends(require_admin)):
        cfg = _get_carddav_config()
        # Mask password
        if cfg["password"]:
            cfg["password"] = "***"
        return cfg

    @router.put("/config")
    async def update_config(data: dict, _admin: str = Depends(require_admin)):
        settings = _load_settings()
        for key in ("carddav_url", "carddav_username", "carddav_password"):
            if key in data:
                if key == "carddav_url" and str(data[key] or "").strip():
                    try:
                        settings[key] = _validate_carddav_url(data[key])
                    except ValueError as e:
                        raise HTTPException(400, str(e))
                else:
                    value = data[key]
                    if key == "carddav_password" and value:
                        from src.secret_storage import encrypt
                        value = encrypt(value)
                    settings[key] = value
        _save_settings(settings)
        # Force re-fetch
        _contact_cache["fetched_at"] = None
        _contact_cache["failed_at"] = None
        return {"success": True}

    @router.delete("/clear")
    async def clear_contacts(request: Request, _admin: str = Depends(require_admin)):
        """Clear all local contacts. If CardDAV is configured, only clears the local fallback cache."""
        owner = effective_user(request)
        if owner:
            remaining = [
                c for c in _load_local_contacts()
                if not _contact_visible_to_owner(c, owner)
            ]
            _save_local_contacts(remaining)
        else:
            _save_local_contacts([])
        return {"success": True}

    # NOTE: the /{uid} routes are declared LAST so the literal paths above
    # (/list, /search, /add, /config) win — otherwise PUT /config would
    # match PUT /{uid} with uid="config".
    @router.put("/{uid}")
    async def edit_contact(uid: str, data: dict, request: Request, _admin: str = Depends(require_admin)):
        """Edit an existing contact — name / emails / phones / address."""
        name = (data.get("name") or "").strip()
        emails = data.get("emails")
        phones = data.get("phones")
        if emails is None and data.get("email"):
            emails = [data["email"]]
        emails = [e.strip() for e in (emails or []) if e and e.strip()]
        phones = [p.strip() for p in (phones or []) if p and p.strip()]
        address = (data.get("address") or "").strip() if "address" in data else None
        if not name and not emails and not address:
            return {"success": False, "error": "Name, email, or address required"}
        if not name and emails:
            name = emails[0].split("@")[0]
        ok = _update_contact(uid, name, emails, phones, address, owner=effective_user(request))
        return {"success": ok}

    @router.delete("/{uid}")
    async def delete_contact(uid: str, request: Request, _admin: str = Depends(require_admin)):
        """Delete a contact by UID."""
        if not uid:
            return {"success": False, "error": "UID required"}
        ok = _delete_contact(uid, owner=effective_user(request))
        return {"success": ok}

    return router
