// services/hwfit/fit.rs  <- the Python `services/hwfit/fit.py`
//! Pure scoring / ranking layer for the hardware-fit advisor.
//!
//! Faithful port of `services/hwfit/fit.py`. The Python module imports a small
//! set of helpers + quant lookup tables from `services.hwfit.models`
//! (`params_b`, `estimate_memory_gb`, `infer_use_case`, `get_models`,
//! `is_prequantized`, `_active_params_b`, `QUANT_BYTES_PER_PARAM`,
//! `QUANT_SPEED_MULT`, `QUANT_QUALITY_PENALTY`) and `rank_image_models` from
//! `services.hwfit.image_models`; the Rust port reaches them through
//! [`super::models`] and [`super::image_models`].
//!
//! Per the package's documented loose-typing convention, a "model" and a
//! "system" are untyped [`serde_json::Value`] objects read with
//! `.get(key, default)`-style accessors. The small `m_*` / `s_*` helpers below
//! mirror Python's `dict.get(key, default)` exactly: they return the requested
//! default when the key is absent or carries the wrong JSON type, and never
//! panic.
//!
//! Results are [`serde_json::Value`] objects built with `serde_json::json!`,
//! which preserves the JSON key insertion order that route callers depend on
//! (the crate enables serde_json's `preserve_order`).
//!
//! ## Rounding
//!
//! Python's `round(x, 1)` uses banker's (round-half-to-even) rounding. The
//! [`round_to`] helper reproduces that rule (same algorithm as `round_py` in
//! `hardware.rs` and `round_half_even` in `image_models.rs`). This matters for
//! `score`, which is the sort key feeding the `limit` truncation: half-away-from-
//! zero rounding could reorder rows that tie on a `.x5` boundary.

use once_cell::sync::Lazy;
use serde_json::{json, Value};

use super::image_models::rank_image_models;
use super::models::{
    self, estimate_memory_gb, infer_use_case, is_prequantized, params_b, QUANT_BYTES_PER_PARAM,
    QUANT_QUALITY_PENALTY, QUANT_SPEED_MULT,
};

// ---------------------------------------------------------------------------
// Constant lookup tables (module-level Python dicts)
// ---------------------------------------------------------------------------

/// `GPU_BANDWIDTH` — memory bandwidth in GB/s keyed by lower-case GPU name
/// substring. Order is preserved only for documentation; lookups go through
/// [`BW_KEYS_SORTED`].
pub static GPU_BANDWIDTH: Lazy<Vec<(&'static str, i64)>> = Lazy::new(|| {
    vec![
        ("5090", 1792),
        ("5080", 960),
        ("5070 ti", 896),
        ("5070", 672),
        ("5060 ti", 448),
        ("5060", 256),
        ("4090", 1008),
        ("4080 super", 736),
        ("4080", 717),
        ("4070 ti super", 672),
        ("4070 ti", 504),
        ("4070 super", 504),
        ("4070", 504),
        ("4060 ti", 288),
        ("4060", 272),
        ("3090 ti", 1008),
        ("3090", 936),
        ("3080 ti", 912),
        ("3080", 760),
        ("3070 ti", 608),
        ("3070", 448),
        ("3060 ti", 448),
        ("3060", 360),
        ("2080 ti", 616),
        ("2080 super", 496),
        ("2080", 448),
        ("2070 super", 448),
        ("2070", 448),
        ("2060 super", 448),
        ("2060", 336),
        ("1660 ti", 288),
        ("1660 super", 336),
        ("1660", 192),
        ("1650 super", 192),
        ("1650", 128),
        ("h100 sxm", 3350),
        ("h100", 2039),
        ("h200", 4800),
        ("a100 sxm", 2039),
        ("a100", 1555),
        ("l40s", 864),
        ("l40", 864),
        ("l4", 300),
        ("a10g", 600),
        ("a10", 600),
        ("t4", 320),
        ("v100 sxm", 900),
        ("v100", 897),
        ("a6000", 768),
        ("a5000", 768),
        ("a4000", 448),
        ("7900 xtx", 960),
        ("7900 xt", 800),
        ("7900 gre", 576),
        ("7800 xt", 624),
        ("7700 xt", 432),
        ("7600", 288),
        ("6950 xt", 576),
        ("6900 xt", 512),
        ("6800 xt", 512),
        ("6800", 512),
        ("6700 xt", 384),
        ("6600 xt", 256),
        ("6600", 224),
        ("mi300x", 5300),
        ("mi300", 5300),
        ("mi250x", 3277),
        ("mi250", 3277),
        ("mi210", 1638),
        ("mi100", 1229),
        ("9070 xt", 624),
        ("9070", 488),
        ("9060 xt", 322),
        ("9060", 322),
        // Apple Silicon unified-memory bandwidth (GB/s). Keyed off the chip name
        // reported by sysctl machdep.cpu.brand_string (e.g. "Apple M4 Max"). Listed
        // before the bare "m_" keys matters less than length-sorting (done below),
        // which guarantees "m4 max" is tried before "m4".
        ("m1 ultra", 800),
        ("m1 max", 400),
        ("m1 pro", 200),
        ("m1", 68),
        ("m2 ultra", 800),
        ("m2 max", 400),
        ("m2 pro", 200),
        ("m2", 100),
        ("m3 ultra", 800),
        ("m3 max", 300),
        ("m3 pro", 150),
        ("m3", 100),
        ("m4 max", 546),
        ("m4 pro", 273),
        ("m4", 120),
        ("m5 max", 546),
        ("m5 pro", 273),
        ("m5", 150),
    ]
});

/// `_BW_KEYS_SORTED` — keys pre-sorted by length descending for correct
/// substring matching (so "5070 ti" wins over "5070" when both substring-match).
///
/// Python: `sorted(GPU_BANDWIDTH.keys(), key=len, reverse=True)`. Python's
/// `sorted` is stable; `slice::sort_by_key` is also stable, so ties (equal
/// length) keep insertion order, matching Python's dict-iteration order.
static BW_KEYS_SORTED: Lazy<Vec<&'static str>> = Lazy::new(|| {
    let mut keys: Vec<&'static str> = GPU_BANDWIDTH.iter().map(|(k, _)| *k).collect();
    keys.sort_by_key(|k| std::cmp::Reverse(k.len()));
    keys
});

/// `FALLBACK_K` — fallback tok/s constant per CPU/GPU backend.
///
/// `metal` is a backstop for Apple Silicon chips not in `GPU_BANDWIDTH` (e.g. a
/// future M5) — the named chips above take the accurate bandwidth path instead.
pub static FALLBACK_K: Lazy<Vec<(&'static str, f64)>> = Lazy::new(|| {
    vec![
        ("cuda", 220.0),
        ("rocm", 180.0),
        ("metal", 150.0),
        ("cpu_x86", 70.0),
        ("cpu_arm", 90.0),
    ]
});

/// `USE_CASE_WEIGHTS` — `(quality, speed, fit, context)` weights per use case.
// mirrors the Python `(quality, speed, fit, context)` weight tuple per use case 1:1
#[allow(clippy::type_complexity)]
pub static USE_CASE_WEIGHTS: Lazy<Vec<(&'static str, (f64, f64, f64, f64))>> = Lazy::new(|| {
    vec![
        ("general", (0.45, 0.30, 0.15, 0.10)),
        ("coding", (0.50, 0.20, 0.15, 0.15)),
        ("reasoning", (0.55, 0.15, 0.15, 0.15)),
        ("chat", (0.40, 0.35, 0.15, 0.10)),
        ("multimodal", (0.50, 0.20, 0.15, 0.15)),
        ("embedding", (0.30, 0.40, 0.20, 0.10)),
        ("tts", (0.40, 0.35, 0.15, 0.10)),
        ("stt", (0.40, 0.35, 0.15, 0.10)),
    ]
});

/// `SPEED_TARGET` — target tok/s per use case (used to normalize the speed score).
pub static SPEED_TARGET: Lazy<Vec<(&'static str, f64)>> = Lazy::new(|| {
    vec![
        ("general", 40.0),
        ("coding", 40.0),
        ("multimodal", 40.0),
        ("chat", 40.0),
        ("reasoning", 25.0),
        ("embedding", 200.0),
        ("tts", 40.0),
        ("stt", 40.0),
    ]
});

/// `CONTEXT_TARGET` — target context window per use case.
pub static CONTEXT_TARGET: Lazy<Vec<(&'static str, i64)>> = Lazy::new(|| {
    vec![
        ("general", 4096),
        ("chat", 4096),
        ("coding", 8192),
        ("reasoning", 8192),
        ("multimodal", 4096),
        ("embedding", 512),
        ("tts", 2048),
        ("stt", 2048),
    ]
});

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

/// `round(x, ndigits)` faithful to CPython's `round` (round-half-to-even, aka
/// banker's rounding), for `ndigits >= 0`. This is the SORT KEY rounding for
/// `score` (and feeds the `limit` truncation), so it must match Python exactly:
/// `f64::round` rounds half away from zero and would change ordering on `.x5`
/// ties. Mirrors `round_py` (hardware.rs) / `round_half_even` (image_models.rs).
fn round_to(x: f64, ndigits: i32) -> f64 {
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
        // Exact half: round to even.
        floor
    } else {
        floor + 1.0
    };
    rounded / factor
}

