// routes/search_routes.rs  <- routes/search_routes.py
//! Search routes — `/api/search/config` GET, `/api/search` POST, plus the
//! provider listing/query endpoints.
//!
//! Faithful port of `routes/search_routes.py`. The Python factory
//! `setup_search_routes(config) -> APIRouter` mounts four handlers (no prefix —
//! each `@router.<method>` carries the full `/api/...` path) and captures
//! nothing but the `config` arg (which the handlers never actually read). The
//! axum analogue [`setup_search_routes`] returns a `Router<AppState>`; the
//! handlers reach the search engine through the already-ported
//! `crate::services::search` re-exports, so the factory takes no params.
//!
//! ## Auth & error model
//!
//! These four endpoints have **no auth** (no `Depends(require_*)`, no
//! `get_current_user`) and **never raise**: every failure path returns a JSON
//! object carrying an `error` key (`{"context": "", "sources": [], "error": ...}`
//! / `{"results": [], "provider": ..., "error": ...}`), mirroring the Python
//! `try/except -> {error: ...}` shape rather than an `HTTPException`. So the
//! handlers return `Json<Value>` directly (no `Result<_, HttpException>`), and
//! the `error` branches are reproduced 1:1.
//!
//! ## `_request_values`
//!
//! The browser UI posts `FormData`, while the agent's generic `app_api` tool
//! posts JSON. FastAPI `Form(...)` rejects JSON with a 422 before the handler
//! runs, so the Python helper sniffs the content-type and merges query params
//! with whichever body shape arrived, swallowing any parse error. The port
//! reproduces that sniff over the raw [`axum::extract::Request`]: query params
//! first (`dict(request.query_params)`), then a JSON object (`isinstance(body,
//! dict)`) or url-encoded form merged on top (`values.update(...)`), with all
//! parse failures swallowed (`except Exception: pass`).
//!
//! ## Blocking engine
//!
//! `comprehensive_web_search` / `_call_provider` are synchronous and blocking
//! (the engine uses `reqwest::blocking`), so — matching the rewire convention
//! used by `services::search::service` and the deep-research/chat ports — the
//! blocking work runs inside `tokio::task::spawn_blocking` while the handlers
//! stay `async` like the Python `async def`s.


use std::time::Instant;

use axum::extract::Request;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde_json::{json, Map, Value};

use crate::routes::AppState;
use crate::services::search::{
    comprehensive_web_search, get_search_config, ComprehensiveResult, PROVIDER_INFO, _call_provider,
};
use crate::src::search::providers::{_get_provider_key, _get_search_instance};

/// `round(x, 2)` faithful to CPython's `round` (round-half-to-even). The
/// elapsed timing values are inherently non-deterministic, but the rounding
/// rule is reproduced exactly so the JSON shape matches Python's `round(..., 2)`.
fn round2(x: f64) -> f64 {
    if !x.is_finite() {
        return x;
    }
    let factor = 100.0_f64;
    let scaled = x * factor;
    let floor = scaled.floor();
    let diff = scaled - floor;
    let rounded = if (diff - 0.5).abs() < f64::EPSILON {
        if (floor as i64) % 2 == 0 {
            floor
        } else {
            floor + 1.0
        }
    } else {
        scaled.round()
    };
    rounded / factor
}

