// src/model_discovery.rs  <- src/model_discovery.py
//! vLLM / OpenAI-compatible model discovery across Tailscale (or env-listed)
//! hosts.
//!
//! Faithful port of `src/model_discovery.py`. The Python uses a synchronous
//! `httpx.get` fan-out across a `ThreadPoolExecutor(max_workers=50)`; the Rust
//! port runs the same fan-out on tokio via `reqwest` + `futures_util`'s
//! `buffer_unordered(50)` (documented async swap — this module is `web`-gated and
//! only ever runs inside the async server). Tailscale discovery shells out to the
//! `tailscale` binary via `tokio::process::Command` with a 5s timeout and a 60s
//! TTL cache, exactly like the Python.

use crate::pylog as logger;
use crate::pyos as os;
use crate::pytime as time;
use futures_util::stream::{self, StreamExt};
use once_cell::sync::Lazy;
use serde_json::{json, Value};
use std::sync::Mutex;

/// Cache for discovered hosts. The Python module-level globals `_hosts_cache`
/// (`List[str]`) and `_hosts_cache_time` (`float`) collapse into one mutex-guarded
/// tuple; `_HOSTS_CACHE_TTL = 60` seconds.
static HOSTS_CACHE: Lazy<Mutex<(Vec<String>, f64)>> =
    Lazy::new(|| Mutex::new((Vec::new(), 0.0)));
const HOSTS_CACHE_TTL: f64 = 60.0; // seconds