/// Format an f64 the way Python's `str(float)` / f-string would for the small
/// registry values used here: a whole number still renders with a trailing
/// `.0` (`f"{6.0}B"` -> `"6.0B"`, not `"6B"`), and fractional values keep their
/// shortest decimal form (`3.5` -> `"3.5"`). The image-model registry only
/// carries values with at most one decimal place, so this is exact for the
/// `parameter_count` string Python builds with `f"{im['params_b']}B"`.
fn py_float_str(x: f64) -> String {
    if x.is_finite() && x == x.trunc() {
        format!("{:.1}", x)
    } else {
        format!("{}", x)
    }
}

/// Look up a constant from one of the local `Vec<(&str, T)>` lookup tables,
/// falling back to `default` when the key is absent — mirrors `MAP.get(key,
/// default)`.
fn lookup_f64(table: &[(&'static str, f64)], key: &str, default: f64) -> f64 {
    table
        .iter()
        .find(|(k, _)| *k == key)
        .map(|(_, v)| *v)
        .unwrap_or(default)
}

/// `MAP.get(key, default)` over one of the imported `HashMap<&str, f64>` quant
/// tables from [`super::models`].
fn quant_lookup(table: &std::collections::HashMap<&'static str, f64>, key: &str, default: f64) -> f64 {
    table.get(key).copied().unwrap_or(default)
}

fn lookup_i64(table: &[(&'static str, i64)], key: &str, default: i64) -> i64 {
    table
        .iter()
        .find(|(k, _)| *k == key)
        .map(|(_, v)| *v)
        .unwrap_or(default)
}

/// `USE_CASE_WEIGHTS.get(use_case, (0.45, 0.30, 0.15, 0.10))`.
fn lookup_weights(use_case: &str) -> (f64, f64, f64, f64) {
    USE_CASE_WEIGHTS
        .iter()
        .find(|(k, _)| *k == use_case)
        .map(|(_, w)| *w)
        .unwrap_or((0.45, 0.30, 0.15, 0.10))
}

// ---------------------------------------------------------------------------
// Value accessors mirroring Python's `dict.get(key, default)`
// ---------------------------------------------------------------------------

/// `system.get(key, default)` as a string.
fn s_str(system: &Value, key: &str, default: &str) -> String {
    system
        .get(key)
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| default.to_string())
}

/// `system.get(key)` as an optional string (Python `system.get("gpu_name")`
/// returns `None`/falsy when missing).
fn s_opt_str<'a>(system: &'a Value, key: &str) -> Option<&'a str> {
    system.get(key).and_then(|v| v.as_str())
}

/// `system.get(key, default)` as an f64. JSON ints/floats both coerce.
fn s_f64(system: &Value, key: &str, default: f64) -> f64 {
    match system.get(key) {
        Some(v) if v.is_number() => v.as_f64().unwrap_or(default),
        _ => default,
    }
}

/// `system.get(key, default)` as an i64.
fn s_i64(system: &Value, key: &str, default: i64) -> i64 {
    match system.get(key) {
        Some(v) if v.is_number() => v.as_i64().unwrap_or(default),
        _ => default,
    }
}

/// `bool(system.get(key))` — Python truthiness: missing/null/`false`/`0`/`""`/
/// `[]`/`{}` are all falsy.
fn s_truthy(system: &Value, key: &str) -> bool {
    truthy(system.get(key))
}

/// `model.get(key, default)` as a string.
fn m_str(model: &Value, key: &str, default: &str) -> String {
    model
        .get(key)
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| default.to_string())
}

/// `model.get(key, default)` as an i64.
fn m_i64(model: &Value, key: &str, default: i64) -> i64 {
    match model.get(key) {
        Some(v) if v.is_number() => v.as_i64().unwrap_or(default),
        _ => default,
    }
}

/// `model.get(key)` as an optional f64 (no default; preserves the Python
/// `or required_gb` falsy-coalescing the caller applies).
fn m_f64_opt(model: &Value, key: &str) -> Option<f64> {
    model.get(key).and_then(|v| v.as_f64())
}

/// `bool(model.get(key))` — Python truthiness.
fn m_truthy(model: &Value, key: &str) -> bool {
    truthy(model.get(key))
}

/// `model.get(key, default)` returning the raw [`Value`] (cloned), preserving
/// JSON shape for pass-through fields such as `gguf_sources`.
fn m_value(model: &Value, key: &str, default: Value) -> Value {
    model.get(key).cloned().unwrap_or(default)
}

/// Python truthiness of an `Option<&Value>` (the result of `dict.get(key)`).
fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(false),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

// ---------------------------------------------------------------------------
// Scoring primitives
// ---------------------------------------------------------------------------

/// `_lookup_bandwidth(gpu_name)` — substring-match the GPU name against the
/// length-sorted bandwidth keys; `None` when no match.
fn lookup_bandwidth(gpu_name: Option<&str>) -> Option<i64> {
    let gn = gpu_name?;
    if gn.is_empty() {
        return None;
    }
    let gn = gn.to_lowercase();
    for key in BW_KEYS_SORTED.iter() {
        if gn.contains(key) {
            return Some(lookup_i64(&GPU_BANDWIDTH, key, 0));
        }
    }
    None
}

/// `_estimate_speed(model, quant, run_mode, system, offload_frac=0.0)` — estimate
/// tok/s. Uses active params for MoE (only active experts run per token).
///
/// `offload_frac` (0..1): fraction of the model's weights that spill to system
/// RAM (CPU) because they don't fit VRAM. Generation reads every active weight
/// per token, so when part lives in CPU RAM the per-token time is dominated by
/// the slow path. We model effective bandwidth as a blend of GPU VRAM bandwidth
/// and system-RAM bandwidth weighted by what's where — far more accurate than a
/// flat "halve it" for partial offload, which under/over-shoots depending on
/// amount. Calibrated against a measured RX 9060 XT: DeepSeek-Coder-V2-Lite
/// Q4_K_M with light offload → ~59 t/s est vs 59.8 measured.
fn estimate_speed(model: &Value, quant: &str, run_mode: &str, system: &Value, offload_frac: f64) -> f64 {
    let pb = models::_active_params_b(model);
    let is_moe = m_truthy(model, "is_moe");
    let bw = lookup_bandwidth(s_opt_str(system, "gpu_name"));
    let backend = s_str(system, "backend", "cpu_x86");

    if let Some(bw) = bw {
        if run_mode == "gpu" || run_mode == "cpu_offload" {
            let bpp = quant_lookup(&QUANT_BYTES_PER_PARAM, quant, 0.5);
            let model_gb = pb * bpp;
            if model_gb <= 0.0 {
                return 0.0;
            }
            let efficiency = 0.55;
            if run_mode == "cpu_offload" {
                // Dual-channel DDR4-3200 ≈ 50 GB/s; DDR5 systems higher, but be
                // conservative since offloaded MoE is also compute-bound on CPU.
                let cpu_bw = 55.0;
                let mut frac = offload_frac.clamp(0.0, 1.0);
                // If we don't know the fraction (legacy callers pass 0 with
                // cpu_offload), assume a meaningful spill so we don't overestimate.
                if frac <= 0.0 {
                    frac = 0.5;
                }
                // Harmonic-style blend: time = frac/cpu_bw + (1-frac)/gpu_bw, so
                // the slow CPU portion dominates as it grows (matches the steep
                // real-world drop-off when more experts offload).
                let eff_bw = 1.0 / (frac / cpu_bw + (1.0 - frac) / bw as f64);
                let raw_tps = (eff_bw / model_gb) * efficiency;
                return raw_tps * if is_moe { 0.8 } else { 1.0 };
            }
            // Fully on GPU.
            let raw_tps = (bw as f64 / model_gb) * efficiency;
            return raw_tps * if is_moe { 0.8 } else { 1.0 };
        }
    }

    let k = lookup_f64(&FALLBACK_K, &backend, 70.0);
    if pb <= 0.0 {
        return 0.0;
    }
    let sm = quant_lookup(&QUANT_SPEED_MULT, quant, 1.0);
    k / pb * sm
}

/// `_architecture_bonus(model)` — small quality bump for current model families
/// so they aren't scored the same as older Qwen2/Llama-era entries just because
/// the parameter count is similar.
fn architecture_bonus(model: &Value) -> f64 {
    let name = m_str(model, "name", "").to_lowercase();
    let arch = m_str(model, "architecture", "").to_lowercase();
    let text = format!("{} {}", name, arch);

    // Keep this intentionally small: hardware fit and speed still matter, but
    // current model families should not be scored the same as older Qwen2/LLama
    // era entries just because the parameter count is similar.
    if text.contains("qwen3.6") || text.contains("qwen3_6") {
        return 9.0;
    }
    if text.contains("qwen3.5") || text.contains("qwen3_5") {
        return 8.0;
    }
    if text.contains("qwen3-next") || text.contains("qwen3_next") {
        return 6.0;
    }
    if text.contains("qwen3") || arch.starts_with("qwen3") {
        return 4.0;
    }
    if text.contains("qwen2.5") || text.contains("qwen2_5") {
        return 2.0;
    }
    0.0
}

