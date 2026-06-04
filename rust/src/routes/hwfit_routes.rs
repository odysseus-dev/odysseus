// routes/hwfit_routes.rs  <- the Python `routes/hwfit_routes.py`
//! Mirrors the Python `routes/hwfit_routes.py` (`setup_hwfit_routes()`).
//!
//! Three no-auth, no-DB GET endpoints under the `/api/hwfit` prefix that rank
//! the model catalog against detected (or manually simulated) hardware:
//!
//!   * `GET /api/hwfit/system`        — [`get_system`]
//!   * `GET /api/hwfit/models`        — [`get_models`]
//!   * `GET /api/hwfit/image-models`  — [`get_image_models`]
//!
//! Every handler is a thin shell over the already-ported
//! [`crate::services::hwfit`] package (`hardware::detect_system`,
//! `fit::rank_models`, `image_models::rank_image_models`,
//! `models::get_models` / `models::model_catalog_path`). The Python keeps the
//! `system` and model rows as untyped `dict`s flowing loosely between modules;
//! the Rust port keeps them as order-preserving [`serde_json::Value`] objects
//! (the `preserve_order` feature) so the JSON key insertion order the UI
//! depends on is reproduced byte-for-byte, exactly as in the service layer.
//!
//! ## Feature gate
//!
//! `web` only. None of the three handlers touch the DB or any manager — they
//! read no `AppState` field (the factory still returns `Router<AppState>` so it
//! merges into the shared aggregator), call only the default-gated `hwfit`
//! service, and never authenticate. No path overlaps the inline `web/mod.rs`
//! subset (`/api/hwfit/*` is otherwise unused), so the merge is collision-free.
//!
//! ## Query-parameter parsing fidelity
//!
//! FastAPI declares the typed query params (`fresh: bool`, `limit: int`, the
//! rest `str`). Pydantic v2 coerces them and, on a malformed value, FastAPI
//! returns `422` with `{"detail": [ {type, loc, msg, input} ]}`. The port
//! reproduces that: [`query_bool`] mirrors Pydantic's lax bool coercion (the
//! `true/1/on/yes/y/t` ⇄ `false/0/off/no/n/f` set, case-insensitive) and
//! [`query_int`] mirrors integer parsing, each emitting the matching FastAPI
//! `422` body on failure. The `str` params (`gpu_count`, `gpu_group`,
//! `manual_*`) are taken verbatim — the *handler* does the `int(...)`/`float`
//! conversions, faithfully including the raw `int(gpu_count)` that raises on a
//! non-numeric value (Python `ValueError` -> 500, reproduced as a 500
//! [`HttpException`]).

use std::collections::HashMap;

use axum::extract::Query;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};

use crate::routes::{AppState, HttpException};
use crate::services::hwfit::{fit, hardware, image_models, models, profiles};

// ---------------------------------------------------------------------------
// Query-parameter coercion (the FastAPI/Pydantic v2 boundary)
// ---------------------------------------------------------------------------

/// A FastAPI/Pydantic `422 Unprocessable Entity` validation error for a single
/// query parameter, rendered as Starlette's
/// `{"detail": [ {"type", "loc": ["query", <name>], "msg", "input"} ]}`.
fn validation_error(err_type: &str, name: &str, msg: &str, input: &str) -> HttpException {
    HttpException::with_detail(
        422,
        json!([{
            "type": err_type,
            "loc": ["query", name],
            "msg": msg,
            "input": input,
        }]),
    )
}

/// `param: bool = <default>` — Pydantic v2 lax bool coercion of a query value.
///
/// Missing -> `default`. Present -> case-insensitive coercion: the truthy set
/// `{"1","on","t","true","y","yes"}` -> `true`, the falsy set
/// `{"0","off","f","false","n","no"}` -> `false`; anything else is a `422`
/// `bool_parsing` error, exactly as FastAPI would reject it.
fn query_bool(q: &HashMap<String, String>, name: &str, default: bool) -> Result<bool, HttpException> {
    match q.get(name) {
        None => Ok(default),
        Some(raw) => {
            let v = raw.trim().to_lowercase();
            match v.as_str() {
                "1" | "on" | "t" | "true" | "y" | "yes" => Ok(true),
                "0" | "off" | "f" | "false" | "n" | "no" => Ok(false),
                _ => Err(validation_error(
                    "bool_parsing",
                    name,
                    "Input should be a valid boolean, unable to interpret input",
                    raw,
                )),
            }
        }
    }
}

/// `param: int = <default>` — Pydantic v2 integer coercion of a query value.
///
/// Missing -> `default`. Present -> parsed as an `i64` (Pydantic accepts
/// surrounding whitespace); a non-integer string is a `422` `int_parsing`
/// error, matching FastAPI.
fn query_int(q: &HashMap<String, String>, name: &str, default: i64) -> Result<i64, HttpException> {
    match q.get(name) {
        None => Ok(default),
        Some(raw) => raw.trim().parse::<i64>().map_err(|_| {
            validation_error(
                "int_parsing",
                name,
                "Input should be a valid integer, unable to parse string as an integer",
                raw,
            )
        }),
    }
}

/// `param: str = ""` — a string query param defaulting to empty, returned as an
/// owned `String` so the handler can pass `&str` / compare against `""` exactly
/// like the Python.
fn query_str(q: &HashMap<String, String>, name: &str) -> String {
    q.get(name).cloned().unwrap_or_default()
}

/// `param: str = "<default>"` — a string query param with a non-empty default.
fn query_str_default(q: &HashMap<String, String>, name: &str, default: &str) -> String {
    match q.get(name) {
        Some(s) => s.clone(),
        None => default.to_string(),
    }
}

// ---------------------------------------------------------------------------
// Small `serde_json::Value` object helpers (mirror Python `dict` operations)
// ---------------------------------------------------------------------------

