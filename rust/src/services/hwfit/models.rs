// services/hwfit/models.rs  <- services/hwfit/models.py
//! Quantization math + the Hugging Face model catalog loader.
//!
//! Pure number-crunching (no async/HTTP/DB), so this builds on the default
//! profile. Catalog rows arrive from `data/hf_models.json` as untyped JSON
//! objects; mirroring the Python's `model.get(key, default)` duck-typing, a
//! model here is a [`serde_json::Value`] and the `model_*` free functions are
//! the loose `.get()` accessors shared with [`super::fit`] and
//! [`super::image_models`].

use crate::pylog as logger;
use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Mutex;

/// `QUANT_HIERARCHY` — GGUF quants from highest to lowest quality.
///
/// Held as a `Lazy<Vec<&str>>` (not a fixed array) so callers can use Python
/// list semantics on it — `&*QUANT_HIERARCHY` to borrow the slice,
/// `.iter().position(...)` for `.index()`, and `[idx + 1..]` slicing — exactly
/// as `fit.py` does (`QUANT_HIERARCHY.index(target)` + `QUANT_HIERARCHY[idx+1:]`).
pub static QUANT_HIERARCHY: Lazy<Vec<&'static str>> =
    Lazy::new(|| vec!["Q8_0", "Q6_K", "Q5_K_M", "Q4_K_M", "Q3_K_M", "Q2_K"]);

/// `QUANT_BPP` — bytes-per-param multiplier used by `estimate_memory_gb`.
pub static QUANT_BPP: Lazy<HashMap<&'static str, f64>> = Lazy::new(|| {
    HashMap::from([
        ("F32", 4.0),
        ("F16", 2.0),
        ("BF16", 2.0),
        ("FP8", 1.0),
        ("FP4", 0.50),
        ("NVFP4", 0.50),
        ("MXFP4", 0.50),
        ("NF4", 0.50),
        ("INT4", 0.50),
        ("INT8", 1.0),
        ("W4A16", 0.50),
        ("W8A8", 1.0),
        ("W8A16", 1.0),
        ("Q8_0", 1.05),
        ("Q6_K", 0.80),
        ("Q5_K_M", 0.68),
        ("Q4_K_M", 0.58),
        ("Q4_0", 0.58),
        ("Q3_K_M", 0.48),
        ("Q2_K", 0.37),
        ("AWQ-4bit", 0.50),
        ("AWQ-8bit", 1.0),
        ("GPTQ-Int4", 0.50),
        ("GPTQ-Int8", 1.0),
        ("mlx-4bit", 0.55),
        ("mlx-8bit", 1.0),
        ("mlx-6bit", 0.75),
        // DeepSeek-V4-style mixed: MoE experts in FP4 (bulk), attention +
        // non-expert dense in FP8, embeddings/LM head in BF16. Effective
        // BPP ≈ 0.55 B/param (empirical: DeepSeek-V4-Flash 284B / 156 GB).
        ("FP4-MoE-Mixed", 0.55),
        // FP8-Mixed = the *-Base variants (MoE experts also FP8, not FP4).
        ("FP8-Mixed", 1.0),
    ])
});

/// `QUANT_SPEED_MULT` — relative tok/s multiplier per quant (consumed by
/// [`super::fit`]).
pub static QUANT_SPEED_MULT: Lazy<HashMap<&'static str, f64>> = Lazy::new(|| {
    HashMap::from([
        ("F16", 0.6),
        ("BF16", 0.6),
        ("FP8", 0.85),
        ("FP4", 1.15),
        ("NVFP4", 1.15),
        ("MXFP4", 1.15),
        ("NF4", 1.10),
        ("INT4", 1.15),
        ("INT8", 0.85),
        ("W4A16", 1.15),
        ("W8A8", 0.85),
        ("W8A16", 0.85),
        ("Q8_0", 0.8),
        ("Q6_K", 0.95),
        ("Q5_K_M", 1.0),
        ("Q4_K_M", 1.15),
        ("Q4_0", 1.15),
        ("Q3_K_M", 1.25),
        ("Q2_K", 1.35),
        ("AWQ-4bit", 1.2),
        ("AWQ-8bit", 0.85),
        ("GPTQ-Int4", 1.2),
        ("GPTQ-Int8", 0.85),
        ("mlx-4bit", 1.15),
        ("mlx-8bit", 0.85),
        ("mlx-6bit", 1.0),
        // slightly slower than pure FP4 because of mixed-dtype dispatch
        ("FP4-MoE-Mixed", 1.10),
        ("FP8-Mixed", 0.85),
    ])
});

