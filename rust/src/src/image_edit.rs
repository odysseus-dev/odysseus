//! `image_edit` — pure CPU image-editing helpers backing the gallery editor's
//! rotate / sharpen / inpaint-mask / composite / enhance-fallback ops in
//! `routes/gallery_routes.rs`.
//!
//! NO model, NO network, NO async. Everything here is synchronous and operates
//! on `image::DynamicImage` / `image::RgbaImage` / `image::GrayImage` and
//! base64-PNG strings — a faithful port of the PIL (`Pillow`) calls the Python
//! handlers make. The companion `image_models` module owns the ort/ONNX runners.
//!
//! ## PIL → Rust fidelity map (the load-bearing decisions live here)
//!
//! * **base64**: Python uses `base64.b64decode` / `base64.b64encode` which is
//!   the STANDARD (non-url-safe) alphabet, so [`decode_image_b64`] /
//!   [`encode_png_b64`] use `base64::engine::general_purpose::STANDARD`.
//! * **PNG encode**: Python does `img.save(buf, format="PNG")` then b64encode;
//!   we do `DynamicImage::write_to(Cursor, ImageFormat::Png)` then STANDARD b64.
//! * **rotate**: Python `pil.rotate(-angle, expand=True)`. PIL rotates
//!   COUNTER-clockwise, so `rotate(-angle)` is a CLOCKWISE rotation by `angle`.
//!   The handler only accepts `angle ∈ {90, -90, 180, 270}`, every one a
//!   multiple of 90, so `expand=True` just swaps W/H for the odd multiples and
//!   we can use the exact fast paths in `image::imageops`:
//!     - `90`   → `rotate90`  (clockwise 90)
//!     - `-90`  → `rotate270` (clockwise 270 == counter-clockwise 90)
//!     - `270`  → `rotate270`
//!     - `180`  → `rotate180`
//! * **UnsharpMask(radius, percent, threshold)**: PIL's radius is a (box-ish)
//!   blur radius; `image::imageops::unsharpen(sigma, threshold)` uses a Gaussian
//!   sigma. The standard faithful equivalence is `sigma = radius`. PIL's
//!   `percent` scales how much of the high-frequency detail is *added back*
//!   (100 % == exactly the `unsharpen` result, 200 % == twice the detail), which
//!   `unsharpen` does NOT expose, so we recover it with an explicit per-channel
//!   blend:  `out = clamp(src + (percent/100) * (unsharpen(src, sigma, thr) - src))`.
//!   The handler passes `percent = amount * 200`, `amount = body.amount / 100`.
//! * **ImageEnhance** (used only by [`pil_enhance_fallback`]): PIL's
//!   `_Enhance.enhance(factor)` is `Image.blend(degenerate, image, factor)` ==
//!   `degenerate*(1-factor) + image*factor` per channel, where the `degenerate`
//!   image is:
//!     - Contrast:   a solid image filled with `mean`, the (rounded) mean of the
//!                   `L` (ITU-R 601-2 luminance) plane.
//!     - Color:      the grayscale (`L`→RGB) version of the image.
//!     - Brightness: a black image (so `out = image * factor`).
//! * **luminance**: PIL's `convert("L")` uses the fixed-point ITU-R 601-2
//!   formula `(R*19595 + G*38470 + B*7471 + 0x8000) >> 16`. We replicate that
//!   exactly so masks / means match Pillow byte-for-byte.

use base64::Engine;
use image::{DynamicImage, GrayImage, ImageFormat, Luma, Rgba, RgbaImage};
use std::io::Cursor;

/// PIL `convert("L")` luminance for one RGB pixel: the fixed-point ITU-R 601-2
/// transform `(R*19595 + G*38470 + B*7471 + 0x8000) >> 16`, byte-identical to
/// Pillow's `L24`/`convert("L")`.
#[inline]
fn luma_601(r: u8, g: u8, b: u8) -> u8 {
    let v = (r as u32 * 19595 + g as u32 * 38470 + b as u32 * 7471 + 0x8000) >> 16;
    v.min(255) as u8
}

