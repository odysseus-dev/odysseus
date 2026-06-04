// src/caldav_writeback.rs  <- src/caldav_writeback.py
//! CalDAV write-back: push local create/update/delete out to the remote (#800).
//!
//! `src/caldav_sync.rs` is a one-way pull (remote -> local). So events created,
//! edited, or deleted in Odysseus on a CalDAV-backed calendar only changed the
//! local SQLite copy and never reached the server (iCloud/Nextcloud/Radicale/
//! Fastmail) — they'd silently disappear on the next pull and never show on the
//! user's phone.
//!
//! This adds the missing write half. The remote calendar URL isn't stored
//! locally (the local calendar id is a one-way hash of it), so we re-discover
//! the remote calendar by matching that same hash, then PUT/DELETE the VEVENT by
//! its UID. Writes are best-effort: the local DB stays the source of truth, and
//! a remote failure is reported, never fatal to the local operation.
//!
//! The pure pieces (`build_event_ical`, `find_remote_calendar`, `push_event`)
//! take their inputs by argument so they unit-test against a fake client with no
//! network.
//!
//! PORT_PARTIAL. The Python uses the `caldav` library (`DAVClient` /
//! `principal().calendars()` / `event_by_uid` / `save_event` / `save` /
//! `delete`). There is no equivalent Rust `caldav` crate, so — exactly as the
//! sibling pull path (`caldav_sync.rs`) — the protocol is hand-rolled over
//! `reqwest::blocking` with Basic auth, custom HTTP methods (PUT/DELETE plus a
//! UID `REPORT` for `event_by_uid`) and small XML bodies. The blocking work runs
//! inside `tokio::task::spawn_blocking` (the `asyncio.to_thread` analogue).
//! VEVENT serialization uses the `icalendar` crate.

use crate::pylog as logger;
use chrono::{NaiveDateTime, TimeZone, Utc};
use icalendar::{Calendar, Component, Event as IEvent, EventLike};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

/// Deterministic local id for a remote CalDAV calendar — reuses the sync
/// module's hashing so a local CalDAV calendar id maps back to the same remote
/// URL it was pulled from (Python's `_stable_cal_id` defers to
/// `src.caldav_sync._stable_cal_id`).
fn stable_cal_id(remote_url: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(remote_url.as_bytes());
    let hex = hex::encode(hasher.finalize());
    format!("caldav-{}", &hex[..24])
}

/// A local calendar event ready to be serialized + pushed. Mirrors the Python
/// `ev` dict keys: `uid`, `summary`, `description`, `location`, `dtstart`,
/// `dtend`, `all_day`, `is_utc`, `rrule`.
#[derive(Clone, Debug, Default)]
pub struct LocalEvent {
    pub uid: String,
    pub summary: String,
    pub description: String,
    pub location: String,
    pub dtstart: NaiveDateTime,
    pub dtend: NaiveDateTime,
    pub all_day: bool,
    pub is_utc: bool,
    pub rrule: String,
}

impl LocalEvent {
    /// Build a `LocalEvent` from the loosely-typed JSON the routes pass around
    /// (the `ev` dict analogue). Datetimes accept either the SQLite row format
    /// (`%Y-%m-%d %H:%M:%S%.f`) or ISO 8601 with a `T` separator. Missing string
    /// fields become empty strings; missing flags become `false`; an absent
    /// `dtstart`/`dtend` becomes the Unix epoch.
    pub fn from_json(ev: &Value) -> Self {
        let s = |k: &str| ev.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string();
        let b = |k: &str| ev.get(k).and_then(|v| v.as_bool()).unwrap_or(false);
        let dt = |k: &str| {
            ev.get(k)
                .and_then(|v| v.as_str())
                .and_then(parse_local_datetime)
                .unwrap_or_else(|| {
                    // Unix epoch as a stable fallback (matches the absent-field
                    // case; never actually pushed for a well-formed event).
                    chrono::NaiveDate::from_ymd_opt(1970, 1, 1)
                        .unwrap()
                        .and_hms_opt(0, 0, 0)
                        .unwrap()
                })
        };
        LocalEvent {
            uid: s("uid"),
            summary: s("summary"),
            description: s("description"),
            location: s("location"),
            dtstart: dt("dtstart"),
            dtend: dt("dtend"),
            all_day: b("all_day"),
            is_utc: b("is_utc"),
            rrule: s("rrule"),
        }
    }
}

