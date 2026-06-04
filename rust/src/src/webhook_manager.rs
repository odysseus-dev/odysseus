// src/webhook_manager.rs  <- src/webhook_manager.py
//! Outgoing webhook manager — fires HTTP POSTs when events happen.
//!
//! Faithful port of `src/webhook_manager.py`. The pure validators / SSRF guards
//! map directly; the `httpx.AsyncClient(timeout=10, follow_redirects=False)`
//! delivery client becomes `reqwest::Client` with `redirect(Policy::none())` and
//! a 10s timeout. SQLAlchemy `db.query(Webhook)` becomes raw rusqlite SQL over the
//! `webhooks` table via `core::database::session_local()` (the established
//! convention — `src/database.rs`'s ORM models are not ported). HMAC-SHA256 is
//! `hmac` + `sha2` + `hex` (already deps).
//!
//! DEVIATIONS:
//! * `ipaddress.ip_network(...)` CIDR membership is hand-rolled over
//!   `std::net::IpAddr` octet/segment range checks (no `ip-cidr` crate), matching
//!   the project's hand-rolled-protocol precedent.
//! * The webhook BODY timestamp is `datetime.utcnow().isoformat()` (a `T`
//!   separator, microseconds only when non-zero), which is **distinct** from the
//!   space-separated SQLite DateTime format used for the `last_triggered_at` row
//!   column — kept separate here just like the Python.
//! * Python's `fire_and_forget` event-loop bookkeeping (`set_loop` /
//!   `run_coroutine_threadsafe`) collapses to a single `tokio` `Handle` spawn:
//!   the Rust server is always inside the tokio runtime, so there is no separate
//!   "sync thread" loop to thread through.

use crate::pylog as logger;
use crate::src::api_key_manager::APIKeyManager;
use chrono::{Timelike, Utc};
use hmac::{Hmac, Mac};
use once_cell::sync::Lazy;
use sha2::Sha256;
use std::collections::HashSet;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, ToSocketAddrs};
use std::sync::Arc;

type HmacSha256 = Hmac<Sha256>;

/// `ALLOWED_EVENTS = frozenset({...})`.
pub static ALLOWED_EVENTS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    HashSet::from([
        "session.created",
        "chat.completed",
        "chat.message",
        "webhook.test",
    ])
});

/// A private/internal network range, hand-rolled to replace `ipaddress.ip_network`.
enum PrivateNet {
    /// IPv4 prefix: `(network base, prefix length)`.
    V4(Ipv4Addr, u8),
    /// IPv6 prefix: `(network base, prefix length)`.
    V6(Ipv6Addr, u8),
}

/// Block requests to private/internal networks. Mirrors `_PRIVATE_NETWORKS`:
/// 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8, 169.254.0.0/16,
/// ::1/128, fc00::/7, fe80::/10.
static PRIVATE_NETWORKS: Lazy<Vec<PrivateNet>> = Lazy::new(|| {
    vec![
        PrivateNet::V4(Ipv4Addr::new(10, 0, 0, 0), 8),
        PrivateNet::V4(Ipv4Addr::new(172, 16, 0, 0), 12),
        PrivateNet::V4(Ipv4Addr::new(192, 168, 0, 0), 16),
        PrivateNet::V4(Ipv4Addr::new(127, 0, 0, 0), 8),
        PrivateNet::V4(Ipv4Addr::new(169, 254, 0, 0), 16),
        PrivateNet::V6(Ipv6Addr::LOCALHOST, 128),
        PrivateNet::V6(Ipv6Addr::new(0xfc00, 0, 0, 0, 0, 0, 0, 0), 7),
        PrivateNet::V6(Ipv6Addr::new(0xfe80, 0, 0, 0, 0, 0, 0, 0), 10),
    ]
});

fn v4_in_net(addr: Ipv4Addr, base: Ipv4Addr, prefix: u8) -> bool {
    if prefix == 0 {
        return true;
    }
    let a = u32::from(addr);
    let b = u32::from(base);
    let shift = 32 - prefix as u32;
    (a >> shift) == (b >> shift)
}

fn v6_in_net(addr: Ipv6Addr, base: Ipv6Addr, prefix: u8) -> bool {
    if prefix == 0 {
        return true;
    }
    let a = u128::from(addr);
    let b = u128::from(base);
    let shift = 128 - prefix as u32;
    (a >> shift) == (b >> shift)
}

