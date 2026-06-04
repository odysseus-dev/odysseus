// services/hwfit/profiles.rs  <- services/hwfit/profiles.py
//! Compute intelligent llama.cpp serve profiles from detected hardware.
//!
//! Given a system (VRAM/RAM/arch) and a model, produce 1-4 ready-to-launch
//! profiles — Quality / Balanced / Speed — with concrete llama.cpp flags
//! (n_gpu_layers, n_cpu_moe, cache-type, context). This turns the by-hand tuning
//! (how many MoE layers fit on the GPU, when to spend VRAM on a q8 KV cache vs
//! more context, how much headroom to leave for a vision encoder) into a
//! formula.
//!
//! Pure/deterministic — no benchmarking, no I/O. Reuses the same VRAM math as
//! `fit.py`/`models.py` so "what the Cookbook recommends" and "what it serves"
//! agree.
//!
//! NOTE: token/s figures are NOT computed here — real speed on partial-offload
//! MoE is CPU-bound and not reliably predictable from specs. The UI labels
//! profiles by their tradeoff (Quality/Balanced/Speed), and the VRAM fit (the
//! part that decides whether it even loads) is what's computed from real
//! numbers.
//!
//! ## Model/system-row loose-typing convention
//!
//! Like the sibling `models`/`fit`/`hardware` modules, a "model" and a "system"
//! arrive as untyped [`serde_json::Value`] objects read with `.get(key,
//! default)` duck-typing. The returned profiles are likewise [`Value`] objects
//! (built via `serde_json::json!`, which preserves insertion order under the
//! crate's `preserve_order` feature) so the JSON key ordering the route handler
//! and the UI depend on matches the Python `dict` literal exactly.

use crate::services::hwfit::models::{
    QUANT_BPP, _active_params_b, is_prequantized, model_f64, model_str, model_truthy, params_b,
};
use once_cell::sync::Lazy;
use serde_json::{json, Value};
use std::collections::HashSet;

/// `_KV_FACTOR` — GGUF KV-cache cost per token, in bytes-per-active-billion-param,
/// by cache type. `q4_0` is ~half of `q8_0` is ~half of `f16`. The `8e-6` base in
/// `estimate_memory_gb` is the `q8_0`-ish figure; scale from there.
static KV_FACTOR: Lazy<std::collections::HashMap<&'static str, f64>> = Lazy::new(|| {
    std::collections::HashMap::from([("q4_0", 0.5), ("q8_0", 1.0), ("f16", 2.0)])
});

/// `_QUANT_LADDER` — quant ladder from highest quality/size down. A profile that
/// wants "best quant that fits fully on GPU" walks this until one fits.
static QUANT_LADDER: Lazy<Vec<&'static str>> =
    Lazy::new(|| vec!["Q8_0", "Q6_K", "Q5_K_M", "Q4_K_M", "Q3_K_M", "Q2_K"]);

/// `_weights_gb(model, quant, fixed_gb=None)` — VRAM for the full weights. When
/// `fixed_gb` is given (serving a specific GGUF file already on disk), use its
/// real size — the quant is whatever the file is, not something we get to pick.
fn weights_gb(model: &Value, quant: &str, fixed_gb: Option<f64>) -> f64 {
    // `if fixed_gb and fixed_gb > 0: return float(fixed_gb)`
    if let Some(g) = fixed_gb {
        if g > 0.0 {
            return g;
        }
    }
    // `params_b(model) * QUANT_BPP.get(quant, 0.58)`
    params_b(model) * QUANT_BPP.get(quant).copied().unwrap_or(0.58)
}

/// `_kv_gb(model, ctx, kv_type)` — KV-cache VRAM at a context length and cache
/// type.
fn kv_gb(model: &Value, ctx: f64, kv_type: &str) -> f64 {
    let kv_params = _active_params_b(model);
    0.000008 * kv_params * ctx * KV_FACTOR.get(kv_type).copied().unwrap_or(1.0)
}

/// `_n_layers(model)` — best-effort total transformer block count (for
/// `n-cpu-moe` math).
fn n_layers(model: &Value) -> i64 {
    for k in ["num_hidden_layers", "n_layers", "num_layers", "block_count"] {
        // `if isinstance(v, (int, float)) and v > 0: return int(v)`
        if let Some(v) = model_f64(model, k) {
            if v > 0.0 {
                return v as i64;
            }
        }
    }
    // Fallback heuristic by size — most MoE/dense LLMs land 28-64 layers.
    let pb = params_b(model);
    if pb >= 60.0 {
        return 64;
    }
    if pb >= 25.0 {
        return 48;
    }
    if pb >= 12.0 {
        return 40;
    }
    32
}

