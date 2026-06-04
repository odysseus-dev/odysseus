// routes/contacts_routes.rs  <- routes/contacts_routes.py
//! CardDAV contacts integration — `/api/contacts/*` (routes WAVE 8, app.py include #40).
//!
//! Faithful translation of `routes/contacts_routes.py`'s `setup_contacts_routes()`
//! factory. The module reads from a local Radicale (or any CardDAV server) when
//! configured, and falls back to a local `data/contacts.json` store otherwise; it
//! supports list / search / add / edit / delete, vCard + CSV import, and vCard +
//! CSV export.
//!
//! ## Shape (the integration substrate)
//! * `setup_contacts_routes() -> Router<AppState>` mirrors the Python factory's
//!   `APIRouter(prefix="/api/contacts", tags=["contacts"])`; the absolute paths
//!   below are the `prefix + route` concatenations verbatim (axum 0.7 `:uid`).
//! * Every handler is `Depends(require_admin)`-gated — the FIRST thing each does is
//!   call [`admin_gate`], delegating to [`auth_adapter::require_admin`] (reads the
//!   `X-Odysseus-Internal-Token` header + the stamped `current_user`). On the
//!   `Err(())` arm the Python raises `HTTPException(403, "Admin only")`.
//! * `data: dict` bodies -> `Json<Value>` (a non-object body is treated as `{}` for
//!   `.get(...)`, since `data.get(...)` on a non-dict would raise before any work —
//!   and the handlers only ever `.get` string keys, returning the validation error).
//! * `format: str = Query("vcf", pattern="^(vcf|csv)$")` -> a `serde`-defaulted
//!   query struct; an out-of-pattern value 422s exactly like Pydantic's regex
//!   constraint (mirrored manually since axum has no regex query validation).
//!
//! ## The mail/xml/csv stack (PORT_NOW — no over-defer)
//! `contacts_routes.py` uses **synchronous** `httpx.request/get/put/delete` against
//! the CardDAV server (NOT `httpx.AsyncClient`), so every CardDAV HTTP round-trip
//! runs inside `tokio::task::spawn_blocking` over a `reqwest::blocking` client (the
//! `caldav_sync` precedent). The CardDAV REPORT `addressbook-query` response is
//! parsed with `quick-xml` (the `defusedxml.ElementTree` analogue — quick-xml does
//! not expand external entities by default, matching defusedxml's intent). CSV
//! import/export uses the `csv` crate, with a faithful port of CPython's
//! `csv.Sniffer` (`_guess_quote_and_delimiter` via `fancy-regex` backreferences +
//! `_guess_delimiter` frequency analysis + `has_header` type-voting) so the
//! delimiter / header detection matches Python byte-for-byte on the common inputs.
//! vCard parse/build is hand-rolled exactly as the Python (`re.split("BEGIN:VCARD")`
//! + line scan; RFC-6350 `_vesc`/`_vunesc`).
//!
//! ## The in-memory cache (module-global mutable state)
//! Python keeps a module-level `_contact_cache = {"contacts": [...], "fetched_at":
//! ...}` mutated across requests (a 60-second TTL on `_fetch_contacts`). The Rust
//! analogue is a `static Lazy<Mutex<ContactCache>>`; every sync helper that the
//! Python mutates (`_fetch_*`, `_save_local_contacts`, `_create/_update/_delete`,
//! the imports, config update) locks it inside `spawn_blocking`, so the observable
//! TTL + invalidation behavior is preserved.
//!
//! ## No path collision
//! `/api/contacts/*` is a fresh prefix the inline `web/mod.rs` subset never touches,
//! so the aggregator merges this router without an axum duplicate-`method`+`path`
//! panic. Purely additive (WAVE 8) — no reconciliation. The `/{uid}` PUT/DELETE
//! routes are declared LAST so the literal paths (`/list`, `/search`, `/add`,
//! `/import`, `/export`, `/config`, `/clear`) win — but axum's matcher already
//! prefers static segments over `:uid`, so ordering is belt-and-suspenders.


use std::collections::HashMap;
use std::sync::Mutex;

use axum::extract::{Query, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{delete, get, post, put};
use axum::{Extension, Json, Router};
use once_cell::sync::Lazy;
use percent_encoding::{utf8_percent_encode, AsciiSet, CONTROLS};
use serde::Deserialize;
use serde_json::{json, Map, Value};

use crate::core::middleware::INTERNAL_TOOL_HEADER;
use crate::pylog as logger;
use crate::routes::auth_adapter;
use crate::routes::{AppState, CurrentUser, HttpException};

// ===========================================================================
// Paths / settings (DATA_DIR / SETTINGS_FILE / LOCAL_CONTACTS_FILE)
// ===========================================================================

/// `DATA_DIR = Path(__file__).resolve().parent.parent / "data"` — honoring
/// `ODYSSEUS_DATA_DIR` via the shared [`crate::core::constants::DATA_DIR`].
fn data_dir() -> std::path::PathBuf {
    std::path::PathBuf::from(&*crate::core::constants::DATA_DIR)
}

/// `SETTINGS_FILE = DATA_DIR / "settings.json"`.
fn settings_file() -> std::path::PathBuf {
    data_dir().join("settings.json")
}

/// `LOCAL_CONTACTS_FILE = DATA_DIR / "contacts.json"`.
fn local_contacts_file() -> std::path::PathBuf {
    data_dir().join("contacts.json")
}

/// `_load_settings()` — `json.loads(SETTINGS_FILE.read_text())` if it exists, else
/// `{}`.
fn load_settings() -> Map<String, Value> {
    let path = settings_file();
    if path.exists() {
        if let Ok(text) = std::fs::read_to_string(&path) {
            if let Ok(Value::Object(m)) = serde_json::from_str::<Value>(&text) {
                return m;
            }
        }
    }
    Map::new()
}

/// `_save_settings(settings)` — `atomic_write_json(str(SETTINGS_FILE), settings,
/// indent=2)`.
fn save_settings(settings: &Map<String, Value>) {
    let value = Value::Object(settings.clone());
    let _ = crate::core::atomic_io::atomic_write_json(
        &settings_file().to_string_lossy(),
        &value,
        Some(2),
    );
}

// ===========================================================================
// CardDAV config (`_get_carddav_config` / `_carddav_configured`)
// ===========================================================================

/// The resolved CardDAV config — the dict `_get_carddav_config` returns, in
/// insertion order (`url`, `username`, `password`).
#[derive(Debug, Clone, Default)]
struct CardDavConfig {
    url: String,
    username: String,
    password: String,
}

/// `_get_carddav_config()` — settings.json values, falling back to the
/// `CARDDAV_URL` / `CARDDAV_USERNAME` / `CARDDAV_PASSWORD` env vars (default `""`).
fn get_carddav_config() -> CardDavConfig {
    let settings = load_settings();
    let pick = |key: &str, env: &str| -> String {
        match settings.get(key).and_then(|v| v.as_str()) {
            Some(s) => s.to_string(),
            None => crate::pyos::getenv(env, ""),
        }
    };
    CardDavConfig {
        url: pick("carddav_url", "CARDDAV_URL"),
        username: pick("carddav_username", "CARDDAV_USERNAME"),
        password: pick("carddav_password", "CARDDAV_PASSWORD"),
    }
}

/// `_carddav_configured(cfg)` — `bool((cfg.get("url") or "").strip())`.
fn carddav_configured(cfg: &CardDavConfig) -> bool {
    !cfg.url.trim().is_empty()
}

// ===========================================================================
// Contact model (`_normalize_contact`)
// ===========================================================================

/// One contact. The Python source carries TWO distinct dict shapes through the
/// same `_fetch_contacts` return value, with DIFFERENT key insertion orders that
/// FastAPI serializes verbatim — so the order is observable and must be preserved:
///
/// * **Locally-normalized** (`_normalize_contact`, py L70-75): the local-store path
///   and `_save_local_contacts` produce `{"uid", "name", "emails", "phones"}` — uid
///   FIRST, NO `href`. ([`normalize_contact`] sets [`Contact::normalized`] `true`.)
/// * **Raw `_parse_vcards`** (py L130): the CardDAV paths return the raw dict
///   `{"name": "", "emails": [], "phones": [], "uid": ""}` — name/emails/phones/uid,
///   uid LAST. The **REPORT** path (`_fetch_via_report`) additionally appends
///   `c["href"] = href_el.text.strip()` (py L263) as the final key; the **GET
///   fallback** (py L304) leaves it absent. ([`raw_to_contact`] sets `normalized`
///   `false`.)
///
/// `/list` (`{"contacts": contacts}`), `/search` (the appended raw `c`), and `/add`
/// (`{"contact": c}` on the already-exists branch) all serialize whichever dict
/// `_fetch_contacts` returned, so [`Contact::to_json`] must reproduce both orderings.
#[derive(Debug, Clone)]
struct Contact {
    uid: String,
    name: String,
    emails: Vec<String>,
    phones: Vec<String>,
    /// Server resource href, captured by `_fetch_via_report` (REPORT path only).
    href: Option<String>,
    /// `true` for the locally-normalized `_normalize_contact` dict shape (keys
    /// `uid, name, emails, phones`); `false` for the raw `_parse_vcards` shape
    /// (keys `name, emails, phones, uid[, href]`). Drives [`Contact::to_json`]'s
    /// key order so the observable JSON matches FastAPI's dict serialization.
    normalized: bool,
}

impl Default for Contact {
    fn default() -> Self {
        // The default ctor is used by test fixtures / placeholder values; treat it
        // as the normalized (local) shape, matching `_normalize_contact`'s output.
        Contact {
            uid: String::new(),
            name: String::new(),
            emails: Vec::new(),
            phones: Vec::new(),
            href: None,
            normalized: true,
        }
    }
}

impl Contact {
    /// The contact dict in Python key insertion order — which FastAPI serializes
    /// verbatim. For a locally-normalized contact (`normalized == true`) the keys
    /// are `uid, name, emails, phones` (`_normalize_contact`, py L70-75, never any
    /// `href`). For a raw `_parse_vcards` contact (`normalized == false`) the keys
    /// are `name, emails, phones, uid` (py L130), with `href` appended LAST when the
    /// REPORT path set it (py L263) and omitted otherwise.
    fn to_json(&self) -> Value {
        let mut m = Map::new();
        if self.normalized {
            m.insert("uid".into(), json!(self.uid));
            m.insert("name".into(), json!(self.name));
            m.insert("emails".into(), json!(self.emails));
            m.insert("phones".into(), json!(self.phones));
        } else {
            m.insert("name".into(), json!(self.name));
            m.insert("emails".into(), json!(self.emails));
            m.insert("phones".into(), json!(self.phones));
            m.insert("uid".into(), json!(self.uid));
            if let Some(href) = &self.href {
                m.insert("href".into(), json!(href));
            }
        }
        Value::Object(m)
    }
}

/// `_normalize_contact(contact)` — coalesce `emails`/`email` and `phones`/`phone`
/// into deduped, stripped lists; derive `name` from the first email local-part when
/// blank; mint a `uid` when missing.
///
/// `contact` is a loosely-typed dict (`Value`); the field accessors mirror the
/// Python `.get(...)`-with-`or`-fallback chains exactly.
fn normalize_contact(contact: &Value) -> Contact {
    // emails = contact.get("emails") or ([] if not contact.get("email") else [contact.get("email")])
    let mut emails: Vec<String> = Vec::new();
    let raw_emails = list_or_single(contact, "emails", "email");
    for e in raw_emails {
        let e = e.trim().to_string();
        if !e.is_empty() && !emails.contains(&e) {
            emails.push(e);
        }
    }
    // phones = contact.get("phones") or ([] if not contact.get("phone") else [contact.get("phone")])
    let mut phones: Vec<String> = Vec::new();
    let raw_phones = list_or_single(contact, "phones", "phone");
    for p in raw_phones {
        let p = p.trim().to_string();
        if !p.is_empty() && !phones.contains(&p) {
            phones.push(p);
        }
    }
    // name = str(contact.get("name") or "").strip()
    let mut name = value_to_str(contact.get("name")).trim().to_string();
    // if not name and emails: name = emails[0].split("@")[0]
    if name.is_empty() {
        if let Some(first) = emails.first() {
            name = first.split('@').next().unwrap_or("").to_string();
        }
    }
    // uid = str(contact.get("uid") or uuid.uuid4())
    let uid = {
        let raw = contact.get("uid");
        if value_is_truthy(raw) {
            value_to_str(raw)
        } else {
            uuid::Uuid::new_v4().to_string()
        }
    };
    Contact {
        uid,
        name,
        emails,
        phones,
        href: None,
        // `_normalize_contact` returns `{uid, name, emails, phones}` — uid-first.
        normalized: true,
    }
}

/// `contact.get(list_key) or ([] if not contact.get(single_key) else
/// [contact.get(single_key)])` — the email/phone list coalescing. Each element is
/// rendered as a string (`str(e or "")`) just before the caller strips it.
fn list_or_single(contact: &Value, list_key: &str, single_key: &str) -> Vec<String> {
    // The list value, if truthy (non-empty array / any non-falsy value).
    let list_val = contact.get(list_key);
    if value_is_truthy(list_val) {
        // Iterate as a list when it IS a JSON array; Python would iterate any
        // iterable, but in practice these are always lists. `str(e or "")`.
        if let Some(arr) = list_val.and_then(|v| v.as_array()) {
            return arr.iter().map(value_falsy_to_str).collect();
        }
        // Truthy non-array (string) — Python would iterate its characters; not a
        // real shape here, but reproduce by yielding the single rendered value.
        return vec![value_falsy_to_str(list_val.unwrap())];
    }
    // Falsy list -> `[] if not single else [single]`.
    let single = contact.get(single_key);
    if value_is_truthy(single) {
        vec![value_falsy_to_str(single.unwrap())]
    } else {
        Vec::new()
    }
}

/// Python truthiness for a `data.get(key)` result: `None`/absent, empty string,
/// empty array/object, `false`, `0` are falsy.
fn value_is_truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
        Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
    }
}

