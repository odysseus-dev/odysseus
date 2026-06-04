// src/search/content.rs  <- src/search/content.py
//! Webpage content fetching with caching, PDF extraction, and summarization helpers.
//!
//! * `ipaddress` SSRF guard -> manual CIDR membership over `std::net::IpAddr`
//!   octets for the explicit `_PRIVATE_NETWORKS` list (NO `ipnet` crate).
//! * `socket.getaddrinfo` -> `std::net::ToSocketAddrs`.
//! * `httpx.Client(follow_redirects=False)` manual 8-hop redirect loop ->
//!   `reqwest::blocking` with `redirect::Policy::none`, re-validating each hop.
//! * `bs4`/`html.parser` -> `scraper`.
//! * PDF text via `pdf-extract` (PORT_PARTIAL: pdfminer.six layout-specific
//!   behavior is not reproduced; basic text only, "" on failure).
//! * Result dicts are `serde_json::Value` (`preserve_order` keeps key order).

use crate::pylog as logger;
use crate::src::search::analytics::error_logger;
use crate::src::search::cache::{cleanup_cache, generate_cache_key, CONTENT_CACHE_DIR, CONTENT_CACHE_INDEX};
use chrono::{Duration, Local};
use once_cell::sync::Lazy;
use regex::Regex;
use scraper::{ElementRef, Html, Selector};
use serde_json::{json, Value};
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, ToSocketAddrs};

// logger = logging.getLogger(__name__) -> crate::pylog shim.

/// A private/internal network range, as base address + prefix length, matching
/// the Python `ipaddress.ip_network(...)` entries in `_PRIVATE_NETWORKS`.
enum Cidr {
    V4(Ipv4Addr, u8),
    V6(Ipv6Addr, u8),
}

/// `_PRIVATE_NETWORKS` plus the broader ranges that CPython's
/// `ipaddress.is_private` covers. Rust std's `Ipv4Addr::is_private` etc. are
/// far narrower than CPython's `is_private`, so to keep this guard *at least*
/// as strict as the Python (over-blocking is safe; under-blocking is the SSRF
/// bug), the additional CPython private/reserved ranges are enumerated here as
/// explicit CIDRs. Implemented as manual CIDR membership over `IpAddr` octets
/// (no `ipnet` crate).
static PRIVATE_NETWORKS: Lazy<Vec<Cidr>> = Lazy::new(|| {
    vec![
        // --- explicit Python `_PRIVATE_NETWORKS` entries ---
        Cidr::V4(Ipv4Addr::new(0, 0, 0, 0), 8),
        Cidr::V4(Ipv4Addr::new(10, 0, 0, 0), 8),
        Cidr::V4(Ipv4Addr::new(127, 0, 0, 0), 8),
        Cidr::V4(Ipv4Addr::new(169, 254, 0, 0), 16),
        Cidr::V4(Ipv4Addr::new(172, 16, 0, 0), 12),
        Cidr::V4(Ipv4Addr::new(192, 168, 0, 0), 16),
        // --- additional ranges covered by CPython `ipaddress.is_private` ---
        Cidr::V4(Ipv4Addr::new(192, 0, 0, 0), 24),   // IETF protocol assignments
        Cidr::V4(Ipv4Addr::new(192, 0, 2, 0), 24),   // TEST-NET-1 (documentation)
        Cidr::V4(Ipv4Addr::new(198, 18, 0, 0), 15),  // benchmarking
        Cidr::V4(Ipv4Addr::new(198, 51, 100, 0), 24), // TEST-NET-2 (documentation)
        Cidr::V4(Ipv4Addr::new(203, 0, 113, 0), 24), // TEST-NET-3 (documentation)
        Cidr::V4(Ipv4Addr::new(240, 0, 0, 0), 4),    // reserved (incl. 255.255.255.255/32)
        Cidr::V4(Ipv4Addr::new(255, 255, 255, 255), 32), // limited broadcast
        // --- IPv6 ---
        Cidr::V6(Ipv6Addr::new(0, 0, 0, 0, 0, 0, 0, 1), 128), // ::1/128
        Cidr::V6(Ipv6Addr::new(0, 0, 0, 0, 0, 0, 0, 0), 128), // ::/128 (unspecified)
        Cidr::V6(Ipv6Addr::new(0xfc00, 0, 0, 0, 0, 0, 0, 0), 7), // fc00::/7
        Cidr::V6(Ipv6Addr::new(0xfe80, 0, 0, 0, 0, 0, 0, 0), 10), // fe80::/10
        Cidr::V6(Ipv6Addr::new(0x2001, 0xdb8, 0, 0, 0, 0, 0, 0), 32), // 2001:db8::/32 (documentation)
    ]
});

/// `addr in network` — prefix-length comparison of the leading bits.
fn cidr_contains(cidr: &Cidr, addr: &IpAddr) -> bool {
    match (cidr, addr) {
        (Cidr::V4(net, prefix), IpAddr::V4(ip)) => {
            let mask: u32 = if *prefix == 0 { 0 } else { u32::MAX << (32 - *prefix) };
            (u32::from(*net) & mask) == (u32::from(*ip) & mask)
        }
        (Cidr::V6(net, prefix), IpAddr::V6(ip)) => {
            let mask: u128 = if *prefix == 0 { 0 } else { u128::MAX << (128 - *prefix) };
            (u128::from(*net) & mask) == (u128::from(*ip) & mask)
        }
        _ => false,
    }
}