/// `QUANT_QUALITY_PENALTY` — quality delta (always <= 0) per quant.
pub static QUANT_QUALITY_PENALTY: Lazy<HashMap<&'static str, f64>> = Lazy::new(|| {
    HashMap::from([
        ("F16", 0.0),
        ("BF16", 0.0),
        ("FP8", 0.0),
        ("FP4", -3.0),
        ("NVFP4", -3.0),
        ("MXFP4", -3.0),
        ("NF4", -4.0),
        ("INT4", -4.0),
        ("INT8", 0.0),
        ("W4A16", -4.0),
        ("W8A8", 0.0),
        ("W8A16", 0.0),
        ("Q8_0", 0.0),
        ("Q6_K", -1.0),
        ("Q5_K_M", -2.0),
        ("Q4_K_M", -5.0),
        ("Q4_0", -5.0),
        ("Q3_K_M", -8.0),
        ("Q2_K", -12.0),
        // AWQ/GPTQ: calibrated reconstruction, not raw weights — small but real
        // quality loss vs FP8. Give them a slight penalty so FP8 wins when both
        // fit. The bare "AWQ"/"GPTQ" keys (as from infer_quantization_from_name)
        // receive the same penalty as the -8bit variants.
        ("AWQ", -1.0),
        ("AWQ-4bit", -4.0),
        ("AWQ-8bit", -1.0),
        ("GPTQ", -1.0),
        ("GPTQ-Int4", -4.0),
        ("GPTQ-Int8", -1.0),
        ("mlx-4bit", -4.0),
        ("mlx-8bit", -0.5),
        ("mlx-6bit", -1.5),
        // DeepSeek-V4 mixed: only MoE experts at FP4 (the rest is FP8/BF16),
        // so realized quality is much closer to FP8 than to pure FP4.
        ("FP4-MoE-Mixed", -0.5),
        ("FP8-Mixed", 0.0),
    ])
});

/// `QUANT_BYTES_PER_PARAM` — bytes/param table (consumed by [`super::fit`]).
pub static QUANT_BYTES_PER_PARAM: Lazy<HashMap<&'static str, f64>> = Lazy::new(|| {
    HashMap::from([
        ("F16", 2.0),
        ("BF16", 2.0),
        ("FP8", 1.0),
        ("FP4", 0.5),
        ("NVFP4", 0.5),
        ("MXFP4", 0.5),
        ("NF4", 0.5),
        ("INT4", 0.5),
        ("INT8", 1.0),
        ("W4A16", 0.5),
        ("W8A8", 1.0),
        ("W8A16", 1.0),
        ("Q8_0", 1.0),
        ("Q6_K", 0.75),
        ("Q5_K_M", 0.625),
        ("Q4_K_M", 0.5),
        ("Q4_0", 0.5),
        ("Q3_K_M", 0.375),
        ("Q2_K", 0.25),
        ("AWQ-4bit", 0.5),
        ("AWQ-8bit", 1.0),
        ("GPTQ-Int4", 0.5),
        ("GPTQ-Int8", 1.0),
        ("mlx-4bit", 0.5),
        ("mlx-8bit", 1.0),
        ("mlx-6bit", 0.75),
        ("FP4-MoE-Mixed", 0.55),
        ("FP8-Mixed", 1.0),
    ])
});

/// `PREQUANTIZED_PREFIXES` — formats that should NOT go through the GGUF quant
/// hierarchy. These are native HF/vLLM-style repos, not llama.cpp GGUF quant
/// tiers.
pub static PREQUANTIZED_PREFIXES: [&str; 13] = [
    "AWQ-", "GPTQ-", "mlx-", "FP8", "FP4", "NVFP4", "MXFP4", "NF4",
    "INT4", "INT8", "W4A16", "W8A8", "W8A16",
];

// ---------------------------------------------------------------------------
// Value-backed model accessors (the Python `model.get(...)` duck-typing).
// ---------------------------------------------------------------------------

/// `model.get(key, "")` for a string field. Mirrors Python's loose `.get`: a
/// missing/non-string value yields the empty string.
pub fn model_str(model: &Value, key: &str) -> String {
    match model.get(key) {
        Some(Value::String(s)) => s.clone(),
        _ => String::new(),
    }
}

/// `model.get(key)` for a numeric field, as an `f64` (or `None`).
pub fn model_f64(model: &Value, key: &str) -> Option<f64> {
    model.get(key).and_then(Value::as_f64)
}