/// `str(v or "")` — render a value to a string, mapping a falsy/absent value to
/// `""`. Mirrors `str(e or "")` where a number/string is stringified.
fn value_falsy_to_str(v: &Value) -> String {
    match v {
        Value::Null => String::new(),
        Value::Bool(false) => String::new(),
        Value::String(s) if s.is_empty() => String::new(),
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        Value::Bool(true) => "True".to_string(),
        other => other.to_string(),
    }
}

/// `str(v or "")` for the `name` field — `str(contact.get("name") or "")`. A
/// JSON string is used as-is; anything else is rendered.
fn value_to_str(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) | Some(Value::Bool(false)) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) => n.to_string(),
        Some(Value::Bool(true)) => "True".to_string(),
        Some(other) => other.to_string(),
    }
}

// ===========================================================================
// Local contacts store (`_load_local_contacts` / `_save_local_contacts`)
// ===========================================================================

/// `_load_local_contacts()` — read `data/contacts.json`, accept either a bare list
/// or `{"contacts": [...]}`, normalize each dict, swallow any error to `[]`.
fn load_local_contacts() -> Vec<Contact> {
    let path = local_contacts_file();
    let result = (|| -> Result<Vec<Contact>, String> {
        if !path.exists() {
            return Ok(Vec::new());
        }
        let text = std::fs::read_to_string(&path).map_err(|e| e.to_string())?;
        let data: Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
        // rows = data.get("contacts", data) if isinstance(data, dict) else data
        let rows = match &data {
            Value::Object(_) => data
                .get("contacts")
                .cloned()
                .unwrap_or_else(|| data.clone()),
            _ => data.clone(),
        };
        // [_normalize_contact(c) for c in (rows or []) if isinstance(c, dict)]
        let mut out = Vec::new();
        if let Value::Array(arr) = rows {
            for c in &arr {
                if c.is_object() {
                    out.push(normalize_contact(c));
                }
            }
        }
        Ok(out)
    })();
    match result {
        Ok(v) => v,
        Err(e) => {
            logger::error(&format!("Failed to load local contacts: {e}"));
            Vec::new()
        }
    }
}

/// `_save_local_contacts(contacts)` — write `{"contacts": [normalized...]}` to
/// `data/contacts.json` (creating `DATA_DIR`), then refresh the in-memory cache.
///
/// Takes the cache guard so the cache write is part of the same critical section
/// the Python performs synchronously.
fn save_local_contacts(cache: &mut ContactCache, contacts: &[Contact]) {
    let _ = std::fs::create_dir_all(data_dir());
    let normalized: Vec<Contact> = contacts.iter().map(normalize_contact_struct).collect();
    let payload = json!({
        "contacts": normalized.iter().map(|c| c.to_json()).collect::<Vec<_>>(),
    });
    let _ = crate::core::atomic_io::atomic_write_json(
        &local_contacts_file().to_string_lossy(),
        &payload,
        Some(2),
    );
    // _contact_cache["contacts"] = [_normalize_contact(c) for c in contacts]
    cache.contacts = contacts.iter().map(normalize_contact_struct).collect();
    // _contact_cache["fetched_at"] = datetime.utcnow()
    cache.fetched_at = Some(std::time::Instant::now());
}

/// `_normalize_contact(c)` applied to an already-`Contact`-shaped value — used when
/// re-normalizing in `_save_local_contacts` (Python re-runs `_normalize_contact`
/// over the same dicts). Dropping the `href` matches the normalized output.
fn normalize_contact_struct(c: &Contact) -> Contact {
    let as_value = json!({
        "uid": c.uid,
        "name": c.name,
        "emails": c.emails,
        "phones": c.phones,
    });
    normalize_contact(&as_value)
}

// ===========================================================================
// vCard parsing (`_vunesc` / `_parse_vcards` / `_vesc` / `_build_vcard`)
// ===========================================================================

/// `_vunesc(value)` — reverse `_vesc`: turn escaped vCard text back into the raw
/// value. `\n`/`\N` -> newline; `\,`/`\;`/`\\` -> the literal; any other
/// `\<ch>` -> `<ch>`.
fn vunesc(value: &str) -> String {
    if value.is_empty() {
        return String::new();
    }
    let chars: Vec<char> = value.chars().collect();
    let mut out = String::with_capacity(value.len());
    let mut i = 0usize;
    while i < chars.len() {
        let ch = chars[i];
        if ch == '\\' && i + 1 < chars.len() {
            let nxt = chars[i + 1];
            if nxt == 'n' || nxt == 'N' {
                out.push('\n');
            } else {
                // ",", ";", "\\" -> the char itself; any other -> the char itself.
                out.push(nxt);
            }
            i += 2;
        } else {
            out.push(ch);
            i += 1;
        }
    }
    out
}

/// One raw vCard dict from `_parse_vcards`: `{name, emails, phones, uid}` (+ an
/// optional `href` the REPORT path attaches). NOT normalized.
#[derive(Debug, Clone, Default)]
struct RawVcard {
    name: String,
    emails: Vec<String>,
    phones: Vec<String>,
    uid: String,
    href: Option<String>,
}

/// Strip a leading RFC 6350 group token (e.g. `"item1."`) from a vCard line.
///
/// Apple Contacts / iCloud and many CardDAV servers emit grouped properties like
/// `item1.EMAIL:foo@bar` or `item1.EMAIL;TYPE=work:foo@bar`. Without stripping the
/// group prefix the property-name checks below miss those lines and silently drop
/// the email / phone.
///
/// Mirrors `re.sub(r"^[A-Za-z0-9-]+\.", "", line, count=1)` from Python line 139:
/// strip one leading `[A-Za-z0-9-]+.` group token (the dot is a literal period, not
/// a regex metachar). A non-grouped line is returned unchanged.
fn strip_vcard_group(line: &str) -> &str {
    // Find a '.' before any ':' or ';' (which would start parameters/value).
    // The group token must be [A-Za-z0-9-]+ followed by '.'.
    // Use the byte index; all chars in [A-Za-z0-9-] are single-byte ASCII.
    let bytes = line.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        let b = bytes[i];
        if b == b'.' {
            // Everything before i must be [A-Za-z0-9-] and non-empty.
            if i > 0 && bytes[..i].iter().all(|&c| c.is_ascii_alphanumeric() || c == b'-') {
                // Return the slice after the dot.
                return &line[i + 1..];
            }
            // '.' found but prefix contains non-group chars — stop scanning.
            break;
        }
        // ':' or ';' means we've reached property params/value without a '.'.
        if b == b':' || b == b';' {
            break;
        }
        i += 1;
    }
    line
}

/// `_parse_vcards(text)` — split a stream of vCards on `BEGIN:VCARD` and parse each
/// block's `FN`/`EMAIL`/`TEL`/`UID` lines into a dict. Keeps a block only when it
/// has a `name` or at least one email.
///
/// Each line is first stripped of leading whitespace, then any RFC 6350 group prefix
/// (e.g. `item1.`) is removed to yield `name_part`; all property checks and value
/// extraction operate on `name_part`. This matches the Python `re.sub` on line 139
/// that handles grouped vCard fields from Apple Contacts / iCloud / many CardDAV
/// servers.
fn parse_vcards(text: &str) -> Vec<RawVcard> {
    let mut contacts = Vec::new();
    // re.split(r"BEGIN:VCARD", text)
    for block in text.split("BEGIN:VCARD") {
        if block.trim().is_empty() {
            continue;
        }
        let mut contact = RawVcard::default();
        // for line in block.split("\n"): line = line.strip()
        for line in block.split('\n') {
            let line = line.trim();
            // Strip an optional RFC 6350 group prefix (e.g. "item1.") — without this
            // grouped properties like "item1.EMAIL:..." are silently dropped.
            let name_part = strip_vcard_group(line);
            if name_part.starts_with("FN:") || name_part.starts_with("FN;") {
                // contact["name"] = _vunesc(name_part.split(":",1)[1]) if ":" in name_part else ""
                contact.name = match name_part.split_once(':') {
                    Some((_, rest)) => vunesc(rest),
                    None => String::new(),
                };
            } else if name_part.starts_with("EMAIL") {
                if let Some((_, rest)) = name_part.split_once(':') {
                    let email_addr = vunesc(rest);
                    if !email_addr.is_empty() && !contact.emails.contains(&email_addr) {
                        contact.emails.push(email_addr);
                    }
                }
            } else if name_part.starts_with("TEL") {
                if let Some((_, rest)) = name_part.split_once(':') {
                    let phone = vunesc(rest);
                    if !phone.is_empty() && !contact.phones.contains(&phone) {
                        contact.phones.push(phone);
                    }
                }
            } else if let Some(rest) = name_part.strip_prefix("UID:") {
                // contact["uid"] = _vunesc(name_part[4:])
                contact.uid = vunesc(rest);
            }
        }
        if !contact.name.is_empty() || !contact.emails.is_empty() {
            contacts.push(contact);
        }
    }
    contacts
}

/// `_vesc(value)` — escape a vCard property VALUE per RFC 6350 §3.4: backslash,
/// newline, CR-strip, comma, semicolon. Order matches the Python chained
/// `.replace(...)` exactly (backslash first).
fn vesc(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('\n', "\\n")
        .replace('\r', "")
        .replace(',', "\\,")
        .replace(';', "\\;")
}

/// `_build_vcard(name, email, uid=None, emails=None, phones=None)` — build a vCard
/// string. `emails` (when `Some`) is authoritative; otherwise the single `email`
/// arg is used. The first email is marked `PREF=1`. All values RFC-6350-escaped.
fn build_vcard(
    name: &str,
    email: &str,
    uid: Option<&str>,
    emails: Option<&[String]>,
    phones: Option<&[String]>,
) -> String {
    // if not uid: uid = str(uuid.uuid4())
    let uid_owned;
    let uid = match uid {
        Some(u) if !u.is_empty() => u,
        _ => {
            uid_owned = uuid::Uuid::new_v4().to_string();
            &uid_owned
        }
    };
    // email_list = [e.strip() for e in (emails if emails is not None else ([email] if email else [])) if e and e.strip()]
    let email_source: Vec<String> = match emails {
        Some(list) => list.to_vec(),
        None => {
            if email.is_empty() {
                Vec::new()
            } else {
                vec![email.to_string()]
            }
        }
    };
    let email_list: Vec<String> = email_source
        .iter()
        .filter(|e| !e.is_empty() && !e.trim().is_empty())
        .map(|e| e.trim().to_string())
        .collect();
    // phone_list = [p.strip() for p in (phones or []) if p and p.strip()]
    let phone_list: Vec<String> = phones
        .unwrap_or(&[])
        .iter()
        .filter(|p| !p.is_empty() && !p.trim().is_empty())
        .map(|p| p.trim().to_string())
        .collect();
    // parts = name.strip().split()
    let parts: Vec<&str> = name.split_whitespace().collect();
    let (first, last) = if parts.len() >= 2 {
        (parts[0].to_string(), parts[1..].join(" "))
    } else {
        (name.to_string(), String::new())
    };
    // n_field = f"{_vesc(last)};{_vesc(first)};;;"
    let n_field = format!("{};{};;;", vesc(&last), vesc(&first));
    let mut lines = vec![
        "BEGIN:VCARD".to_string(),
        "VERSION:4.0".to_string(),
        format!("UID:{}", vesc(uid)),
        format!("FN:{}", vesc(name)),
        format!("N:{}", n_field),
    ];
    for (i, em) in email_list.iter().enumerate() {
        if i == 0 {
            lines.push(format!("EMAIL;PREF=1:{}", vesc(em)));
        } else {
            lines.push(format!("EMAIL:{}", vesc(em)));
        }
    }
    for ph in &phone_list {
        lines.push(format!("TEL:{}", vesc(ph)));
    }
    lines.push("END:VCARD".to_string());
    // "\r\n".join(lines) + "\r\n"
    lines.join("\r\n") + "\r\n"
}

// ===========================================================================
// In-memory cache + URL helpers
// ===========================================================================

/// `_contact_cache = {"contacts": [], "fetched_at": None}`. `fetched_at` is an
/// `Instant` (we only ever compute an age in seconds vs `now`, never serialize it),
/// matching `datetime.utcnow()` + the `(now - fetched_at).total_seconds()` check.
struct ContactCache {
    contacts: Vec<Contact>,
    fetched_at: Option<std::time::Instant>,
}

static CONTACT_CACHE: Lazy<Mutex<ContactCache>> = Lazy::new(|| {
    Mutex::new(ContactCache {
        contacts: Vec::new(),
        fetched_at: None,
    })
});

