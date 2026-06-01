"""
contacts_routes.py

CardDAV contacts integration. Reads from local Radicale, supports
search and adding new contacts.
"""

import re
import logging
import uuid
import json
import csv
import io
import httpx
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Query, Depends, Response
from typing import List, Dict, Optional

from src.auth_helpers import require_user
from core.middleware import require_admin

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"
LOCAL_CONTACTS_FILE = DATA_DIR / "contacts.json"


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
    return {
        "url": settings.get("carddav_url", os.environ.get("CARDDAV_URL", "")),
        "username": settings.get("carddav_username", os.environ.get("CARDDAV_USERNAME", "")),
        "password": settings.get("carddav_password", os.environ.get("CARDDAV_PASSWORD", "")),
    }


def _carddav_configured(cfg: Optional[Dict] = None) -> bool:
    cfg = cfg or _get_carddav_config()
    return bool((cfg.get("url") or "").strip())


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
    return {
        "uid": str(contact.get("uid") or uuid.uuid4()),
        "name": name,
        "emails": emails,
        "phones": phones,
    }


def _load_local_contacts() -> List[Dict]:
    try:
        if not LOCAL_CONTACTS_FILE.exists():
            return []
        data = json.loads(LOCAL_CONTACTS_FILE.read_text(encoding="utf-8"))
        rows = data.get("contacts", data) if isinstance(data, dict) else data
        return [_normalize_contact(c) for c in (rows or []) if isinstance(c, dict)]
    except Exception as e:
        logger.error(f"Failed to load local contacts: {e}")
        return []


def _save_local_contacts(contacts: List[Dict]) -> None:
    from core.atomic_io import atomic_write_json
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(str(LOCAL_CONTACTS_FILE), {"contacts": [_normalize_contact(c) for c in contacts]}, indent=2)
    _contact_cache["contacts"] = [_normalize_contact(c) for c in contacts]
    _contact_cache["fetched_at"] = datetime.utcnow()


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


def _parse_vcards(text: str) -> List[Dict]:
    """Parse a stream of vCards into dicts with name, email, phone."""
    contacts = []
    for block in re.split(r"BEGIN:VCARD", text):
        if not block.strip():
            continue
        contact = {"name": "", "emails": [], "phones": [], "uid": ""}
        for line in block.split("\n"):
            line = line.strip()
            if line.startswith("FN:") or line.startswith("FN;"):
                contact["name"] = _vunesc(line.split(":", 1)[1]) if ":" in line else ""
            elif line.startswith("EMAIL"):
                # Handle EMAIL:foo@bar OR EMAIL;TYPE=...:foo@bar OR EMAIL;PREF=1:foo@bar
                if ":" in line:
                    email_addr = _vunesc(line.split(":", 1)[1])
                    if email_addr and email_addr not in contact["emails"]:
                        contact["emails"].append(email_addr)
            elif line.startswith("TEL"):
                if ":" in line:
                    phone = _vunesc(line.split(":", 1)[1])
                    if phone and phone not in contact["phones"]:
                        contact["phones"].append(phone)
            elif line.startswith("UID:"):
                contact["uid"] = _vunesc(line[4:])
        if contact["name"] or contact["emails"]:
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
                 phones: Optional[List[str]] = None) -> str:
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
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


# ── In-memory cache ──

_contact_cache = {"contacts": [], "fetched_at": None}


def _abs_url(href: str) -> str:
    """Combine a multistatus <href> (an absolute path like
    /user/contacts/x.vcf) with the configured CardDAV server origin so we
    get a fully-qualified URL to PUT/DELETE. If href is already absolute
    (http...), return it as-is."""
    from urllib.parse import urlparse, urlunparse
    if href.startswith("http://") or href.startswith("https://"):
        return href
    cfg = _get_carddav_config()
    p = urlparse(cfg["url"])
    return urlunparse((p.scheme, p.netloc, href, "", "", ""))


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
            auth=auth, timeout=10,
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


