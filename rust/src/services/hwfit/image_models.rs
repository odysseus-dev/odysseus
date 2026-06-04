// services/hwfit/image_models.rs  <- services/hwfit/image_models.py
//! Image generation model registry and VRAM fitting for Cookbook.
//!
//! Pure, zero external deps (the registry is static data). Output rows are
//! [`serde_json::Value`] objects whose key insertion order mirrors the Python
//! dicts exactly (serde_json is compiled with `preserve_order`). Consumed by
//! [`super::fit::rank_models`] (the `image_gen` use-case path) and the hwfit
//! route. Builds on the default profile.

use once_cell::sync::Lazy;
use serde_json::{json, Map, Value};

/// `IMAGE_MODEL_REGISTRY` — curated registry of image generation models
/// supported by diffusers. ONLY verified HuggingFace repo IDs. VRAM estimates
/// are for inference (single image generation).
///
/// Held as a `Lazy<Vec<Value>>` of object maps so the `model.get(...)` /
/// `model["..."]` access in `rank_image_models` reads like the Python and the
/// emitted output preserves field order. `vram_q4` is `null` (Python `None`)
/// for the rows that declare it so.
pub static IMAGE_MODEL_REGISTRY: Lazy<Vec<Value>> = Lazy::new(|| {
    vec![
        // -- Z-Image (Alibaba Tongyi) --
        json!({
            "id": "Tongyi-MAI/Z-Image-Turbo",
            "name": "Z-Image Turbo",
            "provider": "Tongyi",
            "params_b": 6.0,
            "vram_bf16": 19.0,
            "vram_fp8": 10.0,
            "vram_q4": 6.0,
            "default_quant": "BF16",
            "quant_repos": {
                "FP8": "drbaph/Z-Image-Turbo-FP8",
            },
            "capabilities": ["text-to-image"],
            "description": "6B distilled, 8-step. Sub-second on H800. Apache 2.0.",
            "quality": 92,
            "speed": 95,
            "released": "2025-12",
        }),
        json!({
            "id": "Tongyi-MAI/Z-Image",
            "name": "Z-Image",
            "provider": "Tongyi",
            "params_b": 6.0,
            "vram_bf16": 19.0,
            "vram_fp8": 10.0,
            "vram_q4": 6.0,
            "default_quant": "BF16",
            "quant_repos": {
                "FP8": "drbaph/Z-Image-fp8",
            },
            "capabilities": ["text-to-image"],
            "description": "Full undistilled model. Highest creative freedom. Apache 2.0.",
            "quality": 93,
            "speed": 70,
            "released": "2025-12",
        }),
        // -- Qwen Image --
        json!({
            "id": "Qwen/Qwen-Image-2512",
            "name": "Qwen Image 2512",
            "provider": "Qwen",
            "params_b": 20.0,
            "vram_bf16": 42.0,
            "vram_fp8": 22.0,
            "vram_q4": 14.0,
            "default_quant": "FP8",
            "quant_repos": {},
            "capabilities": ["text-to-image", "text-rendering"],
            "description": "Dec 2025 update. Better humans, finer detail, strong text. Apache 2.0.",
            "quality": 95,
            "speed": 50,
            "released": "2025-12",
        }),
        json!({
            "id": "Qwen/Qwen-Image",
            "name": "Qwen Image",
            "provider": "Qwen",
            "params_b": 20.0,
            "vram_bf16": 42.0,
            "vram_fp8": 22.0,
            "vram_q4": 14.0,
            "default_quant": "FP8",
            "quant_repos": {},
            "capabilities": ["text-to-image", "text-rendering"],
            "description": "20B foundation. Best text rendering in images. Apache 2.0.",
            "quality": 94,
            "speed": 50,
            "released": "2025-08",
        }),
        json!({
            "id": "Qwen/Qwen-Image-Edit-2511",
            "name": "Qwen Image Edit",
            "provider": "Qwen",
            "params_b": 20.0,
            "vram_bf16": 42.0,
            "vram_fp8": 22.0,
            "vram_q4": 14.0,
            "default_quant": "FP8",
            "quant_repos": {},
            "capabilities": ["image-editing", "inpainting"],
            "description": "Dedicated editing. Style transfer, object removal. Apache 2.0.",
            "quality": 92,
            "speed": 50,
            "released": "2025-11",
        }),
        // -- Stable Diffusion (dedicated inpainting) --
        json!({
            "id": "diffusers/stable-diffusion-xl-1.0-inpainting-0.1",
            "name": "SDXL Inpainting",
            "provider": "Stability AI",
            "params_b": 3.5,
            "vram_bf16": 12.0,
            "vram_fp8": 8.0,
            "vram_q4": 6.0,
            "default_quant": "BF16",
            "quant_repos": {},
            "capabilities": ["inpainting", "image-editing"],
            "description": "SDXL fine-tuned for inpainting (9-channel UNet). Best SD-family fill quality; fits a 24GB card comfortably.",
            "quality": 86,
            "speed": 68,
            "released": "2023-11",
        }),
        json!({
            "id": "stable-diffusion-v1-5/stable-diffusion-inpainting",
            "name": "SD 1.5 Inpainting",
            "provider": "Stability AI",
            "params_b": 1.1,
            "vram_bf16": 4.0,
            "vram_fp8": 3.0,
            "vram_q4": 2.5,
            "default_quant": "BF16",
            "quant_repos": {},
            "capabilities": ["inpainting"],
            "description": "Classic SD 1.5 inpaint. Very light and fast; lower fidelity than SDXL.",
            "quality": 70,
            "speed": 92,
            "released": "2022-10",
        }),
        // -- FLUX --
        json!({
            "id": "black-forest-labs/FLUX.1-dev",
            "name": "FLUX.1 Dev",
            "provider": "Black Forest Labs",
            "params_b": 12.0,
            "vram_bf16": 33.0,
            "vram_fp8": 17.0,
            "vram_q4": 10.0,
            "default_quant": "FP8",
            "quant_repos": {
                "FP8": "diffusers/FLUX.1-dev-torchao-fp8",
            },
            "capabilities": ["text-to-image"],
            "description": "High quality, detailed. Popular community model. Non-commercial.",
            "quality": 92,
            "speed": 55,
            "released": "2024-08",
        }),
        json!({
            "id": "black-forest-labs/FLUX.1-schnell",
            "name": "FLUX.1 Schnell",
            "provider": "Black Forest Labs",
            "params_b": 12.0,
            "vram_bf16": 33.0,
            "vram_fp8": 17.0,
            "vram_q4": 10.0,
            "default_quant": "FP8",
            "quant_repos": {
                "FP8": "Kijai/flux-fp8",
            },
            "capabilities": ["text-to-image"],
            "description": "Fast 4-step variant. Apache 2.0 license.",
            "quality": 85,
            "speed": 90,
            "released": "2024-08",
        }),
        // -- Stable Diffusion --
        json!({
            "id": "stabilityai/stable-diffusion-3.5-medium",
            "name": "SD 3.5 Medium",
            "provider": "Stability AI",
            "params_b": 2.5,
            "vram_bf16": 12.0,
            "vram_fp8": 7.0,
            "vram_q4": null,
            "default_quant": "BF16",
            "quant_repos": {
                "FP8": "Comfy-Org/stable-diffusion-3.5-fp8",
            },
            "capabilities": ["text-to-image"],
            "description": "2.5B lightweight, fast. Fits almost any GPU.",
            "quality": 75,
            "speed": 95,
            "released": "2024-10",
        }),
        json!({
            "id": "stabilityai/stable-diffusion-3.5-large",
            "name": "SD 3.5 Large",
            "provider": "Stability AI",
            "params_b": 8.1,
            "vram_bf16": 22.0,
            "vram_fp8": 12.0,
            "vram_q4": null,
            "default_quant": "BF16",
            "quant_repos": {
                "FP8": "Comfy-Org/stable-diffusion-3.5-fp8",
            },
            "capabilities": ["text-to-image"],
            "description": "8B high quality. Good balance of speed and quality.",
            "quality": 85,
            "speed": 70,
            "released": "2024-10",
        }),
        json!({
            "id": "stabilityai/stable-diffusion-3.5-large-turbo",
            "name": "SD 3.5 Large Turbo",
            "provider": "Stability AI",
            "params_b": 8.1,
            "vram_bf16": 22.0,
            "vram_fp8": 12.0,
            "vram_q4": null,
            "default_quant": "BF16",
            "quant_repos": {
                "FP8": "Comfy-Org/stable-diffusion-3.5-fp8",
            },
            "capabilities": ["text-to-image"],
            "description": "Distilled for few-step inference. Fastest large SD.",
            "quality": 80,
            "speed": 92,
            "released": "2024-10",
        }),
        json!({
            "id": "stabilityai/stable-diffusion-xl-base-1.0",
            "name": "SDXL",
            "provider": "Stability AI",
            "params_b": 3.5,
            "vram_bf16": 10.0,
            "vram_fp8": 6.0,
            "vram_q4": null,
            "default_quant": "BF16",
            "quant_repos": {},
            "capabilities": ["text-to-image"],
            "description": "Classic workhorse. Huge LoRA ecosystem. Fits 8GB+.",
            "quality": 72,
            "speed": 90,
            "released": "2023-07",
        }),
        // -- Hunyuan --
        json!({
            "id": "tencent/HunyuanImage-3.0",
            "name": "HunyuanImage 3.0",
            "provider": "Tencent",
            "params_b": 13.0,
            "vram_bf16": 30.0,
            "vram_fp8": 16.0,
            "vram_q4": 9.0,
            "default_quant": "FP8",
            "quant_repos": {
                "Q4": "wikeeyang/Hunyuan-Image-30-Qint4",
                "NF4": "EricRollei/HunyuanImage-3.0-Instruct-NF4",
            },
            "capabilities": ["text-to-image", "text-rendering"],
            "description": "Strong text rendering. Bilingual Chinese/English. 13B activated per token.",
            "quality": 88,
            "speed": 60,
            "released": "2025-09",
        }),
        json!({
            "id": "tencent/HunyuanImage-3.0-Instruct-Distil",
            "name": "HunyuanImage 3.0 Distil",
            "provider": "Tencent",
            "params_b": 13.0,
            "vram_bf16": 30.0,
            "vram_fp8": 16.0,
            "vram_q4": 9.0,
            "default_quant": "FP8",
            "quant_repos": {},
            "capabilities": ["text-to-image", "text-rendering"],
            "description": "Distilled variant, fewer steps. Faster with comparable quality.",
            "quality": 85,
            "speed": 80,
            "released": "2026-01",
        }),
    ]
});