/// `_ip_is_private(addr)` — the explicit `_PRIVATE_NETWORKS` OR the
/// private/loopback/link-local/reserved/multicast/unspecified predicates, after
/// unwrapping an IPv4-mapped IPv6 (e.g. `::ffff:127.0.0.1`) to its embedded v4.
fn ip_is_private(addr: &IpAddr) -> bool {
    let addr = match *addr {
        IpAddr::V6(v6) => match v6.to_ipv4_mapped() {
            Some(v4) => IpAddr::V4(v4),
            None => IpAddr::V6(v6),
        },
        v4 => v4,
    };
    let in_networks = PRIVATE_NETWORKS.iter().any(|net| match (&addr, net) {
        (IpAddr::V4(a), PrivateNet::V4(base, p)) => v4_in_net(*a, *base, *p),
        (IpAddr::V6(a), PrivateNet::V6(base, p)) => v6_in_net(*a, *base, *p),
        _ => false,
    });
    in_networks
        || match addr {
            IpAddr::V4(a) => {
                a.is_private()
                    || a.is_loopback()
                    || a.is_link_local()
                    || a.octets()[0] >= 240 // 240.0.0.0/4 (is_reserved is unstable)
                    || a.is_multicast()
                    || a.is_unspecified()
            }
            IpAddr::V6(a) => {
                a.is_loopback()
                    || a.is_multicast()
                    || a.is_unspecified()
                    || (a.segments()[0] & 0xffc0) == 0xfe80 // fe80::/10
                    || matches!(a.segments()[0] >> 8, 0xfc | 0xfd) // fc00::/7
            }
        }
}

/// Resolve a hostname to all its A/AAAA records. Empty list on failure.
///
/// Python uses `socket.getaddrinfo(hostname, None)`; the closest stable Rust
/// analogue is `ToSocketAddrs` with a dummy port (0).
fn resolve_hostname_ips(hostname: &str) -> Vec<IpAddr> {
    match (hostname, 0u16).to_socket_addrs() {
        Ok(addrs) => addrs.map(|sa| sa.ip()).collect(),
        Err(_) => Vec::new(),
    }
}

/// Check if a URL points to a private/internal address.
///
/// Resolves DNS names so attackers can't hide an internal IP behind
/// `internal.lan` or `127.0.0.1.nip.io`. Re-checked at delivery time too, as a
/// partial defense against DNS rebinding.
pub fn is_private_url(url: &str) -> bool {
    let parsed = match url::Url::parse(url) {
        Ok(p) => p,
        // urlparse never raises in Python; a parse failure here is the closest
        // analogue to "no hostname" -> fail closed.
        Err(_) => return true,
    };
    let hostname = parsed.host_str().unwrap_or("").trim().to_string();
    if hostname.is_empty() {
        return true;
    }
    // Block common internal hostnames + suffixes the resolver may not catch.
    let h_lower = hostname.to_lowercase();
    if matches!(
        h_lower.as_str(),
        "localhost" | "0.0.0.0" | "metadata.google.internal" | "metadata"
    ) {
        return true;
    }
    for suffix in [".local", ".internal", ".lan", ".intranet", ".localhost"] {
        if h_lower.ends_with(suffix) {
            return true;
        }
    }
    // IP literal? short-circuit. `url` strips the brackets from IPv6 literals,
    // so a bare parse matches Python's `ipaddress.ip_address(hostname)`.
    if let Ok(ip) = hostname.parse::<IpAddr>() {
        return ip_is_private(&ip);
    }
    // DNS hostname — resolve and check every record.
    let addrs = resolve_hostname_ips(&hostname);
    if addrs.is_empty() {
        // Couldn't resolve -> fail closed; let validation reject the URL.
        return true;
    }
    addrs.iter().any(ip_is_private)
}