/// `_cpu_moe_for_budget(model, quant, kv_gb, vram_budget_gb, fixed_gb=None)` —
/// how many MoE layers must move to CPU so weights+KV fit `vram_budget_gb`.
///
/// Returns `(n_cpu_moe, fits_fully)`. When the model already fits, `n_cpu_moe=0`.
/// Each offloaded layer frees roughly `weights/n_layers` of VRAM. We only model
/// this for MoE (where `--n-cpu-moe` applies); dense models just report whether
/// they fit at the given `n_gpu_layers=999`.
fn cpu_moe_for_budget(
    model: &Value,
    quant: &str,
    kv: f64,
    vram_budget_gb: f64,
    fixed_gb: Option<f64>,
) -> (i64, bool) {
    let weights = weights_gb(model, quant, fixed_gb);
    let needed = weights + kv + 0.6; // +0.6 GB runtime/compute buffers
    if needed <= vram_budget_gb {
        return (0, true);
    }
    if !model_truthy(model, "is_moe") {
        // Dense: no per-expert offload knob; either it fits or it spills via -ngl.
        return (0, false);
    }
    let layers = n_layers(model);
    // `per_layer = weights / max(layers, 1)`
    let per_layer = weights / (layers.max(1) as f64);
    let overflow = needed - vram_budget_gb;
    // `n = math.ceil(overflow / max(per_layer, 1e-6))`
    let n = (overflow / per_layer.max(1e-6)).ceil() as i64;
    // `n = max(0, min(n, layers))`  — clamp
    let n = n.clamp(0, layers);
    (n, false)
}