/// Parse a datetime string, tolerating both the SQLite row format
/// (`YYYY-MM-DD HH:MM:SS[.ffffff]`) and ISO 8601 (`YYYY-MM-DDTHH:MM:SS[.ffffff]`,
/// with an optional trailing `Z`).
fn parse_local_datetime(raw: &str) -> Option<NaiveDateTime> {
    let cleaned = raw.trim().trim_end_matches('Z').replace('T', " ");
    for fmt in ["%Y-%m-%d %H:%M:%S%.f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"] {
        if let Ok(dt) = NaiveDateTime::parse_from_str(&cleaned, fmt) {
            return Some(dt);
        }
    }
    None
}

/// Serialize a local event to a VCALENDAR/VEVENT iCalendar string.
///
/// Mirrors how the pull path interprets `is_utc`/`all_day` so a round-trip is
/// stable:
/// * `all_day` -> `DTSTART;VALUE=DATE` / `DTEND;VALUE=DATE` (date only),
/// * `is_utc` -> re-attach UTC so the server gets a `Z` time
///   (`DTSTART:...Z`),
/// * otherwise -> legacy naive-local ("floating") time, emitted without a TZ.
pub fn build_event_ical(ev: &LocalEvent) -> String {
    let mut cal = Calendar::empty();
    cal.append_property(("PRODID", "-//Odysseus//CalDAV write-back//EN"));
    cal.append_property(("VERSION", "2.0"));

    let mut ve = IEvent::new();
    ve.uid(&ev.uid);
    // ve.add("summary", ev.get("summary") or "")
    ve.summary(&ev.summary);
    // Only add description/location when truthy (non-empty), as the Python does.
    if !ev.description.is_empty() {
        ve.description(&ev.description);
    }
    if !ev.location.is_empty() {
        ve.location(&ev.location);
    }

    if ev.all_day {
        // dtstart.date() / dtend.date()
        ve.starts(ev.dtstart.date());
        ve.ends(ev.dtend.date());
    } else if ev.is_utc {
        // Stored as naive-UTC instants — re-attach UTC so the server gets a Z time.
        ve.starts(Utc.from_utc_datetime(&ev.dtstart));
        ve.ends(Utc.from_utc_datetime(&ev.dtend));
    } else {
        // Legacy naive-local ("floating") time — emit without a TZ.
        ve.starts(ev.dtstart);
        ve.ends(ev.dtend);
    }

    if !ev.rrule.is_empty() {
        // The Python parses via `vRecur.from_ical` and silently skips an
        // unparseable rrule. The local rrule is already an iCal RRULE value
        // string (the pull path stores `comp.get("rrule").to_ical()`); emit it
        // back verbatim as the RRULE property value.
        ve.add_property("RRULE", &ev.rrule);
    }

    cal.push(ve.done());
    cal.to_string()
}

/// A discovered remote calendar: its absolute URL + display name. Mirrors the
/// `caldav` lib's calendar object for the bits we use (`cal.url`).
#[derive(Clone, Debug)]
pub struct RemoteCalendar {
    pub url: String,
    pub name: String,
}

/// Find the remote calendar whose URL hashes to `local_cal_id`, or `None`.
pub fn find_remote_calendar<'a>(
    calendars: &'a [RemoteCalendar],
    local_cal_id: &str,
) -> Option<&'a RemoteCalendar> {
    calendars
        .iter()
        .find(|cal| stable_cal_id(&cal.url) == local_cal_id)
}

/// A small wrapper that re-attaches Basic auth to every CalDAV request (mirrors
/// `caldav_sync.rs::AuthClient`).
struct AuthClient {
    client: reqwest::blocking::Client,
    username: String,
    password: String,
}

