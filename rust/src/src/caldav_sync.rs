// src/caldav_sync.rs  <- src/caldav_sync.py
//! CalDAV -> local SQLite sync.
//!
//! The Settings UI lets users save CalDAV credentials, but the original sync
//! path was removed when calendar storage was migrated to SQLite. This module
//! re-wires that gap as a one-way pull (remote -> local), called on calendar open
//! and from a periodic scheduler loop.
//!
//! PORT_PARTIAL. The Python uses the `caldav` Python library (PROPFIND discovery
//! + REPORT XML) on top of `requests`, run in a threadpool via
//! `asyncio.to_thread`. There is no equivalent Rust `caldav` crate, so the
//! protocol is hand-rolled over `reqwest` with custom HTTP methods (`PROPFIND` /
//! `REPORT`), Depth headers, and small XML request/response bodies — consistent
//! with the project's hand-rolled-protocol precedent (`mcp_manager` / `core::codex`).
//! VEVENT parsing uses the `icalendar` crate. The blocking work runs inside
//! `tokio::task::spawn_blocking` (the `asyncio.to_thread` analogue), and the
//! `reqwest::blocking` client is built there.
//!
//! HONEST DEFERS (documented, never faked):
//! * `routes.prefs_routes._load_for_user` is unported; this module re-implements
//!   the small JSON loader against `data/user_prefs.json` (CWD-relative, honoring
//!   the `_users` vs flat shapes) so credentials CAN be read if present. When the
//!   file is absent / CalDAV is unconfigured, `sync_caldav` returns the honest
//!   unconfigured shape (`errors: ["CalDAV is not configured"]`) — never a fake
//!   success.
//! * The full multi-calendar PROPFIND principal -> calendar-home -> collection
//!   walk is implemented best-effort (current-user-principal +
//!   calendar-home-set), falling back to treating the URL directly as a single
//!   calendar (mirroring the Python `client.calendar(url=url)` fallback). If
//!   discovery yields nothing, the single-calendar REPORT path still runs.
//!
//! Datetimes are converted to UTC and the row is flagged `is_utc=True` so the
//! serializer adds the Z suffix and the frontend renders in the user's local TZ.

use crate::pylog as logger;
use chrono::{NaiveDateTime, TimeZone, Utc};
use icalendar::parser;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

/// Pull window: 90 days back, 1 year forward.
const LOOKBACK_DAYS: i64 = 90;
const LOOKAHEAD_DAYS: i64 = 365;

/// SQLite DateTime column format (matches `pydatetime`/management_db).
const SQLITE_DT_FMT: &str = "%Y-%m-%d %H:%M:%S%.6f";

/// Hostnames blocked outright before any network call (`_BLOCKED_HOSTS`).
const BLOCKED_HOSTS: [&str; 4] = [
    "localhost",
    "localhost.",
    "ip6-localhost",
    "metadata.google.internal",
];

// ---------------------------------------------------------------------------
// SSRF hardening for user-supplied CalDAV URLs (validate_caldav_url + helpers).
//
// The crate's existing SSRF modules (`crate::src::url_security` /
// `crate::src::url_safety`) classify IPs, but their `block_private` gating is
// driven by *different* env vars and their predicate functions are private. The
// CalDAV path has its own gate (`ODYSSEUS_ALLOW_PRIVATE_CALDAV`) and its own set
// of always-blocked categories, so the IP classification below mirrors Python's
// `ipaddress` predicates directly to match the exact blocked set + messages.
// ---------------------------------------------------------------------------

/// `_private_caldav_allowed()` — true iff `ODYSSEUS_ALLOW_PRIVATE_CALDAV` is set
/// to one of `1` / `true` / `yes` (case-insensitive; default `0`).
fn private_caldav_allowed() -> bool {
    matches!(
        std::env::var("ODYSSEUS_ALLOW_PRIVATE_CALDAV")
            .unwrap_or_else(|_| "0".to_string())
            .to_lowercase()
            .as_str(),
        "1" | "true" | "yes"
    )
}

/// `_validate_caldav_ip(host)` — if `host` is an IP literal, reject the
/// loopback / link-local / multicast / unspecified categories outright and the
/// private ranges unless `ODYSSEUS_ALLOW_PRIVATE_CALDAV` is set. A non-IP host is
/// a no-op (returns `Ok`), matching Python's `ipaddress.ip_address` `ValueError`
/// early-return.
fn validate_caldav_ip(host: &str) -> Result<(), String> {
    // Python does `host.strip("[]")` before parsing the literal.
    let literal = host.trim_matches(|c| c == '[' || c == ']');
    let ip: std::net::IpAddr = match literal.parse() {
        Ok(ip) => ip,
        Err(_) => return Ok(()),
    };
    // v4-mapped IPv6 -> judge the embedded v4 (parity with `ipaddress` semantics,
    // matching the crate's other SSRF helpers).
    let ip = match ip {
        std::net::IpAddr::V6(v6) => match v6.to_ipv4_mapped() {
            Some(v4) => std::net::IpAddr::V4(v4),
            None => std::net::IpAddr::V6(v6),
        },
        v4 => v4,
    };
    if ip_is_loopback(ip)
        || ip_is_link_local(ip)
        || ip.is_multicast()
        || ip.is_unspecified()
    {
        return Err("CalDAV URL host is not allowed".to_string());
    }
    if ip_is_private(ip) && !private_caldav_allowed() {
        return Err("Private CalDAV IPs require ODYSSEUS_ALLOW_PRIVATE_CALDAV=1".to_string());
    }
    Ok(())
}