/// `quote(uid, safe="")` — percent-encode everything except RFC-3986 unreserved
/// chars (`ALPHA` / `DIGIT` / `-` / `_` / `.` / `~`). Built by starting from
/// "encode all non-alphanumerics" and removing the four unreserved punctuation
/// chars.
const QUOTE_SAFE_NONE: &AsciiSet = &CONTROLS
    .add(b' ')
    .add(b'!')
    .add(b'"')
    .add(b'#')
    .add(b'$')
    .add(b'%')
    .add(b'&')
    .add(b'\'')
    .add(b'(')
    .add(b')')
    .add(b'*')
    .add(b'+')
    .add(b',')
    .add(b'/')
    .add(b':')
    .add(b';')
    .add(b'<')
    .add(b'=')
    .add(b'>')
    .add(b'?')
    .add(b'@')
    .add(b'[')
    .add(b'\\')
    .add(b']')
    .add(b'^')
    .add(b'`')
    .add(b'{')
    .add(b'|')
    .add(b'}');

/// `urllib.parse.quote(value, safe="")`.
fn quote_safe_none(value: &str) -> String {
    utf8_percent_encode(value, QUOTE_SAFE_NONE).to_string()
}

/// `_abs_url(href)` — combine a multistatus `<href>` (an absolute path) with the
/// configured CardDAV origin so we get a fully-qualified URL. If `href` already
/// starts with `http`, return it as-is.
fn abs_url(href: &str, cfg: &CardDavConfig) -> String {
    if href.starts_with("http://") || href.starts_with("https://") {
        return href.to_string();
    }
    // urlparse(cfg["url"]); urlunparse((scheme, netloc, href, "", "", ""))
    match url::Url::parse(&cfg.url) {
        Ok(p) => {
            let scheme = p.scheme();
            // netloc = host[:port] (with userinfo if present, though Radicale URLs
            // don't carry it). url::Url::host_str + port reconstruct netloc.
            let netloc = match (p.host_str(), p.port()) {
                (Some(h), Some(port)) => format!("{h}:{port}"),
                (Some(h), None) => h.to_string(),
                (None, _) => String::new(),
            };
            // urlunparse with empty params/query/fragment. href is the path.
            format!("{scheme}://{netloc}{href}")
        }
        // urlparse never raises in Python for a malformed URL; mirror by best-effort
        // returning the href unchanged when we can't parse an origin.
        Err(_) => href.to_string(),
    }
}

/// `_vcard_url(uid)` — `cfg["url"].rstrip("/") + "/" + quote(uid, safe="") + ".vcf"`.
fn vcard_url(uid: &str, cfg: &CardDavConfig) -> String {
    format!(
        "{}/{}.vcf",
        cfg.url.trim_end_matches('/'),
        quote_safe_none(uid)
    )
}

// ===========================================================================
// CardDAV REPORT (`_ADDRESSBOOK_QUERY` / `_fetch_via_report`) — quick-xml
// ===========================================================================

/// The fixed CardDAV REPORT `addressbook-query` body.
const ADDRESSBOOK_QUERY: &str = concat!(
    r#"<?xml version="1.0" encoding="utf-8"?>"#,
    r#"<C:addressbook-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav">"#,
    r#"<D:prop><D:getetag/><C:address-data/></D:prop>"#,
    r#"<C:filter/>"#,
    r#"</C:addressbook-query>"#,
);

/// A blocking `reqwest` client with the CardDAV creds baked in via per-request
/// `basic_auth` — the synchronous `httpx.request(..., auth=auth)` analogue.
struct DavClient {
    client: reqwest::blocking::Client,
    auth: Option<(String, String)>,
}

impl DavClient {
    /// Build from a config: `auth = (username, password) if username else None`.
    fn new(cfg: &CardDavConfig, timeout_secs: u64) -> Result<Self, String> {
        let client = reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(timeout_secs))
            .build()
            .map_err(|e| e.to_string())?;
        let auth = if cfg.username.is_empty() {
            None
        } else {
            Some((cfg.username.clone(), cfg.password.clone()))
        };
        Ok(DavClient { client, auth })
    }

    fn apply_auth(&self, rb: reqwest::blocking::RequestBuilder) -> reqwest::blocking::RequestBuilder {
        match &self.auth {
            Some((u, p)) => rb.basic_auth(u, Some(p)),
            None => rb,
        }
    }
}

/// `_fetch_via_report(cfg, auth)` — try a CardDAV REPORT addressbook-query. Returns
/// `Some(contacts WITH href)` on success, or `None` if the server doesn't support
/// it / errors / parsed to zero (the empty-`<filter/>` guard).
///
/// Parses the 207 multistatus with `quick-xml` (the `defusedxml.ElementTree`
/// analogue): for each `D:response`, take `D:href` + `.//C:address-data`, feed the
/// address-data through `_parse_vcards`, keep the first parsed card, attach the
/// href.
fn fetch_via_report(client: &DavClient, cfg: &CardDavConfig) -> Option<Vec<RawVcard>> {
    let result = (|| -> Result<Option<Vec<RawVcard>>, String> {
        let method = reqwest::Method::from_bytes(b"REPORT").map_err(|e| e.to_string())?;
        let resp = client
            .apply_auth(
                client
                    .client
                    .request(method, &cfg.url)
                    .header(
                        reqwest::header::CONTENT_TYPE,
                        "application/xml; charset=utf-8",
                    )
                    .header("Depth", "1")
                    .body(ADDRESSBOOK_QUERY.as_bytes().to_vec()),
            )
            .send()
            .map_err(|e| e.to_string())?;
        // if r.status_code not in (207, 200): return None
        let code = resp.status().as_u16();
        if code != 207 && code != 200 {
            return Ok(None);
        }
        let text = resp.text().map_err(|e| e.to_string())?;
        // ET.fromstring(r.text) + findall D:response / D:href / .//C:address-data
        let responses = parse_report_responses(&text)?;
        let mut out = Vec::new();
        for (href, data) in responses {
            // Python's per-response guard is `if href_el is None or data_el is None
            // or not (data_el.text or "").strip(): continue` (py L257) — it skips
            // ONLY on a missing href/data element or empty address-data text. It does
            // NOT skip on empty <href> TEXT; an empty-href entry instead passes the
            // guard and reaches `href_el.text.strip()` -> AttributeError on None,
            // which the outer try/except (py L273) turns into a whole-REPORT abort +
            // GET fallback. `parse_report_responses` collapses an absent href element
            // and an empty <href></href> to the same empty string (quick-xml emits no
            // Text event for either), so we cannot cheaply tell them apart to
            // reproduce the abort; we match Python's common path by skipping only on
            // empty address-data (NOT on empty href). See notes — this is a documented
            // edge-case drift on malformed/empty-href REPORT responses.
            if data.trim().is_empty() {
                continue;
            }
            let parsed = parse_vcards(&data);
            if parsed.is_empty() {
                continue;
            }
            let mut c = parsed.into_iter().next().unwrap();
            // c["href"] = href_el.text.strip()
            c.href = Some(href.trim().to_string());
            out.push(c);
        }
        // If the REPORT parsed to ZERO contacts, don't trust it — return None.
        if out.is_empty() {
            return Ok(None);
        }
        Ok(Some(out))
    })();
    match result {
        Ok(v) => v,
        Err(e) => {
            logger::warning(&format!("CardDAV REPORT failed, falling back to GET: {e}"));
            None
        }
    }
}

/// Namespace-aware quick-xml parse of the REPORT multistatus: for each
/// `{DAV:}response`, return `(href_text, address_data_text)`. `href` is the FIRST
/// direct `{DAV:}href`; `address-data` is the FIRST descendant
/// `{urn:ietf:params:xml:ns:carddav}address-data` (the Python `.//C:address-data`).
fn parse_report_responses(xml: &str) -> Result<Vec<(String, String)>, String> {
    use quick_xml::events::Event;
    use quick_xml::name::ResolveResult;
    use quick_xml::reader::NsReader;

    const DAV: &[u8] = b"DAV:";
    const CARDDAV: &[u8] = b"urn:ietf:params:xml:ns:carddav";

    let mut reader = NsReader::from_str(xml);
    reader.config_mut().trim_text(false);

    let mut out: Vec<(String, String)> = Vec::new();
    // Per-response accumulators.
    let mut in_response = false;
    let mut response_depth = 0i32; // depth within the current <response>
    let mut href: Option<String> = None;
    let mut addr: Option<String> = None;
    // Capture state: when inside an href / address-data element, accumulate text.
    let mut capturing_href = false;
    let mut capturing_addr = false;
    let mut href_buf = String::new();
    let mut addr_buf = String::new();
    // Track depth so a nested <href> (inside address-data, unlikely) doesn't leak.
    let mut href_open_depth = 0i32;
    let mut addr_open_depth = 0i32;

    loop {
        let (ns, ev) = reader
            .read_resolved_event()
            .map_err(|e| format!("XML parse error: {e}"))?;
        match ev {
            Event::Start(e) => {
                let local = e.local_name();
                let lname = local.as_ref();
                let bound = matches!(ns, ResolveResult::Bound(n) if n.as_ref() == DAV);
                let cbound = matches!(ns, ResolveResult::Bound(n) if n.as_ref() == CARDDAV);
                if !in_response && bound && lname == b"response" {
                    in_response = true;
                    response_depth = 0;
                    href = None;
                    addr = None;
                    continue;
                }
                if in_response {
                    response_depth += 1;
                    // FIRST direct D:href (depth 1 within response).
                    if bound && lname == b"href" && href.is_none() && !capturing_href {
                        capturing_href = true;
                        href_buf.clear();
                        href_open_depth = response_depth;
                    } else if cbound
                        && lname == b"address-data"
                        && addr.is_none()
                        && !capturing_addr
                    {
                        // .//C:address-data — any descendant.
                        capturing_addr = true;
                        addr_buf.clear();
                        addr_open_depth = response_depth;
                    }
                }
            }
            Event::Text(t) => {
                // `xml_content` decodes bytes->str + normalizes EOLs. quick-xml 0.40
                // emits entity references (`&amp;`/`&lt;`/&c.) as SEPARATE
                // `Event::GeneralRef` events, NOT inline in `Text`, so literal text
                // here carries no entities — the `GeneralRef` arm below re-assembles
                // them into the captured value (the `ElementTree` `.text` analogue,
                // where the parser presents the fully-unescaped string).
                if capturing_href {
                    href_buf.push_str(
                        &t.xml_content(quick_xml::XmlVersion::Implicit1_0)
                            .map_err(|e| e.to_string())?,
                    );
                }
                if capturing_addr {
                    addr_buf.push_str(
                        &t.xml_content(quick_xml::XmlVersion::Implicit1_0)
                            .map_err(|e| e.to_string())?,
                    );
                }
            }
            Event::GeneralRef(r) => {
                // Resolve the reference to its character(s): a numeric `&#N;` / `&#xN;`
                // char-ref via `resolve_char_ref`, else a predefined XML entity
                // (`amp`/`lt`/`gt`/`quot`/`apos`). An unknown named entity is dropped
                // (Radicale's address-data only uses the predefined five).
                let resolved: String = if let Ok(Some(ch)) = r.resolve_char_ref() {
                    ch.to_string()
                } else {
                    let name = r.decode().map_err(|e| e.to_string())?;
                    quick_xml::escape::resolve_predefined_entity(&name)
                        .map(|s| s.to_string())
                        .unwrap_or_default()
                };
                if capturing_href {
                    href_buf.push_str(&resolved);
                }
                if capturing_addr {
                    addr_buf.push_str(&resolved);
                }
            }
            Event::CData(t) => {
                let decoded = t.decode().map_err(|e| e.to_string())?;
                if capturing_href {
                    href_buf.push_str(&decoded);
                }
                if capturing_addr {
                    addr_buf.push_str(&decoded);
                }
            }
            Event::End(e) => {
                let local = e.local_name();
                let lname = local.as_ref();
                let bound = matches!(ns, ResolveResult::Bound(n) if n.as_ref() == DAV);
                if in_response && bound && lname == b"response" && response_depth == 0 {
                    // Close the response: record it.
                    out.push((
                        href.take().unwrap_or_default(),
                        addr.take().unwrap_or_default(),
                    ));
                    in_response = false;
                    capturing_href = false;
                    capturing_addr = false;
                    continue;
                }
                if in_response {
                    if capturing_href && response_depth == href_open_depth {
                        capturing_href = false;
                        href = Some(href_buf.clone());
                    }
                    if capturing_addr && response_depth == addr_open_depth {
                        capturing_addr = false;
                        addr = Some(addr_buf.clone());
                    }
                    response_depth -= 1;
                }
            }
            Event::Eof => break,
            _ => {}
        }
    }
    Ok(out)
}

// ===========================================================================
// Fetch / resolve / mutate (the sync CardDAV + local helpers)
// ===========================================================================