def _fetch_contacts(force=False):
    """Fetch all contacts. Uses CardDAV when configured, otherwise local JSON."""
    if not force and _contact_cache["fetched_at"]:
        age = (datetime.utcnow() - _contact_cache["fetched_at"]).total_seconds()
        if age < 60:
            return _contact_cache["contacts"]

    cfg = _get_carddav_config()
    if not _carddav_configured(cfg):
        contacts = _load_local_contacts()
        _contact_cache["contacts"] = contacts
        _contact_cache["fetched_at"] = datetime.utcnow()
        return contacts

    try:
        auth = None
        if cfg["username"]:
            auth = (cfg["username"], cfg["password"])
        # Preferred path: REPORT gives us hrefs for reliable edit/delete.
        contacts = _fetch_via_report(cfg, auth)
        if contacts is None:
            # Fallback: plain GET, concatenated vCards, no hrefs.
            r = httpx.get(cfg["url"], auth=auth, timeout=10)
            if r.status_code != 200:
                logger.warning(f"CardDAV returned {r.status_code}")
                return _contact_cache["contacts"]
            contacts = _parse_vcards(r.text)
        _contact_cache["contacts"] = contacts
        _contact_cache["fetched_at"] = datetime.utcnow()
        return contacts
    except Exception as e:
        logger.error(f"Failed to fetch contacts: {e}")
        return _contact_cache["contacts"]


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