/// `async def _request_values(request) -> Dict[str, Any]` — accept JSON, form
/// data, or query params for search endpoints.
///
/// ```python
/// values: Dict[str, Any] = dict(request.query_params)
/// content_type = (request.headers.get("content-type") or "").lower()
/// try:
///     if "application/json" in content_type:
///         body = await request.json()
///         if isinstance(body, dict):
///             values.update(body)
///     else:
///         form = await request.form()
///         values.update(dict(form))
/// except Exception:
///     pass
/// return values
/// ```
///
/// `dict(request.query_params)` / `dict(form)` keep the **last** value for a
/// repeated key (verified against Starlette), which `Map::insert` reproduces by
/// overwriting in iteration order. The whole try/except is swallowed: any body
/// that fails to parse simply leaves `values` as the query params alone.
async fn request_values(request: Request) -> Map<String, Value> {
    // content_type = (request.headers.get("content-type") or "").lower()
    let content_type = request
        .headers()
        .get(axum::http::header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_lowercase();

    // values = dict(request.query_params) — string values, last-wins.
    let mut values: Map<String, Value> = Map::new();
    if let Some(qs) = request.uri().query() {
        for (k, v) in url::form_urlencoded::parse(qs.as_bytes()) {
            values.insert(k.into_owned(), Value::String(v.into_owned()));
        }
    }

    // try: ... except Exception: pass — swallow every body-parse failure.
    if content_type.contains("application/json") {
        // body = await request.json(); if isinstance(body, dict): values.update(body)
        if let Ok(bytes) =
            axum::body::to_bytes(request.into_body(), usize::MAX).await
        {
            if let Ok(Value::Object(body)) = serde_json::from_slice::<Value>(&bytes) {
                for (k, v) in body {
                    values.insert(k, v);
                }
            }
        }
    } else {
        // form = await request.form(); values.update(dict(form))
        if let Ok(bytes) =
            axum::body::to_bytes(request.into_body(), usize::MAX).await
        {
            for (k, v) in url::form_urlencoded::parse(&bytes) {
                values.insert(k.into_owned(), Value::String(v.into_owned()));
            }
        }
    }

    values
}

/// `(values.get(a) or values.get(b) or "")` -> the first non-empty string,
/// coercing non-strings via Python `str(...)` semantics for the cases search
/// uses (the agent's JSON tool can send a non-string `query`/`count`).
///
/// Python does `str(values.get("query") or values.get("q") or "")`: the `or`
/// chain short-circuits on the first truthy value, then `str(...)` stringifies
/// it. For JSON numbers/bools/null this matters; this helper reproduces the
/// truthiness + `str()` coercion for the value shapes that reach these handlers.
fn coerce_str(values: &Map<String, Value>, keys: &[&str]) -> String {
    for key in keys {
        if let Some(v) = values.get(*key) {
            if value_truthy(v) {
                return py_str(v);
            }
        }
    }
    String::new()
}

/// Python truthiness for the JSON value shapes a search payload carries.
fn value_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Python `str(value)` for the JSON value shapes (used by the `str(... or ...)`
/// coercions). Strings pass through; numbers/bools/null mirror `str()`.
fn py_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Bool(b) => {
            if *b {
                "True".to_string()
            } else {
                "False".to_string()
            }
        }
        Value::Null => "None".to_string(),
        other => other.to_string(),
    }
}

/// `setup_search_routes(config) -> APIRouter` — assemble the four search
/// endpoints. The Python `config` arg is captured but never read; the axum
/// factory takes no params (deps come from `State<AppState>` / the engine
/// singletons). `tags=["search"]` has no axum analogue.
pub fn setup_search_routes() -> Router<AppState> {
    Router::new()
        .route("/api/search/config", get(get_search_settings))
        .route("/api/search", post(do_web_search))
        .route("/api/search/providers", get(list_search_providers))
        .route("/api/search/query", post(search_with_provider))
}

/// `GET /api/search/config` — `async def get_search_settings() -> Dict`.
///
/// ```python
/// return get_search_config()
/// ```
async fn get_search_settings() -> Json<Value> {
    Json(get_search_config())
}