/// Discover online Tailscale peers, returning their IPv4 addresses.
pub async fn discover_tailscale_hosts() -> Vec<String> {
    let now = time::time();
    {
        let cache = HOSTS_CACHE.lock().unwrap();
        if !cache.0.is_empty() && (now - cache.1) < HOSTS_CACHE_TTL {
            return cache.0.clone();
        }
    }

    let mut hosts: Vec<String> = Vec::new();

    // subprocess.run(["tailscale", "status", "--json"], timeout=5)
    let output = match tokio::time::timeout(
        std::time::Duration::from_secs(5),
        tokio::process::Command::new("tailscale")
            .args(["status", "--json"])
            .output(),
    )
    .await
    {
        Ok(Ok(out)) => out,
        Ok(Err(e)) => {
            // FileNotFoundError -> the binary is missing.
            if e.kind() == std::io::ErrorKind::NotFound {
                logger::debug("tailscale command not found");
            } else {
                logger::warning(&format!("Tailscale discovery failed: {e}"));
            }
            return hosts;
        }
        Err(_) => {
            // subprocess timeout (5s) -> treat as a generic failure.
            logger::warning("Tailscale discovery failed: timed out");
            return hosts;
        }
    };

    // if result.returncode != 0: return hosts
    if !output.status.success() {
        return hosts;
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let data: Value = match serde_json::from_str(&stdout) {
        Ok(v) => v,
        Err(e) => {
            logger::warning(&format!("Tailscale discovery failed: {e}"));
            return hosts;
        }
    };

    // Add self — first IPv4 in data["Self"]["TailscaleIPs"].
    if let Some(self_ips) = data
        .get("Self")
        .and_then(|s| s.get("TailscaleIPs"))
        .and_then(|v| v.as_array())
    {
        for ip in self_ips {
            if let Some(ip) = ip.as_str() {
                if ip.contains('.') {
                    hosts.push(ip.to_string());
                    break;
                }
            }
        }
    }

    // Add online peers (skip funnel-ingress-nodes and android devices).
    if let Some(peers) = data.get("Peer").and_then(|v| v.as_object()) {
        for peer in peers.values() {
            if !peer.get("Online").and_then(|v| v.as_bool()).unwrap_or(false) {
                continue;
            }
            let hostname = peer.get("HostName").and_then(|v| v.as_str()).unwrap_or("");
            if hostname == "funnel-ingress-node" {
                continue;
            }
            let os_name = peer.get("OS").and_then(|v| v.as_str()).unwrap_or("");
            if os_name == "android" {
                continue;
            }
            if let Some(peer_ips) = peer.get("TailscaleIPs").and_then(|v| v.as_array()) {
                for ip in peer_ips {
                    if let Some(ip) = ip.as_str() {
                        if ip.contains('.') {
                            hosts.push(ip.to_string());
                            break;
                        }
                    }
                }
            }
        }
    }

    {
        let mut cache = HOSTS_CACHE.lock().unwrap();
        cache.0 = hosts.clone();
        cache.1 = now;
    }
    logger::info(&format!(
        "Tailscale discovery found {} hosts: {:?}",
        hosts.len(),
        hosts
    ));

    hosts
}

pub struct ModelDiscovery {
    pub default_host: String,
    pub openai_api_key: Option<String>,
    pub openai_compat_path: String,
    /// Custom ports from env vars, merged into the scan list by `discover_models`.
    /// Mirrors the Python `self._extra_ports: set`. `_get_hosts` resets and
    /// repopulates it on each call, so it is interior-mutable behind a `Mutex`
    /// (the Python instance attribute is freely mutated; `&self` here is not).
    extra_ports: Mutex<std::collections::BTreeSet<i64>>,
}

impl ModelDiscovery {
    pub fn new(default_host: &str, openai_api_key: Option<String>) -> Self {
        ModelDiscovery {
            default_host: default_host.to_string(),
            openai_api_key,
            openai_compat_path: "/v1/chat/completions".to_string(),
            extra_ports: Mutex::new(std::collections::BTreeSet::new()),
        }
    }

    /// Append `host` (trimmed) to `out` unless empty or already present.
    /// Mirrors the Python inner `_append_host`.
    fn append_host(out: &mut Vec<String>, host: &str) {
        let host = host.trim();
        if host.is_empty() || out.iter().any(|h| h == host) {
            return;
        }
        out.push(host.to_string());
    }

    /// Add hosts (and any custom ports) from provider-specific env vars.
    /// Mirrors the Python inner `_append_env_hosts`.
    fn append_env_hosts(&self, out: &mut Vec<String>) {
        for env_name in ["OLLAMA_BASE_URL", "OLLAMA_URL", "LM_STUDIO_URL"] {
            let raw = os::getenv(env_name, "");
            let raw = raw.trim();
            if raw.is_empty() {
                continue;
            }
            // urlparse(raw if "://" in raw else "http://" + raw)
            let with_scheme = if raw.contains("://") {
                raw.to_string()
            } else {
                format!("http://{raw}")
            };
            // Python wraps the parse + extraction in try/except, swallowing any
            // error. url::Url::parse already returns Result, so a parse failure
            // is the equivalent no-op.
            if let Ok(parsed) = url::Url::parse(&with_scheme) {
                // parsed.hostname or "" -> host_str() (lowercased for domains by
                // the url crate, matching urlparse().hostname).
                Self::append_host(out, parsed.host_str().unwrap_or(""));
                // parsed.port (explicit port only; None otherwise) -> .port().
                if let Some(port) = parsed.port() {
                    self.extra_ports.lock().unwrap().insert(port as i64);
                }
            }
        }
    }

    /// Get all hosts to scan, using env override, Tailscale, or default.
    pub async fn get_hosts(&self) -> Vec<String> {
        // Reset extra ports at the start of every call (Python: self._extra_ports = set()).
        self.extra_ports.lock().unwrap().clear();

        // Manual override takes priority.
        let extra = os::getenv("LLM_HOSTS", "");
        let extra = extra.trim();
        if !extra.is_empty() {
            let mut hosts: Vec<String> = extra
                .split(',')
                .map(|h| h.trim().to_string())
                .filter(|h| !h.is_empty())
                .collect();
            // Always include the default host too.
            if !hosts.contains(&self.default_host) {
                hosts.insert(0, self.default_host.clone());
            }
            Self::append_host(&mut hosts, "host.docker.internal");
            self.append_env_hosts(&mut hosts);
            return hosts;
        }

        // Try Tailscale discovery.
        let mut ts_hosts = discover_tailscale_hosts().await;
        if !ts_hosts.is_empty() {
            // Ensure default_host is included.
            if !ts_hosts.contains(&self.default_host) {
                ts_hosts.insert(0, self.default_host.clone());
            }
            Self::append_host(&mut ts_hosts, "host.docker.internal");
            self.append_env_hosts(&mut ts_hosts);
            return ts_hosts;
        }

        // Fallback to single host.
        let mut hosts = vec![self.default_host.clone()];
        // Docker desktop/Linux compose maps this to the host machine. That is
        // the common "I started Ollama normally on this computer" case.
        Self::append_host(&mut hosts, "host.docker.internal");
        self.append_env_hosts(&mut hosts);
        hosts
    }

    /// Identify the server software via its native API, independent of port.
    ///
    /// Probes `GET /api/v1/models` with a 1.5s timeout; returns `Some("lmstudio")`
    /// when the response is an LM Studio-shaped model list (first model object has
    /// both `key` and `architecture`). Any error is swallowed -> `None`.
    async fn fingerprint_provider(
        &self,
        client: &reqwest::Client,
        host: &str,
        port: i64,
    ) -> Option<String> {
        let url = format!("http://{host}:{port}/api/v1/models");
        let r = client
            .get(&url)
            .timeout(std::time::Duration::from_millis(1500))
            .send()
            .await
            .ok()?;
        if !r.status().is_success() {
            return None;
        }
        // (r.json() or {}).get("models")
        let data: Value = r.json().await.unwrap_or(Value::Null);
        let models = data.get("models").and_then(|v| v.as_array())?;
        let first = models.first()?.as_object()?;
        if first.contains_key("key") && first.contains_key("architecture") {
            return Some("lmstudio".to_string());
        }
        None
    }

    /// Check a single host:port for models.
    ///
    /// `httpx.get(f"{base}/models", timeout=3)`; any error / non-success status /
    /// empty id list yields `None`.
    async fn check_port(&self, client: &reqwest::Client, host: &str, port: i64) -> Option<Value> {
        let base = format!("http://{host}:{port}/v1");
        let url = format!("{base}/models");
        let r = client
            .get(&url)
            .timeout(std::time::Duration::from_secs(3))
            .send()
            .await
            .ok()?;
        // r.is_success — httpx considers 2xx successful.
        if !r.status().is_success() {
            return None;
        }
        let data: Value = r.json().await.unwrap_or(Value::Null);
        // ids = [m["id"] for m in (data["data"] or []) if m["id"]]
        let mut ids: Vec<String> = Vec::new();
        if let Some(items) = data.get("data").and_then(|v| v.as_array()) {
            for m in items {
                if let Some(id) = m.get("id").and_then(|v| v.as_str()) {
                    if !id.is_empty() {
                        ids.push(id.to_string());
                    }
                }
            }
        }
        if ids.is_empty() {
            return None;
        }
        // models_display: lstrip ALL leading '/'.
        let models_display: Vec<String> = ids
            .iter()
            .map(|i| i.trim_start_matches('/').to_string())
            .collect();
        // provider: native-API fingerprint (None unless LM Studio).
        let provider = self.fingerprint_provider(client, host, port).await;
        Some(json!({
            "host": host,
            "port": port,
            "url": format!("http://{host}:{port}{}", self.openai_compat_path),
            "models": ids,
            "models_display": models_display,
            "provider": provider,
        }))
    }

    /// Discover available models from all reachable hosts.
    pub async fn discover_models(&self) -> Value {
        let hosts = self.get_hosts().await;

        logger::info(&format!(
            "Scanning {} hosts for models: {:?}",
            hosts.len(),
            hosts
        ));

        // Well-known ports: 8000-8020 (vLLM, llama.cpp, SGLang, Cookbook),
        // 1234 (LM Studio), 11434 (Ollama).
        let mut ports: Vec<i64> = (8000..=8020).collect();
        ports.push(1234);
        ports.push(11434);
        // ports += [p for p in sorted(self._extra_ports) if p not in ports]
        // BTreeSet iterates in sorted order, matching Python's sorted().
        for p in self.extra_ports.lock().unwrap().iter().copied() {
            if !ports.contains(&p) {
                ports.push(p);
            }
        }

        // Build list of (host, port) to check.
        let mut targets: Vec<(String, i64)> = Vec::new();
        for h in &hosts {
            for &p in &ports {
                targets.push((h.clone(), p));
            }
        }

        // httpx.get with no shared client; reqwest reuses one client across the
        // fan-out (each request still carries its own 3s timeout).
        let client = reqwest::Client::new();

        // ThreadPoolExecutor(max_workers=50) -> buffer_unordered(50).
        let results: Vec<Option<Value>> = stream::iter(targets.into_iter().map(|(h, p)| {
            let client = &client;
            async move { self.check_port(client, &h, p).await }
        }))
        .buffer_unordered(50)
        .collect()
        .await;

        // dedupe by (port, sorted(models)) to avoid the same machine via
        // different IPs. The Python iterates `as_completed`, so insertion order
        // is nondeterministic; the final `items.sort` makes it deterministic.
        let mut items: Vec<Value> = Vec::new();
        let mut seen_models: std::collections::HashSet<(i64, Vec<String>)> =
            std::collections::HashSet::new();
        for result in results.into_iter().flatten() {
            let port = result.get("port").and_then(|v| v.as_i64()).unwrap_or(0);
            let mut models: Vec<String> = result
                .get("models")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|m| m.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();
            models.sort();
            let key = (port, models);
            if !seen_models.contains(&key) {
                seen_models.insert(key);
                items.push(result);
            }
        }

        // Sort by host then port for consistent ordering.
        items.sort_by(|a, b| {
            let ah = a.get("host").and_then(|v| v.as_str()).unwrap_or("");
            let bh = b.get("host").and_then(|v| v.as_str()).unwrap_or("");
            let ap = a.get("port").and_then(|v| v.as_i64()).unwrap_or(0);
            let bp = b.get("port").and_then(|v| v.as_i64()).unwrap_or(0);
            ah.cmp(bh).then(ap.cmp(&bp))
        });

        logger::info(&format!(
            "Discovered {} model endpoints across {} hosts",
            items.len(),
            hosts.len()
        ));
        json!({ "hosts": hosts, "items": items })
    }

    /// Get all available providers.
    pub async fn get_providers(&self) -> Value {
        let discovery = self.discover_models().await;
        let items = discovery
            .get("items")
            .cloned()
            .unwrap_or_else(|| Value::Array(vec![]));
        let hosts = discovery
            .get("hosts")
            .cloned()
            .unwrap_or_else(|| Value::Array(vec![]));

        let mut providers: Vec<Value> = vec![json!({
            "provider": "vllm",
            "hosts": hosts,
            "items": items,
        })];

        if self
            .openai_api_key
            .as_deref()
            .map(|s| !s.is_empty())
            .unwrap_or(false)
        {
            let openai_models = vec![
                "gpt-5.2-codex",
                "gpt-4o-mini",
                "gpt-image-1.5",
                "gpt-4o",
                "gpt-5.2",
                "gpt-5.2-pro",
            ];
            providers.push(json!({
                "provider": "openai",
                "items": [{
                    "url": "https://api.openai.com/v1/chat/completions",
                    "models": openai_models,
                }],
            }));
        }

        json!({ "providers": providers })
    }
}
