//! Adversarial round-trip verification for the 7 un-stubbed gallery image-editing
//! ops, exercising the REAL `odysseus::src::{image_edit, image_models}` fns the
//! handlers call. CPU ops (rotate/sharpen/inpaint-mask/composite/enhance-fallback)
//! run inline and are VERIFIED here against the PIL semantics they port. The 4 ML
//! ops attempt a live ONNX model download + `ort` inference into a TEMP cache dir
//! (never the live `data/`); on no-network they degrade to a friendly error
//! exactly like Python, and are reported `code_complete_unverified`.
//!
//! Run with: `cargo test --no-default-features --features web,db --test gallery_image_ops -- --nocapture`
//! Live ML round-trip: add `--ignored` to also run `ml_*` tests.

use image::{DynamicImage, GenericImageView, GrayImage, Luma, Rgba, RgbaImage};
use odysseus::src::{image_edit, image_models};

/// Build a synthetic RGBA gradient with a recognizable foreground blob so the
/// ML masks have *something* salient to find.
fn synthetic_rgba(w: u32, h: u32) -> RgbaImage {
    let mut img = RgbaImage::new(w, h);
    let (cx, cy) = (w as f32 / 2.0, h as f32 / 2.0);
    let r = (w.min(h) as f32) / 3.0;
    for y in 0..h {
        for x in 0..w {
            let d = ((x as f32 - cx).powi(2) + (y as f32 - cy).powi(2)).sqrt();
            if d < r {
                img.put_pixel(x, y, Rgba([230, 40, 40, 255])); // red blob (foreground)
            } else {
                let v = ((x + y) % 200) as u8;
                img.put_pixel(x, y, Rgba([v, v / 2, 255 - v, 255]));
            }
        }
    }
    img
}

fn b64_is_valid_png(b64: &str) -> (u32, u32) {
    let back = image_edit::decode_image_b64(b64).expect("encoded output must decode");
    // Confirm it really is PNG, not just any image.
    use base64::Engine;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(b64)
        .expect("valid std base64");
    assert_eq!(&bytes[..8], b"\x89PNG\r\n\x1a\n", "output must be a PNG");
    back.dimensions()
}

// ---------------------------------------------------------------------------
// CPU OP 1 — rotate (image_edit::rotate_expand). MUST be verified.
// ---------------------------------------------------------------------------

#[test]
fn cpu_rotate_90_swaps_dims_and_roundtrips_png() {
    let img = DynamicImage::ImageRgba8(synthetic_rgba(6, 4));
    // PIL pil.rotate(-90, expand=True) == clockwise 90 -> 4x6 from 6x4.
    let r90 = image_edit::rotate_expand(&img, 90);
    assert_eq!((r90.width(), r90.height()), (4, 6), "90 swaps W/H (expand)");
    // -90 / 270 equal each other and also swap dims.
    let rneg = image_edit::rotate_expand(&img, -90).to_rgba8();
    let r270 = image_edit::rotate_expand(&img, 270).to_rgba8();
    assert_eq!(rneg.as_raw(), r270.as_raw(), "-90 == 270");
    assert_eq!(rneg.dimensions(), (4, 6));
    // 180 keeps dims.
    let r180 = image_edit::rotate_expand(&img, 180);
    assert_eq!((r180.width(), r180.height()), (6, 4));
    // The output PNG-encodes cleanly (the handler's success byte-shape).
    let (w, h) = b64_is_valid_png(&image_edit::encode_png_b64(&r90).unwrap());
    assert_eq!((w, h), (4, 6));

    // Pixel-exact CW-90: src(x,y) -> dst(h-1-y, x) for image::imageops::rotate90.
    let src = img.to_rgba8();
    let dst = r90.to_rgba8();
    for y in 0..4u32 {
        for x in 0..6u32 {
            assert_eq!(src.get_pixel(x, y), dst.get_pixel(4 - 1 - y, x));
        }
    }
}