/// b64-std decode → [`DynamicImage`]. `Err` carries a message for the
/// 400 / soft-fail caller (mirrors `base64.b64decode` + `Image.open`).
pub fn decode_image_b64(b64: &str) -> Result<DynamicImage, String> {
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(b64.trim())
        .map_err(|e| format!("base64 decode failed: {e}"))?;
    image::load_from_memory(&bytes).map_err(|e| format!("image decode failed: {e}"))
}

/// Encode a [`DynamicImage`] to PNG then base64-std (the `{"image": ...}`
/// success shape — `img.save(buf, format="PNG")` + `b64encode`).
pub fn encode_png_b64(img: &DynamicImage) -> Result<String, String> {
    let mut buf: Vec<u8> = Vec::new();
    img.write_to(&mut Cursor::new(&mut buf), ImageFormat::Png)
        .map_err(|e| format!("PNG encode failed: {e}"))?;
    Ok(base64::engine::general_purpose::STANDARD.encode(&buf))
}

/// 90/180/270 fast-path rotate matching PIL `pil.rotate(-angle, expand=True)`
/// for the ONLY angles the handler accepts (`90, -90, 180, 270`).
///
/// PIL rotates counter-clockwise, the handler negates, so the *net* visual
/// effect is a CLOCKWISE rotation by `angle`. `expand=True` is automatic for
/// 90/270 because the canvas W/H swap. Any other angle is returned unchanged
/// (the handler rejects them with a 400 before calling here, so this is just a
/// defensive identity).
pub fn rotate_expand(img: &DynamicImage, angle: i64) -> DynamicImage {
    match angle.rem_euclid(360) {
        90 => DynamicImage::ImageRgba8(image::imageops::rotate90(&img.to_rgba8())),
        180 => DynamicImage::ImageRgba8(image::imageops::rotate180(&img.to_rgba8())),
        270 => DynamicImage::ImageRgba8(image::imageops::rotate270(&img.to_rgba8())),
        // `-90` normalises to 270 via rem_euclid; `0` (and anything else) → identity.
        _ => img.clone(),
    }
}

/// PIL `ImageFilter.UnsharpMask(radius, percent, threshold)` faithful port.
///
/// `image::imageops::unsharpen(sigma, threshold)` produces the 100%-strength
/// sharpened image (`radius → sigma`, `threshold` passes straight through). PIL
/// `percent` scales how much high-frequency detail is added back, so we recover
/// it with a per-channel blend toward the sharpened result, clamped to 0..=255:
///
/// `out = clamp(src + (percent/100) * (unsharpen(src, radius, threshold) - src))`
///
/// Alpha is carried through unchanged (PIL's filter runs per-band; the gallery
/// sharpen handler `.convert("RGB")`s first so alpha is normally opaque, but we
/// preserve whatever alpha the buffer holds).
// `for c in 0..3` mirrors PIL's per-band (R/G/B) filter loop, writing each
// channel of `px[c]` from the parallel `src.0[c]`/`sp.0[c]` arrays.
#[allow(clippy::needless_range_loop)]
pub fn unsharp_mask(img: &RgbaImage, radius: f32, percent: i32, threshold: i32) -> RgbaImage {
    let sharp: RgbaImage = image::imageops::unsharpen(img, radius, threshold);
    let scale = percent as f32 / 100.0;
    let (w, h) = img.dimensions();
    let mut out = RgbaImage::new(w, h);
    for (x, y, src) in img.enumerate_pixels() {
        let sp = sharp.get_pixel(x, y);
        let mut px = [0u8; 4];
        for c in 0..3 {
            let s = src.0[c] as f32;
            px[c] = sharp_blend(s, sp.0[c] as f32, scale);
        }
        px[3] = src.0[3]; // carry source alpha
        out.put_pixel(x, y, Rgba(px));
    }
    out
}