/// `POST /api/search` — `async def do_web_search(request) -> Dict`.
///
/// Standalone web search — returns context string + source list. Used by
/// Compare mode to pre-search once and share results across panes.
///
/// ```python
/// values = await _request_values(request)
/// query = str(values.get("query") or values.get("q") or "").strip()
/// if not query:
///     return {"context": "", "sources": [], "error": "query is required"}
/// time_filter = values.get("time_filter") or values.get("freshness")
/// if time_filter is not None:
///     time_filter = str(time_filter).strip() or None
/// try:
///     context, sources = comprehensive_web_search(query, return_sources=True, time_filter=time_filter)
///     return {"context": context, "sources": sources}
/// except Exception as e:
///     logger.error(...); return {"context": "", "sources": [], "error": str(e)}
/// ```
async fn do_web_search(request: Request) -> Json<Value> {
    let values = request_values(request).await;

    // query = str(values.get("query") or values.get("q") or "").strip()
    let query = coerce_str(&values, &["query", "q"]).trim().to_string();
    if query.is_empty() {
        // return {"context": "", "sources": [], "error": "query is required"}
        return Json(json!({"context": "", "sources": [], "error": "query is required"}));
    }

    // time_filter = values.get("time_filter") or values.get("freshness")
    // if time_filter is not None: time_filter = str(time_filter).strip() or None
    //
    // Python's `or` chain yields the first truthy of time_filter/freshness, else
    // the last (falsy) value or None. The subsequent `if time_filter is not None`
    // then stringifies+strips it; an empty result becomes None. Net effect: pick
    // the first non-empty of the two, stringified+stripped; otherwise None.
    let time_filter: Option<String> = {
        let picked = values
            .get("time_filter")
            .filter(|v| value_truthy(v))
            .or_else(|| values.get("freshness"));
        match picked {
            // time_filter is None only when neither key is present.
            None => None,
            Some(v) => {
                let s = py_str(v);
                let trimmed = s.trim();
                if trimmed.is_empty() {
                    None
                } else {
                    Some(trimmed.to_string())
                }
            }
        }
    };

    // context, sources = comprehensive_web_search(query, return_sources=True, time_filter=...)
    // (blocking engine -> spawn_blocking, like services::search::service).
    let owned_query = query.clone();
    let owned_tf = time_filter.clone();
    let joined = tokio::task::spawn_blocking(move || {
        comprehensive_web_search(
            &owned_query,
            3,                          // max_pages (Python default)
            4,                          // max_workers (Python default)
            owned_tf.as_deref(),        // time_filter
            None,                       // domain_whitelist
            None,                       // domain_blacklist
            None,                       // content_type
            None,                       // language
            0,                          // min_content_length
            true,                       // return_sources
        )
    })
    .await;

    match joined {
        Ok(ComprehensiveResult::WithSources(context, sources)) => {
            // return {"context": context, "sources": sources}
            Json(json!({"context": context, "sources": sources}))
        }
        // `return_sources=True` always yields `WithSources`; a `Report` is a
        // total-match safety net (treat its report as the context, empty sources).
        Ok(ComprehensiveResult::Report(context)) => {
            Json(json!({"context": context, "sources": []}))
        }
        // The blocking task panicked — surface it exactly like the Python
        // `except Exception as e: return {... "error": str(e)}`.
        Err(e) => {
            let msg = if let Ok(reason) = e.try_into_panic() {
                panic_message(&reason)
            } else {
                "task cancelled".to_string()
            };
            crate::pylog::error(&format!("Standalone web search failed: {msg}"));
            Json(json!({"context": "", "sources": [], "error": msg}))
        }
    }
}

/// `GET /api/search/providers` — `async def list_search_providers()`.
///
/// Return available search providers with config status (a **bare array**, not
/// an object).
///
/// ```python
/// providers = []
/// for pid, (label, needs_key, needs_url) in PROVIDER_INFO.items():
///     if pid == "disabled":
///         continue
///     available = True
///     if needs_key and not _get_provider_key(pid):
///         available = False
///     if needs_url and pid == "searxng" and not _get_search_instance():
///         available = False
///     providers.append({"id": pid, "label": label, "available": available})
/// return providers
/// ```
async fn list_search_providers() -> Json<Value> {
    let mut providers: Vec<Value> = Vec::new();
    // PROVIDER_INFO is insertion-ordered (IndexMap) -> same iteration order.
    for (pid, (label, needs_key, needs_url)) in PROVIDER_INFO.iter() {
        // if pid == "disabled": continue
        if *pid == "disabled" {
            continue;
        }
        let mut available = true;
        // if needs_key and not _get_provider_key(pid): available = False
        if *needs_key && _get_provider_key(pid).is_empty() {
            available = false;
        }
        // if needs_url and pid == "searxng" and not _get_search_instance(): available = False
        if *needs_url && *pid == "searxng" && _get_search_instance().is_empty() {
            available = false;
        }
        providers.push(json!({
            "id": pid,
            "label": label,
            "available": available,
        }));
    }
    Json(Value::Array(providers))
}