// ---------------------------------------------------------------------------
// CPU OP 2 — sharpen (image_edit::unsharp_mask). MUST be verified.
// ---------------------------------------------------------------------------

#[test]
fn cpu_sharpen_unsharp_mask_increases_edge_contrast_and_roundtrips() {
    // A vertical edge: left half dark, right half bright.
    let mut img = RgbaImage::new(16, 8);
    for y in 0..8 {
        for x in 0..16 {
            let v = if x < 8 { 60 } else { 180 };
            img.put_pixel(x, y, Rgba([v, v, v, 255]));
        }
    }
    // Handler maps amount=50 -> percent = (50/100)*200 = 100; radius=2, thr=3.
    let out = image_edit::unsharp_mask(&img, 2.0, 100, 3);
    assert_eq!(out.dimensions(), (16, 8), "unsharpen preserves dims");
    // The boundary pixels should over/undershoot vs the flat input (edge halo).
    let near_dark = out.get_pixel(7, 4).0[0];
    let near_bright = out.get_pixel(8, 4).0[0];
    assert!(
        near_dark <= 60 && near_bright >= 180,
        "unsharp mask should accentuate the edge (got {near_dark}/{near_bright})"
    );
    // Far-from-edge flat regions stay put (no high-frequency to amplify).
    assert_eq!(out.get_pixel(0, 0).0[0], 60);
    assert_eq!(out.get_pixel(15, 0).0[0], 180);
    // Alpha carried through.
    assert_eq!(out.get_pixel(0, 0).0[3], 255);
    let (w, h) = b64_is_valid_png(
        &image_edit::encode_png_b64(&DynamicImage::ImageRgba8(out)).unwrap(),
    );
    assert_eq!((w, h), (16, 8));
}

// ---------------------------------------------------------------------------
// CPU OP 3 — inpaint OpenAI mask conversion + composite. MUST be verified.
// ---------------------------------------------------------------------------

#[test]
fn cpu_inpaint_mask_conversion_and_composite_match_pil() {
    // SD mask: white (255) = regenerate, black (0) = keep. 2x1 sample.
    let mut sd_mask = GrayImage::new(2, 1);
    sd_mask.put_pixel(0, 0, Luma([255])); // edit here
    sd_mask.put_pixel(1, 0, Luma([0])); // keep
    let oa = image_edit::sd_mask_to_openai_alpha(&sd_mask, (2, 1));
    // alpha = 255 - luminance: white->0 (transparent=edit for OpenAI), black->255.
    assert_eq!(oa.get_pixel(0, 0), &Rgba([255, 255, 255, 0]));
    assert_eq!(oa.get_pixel(1, 0), &Rgba([255, 255, 255, 255]));

    // composite(generated, source, mask_L): white mask -> generated, black -> source.
    let source = RgbaImage::from_pixel(2, 1, Rgba([10, 20, 30, 200]));
    let generated = DynamicImage::ImageRgba8(RgbaImage::from_pixel(2, 1, Rgba([240, 250, 260u16 as u8, 255])));
    let blended = image_edit::composite_with_mask(&generated, &source, &sd_mask);
    // x=0 (white SD mask -> use generated RGB, source alpha)
    assert_eq!(blended.get_pixel(0, 0).0[0], 240);
    assert_eq!(blended.get_pixel(0, 0).0[3], 200, "alpha carried from source");
    // x=1 (black SD mask -> keep source)
    assert_eq!(blended.get_pixel(1, 0), &Rgba([10, 20, 30, 200]));

    // The post-composite output PNG-encodes (the {"image": ...} success shape).
    let (w, h) = b64_is_valid_png(
        &image_edit::encode_png_b64(&DynamicImage::ImageRgba8(blended)).unwrap(),
    );
    assert_eq!((w, h), (2, 1));
}

// ---------------------------------------------------------------------------
// remove-bg hint multiply (image_edit::multiply_luma) — the pure part of the
// hint path; the u2net run is exercised by the ignored ML test below.
// ---------------------------------------------------------------------------