/// `model.get(key)` truthiness — Python treats `False`/`0`/`""`/`null`/missing
/// as falsy. Used for `is_moe` / `active_parameters` gating.
pub fn model_truthy(model: &Value, key: &str) -> bool {
    match model.get(key) {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().map(|v| v != 0.0).unwrap_or(false),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// `infer_quantization_from_name(name)` — infer the quantization type from a
/// model name string. Returns an empty string when nothing is detected.
/// Mirrors the Python function in `models.py` exactly, including the regex
/// word-boundary checks for NF4, FP4, W4A16, W8A8, W8A16.
pub fn infer_quantization_from_name(name: &str) -> &'static str {
    static NF4_RE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"(^|[-_/])nf4($|[-_/])").unwrap());
    static FP4_RE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"(^|[-_/])fp4($|[-_/])").unwrap());
    static W4A16_RE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"(^|[-_/])w4a16($|[-_/])").unwrap());
    static W8A8_RE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"(^|[-_/])w8a8($|[-_/])").unwrap());
    static W8A16_RE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"(^|[-_/])w8a16($|[-_/])").unwrap());

    let n = name.to_lowercase();
    let n = n.as_str();

    if n.contains("nvfp4") {
        return "NVFP4";
    }
    if n.contains("mxfp4") {
        return "MXFP4";
    }
    if NF4_RE.is_match(n) {
        return "NF4";
    }
    if FP4_RE.is_match(n) {
        return "FP4";
    }
    if W4A16_RE.is_match(n) {
        return "W4A16";
    }
    if W8A8_RE.is_match(n) {
        return "W8A8";
    }
    if W8A16_RE.is_match(n) {
        return "W8A16";
    }

    let is8 = n.contains("8bit") || n.contains("8-bit") || n.contains("int8");

    if n.contains("awq") {
        return if is8 { "AWQ-8bit" } else { "AWQ-4bit" };
    }
    if n.contains("gptq") {
        return if is8 { "GPTQ-Int8" } else { "GPTQ-Int4" };
    }
    if n.contains("mlx") {
        if n.contains("6bit") {
            return "mlx-6bit";
        }
        return if is8 { "mlx-8bit" } else { "mlx-4bit" };
    }
    if n.contains("fp8") {
        return "FP8";
    }
    if n.contains("int4") || n.contains("4bit") || n.contains("4-bit") {
        return "INT4";
    }
    if n.contains("int8") || n.contains("8bit") || n.contains("8-bit") {
        return "INT8";
    }
    ""
}

/// `_normalize_model_entry(model)` — apply `infer_quantization_from_name` to
/// backfill or correct the `quantization` field on newly-discovered catalog
/// entries (mirrors Python `_normalize_model_entry`). Mutates the `Value` in
/// place and returns it (caller passes `mut` ownership).
///
/// The Python condition is:
///   `if inferred and (model.get("quantization") in (None, "", "Q4_K_M") or model.get("_discovered"))`
pub fn normalize_model_entry(model: Value) -> Value {
    let mut model = model;
    if !model.is_object() {
        return model;
    }
    let name = model_str(&model, "name");
    let inferred = infer_quantization_from_name(&name);
    if inferred.is_empty() {
        return model;
    }
    let current_q = model_str(&model, "quantization");
    let discovered = model_truthy(&model, "_discovered");
    let should_overwrite = matches!(current_q.as_str(), "" | "Q4_K_M") || discovered;
    if should_overwrite {
        if let Some(obj) = model.as_object_mut() {
            obj.insert(
                "quantization".to_string(),
                Value::String(inferred.to_string()),
            );
        }
    }
    model
}

/// `is_prequantized(model)` — mirrors the Python function exactly. Checks both
/// the `quantization` field (prefix match against `PREQUANTIZED_PREFIXES`) and
/// the model name + format text for well-known markers (nvfp4, fp8, 8bit,
/// awq, gptq, mlx).
pub fn is_prequantized(model: &Value) -> bool {
    let q = model_str(model, "quantization");
    let name = model_str(model, "name").to_lowercase();
    let fmt = model_str(model, "format").to_lowercase();
    let text = format!("{} {}", name, fmt);

    // regex used by the Python for fp8 and 8bit checks (compiled once)
    static FP8_RE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"(^|[-_/])fp8($|[-_/\s])").unwrap());
    static EIGHTBIT_RE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"(^|[-_/])(?:int)?8bit($|[-_/\s])").unwrap());

    text.contains("nvfp4")
        || FP8_RE.is_match(&text)
        || (!model_truthy(model, "is_gguf")
            && !model_truthy(model, "gguf_sources")
            && EIGHTBIT_RE.is_match(&text))
        || ["awq", "gptq", "mlx"]
            .iter()
            .any(|x| text.contains(x))
        || PREQUANTIZED_PREFIXES.iter().any(|p| q.starts_with(p))
}

/// `re.match(r"^([\d.]+)\s*([BKMGT]?)$", pc)` — precompiled once.
static PARAMS_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^([\d.]+)\s*([BKMGT]?)$").unwrap());