/// `ipaddress.is_loopback`.
fn ip_is_loopback(ip: std::net::IpAddr) -> bool {
    ip.is_loopback()
}

/// `ipaddress.is_link_local` (IPv4 169.254.0.0/16, IPv6 fe80::/10).
fn ip_is_link_local(ip: std::net::IpAddr) -> bool {
    match ip {
        std::net::IpAddr::V4(a) => a.is_link_local(),
        std::net::IpAddr::V6(a) => (a.segments()[0] & 0xffc0) == 0xfe80,
    }
}

/// `ipaddress.is_private` (IPv4 10/8, 172.16/12, 192.168/16; IPv6 fc00::/7 ULA).
fn ip_is_private(ip: std::net::IpAddr) -> bool {
    match ip {
        std::net::IpAddr::V4(a) => a.is_private(),
        std::net::IpAddr::V6(a) => matches!(a.segments()[0] >> 8, 0xfc | 0xfd),
    }
}

/// `validate_caldav_url(raw_url)` — validate and normalize a user-provided
/// CalDAV URL before any server-side use. Returns the normalized URL or the
/// Python `ValueError` message as `Err`.
pub fn validate_caldav_url(raw_url: &str) -> Result<String, String> {
    let url = raw_url.trim();
    if url.is_empty() {
        return Err("CalDAV URL is required".to_string());
    }
    // urlparse is lazy about the port; the `url` crate validates it at parse
    // time, so an `InvalidPort` parse error maps to Python's `.port` ValueError.
    let parsed = match url::Url::parse(url) {
        Ok(p) => p,
        Err(url::ParseError::InvalidPort) => {
            return Err("CalDAV URL has an invalid port".to_string());
        }
        // No scheme / otherwise unparseable -> the scheme check below would have
        // rejected it; surface the scheme message for parity.
        Err(_) => {
            return Err("CalDAV URL must start with http:// or https://".to_string());
        }
    };
    if !matches!(parsed.scheme(), "http" | "https") {
        return Err("CalDAV URL must start with http:// or https://".to_string());
    }
    let host = match parsed.host_str() {
        Some(h) if !h.is_empty() => h.to_string(),
        _ => return Err("CalDAV URL must include a host".to_string()),
    };
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err(
            "Put CalDAV credentials in the username/password fields, not the URL".to_string(),
        );
    }
    if parsed.fragment().is_some() {
        return Err("CalDAV URL fragments are not allowed".to_string());
    }
    // urlparse(...).hostname strips IPv6 brackets and lowercases the host.
    let host_l = host
        .trim_start_matches('[')
        .trim_end_matches(']')
        .to_lowercase();
    if BLOCKED_HOSTS.contains(&host_l.as_str()) || host_l.ends_with(".localhost") {
        return Err("CalDAV URL host is not allowed".to_string());
    }
    validate_caldav_ip(&host_l)?;
    // urlunparse(parsed._replace(fragment="")).rstrip("/") — fragments are
    // already rejected above, so reconstruct from the parsed URL and strip any
    // trailing slashes.
    Ok(parsed.as_str().trim_end_matches('/').to_string())
}

/// Deterministic local id for a remote CalDAV calendar — same URL always maps to
/// the same local row across restarts and re-syncs.
fn stable_cal_id(remote_url: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(remote_url.as_bytes());
    let hex = hex::encode(hasher.finalize());
    format!("caldav-{}", &hex[..24])
}

/// The Rust model of Python's `dtstart.dt` / `dtend.dt`: either a date-only
/// (all-day) value, a tz-aware datetime, or a naive (floating) datetime.
#[derive(Clone, Copy)]
enum IcalWhen {
    /// `date` (all-day).
    Date(chrono::NaiveDate),
    /// tz-aware `datetime` — already normalized to UTC.
    Aware(NaiveDateTime),
    /// naive (floating) `datetime`.
    Naive(NaiveDateTime),
}

/// CalDAV datetimes can be tz-aware (with a TZID) or naive. The DB column is
/// naive but we set `is_utc=True` so the serializer adds Z. All-day events stay
/// as `date` and get widened to `datetime` here.
///
/// Returns `(naive_utc_datetime, all_day)`.
fn to_utc_naive(when: IcalWhen) -> (NaiveDateTime, bool) {
    match when {
        IcalWhen::Aware(dt) => (dt, false),
        // naive -> treat as local (stored as-is).
        IcalWhen::Naive(dt) => (dt, false),
        // date-only (all-day) -> midnight.
        IcalWhen::Date(d) => (d.and_hms_opt(0, 0, 0).unwrap(), true),
    }
}