/// `compute_serve_profiles(system, model, serve_weights_gb=None, serve_quant=None)`
/// — return a list of profile dicts for llama.cpp serving of `model` on
/// `system`.
///
/// Each profile: `{key, label, quant, n_gpu_layers, n_cpu_moe, cache_type, ctx,
/// est_vram_gb, fits, offloads, note}`. Empty list if no GGUF path makes sense
/// (caller should fall back to manual flags).
///
/// DOWNLOAD mode (default): the quant isn't chosen yet, so profiles vary it
/// (Quality=Q6, Balanced=Q4, Speed=Q2…) to show download options.
///
/// SERVE mode (`serve_weights_gb` set): a specific GGUF file already exists on
/// disk — its quant is FIXED. Profiles then keep that quant/size and differ only
/// in the actual serving knobs (`n_cpu_moe`, KV-cache type, context).
/// `serve_quant` is the file's quant label (e.g. `"Q4_K_M"`) just for display.
pub fn compute_serve_profiles(
    system: &Value,
    model: &Value,
    serve_weights_gb: Option<f64>,
    serve_quant: Option<&str>,
) -> Vec<Value> {
    // `vram = float(system.get("gpu_vram_gb") or 0)` — Python `or`: any falsy
    // (missing/null/0/non-number) collapses to 0.0.
    let vram = match system.get("gpu_vram_gb").and_then(Value::as_f64) {
        Some(v) if v != 0.0 => v,
        _ => 0.0,
    };
    if vram <= 0.0 {
        return Vec::new();
    }

    // `serve_mode = bool(serve_weights_gb and serve_weights_gb > 0)`
    let serve_mode = matches!(serve_weights_gb, Some(g) if g > 0.0);

    // Never propose more context than the model was trained for — asking
    // llama.cpp for ctx > n_ctx_train triggers a "training context overflow"
    // and, with a quantized KV cache, an oversized allocation that can crash the
    // GPU (radv/amdgpu ErrorDeviceLost). Cap every profile at the model's real
    // limit.
    let mut model_ctx_max: i64 = 0;
    for k in [
        "context_length",
        "max_position_embeddings",
        "n_ctx_train",
        "context",
    ] {
        // `if isinstance(v, (int, float)) and v > 0: model_ctx_max = int(v); break`
        if let Some(v) = model_f64(model, k) {
            if v > 0.0 {
                model_ctx_max = v as i64;
                break;
            }
        }
    }
    if model_ctx_max <= 0 {
        model_ctx_max = 131072; // conservative default when the catalog omits it
    }

    // Vision models need headroom for the image encoder (~1 GB on top of weights).
    let is_vision = model_truthy(model, "is_multimodal")
        || model_truthy(model, "vision")
        || model_truthy(model, "mmproj")
        || model_str(model, "name").to_lowercase().contains("vl");
    let headroom = if is_vision { 1.1 } else { 0.4 };
    // `budget = max(vram - headroom, 1.0)`
    let budget = (vram - headroom).max(1.0);

    // Prequantized (AWQ/GPTQ/FP8) served via GGUF fallback use a fixed ~Q4 quant;
    // GGUF models can pick their quant. Pick a sensible per-profile quant.
    // `fixed_quant = model.get("quantization") if is_prequantized(model) else None`
    //
    // `fixed_quant` is only consumed via Python's `if fixed_quant:` truthiness in
    // `_pick_quant`, so an empty-string `quantization` (falsy) must NOT pin —
    // collapse it to `None` here to match that, exactly as a missing key would.
    let fixed_quant: Option<String> = if is_prequantized(model) {
        match model.get("quantization") {
            Some(Value::String(s)) if !s.is_empty() => Some(s.clone()),
            _ => None,
        }
    } else {
        None
    };

    let is_moe = model_truthy(model, "is_moe");

    // `_pick_quant(prefer, require_full_fit)` — choose a quant for a profile.
    //
    // - fixed_quant (AWQ/GPTQ/FP8 served via GGUF): always that.
    // - require_full_fit=True (Speed): walk DOWN from `prefer` to the best quant
    //   whose weights fit fully on the GPU (no offload) — fastest.
    // - require_full_fit=False (Quality on MoE): keep `prefer` even if it must
    //   offload experts to CPU; that's the whole point of n-cpu-moe on a card too
    //   small to hold the weights. For dense models we can't offload per-expert,
    //   so fall back to the largest fully-fitting quant.
    let pick_quant = |prefer: &str, require_full_fit: bool| -> String {
        if let Some(ref fq) = fixed_quant {
            return fq.clone();
        }
        // `start = _QUANT_LADDER.index(prefer) if prefer in _QUANT_LADDER else 3`
        let start = QUANT_LADDER
            .iter()
            .position(|&q| q == prefer)
            .unwrap_or(3);
        if require_full_fit || !is_moe {
            for &q in &QUANT_LADDER[start..] {
                if weights_gb(model, q, None) + 0.6 <= budget {
                    return q.to_string();
                }
            }
            return QUANT_LADDER[QUANT_LADDER.len() - 1].to_string();
        }
        // MoE quality: keep the preferred (big) quant; offload handles overflow.
        prefer.to_string()
    };

    // spec tuple: (key, label, prefer_quant, full_fit, kv_type, ctx, note)
    type Spec = (&'static str, &'static str, String, bool, &'static str, i64, &'static str);
    let specs: Vec<Spec> = if serve_mode {
        // Fixed file on disk — quant can't change. Vary only the serving knobs.
        // `fq = serve_quant or model.get("quantization") or "GGUF"`
        let fq: String = serve_quant
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string())
            .or_else(|| {
                let q = model_str(model, "quantization");
                if q.is_empty() {
                    None
                } else {
                    Some(q)
                }
            })
            .unwrap_or_else(|| "GGUF".to_string());
        vec![
            (
                "quality",
                "Quality",
                fq.clone(),
                false,
                "q8_0",
                131072,
                "Sharp q8 KV cache + full context. Best long-context accuracy; offloads MoE layers to CPU if needed.",
            ),
            (
                "balanced",
                "Balanced",
                fq.clone(),
                false,
                "q4_0",
                131072,
                "Compact q4 KV at full context — good speed/quality mix.",
            ),
            (
                "speed",
                "Speed",
                fq,
                false,
                "q4_0",
                32768,
                "Trimmed context + light KV for the fastest tokens/s.",
            ),
        ]
    } else {
        vec![
            (
                "quality",
                "Quality",
                "Q6_K".to_string(),
                false,
                "q8_0",
                131072,
                "Biggest quant + sharp q8 KV cache. Best answers; offloads MoE layers to CPU if needed.",
            ),
            (
                "balanced",
                "Balanced",
                "Q4_K_M".to_string(),
                false,
                "q4_0",
                131072,
                "Q4 weights + compact q4 KV. Good speed/quality mix at full context.",
            ),
            (
                "speed",
                "Speed",
                "Q4_K_M".to_string(),
                true,
                "q4_0",
                32768,
                "Smallest offload + trimmed context for the fastest tokens/s.",
            ),
        ]
    };

    let mut profiles: Vec<Value> = Vec::new();
    for (key, label, prefer_q, full_fit, kv_type, ctx, note) in specs {
        // In serve mode the quant is fixed (the file's); in download mode we pick.
        let quant = if serve_mode {
            prefer_q.clone()
        } else {
            pick_quant(&prefer_q, full_fit)
        };
        // Shrink context if even the chosen KV won't fit alongside weights. Start
        // from the smaller of the profile's target and the model's limit.
        let mut cur_ctx = ctx.min(model_ctx_max);
        while cur_ctx >= 8192 {
            let kv = kv_gb(model, cur_ctx as f64, kv_type);
            let (n_cpu_moe, fits) =
                cpu_moe_for_budget(model, &quant, kv, budget, serve_weights_gb);
            let est = weights_gb(model, &quant, serve_weights_gb) + kv + 0.6;
            // If a non-MoE model can't fit even fully offloaded, try less context.
            if model_truthy(model, "is_moe") || fits || cur_ctx <= 8192 {
                profiles.push(json!({
                    "key": key,
                    "label": label,
                    "quant": quant,
                    "n_gpu_layers": 999,
                    "n_cpu_moe": n_cpu_moe,
                    "cache_type": kv_type,
                    "ctx": cur_ctx,
                    // When experts offload, GPU-resident VRAM tops out at the
                    // budget (weights beyond it live in system RAM), so cap the
                    // estimate at `budget`, not the full card — this also leaves
                    // the vision-encoder headroom visible in the number.
                    "est_vram_gb": round1(est.min(budget)),
                    // For MoE we treat it as fitting via offload; report whether
                    // it fit WITHOUT offload as the "clean" flag.
                    "fits": fits || model_truthy(model, "is_moe"),
                    "offloads": n_cpu_moe > 0,
                    "note": note,
                }));
                break;
            }
            cur_ctx /= 2; // `cur_ctx //= 2` (positive ints → floor division)
        }
    }

    // De-dupe identical profiles (e.g. tiny model where all three collapse to the
    // same all-GPU config) — keep the first/highest-quality label.
    let mut seen: HashSet<(String, i64, String, i64)> = HashSet::new();
    let mut deduped: Vec<Value> = Vec::new();
    for p in profiles {
        let sig = (
            p["quant"].as_str().unwrap_or("").to_string(),
            p["n_cpu_moe"].as_i64().unwrap_or(0),
            p["cache_type"].as_str().unwrap_or("").to_string(),
            p["ctx"].as_i64().unwrap_or(0),
        );
        if seen.contains(&sig) {
            continue;
        }
        seen.insert(sig);
        deduped.push(p);
    }
    deduped
}