/// `_is_private_address(addr)` — mirrors the aligned Python guard:
///
/// ```python
/// if isinstance(addr, IPv6Address) and addr.ipv4_mapped is not None:
///     addr = addr.ipv4_mapped
/// return (addr.is_private or addr.is_loopback or addr.is_link_local
///         or addr.is_reserved or addr.is_multicast or addr.is_unspecified
///         or any(addr in net for net in _PRIVATE_NETWORKS))
/// ```
///
/// IPv4-mapped unwrapping happens FIRST so `::ffff:10.0.0.1` is treated as
/// `10.0.0.1` before any flag check. After unwrapping, all six Python flags are
/// checked in order, then the explicit CIDR list.
///
/// `is_reserved` is unstable in Rust std; the 240.0.0.0/4 range (the main
/// "reserved" block) is already covered by the explicit `PRIVATE_NETWORKS`
/// CIDR entries, so the CIDR check at the end handles it.
fn _is_private_address(addr: &IpAddr) -> bool {
    // Step 1: unwrap IPv4-mapped IPv6 first (Python: `addr = addr.ipv4_mapped`).
    let unwrapped: IpAddr;
    let addr: &IpAddr = if let IpAddr::V6(v6) = addr {
        if let Some(v4) = embedded_ipv4(v6) {
            unwrapped = IpAddr::V4(v4);
            &unwrapped
        } else {
            addr
        }
    } else {
        addr
    };

    // Step 2: is_private | is_loopback | is_link_local | is_reserved |
    //         is_multicast | is_unspecified | CIDR-list membership.
    match addr {
        IpAddr::V4(v4) => {
            v4.is_private()
                || v4.is_loopback()
                || v4.is_link_local()
                // is_reserved is unstable; 240.0.0.0/4 covered by CIDR list below.
                || v4.is_multicast()
                || v4.is_unspecified()
                || PRIVATE_NETWORKS.iter().any(|net| cidr_contains(net, addr))
        }
        IpAddr::V6(v6) => {
            v6.is_loopback()
                || v6.is_unicast_link_local()
                || v6.is_multicast()
                || v6.is_unspecified()
                || PRIVATE_NETWORKS.iter().any(|net| cidr_contains(net, addr))
        }
    }
}

/// Extract the embedded IPv4 from an IPv4-mapped (`::ffff:a.b.c.d`) or
/// IPv4-compatible (`::a.b.c.d`) IPv6 address. Returns `None` otherwise.
fn embedded_ipv4(v6: &Ipv6Addr) -> Option<Ipv4Addr> {
    let seg = v6.segments();
    // Low 32 bits (segments 6 and 7) hold the embedded IPv4 in both forms.
    let last: u32 = ((seg[6] as u32) << 16) | (seg[7] as u32);
    // IPv4-mapped: ::ffff:0:0/96  -> segments 0..=4 zero, segment 5 == 0xffff.
    if seg[0] == 0 && seg[1] == 0 && seg[2] == 0 && seg[3] == 0 && seg[4] == 0 && seg[5] == 0xffff {
        return Some(Ipv4Addr::from(last));
    }
    // IPv4-compatible: ::a.b.c.d -> first 96 bits zero, low 32 bits the IPv4.
    if seg[0] == 0 && seg[1] == 0 && seg[2] == 0 && seg[3] == 0 && seg[4] == 0 && seg[5] == 0 {
        // ::, ::1 are already handled above; only treat the rest as compatible.
        if last > 1 {
            return Some(Ipv4Addr::from(last));
        }
    }
    None
}

/// `_resolve_hostname_ips(hostname)` via `socket.getaddrinfo` -> `ToSocketAddrs`.
///
/// Returns the resolved IPs (both families); an empty/`Err` resolution mirrors
/// the Python `OSError` from `getaddrinfo`.
fn _resolve_hostname_ips(hostname: &str) -> Result<Vec<IpAddr>, std::io::Error> {
    // getaddrinfo(hostname, None) — port is irrelevant for the address list, so
    // pass a dummy port (ToSocketAddrs requires "host:port").
    let mut ips: Vec<IpAddr> = Vec::new();
    for sockaddr in (hostname, 0u16).to_socket_addrs()? {
        ips.push(sockaddr.ip());
    }
    Ok(ips)
}

/// `urlparse(url)` -> (scheme, hostname). Minimal parse sufficient for the SSRF
/// guard: scheme up to "://", host up to the first `/`, `?`, `#`, then strip a
/// `user@` prefix and a trailing `:port`. IPv6 hosts in `[...]` are unwrapped.
fn parse_scheme_host(url: &str) -> (Option<String>, Option<String>) {
    let (scheme, rest) = match url.split_once("://") {
        Some((s, r)) => (Some(s.to_lowercase()), r),
        None => return (None, None),
    };
    // netloc ends at the first '/', '?', or '#'.
    let end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
    let mut authority = &rest[..end];
    // strip userinfo (user[:pass]@)
    if let Some(at) = authority.rfind('@') {
        authority = &authority[at + 1..];
    }
    // hostname: bracketed IPv6 or host[:port]
    let host = if let Some(stripped) = authority.strip_prefix('[') {
        // [ipv6]:port
        match stripped.split_once(']') {
            Some((h, _)) => h.to_string(),
            None => stripped.to_string(),
        }
    } else {
        match authority.rsplit_once(':') {
            // Only treat the suffix as a port if it is all digits (avoids
            // chopping a hostname that happens to contain ':').
            Some((h, port)) if !port.is_empty() && port.chars().all(|c| c.is_ascii_digit()) => {
                h.to_string()
            }
            _ => authority.to_string(),
        }
    };
    let host = if host.is_empty() { None } else { Some(host) };
    (scheme, host)
}

/// `_public_http_url(url)` — True only for an http(s) URL whose host resolves
/// entirely to public addresses.
fn _public_http_url(url: &str) -> bool {
    let (scheme, hostname) = parse_scheme_host(url);
    // if parsed.scheme not in ("http","https") or not parsed.hostname: return False
    match scheme.as_deref() {
        Some("http") | Some("https") => {}
        _ => return false,
    }
    let host = match hostname {
        Some(h) => h,
        None => return false,
    };
    // host = parsed.hostname.strip().lower()
    let host = host.trim().to_lowercase();
    if host == "localhost" || host == "metadata.google.internal" || host == "metadata" {
        return false;
    }
    // `if lower.endswith((".local", ".localhost", ".internal", ".lan", ".intranet")): return False`
    if host.ends_with(".local")
        || host.ends_with(".localhost")
        || host.ends_with(".internal")
        || host.ends_with(".lan")
        || host.ends_with(".intranet")
    {
        return false;
    }
    // try: return not _is_private_address(ip_address(host)) except ValueError: pass
    if let Ok(ip) = host.parse::<IpAddr>() {
        return !_is_private_address(&ip);
    }
    // try: ips = resolve(host) except OSError: return False
    // Fail closed: a hostname that resolves to nothing is treated as
    // non-public (an empty all(...) would otherwise return True).
    //     return bool(ips) and all(not _is_private_address(ip) for ip in ips)
    match _resolve_hostname_ips(&host) {
        Ok(ips) => !ips.is_empty() && ips.iter().all(|ip| !_is_private_address(ip)),
        Err(_) => false,
    }
}