/// `system[key] = value` — set/overwrite a key on a `Value::Object`, preserving
/// insertion order (existing keys keep their slot; new keys append), exactly
/// like a Python `dict` assignment under the `preserve_order` map.
fn set(obj: &mut Value, key: &str, value: Value) {
    if let Value::Object(map) = obj {
        map.insert(key.to_string(), value);
    }
}

/// `system.pop(key, None)` — remove a key if present (the value is discarded).
fn pop(obj: &mut Value, key: &str) {
    if let Value::Object(map) = obj {
        map.remove(key);
    }
}

/// `system.get(key)` — borrow a value, `None` when the key is absent.
fn get<'a>(obj: &'a Value, key: &str) -> Option<&'a Value> {
    obj.get(key)
}

/// `float(system.get(key) or 0)` style read: the numeric value, or `0.0` when
/// the key is missing, `null`, or not a number (Python's `... or 0`).
fn get_f64_or0(obj: &Value, key: &str) -> f64 {
    obj.get(key).and_then(Value::as_f64).unwrap_or(0.0)
}

/// Build a JSON number from an `f64`, rendering non-finite as `0` (the same
/// fallback the service layer uses), so `round(...)` results serialize as the
/// numbers the UI expects rather than `null`.
fn num(x: f64) -> Value {
    serde_json::Number::from_f64(x)
        .map(Value::Number)
        .unwrap_or_else(|| Value::Number(serde_json::Number::from(0)))
}

/// `round(x, 1)` — Python's built-in `round` uses banker's (round-half-to-even)
/// rounding; reproduce it so the per-GPU VRAM caps and simulated RAM match the
/// service layer's `round_to`/`round_half_even` exactly (a `.x5` tie rounded
/// the wrong way could shift a model across a fit boundary).
fn round1(x: f64) -> f64 {
    round_half_even(x, 1)
}

/// Round `x` to `ndigits` decimal places using round-half-to-even, matching
/// Python's built-in `round` (same algorithm as the `hwfit` service modules).
// The `diff < 0.5` and even-`floor` arms are deliberately the same value
// (both keep `floor`): the half-even algorithm's distinct decision branches
// mirror Python's `round` even though two of them yield `floor`.
#[allow(clippy::if_same_then_else)]
fn round_half_even(x: f64, ndigits: i32) -> f64 {
    if !x.is_finite() {
        return x;
    }
    let factor = 10f64.powi(ndigits);
    let scaled = x * factor;
    let floor = scaled.floor();
    let diff = scaled - floor;
    let rounded = if diff > 0.5 {
        floor + 1.0
    } else if diff < 0.5 {
        floor
    } else if (floor as i64) % 2 == 0 {
        floor
    } else {
        floor + 1.0
    };
    rounded / factor
}

// ---------------------------------------------------------------------------
// `_apply_manual_hardware` — the "what if I had this setup" simulator
// ---------------------------------------------------------------------------