/// `get_image_models()` — return the image model registry.
///
/// Python returns the module-level list object; the faithful read-only analogue
/// is a clone of the static registry.
pub fn get_image_models() -> Vec<Value> {
    IMAGE_MODEL_REGISTRY.clone()
}

// ---------------------------------------------------------------------------
// Loose accessors mirroring the Python `x["k"]` / `x.get("k", d)` on the
// system dict + registry rows.
// ---------------------------------------------------------------------------

/// `model["key"]` for a string field (registry rows always carry these).
fn s(model: &Value, key: &str) -> String {
    model.get(key).and_then(Value::as_str).unwrap_or("").to_string()
}

/// `model["key"]` for a numeric field (registry rows always carry these).
fn n(model: &Value, key: &str) -> f64 {
    model.get(key).and_then(Value::as_f64).unwrap_or(0.0)
}

/// `model.get(key)` -> `Option<f64>`, distinguishing `null`/missing from a real
/// number (needed for `vram_q4` which is `None` on some rows).
fn opt_n(model: &Value, key: &str) -> Option<f64> {
    model.get(key).and_then(Value::as_f64)
}

/// `round(x, 1)` rendered as a JSON number, using banker's (round-half-to-even)
/// rounding to match Python's built-in `round`.
fn round1_value(x: f64) -> Value {
    let r = round_half_even(x, 1);
    match serde_json::Number::from_f64(r) {
        Some(num) => Value::Number(num),
        None => Value::Number(serde_json::Number::from(0)),
    }
}