/// Convert an `icalendar` `DatePerhapsTime` into our `IcalWhen`, converting
/// tz-aware values to UTC (the `dt.astimezone(timezone.utc)` analogue).
fn parse_when(dpt: &icalendar::DatePerhapsTime) -> IcalWhen {
    use icalendar::CalendarDateTime as CDT;
    use icalendar::DatePerhapsTime as DPT;
    match dpt {
        DPT::Date(d) => IcalWhen::Date(*d),
        DPT::DateTime(cdt) => match cdt {
            CDT::Utc(dt) => IcalWhen::Aware(dt.naive_utc()),
            CDT::Floating(ndt) => IcalWhen::Naive(*ndt),
            CDT::WithTimezone { date_time, tzid } => {
                // Parse the TZID and convert to UTC; the icalendar `chrono-tz`
                // feature is not enabled, so do it directly via chrono_tz.
                match tzid.parse::<chrono_tz::Tz>() {
                    Ok(tz) => match tz.from_local_datetime(date_time).single() {
                        Some(local) => IcalWhen::Aware(local.with_timezone(&Utc).naive_utc()),
                        // Ambiguous/nonexistent local time -> treat as naive.
                        None => IcalWhen::Naive(*date_time),
                    },
                    // Unknown TZID -> treat as naive (best effort).
                    Err(_) => IcalWhen::Naive(*date_time),
                }
            }
        },
    }
}

/// Whether the parsed start carried real timezone information (the `is_utc`
/// column reflects whether the source carried a TZ we converted from; all-day =
/// no TZ semantics).
fn is_aware_datetime(when: IcalWhen) -> bool {
    matches!(when, IcalWhen::Aware(_))
}

// ---------------------------------------------------------------------------
// CalDAV protocol over reqwest (blocking). Hand-rolled PROPFIND/REPORT.
// ---------------------------------------------------------------------------

/// A discovered remote calendar: its absolute URL + display name.
struct RemoteCalendar {
    url: String,
    name: String,
}

/// Format a datetime as a CalDAV time-range bound: `YYYYMMDDTHHMMSSZ`.
fn caldav_time(dt: NaiveDateTime) -> String {
    dt.format("%Y%m%dT%H%M%SZ").to_string()
}

/// Resolve a possibly-relative href against the request base URL.
fn resolve_href(base: &url::Url, href: &str) -> Option<String> {
    base.join(href.trim()).ok().map(|u| u.to_string())
}