/// `_quality_score(model, quant, use_case)` — heuristic 0-100 quality estimate.
fn quality_score(model: &Value, quant: &str, use_case: &str) -> f64 {
    let pb = params_b(model);
    let mut base: f64 = if pb < 1.0 {
        30.0
    } else if pb < 3.0 {
        45.0
    } else if pb < 7.0 {
        60.0
    } else if pb < 10.0 {
        75.0
    } else if pb < 20.0 {
        82.0
    } else if pb < 40.0 {
        89.0
    } else {
        95.0
    };

    let name_lower = m_str(model, "name", "").to_lowercase();
    if name_lower.contains("qwen") {
        base += 2.0;
    }
    if name_lower.contains("deepseek") {
        base += 3.0;
    }
    if name_lower.contains("llama") {
        base += 2.0;
    }
    if name_lower.contains("mistral") || name_lower.contains("mixtral") {
        base += 1.0;
    }
    if name_lower.contains("gemma") {
        base += 1.0;
    }

    base += architecture_bonus(model);
    base += quant_lookup(&QUANT_QUALITY_PENALTY, quant, 0.0);

    let model_uc = infer_use_case(model);
    if model_uc == "coding" && use_case == "coding" {
        base += 6.0;
    } else if model_uc == "coding" && (use_case == "general" || use_case == "chat") {
        // Coder-specialized models are still useful generally, but they should
        // not dominate the default scan. If the user wants code, the Coding
        // filter gives them the boost above.
        base -= 10.0;
    }
    if model_uc == "reasoning" && use_case == "reasoning" && pb >= 13.0 {
        base += 5.0;
    } else if model_uc == "reasoning" && use_case == "chat" {
        base -= 4.0;
    }
    if model_uc == "multimodal" && use_case == "multimodal" {
        base += 6.0;
    }

    base.clamp(0.0, 100.0)
}

/// `_speed_score(tps, use_case)` — normalized 0-100 speed score.
fn speed_score(tps: f64, use_case: &str) -> f64 {
    let target = lookup_f64(&SPEED_TARGET, use_case, 40.0);
    ((tps / target) * 100.0).clamp(0.0, 100.0)
}

/// `_fit_score(required, available)` — how comfortably the model fits in budget.
fn fit_score(required: f64, available: f64) -> f64 {
    if required > available {
        return 0.0;
    }
    if available <= 0.0 {
        return 0.0;
    }
    let ratio = required / available;
    if ratio <= 0.5 {
        return 60.0 + (ratio / 0.5) * 40.0;
    }
    if ratio <= 0.8 {
        return 100.0;
    }
    if ratio <= 0.9 {
        return 70.0;
    }
    50.0
}

/// `_context_score(ctx, use_case)` — how well the achievable context meets the
/// per-use-case target.
fn context_score(ctx: i64, use_case: &str) -> f64 {
    let target = lookup_i64(&CONTEXT_TARGET, use_case, 4096);
    if ctx >= target {
        return 100.0;
    }
    if ctx >= target / 2 {
        return 70.0;
    }
    30.0
}

/// Outcome of [`try_quant_at`]: `(run_mode, quant, ctx, mem_gb)`.
struct QuantFit {
    run_mode: &'static str,
    quant: String,
    ctx: i64,
    mem: f64,
}

/// `_try_quant_at(model, quant, ctx, gpu_vram, available_ram)` — try a specific
/// quant at a context window, shrinking the context by halves down to 1024 if
/// it does not fit. `None` when nothing fits.
fn try_quant_at(
    model: &Value,
    quant: &str,
    ctx: i64,
    gpu_vram: f64,
    available_ram: f64,
) -> Option<QuantFit> {
    let mem = estimate_memory_gb(model, quant, ctx as f64);
    if gpu_vram > 0.0 && mem <= gpu_vram {
        return Some(QuantFit {
            run_mode: "gpu",
            quant: quant.to_string(),
            ctx,
            mem,
        });
    }
    if gpu_vram > 0.0 && mem <= available_ram {
        return Some(QuantFit {
            run_mode: "cpu_offload",
            quant: quant.to_string(),
            ctx,
            mem,
        });
    }
    if gpu_vram <= 0.0 && mem <= available_ram {
        return Some(QuantFit {
            run_mode: "cpu_only",
            quant: quant.to_string(),
            ctx,
            mem,
        });
    }
    // Try halving context
    let mut cur_ctx = ctx / 2;
    while cur_ctx >= 1024 {
        let mem = estimate_memory_gb(model, quant, cur_ctx as f64);
        if gpu_vram > 0.0 && mem <= gpu_vram {
            return Some(QuantFit {
                run_mode: "gpu",
                quant: quant.to_string(),
                ctx: cur_ctx,
                mem,
            });
        }
        if mem <= available_ram {
            return Some(QuantFit {
                run_mode: if gpu_vram > 0.0 {
                    "cpu_offload"
                } else {
                    "cpu_only"
                },
                quant: quant.to_string(),
                ctx: cur_ctx,
                mem,
            });
        }
        cur_ctx /= 2;
    }
    None
}

/// `_quant_bits(q)` — approximate bit-width of a quant label so GGUF quant tiers
/// (Q4/Q8/…) can be matched against prequantized formats (AWQ 4, AWQ-8bit, FP8,
/// GPTQ-4bit…). Returns 0 when unknown (caller treats unknown as "don't filter").
fn quant_bits(q: &str) -> i64 {
    let qu = q.to_uppercase().replace(['-', '_', ' '], "");
    // GGUF k-quants + float formats
    if qu.starts_with("Q8") || qu.contains("FP8") || qu.contains("INT8") || qu.starts_with("W8") {
        return 8;
    }
    if qu.starts_with("Q4")
        || qu.starts_with("IQ4")
        || qu.contains("FP4")
        || qu.contains("NF4")
        || qu.contains("INT4")
        || qu.starts_with("W4")
    {
        return 4;
    }
    if qu.starts_with("Q2") || qu.starts_with("IQ2") {
        return 2;
    }
    if qu.starts_with("Q3") || qu.starts_with("IQ3") {
        return 3;
    }
    if qu.starts_with("Q5") {
        return 5;
    }
    if qu.starts_with("Q6") {
        return 6;
    }
    if qu.starts_with("F16") || qu.starts_with("BF16") || qu.starts_with("F32") {
        return 16;
    }
    // Prequantized formats: pull the bit-width digit (AWQ4 / AWQ4BIT / GPTQ8 /
    // 4BIT / INT8 …). Python: re.search(r"(?:AWQ|GPTQ|MLX|EXL2|BNB|INT|W)(\d{1,2})")
    // or re.search(r"(\d{1,2})BIT").
    static RE_PREFIX: Lazy<regex::Regex> =
        Lazy::new(|| regex::Regex::new(r"(?:AWQ|GPTQ|MLX|EXL2|BNB|INT|W)(\d{1,2})").unwrap());
    static RE_BIT: Lazy<regex::Regex> = Lazy::new(|| regex::Regex::new(r"(\d{1,2})BIT").unwrap());
    let digits = RE_PREFIX
        .captures(&qu)
        .and_then(|c| c.get(1))
        .or_else(|| RE_BIT.captures(&qu).and_then(|c| c.get(1)));
    if let Some(m) = digits {
        if let Ok(b) = m.as_str().parse::<i64>() {
            if (2..=16).contains(&b) {
                return b;
            }
        }
    }
    0
}