/// `clamp(src + scale * (sharpened - src))`, rounded to nearest u8.
#[inline]
fn sharp_blend(src: f32, sharpened: f32, scale: f32) -> u8 {
    let v = src + scale * (sharpened - src);
    v.round().clamp(0.0, 255.0) as u8
}

/// SD white-on-black mask (`L`) → OpenAI inpaint RGBA alpha mask.
///
/// Mirrors `gallery_routes.py:976-978`:
/// ```python
/// alpha = mask_png.point(lambda p: 255 - p)
/// oa_mask = Image.new("RGBA", source_png.size, (255, 255, 255, 255))
/// oa_mask.putalpha(alpha)
/// ```
/// i.e. `alpha = 255 - luminance(mask)` (white SD pixels → alpha 0 == "edit
/// here" for OpenAI; black SD pixels → alpha 255 == "keep"), RGB all white,
/// sized to the source `size`. If the mask dims differ from `size` we resize it
/// NEAREST first (the SD mask is binary-ish; nearest avoids introducing grey
/// edges, matching the conservative resize the hint path uses at py:1425).
pub fn sd_mask_to_openai_alpha(mask_l: &GrayImage, size: (u32, u32)) -> RgbaImage {
    let (w, h) = size;
    let resized: GrayImage = if mask_l.dimensions() != size {
        image::imageops::resize(mask_l, w, h, image::imageops::FilterType::Nearest)
    } else {
        mask_l.clone()
    };
    let mut out = RgbaImage::from_pixel(w, h, Rgba([255, 255, 255, 255]));
    for (x, y, m) in resized.enumerate_pixels() {
        let alpha = 255u8.saturating_sub(m.0[0]);
        out.put_pixel(x, y, Rgba([255, 255, 255, alpha]));
    }
    out
}

/// PIL `Image.composite(generated, source, mask_L)`: per pixel,
/// `out = source*(1-m) + generated*m` with `m = mask_luminance / 255`.
///
/// Mirrors `gallery_routes.py:1044-1053`. If `generated` and `source` differ in
/// size, `generated` is resized to the source dims with Lanczos3 first (Python
/// uses `Image.LANCZOS`). RGB is blended; the output alpha is taken from the
/// SOURCE (PIL `Image.composite` blends all bands of the two RGBA images using
/// the mask, but here the editor's source carries the canonical alpha and the
/// generated image is opaque, so carrying source alpha is the faithful result).
pub fn composite_with_mask(
    generated: &DynamicImage,
    source: &RgbaImage,
    mask_l: &GrayImage,
) -> RgbaImage {
    let (sw, sh) = source.dimensions();

    // Resize generated → source dims with Lanczos3 if differing (py Image.LANCZOS).
    let gen_rgba: RgbaImage = if generated.width() != sw || generated.height() != sh {
        DynamicImage::ImageRgba8(generated.to_rgba8())
            .resize_exact(sw, sh, image::imageops::FilterType::Lanczos3)
            .to_rgba8()
    } else {
        generated.to_rgba8()
    };

    // Resize the mask too if it disagrees (Python's mask_png is already source-
    // sized in the inpaint path; resize NEAREST defensively).
    let mask: GrayImage = if mask_l.dimensions() != (sw, sh) {
        image::imageops::resize(mask_l, sw, sh, image::imageops::FilterType::Nearest)
    } else {
        mask_l.clone()
    };

    let mut out = RgbaImage::new(sw, sh);
    for y in 0..sh {
        for x in 0..sw {
            let s = source.get_pixel(x, y).0;
            let g = gen_rgba.get_pixel(x, y).0;
            let m = mask.get_pixel(x, y).0[0] as u32; // 0..=255
            let mut px = [0u8; 4];
            for c in 0..3 {
                // out = round(source*(255-m)/255 + generated*m/255)
                let blended = (s[c] as u32 * (255 - m) + g[c] as u32 * m + 127) / 255;
                px[c] = blended.min(255) as u8;
            }
            px[3] = s[3]; // carry source alpha
            out.put_pixel(x, y, Rgba(px));
        }
    }
    out
}