/// Validate and normalize a webhook URL. Returns `Err` (ValueError) if invalid.
pub fn validate_webhook_url(url: &str) -> crate::error::PyResult<String> {
    let url = url.trim().to_string();
    if url.chars().count() > 2048 {
        return Err(crate::error::PyError::value(
            "URL too long (max 2048 characters)",
        ));
    }
    let parsed = url::Url::parse(&url);
    let scheme = parsed.as_ref().map(|p| p.scheme().to_string()).ok();
    if !matches!(scheme.as_deref(), Some("http") | Some("https")) {
        return Err(crate::error::PyError::value("URL must use http or https"));
    }
    let has_host = parsed
        .as_ref()
        .map(|p| p.host_str().is_some())
        .unwrap_or(false);
    if !has_host {
        return Err(crate::error::PyError::value("URL must have a hostname"));
    }
    if is_private_url(&url) {
        return Err(crate::error::PyError::value(
            "URL must not point to private/internal addresses",
        ));
    }
    Ok(url)
}

/// Validate comma-separated event names. Returns cleaned string (ValueError on
/// failure, with the exact sorted error wording).
pub fn validate_events(events_str: &str) -> crate::error::PyResult<String> {
    let events: Vec<String> = events_str
        .split(',')
        .map(|e| e.trim().to_string())
        .filter(|e| !e.is_empty())
        .collect();
    if events.is_empty() {
        return Err(crate::error::PyError::value(
            "At least one event is required",
        ));
    }
    // invalid = set(events) - ALLOWED_EVENTS — dedupe then sort the leftovers.
    let invalid_set: std::collections::BTreeSet<String> = events
        .iter()
        .filter(|e| !ALLOWED_EVENTS.contains(e.as_str()))
        .cloned()
        .collect();
    if !invalid_set.is_empty() {
        let invalid_str = invalid_set.into_iter().collect::<Vec<_>>().join(", ");
        // Allowed: sorted(ALLOWED_EVENTS - {"webhook.test"}).
        let mut allowed: Vec<&str> = ALLOWED_EVENTS
            .iter()
            .filter(|e| **e != "webhook.test")
            .copied()
            .collect();
        allowed.sort();
        let allowed_str = allowed.join(", ");
        return Err(crate::error::PyError::value(format!(
            "Invalid events: {invalid_str}. Allowed: {allowed_str}"
        )));
    }
    Ok(events.join(","))
}

