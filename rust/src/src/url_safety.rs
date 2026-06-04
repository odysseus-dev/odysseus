// src/url_safety.rs  <- src/url_safety.py
//! Outbound URL safety checks (SSRF hardening).
//!
//! Run before the server makes a request to a *user-supplied* URL — e.g. the
//! custom embedding endpoint set via `POST /api/embeddings/endpoint`, which then
//! triggers an outbound HTTP call.
//!
//! Odysseus is local-first: pointing an endpoint at a loopback or LAN address (a
//! local vLLM / llama.cpp / Ollama server) is a normal, intended setup, so this
//! guard does NOT blanket-block private addresses by default. What it ALWAYS
//! rejects: a non-HTTP(S) scheme, the link-local range (169.254.0.0/16 / fe80::/10
//! — the cloud instance-metadata SSRF vector), and multicast / reserved /
//! unspecified addresses. Set `EMBEDDING_BLOCK_PRIVATE_IPS=true` (`block_private`)
//! to additionally reject all private and loopback targets.

use std::net::{IpAddr, ToSocketAddrs};

const ALLOWED_SCHEMES: [&str; 2] = ["http", "https"];

/// `_default_resolver(host)` — resolve a hostname (or IP literal) to its IPs.
/// Python uses `socket.getaddrinfo(host, None)`; `ToSocketAddrs` with port 0 is
/// the stable analogue and likewise accepts IP literals verbatim.
fn default_resolver(host: &str) -> Vec<IpAddr> {
    match (host, 0u16).to_socket_addrs() {
        Ok(addrs) => addrs.map(|sa| sa.ip()).collect(),
        Err(_) => Vec::new(),
    }
}

/// `_classify(ip, block_private)` — rejection reason, or `None` if allowed.
fn classify(ip: IpAddr, block_private: bool) -> Option<String> {
    // IPv4-mapped IPv6 (e.g. ::ffff:169.254.169.254) -> judge the embedded v4.
    let ip = match ip {
        IpAddr::V6(v6) => match v6.to_ipv4_mapped() {
            Some(v4) => IpAddr::V4(v4),
            None => IpAddr::V6(v6),
        },
        v4 => v4,
    };
    let link_local = match ip {
        IpAddr::V4(a) => a.is_link_local(),
        // fe80::/10
        IpAddr::V6(a) => (a.segments()[0] & 0xffc0) == 0xfe80,
    };
    if link_local {
        return Some(format!("link-local address blocked (SSRF metadata risk): {ip}"));
    }
    let reserved = match ip {
        IpAddr::V4(a) => a.octets()[0] >= 240, // 240.0.0.0/4 (is_reserved is unstable)
        IpAddr::V6(_) => false,
    };
    if ip.is_multicast() || reserved || ip.is_unspecified() {
        return Some(format!("disallowed address: {ip}"));
    }
    if block_private {
        let private = match ip {
            IpAddr::V4(a) => a.is_private(),
            // fc00::/7 (unique-local).
            IpAddr::V6(a) => matches!(a.segments()[0] >> 8, 0xfc | 0xfd),
        };
        if private || ip.is_loopback() {
            return Some(format!("private/loopback address blocked: {ip}"));
        }
    }
    None
}

/// `check_outbound_url(url, block_private=False)` — validate a user-supplied
/// outbound URL. Returns `(ok, reason)`; `ok` is true only when safe to fetch.
pub fn check_outbound_url(url: &str, block_private: bool) -> (bool, String) {
    check_outbound_url_with(url, block_private, default_resolver)
}

/// As [`check_outbound_url`] but with an injectable resolver (the Python
/// `resolver=` kwarg) so tests can avoid real DNS.
pub fn check_outbound_url_with(
    url: &str,
    block_private: bool,
    resolver: impl Fn(&str) -> Vec<IpAddr>,
) -> (bool, String) {
    let trimmed = url.trim();
    if trimmed.is_empty() {
        return (false, "URL is required".to_string());
    }
    let parsed = match url::Url::parse(trimmed) {
        Ok(p) => p,
        Err(e) => return (false, format!("unparseable URL: {e}")),
    };
    let scheme = parsed.scheme().to_lowercase();
    if !ALLOWED_SCHEMES.contains(&scheme.as_str()) {
        let shown = if scheme.is_empty() { "(none)" } else { scheme.as_str() };
        return (false, format!("scheme must be http or https, got '{shown}'"));
    }
    // urlparse(...).hostname strips IPv6 brackets; mirror that for the resolver.
    let host = match parsed.host_str() {
        Some(h) if !h.is_empty() => h.trim_start_matches('[').trim_end_matches(']').to_string(),
        _ => return (false, "URL has no host".to_string()),
    };
    let ips = resolver(&host);
    if ips.is_empty() {
        return (false, "host does not resolve".to_string());
    }
    for ip in ips {
        if let Some(reason) = classify(ip, block_private) {
            return (false, reason);
        }
    }
    (true, "ok".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::Ipv4Addr;

    fn r(ip: &str) -> impl Fn(&str) -> Vec<IpAddr> + '_ {
        let ip = ip.to_string();
        move |_| vec![ip.parse().unwrap()]
    }

    #[test]
    fn rejects_non_http_scheme_and_empty() {
        assert!(!check_outbound_url("", false).0);
        assert!(!check_outbound_url_with("file:///etc/passwd", false, r("1.1.1.1")).0);
        assert!(!check_outbound_url_with("ftp://h/x", false, r("1.1.1.1")).0);
    }

    #[test]
    fn always_blocks_link_local_metadata() {
        // 169.254.169.254 (cloud metadata) is rejected even with block_private=false.
        let (ok, reason) = check_outbound_url_with("http://meta/", false, r("169.254.169.254"));
        assert!(!ok);
        assert!(reason.contains("link-local"));
        // IPv4-mapped form too.
        assert!(!check_outbound_url_with("http://meta/", false, r("::ffff:169.254.169.254")).0);
    }

    #[test]
    fn public_allowed_private_gated() {
        assert!(check_outbound_url_with("http://api/", false, r("1.1.1.1")).0);
        // private is allowed by default (local-first) ...
        assert!(check_outbound_url_with("http://lan/", false, r("192.168.1.5")).0);
        // ... but rejected when block_private is set.
        assert!(!check_outbound_url_with("http://lan/", true, r("192.168.1.5")).0);
        assert!(!check_outbound_url_with("http://lo/", true, r("127.0.0.1")).0);
        let _ = Ipv4Addr::LOCALHOST;
    }
}