/// `_native_quant(model)` — resolve the model's native serving quant label,
/// canonicalizing prequantized formats to the catalog keys used by
/// `QUANT_BPP` / `QUANT_QUALITY_PENALTY` (e.g. `GPTQ-Int4`, `AWQ-4bit`, `FP8`).
/// Falls back to `model["quantization"]` (default `Q4_K_M`) when no format is
/// recognized.
fn native_quant(model: &Value) -> String {
    let native_quant = m_str(model, "quantization", "Q4_K_M");
    let name = m_str(model, "name", "").to_lowercase();
    let fmt = m_str(model, "format", "").to_lowercase();
    let text = format!("{} {}", name, fmt);

    if text.contains("nvfp4") {
        return "NVFP4".to_string();
    }
    static RE_FP8: Lazy<regex::Regex> =
        Lazy::new(|| regex::Regex::new(r"(^|[-_/])fp8($|[-_/\s])").unwrap());
    if RE_FP8.is_match(&text) {
        return "FP8".to_string();
    }
    if text.contains("gptq") {
        static RE_GPTQ: Lazy<regex::Regex> =
            Lazy::new(|| regex::Regex::new(r"(?:gptq|int|w)(?:[-_]?)(\d{1,2})(?:bit)?").unwrap());
        // Canonical catalog label is "GPTQ-Int4"/"GPTQ-Int8" (see models.py
        // QUANT_BPP / QUANT_QUALITY_PENALTY keys); "GPTQ-4bit" misses both
        // maps, so BPP and the quality penalty silently fall to defaults.
        return match RE_GPTQ.captures(&text).and_then(|c| c.get(1)) {
            Some(m) => format!("GPTQ-Int{}", m.as_str()),
            None => "GPTQ-Int4".to_string(),
        };
    }
    if text.contains("awq") {
        static RE_AWQ: Lazy<regex::Regex> =
            Lazy::new(|| regex::Regex::new(r"(?:awq|int|w)(?:[-_]?)(\d{1,2})(?:bit)?").unwrap());
        // Catalog keys are "AWQ-4bit"/"AWQ-8bit"; bare "AWQ" misses the maps.
        return match RE_AWQ.captures(&text).and_then(|c| c.get(1)) {
            Some(m) => format!("AWQ-{}bit", m.as_str()),
            None => "AWQ-4bit".to_string(),
        };
    }
    if text.contains("mlx") {
        static RE_MLX: Lazy<regex::Regex> =
            Lazy::new(|| regex::Regex::new(r"mlx[-_]?(\d{1,2})bit").unwrap());
        return match RE_MLX.captures(&text).and_then(|c| c.get(1)) {
            Some(m) => format!("mlx-{}bit", m.as_str()),
            None => native_quant,
        };
    }
    static RE_8BIT: Lazy<regex::Regex> =
        Lazy::new(|| regex::Regex::new(r"(^|[-_/])(?:int)?8bit($|[-_/\s])").unwrap());
    if !(m_truthy(model, "is_gguf") || m_truthy(model, "gguf_sources")) && RE_8BIT.is_match(&text) {
        return "INT8".to_string();
    }
    native_quant
}

// ---------------------------------------------------------------------------
// analyze_model
// ---------------------------------------------------------------------------

/// `analyze_model(model, system, target_quant=None, scoring_use_case=None,
/// target_context=None)` — evaluate a single model against the detected
/// hardware, returning a fit-result dict (as an ordered JSON object), a
/// `too_tight` sentinel dict, or `None` when the model has no usable parameter
/// count or doesn't fit even on the budget it claimed.
pub fn analyze_model(
    model: &Value,
    system: &Value,
    target_quant: Option<&str>,
    scoring_use_case: Option<&str>,
    target_context: Option<i64>,
) -> Option<Value> {
    let pb = params_b(model);
    if pb <= 0.0 {
        return None;
    }

    let model_use_case = infer_use_case(model);
    // score_use_case = scoring_use_case or "general"
    let score_use_case = match scoring_use_case {
        Some(s) if !s.is_empty() => s,
        _ => "general",
    };
    let has_gpu = s_truthy(system, "has_gpu");
    // gpu_vram = (system.get("gpu_vram_gb") or 0) if has_gpu else 0
    let gpu_vram = if has_gpu {
        s_f64(system, "gpu_vram_gb", 0.0)
    } else {
        0.0
    };
    // gpu_count = system.get("gpu_count", 1) or 1
    let mut gpu_count = s_i64(system, "gpu_count", 1);
    if gpu_count == 0 {
        gpu_count = 1;
    }
    let single_gpu_vram = if gpu_count > 1 {
        gpu_vram / gpu_count as f64
    } else {
        gpu_vram
    };
    let available_ram = s_f64(system, "available_ram_gb", 0.0);
    // When the user has explicitly picked a GPU config (not RAM mode), they want
    // to see what runs ON the GPU(s) — not big models that only "fit" by spilling
    // most layers to system RAM. Zeroing the offload budget makes _try_quant_at
    // take only its GPU branches (fit on VRAM, shrinking context if needed),
    // otherwise return None. Fixes "96 GB GPU still lists a 175 GB model".
    let gpu_only = s_truthy(system, "gpu_only") && has_gpu && gpu_vram > 0.0;
    let eff_ram = if gpu_only { 0.0 } else { available_ram };
    let is_moe = m_truthy(model, "is_moe");
    // model_ctx = model.get("context_length", 4096) or 4096
    let mut model_ctx = m_i64(model, "context_length", 4096);
    if model_ctx == 0 {
        model_ctx = 4096;
    }
    // try: target_context = int(target_context or 0) except (TypeError, ValueError): 0
    let target_context = target_context.unwrap_or(0).max(0);
    // ctx = min(model_ctx, target_context) if target_context > 0 else model_ctx
    let ctx = if target_context > 0 {
        model_ctx.min(target_context)
    } else {
        model_ctx
    };

    let native_quant = native_quant(model);
    let preq = is_prequantized(model);

    // GGUF models can't be sharded across GPUs — use single GPU VRAM
    let is_gguf = m_truthy(model, "gguf_sources");
    let quant_upper = native_quant.to_uppercase();
    let is_gguf_quant = ["Q2", "Q3", "Q4", "Q5", "Q6", "Q8", "IQ", "F16", "F32"]
        .iter()
        .any(|p| quant_upper.starts_with(p));
    // Single-GPU VRAM only applies to GGUF/dense builds (llama.cpp can't shard
    // across GPUs). Prequantized formats (AWQ/GPTQ/FP8) are served sharded by
    // vLLM across all GPUs, so they get the FULL multi-GPU VRAM — even when the
    // model also lists a GGUF alternate download (gguf_sources).
    let effective_vram = if (is_gguf || is_gguf_quant) && !preq {
        single_gpu_vram
    } else {
        gpu_vram
    };

    // native_gpu_only = preq and not native_quant.startswith("mlx-")
    let native_gpu_only = preq && !native_quant.starts_with("mlx-");

    // Determine which quant to evaluate at
    let native_quant_prefixes = [
        "AWQ-", "GPTQ-", "FP8", "FP4", "NVFP4", "MXFP4", "NF4", "INT4", "INT8", "W4A16", "W8A8",
        "W8A16",
    ];

    let quant_to_try: String = if preq {
        // Native HF/vLLM quantized repos come at a fixed format. If the user
        // picked a GGUF quant tier (Q4/Q8/etc.), do not treat same-bit
        // AWQ/GPTQ/FP8/FP4 builds as equivalent; those formats are separate
        // serving paths and only appear when explicitly selected or unfiltered.
        if let Some(tq) = target_quant {
            if !native_quant_prefixes.iter().any(|p| tq.starts_with(p)) {
                return None;
            }
            let tb = quant_bits(tq);
            let nb = quant_bits(&native_quant);
            if tb != 0 && nb != 0 && tb != nb {
                return None;
            }
        }
        native_quant.clone()
    } else if let Some(tq) = target_quant {
        // User picked a specific quant
        tq.to_string()
    } else if gpu_count >= 2 {
        // Multi-GPU box: vLLM/SGLang can't serve GGUF Q* quants (those are
        // llama.cpp-only). Default non-prequantized models to BF16 so the row
        // is meaningful on a multi-GPU rig. If BF16 doesn't fit, the model
        // surfaces as too_tight — better than showing a Q4 row the user
        // can't actually serve with vLLM on >1 GPU.
        "BF16".to_string()
    } else {
        // Default: Q4_K_M (user's stated preference) — kept for single-GPU
        // and RAM modes where llama.cpp serving is the natural path.
        "Q4_K_M".to_string()
    };

    // Multi-GPU filter: skip the row if the resolved quant is a GGUF tier
    // (Q*/IQ-prefixed) — vLLM/SGLang can't serve those, so showing them on
    // a 2+ GPU rig just clutters the list with unservable candidates.
    if gpu_count >= 2 {
        let qt_upper = quant_to_try.to_uppercase();
        if ["Q2", "Q3", "Q4", "Q5", "Q6", "Q8", "IQ"]
            .iter()
            .any(|p| qt_upper.starts_with(p))
        {
            return None;
        }
    }

    let result = try_quant_at(
        model,
        &quant_to_try,
        ctx,
        effective_vram,
        if native_gpu_only { 0.0 } else { eff_ram },
    );

    let result = match result {
        Some(r) => r,
        None => {
            // Model doesn't fit on the user's current hardware. Surface it
            // anyway with a "too_tight" badge instead of silently dropping
            // it — without this, editing the hardware config to try LARGER
            // tiers never revealed the bigger models, because they were
            // filtered out before the user could see what would fit. The
            // client already knows how to render too_tight (red row).
            let oversized_required = estimate_memory_gb(model, &quant_to_try, ctx as f64);
            return Some(json!({
                "name": m_value(model, "name", Value::Null),
                "provider": m_value(model, "provider", Value::Null),
                "parameter_count": m_value(model, "parameter_count", Value::Null),
                "params_b": round_to(pb, 1),
                "is_moe": is_moe,
                "use_case": model_use_case,
                "fit_level": "too_tight",
                "run_mode": "no_fit",
                "quant": quant_to_try,
                "context": ctx,
                "required_gb": round_to(oversized_required, 1),
                "speed_tps": 0,
                "score": 0,
                "scores": {"quality": 0, "speed": 0, "fit": 0, "context": 0},
                "gguf_sources": m_value(model, "gguf_sources", json!([])),
                "context_length": model_ctx,
                // target_context or None
                "target_context": if target_context > 0 { json!(target_context) } else { Value::Null },
            }));
        }
    };

    let run_mode = result.run_mode;
    let quant = result.quant;
    let fit_ctx = result.ctx;
    let required_gb = result.mem;

    // Determine fit level
    let budget = if run_mode == "gpu" {
        effective_vram
    } else {
        available_ram
    };
    if required_gb > budget {
        return None;
    }
    let fit_level: &str = if run_mode == "gpu" {
        // rec = model.get("recommended_ram_gb") or required_gb
        let rec = m_f64_opt(model, "recommended_ram_gb")
            .filter(|v| *v != 0.0)
            .unwrap_or(required_gb);
        if rec <= gpu_vram {
            "perfect"
        } else if gpu_vram >= required_gb * 1.2 {
            "good"
        } else {
            "marginal"
        }
    } else if run_mode == "cpu_offload" {
        if available_ram >= required_gb * 1.2 {
            "good"
        } else {
            "marginal"
        }
    } else {
        "marginal"
    };

    // Fraction of the model that spills to CPU RAM (drives the offload speed
    // model). When offloading, anything beyond the GPU's VRAM lives in system RAM.
    let mut offload_frac = 0.0;
    if run_mode == "cpu_offload" && required_gb > 0.0 && effective_vram > 0.0 {
        offload_frac = ((required_gb - effective_vram) / required_gb).max(0.0);
    }
    let tps = estimate_speed(model, &quant, run_mode, system, offload_frac);

    let q_score = quality_score(model, &quant, score_use_case);
    let s_score = speed_score(tps, score_use_case);
    let f_score = fit_score(required_gb, budget);
    let c_score = context_score(fit_ctx, score_use_case);

    let (wq, ws, wf, wc) = lookup_weights(score_use_case);
    let composite = q_score * wq + s_score * ws + f_score * wf + c_score * wc;

    Some(json!({
        "name": m_value(model, "name", Value::Null),
        "provider": m_value(model, "provider", Value::Null),
        "parameter_count": m_value(model, "parameter_count", Value::Null),
        "params_b": round_to(pb, 1),
        "is_moe": is_moe,
        "use_case": model_use_case,
        "fit_level": fit_level,
        "run_mode": run_mode,
        "quant": quant,
        "context": fit_ctx,
        "required_gb": round_to(required_gb, 1),
        "speed_tps": round_to(tps, 1),
        "score": round_to(composite, 1),
        "scores": {
            "quality": round_to(q_score, 1),
            "speed": round_to(s_score, 1),
            "fit": round_to(f_score, 1),
            "context": round_to(c_score, 1),
        },
        "gguf_sources": m_value(model, "gguf_sources", json!([])),
        "context_length": model_ctx,
        "release_date": m_value(model, "release_date", json!("")),
        // target_context or None
        "target_context": if target_context > 0 { json!(target_context) } else { Value::Null },
    }))
}