/// `_fetch_contacts(force=False)` — fetch all contacts, CardDAV-or-local, with the
/// 60-second TTL. Operates on the locked cache.
fn fetch_contacts(cache: &mut ContactCache, force: bool) -> Vec<Contact> {
    // if not force and _contact_cache["fetched_at"]: age = ...; if age < 60: return cached
    if !force {
        if let Some(at) = cache.fetched_at {
            if at.elapsed().as_secs_f64() < 60.0 {
                return cache.contacts.clone();
            }
        }
    }
    let cfg = get_carddav_config();
    if !carddav_configured(&cfg) {
        let contacts = load_local_contacts();
        cache.contacts = contacts.clone();
        cache.fetched_at = Some(std::time::Instant::now());
        return contacts;
    }
    let result = (|| -> Result<Vec<Contact>, String> {
        let client = DavClient::new(&cfg, 10)?;
        // Preferred path: REPORT gives us hrefs.
        let raw = match fetch_via_report(&client, &cfg) {
            Some(r) => r,
            None => {
                // Fallback: plain GET.
                let resp = client
                    .apply_auth(client.client.get(&cfg.url))
                    .send()
                    .map_err(|e| e.to_string())?;
                if resp.status().as_u16() != 200 {
                    // logger.warning + return cached (we throw a sentinel to keep cache).
                    return Err(format!("__keep_cache__:{}", resp.status().as_u16()));
                }
                let text = resp.text().map_err(|e| e.to_string())?;
                parse_vcards(&text)
            }
        };
        Ok(raw.iter().map(raw_to_contact).collect())
    })();
    match result {
        Ok(contacts) => {
            cache.contacts = contacts.clone();
            cache.fetched_at = Some(std::time::Instant::now());
            contacts
        }
        Err(e) if e.starts_with("__keep_cache__:") => {
            // `if r.status_code != 200: logger.warning(...); return _contact_cache["contacts"]`
            let code = e.trim_start_matches("__keep_cache__:");
            logger::warning(&format!("CardDAV returned {code}"));
            cache.contacts.clone()
        }
        Err(e) => {
            // except Exception as e: logger.error(...); return _contact_cache["contacts"]
            logger::error(&format!("Failed to fetch contacts: {e}"));
            cache.contacts.clone()
        }
    }
}

/// `_fetch_contacts(force=False)` as the email-pipeline callers consume it —
/// `from routes.contacts_routes import _fetch_contacts`. Returns the contact dicts
/// (the `{uid, name, emails, phones[, href]}` shape `_normalize_contact` produces,
/// via [`Contact::to_json`]) so callers can `.get("name")` / `.get("email")` /
/// `.get("phone")` exactly as the Python does. NOTE the Python reads the SINGULAR
/// `email`/`phone` keys, which the normalized dict never carries (it stores the
/// plural `emails`/`phones` lists) — so those `.get` lookups are falsy, the
/// faithful behavior `_pre_retrieve_context` already relies on. Locks the shared
/// cache synchronously (a poisoned lock is recovered, matching the always-available
/// Python global). Must run under `spawn_blocking` like its sync callers.
pub fn fetch_contacts_json(force: bool) -> Vec<serde_json::Value> {
    let mut guard = CONTACT_CACHE.lock().unwrap_or_else(|p| p.into_inner());
    fetch_contacts(&mut guard, force)
        .iter()
        .map(Contact::to_json)
        .collect()
}

/// Carry a `RawVcard` (with its server `href`) into a `Contact`, preserving the
/// href in the cache. The cache stores the RAW parsed dicts (with `href`), NOT the
/// normalized output — `_fetch_via_report` returns the raw `c` (`{name, emails,
/// phones, uid, href}`), so the cache's `contacts` carry `href` for
/// `_resolve_resource_url`. The handler-facing serialization (`/list`/`/search`)
/// uses these dicts' `name`/`emails`/`phones` directly.
fn raw_to_contact(raw: &RawVcard) -> Contact {
    Contact {
        uid: raw.uid.clone(),
        name: raw.name.clone(),
        emails: raw.emails.clone(),
        phones: raw.phones.clone(),
        href: raw.href.clone(),
        // The raw `_parse_vcards` dict order is `name, emails, phones, uid[, href]`.
        normalized: false,
    }
}

/// `_resolve_resource_url(uid)` — map a UID to its real CardDAV resource URL. Uses
/// the cached href when available; refreshes once before falling back to the
/// `<uid>.vcf` guess.
fn resolve_resource_url(cache: &mut ContactCache, uid: &str, cfg: &CardDavConfig) -> String {
    let lookup = |cache: &ContactCache| -> Option<String> {
        for c in &cache.contacts {
            if c.uid == uid {
                if let Some(href) = &c.href {
                    if !href.is_empty() {
                        return Some(abs_url(href, cfg));
                    }
                }
            }
        }
        None
    };
    if let Some(found) = lookup(cache) {
        return found;
    }
    // Not in cache (or no href) — refresh once and retry. Swallow errors.
    let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        fetch_contacts(cache, true);
    }));
    lookup(cache).unwrap_or_else(|| vcard_url(uid, cfg))
}

/// `_create_contact(name, email)` — add a contact via CardDAV-or-local. Returns
/// `True`/`False`.
fn create_contact(cache: &mut ContactCache, name: &str, email: &str) -> bool {
    let cfg = get_carddav_config();
    if !carddav_configured(&cfg) {
        let mut contacts = load_local_contacts();
        let email_l = email.trim().to_lowercase();
        for c in &contacts {
            if !email_l.is_empty()
                && c.emails.iter().any(|e| e.to_lowercase() == email_l)
            {
                return true;
            }
        }
        let new_c = normalize_contact(&json!({"name": name, "emails": [email]}));
        contacts.push(new_c);
        save_local_contacts(cache, &contacts);
        return true;
    }
    let contact_uid = uuid::Uuid::new_v4().to_string();
    let vcard = build_vcard(name, email, Some(&contact_uid), None, None);
    let url = format!("{}/{}.vcf", cfg.url.trim_end_matches('/'), contact_uid);
    let result = (|| -> Result<bool, String> {
        let client = DavClient::new(&cfg, 10)?;
        let resp = client
            .apply_auth(
                client
                    .client
                    .put(&url)
                    .header("Content-Type", "text/vcard; charset=utf-8")
                    .body(vcard.into_bytes()),
            )
            .send()
            .map_err(|e| e.to_string())?;
        let code = resp.status().as_u16();
        if code == 200 || code == 201 || code == 204 {
            cache.fetched_at = None;
            return Ok(true);
        }
        let body = resp.text().unwrap_or_default();
        logger::warning(&format!(
            "CardDAV PUT returned {code}: {}",
            truncate_chars(&body, 200)
        ));
        Ok(false)
    })();
    match result {
        Ok(v) => v,
        Err(e) => {
            logger::error(&format!("Failed to create contact: {e}"));
            false
        }
    }
}

/// `_update_contact(uid, name, emails, phones)` — rewrite a contact via
/// CardDAV-or-local.
fn update_contact(
    cache: &mut ContactCache,
    uid: &str,
    name: &str,
    emails: &[String],
    phones: &[String],
) -> bool {
    let cfg = get_carddav_config();
    if !carddav_configured(&cfg) {
        let contacts = load_local_contacts();
        let mut found = false;
        let mut out: Vec<Contact> = Vec::new();
        for c in &contacts {
            if c.uid == uid {
                out.push(normalize_contact(&json!({
                    "uid": uid, "name": name, "emails": emails, "phones": phones,
                })));
                found = true;
            } else {
                out.push(c.clone());
            }
        }
        if !found {
            out.push(normalize_contact(&json!({
                "uid": uid, "name": name, "emails": emails, "phones": phones,
            })));
        }
        save_local_contacts(cache, &out);
        return true;
    }
    let vcard = build_vcard(name, "", Some(uid), Some(emails), Some(phones));
    let url = resolve_resource_url(cache, uid, &cfg);
    let result = (|| -> Result<bool, String> {
        let client = DavClient::new(&cfg, 10)?;
        let resp = client
            .apply_auth(
                client
                    .client
                    .put(&url)
                    .header("Content-Type", "text/vcard; charset=utf-8")
                    .body(vcard.into_bytes()),
            )
            .send()
            .map_err(|e| e.to_string())?;
        let code = resp.status().as_u16();
        if code == 200 || code == 201 || code == 204 {
            cache.fetched_at = None;
            return Ok(true);
        }
        let body = resp.text().unwrap_or_default();
        logger::warning(&format!(
            "CardDAV update PUT returned {code}: {}",
            truncate_chars(&body, 200)
        ));
        Ok(false)
    })();
    match result {
        Ok(v) => v,
        Err(e) => {
            logger::error(&format!("Failed to update contact: {e}"));
            false
        }
    }
}

/// `_delete_contact(uid)` — delete a contact via CardDAV-or-local.
fn delete_contact(cache: &mut ContactCache, uid: &str) -> bool {
    let cfg = get_carddav_config();
    if !carddav_configured(&cfg) {
        let contacts = load_local_contacts();
        let remaining: Vec<Contact> = contacts.into_iter().filter(|c| c.uid != uid).collect();
        save_local_contacts(cache, &remaining);
        return true;
    }
    let url = resolve_resource_url(cache, uid, &cfg);
    let result = (|| -> Result<bool, String> {
        let client = DavClient::new(&cfg, 10)?;
        let resp = client
            .apply_auth(client.client.delete(&url))
            .send()
            .map_err(|e| e.to_string())?;
        let code = resp.status().as_u16();
        if code == 200 || code == 204 {
            cache.fetched_at = None;
            return Ok(true);
        }
        if code == 404 {
            // Treat as already gone: invalidate cache + report success.
            logger::info(&format!(
                "CardDAV DELETE 404 for {uid} — treating as already gone"
            ));
            cache.fetched_at = None;
            return Ok(true);
        }
        let body = resp.text().unwrap_or_default();
        logger::warning(&format!(
            "CardDAV DELETE returned {code}: {}",
            truncate_chars(&body, 200)
        ));
        Ok(false)
    })();
    match result {
        Ok(v) => v,
        Err(e) => {
            logger::error(&format!("Failed to delete contact: {e}"));
            false
        }
    }
}

// ===========================================================================
// Import (`_import_vcards` / `_import_csv_contacts`) + export
// ===========================================================================

/// `_import_vcards(text)` — import a (possibly multi-card) vCard blob, PUTting each
/// card to CardDAV (preserving its full original content) or appending to the local
/// store. Returns `{imported, failed, total}`.
fn import_vcards(cache: &mut ContactCache, text: &str) -> Map<String, Value> {
    let cfg = get_carddav_config();
    // if not cfg.get("url"): local path
    if cfg.url.is_empty() {
        let parsed = parse_vcards(text);
        let mut contacts = load_local_contacts();
        // existing = {e.lower() for c in contacts for e in (c.emails or []) if e}
        let mut existing: std::collections::HashSet<String> = std::collections::HashSet::new();
        for c in &contacts {
            for e in &c.emails {
                if !e.is_empty() {
                    existing.insert(e.to_lowercase());
                }
            }
        }
        let mut imported = 0i64;
        for c in &parsed {
            let emails: Vec<String> = c.emails.iter().filter(|e| !e.is_empty()).cloned().collect();
            if !emails.is_empty() && emails.iter().any(|e| existing.contains(&e.to_lowercase())) {
                continue;
            }
            contacts.push(normalize_contact(&raw_vcard_to_value(c)));
            for e in &emails {
                existing.insert(e.to_lowercase());
            }
            imported += 1;
        }
        if imported > 0 {
            save_local_contacts(cache, &contacts);
        }
        let mut m = Map::new();
        m.insert("imported".into(), json!(imported));
        m.insert("failed".into(), json!(0));
        m.insert("total".into(), json!(parsed.len() as i64));
        return m;
    }

    // CardDAV path. raw = text.replace CRLF/CR -> LF; split on BEGIN:VCARD.
    let raw = text.replace("\r\n", "\n").replace('\r', "\n");
    let mut blocks: Vec<String> = Vec::new();
    for chunk in raw.split("BEGIN:VCARD") {
        let chunk = chunk.trim();
        if chunk.is_empty() {
            continue;
        }
        // Trim anything after END:VCARD (defensive). chunk.upper().find("END:VCARD")
        let body = match chunk.to_uppercase().find("END:VCARD") {
            Some(end) => {
                // chunk[: end + len("END:VCARD")] — note end is a byte index into the
                // UPPERCASED string; for ASCII vCard keywords this equals the byte
                // index into `chunk`. vCard property names are ASCII, so the offset is
                // stable (uppercasing is 1:1 in bytes for ASCII).
                let cut = end + "END:VCARD".len();
                if cut <= chunk.len() {
                    chunk[..cut].to_string()
                } else {
                    chunk.to_string()
                }
            }
            None => chunk.to_string(),
        };
        blocks.push(format!("BEGIN:VCARD\n{body}"));
    }

    let cfg_for_client = cfg.clone();
    let client = match DavClient::new(&cfg_for_client, 15) {
        Ok(c) => c,
        Err(e) => {
            logger::error(&format!("Failed to build HTTP client: {e}"));
            // Every PUT will fail; mirror by counting all as failed.
            let mut m = Map::new();
            m.insert("imported".into(), json!(0));
            m.insert("failed".into(), json!(blocks.len() as i64));
            m.insert("total".into(), json!(blocks.len() as i64));
            return m;
        }
    };

    let mut imported = 0i64;
    let mut failed = 0i64;
    for block in &blocks {
        // m = re.search(r"^UID:(.+)$", block, re.MULTILINE)
        let uid_match = find_line_capture(block, "UID:");
        let uid = match &uid_match {
            Some(u) if !u.trim().is_empty() => u.trim().to_string(),
            _ => uuid::Uuid::new_v4().to_string(),
        };
        let mut block = block.clone();
        if uid_match.is_none() {
            // Inject UID after VERSION line (or after BEGIN).
            if has_line_prefix(&block, "VERSION:") {
                block = inject_after_version(&block, &uid);
            } else {
                block = block.replacen(
                    "BEGIN:VCARD",
                    &format!("BEGIN:VCARD\nVERSION:4.0\nUID:{uid}"),
                    1,
                );
            }
        } else if !has_line_prefix(&block, "VERSION:") {
            block = block.replacen("BEGIN:VCARD", "BEGIN:VCARD\nVERSION:4.0", 1);
        }
        let vcard = block.replace('\n', "\r\n") + "\r\n";
        let url = format!("{}/{}.vcf", cfg.url.trim_end_matches('/'), quote_safe_none(&uid));
        let put = (|| -> Result<u16, String> {
            let resp = client
                .apply_auth(
                    client
                        .client
                        .put(&url)
                        .header("Content-Type", "text/vcard; charset=utf-8")
                        .body(vcard.into_bytes()),
                )
                .send()
                .map_err(|e| e.to_string())?;
            Ok(resp.status().as_u16())
        })();
        match put {
            Ok(code) if code == 200 || code == 201 || code == 204 => imported += 1,
            Ok(code) => {
                failed += 1;
                logger::warning(&format!("Import PUT {uid} returned {code}"));
            }
            Err(e) => {
                failed += 1;
                logger::error(&format!("Import PUT {uid} failed: {e}"));
            }
        }
    }
    if imported > 0 {
        cache.fetched_at = None;
    }
    let mut m = Map::new();
    m.insert("imported".into(), json!(imported));
    m.insert("failed".into(), json!(failed));
    m.insert("total".into(), json!(blocks.len() as i64));
    m
}