/// `round(x, 1)` — Python's built-in `round` uses banker's (round-half-to-even)
/// rounding. The `est_vram_gb` field is the only rounded value; reproduce
/// CPython's `round(x, 1)` exactly so a `.x5` tie can't shift a model across a
/// fit boundary.
///
/// The naive approach (`(x * 10.0).floor()` + half-to-even on the f64 diff)
/// fails because floating-point multiplication can produce an exact tie that
/// the true decimal value does NOT have — e.g. `0.35_f64 * 10.0 == 3.5`
/// exactly, but the true decimal value of `0.35_f64` is
/// `0.34999999999999997779…`, so `round(0.35, 1)` should be `0.3`, not `0.4`.
///
/// CPython's `float.__round__` avoids this by working with the **exact rational
/// value** of the double (mantissa × 2^exp). We replicate that here using
/// `i128` integer arithmetic, which is wide enough for any finite `f64` value
/// in the range used by `est_vram_gb` (0 – ~1000 GB).
fn round1(x: f64) -> f64 {
    round_half_even(x, 1)
}

/// Round `x` to `ndigits` decimal places using round-half-to-even, matching
/// CPython's `round(x, ndigits)` exactly.
///
/// Algorithm: decompose `x` into its exact rational representation
/// `sign × mantissa / 2^(-exp)` (or `sign × mantissa × 2^exp` when exp ≥ 0)
/// using the IEEE 754 bit layout, then compute the floor and remainder of
/// `x × 10^ndigits` in exact integer arithmetic to decide the rounding
/// direction without any floating-point intermediate that can introduce error.
fn round_half_even(x: f64, ndigits: i32) -> f64 {
    if !x.is_finite() {
        return x;
    }
    // Decompose the f64 bit pattern into (sign, mantissa, binary_exp) such
    // that `x = sign * mantissa * 2^binary_exp` exactly.
    let bits = x.to_bits();
    let sign: i128 = if bits >> 63 != 0 { -1 } else { 1 };
    let exp_bits = ((bits >> 52) & 0x7FF) as i32;
    let raw_mantissa = (bits & 0x000F_FFFF_FFFF_FFFF) as i128;
    let (mantissa, binary_exp): (i128, i32) = if exp_bits == 0 {
        // Subnormal (including ±0): implicit leading bit is 0.
        (raw_mantissa, -1022 - 52)
    } else {
        // Normal: implicit leading 1 bit.
        (raw_mantissa | (1_i128 << 52), exp_bits - 1023 - 52)
    };
    // We want to compute floor(x * 10^ndigits) and the remainder, all in exact
    // integer arithmetic.
    //
    // `x = sign * mantissa * 2^binary_exp`
    //
    // Case A: binary_exp >= 0  →  x * 10^n = sign * mantissa * 2^binary_exp * 10^n
    //         numerator = mantissa * 2^binary_exp * 10^n, denominator = 1
    // Case B: binary_exp < 0  →  x * 10^n = sign * mantissa * 10^n / 2^(-binary_exp)
    //         numerator = mantissa * 10^n, denominator = 2^(-binary_exp)
    //
    // We then perform floor-div and remainder on numerator / denominator (both
    // positive; sign is tracked separately and applied at the end so that
    // floor(-x) = -ceil(x) — consistent with Python's `int(floor(y))`).
    let scale: i128 = 10_i128.pow(ndigits as u32);
    let (num, den): (i128, i128) = if binary_exp >= 0 {
        // Multiply mantissa by 2^binary_exp and by 10^ndigits.
        // binary_exp is at most ~970 for normal doubles — too large for i128.
        // However, for any finite value in our domain (< ~10^15 GB of VRAM),
        // binary_exp is small enough. Guard with a fast-path: if the result
        // would clearly have no fractional part (x * 10^n is a large integer),
        // just return x rounded to ndigits via f64 arithmetic.
        if binary_exp > 120 {
            // No fractional part at all; x * 10^n >> 0.
            let factor = 10f64.powi(ndigits);
            return (x * factor).round() / factor;
        }
        let num = mantissa.wrapping_shl(binary_exp as u32) * scale;
        (num, 1_i128)
    } else {
        let neg_exp = (-binary_exp) as u32;
        if neg_exp > 120 {
            // |x| < 2^-120 * 10^ndigits — rounds to 0.
            return 0.0_f64.copysign(x);
        }
        let den = 1_i128.wrapping_shl(neg_exp);
        (mantissa * scale, den)
    };
    // Euclidean floor-div: floor_val = floor(num / den), rem = num - floor_val * den.
    // Both num and den are positive; sign is separate.
    let floor_val = num / den;  // truncates toward zero (= floor for non-negative)
    let remainder = num - floor_val * den;
    // Compare remainder / den to 1/2:  2*remainder vs den.
    let twice_rem = 2 * remainder;
    let rounded_abs: i128 = if twice_rem < den {
        floor_val               // below midpoint → keep floor
    } else if twice_rem > den {
        floor_val + 1           // above midpoint → round up
    } else {
        // Exact tie → round-half-to-even: choose the even value.
        if floor_val % 2 == 0 { floor_val } else { floor_val + 1 }
    };
    // Re-apply sign (sign=-1 means we were working with |x|, so negate the floor).
    // For negative x: Python's round(-0.35, 1) == -0.3 (rounds toward zero at tie).
    // Our floor_val is floor(|x| * 10^n), so the signed rounded value is:
    //   positive x → rounded_abs / 10^n
    //   negative x → -(rounded_abs / 10^n)  [same magnitude, negated]
    let signed = sign * rounded_abs;
    signed as f64 / scale as f64
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // --- shared fixtures (mirrors tests/test_serve_profiles.py) --------------

    fn qwen_35b_moe() -> Value {
        json!({
            "name": "Qwen3.6-35B-A3B",
            "parameter_count": "35B",
            "is_moe": true,
            "active_parameters": 3_000_000_000i64,
            "num_hidden_layers": 48,
        })
    }

    fn dense_8b() -> Value {
        json!({
            "name": "Qwen3-8B",
            "parameter_count": "8B",
            "is_moe": false,
            "num_hidden_layers": 36,
        })
    }

    fn sys(vram: f64) -> Value {
        json!({"backend": "rocm", "gpu_vram_gb": vram, "gpu_family": "rdna"})
    }

    fn by_key<'a>(profs: &'a [Value], key: &str) -> Option<&'a Value> {
        profs.iter().find(|p| p["key"].as_str() == Some(key))
    }

    // --- helper-level unit tests --------------------------------------------

    #[test]
    fn weights_gb_uses_fixed_when_positive() {
        let m = qwen_35b_moe();
        // fixed_gb > 0 wins over computed weights.
        assert!((weights_gb(&m, "Q4_K_M", Some(20.6)) - 20.6).abs() < 1e-9);
        // fixed_gb == 0 falls through to params_b * bpp.
        let computed = params_b(&m) * 0.58;
        assert!((weights_gb(&m, "Q4_K_M", Some(0.0)) - computed).abs() < 1e-9);
        assert!((weights_gb(&m, "Q4_K_M", None) - computed).abs() < 1e-9);
        // Unknown quant defaults to 0.58 bpp.
        assert!((weights_gb(&m, "NOPE", None) - params_b(&m) * 0.58).abs() < 1e-9);
    }

    #[test]
    fn kv_gb_scales_with_cache_type_and_active_params() {
        let m = qwen_35b_moe();
        let kv_params = _active_params_b(&m); // 3.0 (active params)
        let base = 0.000008 * kv_params * 4096.0;
        assert!((kv_gb(&m, 4096.0, "q8_0") - base * 1.0).abs() < 1e-12);
        assert!((kv_gb(&m, 4096.0, "q4_0") - base * 0.5).abs() < 1e-12);
        assert!((kv_gb(&m, 4096.0, "f16") - base * 2.0).abs() < 1e-12);
        // Unknown cache type defaults to factor 1.0.
        assert!((kv_gb(&m, 4096.0, "weird") - base * 1.0).abs() < 1e-12);
    }

    #[test]
    fn n_layers_explicit_and_fallback() {
        assert_eq!(n_layers(&json!({"num_hidden_layers": 48})), 48);
        assert_eq!(n_layers(&json!({"n_layers": 40})), 40);
        assert_eq!(n_layers(&json!({"block_count": 28})), 28);
        // Fallback by params_b size buckets.
        assert_eq!(n_layers(&json!({"parameter_count": "70B"})), 64);
        assert_eq!(n_layers(&json!({"parameter_count": "30B"})), 48);
        assert_eq!(n_layers(&json!({"parameter_count": "13B"})), 40);
        assert_eq!(n_layers(&json!({"parameter_count": "7B"})), 32);
        // Zero/negative layer counts are ignored, falls to heuristic.
        assert_eq!(n_layers(&json!({"num_hidden_layers": 0, "parameter_count": "7B"})), 32);
    }

    #[test]
    fn cpu_moe_fits_returns_zero() {
        // Tiny model fits a big budget fully: (0, true).
        let m = json!({"parameter_count": "1B", "is_moe": true, "active_parameters": 1_000_000_000i64, "num_hidden_layers": 24});
        let kv = kv_gb(&m, 4096.0, "q4_0");
        let (n, fits) = cpu_moe_for_budget(&m, "Q4_K_M", kv, 24.0, None);
        assert_eq!(n, 0);
        assert!(fits);
    }

    #[test]
    fn cpu_moe_dense_no_offload_knob() {
        // A dense model that overflows reports (0, false) — no per-expert knob.
        let m = json!({"parameter_count": "70B", "is_moe": false, "num_hidden_layers": 80});
        let (n, fits) = cpu_moe_for_budget(&m, "Q8_0", 1.0, 8.0, None);
        assert_eq!(n, 0);
        assert!(!fits);
    }

    #[test]
    fn cpu_moe_offloads_clamped_to_layers() {
        // A big MoE on a small budget must offload some layers; clamp ≤ layers.
        let m = qwen_35b_moe();
        let kv = kv_gb(&m, 131072.0, "q8_0");
        let (n, fits) = cpu_moe_for_budget(&m, "Q6_K", kv, 15.5, None);
        assert!(n > 0, "expected offload, got {n}");
        assert!(n <= 48, "clamped to n_layers, got {n}");
        assert!(!fits, "fits_fully must be false when offloading");
    }

    // --- behavioral tests (ported from tests/test_serve_profiles.py) --------

    #[test]
    fn big_moe_on_small_card_offloads_not_fails() {
        let profs = compute_serve_profiles(&sys(15.9), &qwen_35b_moe(), None, None);
        assert!(!profs.is_empty(), "expected at least one profile");
        let q = by_key(&profs, "quality").expect("quality profile");
        assert!(q["n_cpu_moe"].as_i64().unwrap() > 0);
        assert_eq!(q["offloads"].as_bool(), Some(true));
        assert_eq!(q["cache_type"].as_str(), Some("q8_0"));
        assert!(q["est_vram_gb"].as_f64().unwrap() <= 16.0);
    }

    #[test]
    fn profiles_never_exceed_vram() {
        for vram in [8.0, 12.0, 16.0, 24.0] {
            for p in compute_serve_profiles(&sys(vram), &qwen_35b_moe(), None, None) {
                assert!(
                    p["est_vram_gb"].as_f64().unwrap() <= vram + 0.05,
                    "vram={vram} p={p}"
                );
            }
        }
    }

    #[test]
    fn small_model_stays_fully_on_gpu() {
        for p in compute_serve_profiles(&sys(15.9), &dense_8b(), None, None) {
            assert_eq!(p["n_cpu_moe"].as_i64(), Some(0));
            assert_eq!(p["offloads"].as_bool(), Some(false));
        }
    }

    #[test]
    fn speed_profile_is_lighter_than_quality() {
        let profs = compute_serve_profiles(&sys(15.9), &qwen_35b_moe(), None, None);
        let speed = by_key(&profs, "speed");
        let quality = by_key(&profs, "quality");
        if let (Some(s), Some(q)) = (speed, quality) {
            assert!(s["n_cpu_moe"].as_i64().unwrap() <= q["n_cpu_moe"].as_i64().unwrap());
            assert!(s["ctx"].as_i64().unwrap() <= q["ctx"].as_i64().unwrap());
        }
    }

    #[test]
    fn flags_are_launchable() {
        for p in compute_serve_profiles(&sys(15.9), &qwen_35b_moe(), None, None) {
            assert_eq!(p["n_gpu_layers"].as_i64(), Some(999));
            let n_cpu_moe = p["n_cpu_moe"].as_i64().expect("n_cpu_moe is int");
            assert!(n_cpu_moe >= 0);
            assert!(matches!(
                p["cache_type"].as_str(),
                Some("q4_0") | Some("q8_0") | Some("f16")
            ));
            assert!(p["ctx"].as_i64().unwrap() >= 8192);
            assert!(!p["quant"].as_str().unwrap().is_empty());
        }
    }

    #[test]
    fn context_capped_at_model_limit() {
        let mut small_ctx_model = qwen_35b_moe();
        small_ctx_model["name"] = json!("X");
        small_ctx_model["context_length"] = json!(32768);
        for p in compute_serve_profiles(&sys(15.9), &small_ctx_model, None, None) {
            assert!(p["ctx"].as_i64().unwrap() <= 32768, "p={p}");
        }
    }

    #[test]
    fn no_gpu_returns_empty() {
        let profs = compute_serve_profiles(
            &json!({"backend": "cpu_x86", "gpu_vram_gb": 0}),
            &qwen_35b_moe(),
            None,
            None,
        );
        assert!(profs.is_empty());
        // Missing gpu_vram_gb entirely also yields empty.
        assert!(compute_serve_profiles(&json!({"backend": "cpu"}), &qwen_35b_moe(), None, None).is_empty());
    }

    #[test]
    fn vision_model_leaves_encoder_headroom() {
        let mut vis = qwen_35b_moe();
        vis["name"] = json!("Qwen3-VL-35B");
        vis["is_multimodal"] = json!(true);
        for p in compute_serve_profiles(&sys(15.9), &vis, None, None) {
            // ~1.1 GB encoder headroom: est ≤ 15.9 - 1.0 + 0.05.
            assert!(p["est_vram_gb"].as_f64().unwrap() <= 15.9 - 1.0 + 0.05, "p={p}");
        }
    }

    #[test]
    fn vision_detected_via_vl_in_name() {
        // No explicit multimodal flags, but "vl" in the name triggers the encoder
        // headroom (budget = vram - 1.1).
        let mut vis = dense_8b();
        vis["name"] = json!("Qwen3-VL-8B");
        for p in compute_serve_profiles(&sys(15.9), &vis, None, None) {
            assert!(p["est_vram_gb"].as_f64().unwrap() <= 15.9 - 1.0 + 0.05, "p={p}");
        }
    }

    #[test]
    fn serve_mode_keeps_fixed_quant() {
        let profs = compute_serve_profiles(
            &sys(15.9),
            &qwen_35b_moe(),
            Some(20.6),
            Some("Q4_K_M"),
        );
        assert!(!profs.is_empty());
        assert!(
            profs.iter().all(|p| p["quant"].as_str() == Some("Q4_K_M")),
            "{:?}",
            profs.iter().map(|p| p["quant"].clone()).collect::<Vec<_>>()
        );
        // The knobs should still differ across profiles (KV type and/or context).
        let kvs: HashSet<&str> = profs.iter().filter_map(|p| p["cache_type"].as_str()).collect();
        let ctxs: HashSet<i64> = profs.iter().filter_map(|p| p["ctx"].as_i64()).collect();
        assert!(kvs.len() > 1 || ctxs.len() > 1, "serve profiles are identical");
        // All must fit the card.
        assert!(profs.iter().all(|p| p["est_vram_gb"].as_f64().unwrap() <= 16.0));
    }

    #[test]
    fn serve_mode_falls_back_to_gguf_label() {
        // No serve_quant and no model quantization → label "GGUF".
        let profs = compute_serve_profiles(&sys(15.9), &qwen_35b_moe(), Some(20.6), None);
        assert!(!profs.is_empty());
        assert!(profs.iter().all(|p| p["quant"].as_str() == Some("GGUF")));
    }

    #[test]
    fn prequantized_pins_quant_in_download_mode() {
        // An AWQ-4bit model: fixed_quant pins every profile to "AWQ-4bit".
        let m = json!({
            "name": "Qwen3-35B-AWQ-4bit",
            "parameter_count": "35B",
            "is_moe": true,
            "active_parameters": 3_000_000_000i64,
            "num_hidden_layers": 48,
            "quantization": "AWQ-4bit",
        });
        let profs = compute_serve_profiles(&sys(24.0), &m, None, None);
        assert!(!profs.is_empty());
        assert!(profs.iter().all(|p| p["quant"].as_str() == Some("AWQ-4bit")));
    }

    #[test]
    fn dedup_collapses_identical_profiles() {
        // A tiny dense model on a huge card: all three specs collapse to the same
        // (quant, n_cpu_moe, cache_type, ctx)? Not necessarily — Quality uses
        // q8_0 and Balanced/Speed use q4_0, and Speed trims ctx. But a tiny model
        // with a small ctx limit can collapse Balanced/Speed. Verify the dedup
        // keeps signatures unique.
        let m = json!({
            "name": "tiny",
            "parameter_count": "1B",
            "is_moe": false,
            "num_hidden_layers": 24,
            "context_length": 8192,
        });
        let profs = compute_serve_profiles(&sys(24.0), &m, None, None);
        let mut sigs: HashSet<(String, i64, String, i64)> = HashSet::new();
        for p in &profs {
            let sig = (
                p["quant"].as_str().unwrap().to_string(),
                p["n_cpu_moe"].as_i64().unwrap(),
                p["cache_type"].as_str().unwrap().to_string(),
                p["ctx"].as_i64().unwrap(),
            );
            assert!(sigs.insert(sig), "duplicate profile survived dedup: {p}");
        }
    }

    #[test]
    fn round1_matches_python_banker_rounding() {
        // Python round(x, 1): half-to-even at the first decimal, but binary float
        // representation means many "ties" aren't exact ties. These match CPython
        // `round(x, 1)` exactly (verified against the interpreter).
        assert_eq!(round1(0.25), 0.2); // exact .5 tie, 2 is even → down
        assert_eq!(round1(0.35), 0.3); // 0.35 binary < .35 → down
        assert_eq!(round1(15.95), 15.9); // 15.95 binary < .95 → down (CPython 15.9)
        assert_eq!(round1(2.05), 2.0); // 2.05 binary < .05 → down
        assert_eq!(round1(0.0), 0.0);
    }

    #[test]
    fn budget_clamped_to_one_gb_minimum() {
        // A tiny card minus headroom never goes below 1.0 GB budget — profiles
        // still produced (MoE always "fits" via offload).
        let profs = compute_serve_profiles(&sys(1.0), &qwen_35b_moe(), None, None);
        assert!(!profs.is_empty());
        for p in &profs {
            // est_vram_gb is capped at the budget = max(1.0 - 0.4, 1.0) = 1.0.
            assert!(p["est_vram_gb"].as_f64().unwrap() <= 1.0 + 1e-9, "p={p}");
        }
    }
}