def _create_contact(name: str, email: str) -> bool:
    """Add a new contact via CardDAV or local contacts."""
    cfg = _get_carddav_config()
    if not _carddav_configured(cfg):
        contacts = _load_local_contacts()
        email_l = (email or "").strip().lower()
        for c in contacts:
            if email_l and email_l in [e.lower() for e in c.get("emails", [])]:
                return True
        contacts.append(_normalize_contact({"name": name, "emails": [email]}))
        _save_local_contacts(contacts)
        return True

    contact_uid = str(uuid.uuid4())
    vcard = _build_vcard(name, email, contact_uid)
    url = cfg["url"].rstrip("/") + "/" + contact_uid + ".vcf"
    try:
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
    return cfg["url"].rstrip("/") + "/" + quote(uid, safe="") + ".vcf"


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
        imported = 0
        for c in parsed:
            emails = [e for e in (c.get("emails") or []) if e]
            if emails and any(e.lower() in existing for e in emails):
                continue
            contacts.append(_normalize_contact(c))
            for e in emails:
                existing.add(e.lower())
            imported += 1
        if imported:
            _save_local_contacts(contacts)
        return {"imported": imported, "failed": 0, "total": len(parsed)}
    auth = (cfg["username"], cfg["password"]) if cfg["username"] else None
    # Split into individual cards. re.split drops the BEGIN line, so we
    # re-add it. Normalize CRLF.
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = []
    for chunk in raw.split("BEGIN:VCARD"):
        chunk = chunk.strip()
        if not chunk:
            continue
        # Trim anything after END:VCARD (defensive).
        end = chunk.upper().find("END:VCARD")
        body = chunk[: end + len("END:VCARD")] if end != -1 else chunk
        blocks.append("BEGIN:VCARD\n" + body)
    imported = 0
    failed = 0
    for block in blocks:
        # Extract or assign a UID.
        m = re.search(r"^UID:(.+)$", block, re.MULTILINE)
        uid = (m.group(1).strip() if m else "") or str(uuid.uuid4())
        if not m:
            # Inject a UID right after the VERSION line (or after BEGIN).
            if re.search(r"^VERSION:", block, re.MULTILINE):
                block = re.sub(r"(^VERSION:.*$)", r"\1\nUID:" + uid, block, count=1, flags=re.MULTILINE)
            else:
                block = block.replace("BEGIN:VCARD", f"BEGIN:VCARD\nVERSION:4.0\nUID:{uid}", 1)
        elif not re.search(r"^VERSION:", block, re.MULTILINE):
            block = block.replace("BEGIN:VCARD", "BEGIN:VCARD\nVERSION:4.0", 1)
        vcard = block.replace("\n", "\r\n") + "\r\n"
        url = cfg["url"].rstrip("/") + "/" + quote(uid, safe="") + ".vcf"
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
    """Import contacts from CSV. Supports common headers:
    name/full_name/display_name, email/email_address/e-mail, phone/tel.
    Falls back to first columns as name,email,phone when no headers exist."""
    raw = (text or "").strip()
    if not raw:
        return {"imported": 0, "failed": 0, "total": 0, "error": "No CSV data found"}

    try:
        sample = raw[:2048]
        dialect = csv.Sniffer().sniff(sample)
    except Exception:
        dialect = csv.excel

    stream = io.StringIO(raw)
    try:
        has_header = csv.Sniffer().has_header(raw[:2048])
    except Exception:
        has_header = True

    rows = []
    if has_header:
        reader = csv.DictReader(stream, dialect=dialect)
        for row in reader:
            lowered = {str(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            name = (
                lowered.get("name") or lowered.get("full name") or lowered.get("full_name")
                or lowered.get("display name") or lowered.get("display_name")
                or lowered.get("fn") or ""
            )
            email = (
                lowered.get("email") or lowered.get("email address")
                or lowered.get("email_address") or lowered.get("e-mail")
                or lowered.get("mail") or ""
            )
            phone = lowered.get("phone") or lowered.get("telephone") or lowered.get("tel") or ""
            rows.append((name, email, phone))
    else:
        stream.seek(0)
        reader = csv.reader(stream, dialect=dialect)
        for row in reader:
            cols = [(c or "").strip() for c in row]
            if not any(cols):
                continue
            rows.append((
                cols[0] if len(cols) > 0 else "",
                cols[1] if len(cols) > 1 else "",
                cols[2] if len(cols) > 2 else "",
            ))

    imported = 0
    failed = 0
    total = 0
    existing_emails = {
        e.lower()
        for c in _fetch_contacts()
        for e in (c.get("emails") or [])
        if e
    }
    for name, email, phone in rows:
        email = (email or "").strip()
        name = (name or "").strip() or (email.split("@")[0] if email else "")
        if not email:
            continue
        total += 1
        if email.lower() in existing_emails:
            continue
        ok = _create_contact(name, email)
        if ok:
            imported += 1
            existing_emails.add(email.lower())
            # If the CSV had a phone number, rewrite the just-created row
            # through the richer update path so phone lands in CardDAV too.
            if phone:
                try:
                    contacts = _fetch_contacts(force=True)
                    created = next((c for c in contacts if email.lower() in [e.lower() for e in c.get("emails", [])]), None)
                    if created and created.get("uid"):
                        _update_contact(created["uid"], name, [email], [phone])
                except Exception:
                    pass
        else:
            failed += 1

    if imported:
        _contact_cache["fetched_at"] = None
    return {"imported": imported, "failed": failed, "total": total}


def _contacts_to_vcf(contacts: List[Dict]) -> str:
    return "".join(
        _build_vcard(
            c.get("name") or ((c.get("emails") or [""])[0].split("@")[0] if c.get("emails") else "Contact"),
            "",
            uid=c.get("uid") or str(uuid.uuid4()),
            emails=c.get("emails") or [],
            phones=c.get("phones") or [],
        )
        for c in contacts
    )


def _contacts_to_csv(contacts: List[Dict]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["name", "email", "phone"])
    for c in contacts:
        emails = c.get("emails") or [""]
        phones = c.get("phones") or [""]
        max_len = max(len(emails), len(phones), 1)
        for i in range(max_len):
            writer.writerow([
                c.get("name") or "",
                emails[i] if i < len(emails) else "",
                phones[i] if i < len(phones) else "",
            ])
    return out.getvalue()


def _update_contact(uid: str, name: str, emails: List[str], phones: List[str]) -> bool:
    """Rewrite an existing contact via CardDAV or local contacts."""
    cfg = _get_carddav_config()
    if not _carddav_configured(cfg):
        contacts = _load_local_contacts()
        found = False
        out = []
        for c in contacts:
            if c.get("uid") == uid:
                out.append(_normalize_contact({"uid": uid, "name": name, "emails": emails, "phones": phones}))
                found = True
            else:
                out.append(c)
        if not found:
            out.append(_normalize_contact({"uid": uid, "name": name, "emails": emails, "phones": phones}))
        _save_local_contacts(out)
        return True

    vcard = _build_vcard(name, "", uid=uid, emails=emails, phones=phones)
    # Use the real resource href (handles externally-created contacts whose
    # filename != UID); falls back to the <uid>.vcf guess.
    url = _resolve_resource_url(uid)
    try:
        auth = (cfg["username"], cfg["password"]) if cfg["username"] else None
        r = httpx.put(
            url,
            data=vcard.encode("utf-8"),
            headers={"Content-Type": "text/vcard; charset=utf-8"},
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


def _delete_contact(uid: str) -> bool:
    """Delete a contact via CardDAV or local contacts."""
    cfg = _get_carddav_config()
    if not _carddav_configured(cfg):
        contacts = _load_local_contacts()
        remaining = [c for c in contacts if c.get("uid") != uid]
        _save_local_contacts(remaining)
        return True

    url = _resolve_resource_url(uid)
    try:
        auth = (cfg["username"], cfg["password"]) if cfg["username"] else None
        r = httpx.delete(url, auth=auth, timeout=10)
        if r.status_code in (200, 204):
            _contact_cache["fetched_at"] = None
            return True
        if r.status_code == 404:
            # Resource not found at the resolved URL. With href resolution
            # this should be rare (genuinely already deleted). Invalidate
            # the cache and report success so the UI doesn't keep a ghost.
            logger.info(f"CardDAV DELETE 404 for {uid} — treating as already gone")
            _contact_cache["fetched_at"] = None
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
    async def list_contacts(_admin: str = Depends(require_admin)):
        """List all contacts."""
        contacts = _fetch_contacts()
        return {"contacts": contacts, "count": len(contacts)}

    @router.get("/search")
    async def search_contacts(q: str = Query(""), _admin: str = Depends(require_admin)):
        """Search contacts by name or email. Returns up to 10 matches."""
        contacts = _fetch_contacts()
        if not q:
            return {"results": []}
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
        return {"results": results[:10]}

    @router.post("/add")
    async def add_contact(data: dict, _admin: str = Depends(require_admin)):
        """Add a new contact."""
        name = data.get("name", "").strip()
        email = data.get("email", "").strip()
        if not email:
            return {"success": False, "error": "Email required"}
        # Check if already exists
        contacts = _fetch_contacts()
        for c in contacts:
            if email.lower() in [e.lower() for e in c["emails"]]:
                return {"success": True, "message": "Already exists", "contact": c}
        if not name:
            name = email.split("@")[0]
        ok = _create_contact(name, email)
        return {"success": ok}

    @router.post("/import")
    async def import_vcf(data: dict, _admin: str = Depends(require_admin)):
        """Import contacts from .vcf or CSV. Body: {"vcf": "..."} or {"csv": "..."}."""
        text = data.get("vcf") or data.get("text") or ""
        csv_text = data.get("csv") or ""
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
        format: str = Query("vcf", pattern="^(vcf|csv)$"),
        _admin: str = Depends(require_admin),
    ):
        """Export all contacts as vCard or CSV."""
        contacts = _fetch_contacts(force=True)
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
                settings[key] = data[key]
        _save_settings(settings)
        # Force re-fetch
        _contact_cache["fetched_at"] = None
        return {"success": True}

    @router.delete("/clear")
    async def clear_contacts(_admin: str = Depends(require_admin)):
        """Clear all local contacts. If CardDAV is configured, only clears the local fallback cache."""
        _save_local_contacts([])
        return {"success": True}

    # NOTE: the /{uid} routes are declared LAST so the literal paths above
    # (/list, /search, /add, /config) win — otherwise PUT /config would
    # match PUT /{uid} with uid="config".
    @router.put("/{uid}")
    async def edit_contact(uid: str, data: dict, _admin: str = Depends(require_admin)):
        """Edit an existing contact — name / emails / phones."""
        name = (data.get("name") or "").strip()
        emails = data.get("emails")
        phones = data.get("phones")
        if emails is None and data.get("email"):
            emails = [data["email"]]
        emails = [e.strip() for e in (emails or []) if e and e.strip()]
        phones = [p.strip() for p in (phones or []) if p and p.strip()]
        if not name and not emails:
            return {"success": False, "error": "Name or email required"}
        if not name and emails:
            name = emails[0].split("@")[0]
        ok = _update_contact(uid, name, emails, phones)
        return {"success": ok}

    @router.delete("/{uid}")
    async def delete_contact(uid: str, _admin: str = Depends(require_admin)):
        """Delete a contact by UID."""
        if not uid:
            return {"success": False, "error": "UID required"}
        ok = _delete_contact(uid)
        return {"success": ok}

    return router