/// Extract the text content of the first element with the given local name
/// (namespace-agnostic) from an XML fragment. Tiny, dependency-free scan that
/// tolerates the `d:` / `cal:` / `C:` namespace prefixes CalDAV servers use.
fn first_local_text(xml: &str, local: &str) -> Option<String> {
    let lname = local.to_lowercase();
    let lower = xml.to_lowercase();
    // Find an opening tag whose local name matches (after an optional `prefix:`).
    let mut idx = 0usize;
    while let Some(rel) = lower[idx..].find('<') {
        let start = idx + rel;
        let rest = &lower[start + 1..];
        // Skip closing tags / declarations.
        if rest.starts_with('/') || rest.starts_with('?') || rest.starts_with('!') {
            idx = start + 1;
            continue;
        }
        // Tag name up to whitespace or '>'.
        let end_name = rest.find(|c: char| c.is_whitespace() || c == '>' || c == '/');
        if let Some(en) = end_name {
            let tagname = &rest[..en];
            let local_part = tagname.rsplit(':').next().unwrap_or(tagname);
            if local_part == lname {
                // Find the end of the opening tag.
                if let Some(gt) = lower[start..].find('>') {
                    let content_start = start + gt + 1;
                    // Find the matching close tag (same local name).
                    if let Some(close_rel) =
                        lower[content_start..].find(&format!("</{tagname}"))
                    {
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
fn all_hrefs(xml: &str) -> Vec<String> {
    let mut out = Vec::new();
    let lower = xml.to_lowercase();
    let mut idx = 0usize;
    while let Some(rel) = lower[idx..].find("href") {
        let pos = idx + rel;
        // Only treat `<...href>` openings.
        // Find the '>' after this tag.
        if let Some(gt_rel) = lower[pos..].find('>') {
            let content_start = pos + gt_rel + 1;
            if let Some(close_rel) = lower[content_start..].find("</") {
                let content_end = content_start + close_rel;
                let text = xml[content_start..content_end].trim().to_string();
                if !text.is_empty() {
                    out.push(text);
                }
                idx = content_end;
                continue;
            }
        }
        idx = pos + 4;
    }
    out
}

/// Split a multistatus response into individual `<response>` blocks.
fn response_blocks(xml: &str) -> Vec<String> {
    let mut out = Vec::new();
    let lower = xml.to_lowercase();
    let mut idx = 0usize;
    // Find an opening `response` tag (with optional prefix).
    while let Some((s, tagname)) = find_local_open(&lower[idx..], "response") {
        let open_pos = idx + s;
        let close_marker = format!("</{tagname}");
        if let Some(close_rel) = lower[open_pos..].find(&close_marker) {
            let block_end = open_pos + close_rel + close_marker.len();
            // include up to the closing '>'.
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

// ---------------------------------------------------------------------------
// The blocking sync (intended to run inside spawn_blocking).
// ---------------------------------------------------------------------------

/// One parsed VEVENT, ready for upsert.
struct EventRow {
    uid: String,
    summary: String,
    description: String,
    location: String,
    dtstart: NaiveDateTime,
    dtend: NaiveDateTime,
    all_day: bool,
    is_utc: bool,
    rrule: String,
}

/// Parse the VEVENTs out of one iCalendar payload, mirroring the Python
/// `iCal.from_ical(...).walk()` loop.
fn parse_vevents(ical_text: &str, errors: &mut Vec<String>, display_name: &str) -> Vec<EventRow> {
    let mut out = Vec::new();
    // icalendar::parser unfolds + tokenizes; then Calendar::try_from.
    let unfolded = parser::unfold(ical_text);
    let parsed = match parser::read_calendar(&unfolded) {
        Ok(p) => p,
        Err(e) => {
            errors.push(format!("{display_name}: parse failed ({e})"));
            return out;
        }
    };
    let cal: icalendar::Calendar = parsed.into();

    use icalendar::{Component, EventLike};
    for comp in &cal.components {
        // Only VEVENTs.
        let event = match comp {
            icalendar::CalendarComponent::Event(ev) => ev,
            _ => continue,
        };

        // uid_val = str(comp.get("uid","")) or str(uuid4())
        let uid_val = event
            .get_uid()
            .map(|s| s.to_string())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());

        // dtstart_p = comp.get("dtstart"); if not dtstart_p: continue
        let dtstart_p = match event.get_start() {
            Some(d) => d,
            None => continue,
        };
        let start_when = parse_when(&dtstart_p);
        let (start_dt, all_day) = to_utc_naive(start_when);

        // dtend handling.
        let end_dt = match event.get_end() {
            Some(dtend_p) => to_utc_naive(parse_when(&dtend_p)).0,
            None if all_day => start_dt + chrono::Duration::days(1),
            None => start_dt + chrono::Duration::hours(1),
        };

        // is_utc: not all_day and dtstart is datetime and tz present.
        let row_is_utc = !all_day && is_aware_datetime(start_when);

        let summary = event.get_summary().unwrap_or("").to_string();
        let description = event.get_description().unwrap_or("").to_string();
        let location = event.get_location().unwrap_or("").to_string();
        // rrule = comp.get("rrule").to_ical().decode() if present else ""
        let rrule = event
            .properties()
            .get("RRULE")
            .map(|p| p.value().to_string())
            .unwrap_or_default();

        out.push(EventRow {
            uid: uid_val,
            summary,
            description,
            location,
            dtstart: start_dt,
            dtend: end_dt,
            all_day,
            is_utc: row_is_utc,
            rrule,
        });
    }
    out
}

/// The actual sync — synchronous, intended to run in a threadpool. Returns
/// counts: `{calendars, events, deleted, errors}`.
fn sync_blocking(owner: &str, url: &str, username: &str, password: &str) -> Value {
    let mut calendars_count = 0i64;
    let mut events_count = 0i64;
    let mut deleted_count = 0i64;
    let mut errors: Vec<String> = Vec::new();

    let client = match reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            errors.push(format!("Could not build HTTP client: {e}"));
            return counts(calendars_count, events_count, deleted_count, errors);
        }
    };
    // CalDAV Basic auth. reqwest's blocking RequestBuilder takes per-request
    // auth; bake it into a closure-friendly default by cloning creds.
    // (We attach .basic_auth on every request below via a small wrapper.)
    let auth_client = AuthClient {
        client,
        username: username.to_string(),
        password: password.to_string(),
    };

    let calendars = auth_client.discover(url, &mut errors);
    if calendars.is_empty() {
        // Discovery + fallback both produced nothing — honest error, no fake sync.
        if errors.is_empty() {
            errors.push("No calendars found".to_string());
        }
        return counts(calendars_count, events_count, deleted_count, errors);
    }

    let now = Utc::now().naive_utc();
    let start = now - chrono::Duration::days(LOOKBACK_DAYS);
    let end = now + chrono::Duration::days(LOOKAHEAD_DAYS);

    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(e) => {
            errors.push(format!("DB unavailable: {e}"));
            return counts(calendars_count, events_count, deleted_count, errors);
        }
    };

    for remote_cal in &calendars {
        // Wrap per-calendar work so one failure doesn't abort the rest.
        let cal_result = (|| -> Result<(), String> {
            let remote_url = &remote_cal.url;
            let cal_id = stable_cal_id(remote_url);
            let display_name = {
                let n = remote_cal.name.trim();
                if n.is_empty() {
                    "CalDAV".to_string()
                } else {
                    n.to_string()
                }
            };

            // Upsert the local calendar row (id + owner).
            let existing_name: Option<String> = conn
                .query_row(
                    "SELECT name FROM calendars WHERE id = ?1 AND owner = ?2",
                    rusqlite::params![cal_id, owner],
                    |r| r.get::<_, String>(0),
                )
                .ok();
            let now_str = now.format(SQLITE_DT_FMT).to_string();
            match existing_name {
                None => {
                    conn.execute(
                        "INSERT INTO calendars (id, owner, name, color, source, created_at, updated_at) \
                         VALUES (?1, ?2, ?3, '#5b8abf', 'caldav', ?4, ?4)",
                        rusqlite::params![cal_id, owner, display_name, now_str],
                    )
                    .map_err(|e| e.to_string())?;
                }
                Some(name) if name != display_name => {
                    // Refresh the display name; preserve any local color override.
                    conn.execute(
                        "UPDATE calendars SET name = ?2, updated_at = ?3 WHERE id = ?1",
                        rusqlite::params![cal_id, display_name, now_str],
                    )
                    .map_err(|e| e.to_string())?;
                }
                Some(_) => {}
            }
            calendars_count += 1;

            // Fetch events in window.
            let payloads = match auth_client.report(remote_url, start, end) {
                Ok(p) => p,
                Err(e) => {
                    errors.push(format!("{display_name}: date_search failed ({e})"));
                    return Ok(());
                }
            };

            let mut seen_uids: std::collections::HashSet<String> = std::collections::HashSet::new();
            for payload in &payloads {
                for ev in parse_vevents(payload, &mut errors, &display_name) {
                    seen_uids.insert(ev.uid.clone());
                    upsert_event(&conn, &cal_id, &ev, &now_str).map_err(|e| e.to_string())?;
                    events_count += 1;
                }
            }

            // Prune locally-cached CalDAV events that vanished upstream (only
            // within our sync window).
            let start_str = start.format(SQLITE_DT_FMT).to_string();
            let end_str = end.format(SQLITE_DT_FMT).to_string();
            let mut stale_uids: Vec<String> = {
                let mut stmt = conn
                    .prepare(
                        "SELECT uid FROM calendar_events \
                         WHERE calendar_id = ?1 AND dtstart >= ?2 AND dtstart <= ?3",
                    )
                    .map_err(|e| e.to_string())?;
                let rows = stmt
                    .query_map(rusqlite::params![cal_id, start_str, end_str], |r| {
                        r.get::<_, String>(0)
                    })
                    .map_err(|e| e.to_string())?;
                rows.filter_map(|r| r.ok()).collect()
            };
            // ~uid.in_(seen_uids) — when seen_uids is empty the Python keeps
            // every in-window row (uid IS NOT NULL), so nothing is deleted; that
            // matches dropping rows whose uid IS in seen_uids only when non-empty.
            if !seen_uids.is_empty() {
                stale_uids.retain(|u| !seen_uids.contains(u));
            } else {
                stale_uids.clear();
            }
            for uid in &stale_uids {
                conn.execute(
                    "DELETE FROM calendar_events WHERE uid = ?1",
                    rusqlite::params![uid],
                )
                .map_err(|e| e.to_string())?;
            }
            deleted_count += stale_uids.len() as i64;
            Ok(())
        })();

        if let Err(e) = cal_result {
            logger::error("CalDAV sync failed for one calendar");
            let truncated: String = e.chars().take(200).collect();
            errors.push(truncated);
        }
    }

    counts(calendars_count, events_count, deleted_count, errors)
}

/// Upsert a single event by uid (the `existing` / `db.add` branches).
fn upsert_event(
    conn: &rusqlite::Connection,
    cal_id: &str,
    ev: &EventRow,
    now_str: &str,
) -> rusqlite::Result<()> {
    let dtstart_store = ev.dtstart.format(SQLITE_DT_FMT).to_string();
    let dtend_store = ev.dtend.format(SQLITE_DT_FMT).to_string();
    let exists: bool = conn
        .query_row(
            "SELECT 1 FROM calendar_events WHERE uid = ?1",
            rusqlite::params![ev.uid],
            |_| Ok(true),
        )
        .unwrap_or(false);
    if exists {
        conn.execute(
            "UPDATE calendar_events SET calendar_id = ?2, summary = ?3, description = ?4, \
               location = ?5, dtstart = ?6, dtend = ?7, all_day = ?8, is_utc = ?9, rrule = ?10, \
               updated_at = ?11 \
             WHERE uid = ?1",
            rusqlite::params![
                ev.uid,
                cal_id,
                ev.summary,
                ev.description,
                ev.location,
                dtstart_store,
                dtend_store,
                ev.all_day,
                ev.is_utc,
                ev.rrule,
                now_str,
            ],
        )?;
    } else {
        conn.execute(
            "INSERT INTO calendar_events \
               (uid, calendar_id, summary, description, location, dtstart, dtend, all_day, \
                is_utc, rrule, status, created_at, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, 'confirmed', ?11, ?11)",
            rusqlite::params![
                ev.uid,
                cal_id,
                ev.summary,
                ev.description,
                ev.location,
                dtstart_store,
                dtend_store,
                ev.all_day,
                ev.is_utc,
                ev.rrule,
                now_str,
            ],
        )?;
    }
    Ok(())
}

fn counts(calendars: i64, events: i64, deleted: i64, errors: Vec<String>) -> Value {
    json!({
        "calendars": calendars,
        "events": events,
        "deleted": deleted,
        "errors": errors,
    })
}

/// A small wrapper that re-attaches Basic auth to every CalDAV request.
struct AuthClient {
    client: reqwest::blocking::Client,
    username: String,
    password: String,
}

impl AuthClient {
    fn propfind(&self, url: &str, depth: u8, body: &str) -> Result<String, String> {
        let method = reqwest::Method::from_bytes(b"PROPFIND").map_err(|e| e.to_string())?;
        let resp = self
            .client
            .request(method, url)
            .basic_auth(&self.username, Some(&self.password))
            .header("Depth", depth.to_string())
            .header(reqwest::header::CONTENT_TYPE, "application/xml; charset=utf-8")
            .body(body.to_string())
            .send()
            .map_err(|e| e.to_string())?;
        if !resp.status().is_success() && resp.status().as_u16() != 207 {
            return Err(format!("PROPFIND {url} -> HTTP {}", resp.status().as_u16()));
        }
        resp.text().map_err(|e| e.to_string())
    }

    fn discover(&self, url: &str, errors: &mut Vec<String>) -> Vec<RemoteCalendar> {
        // Reuse the free-fn discovery logic but route PROPFIND through auth.
        // For simplicity we inline the principal/home/list walk here.
        let base = match url::Url::parse(url) {
            Ok(u) => u,
            Err(e) => {
                errors.push(format!("Invalid CalDAV URL: {e}"));
                return Vec::new();
            }
        };

        let principal_url = self
            .propfind(
                url,
                0,
                r#"<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>"#,
            )
            .ok()
            .and_then(|xml| first_local_text(&xml, "current-user-principal"))
            .and_then(|frag| all_hrefs(&frag).into_iter().next())
            .and_then(|href| resolve_href(&base, &href));

        let home_url = principal_url.as_ref().and_then(|purl| {
            self.propfind(
                purl,
                0,
                r#"<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><c:calendar-home-set/></d:prop></d:propfind>"#,
            )
            .ok()
            .and_then(|xml| first_local_text(&xml, "calendar-home-set"))
            .and_then(|frag| all_hrefs(&frag).into_iter().next())
            .and_then(|href| resolve_href(&base, &href))
        });

        if let Some(home) = &home_url {
            if let Ok(xml) = self.propfind(
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
                    let abs = match resolve_href(&base, &href) {
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
        let name = self
            .propfind(
                url,
                0,
                r#"<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:"><d:prop><d:displayname/></d:prop></d:propfind>"#,
            )
            .ok()
            .and_then(|xml| first_local_text(&xml, "displayname"))
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "CalDAV".to_string());
        vec![RemoteCalendar {
            url: url.to_string(),
            name,
        }]
    }

    fn report(
        &self,
        cal_url: &str,
        start: NaiveDateTime,
        end: NaiveDateTime,
    ) -> Result<Vec<String>, String> {
        let body = format!(
            r#"<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:getetag/><c:calendar-data/></d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{}" end="{}"/>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"#,
            caldav_time(start),
            caldav_time(end)
        );
        let method = reqwest::Method::from_bytes(b"REPORT").map_err(|e| e.to_string())?;
        let resp = self
            .client
            .request(method, cal_url)
            .basic_auth(&self.username, Some(&self.password))
            .header("Depth", "1")
            .header(reqwest::header::CONTENT_TYPE, "application/xml; charset=utf-8")
            .body(body)
            .send()
            .map_err(|e| e.to_string())?;
        if !resp.status().is_success() && resp.status().as_u16() != 207 {
            return Err(format!("REPORT -> HTTP {}", resp.status().as_u16()));
        }
        let xml = resp.text().map_err(|e| e.to_string())?;
        let mut out = Vec::new();
        for block in response_blocks(&xml) {
            if let Some(data) = first_local_text(&block, "calendar-data") {
                let unescaped = html_escape::decode_html_entities(&data).to_string();
                if !unescaped.trim().is_empty() {
                    out.push(unescaped);
                }
            }
        }
        Ok(out)
    }
}

// ---------------------------------------------------------------------------
// Prefs loader (routes.prefs_routes._load_for_user re-implementation).
// ---------------------------------------------------------------------------

/// Re-implementation of `routes.prefs_routes._load_for_user` against
/// `data/user_prefs.json` (CWD-relative, honoring the `_users` vs flat shapes).
/// `owner` is always passed here (never None), so the `_users` branch indexes by
/// user; a missing file / decode error yields `{}`.
fn load_prefs_for_user(user: &str) -> Value {
    const PREFS_FILE: &str = "data/user_prefs.json";
    let raw = match std::fs::read_to_string(PREFS_FILE) {
        Ok(s) => s,
        Err(_) => return json!({}),
    };
    let all_prefs: Value = match serde_json::from_str(&raw) {
        Ok(v) => v,
        Err(_) => return json!({}),
    };
    if let Some(users) = all_prefs.get("_users") {
        // user is Some -> all_prefs["_users"].get(user, {})
        return users.get(user).cloned().unwrap_or_else(|| json!({}));
    }
    // Legacy flat format — return as-is.
    all_prefs
}

/// Pull CalDAV state into local DB for `owner`. Returns counts + errors. Loads
/// credentials from the user's prefs; no-ops with a clear error if CalDAV isn't
/// configured.
pub async fn sync_caldav(owner: &str) -> Value {
    let prefs = load_prefs_for_user(owner);
    let cfg = prefs.get("caldav").cloned().unwrap_or_else(|| json!({}));
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
    // The stored CalDAV password is encrypted at rest -> decrypt before use
    // (lenient: a legacy plaintext value is returned unchanged).
    let pw = crate::src::secret_storage::decrypt(
        cfg.get("password").and_then(|v| v.as_str()).unwrap_or(""),
    );
    if url.is_empty() || user.is_empty() || pw.is_empty() {
        return json!({
            "calendars": 0,
            "events": 0,
            "deleted": 0,
            "errors": ["CalDAV is not configured"],
        });
    }

    // SSRF hardening: validate + normalize the user-supplied URL BEFORE any
    // network call. A `ValueError` (the `Err`) is surfaced as an errors entry.
    let url = match validate_caldav_url(&url) {
        Ok(u) => u,
        Err(e) => {
            return json!({"calendars": 0, "events": 0, "deleted": 0, "errors": [e]});
        }
    };

    let owner = owner.to_string();
    match tokio::task::spawn_blocking(move || sync_blocking(&owner, &url, &user, &pw)).await {
        Ok(v) => v,
        Err(e) => {
            logger::error("CalDAV sync raised");
            let truncated: String = e.to_string().chars().take(200).collect();
            json!({"calendars": 0, "events": 0, "deleted": 0, "errors": [truncated]})
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stable_cal_id_is_deterministic_and_prefixed() {
        let a = stable_cal_id("https://cal.example.com/u/me/calendar/");
        let b = stable_cal_id("https://cal.example.com/u/me/calendar/");
        assert_eq!(a, b);
        assert!(a.starts_with("caldav-"));
        // "caldav-" (7) + 24 hex chars.
        assert_eq!(a.len(), 7 + 24);
    }

    #[test]
    fn to_utc_naive_branches() {
        // tz-aware -> false; naive UTC kept.
        let dt = NaiveDateTime::parse_from_str("2024-03-10 12:00:00", "%Y-%m-%d %H:%M:%S").unwrap();
        let (out, all_day) = to_utc_naive(IcalWhen::Aware(dt));
        assert_eq!(out, dt);
        assert!(!all_day);
        // naive -> as-is, false.
        let (out, all_day) = to_utc_naive(IcalWhen::Naive(dt));
        assert_eq!(out, dt);
        assert!(!all_day);
        // date-only -> midnight, true.
        let d = chrono::NaiveDate::from_ymd_opt(2024, 3, 10).unwrap();
        let (out, all_day) = to_utc_naive(IcalWhen::Date(d));
        assert_eq!(out, d.and_hms_opt(0, 0, 0).unwrap());
        assert!(all_day);
    }

    #[test]
    fn parse_vevent_basic() {
        let ics = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:evt-1\r\nSUMMARY:Standup\r\nLOCATION:Room 1\r\nDTSTART:20240310T120000Z\r\nDTEND:20240310T123000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n";
        let mut errors = Vec::new();
        let rows = parse_vevents(ics, &mut errors, "Test");
        assert_eq!(rows.len(), 1);
        let ev = &rows[0];
        assert_eq!(ev.uid, "evt-1");
        assert_eq!(ev.summary, "Standup");
        assert_eq!(ev.location, "Room 1");
        assert!(!ev.all_day);
        // UTC datetime -> is_utc true.
        assert!(ev.is_utc);
        assert_eq!(
            ev.dtstart,
            NaiveDateTime::parse_from_str("2024-03-10 12:00:00", "%Y-%m-%d %H:%M:%S").unwrap()
        );
    }

    #[test]
    fn parse_vevent_all_day() {
        let ics = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:day-1\r\nSUMMARY:Holiday\r\nDTSTART;VALUE=DATE:20240701\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n";
        let mut errors = Vec::new();
        let rows = parse_vevents(ics, &mut errors, "Test");
        assert_eq!(rows.len(), 1);
        let ev = &rows[0];
        assert!(ev.all_day);
        assert!(!ev.is_utc);
        // No DTEND, all-day -> +1 day.
        let start = chrono::NaiveDate::from_ymd_opt(2024, 7, 1)
            .unwrap()
            .and_hms_opt(0, 0, 0)
            .unwrap();
        assert_eq!(ev.dtstart, start);
        assert_eq!(ev.dtend, start + chrono::Duration::days(1));
    }

    #[test]
    fn first_local_text_strips_prefix() {
        let xml = r#"<d:multistatus xmlns:d="DAV:"><d:response><d:displayname>Work</d:displayname></d:response></d:multistatus>"#;
        assert_eq!(first_local_text(xml, "displayname").as_deref(), Some("Work"));
    }

    #[test]
    fn response_blocks_split() {
        let xml = r#"<multistatus><response><href>/a</href></response><response><href>/b</href></response></multistatus>"#;
        let blocks = response_blocks(xml);
        assert_eq!(blocks.len(), 2);
        assert!(blocks[0].contains("/a"));
        assert!(blocks[1].contains("/b"));
    }

    #[test]
    fn caldav_time_format() {
        let dt =
            NaiveDateTime::parse_from_str("2024-03-10 12:34:56", "%Y-%m-%d %H:%M:%S").unwrap();
        assert_eq!(caldav_time(dt), "20240310T123456Z");
    }

    #[test]
    fn validate_caldav_url_accepts_public_and_normalizes() {
        // Trailing slash stripped; scheme accepted.
        assert_eq!(
            validate_caldav_url("https://cal.example.com/dav/").unwrap(),
            "https://cal.example.com/dav"
        );
        // Whitespace trimmed; multiple trailing slashes stripped.
        assert_eq!(
            validate_caldav_url("  https://cal.example.com/dav//  ").unwrap(),
            "https://cal.example.com/dav"
        );
        // Public IP literal is allowed.
        assert!(validate_caldav_url("https://1.1.1.1/dav").is_ok());
    }

    #[test]
    fn validate_caldav_url_rejects_scheme_host_creds_fragment() {
        assert_eq!(
            validate_caldav_url("").unwrap_err(),
            "CalDAV URL is required"
        );
        assert_eq!(
            validate_caldav_url("   ").unwrap_err(),
            "CalDAV URL is required"
        );
        assert_eq!(
            validate_caldav_url("ftp://cal.example.com/dav").unwrap_err(),
            "CalDAV URL must start with http:// or https://"
        );
        // No scheme -> unparseable -> scheme message (parity).
        assert_eq!(
            validate_caldav_url("cal.example.com/dav").unwrap_err(),
            "CalDAV URL must start with http:// or https://"
        );
        // Embedded credentials.
        assert_eq!(
            validate_caldav_url("https://user:pass@cal.example.com/dav").unwrap_err(),
            "Put CalDAV credentials in the username/password fields, not the URL"
        );
        assert_eq!(
            validate_caldav_url("https://user@cal.example.com/dav").unwrap_err(),
            "Put CalDAV credentials in the username/password fields, not the URL"
        );
        // Fragment.
        assert_eq!(
            validate_caldav_url("https://cal.example.com/dav#frag").unwrap_err(),
            "CalDAV URL fragments are not allowed"
        );
        // Invalid port.
        assert_eq!(
            validate_caldav_url("https://cal.example.com:99999/dav").unwrap_err(),
            "CalDAV URL has an invalid port"
        );
    }

    #[test]
    fn validate_caldav_url_blocks_internal_hostnames() {
        for h in [
            "http://localhost/dav",
            "http://localhost./dav",
            "http://ip6-localhost/dav",
            "http://metadata.google.internal/dav",
            "http://foo.localhost/dav",
        ] {
            assert_eq!(
                validate_caldav_url(h).unwrap_err(),
                "CalDAV URL host is not allowed",
                "expected {h} to be blocked"
            );
        }
    }

    #[test]
    fn validate_caldav_url_blocks_loopback_linklocal_multicast_unspecified_ips() {
        for h in [
            "http://127.0.0.1/dav",        // loopback
            "http://[::1]/dav",            // IPv6 loopback
            "http://169.254.169.254/dav",  // link-local (cloud metadata)
            "http://[fe80::1]/dav",        // IPv6 link-local
            "http://224.0.0.1/dav",        // multicast
            "http://0.0.0.0/dav",          // unspecified
        ] {
            assert_eq!(
                validate_caldav_url(h).unwrap_err(),
                "CalDAV URL host is not allowed",
                "expected {h} to be blocked"
            );
        }
    }

    #[test]
    fn validate_caldav_url_private_ip_gated_by_env() {
        // This is the only test that touches ODYSSEUS_ALLOW_PRIVATE_CALDAV; it
        // exercises both branches in sequence to stay deterministic regardless of
        // parallel test ordering.
        std::env::remove_var("ODYSSEUS_ALLOW_PRIVATE_CALDAV");
        for h in [
            "http://10.0.0.5/dav",
            "http://192.168.1.5/dav",
            "http://172.16.0.1/dav",
        ] {
            assert_eq!(
                validate_caldav_url(h).unwrap_err(),
                "Private CalDAV IPs require ODYSSEUS_ALLOW_PRIVATE_CALDAV=1",
                "expected {h} to be gated"
            );
        }
        // With the env var set, private targets are allowed.
        std::env::set_var("ODYSSEUS_ALLOW_PRIVATE_CALDAV", "1");
        assert_eq!(
            validate_caldav_url("http://10.0.0.5/dav/").unwrap(),
            "http://10.0.0.5/dav"
        );
        std::env::set_var("ODYSSEUS_ALLOW_PRIVATE_CALDAV", "true");
        assert!(validate_caldav_url("http://192.168.1.5/dav").is_ok());
        // ... but loopback stays blocked even when private is allowed.
        assert_eq!(
            validate_caldav_url("http://127.0.0.1/dav").unwrap_err(),
            "CalDAV URL host is not allowed"
        );
        // Cleanup so we don't leak into any other test that might read it.
        std::env::remove_var("ODYSSEUS_ALLOW_PRIVATE_CALDAV");
    }
}