// ---------------------------------------------------------------------------
// rank_models
// ---------------------------------------------------------------------------

/// `_version_key(name)` — parse the model's version number from its display
/// name so equal-score rows can break ties in favor of the newer release (e.g.
/// M2.7 > M2.5). Returns a float; 0.0 for names with no recognizable version.
/// The regex grabs the FIRST 'letter followed by a number' pattern, ignoring "B"
/// param-count suffixes (Qwen3-235B yields 3, not 235) and bare integers >= 100
/// (almost certainly param counts, not version numbers).
fn version_key(name: &str) -> f64 {
    if name.is_empty() {
        return 0.0;
    }
    // Match the version-marker word: a letter followed by a number with optional
    // decimal, e.g. M2.7, V4, Pro3. Python:
    // re.finditer(r"[A-Za-z](\d+(?:\.\d+)?)(?![A-Za-z])"). The Rust `regex` crate
    // lacks lookahead, so the negative-lookahead `(?![A-Za-z])` is re-implemented
    // by inspecting the char right after the captured digit run and skipping the
    // match when it is a letter. A skipped match ends exactly before that letter,
    // which is where the next candidate token begins, so resumption matches
    // Python's one-position-advance-on-lookahead-failure behavior.
    static RE_VER: Lazy<regex::Regex> =
        Lazy::new(|| regex::Regex::new(r"[A-Za-z](\d+(?:\.\d+)?)").unwrap());
    for caps in RE_VER.captures_iter(name) {
        let m = match caps.get(1) {
            Some(m) => m,
            None => continue,
        };
        // The match must NOT be immediately followed by a letter (negative
        // lookahead). Check the char right after the captured digit run.
        let end = m.end();
        if let Some(next) = name[end..].chars().next() {
            if next.is_ascii_alphabetic() {
                continue;
            }
        }
        let val = m.as_str();
        let f: f64 = match val.parse() {
            Ok(f) => f,
            Err(_) => continue,
        };
        // Heuristic: bare integers >= 100 are almost certainly param counts
        // (1B/3B/8B/70B/235B…), not version numbers. Skip them.
        if !val.contains('.') && f >= 100.0 {
            continue;
        }
        return f;
    }
    0.0
}

/// Comparison key for one result row under the requested sort column. Mirrors the
/// Python `SORT_KEYS` dict: `score` carries a version tiebreaker
/// `(score, version_key(name))`, `newest` sorts by the `release_date` string,
/// and the remaining columns are single numeric fields.
enum SortKey {
    /// (score, version_key) — tuple compare, as Python's `(r["score"], _version_key(...))`.
    ScoreVer(f64, f64),
    /// `release_date` string (Python: `r.get("release_date") or ""`).
    Date(String),
    /// single numeric field
    Num(f64),
}

impl SortKey {
    fn for_row(row: &Value, key: &str) -> SortKey {
        match key {
            "score" => {
                let score = row.get("score").and_then(|v| v.as_f64()).unwrap_or(0.0);
                let name = row.get("name").and_then(|v| v.as_str()).unwrap_or("");
                SortKey::ScoreVer(score, version_key(name))
            }
            "speed" => SortKey::Num(row.get("speed_tps").and_then(|v| v.as_f64()).unwrap_or(0.0)),
            "vram" => SortKey::Num(row.get("required_gb").and_then(|v| v.as_f64()).unwrap_or(0.0)),
            "params" => SortKey::Num(row.get("params_b").and_then(|v| v.as_f64()).unwrap_or(0.0)),
            "context" => SortKey::Num(row.get("context").and_then(|v| v.as_f64()).unwrap_or(0.0)),
            "newest" => SortKey::Date(
                row.get("release_date")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
            ),
            // SORT_KEYS.get(sort, SORT_KEYS["score"]) — unknown key falls back to score
            _ => {
                let score = row.get("score").and_then(|v| v.as_f64()).unwrap_or(0.0);
                let name = row.get("name").and_then(|v| v.as_str()).unwrap_or("");
                SortKey::ScoreVer(score, version_key(name))
            }
        }
    }

    fn cmp(&self, other: &SortKey) -> std::cmp::Ordering {
        use std::cmp::Ordering;
        match (self, other) {
            (SortKey::ScoreVer(sa, va), SortKey::ScoreVer(sb, vb)) => sa
                .partial_cmp(sb)
                .unwrap_or(Ordering::Equal)
                .then_with(|| va.partial_cmp(vb).unwrap_or(Ordering::Equal)),
            (SortKey::Date(a), SortKey::Date(b)) => a.cmp(b),
            (SortKey::Num(a), SortKey::Num(b)) => a.partial_cmp(b).unwrap_or(Ordering::Equal),
            _ => Ordering::Equal,
        }
    }
}

/// Stable sort by the requested column. Python: `results.sort(key=fn,
/// reverse=...)`. Python's list sort is stable; `slice::sort_by` is also stable,
/// so ties preserve prior order.
fn sort_rows(rows: &mut [Value], key: &str, reverse: bool) {
    rows.sort_by(|a, b| {
        let ord = SortKey::for_row(a, key).cmp(&SortKey::for_row(b, key));
        if reverse {
            ord.reverse()
        } else {
            ord
        }
    });
}