/// Manual hardware is a "what if I had this setup" simulator — REPLACES the
/// detected hardware entirely instead of adding to it.
///
/// The previous additive behavior averaged the manual VRAM across all GPUs
/// (base + manual), which meant adding "1× 400 GB" on top of "2× 70 GB" only
/// nudged the per-GPU cap from 70 to 180 GB (= 540 / 3), so GGUF models bigger
/// than that still didn't surface — exactly the "cap stuck at detected level"
/// bug the user hit.
fn apply_manual_hardware(
    system: &mut Value,
    manual_mode: &str,
    manual_gpu_count: &str,
    manual_vram_gb: &str,
    manual_ram_gb: &str,
    manual_backend: &str,
) {
    // manual_mode = (manual_mode or "").lower()
    let manual_mode = manual_mode.to_lowercase();
    // if manual_mode not in {"gpu", "ram"}: return system
    if manual_mode != "gpu" && manual_mode != "ram" {
        return;
    }

    // try: override_ram_gb = float(manual_ram_gb) if manual_ram_gb else 0
    // except ValueError: override_ram_gb = 0
    let mut override_ram_gb: f64 = if manual_ram_gb.is_empty() {
        0.0
    } else {
        manual_ram_gb.trim().parse::<f64>().unwrap_or(0.0)
    };
    // override_ram_gb = max(0.0, override_ram_gb)
    override_ram_gb = override_ram_gb.max(0.0);
    if override_ram_gb != 0.0 {
        // Replace RAM, don't add. The number in the field is the TOTAL system
        // memory the user wants to simulate.
        set(system, "available_ram_gb", num(round1(override_ram_gb)));
        set(system, "total_ram_gb", num(round1(override_ram_gb)));
    }
    set(system, "manual_hardware", Value::Bool(true));

    if manual_mode == "ram" {
        // RAM-only simulation — wipe GPU entirely so the ranker uses CPU/RAM
        // paths.
        set(system, "has_gpu", Value::Bool(false));
        set(system, "gpu_name", Value::Null);
        set(system, "gpu_vram_gb", num(0.0));
        set(system, "gpu_count", json!(0));
        set(system, "gpus", json!([]));
        set(system, "gpu_groups", json!([]));
        set(system, "backend", json!("cpu_x86"));
        pop(system, "unified_memory");
        return;
    }

    // try: count = int(manual_gpu_count) if manual_gpu_count else 1
    // except ValueError: count = 1
    let mut count: i64 = if manual_gpu_count.is_empty() {
        1
    } else {
        manual_gpu_count.trim().parse::<i64>().unwrap_or(1)
    };
    // try: vram_each = float(manual_vram_gb) if manual_vram_gb else 8.0
    // except ValueError: vram_each = 8.0
    let mut vram_each: f64 = if manual_vram_gb.is_empty() {
        8.0
    } else {
        manual_vram_gb.trim().parse::<f64>().unwrap_or(8.0)
    };
    // count = max(1, min(count, 16))
    count = count.clamp(1, 16);
    // vram_each = max(1.0, vram_each)
    vram_each = vram_each.max(1.0);
    // backend = (manual_backend or system.get("backend") or "cuda").lower()
    let backend_src = if !manual_backend.is_empty() {
        manual_backend.to_string()
    } else {
        match get(system, "backend").and_then(Value::as_str) {
            Some(b) if !b.is_empty() => b.to_string(),
            _ => "cuda".to_string(),
        }
    };
    let mut backend = backend_src.to_lowercase();
    // if backend not in {"cuda", "rocm", "metal", "cpu_x86", "cpu_arm"}: backend = "cuda"
    if !matches!(backend.as_str(), "cuda" | "rocm" | "metal" | "cpu_x86" | "cpu_arm") {
        backend = "cuda".to_string();
    }
    // total_vram = round(vram_each * count, 1)
    let total_vram = round1(vram_each * count as f64);
    // gpu_name = f"Simulated {backend.upper()} GPU" + (f" × {count}" if count > 1 else "")
    let gpu_name = if count > 1 {
        format!("Simulated {} GPU × {}", backend.to_uppercase(), count)
    } else {
        format!("Simulated {} GPU", backend.to_uppercase())
    };
    set(system, "has_gpu", Value::Bool(true));
    set(system, "gpu_name", json!(gpu_name));
    set(system, "gpu_vram_gb", num(total_vram));
    set(system, "gpu_count", json!(count));
    // system["gpus"] = [{"index": i, "name": gpu_name, "vram_gb": vram_each} for i in range(count)]
    let gpus: Vec<Value> = (0..count)
        .map(|i| {
            json!({
                "index": i,
                "name": gpu_name,
                "vram_gb": num(vram_each),
            })
        })
        .collect();
    set(system, "gpus", Value::Array(gpus));
    // Single homogeneous pool — vram_each here is the ACTUAL per-GPU VRAM the
    // user entered, not an average. That's the whole point: raising vram_each
    // lifts the per-GPU cap (GGUF, tensor-parallel math) all the way up, not
    // just by a small fraction.
    let indices: Vec<Value> = (0..count).map(|i| json!(i)).collect();
    set(
        system,
        "gpu_groups",
        json!([{
            "name": gpu_name,
            "vram_each": num(vram_each),
            "count": count,
            "indices": indices,
            "vram_total": num(total_vram),
        }]),
    );
    set(system, "homogeneous", Value::Bool(true));
    set(system, "backend", json!(backend));
    // Apple Silicon shares one unified memory pool with the GPU; flag it so
    // the API/UI report it the way real Metal detection does. Discrete GPUs
    // (cuda/rocm) and the CPU backends carry separate VRAM, so clear any
    // stale flag a previous detection left on the dict.
    if backend == "metal" {
        set(system, "unified_memory", Value::Bool(true));
    } else {
        pop(system, "unified_memory");
    }
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

/// `GET /api/hwfit/system` — `get_system(host, ssh_port, platform, fresh)`.
///
/// Detect and return current system hardware info. Pass `host=user@server` for
/// remote. `fresh=true` bypasses the per-host cache (the Rescan button).
async fn get_system(Query(q): Query<HashMap<String, String>>) -> Result<Response, HttpException> {
    let host = query_str(&q, "host");
    let ssh_port = query_str(&q, "ssh_port");
    let platform = query_str(&q, "platform");
    let fresh = query_bool(&q, "fresh", false)?;
    // return detect_system(host=host, ssh_port=ssh_port, platform=platform, fresh=fresh)
    let system = hardware::detect_system(&host, &ssh_port, &platform, fresh);
    Ok(Json(system).into_response())
}

/// `GET /api/hwfit/models` — rank LLM models against detected hardware and
/// return scored results.
///
/// `gpu_count`: override GPU count (0 = CPU only, 1-N = simulate N GPUs of the
/// active group). `gpu_group`: index into `system.gpu_groups` (the homogeneous
/// pools) to target — empty/auto = the largest pool. vLLM can only
/// tensor-parallel across identical GPUs, so we never mix pools. `fresh=true`
/// bypasses the hardware-detection cache.
async fn get_models(Query(q): Query<HashMap<String, String>>) -> Result<Response, HttpException> {
    let use_case = query_str(&q, "use_case");
    let sort = query_str_default(&q, "sort", "score");
    let limit = query_int(&q, "limit", 50)?;
    let search = query_str(&q, "search");
    let host = query_str(&q, "host");
    let quant = query_str(&q, "quant");
    let gpu_count = query_str(&q, "gpu_count");
    let gpu_group = query_str(&q, "gpu_group");
    let ssh_port = query_str(&q, "ssh_port");
    let platform = query_str(&q, "platform");
    let fresh = query_bool(&q, "fresh", false)?;
    let manual_mode = query_str(&q, "manual_mode");
    let manual_gpu_count = query_str(&q, "manual_gpu_count");
    let manual_vram_gb = query_str(&q, "manual_vram_gb");
    let manual_ram_gb = query_str(&q, "manual_ram_gb");
    let manual_backend = query_str(&q, "manual_backend");
    let ctx = query_str(&q, "ctx");
    let ignore_detected_gpu = query_bool(&q, "ignore_detected_gpu", false)?;
    let ignore_detected_ram = query_bool(&q, "ignore_detected_ram", false)?;
    let fit_only = query_bool(&q, "fit_only", false)?;

    // system = deepcopy(detect_system(...)) — detect_system already returns an
    // owned clone of the cached value, so the local `system` is the
    // mutable-copy the handler edits without disturbing the cache.
    let mut system = hardware::detect_system(&host, &ssh_port, &platform, fresh);

    // if system.get("error"): return {"system": system, "models": [], "error": ...}
    if let Some(err) = get(&system, "error") {
        if !err.is_null() {
            let err = err.clone();
            return Ok(Json(json!({
                "system": system,
                "models": [],
                "error": err,
            }))
            .into_response());
        }
    }
    // if not get_models(): return {"system": system, "models": [], "error": "Model catalog missing or empty: ..."}
    if models::get_models().is_empty() {
        return Ok(Json(json!({
            "system": system,
            "models": [],
            "error": format!("Model catalog missing or empty: {}", models::model_catalog_path()),
        }))
        .into_response());
    }

    if ignore_detected_gpu {
        set(&mut system, "has_gpu", Value::Bool(false));
        set(&mut system, "gpu_name", Value::Null);
        set(&mut system, "gpu_vram_gb", num(0.0));
        set(&mut system, "gpu_count", json!(0));
        set(&mut system, "gpus", json!([]));
        set(&mut system, "gpu_groups", json!([]));
    }
    if ignore_detected_ram {
        set(&mut system, "available_ram_gb", num(0.0));
        set(&mut system, "total_ram_gb", num(0.0));
    }

    apply_manual_hardware(
        &mut system,
        &manual_mode,
        &manual_gpu_count,
        &manual_vram_gb,
        &manual_ram_gb,
        &manual_backend,
    );

    // Keep the raw detection around so the UI can still show the box's full GPU
    // complement even while we rank against one homogeneous pool.
    // system["detected_gpu_vram_gb"] = system.get("gpu_vram_gb")
    let detected_gpu_vram_gb = get(&system, "gpu_vram_gb").cloned().unwrap_or(Value::Null);
    set(&mut system, "detected_gpu_vram_gb", detected_gpu_vram_gb);
    // system["detected_gpu_count"] = system.get("gpu_count")
    let detected_gpu_count = get(&system, "gpu_count").cloned().unwrap_or(Value::Null);
    set(&mut system, "detected_gpu_count", detected_gpu_count);

    // groups = system.get("gpu_groups") or []
    let groups: Vec<Value> = match get(&system, "gpu_groups") {
        Some(Value::Array(a)) => a.clone(),
        _ => Vec::new(),
    };

    // Resolve the target homogeneous pool. Default (auto) = the largest pool,
    // which for a uniform box is simply "all the GPUs" — no behaviour change.
    let mut grp: Option<Value> = None;
    if !groups.is_empty() {
        // try: gidx = int(gpu_group) if gpu_group != "" else 0
        // except ValueError: gidx = 0
        let gidx: i64 = if gpu_group.is_empty() {
            0
        } else {
            gpu_group.trim().parse::<i64>().unwrap_or(0)
        };
        // if 0 <= gidx < len(groups): grp = groups[gidx]
        if gidx >= 0 && (gidx as usize) < groups.len() {
            grp = Some(groups[gidx as usize].clone());
        }
    }

    // if gpu_count != "":
    if !gpu_count.is_empty() {
        // n = int(gpu_count) — RAW int(), no try/except: a non-numeric value
        // raises ValueError in Python (-> 500). Reproduce as a 500 HttpException.
        let n: i64 = gpu_count.trim().parse::<i64>().map_err(|_| {
            HttpException::new(
                500,
                format!("invalid literal for int() with base 10: '{}'", gpu_count),
            )
        })?;
        if n == 0 {
            // RAM-only mode: rank against system memory, offload allowed.
            set(&mut system, "has_gpu", Value::Bool(false));
            set(&mut system, "gpu_vram_gb", num(0.0));
            set(&mut system, "gpu_count", json!(0));
            set(&mut system, "gpu_only", Value::Bool(false));
            pop(&mut system, "active_group");
        } else if let Some(ref g) = grp {
            apply_group(&mut system, g, n);
            set(&mut system, "gpu_only", Value::Bool(true));
        } else {
            // No per-GPU detail (older detection) — assume uniform split.
            // single_vram = (system.get("gpu_vram_gb") or 0) / (system.get("gpu_count") or 1)
            let cur_vram = get_f64_or0(&system, "gpu_vram_gb");
            let cur_count = match get(&system, "gpu_count").and_then(Value::as_f64) {
                Some(c) if c != 0.0 => c,
                _ => 1.0,
            };
            let single_vram = cur_vram / cur_count;
            // system["gpu_count"] = max(1, n)
            let n_clamped = n.max(1);
            set(&mut system, "gpu_count", json!(n_clamped));
            // system["gpu_vram_gb"] = round(single_vram * max(1, n), 1)
            set(
                &mut system,
                "gpu_vram_gb",
                num(round1(single_vram * n_clamped as f64)),
            );
            set(&mut system, "gpu_only", Value::Bool(true));
        }
    } else if let Some(ref g) = grp {
        // No explicit count, but we still pin to one pool so heterogeneous boxes
        // rank against a real mixable group, not a fictional VRAM sum. gpu_only
        // stays off here so the default view still surfaces offload.
        // _apply_group(grp, grp["count"])
        let g_count = g.get("count").and_then(Value::as_i64).unwrap_or(0);
        apply_group(&mut system, g, g_count);
    }

    // try: target_context = int(ctx) if ctx else None
    // except ValueError: target_context = None
    // if target_context is not None: target_context = max(1024, min(target_context, 1000000))
    let target_context: Option<i64> = if ctx.is_empty() {
        None
    } else {
        ctx.trim().parse::<i64>().ok().map(|v| v.clamp(1024, 1_000_000))
    };

    // results = rank_models(system, use_case=use_case or None, limit=limit,
    //                       search=search or None, sort=sort, quant=quant or None,
    //                       target_context=target_context, fit_only=fit_only)
    let results = fit::rank_models(
        &system,
        none_if_empty(&use_case),
        // `limit: int` is a Python int passed straight to rank_models; the Rust
        // ctor takes a `usize`. A negative limit would be a `ValueError`-free
        // Python slice quirk, but limits arrive non-negative from the UI;
        // clamp at 0 for the usize cast.
        limit.max(0) as usize,
        none_if_empty(&search),
        &sort,
        none_if_empty(&quant),
        target_context,
        fit_only,
    );

    Ok(Json(json!({ "system": system, "models": results })).into_response())
}

/// `GET /api/hwfit/image-models` — rank image generation models against
/// detected hardware.
async fn get_image_models(
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let sort = query_str_default(&q, "sort", "fit");
    let search = query_str(&q, "search");
    let host = query_str(&q, "host");
    let _gpu_count = query_str(&q, "gpu_count"); // declared in Python; unused by the body
    let ssh_port = query_str(&q, "ssh_port");
    let platform = query_str(&q, "platform");
    let fresh = query_bool(&q, "fresh", false)?;
    let manual_mode = query_str(&q, "manual_mode");
    let manual_gpu_count = query_str(&q, "manual_gpu_count");
    let manual_vram_gb = query_str(&q, "manual_vram_gb");
    let manual_ram_gb = query_str(&q, "manual_ram_gb");
    let manual_backend = query_str(&q, "manual_backend");
    let ignore_detected_gpu = query_bool(&q, "ignore_detected_gpu", false)?;
    let ignore_detected_ram = query_bool(&q, "ignore_detected_ram", false)?;

    let mut system = hardware::detect_system(&host, &ssh_port, &platform, fresh);

    // if system.get("error"): return {"system": system, "models": [], "error": ...}
    if let Some(err) = get(&system, "error") {
        if !err.is_null() {
            let err = err.clone();
            return Ok(Json(json!({
                "system": system,
                "models": [],
                "error": err,
            }))
            .into_response());
        }
    }
    if ignore_detected_gpu {
        set(&mut system, "has_gpu", Value::Bool(false));
        set(&mut system, "gpu_name", Value::Null);
        set(&mut system, "gpu_vram_gb", num(0.0));
        set(&mut system, "gpu_count", json!(0));
        set(&mut system, "gpus", json!([]));
        set(&mut system, "gpu_groups", json!([]));
    }
    if ignore_detected_ram {
        set(&mut system, "available_ram_gb", num(0.0));
        set(&mut system, "total_ram_gb", num(0.0));
    }
    apply_manual_hardware(
        &mut system,
        &manual_mode,
        &manual_gpu_count,
        &manual_vram_gb,
        &manual_ram_gb,
        &manual_backend,
    );

    // Image models use a single GPU — always use per-GPU VRAM.
    // gpu_vrams = [float(g.get("vram_gb") or 0) for g in (system.get("gpus") or []) if isinstance(g, dict)]
    let mut gpu_vrams: Vec<f64> = Vec::new();
    if let Some(Value::Array(gpus)) = get(&system, "gpus") {
        for g in gpus {
            if g.is_object() {
                gpu_vrams.push(g.get("vram_gb").and_then(Value::as_f64).unwrap_or(0.0));
            }
        }
    }
    // single_vram = max(gpu_vrams) if gpu_vrams else ((system.get("gpu_vram_gb") or 0) / max(system.get("gpu_count") or 1, 1))
    let single_vram = if !gpu_vrams.is_empty() {
        gpu_vrams.iter().cloned().fold(f64::NEG_INFINITY, f64::max)
    } else {
        let vram = get_f64_or0(&system, "gpu_vram_gb");
        // max(system.get("gpu_count") or 1, 1) — `or 1` collapses 0/None to 1,
        // then `max(_, 1)` keeps it >= 1.
        let count = match get(&system, "gpu_count").and_then(Value::as_f64) {
            Some(c) if c != 0.0 => c,
            _ => 1.0,
        };
        vram / count.max(1.0)
    };
    // system["gpu_vram_gb"] = single_vram  (NOTE: not rounded in Python)
    set(&mut system, "gpu_vram_gb", num(single_vram));
    // system["gpu_count"] = 1 if single_vram > 0 else 0
    set(
        &mut system,
        "gpu_count",
        if single_vram > 0.0 { json!(1) } else { json!(0) },
    );
    // results = rank_image_models(system, search=search or None, sort=sort)
    let results = image_models::rank_image_models(&system, none_if_empty(&search), &sort);

    Ok(Json(json!({ "system": system, "models": results })).into_response())
}

/// `_apply_group(g, n)` — pin the system to `n` GPUs of the homogeneous pool
/// `g`, recomputing the active VRAM/name and recording the active group.
///
/// Defined as a closure capturing `system` in Python; here it is a free
/// function taking `&mut Value` since Rust closures cannot mutate a borrow held
/// across the surrounding match arms.
fn apply_group(system: &mut Value, g: &Value, n: i64) {
    // n = max(1, min(n, g["count"]))
    let g_count = g.get("count").and_then(Value::as_i64).unwrap_or(0);
    let n = n.min(g_count).max(1);
    set(system, "gpu_count", json!(n));
    // system["gpu_vram_gb"] = round(g["vram_each"] * n, 1)
    let vram_each = g.get("vram_each").and_then(Value::as_f64).unwrap_or(0.0);
    set(system, "gpu_vram_gb", num(round1(vram_each * n as f64)));
    // system["gpu_name"] = g["name"]
    set(system, "gpu_name", g.get("name").cloned().unwrap_or(Value::Null));
    // system["active_group"] = {**g, "use_count": n}
    let mut active = g.clone();
    set(&mut active, "use_count", json!(n));
    set(system, "active_group", active);
}

/// `value or None` for the `str` query params passed to the rankers: an empty
/// string becomes `None` (Python's `use_case or None`, `search or None`,
/// `quant or None`).
fn none_if_empty(s: &str) -> Option<&str> {
    if s.is_empty() {
        None
    } else {
        Some(s)
    }
}

/// `GET /api/hwfit/profiles` — `get_serve_profiles(model, host, ssh_port,
/// platform, fresh, serve_weights_gb, serve_quant)`.
///
/// Compute llama.cpp serve profiles (Quality / Balanced / Speed) for `model`
/// against the detected hardware on `host` (or local). Returns concrete flags
/// (`n_gpu_layers`, `n_cpu_moe`, `cache_type`, `ctx`) the serve UI can apply.
///
/// `model` is matched against the catalog by name; if not found the response
/// carries `"profiles": []` and an error message.
async fn get_serve_profiles(
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let model_name = query_str(&q, "model");
    let host = query_str(&q, "host");
    let ssh_port = query_str(&q, "ssh_port");
    let platform = query_str(&q, "platform");
    let fresh = query_bool(&q, "fresh", false)?;
    // `serve_weights_gb: float = 0.0` — FastAPI coerces; we mimic with a float
    // query param defaulting to 0.0.  A missing or empty value means 0.0.
    let serve_weights_gb: f64 = q
        .get("serve_weights_gb")
        .and_then(|s| s.trim().parse::<f64>().ok())
        .unwrap_or(0.0);
    let serve_quant = query_str(&q, "serve_quant");

    let system = hardware::detect_system(&host, &ssh_port, &platform, fresh);
    // if system.get("error"): return {"system": system, "profiles": [], "error": ...}
    if let Some(err) = get(&system, "error") {
        if !err.is_null() {
            let err = err.clone();
            return Ok(Json(json!({
                "system": system,
                "profiles": [],
                "error": err,
            }))
            .into_response());
        }
    }

    // catalog = {m.get("name"): m for m in (get_models() or [])}
    let catalog_list = models::get_models();

    // _norm(s) — normalize for matching: drop org/ prefix, trailing -GGUF marker,
    // and any quant tag, lowercase.
    let norm = |s: &str| -> String {
        let s = s.to_lowercase();
        // s.split("/")[-1]  — drop org prefix
        let s = s.split('/').next_back().unwrap_or(&s).to_string();
        // re.sub(r"[-_.]?gguf$", "", s)
        let s = if let Some(stripped) = s.strip_suffix("gguf") {
            let stripped = stripped.trim_end_matches(['-', '_', '.']);
            stripped.to_string()
        } else {
            s
        };
        // re.sub(r"[-_.](q\d[^/]*|iq\d[^/]*|fp8|bf16|f16|awq[^/]*|gptq[^/]*)$", "", s)
        // Simplified: strip a trailing segment after [-_.] that looks like a quant tag.
        let quant_suffixes = ["fp8", "bf16", "f16"];
        let mut result = s.clone();
        for sfx in &quant_suffixes {
            for sep in &['-', '_', '.'] {
                let candidate = format!("{}{}", sep, sfx);
                if result.ends_with(&candidate) {
                    result = result[..result.len() - candidate.len()].to_string();
                    break;
                }
            }
        }
        // q\d... / iq\d... / awq... / gptq... patterns
        // Walk backwards from the end, find last [-_.](q<digit>...|iq<digit>...|awq...|gptq...)
        for sep in &['-', '_', '.'] {
            let sep_char = *sep;
            if let Some(pos) = result.rfind(sep_char) {
                let tail = &result[pos + 1..];
                let is_quant = (tail.starts_with('q') && tail.len() > 1 && tail.chars().nth(1).is_some_and(|c| c.is_ascii_digit()))
                    || (tail.starts_with("iq") && tail.len() > 2 && tail.chars().nth(2).is_some_and(|c| c.is_ascii_digit()))
                    || tail.starts_with("awq")
                    || tail.starts_with("gptq");
                if is_quant {
                    result = result[..pos].to_string();
                }
            }
        }
        result
    };

    // Find the model in the catalog, with fuzzy name matching.
    let mut found_model: Option<Value> = None;
    // Exact key lookup first.
    for entry in &catalog_list {
        if let Some(name) = entry.get("name").and_then(Value::as_str) {
            if name == model_name {
                found_model = Some(entry.clone());
                break;
            }
        }
    }
    // Fuzzy match: normalized names.
    if found_model.is_none() && !model_name.is_empty() {
        let want = norm(&model_name);
        for entry in &catalog_list {
            if let Some(name) = entry.get("name").and_then(Value::as_str) {
                let nn = norm(name);
                if !nn.is_empty()
                    && (nn == want || want.ends_with(&nn) || nn.ends_with(&want))
                {
                    found_model = Some(entry.clone());
                    break;
                }
            }
        }
    }

    let m = match found_model {
        Some(m) => m,
        None => {
            return Ok(Json(json!({
                "system": system,
                "profiles": [],
                "error": "model not in catalog",
            }))
            .into_response());
        }
    };

    // Surface the model's trained context limit so the serve UI can clamp a
    // user-typed context down to it.
    let mut model_ctx_max: i64 = 0;
    for k in ["context_length", "max_position_embeddings", "n_ctx_train", "context"] {
        if let Some(v) = m.get(k).and_then(Value::as_f64) {
            if v > 0.0 {
                model_ctx_max = v as i64;
                break;
            }
        }
    }

    // serve_weights_gb=serve_weights_gb or None, serve_quant=serve_quant or None
    let swg = if serve_weights_gb > 0.0 { Some(serve_weights_gb) } else { None };
    let sq = if serve_quant.is_empty() { None } else { Some(serve_quant.as_str()) };

    Ok(Json(json!({
        "system": system,
        "profiles": profiles::compute_serve_profiles(&system, &m, swg, sq),
        "model_ctx_max": model_ctx_max,
    }))
    .into_response())
}

// ---------------------------------------------------------------------------
// Router factory (`setup_hwfit_routes()` -> `APIRouter(prefix="/api/hwfit")`)
// ---------------------------------------------------------------------------

/// `setup_hwfit_routes()` — the FastAPI `APIRouter(prefix="/api/hwfit",
/// tags=["hwfit"])` factory, ported to an axum `Router<AppState>`.
///
/// The Python factory takes no deps (the handlers import the `hwfit` service
/// lazily); the axum factory likewise takes nothing and reads no `AppState`
/// field — `Router<AppState>` only so it merges into the shared aggregator.
/// The four routes mount at the absolute paths `prefix + subpath`.
pub fn setup_hwfit_routes() -> axum::Router<AppState> {
    use axum::routing::get;
    axum::Router::new()
        .route("/api/hwfit/system", get(get_system))
        .route("/api/hwfit/models", get(get_models))
        .route("/api/hwfit/profiles", get(get_serve_profiles))
        .route("/api/hwfit/image-models", get(get_image_models))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn qmap(pairs: &[(&str, &str)]) -> HashMap<String, String> {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    // --- query_bool: Pydantic v2 lax bool coercion --------------------------

    #[test]
    fn query_bool_missing_returns_default() {
        let q = qmap(&[]);
        assert!(!query_bool(&q, "fresh", false).unwrap());
        assert!(query_bool(&q, "fresh", true).unwrap());
    }

    #[test]
    fn query_bool_truthy_and_falsy_sets() {
        for t in ["1", "on", "t", "true", "y", "yes", "TRUE", "Yes", " on "] {
            let q = qmap(&[("fresh", t)]);
            assert!(query_bool(&q, "fresh", false).unwrap(), "{t:?} should be true");
        }
        for f in ["0", "off", "f", "false", "n", "no", "FALSE", "No"] {
            let q = qmap(&[("fresh", f)]);
            assert!(!query_bool(&q, "fresh", true).unwrap(), "{f:?} should be false");
        }
    }

    #[test]
    fn query_bool_invalid_is_422() {
        let q = qmap(&[("fresh", "maybe")]);
        let err = query_bool(&q, "fresh", false).unwrap_err();
        assert_eq!(err.status_code, 422);
        let body = err.detail_json.unwrap();
        assert_eq!(body[0]["type"], "bool_parsing");
        assert_eq!(body[0]["loc"], json!(["query", "fresh"]));
        assert_eq!(body[0]["input"], "maybe");
    }

    // --- query_int ----------------------------------------------------------

    #[test]
    fn query_int_default_and_parse() {
        assert_eq!(query_int(&qmap(&[]), "limit", 50).unwrap(), 50);
        assert_eq!(query_int(&qmap(&[("limit", "12")]), "limit", 50).unwrap(), 12);
        assert_eq!(query_int(&qmap(&[("limit", " 7 ")]), "limit", 50).unwrap(), 7);
    }

    #[test]
    fn query_int_invalid_is_422() {
        let err = query_int(&qmap(&[("limit", "lots")]), "limit", 50).unwrap_err();
        assert_eq!(err.status_code, 422);
        assert_eq!(err.detail_json.unwrap()[0]["type"], "int_parsing");
    }

    // --- _apply_manual_hardware ---------------------------------------------

    #[test]
    fn manual_hardware_noop_unless_gpu_or_ram() {
        let mut sys = json!({"has_gpu": true, "gpu_count": 2});
        apply_manual_hardware(&mut sys, "", "", "", "", "");
        // unchanged — no manual_hardware key added
        assert!(sys.get("manual_hardware").is_none());
        apply_manual_hardware(&mut sys, "nonsense", "", "", "", "");
        assert!(sys.get("manual_hardware").is_none());
    }

    #[test]
    fn manual_hardware_ram_mode_wipes_gpu() {
        let mut sys = json!({"has_gpu": true, "gpu_count": 4, "gpu_vram_gb": 80.0});
        apply_manual_hardware(&mut sys, "ram", "", "", "128", "");
        assert_eq!(sys["manual_hardware"], json!(true));
        assert_eq!(sys["available_ram_gb"], json!(128.0));
        assert_eq!(sys["total_ram_gb"], json!(128.0));
        assert_eq!(sys["has_gpu"], json!(false));
        assert_eq!(sys["gpu_name"], Value::Null);
        assert_eq!(sys["gpu_vram_gb"], json!(0.0));
        assert_eq!(sys["gpu_count"], json!(0));
        assert_eq!(sys["gpus"], json!([]));
        assert_eq!(sys["gpu_groups"], json!([]));
        assert_eq!(sys["backend"], json!("cpu_x86"));
    }

    #[test]
    fn manual_hardware_gpu_mode_builds_homogeneous_pool() {
        let mut sys = json!({"backend": "rocm"});
        // manual_mode=gpu, count=3, vram_each=40, backend default-from-system=rocm
        apply_manual_hardware(&mut sys, "gpu", "3", "40", "", "");
        assert_eq!(sys["has_gpu"], json!(true));
        assert_eq!(sys["gpu_name"], json!("Simulated ROCM GPU × 3"));
        assert_eq!(sys["gpu_vram_gb"], json!(120.0));
        assert_eq!(sys["gpu_count"], json!(3));
        assert_eq!(sys["homogeneous"], json!(true));
        assert_eq!(sys["backend"], json!("rocm"));
        let gpus = sys["gpus"].as_array().unwrap();
        assert_eq!(gpus.len(), 3);
        assert_eq!(gpus[0], json!({"index": 0, "name": "Simulated ROCM GPU × 3", "vram_gb": 40.0}));
        let grp = &sys["gpu_groups"][0];
        assert_eq!(grp["name"], json!("Simulated ROCM GPU × 3"));
        assert_eq!(grp["vram_each"], json!(40.0));
        assert_eq!(grp["count"], json!(3));
        assert_eq!(grp["indices"], json!([0, 1, 2]));
        assert_eq!(grp["vram_total"], json!(120.0));
    }

    #[test]
    fn manual_hardware_gpu_count_clamped_and_singular_name() {
        let mut sys = json!({});
        // count=99 -> clamp to 16; bad backend -> cuda; count=1 default if empty
        apply_manual_hardware(&mut sys, "gpu", "99", "", "", "tpu");
        assert_eq!(sys["gpu_count"], json!(16));
        assert_eq!(sys["backend"], json!("cuda"));
        // count=1 path -> no " × N" suffix
        let mut sys1 = json!({});
        apply_manual_hardware(&mut sys1, "gpu", "1", "12", "", "cuda");
        assert_eq!(sys1["gpu_name"], json!("Simulated CUDA GPU"));
        assert_eq!(sys1["gpu_count"], json!(1));
        assert_eq!(sys1["gpu_vram_gb"], json!(12.0));
    }

    #[test]
    fn manual_hardware_invalid_numeric_falls_back() {
        let mut sys = json!({});
        // non-numeric count -> 1, non-numeric vram -> 8.0, non-numeric ram -> 0 (no RAM set)
        apply_manual_hardware(&mut sys, "gpu", "abc", "xyz", "junk", "cuda");
        assert_eq!(sys["gpu_count"], json!(1));
        assert_eq!(sys["gpu_vram_gb"], json!(8.0));
        assert!(sys.get("available_ram_gb").is_none());
    }

    // --- _apply_group -------------------------------------------------------

    #[test]
    fn apply_group_pins_pool_and_records_active() {
        let mut sys = json!({});
        let g = json!({"name": "A100", "vram_each": 80.0, "count": 4, "indices": [0,1,2,3], "vram_total": 320.0});
        apply_group(&mut sys, &g, 2);
        assert_eq!(sys["gpu_count"], json!(2));
        assert_eq!(sys["gpu_vram_gb"], json!(160.0));
        assert_eq!(sys["gpu_name"], json!("A100"));
        // active_group = {**g, "use_count": 2}
        assert_eq!(sys["active_group"]["count"], json!(4));
        assert_eq!(sys["active_group"]["use_count"], json!(2));
    }

    #[test]
    fn apply_group_clamps_to_pool_count() {
        let mut sys = json!({});
        let g = json!({"name": "X", "vram_each": 24.0, "count": 2});
        apply_group(&mut sys, &g, 10); // min(10, 2) -> 2
        assert_eq!(sys["gpu_count"], json!(2));
        apply_group(&mut sys, &g, 0); // max(1, min(0,2)) -> 1
        assert_eq!(sys["gpu_count"], json!(1));
    }

    // --- round1 (banker's rounding) -----------------------------------------

    #[test]
    fn round1_is_half_to_even() {
        assert_eq!(round1(2.25), 2.2); // .x5 ties round to even
        assert_eq!(round1(2.35), 2.4);
        assert_eq!(round1(120.04), 120.0);
        assert_eq!(round1(120.06), 120.1);
    }

    // --- none_if_empty ------------------------------------------------------

    #[test]
    fn none_if_empty_maps_blank_to_none() {
        assert_eq!(none_if_empty(""), None);
        assert_eq!(none_if_empty("score"), Some("score"));
    }

    // --- metal backend: unified_memory set/pop ------------------------------

    #[test]
    fn metal_backend_sets_unified_memory() {
        let mut sys = json!({"backend": "cuda"});
        // Simulate a metal GPU override.
        apply_manual_hardware(&mut sys, "gpu", "1", "16", "", "metal");
        assert_eq!(sys["backend"], json!("metal"));
        assert_eq!(sys["unified_memory"], json!(true));
    }

    #[test]
    fn non_metal_backend_pops_unified_memory() {
        // Start with a sys that already has unified_memory (e.g. real Metal
        // detection), then simulate a cuda GPU — the flag must be cleared.
        let mut sys = json!({"unified_memory": true, "backend": "metal"});
        apply_manual_hardware(&mut sys, "gpu", "1", "16", "", "cuda");
        assert_eq!(sys["backend"], json!("cuda"));
        assert!(sys.get("unified_memory").is_none() || sys["unified_memory"].is_null(),
            "unified_memory should be absent for cuda backend");
    }

    #[test]
    fn ram_mode_pops_unified_memory() {
        // RAM mode should also clear any stale unified_memory flag.
        let mut sys = json!({"unified_memory": true, "has_gpu": true, "gpu_count": 1});
        apply_manual_hardware(&mut sys, "ram", "", "", "64", "");
        assert_eq!(sys["backend"], json!("cpu_x86"));
        assert!(sys.get("unified_memory").is_none() || sys["unified_memory"].is_null(),
            "unified_memory should be absent in RAM mode");
    }

    #[test]
    fn metal_is_in_valid_backends() {
        // A system with no detected backend should accept "metal" without
        // falling back to "cuda".
        let mut sys = json!({});
        apply_manual_hardware(&mut sys, "gpu", "1", "32", "", "metal");
        assert_eq!(sys["backend"], json!("metal"));
        assert_eq!(sys["has_gpu"], json!(true));
    }

    #[test]
    fn invalid_backend_still_falls_back_to_cuda() {
        let mut sys = json!({});
        apply_manual_hardware(&mut sys, "gpu", "1", "8", "", "tpu");
        assert_eq!(sys["backend"], json!("cuda"));
        // No unified_memory for cuda fallback.
        assert!(sys.get("unified_memory").is_none() || sys["unified_memory"].is_null());
    }

    // --- ctx clamping and fit_only ------------------------------------------

    #[test]
    // The test inlines the clamp logic with string literals to exercise both the
    // empty and non-empty branches; the literal `.is_empty()` checks are const.
    #[allow(clippy::const_is_empty)]
    fn target_context_clamped_to_bounds() {
        // ctx < 1024 -> clamp to 1024
        let low: Option<i64> = if "500".is_empty() {
            None
        } else {
            "500".trim().parse::<i64>().ok().map(|v| v.clamp(1024, 1_000_000))
        };
        assert_eq!(low, Some(1024));

        // ctx > 1_000_000 -> clamp to 1_000_000
        let high: Option<i64> = "9999999".trim().parse::<i64>().ok().map(|v| v.clamp(1024, 1_000_000));
        assert_eq!(high, Some(1_000_000));

        // ctx in range -> unchanged
        let mid: Option<i64> = "32768".trim().parse::<i64>().ok().map(|v| v.clamp(1024, 1_000_000));
        assert_eq!(mid, Some(32768));

        // empty ctx -> None
        let empty: Option<i64> = if "".is_empty() {
            None
        } else {
            "".trim().parse::<i64>().ok().map(|v| v.clamp(1024, 1_000_000))
        };
        assert_eq!(empty, None);

        // non-numeric ctx -> None
        let bad: Option<i64> = "foo".trim().parse::<i64>().ok().map(|v| v.clamp(1024, 1_000_000));
        assert_eq!(bad, None);
    }
}