/// Strip potentially sensitive details from error messages.
pub fn sanitize_error(error: &str, max_len: usize) -> String {
    // Remove IP addresses and ports.
    static IP_RE: Lazy<regex::Regex> = Lazy::new(|| {
        regex::Regex::new(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?").unwrap()
    });
    static URL_RE: Lazy<regex::Regex> =
        Lazy::new(|| regex::Regex::new(r"https?://[^\s/]+").unwrap());
    let cleaned = IP_RE.replace_all(error, "[redacted]");
    let cleaned = URL_RE.replace_all(&cleaned, "[redacted-url]");
    // cleaned[:max_len] — Python slices by character.
    cleaned.chars().take(max_len).collect()
}

/// A `webhooks` row matched for delivery (the slice `fire`/`_deliver` need).
struct WebhookRow {
    id: String,
    url: String,
    secret: Option<String>,
    events: String,
}

pub struct WebhookManager {
    client: reqwest::Client,
    api_key_manager: Option<Arc<APIKeyManager>>,
}

impl WebhookManager {
    pub fn new(api_key_manager: Option<Arc<APIKeyManager>>) -> Self {
        // Disable redirects to prevent SSRF via redirect chains; timeout=10.
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(10))
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .unwrap_or_else(|_| reqwest::Client::new());
        WebhookManager {
            client,
            api_key_manager,
        }
    }

    /// Decrypt a webhook signing secret from DB storage.
    fn decrypt_secret(&self, encrypted: Option<&str>) -> Option<String> {
        let encrypted = encrypted?;
        if encrypted.is_empty() {
            return None;
        }
        if let Some(akm) = &self.api_key_manager {
            return match akm.decrypt_api_key(encrypted) {
                Ok(s) => Some(s),
                // If decryption fails, assume it's stored in plaintext (legacy).
                Err(_) => Some(encrypted.to_string()),
            };
        }
        Some(encrypted.to_string())
    }

    /// Schedule webhook fire from any context. Never blocks.
    ///
    /// The Rust server always runs inside tokio, so this spawns onto the current
    /// handle (the `RuntimeError`/`run_coroutine_threadsafe` fallback path in the
    /// Python collapses to "no handle -> drop", which never happens at runtime).
    pub fn fire_and_forget(self: &Arc<Self>, event: &str, payload: serde_json::Value) {
        if !ALLOWED_EVENTS.contains(event) {
            return;
        }
        let this = Arc::clone(self);
        let event = event.to_string();
        if let Ok(handle) = tokio::runtime::Handle::try_current() {
            handle.spawn(async move {
                this.fire(&event, payload).await;
            });
        }
    }

    /// Fire webhooks matching the given event.
    pub async fn fire(self: &Arc<Self>, event: &str, payload: serde_json::Value) {
        if !ALLOWED_EVENTS.contains(event) {
            return;
        }
        // db.query(Webhook).filter(is_active == True).all(); filter to events
        // containing this one (split on ',').
        let matching = self.fetch_matching(event);

        for wh in matching {
            let decrypted_secret = self.decrypt_secret(wh.secret.as_deref());
            let this = Arc::clone(self);
            let event = event.to_string();
            let payload = payload.clone();
            tokio::spawn(async move {
                this.deliver(&wh.id, &wh.url, decrypted_secret.as_deref(), &event, &payload)
                    .await;
            });
        }
    }

    /// Active webhooks whose `events` CSV contains `event`.
    fn fetch_matching(&self, event: &str) -> Vec<WebhookRow> {
        let conn = match crate::core::database::session_local() {
            Ok(c) => c,
            Err(e) => {
                logger::warning(&format!("Webhook fetch failed: {e}"));
                return Vec::new();
            }
        };
        let mut stmt = match conn
            .prepare("SELECT id, url, secret, events FROM webhooks WHERE is_active = 1")
        {
            Ok(s) => s,
            Err(e) => {
                logger::warning(&format!("Webhook fetch failed: {e}"));
                return Vec::new();
            }
        };
        let rows = stmt.query_map([], |row| {
            Ok(WebhookRow {
                id: row.get(0)?,
                url: row.get(1)?,
                secret: row.get::<_, Option<String>>(2)?,
                events: row.get(3)?,
            })
        });
        let mut out: Vec<WebhookRow> = Vec::new();
        if let Ok(rows) = rows {
            for r in rows.flatten() {
                // event in w.events.split(",")
                if r.events.split(',').any(|e| e == event) {
                    out.push(r);
                }
            }
        }
        out
    }

    /// Public method for the test-webhook route.
    pub async fn deliver_test(
        self: &Arc<Self>,
        webhook_id: &str,
        url: &str,
        encrypted_secret: Option<&str>,
    ) {
        let decrypted = self.decrypt_secret(encrypted_secret);
        self.deliver(
            webhook_id,
            url,
            decrypted.as_deref(),
            "webhook.test",
            &serde_json::json!({"message": "Test ping from Odysseus"}),
        )
        .await;
    }

    /// Internal delivery. Never call directly from outside this struct (use
    /// `deliver_test`).
    async fn deliver(
        &self,
        webhook_id: &str,
        url: &str,
        secret: Option<&str>,
        event: &str,
        payload: &serde_json::Value,
    ) {
        // Re-validate URL at delivery time in case DB was tampered with.
        if let Err(e) = validate_webhook_url(url) {
            logger::warning(&format!(
                "Webhook {webhook_id} has invalid URL, skipping: {e}"
            ));
            return;
        }

        // body = json.dumps({"event", "timestamp": utcnow().isoformat(), "data"})
        let body = serde_json::json!({
            "event": event,
            "timestamp": utcnow_isoformat(),
            "data": payload,
        });
        let body = serde_json::to_string(&body).unwrap_or_default();

        let mut req = self
            .client
            .post(url)
            .header("Content-Type", "application/json")
            .header("X-Odysseus-Event", event)
            .header("User-Agent", "Odysseus-Webhook/1.0")
            .body(body.clone());
        if let Some(secret) = secret {
            if !secret.is_empty() {
                let mut mac = HmacSha256::new_from_slice(secret.as_bytes())
                    .expect("HMAC accepts any key length");
                mac.update(body.as_bytes());
                let sig = hex::encode(mac.finalize().into_bytes());
                req = req.header("X-Odysseus-Signature", sig);
            }
        }

        match req.send().await {
            Ok(resp) => {
                let status = resp.status().as_u16() as i64;
                self.record_result(webhook_id, Some(status), None);
            }
            Err(e) => {
                logger::warning(&format!("Webhook delivery failed for {webhook_id}"));
                self.record_result(webhook_id, None, Some(sanitize_error(&e.to_string(), 200)));
            }
        }
    }

    /// `db.query(Webhook).filter(id == webhook_id).update({...}); db.commit()`.
    fn record_result(&self, webhook_id: &str, status_code: Option<i64>, last_error: Option<String>) {
        let conn = match crate::core::database::session_local() {
            Ok(c) => c,
            Err(_) => return,
        };
        let now = crate::pydatetime::utcnow_naive_iso();
        let _ = conn.execute(
            "UPDATE webhooks SET last_triggered_at = ?1, last_status_code = ?2, last_error = ?3 \
             WHERE id = ?4",
            rusqlite::params![now, status_code, last_error, webhook_id],
        );
    }
}