/// Round `x` to `ndigits` decimal places using round-half-to-even, matching
/// Python's built-in `round`.
fn round_half_even(x: f64, ndigits: i32) -> f64 {
    if !x.is_finite() {
        return x;
    }
    let factor = 10f64.powi(ndigits);
    let scaled = x * factor;
    let floor = scaled.floor();
    let diff = scaled - floor;
    // Round half to even: round up when strictly above the half, or exactly on
    // the half with an odd floor (ties go to the even neighbour); otherwise keep
    // the floor. (Collapses the two identical round-up branches.)
    let round_up = diff > 0.5 || (diff == 0.5 && (floor as i64) % 2 != 0);
    let rounded = if round_up { floor + 1.0 } else { floor };
    rounded / factor
}

/// `rank_image_models(system, search=None, sort="fit")` — score and rank image
/// models against detected hardware.
///
/// Returns a list of models with fit info (vram needed, fits, recommended
/// quant). Output row key order mirrors the Python dict exactly.
pub fn rank_image_models(system: &Value, search: Option<&str>, sort: &str) -> Vec<Value> {
    // `gpu_vram = system.get("gpu_vram_gb", 0) or 0` — None/missing/0 -> 0.
    let gpu_vram = system.get("gpu_vram_gb").and_then(Value::as_f64).unwrap_or(0.0);
    // `has_gpu = system.get("has_gpu", False)` — Python truthiness.
    let has_gpu = match system.get("has_gpu") {
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(num)) => num.as_f64().map(|v| v != 0.0).unwrap_or(false),
        Some(Value::String(st)) => !st.is_empty(),
        _ => false,
    };
    let mut results: Vec<Value> = Vec::new();

    for model in IMAGE_MODEL_REGISTRY.iter() {
        // Filter by search.
        if let Some(raw) = search {
            // The Python guards with `if search:` (truthy); an empty string is
            // falsy and skips the filter, matching `search or None` callers.
            if !raw.is_empty() {
                let sq = raw.to_lowercase();
                let name_l = s(model, "name").to_lowercase();
                let id_l = s(model, "id").to_lowercase();
                let desc_l = s(model, "description").to_lowercase();
                if !name_l.contains(&sq) && !id_l.contains(&sq) && !desc_l.contains(&sq) {
                    continue;
                }
            }
        }

        // Determine best quant that fits.
        let mut quant: Option<String> = None;
        let mut vram_needed: Option<f64> = None;
        let mut fits = false;
        let mut quant_repo: Option<String> = None;

        if has_gpu && gpu_vram > 0.0 {
            // Try BF16 first, then FP8, then Q4.
            for (q, vram_key) in [("BF16", "vram_bf16"), ("FP8", "vram_fp8"), ("Q4", "vram_q4")] {
                if let Some(v) = opt_n(model, vram_key) {
                    if v <= gpu_vram * 0.90 {
                        // 10% headroom
                        quant = Some(q.to_string());
                        vram_needed = Some(v);
                        fits = true;
                        // `model.get("quant_repos", {}).get(q)`
                        quant_repo = model
                            .get("quant_repos")
                            .and_then(Value::as_object)
                            .and_then(|o| o.get(q))
                            .and_then(Value::as_str)
                            .map(|s| s.to_string());
                        break;
                    }
                }
            }
            // If nothing fits, show what it needs.
            if !fits {
                quant = Some(s(model, "default_quant"));
                // `model.get("vram_bf16", 0)`
                vram_needed = Some(model.get("vram_bf16").and_then(Value::as_f64).unwrap_or(0.0));
            }
        }

        // Fit label.
        let (fit, fit_label): (&str, &str) = if !has_gpu {
            ("no_gpu", "No GPU")
        } else if fits {
            let headroom = gpu_vram - vram_needed.unwrap_or(0.0);
            if headroom > gpu_vram * 0.3 {
                ("perfect", "Perfect")
            } else if headroom > gpu_vram * 0.1 {
                ("good", "Good")
            } else {
                ("tight", "Tight")
            }
        } else {
            ("no_fit", "Too large")
        };

        // Score: quality * speed * fit bonus.
        let mut score = n(model, "quality") * 0.6 + n(model, "speed") * 0.2;
        match fit {
            "perfect" => score += 20.0,
            "good" => score += 10.0,
            "tight" => score += 5.0,
            "no_fit" => score -= 30.0,
            _ => {}
        }

        // Build the output row preserving Python dict key order.
        let mut row = Map::new();
        row.insert("id".to_string(), json!(s(model, "id")));
        row.insert("name".to_string(), json!(s(model, "name")));
        row.insert("provider".to_string(), json!(s(model, "provider")));
        row.insert(
            "params_b".to_string(),
            model.get("params_b").cloned().unwrap_or(Value::Null),
        );
        row.insert(
            "vram_needed".to_string(),
            match vram_needed {
                Some(v) => json!(v),
                None => Value::Null,
            },
        );
        row.insert(
            "quant".to_string(),
            match &quant {
                Some(q) => json!(q),
                None => Value::Null,
            },
        );
        row.insert(
            "quant_repo".to_string(),
            match &quant_repo {
                Some(r) => json!(r),
                None => Value::Null,
            },
        );
        row.insert("fits".to_string(), json!(fits));
        row.insert("fit".to_string(), json!(fit));
        row.insert("fit_label".to_string(), json!(fit_label));
        row.insert(
            "quality".to_string(),
            model.get("quality").cloned().unwrap_or(Value::Null),
        );
        row.insert(
            "speed".to_string(),
            model.get("speed").cloned().unwrap_or(Value::Null),
        );
        row.insert("score".to_string(), round1_value(score));
        row.insert(
            "capabilities".to_string(),
            model.get("capabilities").cloned().unwrap_or_else(|| json!([])),
        );
        row.insert("description".to_string(), json!(s(model, "description")));
        // `model.get("released", "")`
        row.insert(
            "released".to_string(),
            match model.get("released") {
                Some(Value::String(r)) => json!(r),
                _ => json!(""),
            },
        );
        results.push(Value::Object(row));
    }

    // Sort. Python's `list.sort` is a stable sort; `sort_by` in Rust is stable
    // too, so ties keep registry order exactly as Python does.
    match sort {
        "quality" => {
            // key=lambda x: (-x["quality"], -x["score"])
            results.sort_by(|a, b| {
                let qa = row_f64(a, "quality");
                let qb = row_f64(b, "quality");
                let sa = row_f64(a, "score");
                let sb = row_f64(b, "score");
                cmp_f64(qb, qa).then_with(|| cmp_f64(sb, sa))
            });
        }
        "speed" => {
            // key=lambda x: (-x["speed"], -x["score"])
            results.sort_by(|a, b| {
                let pa = row_f64(a, "speed");
                let pb = row_f64(b, "speed");
                let sa = row_f64(a, "score");
                let sb = row_f64(b, "score");
                cmp_f64(pb, pa).then_with(|| cmp_f64(sb, sa))
            });
        }
        "vram" => {
            // key=lambda x: (x["vram_needed"] or 999, -x["score"])
            results.sort_by(|a, b| {
                let va = vram_or_999(a);
                let vb = vram_or_999(b);
                let sa = row_f64(a, "score");
                let sb = row_f64(b, "score");
                cmp_f64(va, vb).then_with(|| cmp_f64(sb, sa))
            });
        }
        _ => {
            // fit (default): key=lambda x: (-x["score"],)
            results.sort_by(|a, b| {
                let sa = row_f64(a, "score");
                let sb = row_f64(b, "score");
                cmp_f64(sb, sa)
            });
        }
    }

    results
}

