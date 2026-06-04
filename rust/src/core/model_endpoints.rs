// core/model_endpoints.rs  <- routes/model_routes.py (the endpoint-config subset)
//! The minimal `model_endpoints` surface the chat interface needs: store a saved
//! LLM endpoint (base URL + encrypted API key), probe its OpenAI-compatible
//! `/models` list, and resolve `(base_url, api_key)` for a session at creation.
//!
//! This is the demo subset of `routes/model_routes.py` — enough for the UI's
//! `/setup` "add an AI endpoint" flow and the model picker (`/api/models`). The
//! full route module (hidden models, model_type routing, vision/utility
//! fallbacks, per-endpoint probing UI) lands with the rest of `routes/`.

use crate::core::database::session_local;
use crate::pydatetime;
use crate::src::secret_storage;
use rusqlite::OptionalExtension;
use serde_json::{json, Value};

/// `(base_url, decrypted api_key)` for an endpoint id, or `None` if not found.
pub fn endpoint_auth(id: &str) -> Option<(String, Option<String>)> {
    let conn = session_local().ok()?;
    let (base, enc_key): (String, Option<String>) = conn
        .query_row(
            "SELECT base_url, api_key FROM model_endpoints WHERE id = ?1",
            [id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()
        .ok()??;
    let key = enc_key
        .filter(|k| !k.is_empty())
        .map(|k| secret_storage::decrypt(&k));
    Some((base, key))
}

/// `db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).first()`
/// (routes/webhook_routes.py:265) — the first enabled endpoint's `(base_url,
/// decrypted api_key)`, or `None` when none is enabled.
///
/// SQLAlchemy's `EncryptedText` type decorator decrypts `api_key` transparently on
/// read, so the Python's `ep.api_key` is the plaintext; the `secret_storage::decrypt`
/// here reproduces that (an empty/NULL stored key -> `None`). No `ORDER BY` — the
/// Python `.first()` takes whatever the engine returns first (insertion order on
/// SQLite); we mirror with a bare `LIMIT 1`.
pub fn first_enabled() -> Option<(String, Option<String>)> {
    let conn = session_local().ok()?;
    let (base, enc_key): (String, Option<String>) = conn
        .query_row(
            "SELECT base_url, api_key FROM model_endpoints WHERE is_enabled = 1 LIMIT 1",
            [],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()
        .ok()??;
    let key = enc_key
        .filter(|k| !k.is_empty())
        .map(|k| secret_storage::decrypt(&k));
    Some((base, key))
}

/// Create a `model_endpoints` row (API key stored encrypted, model list cached).
/// Returns the new id.
pub fn create(
    name: &str,
    base_url: &str,
    api_key: Option<&str>,
    models: &[String],
    owner: Option<&str>,
) -> rusqlite::Result<String> {
    let conn = session_local()?;
    let id = uuid::Uuid::new_v4().simple().to_string();
    let now = pydatetime::utcnow_naive_iso();
    let enc = api_key.filter(|k| !k.is_empty()).map(secret_storage::encrypt);
    let cached = serde_json::to_string(models).unwrap_or_else(|_| "[]".to_string());
    conn.execute(
        "INSERT INTO model_endpoints \
           (id, name, base_url, api_key, is_enabled, cached_models, model_type, owner, created_at, updated_at) \
         VALUES (?1, ?2, ?3, ?4, 1, ?5, 'llm', ?6, ?7, ?7)",
        rusqlite::params![id, name, base_url, enc, cached, owner, now],
    )?;
    Ok(id)
}

/// Does an endpoint with this EXACT `base_url` scheme already exist for `owner`
/// (or shared)? Used to auto-register each Codex provider exactly once per user.
///
/// This is an exact `base_url = ?` match (NOT a `LIKE 'codex%'` prefix), so the
/// two Codex schemes register INDEPENDENTLY: `codex:` (Mode A — "Codex
/// (Harness)") and `codex-responses:` (Mode B — "Codex (API)") each get their own
/// idempotency guard. A `LIKE 'codex%'` match would let only the FIRST scheme
/// register and silently skip the second.
pub fn has_codex_scheme(owner: &str, base_url: &str) -> bool {
    let conn = match session_local() {
        Ok(c) => c,
        Err(_) => return false,
    };
    conn.query_row(
        "SELECT 1 FROM model_endpoints \
         WHERE base_url = ?1 AND (owner = ?2 OR owner IS NULL) LIMIT 1",
        rusqlite::params![base_url, owner],
        |_| Ok(()),
    )
    .optional()
    .ok()
    .flatten()
    .is_some()
}

/// Mark an endpoint row as tool-capable (`supports_tools = 1`). Used for the
/// `codex-responses:` (Mode B) row so `lookup_endpoint_supports_tools` returns
/// `Some(true)` → `is_api_model = true` → the agent loop sends Odysseus's OWN
/// tool schemas (Mode B agents drive Odysseus tools, not codex's).
pub fn set_supports_tools(id: &str, supports: bool) -> rusqlite::Result<()> {
    let conn = session_local()?;
    conn.execute(
        "UPDATE model_endpoints SET supports_tools = ?2 WHERE id = ?1",
        rusqlite::params![id, if supports { 1 } else { 0 }],
    )?;
    Ok(())
}

/// List endpoints visible to `owner` (their own + shared/null-owner rows),
/// newest first, as JSON objects for `/api/model-endpoints` and `/api/models`.
pub fn list(owner: &str) -> Vec<Value> {
    let conn = match session_local() {
        Ok(c) => c,
        Err(_) => return vec![],
    };
    let mut stmt = match conn.prepare(
        "SELECT id, name, base_url, cached_models, model_type, is_enabled \
         FROM model_endpoints WHERE owner = ?1 OR owner IS NULL ORDER BY created_at DESC",
    ) {
        Ok(s) => s,
        Err(_) => return vec![],
    };
    let rows = stmt.query_map([owner], |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, String>(1)?,
            r.get::<_, String>(2)?,
            r.get::<_, Option<String>>(3)?,
            r.get::<_, Option<String>>(4)?,
            r.get::<_, Option<bool>>(5)?.unwrap_or(true),
        ))
    });
    let rows = match rows {
        Ok(it) => it,
        Err(_) => return vec![],
    };
    rows.filter_map(|r| r.ok())
        .map(|(id, name, base_url, cached, mtype, enabled)| {
            let models: Vec<String> = cached
                .and_then(|c| serde_json::from_str(&c).ok())
                .unwrap_or_default();
            json!({
                "id": id,
                "name": name,
                "url": base_url,
                "models": models,
                "model_type": mtype.unwrap_or_else(|| "llm".to_string()),
                "is_enabled": enabled,
                "offline": false,
            })
        })
        .collect()
}

/// Probe an OpenAI-compatible `/models` list (`{"data":[{"id":…}]}`). Returns the
/// model ids, or an empty vec if the endpoint can't be reached / parsed.
pub async fn probe_models(base_url: &str, api_key: Option<&str>) -> Vec<String> {
    let base = crate::src::endpoint_resolver::normalize_base(Some(base_url));
    let url = format!("{base}/models");
    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(8))
        .build()
    {
        Ok(c) => c,
        Err(_) => return vec![],
    };
    let mut req = client.get(&url);
    if let Some(k) = api_key.filter(|k| !k.is_empty()) {
        req = req.bearer_auth(k);
    }
    let resp = match req.send().await {
        Ok(r) => r,
        Err(_) => return vec![],
    };
    if !resp.status().is_success() {
        return vec![];
    }
    let j: Value = match resp.json().await {
        Ok(v) => v,
        Err(_) => return vec![],
    };
    j.get("data")
        .and_then(|d| d.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|m| m.get("id").and_then(|i| i.as_str()).map(String::from))
                .collect()
        })
        .unwrap_or_default()
}