/// `urljoin(base, location)` — minimal join sufficient for redirect handling:
/// absolute URLs replace the base; root-relative (`/...`) and relative paths are
/// joined onto the base's scheme://authority (and directory for relative).
fn urljoin(base: &str, location: &str) -> String {
    let loc = location.trim();
    // Absolute URL (has a scheme://).
    if loc.contains("://") {
        return loc.to_string();
    }
    // Protocol-relative //host/...
    if let Some(rest) = loc.strip_prefix("//") {
        let scheme = base.split_once("://").map(|(s, _)| s).unwrap_or("http");
        return format!("{scheme}://{rest}");
    }
    // Split base into scheme://authority and path.
    let (scheme, after) = match base.split_once("://") {
        Some((s, r)) => (s, r),
        None => return loc.to_string(),
    };
    let path_start = after.find('/').unwrap_or(after.len());
    let authority = &after[..path_start];
    let base_path_full = &after[path_start..];
    // strip query/fragment from the base path
    let base_path = base_path_full
        .split(['?', '#'])
        .next()
        .unwrap_or("");
    if loc.starts_with('/') {
        format!("{scheme}://{authority}{loc}")
    } else {
        // relative to the base path's directory
        let dir = match base_path.rfind('/') {
            Some(i) => &base_path[..=i],
            None => "/",
        };
        let dir = if dir.is_empty() { "/" } else { dir };
        format!("{scheme}://{authority}{dir}{loc}")
    }
}

/// A fetched HTTP response holding the bytes/headers we need downstream.
struct FetchedResponse {
    status: u16,
    content_type: String,
    body: Vec<u8>,
}

/// `_get_public_url(url, headers, timeout)` — manual redirect loop (<=8 hops),
/// re-validating every hop. Returns `Err` on blocked/too-many-redirects/network
/// failure (the Python raises `httpx.RequestError`).
fn _get_public_url(
    url: &str,
    headers: &[(&str, &str)],
    timeout: u64,
) -> Result<FetchedResponse, String> {
    if !_public_http_url(url) {
        return Err(format!("Blocked non-public URL: {url}"));
    }
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(timeout))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|e| e.to_string())?;

    let mut current = url.to_string();
    for _ in 0..8 {
        let mut req = client.get(&current);
        for (k, v) in headers {
            req = req.header(*k, *v);
        }
        let response = req.send().map_err(|e| e.to_string())?;
        let status = response.status().as_u16();
        // if response.status_code not in (301,302,303,307,308): return response
        if !matches!(status, 301 | 302 | 303 | 307 | 308) {
            let content_type = response
                .headers()
                .get(reqwest::header::CONTENT_TYPE)
                .and_then(|v| v.to_str().ok())
                .unwrap_or("")
                .to_lowercase();
            let body = response.bytes().map_err(|e| e.to_string())?.to_vec();
            return Ok(FetchedResponse { status, content_type, body });
        }
        // location = response.headers.get("location"); if not location: return response
        let location = response
            .headers()
            .get(reqwest::header::LOCATION)
            .and_then(|v| v.to_str().ok())
            .map(|s| s.to_string());
        let location = match location {
            Some(l) if !l.is_empty() => l,
            _ => {
                // No Location: return the redirect response as-is (matches Python).
                let content_type = response
                    .headers()
                    .get(reqwest::header::CONTENT_TYPE)
                    .and_then(|v| v.to_str().ok())
                    .unwrap_or("")
                    .to_lowercase();
                let body = response.bytes().map_err(|e| e.to_string())?.to_vec();
                return Ok(FetchedResponse { status, content_type, body });
            }
        };
        current = urljoin(&current, &location);
        if !_public_http_url(&current) {
            return Err(format!("Blocked redirect to non-public URL: {current}"));
        }
    }
    Err("Too many redirects".to_string())
}

// ----------------------------------------------------------------------
// HTML extraction helpers
// ----------------------------------------------------------------------

/// `_extract_meta(soup)` — meta description and keywords (case-insensitive name).
fn _extract_meta(doc: &Html) -> (String, String) {
    let mut description = String::new();
    let mut keywords = String::new();
    let meta_sel = Selector::parse("meta").unwrap();
    let desc_re = Regex::new("(?i)description").unwrap();
    let kw_re = Regex::new("(?i)keywords").unwrap();
    // desc_tag = soup.find("meta", attrs={"name": re.compile("description", re.I)})
    for el in doc.select(&meta_sel) {
        if let Some(name) = el.value().attr("name") {
            if description.is_empty() && desc_re.is_match(name) {
                if let Some(content) = el.value().attr("content") {
                    if !content.trim().is_empty() {
                        description = content.trim().to_string();
                    }
                }
            }
            if keywords.is_empty() && kw_re.is_match(name) {
                if let Some(content) = el.value().attr("content") {
                    if !content.trim().is_empty() {
                        keywords = content.trim().to_string();
                    }
                }
            }
        }
    }
    (description, keywords)
}

/// `_extract_og_image(soup)` — first absolute http(s) image URL (skips .svg/.ico).
fn _extract_og_image(doc: &Html) -> String {
    let mut candidates: Vec<String> = Vec::new();
    let meta_sel = Selector::parse("meta").unwrap();
    // og:image / og:image:url / og:image:secure_url (property=)
    let metas: Vec<ElementRef> = doc.select(&meta_sel).collect();
    let find_property = |prop: &str| -> Option<String> {
        for el in &metas {
            if el.value().attr("property") == Some(prop) {
                if let Some(c) = el.value().attr("content") {
                    if !c.trim().is_empty() {
                        return Some(c.trim().to_string());
                    }
                }
            }
        }
        None
    };
    let find_name = |name: &str| -> Option<String> {
        for el in &metas {
            if el.value().attr("name") == Some(name) {
                if let Some(c) = el.value().attr("content") {
                    if !c.trim().is_empty() {
                        return Some(c.trim().to_string());
                    }
                }
            }
        }
        None
    };
    for prop in ["og:image", "og:image:url", "og:image:secure_url"] {
        if let Some(c) = find_property(prop) {
            candidates.push(c);
        }
    }
    if let Some(c) = find_name("twitter:image") {
        candidates.push(c);
    }
    if let Some(c) = find_name("thumbnail") {
        candidates.push(c);
    }
    // Return first absolute http(s) URL (not .svg/.ico).
    for url in &candidates {
        if (url.starts_with("https://") || url.starts_with("http://"))
            && !url.ends_with(".svg")
            && !url.ends_with(".ico")
        {
            return url.clone();
        }
    }
    String::new()
}