impl AuthClient {
    /// `remote.event_by_uid(uid)` — locate the existing event resource for
    /// `uid` on `cal_url` via a calendar-query REPORT filtering on UID. Returns
    /// the resolved absolute href of the matching resource, or `None` when the
    /// event is absent (or the lookup errors — caught like the Python `except`).
    fn event_by_uid(&self, cal_url: &str, uid: &str) -> Option<String> {
        let body = format!(
            r#"<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:getetag/></d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:prop-filter name="UID">
          <c:text-match collation="i;octet">{}</c:text-match>
        </c:prop-filter>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"#,
            xml_escape(uid)
        );
        let method = reqwest::Method::from_bytes(b"REPORT").ok()?;
        let resp = self
            .client
            .request(method, cal_url)
            .basic_auth(&self.username, Some(&self.password))
            .header("Depth", "1")
            .header(
                reqwest::header::CONTENT_TYPE,
                "application/xml; charset=utf-8",
            )
            .body(body)
            .send()
            .ok()?;
        if !resp.status().is_success() && resp.status().as_u16() != 207 {
            return None;
        }
        let xml = resp.text().ok()?;
        let base = url::Url::parse(cal_url).ok()?;
        // Take the first href in the first response block, resolved absolute.
        let block = response_blocks(&xml).into_iter().next()?;
        let href = all_hrefs(&block).into_iter().next()?;
        base.join(href.trim()).ok().map(|u| u.to_string())
    }

    /// `remote.save_event(ical)` — PUT a brand-new event resource. The resource
    /// URL is the calendar collection URL joined with `<uid>.ics`.
    fn save_event(&self, cal_url: &str, uid: &str, ical: &str) -> Result<(), String> {
        let base = url::Url::parse(cal_url).map_err(|e| e.to_string())?;
        let resource = base
            .join(&format!("{}.ics", uid_filename(uid)))
            .map_err(|e| e.to_string())?;
        self.put(resource.as_str(), ical, true)
    }

    /// `existing.data = ical; existing.save()` — PUT to an existing resource href.
    fn save_existing(&self, href: &str, ical: &str) -> Result<(), String> {
        self.put(href, ical, false)
    }

    /// PUT an iCalendar body to `url`. `if_none_match` adds `If-None-Match: *`
    /// so a create doesn't clobber a resource that appeared concurrently.
    fn put(&self, url: &str, ical: &str, if_none_match: bool) -> Result<(), String> {
        let mut req = self
            .client
            .put(url)
            .basic_auth(&self.username, Some(&self.password))
            .header(reqwest::header::CONTENT_TYPE, "text/calendar; charset=utf-8")
            .body(ical.to_string());
        if if_none_match {
            req = req.header(reqwest::header::IF_NONE_MATCH, "*");
        }
        let resp = req.send().map_err(|e| e.to_string())?;
        if !resp.status().is_success() {
            return Err(format!("PUT {url} -> HTTP {}", resp.status().as_u16()));
        }
        Ok(())
    }

    /// `existing.delete()` — DELETE the resource href.
    fn delete(&self, href: &str) -> Result<(), String> {
        let resp = self
            .client
            .delete(href)
            .basic_auth(&self.username, Some(&self.password))
            .send()
            .map_err(|e| e.to_string())?;
        // 404 means already-gone, which is fine for a delete.
        if !resp.status().is_success() && resp.status().as_u16() != 404 {
            return Err(format!("DELETE {href} -> HTTP {}", resp.status().as_u16()));
        }
        Ok(())
    }
}

/// Create/update (or delete) `ev` on the matching remote calendar.
///
/// Returns a JSON object `{"ok": bool, ...}`. `calendars` is the discovered
/// remote calendar list (injected so this is unit-testable with fakes); `client`
/// performs the actual HTTP. Mirrors the Python `push_event`.
fn push_event(
    client: &AuthClient,
    calendars: &[RemoteCalendar],
    local_cal_id: &str,
    ev: &LocalEvent,
    delete: bool,
) -> Value {
    if ev.uid.is_empty() {
        return json!({"ok": false, "error": "event uid is required"});
    }

    let remote = match find_remote_calendar(calendars, local_cal_id) {
        Some(r) => r,
        None => return json!({"ok": false, "error": "remote calendar not found"}),
    };

    // existing = remote.event_by_uid(uid) (None on error, like the Python except).
    let existing = client.event_by_uid(&remote.url, &ev.uid);

    if delete {
        match existing {
            None => return json!({"ok": true, "note": "already absent on remote"}),
            Some(href) => match client.delete(&href) {
                Ok(()) => return json!({"ok": true}),
                Err(e) => return json!({"ok": false, "error": e}),
            },
        }
    }

    let ical = build_event_ical(ev);
    match existing {
        Some(href) => match client.save_existing(&href, &ical) {
            Ok(()) => json!({"ok": true, "updated": true}),
            Err(e) => json!({"ok": false, "error": e}),
        },
        None => match client.save_event(&remote.url, &ev.uid, &ical) {
            Ok(()) => json!({"ok": true, "created": true}),
            Err(e) => json!({"ok": false, "error": e}),
        },
    }
}