/// A `RawVcard` as a `Value` for `_normalize_contact`.
fn raw_vcard_to_value(c: &RawVcard) -> Value {
    json!({
        "name": c.name,
        "emails": c.emails,
        "phones": c.phones,
        "uid": c.uid,
    })
}

/// `re.search(r"^PREFIX(.+)$", block, re.MULTILINE)` — return the captured group
/// (everything after `PREFIX` on the first line that starts with it, requiring at
/// least one char). `None` when no such line.
fn find_line_capture(block: &str, prefix: &str) -> Option<String> {
    for line in block.split('\n') {
        // re.MULTILINE `^` matches at line starts; `$` before the newline. Python's
        // `.` excludes `\n`, and `(.+)` needs >= 1 char.
        let line = line.strip_suffix('\r').unwrap_or(line);
        if let Some(rest) = line.strip_prefix(prefix) {
            if !rest.is_empty() {
                return Some(rest.to_string());
            }
        }
    }
    None
}

/// `re.search(r"^PREFIX", block, re.MULTILINE)` exists?
fn has_line_prefix(block: &str, prefix: &str) -> bool {
    block.split('\n').any(|line| {
        let line = line.strip_suffix('\r').unwrap_or(line);
        line.starts_with(prefix)
    })
}

/// `re.sub(r"(^VERSION:.*$)", r"\1\nUID:" + uid, block, count=1, flags=MULTILINE)`
/// — append `\nUID:<uid>` right after the first `VERSION:` line.
fn inject_after_version(block: &str, uid: &str) -> String {
    let mut out: Vec<String> = Vec::new();
    let mut done = false;
    for line in block.split('\n') {
        let stripped = line.strip_suffix('\r').unwrap_or(line);
        out.push(line.to_string());
        if !done && stripped.starts_with("VERSION:") {
            out.push(format!("UID:{uid}"));
            done = true;
        }
    }
    out.join("\n")
}

/// `_contacts_to_vcf(contacts)` — concatenate one vCard per contact.
fn contacts_to_vcf(contacts: &[Contact]) -> String {
    let mut out = String::new();
    for c in contacts {
        // name = c.name or (emails[0].split("@")[0] if emails else "Contact")
        let name = if !c.name.is_empty() {
            c.name.clone()
        } else if let Some(first) = c.emails.first() {
            first.split('@').next().unwrap_or("").to_string()
        } else {
            "Contact".to_string()
        };
        let uid = if c.uid.is_empty() {
            uuid::Uuid::new_v4().to_string()
        } else {
            c.uid.clone()
        };
        out.push_str(&build_vcard(
            &name,
            "",
            Some(&uid),
            Some(&c.emails),
            Some(&c.phones),
        ));
    }
    out
}

/// `_contacts_to_csv(contacts)` — CSV with a `name,email,phone` header, one row per
/// email/phone index (the `max_len` zip).
fn contacts_to_csv(contacts: &[Contact]) -> String {
    let mut wtr = csv::WriterBuilder::new()
        .terminator(csv::Terminator::CRLF)
        .from_writer(Vec::new());
    let _ = wtr.write_record(["name", "email", "phone"]);
    for c in contacts {
        // emails = c.emails or [""]; phones = c.phones or [""]
        let emails: Vec<String> = if c.emails.is_empty() {
            vec![String::new()]
        } else {
            c.emails.clone()
        };
        let phones: Vec<String> = if c.phones.is_empty() {
            vec![String::new()]
        } else {
            c.phones.clone()
        };
        let max_len = emails.len().max(phones.len()).max(1);
        for i in 0..max_len {
            let email = emails.get(i).cloned().unwrap_or_default();
            let phone = phones.get(i).cloned().unwrap_or_default();
            let _ = wtr.write_record([c.name.clone(), email, phone]);
        }
    }
    let bytes = wtr.into_inner().unwrap_or_default();
    String::from_utf8_lossy(&bytes).to_string()
}

// ===========================================================================
// CSV import + the csv.Sniffer port
// ===========================================================================

/// `_import_csv_contacts(text)` — parse CSV (header-or-positional), create each new
/// contact, optionally rewrite phones. Returns `{imported, failed, total}` (or an
/// `error` key when empty).
fn import_csv_contacts(cache: &mut ContactCache, text: &str) -> Map<String, Value> {
    let raw = text.trim().to_string();
    if raw.is_empty() {
        let mut m = Map::new();
        m.insert("imported".into(), json!(0));
        m.insert("failed".into(), json!(0));
        m.insert("total".into(), json!(0));
        m.insert("error".into(), json!("No CSV data found"));
        return m;
    }
    // sample = raw[:2048]; dialect = Sniffer().sniff(sample) except -> excel
    let sample = take_chars(&raw, 2048);
    // csv.excel default delimiter when sniffing fails
    let delimiter = sniff_delimiter(&sample).unwrap_or(b',');
    // has_header = Sniffer().has_header(raw[:2048]) except -> True
    let has_header = sniff_has_header(&sample, delimiter).unwrap_or(true);

    // rows: Vec<(name, email, phone)>
    let mut rows: Vec<(String, String, String)> = Vec::new();
    if has_header {
        // csv.DictReader(stream, dialect)
        let mut rdr = csv::ReaderBuilder::new()
            .delimiter(delimiter)
            .has_headers(true)
            .flexible(true)
            .from_reader(raw.as_bytes());
        // Header keys lowercased + stripped at lookup time.
        let headers: Vec<String> = match rdr.headers() {
            Ok(h) => h.iter().map(|s| s.trim().to_lowercase()).collect(),
            Err(_) => Vec::new(),
        };
        for rec in rdr.records().flatten() {
            // lowered = {k.strip().lower(): (v or "").strip()}
            let mut lowered: HashMap<String, String> = HashMap::new();
            for (i, key) in headers.iter().enumerate() {
                let v = rec.get(i).unwrap_or("").trim().to_string();
                // DictReader keeps the LAST value for duplicate keys.
                lowered.insert(key.clone(), v);
            }
            let name = first_nonempty(&lowered, &[
                "name", "full name", "full_name", "display name", "display_name", "fn",
            ]);
            let email = first_nonempty(&lowered, &[
                "email", "email address", "email_address", "e-mail", "mail",
            ]);
            let phone = first_nonempty(&lowered, &["phone", "telephone", "tel"]);
            rows.push((name, email, phone));
        }
    } else {
        // csv.reader(stream, dialect)
        let mut rdr = csv::ReaderBuilder::new()
            .delimiter(delimiter)
            .has_headers(false)
            .flexible(true)
            .from_reader(raw.as_bytes());
        for rec in rdr.records().flatten() {
            let cols: Vec<String> = rec.iter().map(|c| c.trim().to_string()).collect();
            // if not any(cols): continue
            if !cols.iter().any(|c| !c.is_empty()) {
                continue;
            }
            rows.push((
                cols.first().cloned().unwrap_or_default(),
                cols.get(1).cloned().unwrap_or_default(),
                cols.get(2).cloned().unwrap_or_default(),
            ));
        }
    }

    let mut imported = 0i64;
    let mut failed = 0i64;
    let mut total = 0i64;
    // existing_emails = {e.lower() for c in _fetch_contacts() for e in c.emails if e}
    let mut existing_emails: std::collections::HashSet<String> = {
        let fetched = fetch_contacts(cache, false);
        let mut s = std::collections::HashSet::new();
        for c in &fetched {
            for e in &c.emails {
                if !e.is_empty() {
                    s.insert(e.to_lowercase());
                }
            }
        }
        s
    };
    for (name, email, phone) in &rows {
        let email = email.trim().to_string();
        // name = name.strip() or (email.split("@")[0] if email else "")
        let name = {
            let n = name.trim();
            if !n.is_empty() {
                n.to_string()
            } else if !email.is_empty() {
                email.split('@').next().unwrap_or("").to_string()
            } else {
                String::new()
            }
        };
        if email.is_empty() {
            continue;
        }
        total += 1;
        if existing_emails.contains(&email.to_lowercase()) {
            continue;
        }
        let ok = create_contact(cache, &name, &email);
        if ok {
            imported += 1;
            existing_emails.insert(email.to_lowercase());
            // If the CSV had a phone, rewrite through _update_contact.
            if !phone.is_empty() {
                let created = {
                    let fetched = fetch_contacts(cache, true);
                    fetched.into_iter().find(|c| {
                        c.emails
                            .iter()
                            .any(|e| e.to_lowercase() == email.to_lowercase())
                    })
                };
                if let Some(created) = created {
                    if !created.uid.is_empty() {
                        update_contact(
                            cache,
                            &created.uid,
                            &name,
                            std::slice::from_ref(&email),
                            std::slice::from_ref(phone),
                        );
                    }
                }
            }
        } else {
            failed += 1;
        }
    }
    if imported > 0 {
        cache.fetched_at = None;
    }
    let mut m = Map::new();
    m.insert("imported".into(), json!(imported));
    m.insert("failed".into(), json!(failed));
    m.insert("total".into(), json!(total));
    m
}

/// The `lowered.get(a) or lowered.get(b) or ... or ""` chain — first non-empty
/// value among the keys.
fn first_nonempty(map: &HashMap<String, String>, keys: &[&str]) -> String {
    for k in keys {
        if let Some(v) = map.get(*k) {
            if !v.is_empty() {
                return v.clone();
            }
        }
    }
    String::new()
}

/// `raw[:n]` by characters (Python slices by code point).
fn take_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

// ---------------------------------------------------------------------------
// csv.Sniffer port (faithful CPython algorithm)
// ---------------------------------------------------------------------------

/// `csv.Sniffer().sniff(sample).delimiter` — first `_guess_quote_and_delimiter`,
/// then `_guess_delimiter`. Returns `None` when neither finds one (the Python
/// `sniff` raises `Error`, which `_import_csv_contacts` catches -> `csv.excel`, so
/// the caller maps `None` -> `b','`).
fn sniff_delimiter(sample: &str) -> Option<u8> {
    let (_, delim_from_quote) = guess_quote_and_delimiter(sample);
    if let Some(d) = delim_from_quote {
        if !d.is_empty() {
            return d.as_bytes().first().copied();
        }
    }
    let (delim, _) = guess_delimiter(sample);
    if delim.is_empty() {
        None
    } else {
        delim.as_bytes().first().copied()
    }
}