/// `datetime.utcnow().isoformat()` — naive UTC, `T` separator, microseconds only
/// when non-zero. Distinct from the SQLite DateTime column format.
fn utcnow_isoformat(/* */) -> String {
    let now = Utc::now().naive_utc();
    if now.nanosecond() == 0 {
        now.format("%Y-%m-%dT%H:%M:%S").to_string()
    } else {
        now.format("%Y-%m-%dT%H:%M:%S%.6f").to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validate_events_rejects_unknown() {
        let err = validate_events("chat.message, bogus, alsobad").unwrap_err();
        // sorted invalid + sorted allowed (minus webhook.test).
        assert_eq!(
            err.to_string(),
            "Invalid events: alsobad, bogus. Allowed: chat.completed, chat.message, session.created"
        );
    }

    #[test]
    fn validate_events_requires_one() {
        let err = validate_events("  , ,").unwrap_err();
        assert_eq!(err.to_string(), "At least one event is required");
    }

    #[test]
    fn validate_events_cleans() {
        assert_eq!(
            validate_events(" chat.message , session.created ").unwrap(),
            "chat.message,session.created"
        );
    }

    #[test]
    fn private_url_blocks_literals_and_names() {
        assert!(is_private_url("http://127.0.0.1/x"));
        assert!(is_private_url("http://10.1.2.3/x"));
        assert!(is_private_url("http://192.168.0.5/x"));
        assert!(is_private_url("http://localhost/x"));
        assert!(is_private_url("http://foo.internal/x"));
        assert!(is_private_url("http://172.16.5.5/x"));
        // 172.32 is OUTSIDE 172.16.0.0/12.
        assert!(!is_private_url("http://172.32.0.1/x"));
        // ::1 literal.
        assert!(is_private_url("http://[::1]/x"));
    }

    #[test]
    fn validate_url_length_and_scheme() {
        assert!(validate_webhook_url("ftp://example.com").is_err());
        let long = format!("http://example.com/{}", "a".repeat(2100));
        assert!(validate_webhook_url(&long).is_err());
    }

    #[test]
    fn sanitize_redacts_ip_and_url() {
        // The url regex `https?://[^\s/]+` stops at the first `/`, so the path
        // survives — exactly matching Python's `re.sub(...)`.
        let s = sanitize_error("connect to 10.1.2.3:8080 via http://internal/path failed", 200);
        assert_eq!(s, "connect to [redacted] via [redacted-url]/path failed");
    }

    #[test]
    fn v4_cidr_math() {
        assert!(v4_in_net(
            Ipv4Addr::new(10, 255, 255, 255),
            Ipv4Addr::new(10, 0, 0, 0),
            8
        ));
        assert!(!v4_in_net(
            Ipv4Addr::new(11, 0, 0, 0),
            Ipv4Addr::new(10, 0, 0, 0),
            8
        ));
        assert!(v4_in_net(
            Ipv4Addr::new(172, 31, 255, 255),
            Ipv4Addr::new(172, 16, 0, 0),
            12
        ));
        assert!(!v4_in_net(
            Ipv4Addr::new(172, 32, 0, 0),
            Ipv4Addr::new(172, 16, 0, 0),
            12
        ));
    }
}