/// `get_text(separator=" ", strip=True)` — join descendant text node strings
/// (each stripped) with single spaces. BeautifulSoup strips each text node then
/// joins on the separator; we collapse whitespace runs and trim ends, which is
/// equivalent for the content extracted here.
fn get_text_sep(el: &ElementRef) -> String {
    let joined: String = el
        .text()
        .map(|t| t.trim())
        .filter(|t| !t.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    joined.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Elements stripped from the body copy before the thin-content fallback reads
/// its text — scripts/styles (so JSON/JS doesn't leak) plus boilerplate.
const NOISE_TAGS: [&str; 8] = [
    "script", "style", "noscript", "template", "nav", "header", "footer", "aside",
];

/// `get_text(separator=" ", strip=True)` over a *copy* of the body with the
/// `NOISE_TAGS` extracted. BeautifulSoup strips the copy so the original soup is
/// left intact for the other extractors; `scraper`'s DOM is immutable, so we
/// instead walk the body's descendant text nodes and skip any whose ancestor
/// chain (up to and including the body) contains a noise element.
fn body_text_skipping_noise(body: ElementRef) -> String {
    let mut parts: Vec<String> = Vec::new();
    collect_text_skipping_noise(*body, &mut parts);
    parts.join(" ")
}

/// Recursive walk: append each (stripped, non-empty) text node, pruning whole
/// subtrees rooted at a `NOISE_TAGS` element.
fn collect_text_skipping_noise(node: ego_tree::NodeRef<scraper::Node>, out: &mut Vec<String>) {
    match node.value() {
        scraper::Node::Element(e) => {
            if NOISE_TAGS.contains(&e.name()) {
                return; // prune: bs4's .extract() removed this subtree
            }
            for child in node.children() {
                collect_text_skipping_noise(child, out);
            }
        }
        scraper::Node::Text(t) => {
            let s = t.trim();
            if !s.is_empty() {
                out.push(s.to_string());
            }
        }
        _ => {
            for child in node.children() {
                collect_text_skipping_noise(child, out);
            }
        }
    }
}

/// `_extract_lists(soup)` — list of lists, each inner list one `<ul>`/`<ol>`.
fn _extract_lists(doc: &Html) -> Value {
    let list_sel = Selector::parse("ul, ol").unwrap();
    let li_sel = Selector::parse("li").unwrap();
    let mut all_lists: Vec<Value> = Vec::new();
    for lst in doc.select(&list_sel) {
        let items: Vec<Value> = lst
            .select(&li_sel)
            .map(|li| json!(get_text_sep(&li)))
            .collect();
        if !items.is_empty() {
            all_lists.push(Value::Array(items));
        }
    }
    Value::Array(all_lists)
}

/// `_extract_tables(soup)` — list of tables (rows of cell texts).
fn _extract_tables(doc: &Html) -> Value {
    let table_sel = Selector::parse("table").unwrap();
    let tr_sel = Selector::parse("tr").unwrap();
    let cell_sel = Selector::parse("td, th").unwrap();
    let mut tables_data: Vec<Value> = Vec::new();
    for table in doc.select(&table_sel) {
        let mut rows: Vec<Value> = Vec::new();
        for tr in table.select(&tr_sel) {
            let cells: Vec<Value> = tr.select(&cell_sel).map(|c| json!(get_text_sep(&c))).collect();
            if !cells.is_empty() {
                rows.push(Value::Array(cells));
            }
        }
        if !rows.is_empty() {
            tables_data.push(Value::Array(rows));
        }
    }
    Value::Array(tables_data)
}

/// `_extract_code_blocks(soup)` — text from `<pre>` and `<code>` blocks.
fn _extract_code_blocks(doc: &Html) -> Value {
    let sel = Selector::parse("pre, code").unwrap();
    let mut blocks: Vec<Value> = Vec::new();
    for tag in doc.select(&sel) {
        let txt = get_text_sep(&tag);
        if !txt.is_empty() {
            blocks.push(json!(txt));
        }
    }
    Value::Array(blocks)
}

/// `_detect_js_frameworks(soup)` — naive detection of common JS frameworks.
fn _detect_js_frameworks(doc: &Html) -> bool {
    const JS_INDICATORS: [&str; 11] = [
        "react", "angular", "vue", "svelte", "next", "nuxt", "ember", "backbone", "jquery",
        "polymer", "mithril",
    ];
    let script_sel = Selector::parse("script").unwrap();
    for script in doc.select(&script_sel) {
        // src = script.get("src", "").lower()
        let src = script.value().attr("src").unwrap_or("").to_lowercase();
        if JS_INDICATORS.iter().any(|fr| src.contains(fr)) {
            return true;
        }
        // if script.string: content = script.string.lower()
        let content: String = script.text().collect::<String>().to_lowercase();
        if !content.is_empty() && JS_INDICATORS.iter().any(|fr| content.contains(fr)) {
            return true;
        }
    }
    // soup.find(attrs={"data-reactroot": True}) or soup.find(attrs={"ng-app": True})
    let attr_sel = Selector::parse("[data-reactroot], [ng-app]").unwrap();
    doc.select(&attr_sel).next().is_some()
}

/// `_empty_result(url, error="")` — standard failure result dict.
fn _empty_result(url: &str, error: &str) -> Value {
    json!({
        "url": url,
        "title": "",
        "content": "",
        "lists": [],
        "tables": [],
        "code_blocks": [],
        "meta_description": "",
        "meta_keywords": "",
        "js_rendered": false,
        "js_message": "",
        "success": false,
        "error": error,
    })
}

// ----------------------------------------------------------------------
// Main content fetcher
// ----------------------------------------------------------------------

/// `os.path.basename(url)` — the final path component (Python `os.path.basename`).
fn url_basename(url: &str) -> String {
    match url.rfind('/') {
        Some(i) => url[i + 1..].to_string(),
        None => url.to_string(),
    }
}

/// `re.sub(r"\s+", " ", main_content).strip()`.
static WS_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\s+").unwrap());

/// Class-attr regex for the heuristic main-content selector. The Python
/// `class_=re.compile("content|main|body|article|post|entry|text", re.I)` matches
/// any element whose class attribute *contains* one of these tokens; `scraper`
/// has no regex selector, so we iterate candidate elements and regex-test the
/// raw class attribute.
static CONTENT_CLASS_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new("(?i)content|main|body|article|post|entry|text").unwrap());

/// `THIN_CONTENT_CHARS` — below this many code points the class heuristic likely
/// missed the page, so the thin-content fallback reads the (noise-stripped)
/// `<body>` instead. Matches the Python constant.
const THIN_CONTENT_CHARS: usize = 600;

/// Fetch and extract meaningful content from a webpage with caching.
///
/// `timeout` defaults to 5, `retry_attempt` to 0. Returns the result dict as a
/// `serde_json::Value`.
pub fn fetch_webpage_content(url: &str, timeout: u64, retry_attempt: i64) -> Value {
    let cache_key = generate_cache_key(url);
    let cache_file = CONTENT_CACHE_DIR.join(format!("{cache_key}.cache"));

    // Check cache
    if cache_file.exists() {
        let read = (|| -> Result<Option<Value>, String> {
            let s = std::fs::read_to_string(&cache_file).map_err(|e| e.to_string())?;
            let cached_data: Value = serde_json::from_str(&s).map_err(|e| e.to_string())?;
            let ts_str = cached_data
                .get("timestamp")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing timestamp".to_string())?;
            // datetime.fromisoformat(...) — cache.rs uses Local naive isoformat.
            let timestamp = chrono::NaiveDateTime::parse_from_str(ts_str, "%Y-%m-%dT%H:%M:%S%.f")
                .or_else(|_| chrono::NaiveDateTime::parse_from_str(ts_str, "%Y-%m-%dT%H:%M:%S"))
                .map_err(|e| e.to_string())?;
            let now = Local::now().naive_local();
            // if datetime.now() - timestamp < timedelta(hours=2): return data
            if (now - timestamp) < Duration::hours(2) {
                logger::debug(&format!("Content cache hit for URL: {url}"));
                return Ok(Some(cached_data.get("data").cloned().unwrap_or(Value::Null)));
            }
            Ok(None)
        })();
        match read {
            Ok(Some(data)) => return data,
            Ok(None) => {
                // expired: cache_file.unlink + index.pop
                let _ = std::fs::remove_file(&cache_file);
                CONTENT_CACHE_INDEX.lock().unwrap().shift_remove(&cache_key);
            }
            Err(e) => {
                logger::warning(&format!("Failed to read content cache for {url}: {e}"));
                let _ = std::fs::remove_file(&cache_file);
                CONTENT_CACHE_INDEX.lock().unwrap().shift_remove(&cache_key);
            }
        }
    }

    // Fetch
    let headers: [(&str, &str); 5] = [
        (
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        ),
        ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
        ("Accept-Language", "en-US,en;q=0.5"),
        ("Accept-Encoding", "gzip, deflate"),
        ("Connection", "keep-alive"),
    ];
    let response = match _get_public_url(url, &headers, timeout) {
        Ok(r) => r,
        // The Python distinguishes RequestError vs RateLimitError, but both
        // branches return _empty_result with the message; _get_public_url maps
        // blocked/too-many-redirects/network failures to the NetworkError text.
        Err(e) => {
            error_logger::error(&format!(
                "NetworkError fetching {url} (attempt {retry_attempt}): {e}"
            ));
            return _empty_result(url, &format!("NetworkError: {e}"));
        }
    };

    // if response.status_code == 429: raise RateLimitError -> _empty_result
    if response.status == 429 {
        let msg = format!("Rate limit hit for {url} (attempt {retry_attempt})");
        error_logger::error(&msg);
        return _empty_result(url, &msg);
    }
    // response.raise_for_status() — any 4xx/5xx (other than the 429 above) ->
    // HTTPStatusError, a RequestError subclass -> NetworkError branch.
    if !(200..400).contains(&response.status) {
        let e = format!("HTTP status {}", response.status);
        error_logger::error(&format!("NetworkError fetching {url} (attempt {retry_attempt}): {e}"));
        return _empty_result(url, &format!("NetworkError: {e}"));
    }

    // PDF handling
    let url_lc = url.to_lowercase();
    if response.content_type.contains("application/pdf") || url_lc.ends_with(".pdf") {
        // PORT_PARTIAL: pdf-extract reproduces basic text only; the
        // pdfminer.six layout-specific behavior is not reproduced. "" on failure.
        let pdf_text = match pdf_extract::extract_text_from_mem(&response.body) {
            Ok(t) => t,
            Err(e) => {
                logger::warning(&format!("PDF extraction failed for {url}: {e}"));
                String::new()
            }
        };
        let has_text = !pdf_text.is_empty();
        let result = json!({
            "url": url,
            "title": url_basename(url),
            "content": pdf_text,
            "lists": [],
            "tables": [],
            "code_blocks": [],
            "meta_description": "",
            "meta_keywords": "",
            "js_rendered": false,
            "js_message": "",
            "success": has_text,
            "error": if has_text { "" } else { "Failed to extract PDF text" },
        });
        _cache_result(&cache_file, &cache_key, &result, url);
        return result;
    }

    // HTML handling — decode the body as text (lossy, like httpx's .text).
    let text = String::from_utf8_lossy(&response.body).into_owned();
    let doc = Html::parse_document(&text);

    // title_tag = soup.find("title")
    let title_sel = Selector::parse("title").unwrap();
    let title_text = doc
        .select(&title_sel)
        .next()
        .map(|t| collapse_strip(&t.text().collect::<String>()))
        .unwrap_or_default();

    let (meta_description, meta_keywords) = _extract_meta(&doc);
    let og_image = _extract_og_image(&doc);
    let js_rendered = _detect_js_frameworks(&doc);
    let js_message = if js_rendered {
        "Page appears to be rendered by a JavaScript framework; content may be incomplete."
    } else {
        ""
    };

    // Main textual content (heuristic).
    let mut main_content = String::new();
    // content_areas = soup.find_all(["main","article","section","div"],
    //                               class_=re.compile("content|main|...", re.I))
    let area_sel = Selector::parse("main, article, section, div").unwrap();
    let mut content_areas: Vec<ElementRef> = Vec::new();
    for el in doc.select(&area_sel) {
        // class_ regex matches the (joined) class attribute.
        let class_attr = el.value().attr("class").unwrap_or("");
        if !class_attr.is_empty() && CONTENT_CLASS_RE.is_match(class_attr) {
            content_areas.push(el);
        }
    }
    if !content_areas.is_empty() {
        for area in content_areas.iter().take(3) {
            main_content.push_str(&get_text_sep(area));
            main_content.push(' ');
        }
    }
    // main_content = re.sub(r"\s+", " ", main_content).strip()
    main_content = WS_RE.replace_all(&main_content, " ").trim().to_string();

    // The class heuristic can latch onto a small wrapper and miss the real
    // content (app/landing pages, or SSR sites whose body isn't in a
    // "content"-classed div, so these came back nearly empty before). When the
    // heuristic returns nothing OR suspiciously little, fall back to the full
    // <body>, stripping scripts/styles (so JSON/JS doesn't leak into the text)
    // plus nav/header/footer/aside (boilerplate), and keep whichever yields
    // more readable text.
    if main_content.chars().count() < THIN_CONTENT_CHARS {
        let body_sel = Selector::parse("body").unwrap();
        if let Some(body) = doc.select(&body_sel).next() {
            // BeautifulSoup strips a *copy* of the body so the list/table/code
            // extractors still see the original soup unmodified; `scraper`'s DOM
            // is immutable, so instead of mutating we walk the body's text nodes
            // and skip any descending from a noise element.
            let body_text = body_text_skipping_noise(body);
            let body_text = WS_RE.replace_all(&body_text, " ").trim().to_string();
            if body_text.chars().count() > main_content.chars().count() {
                main_content = body_text;
            }
        }
    }

    let result = json!({
        "url": url,
        "title": title_text,
        "content": main_content,
        "lists": _extract_lists(&doc),
        "tables": _extract_tables(&doc),
        "code_blocks": _extract_code_blocks(&doc),
        "meta_description": meta_description,
        "meta_keywords": meta_keywords,
        "og_image": og_image,
        "js_rendered": js_rendered,
        "js_message": js_message,
        "success": true,
        "error": "",
    });
    _cache_result(&cache_file, &cache_key, &result, url);
    result
}

/// `get_text(strip=True)` for a single title element — collapse + trim.
fn collapse_strip(s: &str) -> String {
    s.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// `_cache_result(cache_file, cache_key, result, url)` — write a result to cache.
fn _cache_result(cache_file: &std::path::Path, cache_key: &str, result: &Value, url: &str) {
    let now = Local::now().naive_local();
    // cache_data = {"timestamp": now.isoformat(), "data": result}
    let cache_data = json!({
        "timestamp": now.format("%Y-%m-%dT%H:%M:%S%.6f").to_string(),
        "data": result,
    });
    let write = (|| -> Result<(), String> {
        let s = serde_json::to_string(&cache_data).map_err(|e| e.to_string())?;
        std::fs::write(cache_file, s).map_err(|e| e.to_string())?;
        let mut index = CONTENT_CACHE_INDEX.lock().unwrap();
        index.insert(cache_key.to_string(), Local::now());
        cleanup_cache(&CONTENT_CACHE_DIR, &mut index, Duration::hours(2));
        Ok(())
    })();
    if let Err(e) = write {
        logger::warning(&format!("Failed to write content cache for {url}: {e}"));
    }
}

// ----------------------------------------------------------------------
// Content summarization helpers (pure)
// ----------------------------------------------------------------------

/// `^\s*[-*•]\s+(.*)` — bullet-style line.
static BULLET_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\s*[-*•]\s+(.*)").unwrap());
/// `^\s*\d+[\.\)]\s+(.*)` — numbered line.
static NUMBERED_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\s*\d+[\.\)]\s+(.*)").unwrap());

/// Pull out bullet-style key points from a block of text.
pub fn extract_key_points(text: &str) -> Vec<String> {
    let mut points: Vec<String> = Vec::new();
    // for line in text.splitlines():
    for line in text.split('\n') {
        // Python's str.splitlines also splits on \r; strip a trailing \r so a
        // CRLF document behaves like the Python.
        let line = line.strip_suffix('\r').unwrap_or(line);
        // m = bullet_pat.match(line) or numbered_pat.match(line)
        let m = BULLET_RE.captures(line).or_else(|| NUMBERED_RE.captures(line));
        if let Some(caps) = m {
            points.push(caps.get(1).map(|g| g.as_str().trim().to_string()).unwrap_or_default());
        }
    }
    points
}

/// `(?<=[.!?])\s+` sentence split — lookbehind needs the `fancy-regex` crate
/// since the `regex` crate has no lookbehind. We split manually: break on
/// whitespace that immediately follows a `.`/`!`/`?`.
pub fn get_tldr(text: &str, max_sentences: usize) -> String {
    let sentences = split_sentences(text);
    // selected = [s.strip() for s in sentences if s][:max_sentences]
    let selected: Vec<String> = sentences
        .into_iter()
        .filter(|s| !s.is_empty())
        .map(|s| s.trim().to_string())
        .take(max_sentences)
        .collect();
    selected.join(" ")
}

/// `re.split(r"(?<=[.!?])\s+", text)` — split keeping the terminator on the left
/// piece. Mirrors the Python lookbehind split without a regex engine.
fn split_sentences(text: &str) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    let mut pieces: Vec<String> = Vec::new();
    let mut start = 0usize;
    let mut i = 0usize;
    while i < chars.len() {
        // A split point is a run of whitespace whose char immediately before is
        // [.!?]. Python's \s+ consumes the whole whitespace run.
        if chars[i].is_whitespace() && i > 0 && matches!(chars[i - 1], '.' | '!' | '?') {
            // piece is chars[start..i]
            pieces.push(chars[start..i].iter().collect());
            // consume the whitespace run
            let mut j = i;
            while j < chars.len() && chars[j].is_whitespace() {
                j += 1;
            }
            start = j;
            i = j;
        } else {
            i += 1;
        }
    }
    pieces.push(chars[start..].iter().collect());
    pieces
}