/// Discover the principal's calendars, falling back to the URL itself — same
/// strategy as the pull path (`_discover_calendars`). Hand-rolled PROPFIND
/// (current-user-principal -> calendar-home-set -> Depth:1 list) with a
/// single-calendar-URL fallback.
fn discover_calendars(client: &AuthClient, url: &str) -> Vec<RemoteCalendar> {
    let base = match url::Url::parse(url) {
        Ok(u) => u,
        Err(_) => return Vec::new(),
    };

    let principal_url = client
        .propfind(
            url,
            0,
            r#"<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>"#,
        )
        .ok()
        .and_then(|xml| first_local_text(&xml, "current-user-principal"))
        .and_then(|frag| all_hrefs(&frag).into_iter().next())
        .and_then(|href| base.join(href.trim()).ok().map(|u| u.to_string()));

    let home_url = principal_url.as_ref().and_then(|purl| {
        client
            .propfind(
                purl,
                0,
                r#"<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><c:calendar-home-set/></d:prop></d:propfind>"#,
            )
            .ok()
            .and_then(|xml| first_local_text(&xml, "calendar-home-set"))
            .and_then(|frag| all_hrefs(&frag).into_iter().next())
            .and_then(|href| base.join(href.trim()).ok().map(|u| u.to_string()))
    });

    if let Some(home) = &home_url {
        if let Ok(xml) = client.propfind(
            home,
            1,
            r#"<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><d:resourcetype/><d:displayname/></d:prop></d:propfind>"#,
        ) {
            let mut found: Vec<RemoteCalendar> = Vec::new();
            for block in response_blocks(&xml) {
                if !block.to_lowercase().contains("calendar") {
                    continue;
                }
                let href = match all_hrefs(&block).into_iter().next() {
                    Some(h) => h,
                    None => continue,
                };
                let abs = match base.join(href.trim()).ok().map(|u| u.to_string()) {
                    Some(a) => a,
                    None => continue,
                };
                let name = first_local_text(&block, "displayname")
                    .map(|s| s.trim().to_string())
                    .filter(|s| !s.is_empty())
                    .unwrap_or_else(|| "CalDAV".to_string());
                found.push(RemoteCalendar { url: abs, name });
            }
            if !found.is_empty() {
                return found;
            }
        }
    }

    // Fallback: the URL itself is a single calendar.
    vec![RemoteCalendar {
        url: url.to_string(),
        name: "CalDAV".to_string(),
    }]
}

impl AuthClient {
    fn propfind(&self, url: &str, depth: u8, body: &str) -> Result<String, String> {
        let method = reqwest::Method::from_bytes(b"PROPFIND").map_err(|e| e.to_string())?;
        let resp = self
            .client
            .request(method, url)
            .basic_auth(&self.username, Some(&self.password))
            .header("Depth", depth.to_string())
            .header(
                reqwest::header::CONTENT_TYPE,
                "application/xml; charset=utf-8",
            )
            .body(body.to_string())
            .send()
            .map_err(|e| e.to_string())?;
        if !resp.status().is_success() && resp.status().as_u16() != 207 {
            return Err(format!("PROPFIND {url} -> HTTP {}", resp.status().as_u16()));
        }
        resp.text().map_err(|e| e.to_string())
    }
}