/// `params_b(model)` — the model's parameter count in billions.
// Mirrors the Python bare-number heuristic at models.py:78-82 1:1: the
// `val >= 1000` and the final `else` branch both divide by 1000.0 under distinct
// conditions (the rationale comments differ) — the duplicate body is deliberate.
#[allow(clippy::if_same_then_else)]
pub fn params_b(model: &Value) -> f64 {
    // `raw = model.get("parameters_raw"); if raw and raw > 0:`
    if let Some(raw) = model_f64(model, "parameters_raw") {
        if raw > 0.0 {
            return raw / 1_000_000_000.0;
        }
    }

    let pc = model_str(model, "parameter_count");
    if !pc.is_empty() {
        // `pc = pc.strip().upper()`
        let pc = pc.trim().to_uppercase();
        if let Some(m) = PARAMS_RE.captures(&pc) {
            // `val = float(m.group(1))` — group 1 is `[\d.]+`, always present.
            let val: f64 = m.get(1).map(|g| g.as_str()).unwrap_or("").parse().unwrap_or(0.0);
            let suffix = m.get(2).map(|g| g.as_str()).unwrap_or("");
            return match suffix {
                "B" => val,
                "M" => val / 1000.0,
                "K" => val / 1_000_000.0,
                "T" => val * 1000.0,
                _ => {
                    // No unit. A bare number this size is conventionally a
                    // millions count (e.g. "355" = 355M), NOT billions —
                    // otherwise a 355M model would sort as 355B and leap above
                    // every 7B/70B model. A genuine billions figure carries a
                    // "B" suffix and is handled above; very large bare values
                    // are raw parameter counts.
                    if val >= 1_000_000.0 {
                        val / 1_000_000_000.0 // raw count
                    } else if val >= 1000.0 {
                        val / 1000.0 // thousands of millions? treat as millions
                    } else {
                        val / 1000.0 // e.g. "355" -> 0.355B
                    }
                }
            };
        }
    }
    0.0
}

/// `estimate_memory_gb(model, quant, ctx)` — estimate VRAM needed to serve a
/// model. All weights must be loaded, even for MoE (all experts live in memory,
/// only active ones compute per token). KV cache scales with active params for
/// MoE (only active experts have KV state).
pub fn estimate_memory_gb(model: &Value, quant: &str, ctx: f64) -> f64 {
    let pb = params_b(model);
    let bpp = QUANT_BPP.get(quant).copied().unwrap_or(0.58);
    let kv_params = _active_params_b(model);
    pb * bpp + 0.000008 * kv_params * ctx + 0.5
}

/// `_active_params_b(model)` — for MoE: active params per token (affects KV
/// cache and speed, not total VRAM). For dense: same as total params.
pub fn _active_params_b(model: &Value) -> f64 {
    if model_truthy(model, "is_moe") && model_truthy(model, "active_parameters") {
        // `model["active_parameters"] / 1e9` — guaranteed numeric by the
        // truthiness gate above; `0.0` only if it were a non-number, which the
        // gate already excludes.
        return model_f64(model, "active_parameters").unwrap_or(0.0) / 1_000_000_000.0;
    }
    params_b(model)
}

/// `best_quant_for_budget(model, budget_gb, ctx)` — find the best quant that
/// fits in `budget_gb` of VRAM. Pre-quantized models (AWQ/GPTQ/MLX) use their
/// native quant only. Returns `(quant, ctx, mem_gb)` or `None`.
///
/// Mirrors the Python's `(None, None, None)` triple as a single [`Option`] over
/// the populated tuple. `ctx` is carried as the Python integer context window
/// (an `i64`); `cur_ctx //= 2` is integer floor division.
pub fn best_quant_for_budget(model: &Value, budget_gb: f64, ctx: i64) -> Option<(String, i64, f64)> {
    if is_prequantized(model) {
        // `q = model.get("quantization", "Q4_K_M")`
        let q = match model.get("quantization") {
            Some(Value::String(s)) => s.clone(),
            _ => "Q4_K_M".to_string(),
        };
        let mem = estimate_memory_gb(model, &q, ctx as f64);
        if mem <= budget_gb {
            return Some((q, ctx, mem));
        }
        // Try halving context.
        let mut cur_ctx = ctx / 2;
        while cur_ctx >= 1024 {
            let mem = estimate_memory_gb(model, &q, cur_ctx as f64);
            if mem <= budget_gb {
                return Some((q, cur_ctx, mem));
            }
            cur_ctx /= 2;
        }
        return None;
    }

    // GGUF: try best quality first, then fall back.
    for &q in QUANT_HIERARCHY.iter() {
        let mem = estimate_memory_gb(model, q, ctx as f64);
        if mem <= budget_gb {
            return Some((q.to_string(), ctx, mem));
        }
    }

    let mut cur_ctx = ctx / 2;
    while cur_ctx >= 1024 {
        for &q in QUANT_HIERARCHY.iter() {
            let mem = estimate_memory_gb(model, q, cur_ctx as f64);
            if mem <= budget_gb {
                return Some((q.to_string(), cur_ctx, mem));
            }
        }
        cur_ctx /= 2;
    }

    None
}