/// `rank_models(system, use_case=None, limit=50, search=None, sort="score",
/// quant=None, target_context=None, fit_only=False)` — rank all catalog models
/// against detected hardware. Returns the sorted list of fit-result dicts.
///
/// `fit_only`: when True, drop rows whose `fit_level` is `too_tight` (model
/// doesn't actually fit on the chosen budget). When False (default), every model
/// is shown.
#[allow(clippy::too_many_arguments)] // faithful 1:1 port of the Python rank_models signature
pub fn rank_models(
    system: &Value,
    use_case: Option<&str>,
    limit: usize,
    search: Option<&str>,
    sort: &str,
    quant: Option<&str>,
    target_context: Option<i64>,
    fit_only: bool,
) -> Vec<Value> {
    let models_list = models::get_models();
    let mut results: Vec<Value> = Vec::new();

    // Include image gen models only when explicitly filtered
    if use_case == Some("image_gen") {
        // The Rust port always has rank_image_models available (no optional
        // import / ImportError), so the Python `except ImportError` branch that
        // sets img_results = [] is unreachable here. Python calls
        // `rank_image_models(system, search=search)`, leaving `sort` at its
        // "fit" default.
        let img_results = rank_image_models(system, search, "fit");
        for im in &img_results {
            // fit_map = {"perfect":"perfect","good":"good","tight":"marginal",
            //            "no_fit":"too_tight","no_gpu":"too_tight"}
            let im_fit = im.get("fit").and_then(|v| v.as_str()).unwrap_or("");
            let fit_level = match im_fit {
                "perfect" => "perfect",
                "good" => "good",
                "tight" => "marginal",
                "no_fit" => "too_tight",
                "no_gpu" => "too_tight",
                // fit_map.get(im["fit"], "too_tight")
                _ => "too_tight",
            };
            let im_params_b = im.get("params_b").and_then(|v| v.as_f64()).unwrap_or(0.0);
            let im_fits = im.get("fits").and_then(|v| v.as_bool()).unwrap_or(false);
            // required_gb = round(im.get("vram_needed") or 0, 1)
            let vram_needed = im
                .get("vram_needed")
                .and_then(|v| v.as_f64())
                .filter(|v| *v != 0.0)
                .unwrap_or(0.0);
            results.push(json!({
                "name": im.get("id").cloned().unwrap_or(Value::Null),
                "provider": im.get("provider").cloned().unwrap_or(Value::Null),
                // f"{im['params_b']}B" — preserve Python's float str (6.0 -> "6.0B")
                "parameter_count": py_float_str(im_params_b) + "B",
                "params_b": im_params_b,
                "is_moe": false,
                "use_case": "image_gen",
                "fit_level": fit_level,
                "run_mode": if im_fits { "gpu" } else { "no_fit" },
                // im.get("quant", "BF16") — dict.get substitutes the default ONLY
                // when the key is ABSENT. rank_image_models always inserts the key
                // (set to null on no-GPU hosts), so a present null must pass through.
                "quant": match im.get("quant") { Some(v) => v.clone(), None => json!("BF16") },
                "context": 0,
                "context_length": 0,
                "required_gb": round_to(vram_needed, 1),
                "speed_tps": 0,
                // float(im["score"]) / float(im["quality"]) / float(im["speed"])
                "score": im.get("score").and_then(|v| v.as_f64()).unwrap_or(0.0),
                "scores": {
                    "quality": im.get("quality").and_then(|v| v.as_f64()).unwrap_or(0.0),
                    "speed": im.get("speed").and_then(|v| v.as_f64()).unwrap_or(0.0),
                    "fit": 0,
                    "context": 0,
                },
                "gguf_sources": [],
                "is_image_gen": true,
                // im.get("capabilities", []) / im.get("description", "")
                "capabilities": im.get("capabilities").cloned().unwrap_or_else(|| json!([])),
                "description": im.get("description").cloned().unwrap_or_else(|| json!("")),
            }));
        }
        // if use_case == "image_gen": (always true in this branch)
        // Always descending then truncate (see main path below).
        sort_rows(&mut results, sort, true);
        results.truncate(limit);
        return results;
    }

    // If user picked a native prequantized format, filter to only those models.
    let native_filter_prefixes = [
        "AWQ-", "GPTQ-", "FP8", "FP4", "NVFP4", "MXFP4", "NF4", "INT4", "INT8", "W4A16", "W8A8",
        "W8A16",
    ];
    let filter_native = quant
        .map(|q| !q.is_empty() && native_filter_prefixes.iter().any(|p| q.starts_with(p)))
        .unwrap_or(false);

    let system_backend = s_str(system, "backend", "").to_lowercase();
    let apple_silicon = matches!(system_backend.as_str(), "mps" | "metal" | "apple");
    let rocm = system_backend == "rocm";

    // Consumer AMD Radeon (RDNA, gfx10/11/12): the practical local serving path
    // is GGUF via llama.cpp. vLLM/SGLang on ROCm are validated for datacenter
    // Instinct (CDNA, gfx9xx) but are unreliable on consumer RDNA — AWQ kernels
    // are largely unsupported there and FP8 needs out-of-tree patches. So treat
    // consumer RDNA like Apple Silicon (GGUF-only) and leave CDNA untouched.
    // Unknown family (no rocminfo) is left untouched to avoid hiding models from
    // a possibly-capable Instinct box on a misdetect.
    let gpu_family = s_str(system, "gpu_family", "").to_lowercase();
    let consumer_amd = system_backend == "rocm" && gpu_family == "rdna";

    for m in &models_list {
        let native_q = native_quant(m);

        // MLX needs the mlx_lm runtime, which Odysseus does not generate serve
        // commands for. Hide it on every backend, including Metal.
        if native_q.starts_with("mlx-") || m_str(m, "name", "").to_lowercase().contains("mlx") {
            continue;
        }

        // ROCm support for vLLM/SGLang quantized safetensors is too brittle to
        // recommend blindly in the default scan. Keep AWQ/GPTQ/FP8 discoverable
        // only when the user explicitly picks that format from the quant filter;
        // otherwise prefer GGUF/Q* entries that Odysseus can route through
        // llama.cpp/Ollama without pretending "fits VRAM" means "servable".
        if rocm && is_prequantized(m) && !filter_native {
            continue;
        }

        // On Apple Silicon the only serving engines are llama.cpp and Ollama,
        // both GGUF-only (vLLM/SGLang are CUDA/ROCm and don't run on macOS). So
        // a model is Metal-servable ONLY if it ships a real GGUF. Drop everything
        // else — raw safetensors repos (which the catalog still tags with a
        // default GGUF quant) and vLLM-only AWQ/GPTQ/FP8 builds alike. Without
        // this the Cookbook recommends models the Mac can't run; on CUDA these
        // stay visible because vLLM serves safetensors directly.
        //
        // Consumer AMD (RDNA) is the same story: GGUF via llama.cpp is the
        // servable path, so a model needs a real GGUF to be recommended.
        // Otherwise the Cookbook rates vLLM-only AWQ/GPTQ builds "GOOD" on a
        // Radeon that can't actually serve them.
        if (apple_silicon || consumer_amd) && !(m_truthy(m, "is_gguf") || m_truthy(m, "gguf_sources"))
        {
            continue;
        }

        // Format filter: AWQ tab -> only AWQ models, FP4 tab -> FP4-family models, etc.
        if filter_native {
            let q = quant.unwrap();
            if q == "FP8" && native_q != "FP8" {
                continue;
            }
            if q == "FP4" && !matches!(native_q.as_str(), "FP4" | "NVFP4" | "MXFP4" | "NF4") {
                continue;
            }
            if q.starts_with("AWQ") && !native_q.starts_with("AWQ") {
                continue;
            }
            if q.starts_with("GPTQ") && !native_q.starts_with("GPTQ") {
                continue;
            }
            if q.starts_with("NVFP4") && !native_q.starts_with("NVFP4") {
                continue;
            }
            if matches!(q, "INT4" | "INT8" | "W4A16" | "W8A8" | "W8A16") && native_q != q {
                continue;
            }
        }

        if let Some(search) = search {
            // Python `if search:` is falsy for an empty string.
            if !search.is_empty() {
                let name = m_str(m, "name", "").to_lowercase();
                let provider = m_str(m, "provider", "").to_lowercase();
                let s = search.to_lowercase();
                if !name.contains(&s) && !provider.contains(&s) {
                    continue;
                }
            }
        }

        // scoring_use_case=(use_case or "general")
        let result = match analyze_model(
            m,
            system,
            quant,
            Some(use_case.filter(|u| !u.is_empty()).unwrap_or("general")),
            target_context,
        ) {
            Some(r) => r,
            None => continue,
        };

        if let Some(use_case) = use_case {
            // Python `if use_case:` is falsy for an empty string.
            if !use_case.is_empty() {
                let model_uc = infer_use_case(m);
                if use_case != model_uc && use_case != "general" {
                    continue;
                }
            }
        }

        results.push(result);
    }

    // Pick the visible SET by the REQUESTED column. Models that don't fit are
    // still in the list with their fit_level marking the constraint, so the user
    // can see the truth instead of a quietly-truncated view.
    if fit_only {
        // Hide rows that definitely don't fit (the "too_tight" badge) — user
        // explicitly asked for a Fit-only view.
        results.retain(|r| r.get("fit_level").and_then(|v| v.as_str()) != Some("too_tight"));
    }
    // Always sort descending then truncate top-N so each column shows the
    // global highest by that metric. Before, vram was special-cased ascending →
    // truncate kept the 50 SMALLEST models and "highest VRAM" could never appear,
    // breaking the column-click toggle.
    sort_rows(&mut results, sort, true);
    results.truncate(limit);
    results
}