/// `POST /api/search/query` — `async def search_with_provider(request) -> Dict`.
///
/// Search using a specific provider. Used by compare search mode.
///
/// ```python
/// values = await _request_values(request)
/// query = str(values.get("query") or values.get("q") or "").strip()
/// provider = str(values.get("provider") or "").strip()
/// try:
///     count = int(values.get("count") or values.get("limit") or 10)
/// except Exception:
///     count = 10
/// if not query:
///     return {"results": [], "provider": provider, "error": "query is required"}
/// if provider not in PROVIDER_INFO or provider == "disabled":
///     return {"results": [], "provider": provider, "error": "Unknown provider"}
/// t0 = time.time()
/// try:
///     results = _call_provider(provider, query, min(count, 20))
///     elapsed = round(time.time() - t0, 2)
///     return {"results": results, "provider": provider, "time": elapsed}
/// except Exception as e:
///     elapsed = round(time.time() - t0, 2)
///     logger.error(...); return {"results": [], "provider": provider, "time": elapsed, "error": str(e)}
/// ```
async fn search_with_provider(request: Request) -> Json<Value> {
    let values = request_values(request).await;

    // query = str(values.get("query") or values.get("q") or "").strip()
    let query = coerce_str(&values, &["query", "q"]).trim().to_string();
    // provider = str(values.get("provider") or "").strip()
    let provider = coerce_str(&values, &["provider"]).trim().to_string();

    // try: count = int(values.get("count") or values.get("limit") or 10) except: count = 10
    let count = parse_count(&values);

    // if not query: return {"results": [], "provider": provider, "error": "query is required"}
    if query.is_empty() {
        return Json(json!({"results": [], "provider": provider, "error": "query is required"}));
    }

    // if provider not in PROVIDER_INFO or provider == "disabled":
    //     return {"results": [], "provider": provider, "error": "Unknown provider"}
    if !PROVIDER_INFO.contains_key(provider.as_str()) || provider == "disabled" {
        return Json(json!({"results": [], "provider": provider, "error": "Unknown provider"}));
    }

    // t0 = time.time()
    let t0 = Instant::now();

    // results = _call_provider(provider, query, min(count, 20)) — blocking engine.
    let capped = count.min(20);
    let owned_provider = provider.clone();
    let owned_query = query.clone();
    let joined = tokio::task::spawn_blocking(move || {
        // `_call_provider(provider_name, query, count, time_filter=None)`.
        _call_provider(&owned_provider, &owned_query, capped, None)
    })
    .await;

    let elapsed = round2(t0.elapsed().as_secs_f64());
    match joined {
        Ok(results) => {
            // return {"results": results, "provider": provider, "time": elapsed}
            Json(json!({"results": results, "provider": provider, "time": elapsed}))
        }
        Err(e) => {
            let msg = if let Ok(reason) = e.try_into_panic() {
                panic_message(&reason)
            } else {
                "task cancelled".to_string()
            };
            crate::pylog::error(&format!("Search provider {provider} failed: {msg}"));
            Json(json!({
                "results": [],
                "provider": provider,
                "time": elapsed,
                "error": msg,
            }))
        }
    }
}

/// `try: count = int(values.get("count") or values.get("limit") or 10) except: count = 10`.
///
/// Python's `or` chain takes the first truthy of `count`/`limit` (else the
/// literal `10`), then `int(...)` parses it; any failure falls back to `10`.
/// `int()` accepts an int, a float (truncates), or a numeric string (no
/// truncation — `int("3.5")` raises, falling back to 10).
fn parse_count(values: &Map<String, Value>) -> i64 {
    // picked = values.get("count") or values.get("limit") or 10
    let picked: Value = values
        .get("count")
        .filter(|v| value_truthy(v))
        .or_else(|| values.get("limit").filter(|v| value_truthy(v)))
        .cloned()
        .unwrap_or(json!(10));

    match &picked {
        // int(int) / int(float) — Python int() truncates floats toward zero.
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i
            } else if let Some(f) = n.as_f64() {
                f.trunc() as i64
            } else {
                10
            }
        }
        // int("10") works; int("3.5") raises -> 10. Python int() of a numeric
        // string does NOT accept a decimal point, so parse as an integer only.
        Value::String(s) => s.trim().parse::<i64>().unwrap_or(10),
        // int(True) == 1, int(False) == 0 — but a falsy bool/value never reaches
        // here (the `or` chain skipped it). A truthy `True` -> 1.
        Value::Bool(true) => 1,
        // Anything else (array/object/null) raises in int() -> fallback 10.
        _ => 10,
    }
}