/// The blocking write-back — synchronous, intended to run in a threadpool
/// (`_writeback_blocking`). Builds the HTTP client, discovers calendars, then
/// dispatches to `push_event`.
fn writeback_blocking(
    local_cal_id: &str,
    ev: &LocalEvent,
    delete: bool,
    url: &str,
    username: &str,
    password: &str,
) -> Value {
    // SSRF guard: never issue an outbound request to a private / loopback /
    // internal endpoint. The pull path validates the URL before use; replicate
    // that here defensively via the shared `url_security` helper.
    if let Err(e) = crate::src::url_security::validate_public_http_url(url) {
        return json!({"ok": false, "error": e});
    }

    let client = match reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
    {
        Ok(c) => c,
        Err(e) => return json!({"ok": false, "error": format!("Could not build HTTP client: {e}")}),
    };
    let auth_client = AuthClient {
        client,
        username: username.to_string(),
        password: password.to_string(),
    };

    let calendars = discover_calendars(&auth_client, url);
    if calendars.is_empty() {
        return json!({"ok": false, "error": "no remote calendars discovered"});
    }
    push_event(&auth_client, &calendars, local_cal_id, ev, delete)
}

/// Best-effort push of a local change to the remote CalDAV server.
///
/// No-ops (`{"skipped": ...}`) when the calendar isn't CalDAV-backed or no
/// credentials are configured. Never raises — a remote failure is logged and
/// returned, the local DB remaining the source of truth. Mirrors the Python
/// `writeback_event`.
pub async fn writeback_event(
    owner: &str,
    calendar_source: &str,
    calendar_id: &str,
    ev: &Value,
    delete: bool,
) -> Value {
    if calendar_source != "caldav" {
        return json!({"skipped": "not a caldav calendar"});
    }

    // `routes.prefs_routes._load_for_user` is unported; re-read the user's prefs
    // from `data/user_prefs.json` (the same approach as `caldav_sync.rs`).
    let cfg = load_caldav_cfg(owner);
    let url = cfg
        .get("url")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    let user = cfg
        .get("username")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    // Stored encrypted by routes/calendar_routes; decrypt before use so the
    // remote sees the real password (decrypt is a no-op on legacy plaintext).
    let pw = crate::src::secret_storage::decrypt(
        cfg.get("password").and_then(|v| v.as_str()).unwrap_or(""),
    );
    if url.is_empty() || user.is_empty() || pw.is_empty() {
        return json!({"skipped": "caldav not configured"});
    }

    let local_cal_id = calendar_id.to_string();
    let local_event = LocalEvent::from_json(ev);
    let result = tokio::task::spawn_blocking(move || {
        writeback_blocking(&local_cal_id, &local_event, delete, &url, &user, &pw)
    })
    .await;

    match result {
        Ok(v) => {
            if v.get("ok").and_then(|b| b.as_bool()) != Some(true) {
                let detail = v
                    .get("error")
                    .and_then(|e| e.as_str())
                    .map(|s| s.to_string())
                    .unwrap_or_else(|| v.to_string());
                logger::warning(&format!("CalDAV write-back did not apply: {detail}"));
            }
            v
        }
        Err(e) => {
            logger::error("CalDAV write-back raised");
            let truncated: String = e.to_string().chars().take(200).collect();
            json!({"ok": false, "error": truncated})
        }
    }
}

/// Load the `caldav` config block from the user's prefs file
/// (`routes.prefs_routes._load_for_user(owner).get("caldav", {})`). Honors the
/// `_users` vs flat shapes; a missing file / decode error yields `{}`.
fn load_caldav_cfg(owner: &str) -> Value {
    const PREFS_FILE: &str = "data/user_prefs.json";
    let raw = match std::fs::read_to_string(PREFS_FILE) {
        Ok(s) => s,
        Err(_) => return json!({}),
    };
    let all_prefs: Value = match serde_json::from_str(&raw) {
        Ok(v) => v,
        Err(_) => return json!({}),
    };
    let user_prefs = if let Some(users) = all_prefs.get("_users") {
        users.get(owner).cloned().unwrap_or_else(|| json!({}))
    } else {
        all_prefs
    };
    user_prefs
        .get("caldav")
        .cloned()
        .unwrap_or_else(|| json!({}))
}