#[cfg(test)]
mod tests {
    //! Parity tests. Expected values were captured by running the Python
    //! `services.hwfit.fit` against the same inputs (see the porting notes).
    use super::*;
    use serde_json::json;

    #[test]
    fn lookup_bandwidth_substring_precedence() {
        // "5070 ti" must win over "5070" (length-sorted keys).
        assert_eq!(lookup_bandwidth(Some("RTX 5070 Ti")), Some(896));
        assert_eq!(lookup_bandwidth(Some("RTX 5070")), Some(672));
        assert_eq!(lookup_bandwidth(Some("Intel Arc")), None);
        assert_eq!(lookup_bandwidth(Some("")), None);
        assert_eq!(lookup_bandwidth(None), None);
    }

    #[test]
    fn quant_bits_parity() {
        assert_eq!(quant_bits("Q4_K_M"), 4);
        assert_eq!(quant_bits("Q8_0"), 8);
        assert_eq!(quant_bits("FP8"), 8);
        assert_eq!(quant_bits("AWQ-4bit"), 4);
        assert_eq!(quant_bits("GPTQ-Int8"), 8);
        assert_eq!(quant_bits("mlx-6bit"), 6);
        assert_eq!(quant_bits("IQ2_XS"), 2);
        assert_eq!(quant_bits("weird"), 0);
        assert_eq!(quant_bits("BF16"), 16);
        assert_eq!(quant_bits("F32"), 16);
        // New 8-bit / 4-bit branches.
        assert_eq!(quant_bits("INT8"), 8);
        assert_eq!(quant_bits("W8A8"), 8); // qu = "W8A8" -> starts_with("W8")
        assert_eq!(quant_bits("FP4"), 4);
        assert_eq!(quant_bits("NF4"), 4);
        assert_eq!(quant_bits("INT4"), 4);
        assert_eq!(quant_bits("W4A16"), 4); // starts_with("W4")
        assert_eq!(quant_bits("NVFP4"), 4); // contains FP4
    }

    #[test]
    fn native_quant_canonicalizes_formats() {
        // NVFP4 anywhere in name/format.
        assert_eq!(
            native_quant(&json!({"name": "Foo-NVFP4", "quantization": "BF16"})),
            "NVFP4"
        );
        // FP8 only when delimited (start-of-string or -_/ before "fp8"). A bare
        // space before it does NOT match, matching the Python regex
        // r"(^|[-_/])fp8($|[-_/\s])".
        assert_eq!(native_quant(&json!({"name": "Qwen-FP8"})), "FP8");
        assert_eq!(native_quant(&json!({"name": "fp8-model"})), "FP8");
        // " fp8" (space-prefixed) is NOT a match -> falls through to the default.
        assert_eq!(
            native_quant(&json!({"name": "model", "format": "fp8", "quantization": "Q4_K_M"})),
            "Q4_K_M"
        );
        // GPTQ -> canonical GPTQ-IntN, default Int4 when no digit.
        assert_eq!(
            native_quant(&json!({"name": "Qwen-GPTQ-Int8"})),
            "GPTQ-Int8"
        );
        assert_eq!(native_quant(&json!({"name": "Qwen-GPTQ"})), "GPTQ-Int4");
        // AWQ -> AWQ-Nbit, default 4bit.
        assert_eq!(native_quant(&json!({"name": "Qwen-AWQ-8bit"})), "AWQ-8bit");
        assert_eq!(native_quant(&json!({"name": "Qwen-AWQ"})), "AWQ-4bit");
        // MLX with explicit width, else fall through to quantization.
        assert_eq!(native_quant(&json!({"name": "Q-mlx-6bit"})), "mlx-6bit");
        assert_eq!(
            native_quant(&json!({"name": "Q-mlx", "quantization": "Q5_K_M"})),
            "Q5_K_M"
        );
        // 8bit -> INT8 only for non-GGUF repos.
        assert_eq!(native_quant(&json!({"name": "model-8bit"})), "INT8");
        assert_eq!(
            native_quant(&json!({"name": "model-8bit", "gguf_sources": ["x"]})),
            "Q4_K_M" // gguf present -> not INT8, falls to default quantization
        );
        // Plain model: default quantization.
        assert_eq!(
            native_quant(&json!({"name": "Qwen2.5-7B", "quantization": "Q4_K_M"})),
            "Q4_K_M"
        );
    }

    #[test]
    fn architecture_bonus_tiers() {
        assert_eq!(architecture_bonus(&json!({"name": "Qwen3.6-35B"})), 9.0);
        assert_eq!(architecture_bonus(&json!({"name": "Qwen3.5-30B"})), 8.0);
        assert_eq!(architecture_bonus(&json!({"name": "Qwen3-Next-80B"})), 6.0);
        assert_eq!(architecture_bonus(&json!({"name": "Qwen3-8B"})), 4.0);
        assert_eq!(
            architecture_bonus(&json!({"name": "X", "architecture": "qwen3"})),
            4.0
        );
        assert_eq!(architecture_bonus(&json!({"name": "Qwen2.5-7B"})), 2.0);
        assert_eq!(architecture_bonus(&json!({"name": "Llama-3-8B"})), 0.0);
    }

    #[test]
    fn version_key_parity() {
        assert_eq!(version_key("MiniMax-M2.7"), 2.7);
        assert_eq!(version_key("Qwen3.6-35B"), 3.6);
        assert_eq!(version_key("M2"), 2.0);
        // 235B param count is skipped (bare int >= 100), version 3 wins.
        assert_eq!(version_key("Qwen3-235B"), 3.0);
        assert_eq!(version_key("Llama"), 0.0);
        assert_eq!(version_key(""), 0.0);
        // a letter immediately following the digits disqualifies the match
        // (negative lookahead), so "V4o" does not match "V4"; next token wins.
        assert_eq!(version_key("V4o-Mini2"), 2.0);
    }

    #[test]
    fn estimate_speed_offload_harmonic_blend() {
        // Non-MoE dense model, half offloaded. gpu bw 1008 (RTX 4090), cpu 55.
        // eff_bw = 1 / (0.5/55 + 0.5/1008); model_gb = pb * bpp.
        let model = json!({"name": "X", "parameter_count": "7B", "is_moe": false});
        let system = json!({"gpu_name": "RTX 4090", "backend": "cuda"});
        let pb = models::_active_params_b(&model);
        let model_gb = pb * 0.5; // Q4_K_M bpp
        let eff_bw = 1.0 / (0.5 / 55.0 + 0.5 / 1008.0);
        let expected = (eff_bw / model_gb) * 0.55;
        let got = estimate_speed(&model, "Q4_K_M", "cpu_offload", &system, 0.5);
        assert!((got - expected).abs() < 1e-9, "got {got} expected {expected}");
        // frac<=0 with cpu_offload defaults to 0.5 spill.
        let got0 = estimate_speed(&model, "Q4_K_M", "cpu_offload", &system, 0.0);
        assert!((got0 - expected).abs() < 1e-9);
    }

    #[test]
    fn apple_silicon_bandwidth_and_metal_fallback() {
        // "m4 max" must win over "m4" (length-sorted) and over backend fallback.
        assert_eq!(lookup_bandwidth(Some("Apple M4 Max")), Some(546));
        assert_eq!(lookup_bandwidth(Some("Apple M4")), Some(120));
        assert_eq!(lookup_bandwidth(Some("Apple M1 Ultra")), Some(800));
        // metal fallback constant present.
        assert_eq!(lookup_f64(&FALLBACK_K, "metal", 0.0), 150.0);
        // 9060 added.
        assert_eq!(lookup_bandwidth(Some("RX 9060 XT")), Some(322));
        assert_eq!(lookup_bandwidth(Some("RX 9060")), Some(322));
    }

    #[test]
    fn fit_score_tiers() {
        assert_eq!(fit_score(5.0, 40.0), 70.0); // ratio 0.125 -> 60 + 0.25*40
        assert_eq!(fit_score(30.0, 40.0), 100.0); // ratio 0.75
        assert_eq!(fit_score(35.0, 40.0), 70.0); // ratio 0.875
        assert_eq!(fit_score(38.0, 40.0), 50.0); // ratio 0.95
        assert_eq!(fit_score(50.0, 40.0), 0.0); // over budget
        assert_eq!(fit_score(5.0, 0.0), 0.0); // no budget
    }

    #[test]
    fn speed_and_context_scores() {
        assert_eq!(speed_score(60.0, "general"), 100.0); // clamped
        assert_eq!(speed_score(20.0, "general"), 50.0);
        assert_eq!(context_score(4096, "coding"), 70.0); // target 8192, >= half
        assert_eq!(context_score(6000, "coding"), 70.0);
        assert_eq!(context_score(1000, "coding"), 30.0);
        assert_eq!(context_score(8192, "coding"), 100.0);
    }