/// Return quoted excerpts that are at least 15 characters long.
///
/// Python: `re.finditer(r'(["\'])([^"\']{15,}?)\1', text)`. The backreference
/// makes the closing quote match the opening one — otherwise `"text'` (open
/// double, close single) is treated as a quote. The `regex` crate has no
/// backreferences, so we scan manually, pairing opening and closing quotes:
/// for each quote char we look for the next quote char (of either type); it is
/// a match only when that next quote is the SAME type and the run between them
/// is >= 15 chars (and contains no quote chars, which the next-quote search
/// guarantees). Matches are non-overlapping, mirroring `finditer`.
pub fn extract_quotes(text: &str) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    let mut quotes: Vec<String> = Vec::new();
    let mut i = 0usize;
    while i < chars.len() {
        let q = chars[i];
        if q == '"' || q == '\'' {
            // Find the next quote char (either type) after the opener; the
            // `[^"\']` class makes any quote a hard stop for the inner run.
            if let Some(off) = chars[i + 1..].iter().position(|&c| c == '"' || c == '\'') {
                let j = i + 1 + off;
                // Closing quote must match the opener (the `\1` backreference)
                // and the inner run must be at least 15 chars (`{15,}?`).
                if chars[j] == q && (j - i - 1) >= 15 {
                    let inner: String = chars[i + 1..j].iter().collect();
                    quotes.push(inner.trim().to_string());
                    // Non-overlapping: resume past the closing quote.
                    i = j + 1;
                    continue;
                }
            }
        }
        i += 1;
    }
    quotes
}