// ---------------------------------------------------------------------------
// Tiny XML helpers (kept local to this module; the sibling pull module has its
// own equivalents but they're private to that file).
// ---------------------------------------------------------------------------

/// Minimal XML text escaping for embedding a UID in a request body.
fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

/// Sanitize a UID into a safe `.ics` filename component (no slashes / spaces).
fn uid_filename(uid: &str) -> String {
    uid.chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.' { c } else { '_' })
        .collect()
}

/// Extract the text content of the first element with the given local name
/// (namespace-agnostic) from an XML fragment.
fn first_local_text(xml: &str, local: &str) -> Option<String> {
    let lname = local.to_lowercase();
    let lower = xml.to_lowercase();
    let mut idx = 0usize;
    while let Some(rel) = lower[idx..].find('<') {
        let start = idx + rel;
        let rest = &lower[start + 1..];
        if rest.starts_with('/') || rest.starts_with('?') || rest.starts_with('!') {
            idx = start + 1;
            continue;
        }
        let end_name = rest.find(|c: char| c.is_whitespace() || c == '>' || c == '/');
        if let Some(en) = end_name {
            let tagname = &rest[..en];
            let local_part = tagname.rsplit(':').next().unwrap_or(tagname);
            if local_part == lname {
                if let Some(gt) = lower[start..].find('>') {
                    let content_start = start + gt + 1;
                    if let Some(close_rel) = lower[content_start..].find(&format!("</{tagname}")) {
                        let content_end = content_start + close_rel;
                        return Some(xml[content_start..content_end].trim().to_string());
                    }
                }
            }
        }
        idx = start + 1;
    }
    None
}

/// Extract every `<href>` element's text from an XML fragment (namespace-agnostic).
///
/// Scans OPENING element tags by local name (mirrors `first_local_text`) rather
/// than the bare substring `"href"` — otherwise the closing `</...href>` tag also
/// matches and captures the following sibling's markup as a bogus second href.
fn all_hrefs(xml: &str) -> Vec<String> {
    let mut out = Vec::new();
    let lower = xml.to_lowercase();
    let mut idx = 0usize;
    while let Some(rel) = lower[idx..].find('<') {
        let start = idx + rel;
        let rest = &lower[start + 1..];
        // Only opening element tags hold text — skip closing/PI/declaration tags.
        if rest.starts_with('/') || rest.starts_with('?') || rest.starts_with('!') {
            idx = start + 1;
            continue;
        }
        let end_name = rest.find(|c: char| c.is_whitespace() || c == '>' || c == '/');
        if let Some(en) = end_name {
            let tagname = &rest[..en];
            let local_part = tagname.rsplit(':').next().unwrap_or(tagname);
            if local_part == "href" {
                if let Some(gt) = lower[start..].find('>') {
                    let content_start = start + gt + 1;
                    if let Some(close_rel) = lower[content_start..].find(&format!("</{tagname}")) {
                        let content_end = content_start + close_rel;
                        let text = xml[content_start..content_end].trim().to_string();
                        if !text.is_empty() {
                            out.push(text);
                        }
                        idx = content_end;
                        continue;
                    }
                }
            }
        }
        idx = start + 1;
    }
    out
}

/// Split a multistatus response into individual `<response>` blocks.
fn response_blocks(xml: &str) -> Vec<String> {
    let mut out = Vec::new();
    let lower = xml.to_lowercase();
    let mut idx = 0usize;
    while let Some((s, tagname)) = find_local_open(&lower[idx..], "response") {
        let open_pos = idx + s;
        let close_marker = format!("</{tagname}");
        if let Some(close_rel) = lower[open_pos..].find(&close_marker) {
            let block_end = open_pos + close_rel + close_marker.len();
            let block_end = lower[block_end..]
                .find('>')
                .map(|g| block_end + g + 1)
                .unwrap_or(block_end);
            out.push(xml[open_pos..block_end].to_string());
            idx = block_end;
        } else {
            break;
        }
    }
    out
}