    #[test]
    fn analyze_model_7b_on_4090() {
        let model = json!({
            "name": "Qwen2.5-7B-Instruct",
            "provider": "Qwen",
            "parameter_count": "7B",
            "quantization": "Q4_K_M",
            "context_length": 8192,
            "is_moe": false,
        });
        let system = json!({
            "has_gpu": true,
            "gpu_vram_gb": 24.0,
            "gpu_count": 1,
            "available_ram_gb": 64.0,
            "gpu_name": "RTX 4090",
            "backend": "cuda",
        });
        let r = analyze_model(&model, &system, None, None, None).expect("should fit");
        assert_eq!(r["fit_level"], "perfect");
        assert_eq!(r["run_mode"], "gpu");
        assert_eq!(r["quant"], "Q4_K_M");
        assert_eq!(r["params_b"], 7.0);
        assert_eq!(r["context"], 8192);
        assert_eq!(r["required_gb"], 5.0);
        assert_eq!(r["speed_tps"], 158.4);
        // quality now carries the new _architecture_bonus: "qwen2.5" in the
        // name/arch text => +2 over the old 72.0 -> 74.0; the composite shifts
        // 83.9 -> 84.8 accordingly (general weights 0.45/0.30/0.15/0.10).
        assert_eq!(r["score"], 84.8);
        assert_eq!(r["scores"]["quality"], 74.0);
        assert_eq!(r["scores"]["speed"], 100.0);
        assert_eq!(r["scores"]["fit"], 76.7);
        assert_eq!(r["scores"]["context"], 100.0);
        // target_context not requested -> null; release_date absent -> "".
        assert!(r["target_context"].is_null());
        assert_eq!(r["release_date"], "");
        // Key ordering parity (preserve_order): the result dict must keep the
        // Python insertion order. release_date + target_context are now appended.
        let keys: Vec<&str> = r.as_object().unwrap().keys().map(|s| s.as_str()).collect();
        assert_eq!(
            keys,
            vec![
                "name",
                "provider",
                "parameter_count",
                "params_b",
                "is_moe",
                "use_case",
                "fit_level",
                "run_mode",
                "quant",
                "context",
                "required_gb",
                "speed_tps",
                "score",
                "scores",
                "gguf_sources",
                "context_length",
                "release_date",
                "target_context",
            ]
        );
    }

    #[test]
    fn analyze_model_too_tight_sentinel() {
        // Huge model on a tiny GPU-only system -> too_tight sentinel, never None.
        let big = json!({
            "name": "DeepSeek-V3",
            "provider": "DeepSeek",
            "parameter_count": "671B",
            "quantization": "Q4_K_M",
            "context_length": 4096,
        });
        let sys_small = json!({
            "has_gpu": true,
            "gpu_vram_gb": 8.0,
            "gpu_count": 1,
            "available_ram_gb": 8.0,
            "gpu_only": true,
            "gpu_name": "RTX 3060",
            "backend": "cuda",
        });
        let r = analyze_model(&big, &sys_small, None, None, None).expect("too_tight sentinel, not None");
        assert_eq!(r["fit_level"], "too_tight");
        assert_eq!(r["run_mode"], "no_fit");
        assert_eq!(r["params_b"], 671.0);
        assert_eq!(r["required_gb"], 411.7);
        assert_eq!(r["score"], 0);
        assert_eq!(r["speed_tps"], 0);
    }

    #[test]
    fn analyze_model_no_params_is_none() {
        let bad = json!({ "name": "mystery", "parameter_count": "" });
        let system = json!({ "has_gpu": false, "available_ram_gb": 64.0 });
        assert!(analyze_model(&bad, &system, None, None, None).is_none());
    }

    #[test]
    fn analyze_model_multi_gpu_defaults_bf16_and_skips_gguf_tiers() {
        // Plain (non-prequant) model on a 2-GPU rig: default quant becomes BF16,
        // not Q4_K_M (vLLM/SGLang can't serve GGUF Q* on >1 GPU).
        let model = json!({
            "name": "Qwen2.5-7B-Instruct",
            "parameter_count": "7B",
            "quantization": "Q4_K_M",
            "context_length": 4096,
        });
        let system = json!({
            "has_gpu": true,
            "gpu_vram_gb": 48.0,
            "gpu_count": 2,
            "available_ram_gb": 64.0,
            "gpu_name": "RTX 4090",
            "backend": "cuda",
        });
        let r = analyze_model(&model, &system, None, None, None).expect("BF16 should fit 48GB");
        assert_eq!(r["quant"], "BF16");

        // Explicitly asking for a GGUF tier on the multi-GPU rig drops the row.
        assert!(analyze_model(&model, &system, Some("Q4_K_M"), None, None).is_none());
        assert!(analyze_model(&model, &system, Some("IQ2_XS"), None, None).is_none());
    }

    #[test]
    fn analyze_model_native_gpu_only_zeroes_ram() {
        // A prequant (AWQ) model too big for VRAM but small enough for RAM must
        // NOT offload: native_gpu_only zeroes the RAM budget -> too_tight, no
        // cpu_offload row.
        let awq = json!({
            "name": "Big-AWQ",
            "parameter_count": "70B",
            "quantization": "AWQ-4bit",
            "context_length": 4096,
        });
        let system = json!({
            "has_gpu": true,
            "gpu_vram_gb": 8.0,
            "gpu_count": 1,
            "available_ram_gb": 256.0,
            "gpu_name": "RTX 3060",
            "backend": "cuda",
        });
        let r = analyze_model(&awq, &system, None, None, None).expect("too_tight sentinel");
        assert_eq!(r["fit_level"], "too_tight");
        assert_eq!(r["run_mode"], "no_fit");
    }

    #[test]
    fn analyze_model_target_context_clamps() {
        let model = json!({
            "name": "Qwen2.5-7B-Instruct",
            "parameter_count": "7B",
            "quantization": "Q4_K_M",
            "context_length": 32768,
        });
        let system = json!({
            "has_gpu": true,
            "gpu_vram_gb": 24.0,
            "gpu_count": 1,
            "available_ram_gb": 64.0,
            "gpu_name": "RTX 4090",
            "backend": "cuda",
        });
        // target_context below model_ctx -> ctx clamped to target.
        let r = analyze_model(&model, &system, None, None, Some(8192)).expect("fits");
        assert_eq!(r["context"], 8192);
        assert_eq!(r["target_context"], 8192);
        assert_eq!(r["context_length"], 32768);
        // No target -> uses full model context, target_context null.
        let r2 = analyze_model(&model, &system, None, None, None).expect("fits");
        assert_eq!(r2["context"], 32768);
        assert!(r2["target_context"].is_null());
    }

    #[test]
    fn analyze_model_scoring_use_case_independent_of_model_use_case() {
        // use_case in the output reflects the MODEL's inferred use case, while
        // the SCORING use case (weights/targets) comes from scoring_use_case.
        let model = json!({
            "name": "Qwen2.5-Coder-7B",
            "parameter_count": "7B",
            "quantization": "Q4_K_M",
            "context_length": 8192,
        });
        let system = json!({
            "has_gpu": true,
            "gpu_vram_gb": 24.0,
            "gpu_count": 1,
            "available_ram_gb": 64.0,
            "gpu_name": "RTX 4090",
            "backend": "cuda",
        });
        let coding = analyze_model(&model, &system, None, Some("coding"), None).expect("fits");
        let general = analyze_model(&model, &system, None, Some("general"), None).expect("fits");
        // model_use_case stays "coding" in both; quality differs: coding scoring
        // gives +6 to a coder model, general scoring penalizes it -10.
        assert_eq!(coding["use_case"], "coding");
        assert_eq!(general["use_case"], "coding");
        let q_coding = coding["scores"]["quality"].as_f64().unwrap();
        let q_general = general["scores"]["quality"].as_f64().unwrap();
        assert!(q_coding > q_general, "{q_coding} !> {q_general}");
    }

    #[test]
    fn quant_bits_filters_mismatched_prequant() {
        // target Q8 (8-bit) against an AWQ-4bit prequant model. With the new
        // native-prefix gate, a GGUF tier target (Q8_0 / Q4_K_M) is NOT one of
        // the native_quant_prefixes, so a prequant model is dropped (None) for
        // BOTH — Q* tiers are llama.cpp paths, never equivalent to AWQ/GPTQ/FP8.
        let awq = json!({
            "name": "Qwen2.5-7B-AWQ",
            "parameter_count": "7B",
            "quantization": "AWQ-4bit",
            "context_length": 4096,
        });
        let system = json!({
            "has_gpu": true,
            "gpu_vram_gb": 24.0,
            "gpu_count": 1,
            "available_ram_gb": 64.0,
            "gpu_name": "RTX 4090",
            "backend": "cuda",
        });
        assert!(analyze_model(&awq, &system, Some("Q8_0"), None, None).is_none());
        // A GGUF tier target is now rejected for prequant models (separate serving
        // path), so Q4_K_M no longer keeps the AWQ row.
        assert!(analyze_model(&awq, &system, Some("Q4_K_M"), None, None).is_none());
        // Selecting the matching native format DOES keep it.
        assert!(analyze_model(&awq, &system, Some("AWQ-4bit"), None, None).is_some());
    }
}