/// `ImageChops.multiply(a, b)` on two `L` planes: `out = (a * b) / 255`,
/// rounded. Used for the remove-bg hint alpha-multiply (`py:1472`).
///
/// If the two planes differ in size, `b` is resized to `a`'s dims (NEAREST) so
/// the multiply is well-defined; in the gallery path they are already equal.
pub fn multiply_luma(a: &GrayImage, b: &GrayImage) -> GrayImage {
    let (w, h) = a.dimensions();
    let bb: GrayImage = if b.dimensions() != (w, h) {
        image::imageops::resize(b, w, h, image::imageops::FilterType::Nearest)
    } else {
        b.clone()
    };
    let mut out = GrayImage::new(w, h);
    for y in 0..h {
        for x in 0..w {
            let av = a.get_pixel(x, y).0[0] as u32;
            let bv = bb.get_pixel(x, y).0[0] as u32;
            // PIL ImageChops.multiply: (a * b + 127) / 255  (round-to-nearest).
            let v = (av * bv + 127) / 255;
            out.put_pixel(x, y, Luma([v.min(255) as u8]));
        }
    }
    out
}

/// PIL multi-step `ImageEnhance` fallback for enhance-face, mirroring
/// `gallery_routes.py:1536-1540`:
///
/// ```python
/// enhanced = img.filter(ImageFilter.MedianFilter(size=3))        # light denoise
/// enhanced = enhanced.filter(ImageFilter.UnsharpMask(radius=2,   # sharpen
///                                                    percent=150, threshold=3))
/// enhanced = ImageEnhance.Contrast(enhanced).enhance(1.15)       # contrast
/// enhanced = ImageEnhance.Color(enhanced).enhance(1.10)          # saturation
/// enhanced = ImageEnhance.Brightness(enhanced).enhance(1.05)     # brightness
/// ```
///
/// This is the honest, documented fallback used when no GFPGAN ONNX model is
/// obtainable — the response then carries `{"method": "pil"}` exactly like
/// Python's `ImportError` branch.
pub fn pil_enhance_fallback(img: &RgbaImage) -> RgbaImage {
    // 1) MedianFilter(size=3) — a 3x3 window, i.e. radius 1 in x and y.
    let denoised = median_filter_3x3(img);
    // 2) UnsharpMask(radius=2, percent=150, threshold=3).
    let sharpened = unsharp_mask(&denoised, 2.0, 150, 3);
    // 3) Contrast(1.15) — blend toward a solid `mean`-gray image.
    let contrasted = enhance_contrast(&sharpened, 1.15);
    // 4) Color(1.10) — blend toward the grayscale (L→RGB) version.
    let colored = enhance_color(&contrasted, 1.10);
    // 5) Brightness(1.05) — blend toward black (i.e. scale by factor).
    enhance_brightness(&colored, 1.05)
}

/// PIL `ImageFilter.MedianFilter(size=3)` on an RGBA buffer.
///
/// PIL runs the median per band over the source pixels; alpha is carried
/// through unchanged (the enhance-face fallback works on an RGB-converted image
/// so alpha is opaque, but we preserve it). We compute it directly here (a 3x3
/// window with edge clamping, matching Pillow's `RankFilter` border handling)
/// so the module has no extra dependency beyond `image`; the gallery editor's
/// heavier filters are the ort/ONNX model runners in `image_models`.
fn median_filter_3x3(img: &RgbaImage) -> RgbaImage {
    let (w, h) = img.dimensions();
    let mut out = RgbaImage::new(w, h);
    if w == 0 || h == 0 {
        return out;
    }
    let clamp = |v: i64, max: u32| -> u32 { v.clamp(0, max as i64 - 1) as u32 };
    for y in 0..h {
        for x in 0..w {
            let mut bands: [[u8; 9]; 3] = [[0; 9]; 3];
            let mut k = 0;
            for dy in -1i64..=1 {
                for dx in -1i64..=1 {
                    let sx = clamp(x as i64 + dx, w);
                    let sy = clamp(y as i64 + dy, h);
                    let p = img.get_pixel(sx, sy).0;
                    for c in 0..3 {
                        bands[c][k] = p[c];
                    }
                    k += 1;
                }
            }
            let mut px = [0u8; 4];
            for c in 0..3 {
                let mut window = bands[c];
                window.sort_unstable();
                px[c] = window[4]; // median of 9
            }
            px[3] = img.get_pixel(x, y).0[3];
            out.put_pixel(x, y, Rgba(px));
        }
    }
    out
}