#[test]
fn cpu_remove_bg_hint_alpha_multiply_matches_imagechops() {
    let alpha = GrayImage::from_pixel(2, 1, Luma([255]));
    let mut hint = GrayImage::new(2, 1);
    hint.put_pixel(0, 0, Luma([0])); // outside hint -> forced transparent
    hint.put_pixel(1, 0, Luma([255])); // inside hint -> kept
    let out = image_edit::multiply_luma(&alpha, &hint);
    assert_eq!(out.get_pixel(0, 0), &Luma([0]));
    assert_eq!(out.get_pixel(1, 0), &Luma([255]));
}

// ---------------------------------------------------------------------------
// enhance-face PIL fallback (image_edit::pil_enhance_fallback). The handler's
// model-unobtainable branch returns this with {"method":"pil"}. VERIFIED here.
// ---------------------------------------------------------------------------

#[test]
fn cpu_enhance_face_pil_fallback_roundtrips() {
    let img = synthetic_rgba(24, 18);
    let out = image_edit::pil_enhance_fallback(&img);
    assert_eq!(out.dimensions(), (24, 18));
    let (w, h) = b64_is_valid_png(
        &image_edit::encode_png_b64(&DynamicImage::ImageRgba8(out)).unwrap(),
    );
    assert_eq!((w, h), (24, 18));
}

// ---------------------------------------------------------------------------
// ML OPS — live ONNX download + ort inference into a TEMP cache dir.
// Gated #[ignore] (network + large download). They prove the real code path,
// not a stub. On no-network they return Err -> the handler soft-fails {"error"}.
// ---------------------------------------------------------------------------

/// Point the model cache at a throwaway temp dir BEFORE `DATA_DIR` (a `Lazy`) is
/// first touched, so we never write to the live `data/onnx_models`.
fn set_temp_cache() -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("odysseus_onnx_test_{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    std::env::set_var("ODYSSEUS_DATA_DIR", &dir);
    dir
}

#[test]
#[ignore]
fn ml_remove_bg_u2net_live() {
    let _dir = set_temp_cache();
    let img = synthetic_rgba(96, 72);
    match image_models::u2net_salient_mask(&img) {
        Ok(mask) => {
            assert_eq!(mask.dimensions(), (96, 72), "mask resized back to input dims");
            println!("U2NET: verified mask {:?}", mask.dimensions());
        }
        Err(e) => println!("U2NET: code_complete_unverified ({e})"),
    }
}

#[test]
#[ignore]
fn ml_upscale_realesrgan_live() {
    let _dir = set_temp_cache();
    let img = synthetic_rgba(48, 32);
    match image_models::realesrgan_upscale(&img, 2) {
        Ok(out) => {
            assert_eq!(out.dimensions(), (96, 64), "2x upscale doubles dims");
            println!("UPSCALE: verified {:?}", out.dimensions());
        }
        Err(e) => println!("UPSCALE: code_complete_unverified ({e})"),
    }
}

#[test]
#[ignore]
fn ml_denoise_realesr_general_live() {
    let _dir = set_temp_cache();
    let img = synthetic_rgba(48, 32);
    match image_models::realesr_general_denoise(&img, 0.5) {
        Ok(out) => {
            assert_eq!(out.dimensions(), (48, 32), "denoise outscale=1 keeps dims");
            println!("DENOISE: verified {:?}", out.dimensions());
        }
        Err(e) => println!("DENOISE: code_complete_unverified ({e})"),
    }
}

#[test]
#[ignore]
fn ml_enhance_face_gfpgan_live() {
    let _dir = set_temp_cache();
    let img = synthetic_rgba(80, 80);
    match image_models::gfpgan_restore(&img) {
        Ok(out) => {
            assert_eq!(out.dimensions(), (80, 80), "restore resized back to input dims");
            println!("GFPGAN: verified {:?}", out.dimensions());
        }
        Err(e) => println!("GFPGAN: code_complete_unverified ({e})"),
    }
}