/// `_guess_quote_and_delimiter` — returns `(quotechar, delimiter)`. We only need
/// the delimiter downstream (and whether a quotechar was found), so the doublequote
/// / skipinitialspace votes are not surfaced.
fn guess_quote_and_delimiter(data: &str) -> (Option<char>, Option<String>) {
    use fancy_regex::Regex;
    // The four patterns, tried in order; first that yields matches wins. These use
    // named groups + backreferences (fancy-regex supports both).
    let patterns = [
        r#"(?s)(?P<delim>[^\w\n"'])(?P<space> ?)(?P<quote>["']).*?(?P=quote)(?P=delim)"#,
        r#"(?sm)(?:^|\n)(?P<quote>["']).*?(?P=quote)(?P<delim>[^\w\n"'])(?P<space> ?)"#,
        r#"(?sm)(?P<delim>[^\w\n"'])(?P<space> ?)(?P<quote>["']).*?(?P=quote)(?:$|\n)"#,
        r#"(?sm)(?:^|\n)(?P<quote>["']).*?(?P=quote)(?:$|\n)"#,
    ];
    // Per-pattern, which named groups exist.
    let mut quotes: HashMap<char, usize> = HashMap::new();
    let mut delims: HashMap<char, usize> = HashMap::new();
    let mut matched_pattern: Option<usize> = None;
    let mut last_re: Option<Regex> = None;
    for (pi, pat) in patterns.iter().enumerate() {
        let re = match Regex::new(pat) {
            Ok(r) => r,
            Err(_) => continue,
        };
        // findall -> does it match at all?
        let mut any = false;
        for m in re.captures_iter(data).flatten() {
            any = true;
            if let Some(q) = m.name("quote") {
                if let Some(c) = q.as_str().chars().next() {
                    *quotes.entry(c).or_insert(0) += 1;
                }
            }
            if let Some(d) = m.name("delim") {
                let s = d.as_str();
                if let Some(c) = s.chars().next() {
                    *delims.entry(c).or_insert(0) += 1;
                }
            }
        }
        if any {
            matched_pattern = Some(pi);
            last_re = Some(re);
            break;
        }
    }
    let _ = (matched_pattern, last_re);
    if quotes.is_empty() && delims.is_empty() {
        return (None, None);
    }
    // quotechar = max(quotes, key=quotes.get)
    let quotechar = max_by_count(&quotes);
    // if delims: delim = max(delims, ...); if delim == '\n': delim = ''
    let delim = if !delims.is_empty() {
        let d = max_by_count(&delims);
        match d {
            Some('\n') => Some(String::new()),
            Some(c) => Some(c.to_string()),
            None => Some(String::new()),
        }
    } else {
        Some(String::new())
    };
    (quotechar, delim)
}

/// `max(map, key=map.get)` — the key with the largest count. CPython's `max` keeps
/// the FIRST maximal key in iteration order; dict iteration is insertion order. We
/// don't have insertion order on a HashMap, but the downstream use only cares about
/// the dominant delimiter, which is unambiguous for real CSV.
fn max_by_count(map: &HashMap<char, usize>) -> Option<char> {
    map.iter()
        .max_by_key(|(_, &v)| v)
        .map(|(&k, _)| k)
}

/// `_guess_delimiter(data, None)` — the frequency-based delimiter guess. Returns
/// `(delimiter, skipinitialspace)`; we only use the delimiter.
fn guess_delimiter(data: &str) -> (String, bool) {
    // data = list(filter(None, data.split('\n')))
    let lines: Vec<&str> = data.split('\n').filter(|l| !l.is_empty()).collect();
    if lines.is_empty() {
        return (String::new(), false);
    }
    let ascii_chars: Vec<char> = (0u8..127).map(|c| c as char).collect();
    let chunk_length = std::cmp::min(10, lines.len());
    let mut iteration = 0usize;
    // charFrequency: char -> {freq: count}
    let mut char_frequency: HashMap<char, HashMap<usize, usize>> = HashMap::new();
    // modes: char -> (freq, adjusted_count)
    let mut modes: HashMap<char, (usize, i64)> = HashMap::new();
    let mut delims: HashMap<char, (usize, i64)> = HashMap::new();
    let mut start = 0usize;
    let mut end = chunk_length;

    while start < lines.len() {
        iteration += 1;
        for line in &lines[start..std::cmp::min(end, lines.len())] {
            for &ch in &ascii_chars {
                let meta = char_frequency.entry(ch).or_default();
                let freq = line.matches(ch).count();
                *meta.entry(freq).or_insert(0) += 1;
            }
        }
        for ch in char_frequency.keys().copied().collect::<Vec<_>>() {
            let items: Vec<(usize, usize)> = char_frequency[&ch]
                .iter()
                .map(|(&k, &v)| (k, v))
                .collect();
            // if len(items) == 1 and items[0][0] == 0: continue
            if items.len() == 1 && items[0].0 == 0 {
                continue;
            }
            if items.len() > 1 {
                // mode = max(items, key=lambda x: x[1])
                let mode = *items
                    .iter()
                    .max_by(|a, b| a.1.cmp(&b.1))
                    .unwrap();
                // items.remove(mode); adjusted = mode[1] - sum(other counts)
                let sum_others: i64 = items
                    .iter()
                    .filter(|it| **it != mode)
                    .map(|it| it.1 as i64)
                    .sum();
                modes.insert(ch, (mode.0, mode.1 as i64 - sum_others));
            } else {
                modes.insert(ch, (items[0].0, items[0].1 as i64));
            }
        }
        // total = float(min(chunkLength * iteration, len(data)))
        let total = std::cmp::min(chunk_length * iteration, lines.len()) as f64;
        let mut consistency = 1.0f64;
        let threshold = 0.9f64;
        while delims.is_empty() && consistency >= threshold {
            for (&k, &v) in &modes {
                if v.0 > 0 && v.1 > 0 && (v.1 as f64 / total) >= consistency {
                    delims.insert(k, v);
                }
            }
            consistency -= 0.01;
        }
        if delims.len() == 1 {
            let delim = *delims.keys().next().unwrap();
            return (delim.to_string(), skipinitialspace(&lines, delim));
        }
        start = end;
        end += chunk_length;
    }

    if delims.is_empty() {
        return (String::new(), false);
    }
    // if len(delims) > 1: fall back to preferred list
    if delims.len() > 1 {
        for d in [',', '\t', ';', ' ', ':'] {
            if delims.contains_key(&d) {
                return (d.to_string(), skipinitialspace(&lines, d));
            }
        }
    }
    // items = [(v,k) for k,v in delims.items()]; items.sort(); delim = items[-1][1]
    let mut items: Vec<((usize, i64), char)> = delims.iter().map(|(&k, &v)| (v, k)).collect();
    items.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.cmp(&b.1)));
    let delim = items.last().unwrap().1;
    (delim.to_string(), skipinitialspace(&lines, delim))
}

/// `data[0].count(delim) == data[0].count("%c " % delim)` — used only for the
/// returned `skipinitialspace`, which the caller discards. Kept faithful anyway.
fn skipinitialspace(lines: &[&str], delim: char) -> bool {
    let first = lines.first().copied().unwrap_or("");
    let a = first.matches(delim).count();
    let needle = format!("{delim} ");
    let b = first.matches(&needle).count();
    a == b
}

/// `csv.Sniffer().has_header(sample)` — type-vote whether row 0 is a header. Uses
/// the sniffed `delimiter`. Returns `None` on the `sniff`-failed path (the caller
/// maps that to `True`).
fn sniff_has_header(sample: &str, delimiter: u8) -> Option<bool> {
    let mut rdr = csv::ReaderBuilder::new()
        .delimiter(delimiter)
        .has_headers(false)
        .flexible(true)
        .from_reader(sample.as_bytes());
    let mut records = rdr.records();
    // header = next(rdr)
    let header: Vec<String> = match records.next() {
        Some(Ok(r)) => r.iter().map(|s| s.to_string()).collect(),
        _ => return Some(false), // StopIteration -> Python raises; caller -> True. But
                                  // an empty sample never reaches here (raw is non-empty).
    };
    let columns = header.len();
    // columnTypes: col -> Option<ColType>; None = unset, Some(Number)/Some(Len(n))
    #[derive(Clone, Copy, PartialEq)]
    enum ColType {
        Number,
        Len(usize),
    }
    // Track present columns (Python deletes a column when its type is inconsistent).
    let mut column_types: HashMap<usize, Option<ColType>> = HashMap::new();
    for i in 0..columns {
        column_types.insert(i, None);
    }

    for (checked, rec) in records.enumerate() {
        if checked > 20 {
            break;
        }
        let row: Vec<String> = match rec {
            Ok(r) => r.iter().map(|s| s.to_string()).collect(),
            Err(_) => continue,
        };
        if row.len() != columns {
            continue;
        }
        for col in column_types.keys().copied().collect::<Vec<_>>() {
            // thisType = complex; try complex(row[col]) except -> len(row[col])
            let cell = &row[col];
            let this_type = if parse_complex(cell).is_some() {
                ColType::Number
            } else {
                ColType::Len(cell.chars().count())
            };
            match column_types.get(&col).copied().flatten() {
                None => {
                    column_types.insert(col, Some(this_type));
                }
                Some(existing) => {
                    if existing != this_type {
                        // type inconsistent -> remove column
                        column_types.remove(&col);
                    }
                }
            }
        }
    }

    // vote
    let mut has_header = 0i64;
    for (&col, col_type) in &column_types {
        match col_type {
            Some(ColType::Len(n)) => {
                let hlen = header.get(col).map(|h| h.chars().count()).unwrap_or(0);
                if hlen != *n {
                    has_header += 1;
                } else {
                    has_header -= 1;
                }
            }
            Some(ColType::Number) => {
                // try colType(header[col]) — complex(header[col])
                let h = header.get(col).map(|s| s.as_str()).unwrap_or("");
                if parse_complex(h).is_some() {
                    has_header -= 1;
                } else {
                    has_header += 1;
                }
            }
            None => {}
        }
    }
    Some(has_header > 0)
}

/// `complex(s)` success test — Python's `complex()` accepts ints, floats, and
/// `a+bj` forms. For the Sniffer's purpose (distinguishing numeric cells from
/// text), parse the common int/float forms; the rare `j`-suffixed complex literal
/// is also accepted to stay faithful.
fn parse_complex(s: &str) -> Option<f64> {
    let t = s.trim();
    if t.is_empty() {
        return None;
    }
    // Plain real number.
    if let Ok(f) = t.parse::<f64>() {
        return Some(f);
    }
    // Python `complex()` accepts forms like "1j", "1+2j", "(1+2j)". Accept a
    // trailing 'j'/'J' on an otherwise-numeric token, and parenthesized forms.
    let inner = t.strip_prefix('(').and_then(|x| x.strip_suffix(')')).unwrap_or(t);
    if let Some(num) = inner.strip_suffix(['j', 'J']) {
        if num.is_empty() || num == "+" || num == "-" {
            return Some(0.0);
        }
        if num.parse::<f64>().is_ok() {
            return Some(0.0);
        }
    }
    None
}

/// `r.text[:n]` — slice by characters.
fn truncate_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

// ===========================================================================
// Router + handlers
// ===========================================================================

/// `setup_contacts_routes()` — assemble the contacts router.
///
/// app.py registers this as include-router #40. The Python
/// `APIRouter(prefix="/api/contacts", tags=["contacts"])` registers, in this order:
/// `GET /list`, `GET /search`, `POST /add`, `POST /import`, `GET /export`,
/// `GET /config`, `PUT /config`, `DELETE /clear`, then LAST `PUT /{uid}` +
/// `DELETE /{uid}`. Under the `/api/contacts` prefix those resolve to the absolute
/// paths below. The shared-path verbs (`/config` GET+PUT, `/:uid` PUT+DELETE) are
/// grouped on one `.route(...)` call.
pub fn setup_contacts_routes() -> Router<AppState> {
    Router::new()
        .route("/api/contacts/list", get(list_contacts))
        .route("/api/contacts/search", get(search_contacts))
        .route("/api/contacts/add", post(add_contact))
        .route("/api/contacts/import", post(import_vcf))
        .route("/api/contacts/export", get(export_contacts))
        .route(
            "/api/contacts/config",
            get(get_config).put(update_config),
        )
        .route("/api/contacts/clear", delete(clear_contacts))
        .route(
            "/api/contacts/:uid",
            put(edit_contact).delete(delete_contact_handler),
        )
}

/// `require_admin(request)` — the first thing every handler does.
fn admin_gate(
    state: &AppState,
    headers: &HeaderMap,
    user: Option<&CurrentUser>,
) -> Result<(), HttpException> {
    let internal_header = headers
        .get(INTERNAL_TOOL_HEADER)
        .and_then(|v| v.to_str().ok());
    let user = user.map(|u| u.0.as_str());
    auth_adapter::require_admin(state, internal_header, user)
}

/// Run a closure that needs the locked cache inside `spawn_blocking` (the sync
/// httpx + cache mutation + FS work Python does on the event loop). The closure
/// receives `&mut ContactCache`. A poisoned lock is recovered (we only ever hold it
/// briefly and never panic mid-mutation), matching Python's always-available global.
async fn with_cache<F, T>(f: F) -> T
where
    F: FnOnce(&mut ContactCache) -> T + Send + 'static,
    T: Send + 'static,
{
    tokio::task::spawn_blocking(move || {
        let mut guard = CONTACT_CACHE.lock().unwrap_or_else(|p| p.into_inner());
        f(&mut guard)
    })
    .await
    .expect("contacts cache task panicked")
}

// ===========================================================================
// Public async wrappers for the agent tools (`do_resolve_contact` /
// `do_manage_contact`, tool_implementations.py:3736-3858).
//
// Python drives the CardDAV stack from those tools via `cc = importlib...
// contacts_routes` then `asyncio.to_thread(cc._fetch_contacts[, True])`,
// `asyncio.to_thread(cc._create_contact, name, email)`, etc. — i.e. the
// blocking sync helpers run off the event loop. These wrappers reproduce that
// exactly: they run the EXISTING private sync fns inside the existing
// `with_cache` (spawn_blocking) helper, which both holds the shared cache lock
// briefly and keeps the blocking httpx/CardDAV work off the async runtime. The
// sync fns themselves are unchanged.
// ===========================================================================