/// `row["key"]` numeric read for an emitted output row.
fn row_f64(row: &Value, key: &str) -> f64 {
    row.get(key).and_then(Value::as_f64).unwrap_or(0.0)
}

/// `x["vram_needed"] or 999` — `None`/`0`/missing collapse to `999`.
fn vram_or_999(row: &Value) -> f64 {
    match row.get("vram_needed").and_then(Value::as_f64) {
        Some(v) if v != 0.0 => v,
        _ => 999.0,
    }
}

/// Total order over `f64` for sort keys (no NaN expected; treat as equal).
fn cmp_f64(a: f64, b: f64) -> std::cmp::Ordering {
    a.partial_cmp(&b).unwrap_or(std::cmp::Ordering::Equal)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn registry_has_all_models() {
        assert_eq!(IMAGE_MODEL_REGISTRY.len(), 15);
        assert_eq!(get_image_models().len(), 15);
    }

    #[test]
    fn vram_q4_null_preserved() {
        // SD 3.5 Medium has vram_q4: None.
        let sd = IMAGE_MODEL_REGISTRY
            .iter()
            .find(|m| s(m, "id") == "stabilityai/stable-diffusion-3.5-medium")
            .unwrap();
        assert!(sd.get("vram_q4").unwrap().is_null());
    }

    #[test]
    fn no_gpu_marks_all_no_gpu() {
        let sys = json!({"has_gpu": false, "gpu_vram_gb": 0});
        let ranked = rank_image_models(&sys, None, "fit");
        assert_eq!(ranked.len(), 15);
        for r in &ranked {
            assert_eq!(r.get("fit").unwrap().as_str().unwrap(), "no_gpu");
            assert_eq!(r.get("fit_label").unwrap().as_str().unwrap(), "No GPU");
            assert!(!r.get("fits").unwrap().as_bool().unwrap());
            assert!(r.get("vram_needed").unwrap().is_null());
            assert!(r.get("quant").unwrap().is_null());
        }
    }

    #[test]
    fn large_gpu_fits_bf16_and_picks_repo() {
        let sys = json!({"has_gpu": true, "gpu_vram_gb": 80.0});
        let ranked = rank_image_models(&sys, Some("Z-Image Turbo"), "fit");
        assert_eq!(ranked.len(), 1);
        let r = &ranked[0];
        assert_eq!(r.get("quant").unwrap().as_str().unwrap(), "BF16");
        assert!((r.get("vram_needed").unwrap().as_f64().unwrap() - 19.0).abs() < 1e-9);
        assert!(r.get("fits").unwrap().as_bool().unwrap());
        // BF16 has no quant_repo entry (repos are keyed FP8/Q4/NF4) -> null.
        assert!(r.get("quant_repo").unwrap().is_null());
        // fit: headroom 61 > 80*0.3 -> perfect; score 92*.6+95*.2+20 = 94.2
        assert_eq!(r.get("fit").unwrap().as_str().unwrap(), "perfect");
        assert!((r.get("score").unwrap().as_f64().unwrap() - 94.2).abs() < 1e-9);
    }

    #[test]
    fn small_gpu_falls_through_to_q4() {
        // 12GB GPU: FLUX.1-dev bf16=33 fp8=17 q4=10. 0.90*12=10.8 -> q4(10)<=10.8 fits Q4.
        let sys = json!({"has_gpu": true, "gpu_vram_gb": 12.0});
        let ranked = rank_image_models(&sys, Some("FLUX.1 Dev"), "fit");
        let r = &ranked[0];
        assert_eq!(r.get("quant").unwrap().as_str().unwrap(), "Q4");
        assert!((r.get("vram_needed").unwrap().as_f64().unwrap() - 10.0).abs() < 1e-9);
    }

    #[test]
    fn no_fit_shows_default_quant_and_bf16() {
        // 4GB GPU, Qwen Image 2512: bf16=42 fp8=22 q4=14, 0.9*4=3.6 -> nothing fits.
        let sys = json!({"has_gpu": true, "gpu_vram_gb": 4.0});
        let ranked = rank_image_models(&sys, Some("Qwen Image 2512"), "fit");
        let r = &ranked[0];
        assert!(!r.get("fits").unwrap().as_bool().unwrap());
        assert_eq!(r.get("fit").unwrap().as_str().unwrap(), "no_fit");
        assert_eq!(r.get("quant").unwrap().as_str().unwrap(), "FP8"); // default_quant
        assert!((r.get("vram_needed").unwrap().as_f64().unwrap() - 42.0).abs() < 1e-9);
    }

    #[test]
    fn sort_vram_treats_null_as_999() {
        let sys = json!({"has_gpu": false, "gpu_vram_gb": 0});
        let ranked = rank_image_models(&sys, None, "vram");
        // All no_gpu -> vram_needed null -> 999 sentinel -> stable order, then -score.
        assert_eq!(ranked.len(), 15);
        // vram tie everywhere -> sorted by -score descending.
        let first_score = ranked[0].get("score").unwrap().as_f64().unwrap();
        let last_score = ranked[ranked.len() - 1].get("score").unwrap().as_f64().unwrap();
        assert!(first_score >= last_score);
    }

    #[test]
    fn output_key_order_matches_python() {
        let sys = json!({"has_gpu": false, "gpu_vram_gb": 0});
        let ranked = rank_image_models(&sys, Some("Z-Image Turbo"), "fit");
        let obj = ranked[0].as_object().unwrap();
        let keys: Vec<&str> = obj.keys().map(|k| k.as_str()).collect();
        assert_eq!(
            keys,
            vec![
                "id", "name", "provider", "params_b", "vram_needed", "quant",
                "quant_repo", "fits", "fit", "fit_label", "quality", "speed",
                "score", "capabilities", "description", "released",
            ]
        );
    }

    #[test]
    fn empty_search_does_not_filter() {
        let sys = json!({"has_gpu": false});
        let ranked = rank_image_models(&sys, Some(""), "fit");
        assert_eq!(ranked.len(), 15);
    }

    #[test]
    fn parity_24gb_fit_row0_matches_python() {
        // Golden fixture captured from the real Python `rank_image_models` for a
        // 24GB GPU: top-of-fit row 0 must serialize identically (key order +
        // values), including the FP8/Q4 fallback ladder and the `null` repo.
        let sys = json!({"has_gpu": true, "gpu_vram_gb": 24.0});
        let ranked = rank_image_models(&sys, None, "fit");
        let row0 = serde_json::to_string(&ranked[0]).unwrap();
        let expected = r#"{"id":"Qwen/Qwen-Image-2512","name":"Qwen Image 2512","provider":"Qwen","params_b":20.0,"vram_needed":14.0,"quant":"Q4","quant_repo":null,"fits":true,"fit":"perfect","fit_label":"Perfect","quality":95,"speed":50,"score":87.0,"capabilities":["text-to-image","text-rendering"],"description":"Dec 2025 update. Better humans, finer detail, strong text. Apache 2.0.","released":"2025-12"}"#;
        assert_eq!(row0, expected);
        // Hunyuan 3.0 picks FP8 (16<=21.6) but its repos are keyed Q4/NF4 -> null.
        let hun = ranked
            .iter()
            .find(|r| r.get("id").unwrap().as_str().unwrap() == "tencent/HunyuanImage-3.0")
            .unwrap();
        assert_eq!(hun.get("quant").unwrap().as_str().unwrap(), "FP8");
        assert!(hun.get("quant_repo").unwrap().is_null());
    }

    #[test]
    fn parity_24gb_vram_sort_matches_python() {
        // Golden fixture: `sort="vram"` ascending by vram_needed, then -score.
        let sys = json!({"has_gpu": true, "gpu_vram_gb": 24.0});
        let ranked = rank_image_models(&sys, None, "vram");
        let top3: Vec<(String, f64, f64)> = ranked
            .iter()
            .take(3)
            .map(|r| {
                (
                    r.get("id").unwrap().as_str().unwrap().to_string(),
                    r.get("vram_needed").unwrap().as_f64().unwrap(),
                    r.get("score").unwrap().as_f64().unwrap(),
                )
            })
            .collect();
        assert_eq!(top3[0].0, "stable-diffusion-v1-5/stable-diffusion-inpainting");
        assert!((top3[0].1 - 4.0).abs() < 1e-9 && (top3[0].2 - 80.4).abs() < 1e-9);
        assert_eq!(top3[1].0, "stabilityai/stable-diffusion-xl-base-1.0");
        assert!((top3[1].1 - 10.0).abs() < 1e-9 && (top3[1].2 - 81.2).abs() < 1e-9);
        assert_eq!(top3[2].0, "stabilityai/stable-diffusion-3.5-large-turbo");
        assert!((top3[2].1 - 12.0).abs() < 1e-9 && (top3[2].2 - 86.4).abs() < 1e-9);
    }
}