/// Best-effort extraction of a panic payload's message (`str(e)` analogue).
fn panic_message(reason: &Box<dyn std::any::Any + Send>) -> String {
    if let Some(s) = reason.downcast_ref::<&str>() {
        (*s).to_string()
    } else if let Some(s) = reason.downcast_ref::<String>() {
        s.clone()
    } else {
        "search failed".to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn coerce_str_first_nonempty_then_str() {
        let mut m = Map::new();
        m.insert("query".into(), Value::String("".into()));
        m.insert("q".into(), Value::String("hello".into()));
        // query is empty (falsy) -> falls through to q.
        assert_eq!(coerce_str(&m, &["query", "q"]), "hello");
    }

    #[test]
    fn coerce_str_missing_yields_empty() {
        let m = Map::new();
        assert_eq!(coerce_str(&m, &["query", "q"]), "");
    }

    #[test]
    fn coerce_str_coerces_number() {
        // The agent's JSON tool can send a non-string query; str(123) == "123".
        let mut m = Map::new();
        m.insert("query".into(), json!(123));
        assert_eq!(coerce_str(&m, &["query", "q"]), "123");
    }

    #[test]
    fn parse_count_defaults_to_ten() {
        let m = Map::new();
        assert_eq!(parse_count(&m), 10);
    }

    #[test]
    fn parse_count_count_over_limit() {
        // count wins over limit when truthy.
        let mut m = Map::new();
        m.insert("count".into(), json!(7));
        m.insert("limit".into(), json!(3));
        assert_eq!(parse_count(&m), 7);
    }

    #[test]
    fn parse_count_falls_back_to_limit_when_count_zero() {
        // count == 0 is falsy -> Python `or` chain falls to limit.
        let mut m = Map::new();
        m.insert("count".into(), json!(0));
        m.insert("limit".into(), json!(5));
        assert_eq!(parse_count(&m), 5);
    }

    #[test]
    fn parse_count_string_number() {
        let mut m = Map::new();
        m.insert("count".into(), Value::String("15".into()));
        assert_eq!(parse_count(&m), 15);
    }

    #[test]
    fn parse_count_bad_string_falls_back() {
        // int("3.5") raises in Python -> fallback 10.
        let mut m = Map::new();
        m.insert("count".into(), Value::String("3.5".into()));
        assert_eq!(parse_count(&m), 10);
    }

    #[test]
    fn parse_count_truncates_float() {
        let mut m = Map::new();
        m.insert("count".into(), json!(8.9));
        assert_eq!(parse_count(&m), 8);
    }

    #[test]
    fn round2_half_even() {
        // banker's rounding at 2 digits.
        assert_eq!(round2(0.125), 0.12);
        assert_eq!(round2(0.135), 0.14);
    }

    #[test]
    fn round2_normal() {
        assert_eq!(round2(1.239), 1.24);
        assert_eq!(round2(0.0), 0.0);
    }

    #[tokio::test]
    async fn request_values_query_only_get() {
        // No body, just query params (GET-style); last-wins on dup keys.
        let req = axum::http::Request::builder()
            .uri("/api/search?query=hi&query=bye&b=2")
            .body(axum::body::Body::empty())
            .unwrap();
        let v = request_values(req).await;
        assert_eq!(v.get("query"), Some(&Value::String("bye".into())));
        assert_eq!(v.get("b"), Some(&Value::String("2".into())));
    }

    #[tokio::test]
    async fn request_values_json_body_overrides_query() {
        let req = axum::http::Request::builder()
            .uri("/api/search?query=fromquery")
            .header("content-type", "application/json")
            .body(axum::body::Body::from(r#"{"query":"fromjson","count":5}"#))
            .unwrap();
        let v = request_values(req).await;
        // body.update(values) -> JSON value overrides the query param.
        assert_eq!(v.get("query"), Some(&Value::String("fromjson".into())));
        // non-string JSON values pass through untouched.
        assert_eq!(v.get("count"), Some(&json!(5)));
    }

    #[tokio::test]
    async fn request_values_form_body() {
        let req = axum::http::Request::builder()
            .uri("/api/search")
            .header("content-type", "application/x-www-form-urlencoded")
            .body(axum::body::Body::from("query=formquery&provider=brave"))
            .unwrap();
        let v = request_values(req).await;
        assert_eq!(v.get("query"), Some(&Value::String("formquery".into())));
        assert_eq!(v.get("provider"), Some(&Value::String("brave".into())));
    }

    #[tokio::test]
    async fn request_values_bad_json_is_swallowed() {
        // except Exception: pass -> values stays the query params alone.
        let req = axum::http::Request::builder()
            .uri("/api/search?q=keep")
            .header("content-type", "application/json")
            .body(axum::body::Body::from("not json at all {{{"))
            .unwrap();
        let v = request_values(req).await;
        assert_eq!(v.get("q"), Some(&Value::String("keep".into())));
        assert_eq!(v.len(), 1);
    }

    #[tokio::test]
    async fn request_values_json_array_is_ignored() {
        // isinstance(body, dict) guard: a JSON array does NOT update values.
        let req = axum::http::Request::builder()
            .uri("/api/search?q=keep")
            .header("content-type", "application/json")
            .body(axum::body::Body::from("[1,2,3]"))
            .unwrap();
        let v = request_values(req).await;
        assert_eq!(v.get("q"), Some(&Value::String("keep".into())));
        assert_eq!(v.len(), 1);
    }
}