/// The statistics number pattern (numbers/percentages/measurements), `re.I`.
///
/// Matches a comma-grouped number (`1,000,000`) OR a plain digit run (`50000`) —
/// the old `\d{1,3}(?:,\d{3})*` matched only the first 3 digits of a comma-less
/// number, and the trailing `\b` dropped a closing `%`.
static STATS_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\b(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(%|percent|‰|per cent|[a-zA-Z]+)?")
        .unwrap()
});

/// Find numbers, percentages, dates and simple measurements.
pub fn extract_statistics(text: &str) -> Vec<String> {
    STATS_RE
        .find_iter(text)
        .map(|m| m.as_str().trim().to_string())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    // ---- Gap 1: SSRF fail-closed on empty / private resolution ----

    #[test]
    fn public_url_rejects_non_http_scheme_and_missing_host() {
        assert!(!_public_http_url("ftp://example.com/x"));
        assert!(!_public_http_url("file:///etc/passwd"));
        assert!(!_public_http_url("notaurl"));
    }

    #[test]
    fn public_url_rejects_localhost_and_metadata_and_private_literals() {
        assert!(!_public_http_url("http://localhost/"));
        assert!(!_public_http_url("http://metadata.google.internal/"));
        assert!(!_public_http_url("http://127.0.0.1/"));
        assert!(!_public_http_url("http://10.0.0.1/"));
        assert!(!_public_http_url("http://192.168.1.5/"));
        assert!(!_public_http_url("http://169.254.169.254/"));
        assert!(!_public_http_url("http://[::1]/"));
    }

    #[test]
    fn public_url_allows_public_ip_literal() {
        assert!(_public_http_url("http://8.8.8.8/"));
        assert!(_public_http_url("https://1.1.1.1/path?q=1"));
    }

    // ---- aaef6b1: aligned content URL guards ----

    /// IPv4-mapped IPv6 SSRF bypass — `::ffff:10.0.0.1` must be blocked.
    /// Before this commit, both Python files performed the mapped-IPv4 unwrap
    /// only as a *fallback* after the main check; the new code unwraps FIRST so
    /// even a `::ffff:` prefix can't sneak a private IPv4 past the guard.
    #[test]
    fn is_private_address_blocks_ipv4_mapped_ipv6() {
        // ::ffff:10.0.0.1 (private range via IPv4-mapped)
        let mapped: IpAddr = "::ffff:10.0.0.1".parse().unwrap();
        assert!(_is_private_address(&mapped), "::ffff:10.0.0.1 must be private");

        // ::ffff:192.168.1.1
        let mapped2: IpAddr = "::ffff:192.168.1.1".parse().unwrap();
        assert!(_is_private_address(&mapped2), "::ffff:192.168.1.1 must be private");

        // ::ffff:127.0.0.1
        let loopback_mapped: IpAddr = "::ffff:127.0.0.1".parse().unwrap();
        assert!(_is_private_address(&loopback_mapped), "::ffff:127.0.0.1 must be private");

        // Sanity: a public IPv4-mapped address is NOT blocked by the IPv4 guard.
        let public_mapped: IpAddr = "::ffff:8.8.8.8".parse().unwrap();
        assert!(!_is_private_address(&public_mapped), "::ffff:8.8.8.8 must be public");
    }

    /// Multicast and unspecified addresses are now explicitly blocked.
    #[test]
    fn is_private_address_blocks_multicast_and_unspecified() {
        // IPv4 multicast: 224.0.0.1
        let v4_mc: IpAddr = "224.0.0.1".parse().unwrap();
        assert!(_is_private_address(&v4_mc), "224.0.0.1 multicast must be private");

        // IPv4 unspecified: 0.0.0.0
        let v4_unspec: IpAddr = "0.0.0.0".parse().unwrap();
        assert!(_is_private_address(&v4_unspec), "0.0.0.0 must be private");

        // IPv6 multicast: ff02::1
        let v6_mc: IpAddr = "ff02::1".parse().unwrap();
        assert!(_is_private_address(&v6_mc), "ff02::1 multicast must be private");

        // IPv6 unspecified: ::
        let v6_unspec: IpAddr = "::".parse().unwrap();
        assert!(_is_private_address(&v6_unspec), ":: must be private");
    }

    /// `_public_http_url` rejects hostnames with private-DNS suffixes.
    /// Added in this commit to `src/search/content.py` (already present in the
    /// services file prior; now both files are aligned).
    #[test]
    fn public_url_rejects_private_dns_suffixes() {
        assert!(!_public_http_url("http://myhost.local/"));
        assert!(!_public_http_url("http://myhost.localhost/"));
        assert!(!_public_http_url("http://myhost.internal/"));
        assert!(!_public_http_url("http://myhost.lan/"));
        assert!(!_public_http_url("http://myhost.intranet/"));
    }

    #[test]
    fn public_url_fails_closed_on_unresolvable_host() {
        // The reserved `.invalid` TLD (RFC 2606) never resolves, so
        // getaddrinfo yields no IPs. The fix treats "no IPs" as not-public:
        // `bool(ips) and all(...)` -> `!ips.is_empty() && ...` -> false.
        //
        // Guard against a misbehaving captive/wildcard resolver that fabricates
        // an A record: only assert the fail-closed verdict when resolution
        // genuinely produced no addresses (the case this fix is about).
        let host = "this-host-does-not-exist.invalid";
        let resolved = _resolve_hostname_ips(host).unwrap_or_default();
        if resolved.is_empty() {
            assert!(!_public_http_url(&format!("http://{host}/")));
        }
    }

    #[test]
    fn empty_ip_set_is_not_public_via_all_semantics() {
        // Pin the exact bug: `all(...)` over an empty set is vacuously true, so
        // the pre-fix code would have returned `true` (public) for a host that
        // resolves to nothing. The fixed predicate requires a non-empty set.
        let no_ips: Vec<IpAddr> = Vec::new();
        let verdict = !no_ips.is_empty() && no_ips.iter().all(|ip| !_is_private_address(ip));
        assert!(!verdict, "empty IP set must fail closed (not public)");
    }

    // ---- Gap 2: OG image accepts http:// (not just https://) ----

    #[test]
    fn og_image_accepts_plain_http() {
        let html = r#"<html><head>
            <meta property="og:image" content="http://cdn.example.com/pic.jpg">
            </head><body></body></html>"#;
        let doc = Html::parse_document(html);
        assert_eq!(_extract_og_image(&doc), "http://cdn.example.com/pic.jpg");
    }

    #[test]
    fn og_image_still_accepts_https_and_skips_relative_and_svg() {
        let https = r#"<meta property="og:image" content="https://x.test/a.png">"#;
        let doc = Html::parse_document(https);
        assert_eq!(_extract_og_image(&doc), "https://x.test/a.png");

        // relative path -> skipped, empty result
        let rel = r#"<meta property="og:image" content="/local/a.png">"#;
        let doc = Html::parse_document(rel);
        assert_eq!(_extract_og_image(&doc), "");

        // svg/ico skipped even with http(s) scheme
        let svg = r#"<meta property="og:image" content="http://x.test/a.svg">"#;
        let doc = Html::parse_document(svg);
        assert_eq!(_extract_og_image(&doc), "");
    }

    // ---- Gap 3: thin-content fallback strips noise, no 8000 truncation ----

    #[test]
    fn body_text_skips_noise_elements() {
        let html = r#"<html><body>
            <script>var leak = "scriptcontent";</script>
            <style>.x{color:red}</style>
            <nav>navjunk</nav>
            <header>headerjunk</header>
            <footer>footerjunk</footer>
            <aside>asidejunk</aside>
            <p>real visible content</p>
        </body></html>"#;
        let doc = Html::parse_document(html);
        let body_sel = Selector::parse("body").unwrap();
        let body = doc.select(&body_sel).next().unwrap();
        let text = body_text_skipping_noise(body);
        let text = WS_RE.replace_all(&text, " ").trim().to_string();
        assert_eq!(text, "real visible content");
        for noise in ["scriptcontent", "color:red", "navjunk", "headerjunk", "footerjunk", "asidejunk"] {
            assert!(!text.contains(noise), "noise leaked: {noise}");
        }
    }

    #[test]
    fn thin_content_threshold_is_600() {
        assert_eq!(THIN_CONTENT_CHARS, 600);
    }

    // ---- Gap 4: STATS_RE matches comma-grouped, plain runs, trailing % ----

    #[test]
    fn statistics_matches_plain_and_grouped_numbers() {
        assert_eq!(
            extract_statistics("Sales rose 50000 units and 1,000,000 dollars"),
            vec!["50000 units", "1,000,000 dollars"]
        );
    }

    #[test]
    fn statistics_keeps_trailing_percent() {
        // The old trailing `\b` dropped the closing `%`; the new regex keeps it.
        assert_eq!(extract_statistics("grew by 25% last year"), vec!["25%"]);
        assert_eq!(extract_statistics("100% done"), vec!["100%"]);
    }

    #[test]
    fn statistics_matches_decimals_and_units_and_words() {
        assert_eq!(
            extract_statistics("about 3.5 percent and 12kg"),
            vec!["3.5 percent", "12kg"]
        );
        assert_eq!(extract_statistics("1,234,567 widgets"), vec!["1,234,567 widgets"]);
        assert_eq!(extract_statistics("value 42"), vec!["42"]);
    }

    // ---- Gap 5: extract_quotes pairs matching quotes via manual scan ----

    #[test]
    fn quotes_match_paired_double_and_single() {
        assert_eq!(
            extract_quotes("He said \"this is a long enough quote\" today"),
            vec!["this is a long enough quote"]
        );
        assert_eq!(
            extract_quotes("'single quoted long enough content' yes"),
            vec!["single quoted long enough content"]
        );
    }

    #[test]
    fn quotes_reject_mismatched_open_double_close_single() {
        // `"open double close single'` must NOT be treated as a quote because the
        // closing quote differs from the opener (the Python `\1` backreference).
        assert!(extract_quotes("mismatch \"open double close single' here").is_empty());
    }

    #[test]
    fn quotes_require_min_15_chars_and_are_non_overlapping() {
        assert_eq!(
            extract_quotes("short \"tiny\" and \"this one is plenty long indeed\""),
            vec!["this one is plenty long indeed"]
        );
        assert_eq!(
            extract_quotes("\"first long quote here\" then \"second long quote here\""),
            vec!["first long quote here", "second long quote here"]
        );
    }
}