/// `infer_use_case(model)` — classify the model from its name + declared use
/// case. **Order matters**: the first matching bucket wins.
pub fn infer_use_case(model: &Value) -> &'static str {
    let name = model_str(model, "name").to_lowercase();
    let uc = model_str(model, "use_case").to_lowercase();
    let combined = format!("{} {}", name, uc);
    let has = |keys: &[&str]| keys.iter().any(|k| combined.contains(k));

    if has(&["embedding", "embed", "bge"]) {
        return "embedding";
    }
    if has(&["tts", "text-to-speech", "speech-synthesis", "cosyvoice", "parler"]) {
        return "tts";
    }
    if has(&["stt", "speech-to-text", "whisper", "transcri", "asr"]) {
        return "stt";
    }
    if combined.contains("code") {
        return "coding";
    }
    if has(&["vision", "multimodal", "vlm", "vl-"]) {
        return "multimodal";
    }
    if has(&["reason", "chain-of-thought", "deepseek-r1"]) {
        return "reasoning";
    }
    if has(&["chat", "instruction"]) {
        return "chat";
    }
    "general"
}

/// `_models_cache` — the lazy in-memory catalog cache.
///
/// `Lazy<Mutex<Option<Vec<Value>>>>`: `None` until the first `get_models()`
/// load (which sets it to the parsed list, or `[]` on any error). Once cached,
/// subsequent calls return a clone of the stored list (the Python returns the
/// same list object; cloning is the faithful read-only analogue and the catalog
/// is small relative to the work the callers do).
static MODELS_CACHE: Lazy<Mutex<Option<Vec<Value>>>> = Lazy::new(|| Mutex::new(None));

/// `get_models()` — load and cache the model catalog from `data/hf_models.json`.
/// Returns `[]` on `FileNotFoundError`/`json.JSONDecodeError`.
pub fn get_models() -> Vec<Value> {
    let mut guard = MODELS_CACHE.lock().unwrap();
    if guard.is_none() {
        let data_path = model_catalog_path();
        let loaded: Vec<Value> = match std::fs::read_to_string(&data_path) {
            Ok(text) => match serde_json::from_str::<Value>(&text) {
                Ok(Value::Array(arr)) => arr.into_iter().map(normalize_model_entry).collect(),
                // A non-array top-level (or parse error) is not a list the
                // callers can iterate; Python would only get here on a valid
                // JSON non-list, which `rank_models`/`get_models()` truthiness
                // would treat as data. Faithful behavior: only an array is the
                // catalog; anything else is treated as the empty fallback.
                Ok(_) => Vec::new(),
                Err(e) => {
                    logger::debug(&format!("hwfit catalog parse error: {}", e));
                    Vec::new()
                }
            },
            Err(_) => Vec::new(),
        };
        *guard = Some(loaded);
    }
    guard.as_ref().map(|v| v.clone()).unwrap_or_default()
}