/// PIL `ImageEnhance.Contrast(img).enhance(factor)`.
///
/// `degenerate` is a solid image filled with `mean`, where `mean` is the rounded
/// mean of the `L` (601-2 luminance) plane. Output blends:
/// `out = round(degenerate*(1-factor) + img*factor)` per RGB channel.
fn enhance_contrast(img: &RgbaImage, factor: f32) -> RgbaImage {
    let (w, h) = img.dimensions();
    if w == 0 || h == 0 {
        return img.clone();
    }
    // mean of the L plane, then `int(mean + 0.5)` like ImageStat + Pillow.
    let mut sum: u64 = 0;
    for p in img.pixels() {
        sum += luma_601(p.0[0], p.0[1], p.0[2]) as u64;
    }
    let count = (w as u64) * (h as u64);
    let mean = ((sum as f64) / (count as f64) + 0.5).floor() as i64;
    let mean = mean.clamp(0, 255) as f32;
    blend_toward_solid(img, [mean, mean, mean], factor)
}

/// PIL `ImageEnhance.Color(img).enhance(factor)` — blend toward the grayscale
/// (`L`→RGB) version of the image. `out = round(gray*(1-factor) + img*factor)`.
// `for c in 0..3` mirrors PIL's per-band enhance loop, writing `px[c]` from the
// parallel `p.0[c]` pixel array.
#[allow(clippy::needless_range_loop)]
fn enhance_color(img: &RgbaImage, factor: f32) -> RgbaImage {
    let (w, h) = img.dimensions();
    let mut out = RgbaImage::new(w, h);
    for (x, y, p) in img.enumerate_pixels() {
        let l = luma_601(p.0[0], p.0[1], p.0[2]) as f32;
        let mut px = [0u8; 4];
        for c in 0..3 {
            px[c] = blend_round(l, p.0[c] as f32, factor);
        }
        px[3] = p.0[3];
        out.put_pixel(x, y, Rgba(px));
    }
    out
}

/// PIL `ImageEnhance.Brightness(img).enhance(factor)` — blend toward black,
/// i.e. `out = round(0*(1-factor) + img*factor) = round(img*factor)`.
// `for c in 0..3` mirrors PIL's per-band enhance loop, writing `px[c]` from the
// parallel `p.0[c]` pixel array.
#[allow(clippy::needless_range_loop)]
fn enhance_brightness(img: &RgbaImage, factor: f32) -> RgbaImage {
    let (w, h) = img.dimensions();
    let mut out = RgbaImage::new(w, h);
    for (x, y, p) in img.enumerate_pixels() {
        let mut px = [0u8; 4];
        for c in 0..3 {
            px[c] = blend_round(0.0, p.0[c] as f32, factor);
        }
        px[3] = p.0[3];
        out.put_pixel(x, y, Rgba(px));
    }
    out
}

/// Blend every RGB pixel toward a single solid color (`degenerate`):
/// `out = round(degenerate*(1-factor) + img*factor)`.
fn blend_toward_solid(img: &RgbaImage, degenerate: [f32; 3], factor: f32) -> RgbaImage {
    let (w, h) = img.dimensions();
    let mut out = RgbaImage::new(w, h);
    for (x, y, p) in img.enumerate_pixels() {
        let mut px = [0u8; 4];
        for c in 0..3 {
            px[c] = blend_round(degenerate[c], p.0[c] as f32, factor);
        }
        px[3] = p.0[3];
        out.put_pixel(x, y, Rgba(px));
    }
    out
}