/// `asyncio.to_thread(cc._fetch_contacts[, force])` — fetch all contacts and
/// serialize each to its JSON dict (the `{uid, name, emails, phones[, href]}`
/// shape, via [`Contact::to_json`]) so the tool layer consumes them exactly like
/// the Python `cc._fetch_contacts(...)` list-of-dicts. `force=False` honors the
/// 60s TTL; `force=True` bypasses it (the `do_manage_contact` list/add path).
pub async fn fetch_contacts_json_async(force: bool) -> Vec<serde_json::Value> {
    with_cache(move |cache| {
        fetch_contacts(cache, force)
            .iter()
            .map(Contact::to_json)
            .collect()
    })
    .await
}

/// `asyncio.to_thread(cc._create_contact, name, email)` — add a contact via
/// CardDAV-or-local. Returns the sync helper's `True`/`False`.
pub async fn create_contact_pub(name: String, email: String) -> bool {
    with_cache(move |cache| create_contact(cache, &name, &email)).await
}

/// `asyncio.to_thread(cc._update_contact, uid, name, emails, phones)` — rewrite a
/// contact via CardDAV-or-local. Returns the sync helper's `True`/`False`.
pub async fn update_contact_pub(
    uid: String,
    name: String,
    emails: Vec<String>,
    phones: Vec<String>,
) -> bool {
    with_cache(move |cache| update_contact(cache, &uid, &name, &emails, &phones)).await
}

/// `asyncio.to_thread(cc._delete_contact, uid)` — delete a contact via
/// CardDAV-or-local. Returns the sync helper's `True`/`False`.
pub async fn delete_contact_pub(uid: String) -> bool {
    with_cache(move |cache| delete_contact(cache, &uid)).await
}

/// `data.get(key, "")` / `data.get(key)` helpers over a `Json<Value>` body that may
/// not be an object.
fn body_str(body: &Value, key: &str) -> String {
    body.get(key)
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

/// `GET /api/contacts/list` — list all contacts.
async fn list_contacts(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    let contacts = with_cache(|cache| fetch_contacts(cache, false)).await;
    let count = contacts.len();
    Ok(Json(json!({
        "contacts": contacts.iter().map(|c| c.to_json()).collect::<Vec<_>>(),
        "count": count,
    }))
    .into_response())
}

#[derive(Debug, Deserialize)]
struct SearchQuery {
    #[serde(default)]
    q: String,
}

/// `GET /api/contacts/search?q=...` — up to 10 matches by name/email substring.
async fn search_contacts(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Query(query): Query<SearchQuery>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    let q = query.q;
    let contacts = with_cache(|cache| fetch_contacts(cache, false)).await;
    if q.is_empty() {
        return Ok(Json(json!({"results": []})).into_response());
    }
    let q_lower = q.to_lowercase();
    let mut results: Vec<Value> = Vec::new();
    for c in &contacts {
        if c.name.to_lowercase().contains(&q_lower) {
            results.push(c.to_json());
            continue;
        }
        for em in &c.emails {
            if em.to_lowercase().contains(&q_lower) {
                results.push(c.to_json());
                break;
            }
        }
    }
    results.truncate(10);
    Ok(Json(json!({"results": results})).into_response())
}

/// `POST /api/contacts/add` — add a new contact (idempotent on existing email).
async fn add_contact(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Json(data): Json<Value>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    let name = body_str(&data, "name");
    let name = name.trim().to_string();
    let email = body_str(&data, "email");
    let email = email.trim().to_string();
    if email.is_empty() {
        return Ok(Json(json!({"success": false, "error": "Email required"})).into_response());
    }
    let email_for_task = email.clone();
    let response = with_cache(move |cache| {
        let contacts = fetch_contacts(cache, false);
        let email_l = email_for_task.to_lowercase();
        for c in &contacts {
            if c.emails.iter().any(|e| e.to_lowercase() == email_l) {
                // return {"success": True, "message": "Already exists", "contact": c}
                return json!({
                    "success": true,
                    "message": "Already exists",
                    "contact": c.to_json(),
                });
            }
        }
        let final_name = if name.is_empty() {
            email_for_task.split('@').next().unwrap_or("").to_string()
        } else {
            name.clone()
        };
        let ok = create_contact(cache, &final_name, &email_for_task);
        json!({"success": ok})
    })
    .await;
    Ok(Json(response).into_response())
}

/// `POST /api/contacts/import` — import from `vcf`/`text` or `csv`.
async fn import_vcf(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Json(data): Json<Value>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    // text = data.get("vcf") or data.get("text") or ""
    let text = {
        let vcf = body_str(&data, "vcf");
        if !vcf.is_empty() {
            vcf
        } else {
            let t = body_str(&data, "text");
            if !t.is_empty() {
                t
            } else {
                String::new()
            }
        }
    };
    let csv_text = body_str(&data, "csv");
    if !text.trim().is_empty() {
        // if "BEGIN:VCARD" not in text.upper(): error
        if !text.to_uppercase().contains("BEGIN:VCARD") {
            return Ok(
                Json(json!({"success": false, "error": "No vCard data found"})).into_response()
            );
        }
        let result = with_cache(move |cache| import_vcards(cache, &text)).await;
        return Ok(Json(finalize_import(result)).into_response());
    } else if !csv_text.trim().is_empty() {
        let result = with_cache(move |cache| import_csv_contacts(cache, &csv_text)).await;
        return Ok(Json(finalize_import(result)).into_response());
    }
    Ok(Json(json!({"success": false, "error": "No contact data found"})).into_response())
}

/// `result["success"] = result.get("imported", 0) > 0; return result`.
fn finalize_import(mut result: Map<String, Value>) -> Value {
    let imported = result
        .get("imported")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    result.insert("success".into(), json!(imported > 0));
    Value::Object(result)
}

#[derive(Debug, Deserialize)]
struct ExportQuery {
    #[serde(default = "default_export_format")]
    format: String,
}

fn default_export_format() -> String {
    "vcf".to_string()
}

/// `GET /api/contacts/export?format=vcf|csv` — download all contacts.
async fn export_contacts(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Query(query): Query<ExportQuery>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    // format: str = Query("vcf", pattern="^(vcf|csv)$") — 422 on a non-matching value.
    if query.format != "vcf" && query.format != "csv" {
        return Err(HttpException::with_detail(
            422,
            json!([{
                "type": "string_pattern_mismatch",
                "loc": ["query", "format"],
                "msg": "String should match pattern '^(vcf|csv)$'",
                "input": query.format,
                "ctx": {"pattern": "^(vcf|csv)$"},
            }]),
        ));
    }
    let fmt = query.format.clone();
    let (content, media_type, filename) = with_cache(move |cache| {
        let contacts = fetch_contacts(cache, true);
        if fmt == "csv" {
            (
                contacts_to_csv(&contacts),
                "text/csv; charset=utf-8",
                "odysseus-contacts.csv",
            )
        } else {
            (
                contacts_to_vcf(&contacts),
                "text/vcard; charset=utf-8",
                "odysseus-contacts.vcf",
            )
        }
    })
    .await;
    let resp = Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, media_type)
        .header(
            header::CONTENT_DISPOSITION,
            format!("attachment; filename=\"{filename}\""),
        )
        .body(axum::body::Body::from(content))
        .unwrap();
    Ok(resp)
}

/// `GET /api/contacts/config` — return the CardDAV config (password masked).
async fn get_config(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    let cfg = get_carddav_config();
    // if cfg["password"]: cfg["password"] = "***"
    let password = if cfg.password.is_empty() {
        cfg.password.clone()
    } else {
        "***".to_string()
    };
    Ok(Json(json!({
        "url": cfg.url,
        "username": cfg.username,
        "password": password,
    }))
    .into_response())
}

/// `PUT /api/contacts/config` — write any of the three carddav_* settings keys.
async fn update_config(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Json(data): Json<Value>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    let mut settings = load_settings();
    for key in ["carddav_url", "carddav_username", "carddav_password"] {
        if let Some(v) = data.get(key) {
            settings.insert(key.to_string(), v.clone());
        }
    }
    save_settings(&settings);
    // Force re-fetch: _contact_cache["fetched_at"] = None
    with_cache(|cache| {
        cache.fetched_at = None;
    })
    .await;
    Ok(Json(json!({"success": true})).into_response())
}

/// `DELETE /api/contacts/clear` — clear the local contacts store.
async fn clear_contacts(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    with_cache(|cache| save_local_contacts(cache, &[])).await;
    Ok(Json(json!({"success": true})).into_response())
}

/// `PUT /api/contacts/{uid}` — edit name / emails / phones.
async fn edit_contact(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    axum::extract::Path(uid): axum::extract::Path<String>,
    Json(data): Json<Value>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    let name = body_str(&data, "name");
    let mut name = name.trim().to_string();
    // emails = data.get("emails"); phones = data.get("phones")
    let mut emails: Vec<String> = json_str_list(data.get("emails"));
    let phones_raw = json_str_list(data.get("phones"));
    // if emails is None and data.get("email"): emails = [data["email"]]
    if data.get("emails").map(|v| v.is_null()).unwrap_or(true) {
        let single = body_str(&data, "email");
        if !single.is_empty() {
            emails = vec![single];
        }
    }
    // emails = [e.strip() for e in (emails or []) if e and e.strip()]
    let emails: Vec<String> = emails
        .into_iter()
        .filter(|e| !e.is_empty() && !e.trim().is_empty())
        .map(|e| e.trim().to_string())
        .collect();
    let phones: Vec<String> = phones_raw
        .into_iter()
        .filter(|p| !p.is_empty() && !p.trim().is_empty())
        .map(|p| p.trim().to_string())
        .collect();
    if name.is_empty() && emails.is_empty() {
        return Ok(
            Json(json!({"success": false, "error": "Name or email required"})).into_response()
        );
    }
    if name.is_empty() && !emails.is_empty() {
        name = emails[0].split('@').next().unwrap_or("").to_string();
    }
    let ok = with_cache(move |cache| update_contact(cache, &uid, &name, &emails, &phones)).await;
    Ok(Json(json!({"success": ok})).into_response())
}

/// `DELETE /api/contacts/{uid}` — delete by UID.
async fn delete_contact_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    axum::extract::Path(uid): axum::extract::Path<String>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    // if not uid: error. (axum never routes an empty :uid segment, but mirror it.)
    if uid.is_empty() {
        return Ok(Json(json!({"success": false, "error": "UID required"})).into_response());
    }
    let ok = with_cache(move |cache| delete_contact(cache, &uid)).await;
    Ok(Json(json!({"success": ok})).into_response())
}

