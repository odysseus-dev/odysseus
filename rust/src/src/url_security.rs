// src/url_security.rs  <- src/url_security.py
//! URL validation helpers for server-side outbound requests (SSRF). Used for
//! UNTRUSTED outbound URLs (API-token / user supplied), not admin-created model
//! endpoints that may intentionally target private providers. DNS failures fail
//! closed; DNS checks reduce obvious private-network targets but do not eliminate
//! every DNS-rebinding race.

use std::net::{IpAddr, ToSocketAddrs};

const INTERNAL_HOSTNAMES: [&str; 3] = ["localhost", "metadata", "metadata.google.internal"];
const INTERNAL_SUFFIXES: [&str; 5] = [".localhost", ".local", ".internal", ".lan", ".intranet"];

/// `_blocked_ip(addr)` — the union of the explicit `_BLOCKED_NETWORKS` plus the
/// private/loopback/link-local/multicast/unspecified/reserved predicates.
fn blocked_ip(addr: IpAddr) -> bool {
    // v4-mapped IPv6 -> judge the embedded v4 (parity with ipaddress semantics).
    let addr = match addr {
        IpAddr::V6(v6) => match v6.to_ipv4_mapped() {
            Some(v4) => IpAddr::V4(v4),
            None => IpAddr::V6(v6),
        },
        v4 => v4,
    };
    match addr {
        IpAddr::V4(a) => {
            let o = a.octets();
            a.is_private()
                || a.is_loopback()
                || a.is_link_local()
                || a.is_multicast()
                || a.is_unspecified()
                || o[0] >= 240 // 240.0.0.0/4 reserved (is_reserved is unstable)
                || o[0] == 0 // 0.0.0.0/8
                || (o[0] == 100 && (o[1] & 0xc0) == 64) // 100.64.0.0/10 (CGNAT)
        }
        IpAddr::V6(a) => {
            a.is_loopback()
                || a.is_multicast()
                || a.is_unspecified()
                || (a.segments()[0] & 0xffc0) == 0xfe80 // fe80::/10 link-local
                || matches!(a.segments()[0] >> 8, 0xfc | 0xfd) // fc00::/7 ULA (private)
        }
    }
}

/// `_resolve_hostname_ips(hostname)` — A + AAAA records (empty on failure).
fn resolve_hostname_ips(hostname: &str) -> Vec<IpAddr> {
    match (hostname, 0u16).to_socket_addrs() {
        Ok(addrs) => addrs.map(|sa| sa.ip()).collect(),
        Err(_) => Vec::new(),
    }
}

/// `_host_resolves_publicly(hostname)`.
fn host_resolves_publicly(hostname: &str) -> bool {
    let host = hostname.trim().to_lowercase();
    if INTERNAL_HOSTNAMES.contains(&host.as_str())
        || INTERNAL_SUFFIXES.iter().any(|s| host.ends_with(s))
    {
        return false;
    }
    // IP literal -> classify directly.
    if let Ok(ip) = host.parse::<IpAddr>() {
        return !blocked_ip(ip);
    }
    let addrs = resolve_hostname_ips(&host);
    !addrs.is_empty() && addrs.iter().all(|a| !blocked_ip(*a))
}

/// `is_public_http_url(url)` — true iff `url` is an http(s) URL whose host
/// resolves to a public address.
pub fn is_public_http_url(url: &str) -> bool {
    let parsed = match url::Url::parse(url.trim()) {
        Ok(p) => p,
        Err(_) => return false,
    };
    if !matches!(parsed.scheme(), "http" | "https") {
        return false;
    }
    match parsed.host_str() {
        Some(h) if !h.is_empty() => {
            host_resolves_publicly(h.trim_start_matches('[').trim_end_matches(']'))
        }
        _ => false,
    }
}

/// `validate_public_http_url(url, max_length=2048)` — return the cleaned URL or an
/// `Err(reason)` (the Python `ValueError`).
pub fn validate_public_http_url(url: &str) -> Result<String, String> {
    validate_public_http_url_max(url, 2048)
}

pub fn validate_public_http_url_max(url: &str, max_length: usize) -> Result<String, String> {
    let cleaned = url.trim().to_string();
    if cleaned.chars().count() > max_length {
        return Err("URL is too long".to_string());
    }
    if !is_public_http_url(&cleaned) {
        return Err("URL must point to a public HTTP(S) endpoint".to_string());
    }
    Ok(cleaned)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocks_internal_hosts_and_ips() {
        assert!(!is_public_http_url("http://localhost:8000"));
        assert!(!is_public_http_url("http://foo.internal/x"));
        assert!(!is_public_http_url("http://127.0.0.1/x"));
        assert!(!is_public_http_url("http://10.0.0.5/x"));
        assert!(!is_public_http_url("http://169.254.169.254/latest"));
        assert!(!is_public_http_url("http://100.64.1.1/x")); // CGNAT
        assert!(!is_public_http_url("ftp://example.com/x")); // scheme
        assert!(validate_public_http_url("http://10.0.0.5").is_err());
    }

    #[test]
    fn allows_public_literal() {
        assert!(is_public_http_url("https://1.1.1.1/"));
        assert!(validate_public_http_url("https://1.1.1.1/").is_ok());
    }
}