/// PIL `Image.blend(in1, in2, alpha)` for one channel:
/// `in1 + alpha*(in2 - in1)`, rounded to nearest and clamped to 0..=255.
/// (Pillow's `ImagingBlend` adds 0.5 before truncation for round-to-nearest.)
#[inline]
fn blend_round(in1: f32, in2: f32, alpha: f32) -> u8 {
    let v = in1 + alpha * (in2 - in1);
    (v + 0.5).floor().clamp(0.0, 255.0) as u8
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{GrayImage, Luma, Rgba, RgbaImage};

    #[test]
    fn rotate90_transposes_dimensions_and_pixels() {
        // Build a 2-wide x 3-tall image with distinct pixels so we can track
        // the transposition. Pixel value encodes (x, y) as R=x, G=y.
        let mut img = RgbaImage::new(2, 3);
        for y in 0..3 {
            for x in 0..2 {
                img.put_pixel(x, y, Rgba([x as u8, y as u8, 0, 255]));
            }
        }
        let rotated = rotate_expand(&DynamicImage::ImageRgba8(img.clone()), 90);
        // 90° (clockwise) of a 2x3 → 3x2 (W/H swap, expand=True).
        assert_eq!((rotated.width(), rotated.height()), (3, 2));
        let r = rotated.to_rgba8();
        // image::imageops::rotate90 maps src(x,y) → dst(h-1-y, x).
        // src is 2(w) x 3(h); dst is 3(w) x 2(h).
        for y in 0..3u32 {
            for x in 0..2u32 {
                let src = img.get_pixel(x, y);
                let dst = r.get_pixel(3 - 1 - y, x);
                assert_eq!(src, dst, "src({x},{y}) should map to dst({},{x})", 3 - 1 - y);
            }
        }
    }

    #[test]
    fn rotate_neg90_equals_rotate270() {
        let mut img = RgbaImage::new(2, 3);
        for y in 0..3 {
            for x in 0..2 {
                img.put_pixel(x, y, Rgba([(x * 50) as u8, (y * 50) as u8, 7, 255]));
            }
        }
        let dyn_img = DynamicImage::ImageRgba8(img);
        let neg90 = rotate_expand(&dyn_img, -90).to_rgba8();
        let r270 = rotate_expand(&dyn_img, 270).to_rgba8();
        assert_eq!(neg90.dimensions(), (3, 2));
        assert_eq!(neg90.as_raw(), r270.as_raw());
    }

    #[test]
    fn rotate180_keeps_dimensions() {
        let img = RgbaImage::from_pixel(4, 5, Rgba([10, 20, 30, 255]));
        let rotated = rotate_expand(&DynamicImage::ImageRgba8(img), 180);
        assert_eq!((rotated.width(), rotated.height()), (4, 5));
    }

    #[test]
    fn sd_mask_to_openai_alpha_inverts_luminance() {
        // White (edit region) → alpha 0; black (keep) → alpha 255.
        let mut mask = GrayImage::new(2, 1);
        mask.put_pixel(0, 0, Luma([255])); // white
        mask.put_pixel(1, 0, Luma([0])); // black
        let oa = sd_mask_to_openai_alpha(&mask, (2, 1));
        assert_eq!(oa.get_pixel(0, 0), &Rgba([255, 255, 255, 0]));
        assert_eq!(oa.get_pixel(1, 0), &Rgba([255, 255, 255, 255]));
        // A mid-grey 100 → alpha 155.
        let mut m2 = GrayImage::new(1, 1);
        m2.put_pixel(0, 0, Luma([100]));
        let oa2 = sd_mask_to_openai_alpha(&m2, (1, 1));
        assert_eq!(oa2.get_pixel(0, 0).0[3], 155);
    }

    #[test]
    fn sd_mask_resizes_when_dims_differ() {
        let mask = GrayImage::from_pixel(1, 1, Luma([255]));
        let oa = sd_mask_to_openai_alpha(&mask, (4, 4));
        assert_eq!(oa.dimensions(), (4, 4));
        assert_eq!(oa.get_pixel(2, 2), &Rgba([255, 255, 255, 0]));
    }

    #[test]
    fn composite_all_black_mask_returns_source() {
        // mask black everywhere (m=0) → out = source.
        let source = RgbaImage::from_pixel(3, 3, Rgba([12, 34, 56, 200]));
        let generated =
            DynamicImage::ImageRgba8(RgbaImage::from_pixel(3, 3, Rgba([200, 100, 50, 255])));
        let mask = GrayImage::from_pixel(3, 3, Luma([0]));
        let out = composite_with_mask(&generated, &source, &mask);
        for p in out.pixels() {
            assert_eq!(p, &Rgba([12, 34, 56, 200]));
        }
    }

    #[test]
    fn composite_all_white_mask_returns_generated_rgb_source_alpha() {
        // mask white everywhere (m=255) → RGB = generated, alpha = source.
        let source = RgbaImage::from_pixel(3, 3, Rgba([12, 34, 56, 200]));
        let generated =
            DynamicImage::ImageRgba8(RgbaImage::from_pixel(3, 3, Rgba([200, 100, 50, 255])));
        let mask = GrayImage::from_pixel(3, 3, Luma([255]));
        let out = composite_with_mask(&generated, &source, &mask);
        for p in out.pixels() {
            // RGB from generated, alpha carried from source.
            assert_eq!(p, &Rgba([200, 100, 50, 200]));
        }
    }

    #[test]
    fn composite_resizes_generated_to_source() {
        let source = RgbaImage::from_pixel(4, 4, Rgba([0, 0, 0, 255]));
        let generated =
            DynamicImage::ImageRgba8(RgbaImage::from_pixel(2, 2, Rgba([255, 255, 255, 255])));
        let mask = GrayImage::from_pixel(4, 4, Luma([255]));
        let out = composite_with_mask(&generated, &source, &mask);
        assert_eq!(out.dimensions(), (4, 4));
        // White generated upsampled, full-white mask → all white RGB.
        assert_eq!(out.get_pixel(0, 0).0[0], 255);
    }

    #[test]
    fn multiply_luma_255_is_identity() {
        // ImageChops.multiply(255, x) == x.
        let a = GrayImage::from_pixel(3, 1, Luma([255]));
        let mut b = GrayImage::new(3, 1);
        b.put_pixel(0, 0, Luma([0]));
        b.put_pixel(1, 0, Luma([128]));
        b.put_pixel(2, 0, Luma([255]));
        let out = multiply_luma(&a, &b);
        assert_eq!(out.get_pixel(0, 0), &Luma([0]));
        assert_eq!(out.get_pixel(1, 0), &Luma([128]));
        assert_eq!(out.get_pixel(2, 0), &Luma([255]));
    }

    #[test]
    fn multiply_luma_zero_is_zero() {
        let a = GrayImage::from_pixel(2, 1, Luma([0]));
        let b = GrayImage::from_pixel(2, 1, Luma([200]));
        let out = multiply_luma(&a, &b);
        assert_eq!(out.get_pixel(0, 0), &Luma([0]));
    }

    #[test]
    fn multiply_luma_half_rounds() {
        // 128 * 128 / 255 = 64.25 → (128*128+127)/255 = 64.
        let a = GrayImage::from_pixel(1, 1, Luma([128]));
        let b = GrayImage::from_pixel(1, 1, Luma([128]));
        let out = multiply_luma(&a, &b);
        assert_eq!(out.get_pixel(0, 0), &Luma([64]));
    }

    #[test]
    fn unsharp_mask_on_flat_image_is_noop_and_carries_alpha() {
        // A flat image has no high frequencies, so sharpening can't change it.
        let img = RgbaImage::from_pixel(8, 8, Rgba([120, 130, 140, 77]));
        let out = unsharp_mask(&img, 2.0, 200, 3);
        for p in out.pixels() {
            assert_eq!(p, &Rgba([120, 130, 140, 77]));
        }
    }

    #[test]
    fn enhance_brightness_factor_scales_toward_black() {
        let img = RgbaImage::from_pixel(2, 2, Rgba([100, 200, 50, 255]));
        let out = enhance_brightness(&img, 0.5);
        // 100*0.5=50, 200*0.5=100, 50*0.5=25 (round-to-nearest).
        assert_eq!(out.get_pixel(0, 0), &Rgba([50, 100, 25, 255]));
    }

    #[test]
    fn enhance_brightness_clamps_high() {
        let img = RgbaImage::from_pixel(1, 1, Rgba([200, 200, 200, 255]));
        let out = enhance_brightness(&img, 2.0);
        assert_eq!(out.get_pixel(0, 0), &Rgba([255, 255, 255, 255]));
    }

    #[test]
    fn enhance_color_factor_one_is_identity() {
        let img = RgbaImage::from_pixel(2, 2, Rgba([10, 120, 240, 255]));
        let out = enhance_color(&img, 1.0);
        assert_eq!(out.get_pixel(0, 0), &Rgba([10, 120, 240, 255]));
    }

    #[test]
    fn enhance_color_factor_zero_is_grayscale() {
        let img = RgbaImage::from_pixel(2, 2, Rgba([10, 120, 240, 255]));
        let out = enhance_color(&img, 0.0);
        let l = luma_601(10, 120, 240);
        assert_eq!(out.get_pixel(0, 0), &Rgba([l, l, l, 255]));
    }

    #[test]
    fn enhance_contrast_factor_one_is_identity() {
        let img = RgbaImage::from_pixel(3, 3, Rgba([40, 90, 160, 255]));
        let out = enhance_contrast(&img, 1.0);
        assert_eq!(out.get_pixel(1, 1), &Rgba([40, 90, 160, 255]));
    }

    #[test]
    fn decode_encode_roundtrip_png() {
        // Encode a tiny known image to PNG b64, decode it back, compare pixels.
        let img = DynamicImage::ImageRgba8(RgbaImage::from_pixel(2, 2, Rgba([1, 2, 3, 255])));
        let b64 = encode_png_b64(&img).expect("encode");
        let back = decode_image_b64(&b64).expect("decode");
        assert_eq!(back.to_rgba8(), img.to_rgba8());
    }

    #[test]
    fn decode_bad_b64_is_err_not_panic() {
        assert!(decode_image_b64("!!!not base64!!!").is_err());
        // Valid base64 but not an image.
        let not_img = base64::engine::general_purpose::STANDARD.encode(b"hello world");
        assert!(decode_image_b64(&not_img).is_err());
    }

    #[test]
    fn pil_enhance_fallback_preserves_dimensions() {
        let img = RgbaImage::from_pixel(16, 12, Rgba([100, 110, 120, 255]));
        let out = pil_enhance_fallback(&img);
        assert_eq!(out.dimensions(), (16, 12));
    }

    #[test]
    fn median_filter_removes_single_outlier() {
        // Flat 100 with one spike pixel; the 3x3 median nukes the spike.
        let mut img = RgbaImage::from_pixel(3, 3, Rgba([100, 100, 100, 255]));
        img.put_pixel(1, 1, Rgba([255, 0, 50, 255]));
        let out = median_filter_3x3(&img);
        // Center's 3x3 window is eight 100s + the spike → median is 100.
        assert_eq!(out.get_pixel(1, 1).0[0], 100);
        assert_eq!(out.get_pixel(1, 1).0[1], 100);
    }
}