/// `data.get(key)` -> `Option<list>` rendered as `Vec<String>`. `None`/absent ->
/// empty; non-list -> empty (Python would iterate it, but the only shapes here are
/// lists or `None`). Each element is `str(e)`.
fn json_str_list(v: Option<&Value>) -> Vec<String> {
    match v {
        Some(Value::Array(arr)) => arr.iter().map(value_falsy_to_str).collect(),
        _ => Vec::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn vesc_escapes_rfc6350() {
        assert_eq!(vesc("Sekisui House,Ltd"), "Sekisui House\\,Ltd");
        // `\r` is REMOVED (not escaped), so "c\rd" -> "cd"; `;` -> `\;`, `\n` -> `\n`,
        // and the literal backslash before `e` -> `\\`.
        assert_eq!(vesc("a;b\nc\rd\\e"), "a\\;b\\ncd\\\\e");
        // Order: backslash first, so a literal backslash becomes \\ then commas etc.
        assert_eq!(vesc("\\,"), "\\\\\\,");
    }

    #[test]
    fn vunesc_reverses_vesc() {
        assert_eq!(vunesc("Sekisui House\\,Ltd"), "Sekisui House,Ltd");
        assert_eq!(vunesc("line1\\nline2"), "line1\nline2");
        assert_eq!(vunesc("a\\;b"), "a;b");
        // \N also -> newline.
        assert_eq!(vunesc("a\\Nb"), "a\nb");
        // trailing backslash with no following char -> kept.
        assert_eq!(vunesc("end\\"), "end\\");
    }

    #[test]
    fn build_vcard_single_email_pref() {
        let vc = build_vcard("Ada Lovelace", "ada@x.io", Some("uid-1"), None, None);
        assert!(vc.contains("BEGIN:VCARD\r\n"));
        assert!(vc.contains("VERSION:4.0\r\n"));
        assert!(vc.contains("UID:uid-1\r\n"));
        assert!(vc.contains("FN:Ada Lovelace\r\n"));
        assert!(vc.contains("N:Lovelace;Ada;;;\r\n"));
        assert!(vc.contains("EMAIL;PREF=1:ada@x.io\r\n"));
        assert!(vc.ends_with("END:VCARD\r\n"));
    }

    #[test]
    fn build_vcard_multi_email_phones() {
        let emails = vec!["a@x.io".to_string(), "b@x.io".to_string()];
        let phones = vec!["+1 555".to_string()];
        let vc = build_vcard("Bob", "", Some("u2"), Some(&emails), Some(&phones));
        assert!(vc.contains("EMAIL;PREF=1:a@x.io\r\n"));
        assert!(vc.contains("EMAIL:b@x.io\r\n"));
        assert!(vc.contains("TEL:+1 555\r\n"));
    }

    #[test]
    fn parse_vcards_basic() {
        let text = "BEGIN:VCARD\nVERSION:4.0\nUID:u1\nFN:Grace Hopper\nEMAIL;PREF=1:grace@navy.mil\nTEL:+1 202\nEND:VCARD\n";
        let parsed = parse_vcards(text);
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0].name, "Grace Hopper");
        assert_eq!(parsed[0].emails, vec!["grace@navy.mil".to_string()]);
        assert_eq!(parsed[0].phones, vec!["+1 202".to_string()]);
        assert_eq!(parsed[0].uid, "u1");
    }

    #[test]
    fn parse_vcards_skips_empty_blocks_and_no_name_no_email() {
        // A block with only a TEL (no name, no email) is dropped.
        let text = "BEGIN:VCARD\nVERSION:4.0\nTEL:+1 555\nEND:VCARD\n";
        assert!(parse_vcards(text).is_empty());
    }

    #[test]
    fn strip_vcard_group_strips_group_prefix() {
        // Simple group prefix.
        assert_eq!(strip_vcard_group("item1.EMAIL:foo@bar"), "EMAIL:foo@bar");
        // Group + parameters.
        assert_eq!(strip_vcard_group("item1.EMAIL;TYPE=work:foo@bar"), "EMAIL;TYPE=work:foo@bar");
        // FN with a group prefix.
        assert_eq!(strip_vcard_group("item2.FN:Alice"), "FN:Alice");
        // No group prefix — returned unchanged.
        assert_eq!(strip_vcard_group("EMAIL:foo@bar"), "EMAIL:foo@bar");
        assert_eq!(strip_vcard_group("UID:abc-123"), "UID:abc-123");
        // A period in the value portion (after ':') does not trigger stripping.
        assert_eq!(strip_vcard_group("FN:Alice.Smith"), "FN:Alice.Smith");
        // Hyphen is allowed in group token.
        assert_eq!(strip_vcard_group("my-group.TEL:+1"), "TEL:+1");
        // Empty line stays empty.
        assert_eq!(strip_vcard_group(""), "");
    }

    #[test]
    fn parse_vcards_grouped_properties() {
        // Apple Contacts / iCloud emit grouped fields like "item1.EMAIL:..."
        // — these must be captured, not silently dropped.
        let text = concat!(
            "BEGIN:VCARD\n",
            "VERSION:4.0\n",
            "UID:gid-1\n",
            "FN:Alice Smith\n",
            "item1.EMAIL;TYPE=pref:alice@example.com\n",
            "item2.TEL:+1 800 555 0100\n",
            "END:VCARD\n",
        );
        let parsed = parse_vcards(text);
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0].name, "Alice Smith");
        assert_eq!(parsed[0].emails, vec!["alice@example.com".to_string()]);
        assert_eq!(parsed[0].phones, vec!["+1 800 555 0100".to_string()]);
        assert_eq!(parsed[0].uid, "gid-1");
    }

    #[test]
    fn parse_vcards_grouped_uid_and_fn() {
        // Group prefix on UID and FN lines is also stripped correctly.
        let text = concat!(
            "BEGIN:VCARD\n",
            "VERSION:4.0\n",
            "item0.UID:uid-grp\n",
            "item0.FN:Bob Jones\n",
            "item1.EMAIL:bob@example.org\n",
            "END:VCARD\n",
        );
        let parsed = parse_vcards(text);
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0].name, "Bob Jones");
        assert_eq!(parsed[0].uid, "uid-grp");
        assert_eq!(parsed[0].emails, vec!["bob@example.org".to_string()]);
    }

    #[test]
    fn parse_vcards_ungrouped_unchanged() {
        // Plain (non-grouped) vCard must still parse exactly as before.
        let text = concat!(
            "BEGIN:VCARD\n",
            "VERSION:4.0\n",
            "UID:plain-1\n",
            "FN:Grace Hopper\n",
            "EMAIL;PREF=1:grace@navy.mil\n",
            "TEL:+1 202\n",
            "END:VCARD\n",
        );
        let parsed = parse_vcards(text);
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0].name, "Grace Hopper");
        assert_eq!(parsed[0].emails, vec!["grace@navy.mil".to_string()]);
        assert_eq!(parsed[0].phones, vec!["+1 202".to_string()]);
        assert_eq!(parsed[0].uid, "plain-1");
    }

    #[test]
    fn normalize_contact_dedupes_and_derives_name() {
        let v = json!({"emails": ["a@x.io", "a@x.io", " b@x.io "], "phones": ["1", "1"]});
        let c = normalize_contact(&v);
        assert_eq!(c.emails, vec!["a@x.io".to_string(), "b@x.io".to_string()]);
        assert_eq!(c.phones, vec!["1".to_string()]);
        // name derived from first email local-part.
        assert_eq!(c.name, "a");
        assert!(!c.uid.is_empty());
    }

    #[test]
    fn normalize_contact_single_email_field() {
        let v = json!({"name": "X", "email": "solo@x.io", "phone": "9"});
        let c = normalize_contact(&v);
        assert_eq!(c.emails, vec!["solo@x.io".to_string()]);
        assert_eq!(c.phones, vec!["9".to_string()]);
        assert_eq!(c.name, "X");
    }

    #[test]
    fn quote_safe_none_encodes_path_chars() {
        assert_eq!(quote_safe_none("a/b"), "a%2Fb");
        assert_eq!(quote_safe_none("../x"), "..%2Fx");
        // unreserved chars stay.
        assert_eq!(quote_safe_none("Aa0-_.~"), "Aa0-_.~");
    }

    #[test]
    fn abs_url_combines_origin() {
        let cfg = CardDavConfig {
            url: "https://dav.example.com/u/me/addr/".to_string(),
            username: String::new(),
            password: String::new(),
        };
        assert_eq!(
            abs_url("/u/me/addr/x.vcf", &cfg),
            "https://dav.example.com/u/me/addr/x.vcf"
        );
        // Absolute href returned as-is.
        assert_eq!(
            abs_url("http://other/x.vcf", &cfg),
            "http://other/x.vcf"
        );
    }

    #[test]
    fn vcard_url_encodes_uid() {
        let cfg = CardDavConfig {
            url: "https://dav.example.com/addr/".to_string(),
            username: String::new(),
            password: String::new(),
        };
        assert_eq!(
            vcard_url("a/b", &cfg),
            "https://dav.example.com/addr/a%2Fb.vcf"
        );
    }

    #[test]
    fn report_xml_parses_href_and_address_data() {
        let xml = r#"<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav">
  <D:response>
    <D:href>/u/me/addr/card1.vcf</D:href>
    <D:propstat>
      <D:prop>
        <D:getetag>"etag1"</D:getetag>
        <C:address-data>BEGIN:VCARD
VERSION:4.0
UID:c1
FN:Alan Turing
EMAIL:alan@x.io
END:VCARD
</C:address-data>
      </D:prop>
    </D:propstat>
  </D:response>
</D:multistatus>"#;
        let responses = parse_report_responses(xml).unwrap();
        assert_eq!(responses.len(), 1);
        assert_eq!(responses[0].0, "/u/me/addr/card1.vcf");
        assert!(responses[0].1.contains("FN:Alan Turing"));
        // The parsed card carries the href.
        let parsed = parse_vcards(&responses[0].1);
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0].name, "Alan Turing");
    }

    #[test]
    fn report_xml_escaped_address_data() {
        // address-data with XML-escaped entities (& < >) must be unescaped.
        let xml = r#"<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav"><D:response><D:href>/x.vcf</D:href><C:address-data>BEGIN:VCARD
FN:A &amp; B
EMAIL:ab@x.io
END:VCARD
</C:address-data></D:response></D:multistatus>"#;
        let responses = parse_report_responses(xml).unwrap();
        assert_eq!(responses.len(), 1);
        assert!(responses[0].1.contains("FN:A & B"));
    }

    #[test]
    fn contacts_to_csv_header_and_rows() {
        let c = Contact {
            uid: "u".into(),
            name: "Ada".into(),
            emails: vec!["a@x.io".into(), "a2@x.io".into()],
            phones: vec!["1".into()],
            href: None,
            normalized: true,
        };
        let csv = contacts_to_csv(&[c]);
        let lines: Vec<&str> = csv.lines().collect();
        assert_eq!(lines[0], "name,email,phone");
        assert_eq!(lines[1], "Ada,a@x.io,1");
        // second email row, empty phone.
        assert_eq!(lines[2], "Ada,a2@x.io,");
    }

    #[test]
    fn sniff_delimiter_comma() {
        let sample = "name,email,phone\nAda,a@x.io,1\nBob,b@x.io,2\n";
        assert_eq!(sniff_delimiter(sample), Some(b','));
    }

    #[test]
    fn sniff_delimiter_semicolon() {
        let sample = "name;email;phone\nAda;a@x.io;1\nBob;b@x.io;2\n";
        assert_eq!(sniff_delimiter(sample), Some(b';'));
    }

    #[test]
    fn sniff_has_header_detects_header() {
        // Header row is all text; data rows have a numeric column -> header detected.
        let sample = "name,age\nAda,36\nBob,40\n";
        let d = sniff_delimiter(sample).unwrap();
        assert_eq!(sniff_has_header(sample, d), Some(true));
    }

    #[test]
    fn import_vcards_local_dedup_logic() {
        // The local-import dedup decision (`_import_vcards`'s no-CardDAV branch):
        // parse, build an `existing` email set, skip cards whose email is already
        // present. Exercised purely (no DATA_DIR / env mutation, which would race
        // other parallel tests through the process-global `ODYSSEUS_DATA_DIR`).
        let text = "BEGIN:VCARD\nVERSION:4.0\nUID:c1\nFN:Ada\nEMAIL:ada@x.io\nEND:VCARD\n\
                    BEGIN:VCARD\nVERSION:4.0\nUID:c2\nFN:Ada Dup\nEMAIL:ada@x.io\nEND:VCARD\n";
        let parsed = parse_vcards(text);
        assert_eq!(parsed.len(), 2);

        // Replicate the branch's set-based dedup.
        let mut existing: std::collections::HashSet<String> = std::collections::HashSet::new();
        let mut imported = 0i64;
        for c in &parsed {
            let emails: Vec<String> =
                c.emails.iter().filter(|e| !e.is_empty()).cloned().collect();
            if !emails.is_empty()
                && emails.iter().any(|e| existing.contains(&e.to_lowercase()))
            {
                continue;
            }
            for e in &emails {
                existing.insert(e.to_lowercase());
            }
            imported += 1;
        }
        // 2 cards parsed, but the 2nd dups ada@x.io -> imported 1.
        assert_eq!(imported, 1);
        assert_eq!(parsed.len(), 2);
    }

    #[test]
    fn to_json_key_order_matches_source_path() {
        // REPORT-fetched contact: the raw `_parse_vcards` dict (py L130, order
        // name/emails/phones/uid) with `c["href"]` appended LAST (py L263). The
        // CardDAV paths go through `raw_to_contact` -> `normalized == false`, and
        // `/list` / `/search` / `/add` echo that dict back verbatim, so the keys are
        // `name, emails, phones, uid, href`.
        let report = Contact {
            uid: "c1".into(),
            name: "Ada".into(),
            emails: vec!["ada@x.io".into()],
            phones: vec![],
            href: Some("/u/me/addr/card1.vcf".into()),
            normalized: false,
        };
        let v = report.to_json();
        assert_eq!(v["href"], json!("/u/me/addr/card1.vcf"));
        let keys: Vec<&str> = v
            .as_object()
            .unwrap()
            .keys()
            .map(|s| s.as_str())
            .collect();
        assert_eq!(keys, vec!["name", "emails", "phones", "uid", "href"]);

        // GET-fallback contact: still the raw `_parse_vcards` shape (py L304,
        // `normalized == false`) but no href appended (the plain GET sets none), so
        // the keys are `name, emails, phones, uid` with `href` ABSENT.
        let get_fallback = Contact {
            uid: "c2".into(),
            name: "Bob".into(),
            emails: vec!["bob@x.io".into()],
            phones: vec![],
            href: None,
            normalized: false,
        };
        let v2 = get_fallback.to_json();
        assert!(v2.get("href").is_none());
        let keys2: Vec<&str> = v2
            .as_object()
            .unwrap()
            .keys()
            .map(|s| s.as_str())
            .collect();
        assert_eq!(keys2, vec!["name", "emails", "phones", "uid"]);

        // Locally-normalized contact: `_normalize_contact` returns `{uid, name,
        // emails, phones}` (py L70-75, `normalized == true`, never any href), so the
        // keys are uid-first and `href` is ABSENT.
        let local = Contact {
            uid: "c3".into(),
            name: "Cy".into(),
            emails: vec!["cy@x.io".into()],
            phones: vec![],
            href: None,
            normalized: true,
        };
        let v3 = local.to_json();
        assert!(v3.get("href").is_none());
        let keys3: Vec<&str> = v3
            .as_object()
            .unwrap()
            .keys()
            .map(|s| s.as_str())
            .collect();
        assert_eq!(keys3, vec!["uid", "name", "emails", "phones"]);
    }

    #[test]
    fn parse_complex_numbers() {
        assert!(parse_complex("36").is_some());
        assert!(parse_complex("3.14").is_some());
        assert!(parse_complex("-2").is_some());
        assert!(parse_complex("1j").is_some());
        assert!(parse_complex("Ada").is_none());
        assert!(parse_complex("").is_none());
    }
}