/// `model_catalog_path()` — the absolute path to `data/hf_models.json`.
///
/// Python computes `os.path.join(os.path.dirname(__file__), "data",
/// "hf_models.json")`, i.e. relative to `services/hwfit/`. The Rust crate's
/// `BASE_DIR` is the repository root (see `core::constants`), so the faithful
/// module-relative path is `<repo>/services/hwfit/data/hf_models.json` — the
/// very file the live Python app reads (the 476KB catalog stays in the Python
/// tree).
pub fn model_catalog_path() -> String {
    let base = &*crate::core::constants::BASE_DIR; // ".../<repo>/"
    crate::pyos::path::join(
        &crate::pyos::path::join(
            &crate::pyos::path::join(base, "services"),
            "hwfit",
        ),
        "data/hf_models.json",
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn params_b_raw_takes_priority() {
        let m = json!({"parameters_raw": 7_000_000_000i64, "parameter_count": "70B"});
        assert!((params_b(&m) - 7.0).abs() < 1e-9);
    }

    #[test]
    fn params_b_suffixes() {
        assert!((params_b(&json!({"parameter_count": "7B"})) - 7.0).abs() < 1e-9);
        assert!((params_b(&json!({"parameter_count": "70 B"})) - 70.0).abs() < 1e-9);
        assert!((params_b(&json!({"parameter_count": "355M"})) - 0.355).abs() < 1e-9);
        assert!((params_b(&json!({"parameter_count": "80K"})) - 0.00008).abs() < 1e-12);
        assert!((params_b(&json!({"parameter_count": "1.5T"})) - 1500.0).abs() < 1e-6);
    }

    #[test]
    fn params_b_bare_number_heuristic() {
        // "355" -> 0.355B (millions convention)
        assert!((params_b(&json!({"parameter_count": "355"})) - 0.355).abs() < 1e-9);
        // bare >= 1_000_000 -> raw count
        assert!((params_b(&json!({"parameter_count": "80074"})) - 0.0).abs() < 1e-9 || params_b(&json!({"parameter_count": "80074"})) == 80.074);
        // 80074 is < 1e6 and >= 1000 -> /1000 = 80.074
        assert!((params_b(&json!({"parameter_count": "80074"})) - 80.074).abs() < 1e-9);
        // a true raw count
        assert!((params_b(&json!({"parameter_count": "2000000"})) - 0.002).abs() < 1e-9);
    }

    #[test]
    fn params_b_falls_back_to_zero() {
        assert_eq!(params_b(&json!({})), 0.0);
        assert_eq!(params_b(&json!({"parameter_count": "not-a-number"})), 0.0);
        assert_eq!(params_b(&json!({"parameters_raw": 0})), 0.0);
    }

    #[test]
    fn is_prequantized_detects_prefixes() {
        // Via quantization prefix
        assert!(is_prequantized(&json!({"quantization": "AWQ-4bit"})));
        assert!(is_prequantized(&json!({"quantization": "FP8"})));
        assert!(is_prequantized(&json!({"quantization": "mlx-8bit"})));
        assert!(is_prequantized(&json!({"quantization": "INT4"})));
        assert!(is_prequantized(&json!({"quantization": "NF4"})));
        assert!(is_prequantized(&json!({"quantization": "W4A16"})));
        assert!(is_prequantized(&json!({"quantization": "FP4-MoE-Mixed"})));
        assert!(!is_prequantized(&json!({"quantization": "Q4_K_M"})));
        assert!(!is_prequantized(&json!({})));
    }

    #[test]
    fn is_prequantized_checks_name_and_format() {
        // nvfp4 in name
        assert!(is_prequantized(&json!({"name": "my-nvfp4-model"})));
        // fp8 in name via regex (name contains "-fp8" which is matched by [-_/] before fp8)
        assert!(is_prequantized(&json!({"name": "my-model-fp8"})));
        // format "fp8" alone: text = "my-model fp8" — space before "fp8" is not
        // in [-_/] and not ^, so the Python regex does NOT match here.
        assert!(!is_prequantized(&json!({"name": "my-model", "format": "fp8"})));
        // awq in name
        assert!(is_prequantized(&json!({"name": "my-awq-4bit-model"})));
        // gptq in name
        assert!(is_prequantized(&json!({"name": "model-gptq-int4"})));
        // mlx in format
        assert!(is_prequantized(&json!({"name": "model", "format": "mlx-4bit"})));
        // 8bit in name, not gguf
        assert!(is_prequantized(&json!({"name": "model-8bit"})));
        // 8bit in name but is_gguf=true -> not prequantized by that path
        assert!(!is_prequantized(&json!({"name": "model-8bit", "is_gguf": true})));
        // plain Q4_K_M with no markers -> not prequantized
        assert!(!is_prequantized(&json!({"name": "llama-3-q4_k_m"})));
    }

    #[test]
    fn infer_quantization_from_name_coverage() {
        assert_eq!(infer_quantization_from_name("my-nvfp4-model"), "NVFP4");
        assert_eq!(infer_quantization_from_name("some-mxfp4"), "MXFP4");
        assert_eq!(infer_quantization_from_name("model-nf4"), "NF4");
        assert_eq!(infer_quantization_from_name("model-fp4"), "FP4");
        assert_eq!(infer_quantization_from_name("model-w4a16"), "W4A16");
        assert_eq!(infer_quantization_from_name("model-w8a8"), "W8A8");
        assert_eq!(infer_quantization_from_name("model-w8a16"), "W8A16");
        assert_eq!(infer_quantization_from_name("my-awq-8bit"), "AWQ-8bit");
        assert_eq!(infer_quantization_from_name("my-awq-4bit"), "AWQ-4bit");
        assert_eq!(infer_quantization_from_name("gptq-int8"), "GPTQ-Int8");
        assert_eq!(infer_quantization_from_name("gptq-int4"), "GPTQ-Int4");
        assert_eq!(infer_quantization_from_name("my-mlx-6bit"), "mlx-6bit");
        assert_eq!(infer_quantization_from_name("my-mlx-8bit"), "mlx-8bit");
        assert_eq!(infer_quantization_from_name("my-mlx-model"), "mlx-4bit");
        assert_eq!(infer_quantization_from_name("fp8-model"), "FP8");
        assert_eq!(infer_quantization_from_name("model-int4"), "INT4");
        assert_eq!(infer_quantization_from_name("model-4bit"), "INT4");
        assert_eq!(infer_quantization_from_name("model-int8"), "INT8");
        assert_eq!(infer_quantization_from_name("plain-llama"), "");
        assert_eq!(infer_quantization_from_name(""), "");
    }

    #[test]
    fn normalize_model_entry_backfills_quantization() {
        // No quantization set -> inferred from name
        let m = normalize_model_entry(json!({"name": "my-awq-4bit-model"}));
        assert_eq!(model_str(&m, "quantization"), "AWQ-4bit");

        // quantization = "" -> backfill
        let m = normalize_model_entry(json!({"name": "my-gptq-int4-model", "quantization": ""}));
        assert_eq!(model_str(&m, "quantization"), "GPTQ-Int4");

        // quantization = "Q4_K_M" (default discovered marker) -> overwrite
        let m = normalize_model_entry(json!({"name": "mlx-8bit-model", "quantization": "Q4_K_M"}));
        assert_eq!(model_str(&m, "quantization"), "mlx-8bit");

        // _discovered=true -> always overwrite even with non-default quant
        let m = normalize_model_entry(json!({"name": "my-awq-8bit", "quantization": "Q8_0", "_discovered": true}));
        assert_eq!(model_str(&m, "quantization"), "AWQ-8bit");

        // quantization already explicitly set to a real value -> keep it
        let m = normalize_model_entry(json!({"name": "my-awq-4bit-model", "quantization": "Q6_K"}));
        assert_eq!(model_str(&m, "quantization"), "Q6_K");

        // non-object passes through untouched
        let m = normalize_model_entry(json!("string_value"));
        assert_eq!(m, json!("string_value"));

        // no inferred -> unchanged
        let m = normalize_model_entry(json!({"name": "plain-model"}));
        assert_eq!(model_str(&m, "quantization"), "");
    }

    #[test]
    fn quant_penalty_updated_values() {
        // AWQ-8bit penalty changed from 0.0 to -1.0
        assert_eq!(QUANT_QUALITY_PENALTY.get("AWQ-8bit").copied(), Some(-1.0));
        // GPTQ-Int4 penalty changed from -3.0 to -4.0
        assert_eq!(QUANT_QUALITY_PENALTY.get("GPTQ-Int4").copied(), Some(-4.0));
        // GPTQ-Int8 penalty changed from 0.0 to -1.0
        assert_eq!(QUANT_QUALITY_PENALTY.get("GPTQ-Int8").copied(), Some(-1.0));
        // mlx-8bit penalty changed from 0.0 to -0.5
        assert_eq!(QUANT_QUALITY_PENALTY.get("mlx-8bit").copied(), Some(-0.5));
        // mlx-6bit penalty changed from -1.0 to -1.5
        assert_eq!(QUANT_QUALITY_PENALTY.get("mlx-6bit").copied(), Some(-1.5));
        // new bare keys
        assert_eq!(QUANT_QUALITY_PENALTY.get("AWQ").copied(), Some(-1.0));
        assert_eq!(QUANT_QUALITY_PENALTY.get("GPTQ").copied(), Some(-1.0));
        // new quant type entries
        assert_eq!(QUANT_QUALITY_PENALTY.get("FP4").copied(), Some(-3.0));
        assert_eq!(QUANT_QUALITY_PENALTY.get("NF4").copied(), Some(-4.0));
        assert_eq!(QUANT_QUALITY_PENALTY.get("FP4-MoE-Mixed").copied(), Some(-0.5));
        assert_eq!(QUANT_QUALITY_PENALTY.get("FP8-Mixed").copied(), Some(0.0));
    }

    #[test]
    fn estimate_memory_dense_vs_moe() {
        // dense 7B at Q4_K_M, 4096 ctx: 7*0.58 + 8e-6*7*4096 + 0.5
        let dense = json!({"parameter_count": "7B"});
        let m = estimate_memory_gb(&dense, "Q4_K_M", 4096.0);
        let expected = 7.0 * 0.58 + 0.000008 * 7.0 * 4096.0 + 0.5;
        assert!((m - expected).abs() < 1e-9);

        // MoE: total params drive weights, active params drive KV cache.
        let moe = json!({"parameter_count": "100B", "is_moe": true, "active_parameters": 10_000_000_000i64});
        let mm = estimate_memory_gb(&moe, "Q4_K_M", 4096.0);
        let exp_moe = 100.0 * 0.58 + 0.000008 * 10.0 * 4096.0 + 0.5;
        assert!((mm - exp_moe).abs() < 1e-9);
    }

    #[test]
    fn estimate_memory_unknown_quant_defaults() {
        let m = json!({"parameter_count": "1B"});
        let v = estimate_memory_gb(&m, "TOTALLY_UNKNOWN", 0.0);
        // 1*0.58 + 0 + 0.5
        assert!((v - 1.08).abs() < 1e-9);
    }

    #[test]
    fn best_quant_prequantized_native_only() {
        let m = json!({"parameter_count": "7B", "quantization": "AWQ-4bit"});
        let (q, ctx, _mem) = best_quant_for_budget(&m, 100.0, 8192).unwrap();
        assert_eq!(q, "AWQ-4bit");
        assert_eq!(ctx, 8192);
    }

    #[test]
    fn best_quant_gguf_falls_back_quality() {
        // Tiny budget forces the lowest quant in the hierarchy.
        let m = json!({"parameter_count": "7B"});
        let (q, _ctx, _mem) = best_quant_for_budget(&m, 3.5, 4096).unwrap();
        // Q2_K = 0.37 bpp -> 7*0.37 + kv + 0.5 ~= 3.22 fits 3.5.
        assert_eq!(q, "Q2_K");
    }

    #[test]
    fn best_quant_none_when_impossible() {
        let m = json!({"parameter_count": "7B"});
        assert!(best_quant_for_budget(&m, 0.1, 4096).is_none());
    }

    #[test]
    fn infer_use_case_order() {
        assert_eq!(infer_use_case(&json!({"name": "bge-large-embedding"})), "embedding");
        assert_eq!(infer_use_case(&json!({"name": "whisper-large"})), "stt");
        assert_eq!(infer_use_case(&json!({"name": "qwen-coder"})), "coding");
        assert_eq!(infer_use_case(&json!({"name": "llava-vision"})), "multimodal");
        assert_eq!(infer_use_case(&json!({"name": "deepseek-r1"})), "reasoning");
        assert_eq!(infer_use_case(&json!({"name": "llama-3-chat"})), "chat");
        assert_eq!(infer_use_case(&json!({"name": "mystery-model"})), "general");
        // embedding wins over coding when both present.
        assert_eq!(infer_use_case(&json!({"name": "code-embedding-model"})), "embedding");
    }

    #[test]
    fn catalog_path_is_module_relative() {
        let p = model_catalog_path();
        assert!(p.ends_with("services/hwfit/data/hf_models.json"), "got {}", p);
    }

    #[test]
    fn get_models_loads_real_catalog() {
        // The real 476KB catalog ships in the Python tree; the loader must read
        // it (or, in a stripped checkout, return [] without panicking).
        let models = get_models();
        if !models.is_empty() {
            assert!(models[0].get("name").is_some());
        }
    }

    #[test]
    fn parity_real_catalog_row0_matches_python() {
        // Golden fixtures captured from the live Python `services.hwfit.models`
        // over the shipped catalog. Skip gracefully on a stripped checkout.
        let models = get_models();
        if models.is_empty() {
            return;
        }
        let row0 = &models[0];
        assert_eq!(model_str(row0, "name"), "echarlaix/tiny-random-PhiForCausalLM");
        // params_b: 80074 / 1e9 = 8.0074e-05
        assert!((params_b(row0) - 8.0074e-05).abs() < 1e-15);
        // estimate_memory_gb(row0, "Q4_K_M", 4096) == 0.500049066784832
        assert!((estimate_memory_gb(row0, "Q4_K_M", 4096.0) - 0.500049066784832).abs() < 1e-12);
        assert_eq!(infer_use_case(row0), "general");
        // best_quant_for_budget(row0, 8.0, 4096) == ("Q8_0", 4096, 0.500086701564832)
        let (q, ctx, mem) = best_quant_for_budget(row0, 8.0, 4096).unwrap();
        assert_eq!(q, "Q8_0");
        assert_eq!(ctx, 4096);
        assert!((mem - 0.500086701564832).abs() < 1e-12);

        // The qwen3-next-moe row: active params drive the KV term.
        if let Some(moe) = models
            .iter()
            .find(|m| model_str(m, "name") == "tiny-random/qwen3-next-moe")
        {
            assert!((params_b(moe) - 0.00283916).abs() < 1e-12);
            assert!((_active_params_b(moe) - 0.000984828).abs() < 1e-12);
            // estimate_memory_gb(moe, "BF16", 8192) == 0.505742861687808
            assert!((estimate_memory_gb(moe, "BF16", 8192.0) - 0.505742861687808).abs() < 1e-12);
        }
    }
}