/// Find the byte offset + full lowercase tag name of the first opening tag whose
/// local name matches `local`.
fn find_local_open(lower: &str, local: &str) -> Option<(usize, String)> {
    let mut idx = 0usize;
    while let Some(rel) = lower[idx..].find('<') {
        let start = idx + rel;
        let rest = &lower[start + 1..];
        if rest.starts_with('/') || rest.starts_with('?') || rest.starts_with('!') {
            idx = start + 1;
            continue;
        }
        let end_name = rest.find(|c: char| c.is_whitespace() || c == '>' || c == '/');
        if let Some(en) = end_name {
            let tagname = &rest[..en];
            let local_part = tagname.rsplit(':').next().unwrap_or(tagname);
            if local_part == local {
                return Some((start, tagname.to_string()));
            }
        }
        idx = start + 1;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dt(s: &str) -> NaiveDateTime {
        NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S").unwrap()
    }

    #[test]
    fn stable_cal_id_matches_sync_hashing() {
        let a = stable_cal_id("https://cal.example.com/u/me/calendar/");
        let b = stable_cal_id("https://cal.example.com/u/me/calendar/");
        assert_eq!(a, b);
        assert!(a.starts_with("caldav-"));
        assert_eq!(a.len(), 7 + 24);
    }

    #[test]
    fn build_ical_utc_event_has_required_fields() {
        let ev = LocalEvent {
            uid: "evt-1".into(),
            summary: "Standup".into(),
            description: "Daily sync".into(),
            location: "Room 1".into(),
            dtstart: dt("2024-03-10 12:00:00"),
            dtend: dt("2024-03-10 12:30:00"),
            all_day: false,
            is_utc: true,
            rrule: String::new(),
        };
        let ical = build_event_ical(&ev);
        assert!(ical.contains("BEGIN:VCALENDAR"));
        assert!(ical.contains("END:VCALENDAR"));
        assert!(ical.contains("BEGIN:VEVENT"));
        assert!(ical.contains("END:VEVENT"));
        assert!(ical.contains("PRODID:-//Odysseus//CalDAV write-back//EN"));
        assert!(ical.contains("VERSION:2.0"));
        assert!(ical.contains("UID:evt-1"));
        assert!(ical.contains("SUMMARY:Standup"));
        assert!(ical.contains("DESCRIPTION:Daily sync"));
        assert!(ical.contains("LOCATION:Room 1"));
        // is_utc -> Z-suffixed datetimes.
        assert!(ical.contains("DTSTART:20240310T120000Z"));
        assert!(ical.contains("DTEND:20240310T123000Z"));
        // No RRULE when empty.
        assert!(!ical.contains("RRULE"));
    }

    #[test]
    fn build_ical_all_day_uses_date_value() {
        let ev = LocalEvent {
            uid: "day-1".into(),
            summary: "Holiday".into(),
            dtstart: dt("2024-07-01 00:00:00"),
            dtend: dt("2024-07-02 00:00:00"),
            all_day: true,
            is_utc: false,
            ..Default::default()
        };
        let ical = build_event_ical(&ev);
        assert!(ical.contains("DTSTART;VALUE=DATE:20240701"));
        assert!(ical.contains("DTEND;VALUE=DATE:20240702"));
        // No Z suffix for all-day.
        assert!(!ical.contains("20240701T"));
    }

    #[test]
    fn build_ical_floating_has_no_z_suffix() {
        let ev = LocalEvent {
            uid: "float-1".into(),
            summary: "Local meeting".into(),
            dtstart: dt("2024-05-06 09:00:00"),
            dtend: dt("2024-05-06 10:00:00"),
            all_day: false,
            is_utc: false,
            ..Default::default()
        };
        let ical = build_event_ical(&ev);
        // Floating time: emitted without a trailing Z.
        assert!(ical.contains("DTSTART:20240506T090000"));
        assert!(!ical.contains("DTSTART:20240506T090000Z"));
        assert!(ical.contains("DTEND:20240506T100000"));
        assert!(!ical.contains("DTEND:20240506T100000Z"));
    }

    #[test]
    fn build_ical_omits_empty_description_and_location() {
        let ev = LocalEvent {
            uid: "min-1".into(),
            summary: "Just a title".into(),
            dtstart: dt("2024-05-06 09:00:00"),
            dtend: dt("2024-05-06 10:00:00"),
            ..Default::default()
        };
        let ical = build_event_ical(&ev);
        assert!(ical.contains("SUMMARY:Just a title"));
        assert!(!ical.contains("DESCRIPTION"));
        assert!(!ical.contains("LOCATION"));
    }

    #[test]
    fn build_ical_empty_summary_still_emits_summary() {
        // Python always adds SUMMARY (defaulting to "").
        let ev = LocalEvent {
            uid: "no-sum".into(),
            dtstart: dt("2024-05-06 09:00:00"),
            dtend: dt("2024-05-06 10:00:00"),
            ..Default::default()
        };
        let ical = build_event_ical(&ev);
        assert!(ical.contains("SUMMARY:"));
    }

    #[test]
    fn build_ical_includes_rrule_when_present() {
        let ev = LocalEvent {
            uid: "rec-1".into(),
            summary: "Weekly".into(),
            dtstart: dt("2024-05-06 09:00:00"),
            dtend: dt("2024-05-06 10:00:00"),
            is_utc: true,
            rrule: "FREQ=WEEKLY;BYDAY=MO".into(),
            ..Default::default()
        };
        let ical = build_event_ical(&ev);
        assert!(ical.contains("RRULE:FREQ=WEEKLY;BYDAY=MO"));
    }

    #[test]
    fn find_remote_calendar_matches_by_hash() {
        let url_a = "https://cal.example.com/work/";
        let url_b = "https://cal.example.com/home/";
        let cals = vec![
            RemoteCalendar {
                url: url_a.into(),
                name: "Work".into(),
            },
            RemoteCalendar {
                url: url_b.into(),
                name: "Home".into(),
            },
        ];
        let want = stable_cal_id(url_b);
        let found = find_remote_calendar(&cals, &want).expect("should match home");
        assert_eq!(found.url, url_b);
        // Non-matching id -> None.
        assert!(find_remote_calendar(&cals, "caldav-deadbeefdeadbeefdeadbeef").is_none());
    }

    #[test]
    fn local_event_from_json_parses_fields_and_datetimes() {
        let ev = json!({
            "uid": "j-1",
            "summary": "From JSON",
            "description": "desc",
            "location": "loc",
            "dtstart": "2024-03-10 12:00:00",
            "dtend": "2024-03-10T13:00:00Z",
            "all_day": false,
            "is_utc": true,
            "rrule": "FREQ=DAILY",
        });
        let le = LocalEvent::from_json(&ev);
        assert_eq!(le.uid, "j-1");
        assert_eq!(le.summary, "From JSON");
        assert_eq!(le.description, "desc");
        assert_eq!(le.location, "loc");
        assert!(le.is_utc);
        assert!(!le.all_day);
        assert_eq!(le.rrule, "FREQ=DAILY");
        assert_eq!(le.dtstart, dt("2024-03-10 12:00:00"));
        // ISO 8601 with T and trailing Z is accepted.
        assert_eq!(le.dtend, dt("2024-03-10 13:00:00"));
    }

    #[test]
    fn uid_filename_sanitizes_unsafe_chars() {
        assert_eq!(uid_filename("abc-123_X.ics"), "abc-123_X.ics");
        assert_eq!(uid_filename("a/b c:d"), "a_b_c_d");
    }

    #[test]
    fn xml_escape_escapes_markup() {
        assert_eq!(xml_escape("a&b<c>d"), "a&amp;b&lt;c&gt;d");
    }

    #[test]
    fn first_local_text_and_hrefs_namespace_agnostic() {
        let xml = r#"<d:multistatus xmlns:d="DAV:"><d:response><d:href>/cal/x.ics</d:href><d:displayname>Work</d:displayname></d:response></d:multistatus>"#;
        assert_eq!(first_local_text(xml, "displayname").as_deref(), Some("Work"));
        assert_eq!(all_hrefs(xml), vec!["/cal/x.ics".to_string()]);
    }

    #[tokio::test]
    async fn writeback_skips_non_caldav_source() {
        let r = writeback_event("me", "local", "cal-1", &json!({"uid": "x"}), false).await;
        assert_eq!(r.get("skipped").and_then(|v| v.as_str()), Some("not a caldav calendar"));
    }
}
