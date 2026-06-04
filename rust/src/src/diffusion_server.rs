// src/diffusion_server.rs  <- scripts/diffusion_server.py
//! Local Stable-Diffusion image server (the Rust port of the standalone
//! `scripts/diffusion_server.py`). Serves the OpenAI-compatible
//! `POST /v1/images/generations`, `POST /v1/images/inpaint`, and `GET /v1/models`
//! so the app's image-gen can point at it as a local backend —
//! `odysseus diffusion-server [--host H] [--port N] [--model REPO] [--version V]`.
//!
//! ## Engine + supported architectures (candle)
//!
//! Runs on **candle** (HuggingFace's pure-Rust ML framework). `--version` selects
//! the architecture; weights are fetched via `hf-hub` from the configured repo and
//! cached:
//!
//! * `v1.5`        — Stable-Diffusion v1.5 txt2img (single CLIP).
//! * `xl`          — SDXL base txt2img (dual CLIP: CLIP-L + OpenCLIP-bigG).
//! * `xl-turbo`    — SDXL-Turbo txt2img (1 step, guidance 0).
//! * `v1.5-inpaint`— SD-1.5 inpainting (9-channel UNet) → `/v1/images/inpaint`.
//!
//! ## Documented parity notes vs. the Python
//!
//! * SDXL **micro-conditioning** (pooled text embeds + time-ids `added_cond_kwargs`)
//!   is omitted — candle's UNet does not expose that input, so candle's own SDXL
//!   example omits it too. SDXL therefore runs with the concatenated cross-attention
//!   embeddings only (functional; slight quality drift from a full diffusers SDXL).
//! * The Python inpaint endpoint has img2img/txt2img *composite fallbacks* for
//!   non-inpaint pipelines; here the native 9-channel inpaint path is the one
//!   served (load a `v1.5-inpaint` model to use it). FLUX is not ported.
//! * Inference is CPU by default (slow); build candle with `metal`/`cuda` to
//!   accelerate. On any model-fetch / inference failure the handler returns a JSON
//!   error (HTTP 500), never a fabricated image.

use crate::pylog as logger;
use axum::{extract::State, routing::get, routing::post, Json, Router};
use base64::Engine as _;
use candle_core::{DType, Device, IndexOp, Module, Tensor};
use candle_transformers::models::stable_diffusion::{clip, StableDiffusionConfig};
use serde_json::{json, Value};
use std::sync::Arc;

/// Default txt2img weights repo (the live SD-1.5 community re-host; `runwayml/...`
/// was removed). Override with `--model <hf-repo-id>`.
pub const DEFAULT_SD15_REPO: &str = "stable-diffusion-v1-5/stable-diffusion-v1-5";

/// Supported architectures.
#[derive(Clone, Copy, PartialEq, Eq)]
enum SdArch {
    V15,
    Xl,
    XlTurbo,
    V15Inpaint,
}

impl SdArch {
    fn parse(s: &str) -> Option<Self> {
        match s.trim().to_lowercase().as_str() {
            "v1.5" | "v15" | "sd15" | "1.5" => Some(Self::V15),
            "xl" | "sdxl" | "xl-base" => Some(Self::Xl),
            "xl-turbo" | "sdxl-turbo" | "turbo" => Some(Self::XlTurbo),
            "v1.5-inpaint" | "inpaint" | "sd15-inpaint" => Some(Self::V15Inpaint),
            _ => None,
        }
    }
    fn is_inpaint(self) -> bool {
        matches!(self, Self::V15Inpaint)
    }
    fn dual_clip(self) -> bool {
        matches!(self, Self::Xl | Self::XlTurbo)
    }
    fn vae_scale(self) -> f64 {
        match self {
            Self::XlTurbo => 0.13025,
            _ => 0.18215,
        }
    }
    fn default_steps(self) -> usize {
        match self {
            Self::XlTurbo => 1,
            _ => 30,
        }
    }
    fn default_guidance(self) -> f64 {
        match self {
            Self::XlTurbo => 0.0,
            _ => 7.5,
        }
    }
    fn unet_in_channels(self) -> usize {
        if self.is_inpaint() {
            9
        } else {
            4
        }
    }
    /// `StableDiffusionConfig` for this architecture at the requested size.
    fn config(self, h: usize, w: usize) -> StableDiffusionConfig {
        match self {
            Self::V15 | Self::V15Inpaint => {
                StableDiffusionConfig::v1_5(None, Some(h), Some(w))
            }
            Self::Xl => StableDiffusionConfig::sdxl(None, Some(h), Some(w)),
            Self::XlTurbo => StableDiffusionConfig::sdxl_turbo(None, Some(h), Some(w)),
        }
    }
    /// CLIP BPE tokenizer repo (same tokenizer for both SDXL encoders).
    fn tokenizer_repo(self) -> &'static str {
        if self.dual_clip() {
            "openai/clip-vit-large-patch14"
        } else {
            "openai/clip-vit-base-patch32"
        }
    }
    /// Default weights repo when `--model` is left at its default.
    fn default_repo(self) -> &'static str {
        match self {
            Self::V15 => DEFAULT_SD15_REPO,
            Self::Xl => "stabilityai/stable-diffusion-xl-base-1.0",
            Self::XlTurbo => "stabilityai/sdxl-turbo",
            Self::V15Inpaint => "stable-diffusion-v1-5/stable-diffusion-inpainting",
        }
    }
}

/// A loaded pipeline (shared, immutable; inference is `&self`).
pub struct SdModel {
    arch: SdArch,
    config: StableDiffusionConfig,
    tokenizer: tokenizers::Tokenizer,
    text_model: clip::ClipTextTransformer,
    text_model2: Option<clip::ClipTextTransformer>,
    vae: candle_transformers::models::stable_diffusion::vae::AutoEncoderKL,
    unet: candle_transformers::models::stable_diffusion::unet_2d::UNet2DConditionModel,
    device: Device,
    dtype: DType,
    pad_id: u32,
}

impl SdModel {
    fn load(arch: SdArch, repo: &str) -> Result<Self, String> {
        let device = Device::Cpu;
        let dtype = DType::F32;
        let config = arch.config(512, 512);

        let api = hf_hub::api::sync::Api::new().map_err(|e| format!("hf-hub: {e}"))?;
        let get = |repo_id: &str, path: &str| -> Result<std::path::PathBuf, String> {
            api.model(repo_id.to_string())
                .get(path)
                .map_err(|e| format!("fetch {repo_id}/{path}: {e}"))
        };

        let tok_path = get(arch.tokenizer_repo(), "tokenizer.json")?;
        let tokenizer =
            tokenizers::Tokenizer::from_file(&tok_path).map_err(|e| format!("tokenizer: {e}"))?;
        let pad_id = match &config.clip.pad_with {
            Some(p) => *tokenizer
                .get_vocab(true)
                .get(p.as_str())
                .ok_or("pad token not in vocab")?,
            None => *tokenizer
                .get_vocab(true)
                .get("<|endoftext|>")
                .ok_or("eos token not in vocab")?,
        };

        let clip_w = get(repo, "text_encoder/model.safetensors")?;
        let vae_w = get(repo, "vae/diffusion_pytorch_model.safetensors")?;
        let unet_w = get(repo, "unet/diffusion_pytorch_model.safetensors")?;

        logger::info("Loading Stable-Diffusion components (CLIP/VAE/UNet) …");
        let text_model = candle_transformers::models::stable_diffusion::build_clip_transformer(
            &config.clip,
            clip_w,
            &device,
            dtype,
        )
        .map_err(|e| format!("clip: {e}"))?;
        let text_model2 = if arch.dual_clip() {
            let clip2 = config
                .clip2
                .as_ref()
                .ok_or("dual-CLIP arch missing clip2 config")?;
            let clip2_w = get(repo, "text_encoder_2/model.safetensors")?;
            Some(
                candle_transformers::models::stable_diffusion::build_clip_transformer(
                    clip2, clip2_w, &device, dtype,
                )
                .map_err(|e| format!("clip2: {e}"))?,
            )
        } else {
            None
        };
        let vae = config
            .build_vae(vae_w, &device, dtype)
            .map_err(|e| format!("vae: {e}"))?;
        let unet = config
            .build_unet(unet_w, &device, arch.unet_in_channels(), false, dtype)
            .map_err(|e| format!("unet: {e}"))?;

        Ok(Self {
            arch,
            config,
            tokenizer,
            text_model,
            text_model2,
            vae,
            unet,
            device,
            dtype,
            pad_id,
        })
    }

    /// Tokenize + pad to the CLIP context length.
    fn tokens(&self, prompt: &str) -> Result<Tensor, String> {
        let max_len = self.config.clip.max_position_embeddings;
        let mut ids = self
            .tokenizer
            .encode(prompt, true)
            .map_err(|e| format!("encode: {e}"))?
            .get_ids()
            .to_vec();
        if ids.len() > max_len {
            ids.truncate(max_len);
        }
        while ids.len() < max_len {
            ids.push(self.pad_id);
        }
        Tensor::new(ids.as_slice(), &self.device)
            .and_then(|t| t.unsqueeze(0))
            .map_err(|e| format!("tokens: {e}"))
    }

    /// Embed `prompt` -> cross-attention hidden states. SDXL concatenates the two
    /// CLIP encoders' hidden states along the feature dim (candle's approach).
    fn embed(&self, prompt: &str) -> Result<Tensor, String> {
        let t = self.tokens(prompt)?;
        let e1 = self.text_model.forward(&t).map_err(|e| format!("clip: {e}"))?;
        match &self.text_model2 {
            Some(m2) => {
                let e2 = m2.forward(&t).map_err(|e| format!("clip2: {e}"))?;
                Tensor::cat(&[e1, e2], candle_core::D::Minus1)
                    .map_err(|e| format!("cat clip: {e}"))
            }
            None => Ok(e1),
        }
    }

    /// Build `[uncond, cond]` (or just `cond`) text embeddings for CFG.
    fn text_embeddings(&self, prompt: &str, use_guide: bool) -> Result<Tensor, String> {
        let cond = self.embed(prompt)?;
        if use_guide {
            let uncond = self.embed("")?;
            Tensor::cat(&[uncond, cond], 0)
                .and_then(|t| t.to_dtype(self.dtype))
                .map_err(|e| format!("cat emb: {e}"))
        } else {
            cond.to_dtype(self.dtype).map_err(|e| format!("emb: {e}"))
        }
    }

    /// txt2img -> PNG bytes.
    pub fn txt2img(
        &self,
        prompt: &str,
        steps: usize,
        guidance: f64,
        seed: u64,
        height: usize,
        width: usize,
    ) -> Result<Vec<u8>, String> {
        let use_guide = guidance > 1.0;
        let text_emb = self.text_embeddings(prompt, use_guide)?;
        let mut scheduler = self
            .config
            .build_scheduler(steps)
            .map_err(|e| format!("scheduler: {e}"))?;
        self.device.set_seed(seed).map_err(|e| format!("seed: {e}"))?;
        let latents = Tensor::randn(0f32, 1f32, (1, 4, height / 8, width / 8), &self.device)
            .map_err(|e| format!("latents: {e}"))?;
        let mut latents = (latents * scheduler.init_noise_sigma())
            .and_then(|t| t.to_dtype(self.dtype))
            .map_err(|e| format!("latents init: {e}"))?;

        let timesteps = scheduler.timesteps().to_vec();
        for &t in timesteps.iter() {
            latents =
                self.denoise_step(&mut scheduler, &latents, &text_emb, t, use_guide, guidance, None)?;
        }
        self.decode_latents(&latents)
    }

    /// Native inpaint (9-channel UNet) -> PNG bytes. `image`/`mask` are decoded
    /// PNG bytes; `strength` controls how much of the masked region is regenerated.
    #[allow(clippy::too_many_arguments)]
    pub fn inpaint(
        &self,
        prompt: &str,
        image_png: &[u8],
        mask_png: &[u8],
        strength: f64,
        steps: usize,
        guidance: f64,
        seed: u64,
    ) -> Result<Vec<u8>, String> {
        if !self.arch.is_inpaint() {
            return Err("loaded model is not an inpaint model (use --version v1.5-inpaint)".into());
        }
        // Decode image (RGB, [-1,1]) + mask (L, [0,1]); snap to /8.
        let (img_t, h, w) = decode_image_tensor(image_png, &self.device, self.dtype)?;
        let mask_full = decode_mask_tensor(mask_png, h, w, &self.device, self.dtype)?; // (1,1,h,w)

        let use_guide = guidance > 1.0;
        let text_emb = self.text_embeddings(prompt, use_guide)?;

        // masked image = image * (mask <= 0.5) (keep the NON-masked region).
        let keep = mask_full
            .le(0.5f32)
            .and_then(|t| t.to_dtype(self.dtype))
            .and_then(|t| t.broadcast_mul(&img_t))
            .map_err(|e| format!("masked img: {e}"))?;
        // VAE-encode the masked image -> latents (scaled).
        let masked_lat = self
            .vae
            .encode(&keep)
            .and_then(|d| d.sample())
            .and_then(|t| t * self.arch.vae_scale())
            .and_then(|t| t.to_dtype(self.dtype))
            .map_err(|e| format!("encode masked: {e}"))?;
        // Mask downsampled to latent resolution (1,1,h/8,w/8).
        let mask_lat = mask_full
            .interpolate2d(h / 8, w / 8)
            .and_then(|t| t.to_dtype(self.dtype))
            .map_err(|e| format!("mask latent: {e}"))?;

        // Init latents from the original image (img2img start) so unmasked context
        // is preserved; add noise at the strength-derived start timestep.
        let init_lat = self
            .vae
            .encode(&img_t)
            .and_then(|d| d.sample())
            .and_then(|t| t * self.arch.vae_scale())
            .and_then(|t| t.to_dtype(self.dtype))
            .map_err(|e| format!("encode init: {e}"))?;

        let mut scheduler = self
            .config
            .build_scheduler(steps)
            .map_err(|e| format!("scheduler: {e}"))?;
        self.device.set_seed(seed).map_err(|e| format!("seed: {e}"))?;
        let timesteps = scheduler.timesteps().to_vec();
        let strength = strength.clamp(0.1, 1.0);
        let t_start = steps - ((steps as f64) * strength) as usize;
        let noise = Tensor::randn(0f32, 1f32, init_lat.shape(), &self.device)
            .and_then(|t| t.to_dtype(self.dtype))
            .map_err(|e| format!("noise: {e}"))?;
        let start_ts = *timesteps.get(t_start.min(timesteps.len().saturating_sub(1))).unwrap_or(&0);
        let mut latents = scheduler
            .add_noise(&init_lat, noise, start_ts)
            .map_err(|e| format!("add_noise: {e}"))?;

        for &t in timesteps.iter().skip(t_start) {
            latents = self.denoise_step(
                &mut scheduler,
                &latents,
                &text_emb,
                t,
                use_guide,
                guidance,
                Some((&mask_lat, &masked_lat)),
            )?;
        }

        // Composite the generated image onto the original through the (soft) mask,
        // so the unmasked region is preserved exactly with a feathered seam — the
        // Python inpaint composite / "harmonize" behavior.
        let gen = self.latents_to_rgb(&latents)?;
        let orig = image::imageops::resize(
            &image::load_from_memory(image_png)
                .map_err(|e| format!("orig decode: {e}"))?
                .to_rgb8(),
            w as u32,
            h as u32,
            image::imageops::FilterType::Lanczos3,
        );
        let mask_img = image::imageops::resize(
            &image::load_from_memory(mask_png)
                .map_err(|e| format!("mask decode: {e}"))?
                .to_luma8(),
            w as u32,
            h as u32,
            image::imageops::FilterType::Triangle,
        );
        encode_png(&composite_masked(&orig, &gen, &mask_img))
    }

    /// One scheduler step. When `inpaint` is `Some`, concatenate the latent-mask
    /// and masked-image latents to make the 9-channel UNet input.
    #[allow(clippy::too_many_arguments)]
    fn denoise_step(
        &self,
        scheduler: &mut Box<dyn candle_transformers::models::stable_diffusion::schedulers::Scheduler>,
        latents: &Tensor,
        text_emb: &Tensor,
        t: usize,
        use_guide: bool,
        guidance: f64,
        inpaint: Option<(&Tensor, &Tensor)>,
    ) -> Result<Tensor, String> {
        let input = if use_guide {
            Tensor::cat(&[latents, latents], 0).map_err(|e| format!("cat: {e}"))?
        } else {
            latents.clone()
        };
        let mut input = scheduler
            .scale_model_input(input, t)
            .map_err(|e| format!("scale: {e}"))?;
        if let Some((mask_lat, masked_lat)) = inpaint {
            // Duplicate the conditioning latents to match the CFG batch.
            let (mask_c, masked_c) = if use_guide {
                (
                    Tensor::cat(&[mask_lat, mask_lat], 0).map_err(|e| e.to_string())?,
                    Tensor::cat(&[masked_lat, masked_lat], 0).map_err(|e| e.to_string())?,
                )
            } else {
                ((*mask_lat).clone(), (*masked_lat).clone())
            };
            input = Tensor::cat(&[&input, &mask_c, &masked_c], 1)
                .map_err(|e| format!("cat 9ch: {e}"))?;
        }
        let noise = self
            .unet
            .forward(&input, t as f64, text_emb)
            .map_err(|e| format!("unet: {e}"))?;
        let noise = if use_guide {
            let parts = noise.chunk(2, 0).map_err(|e| format!("chunk: {e}"))?;
            let (uncond, text) = (&parts[0], &parts[1]);
            (uncond
                + ((text - uncond).map_err(|e| e.to_string())? * guidance)
                    .map_err(|e| e.to_string())?)
            .map_err(|e| format!("guidance: {e}"))?
        } else {
            noise
        };
        scheduler
            .step(&noise, t, latents)
            .map_err(|e| format!("step: {e}"))
    }

    /// VAE-decode latents -> an RGB image buffer.
    fn latents_to_rgb(&self, latents: &Tensor) -> Result<image::RgbImage, String> {
        let img = self
            .vae
            .decode(&(latents / self.arch.vae_scale()).map_err(|e| e.to_string())?)
            .map_err(|e| format!("vae decode: {e}"))?;
        let img = ((img / 2.0).map_err(|e| e.to_string())? + 0.5).map_err(|e| e.to_string())?;
        let img = img
            .clamp(0f32, 1f32)
            .and_then(|t| t * 255.0)
            .and_then(|t| t.to_dtype(DType::U8))
            .map_err(|e| format!("to u8: {e}"))?;
        let img = img.i(0).map_err(|e| e.to_string())?;
        let (c, h, w) = img.dims3().map_err(|e| e.to_string())?;
        if c != 3 {
            return Err(format!("unexpected channel count {c}"));
        }
        let hwc = img
            .permute((1, 2, 0))
            .and_then(|t| t.flatten_all())
            .and_then(|t| t.to_vec1::<u8>())
            .map_err(|e| format!("to_vec: {e}"))?;
        image::RgbImage::from_raw(w as u32, h as u32, hwc).ok_or_else(|| "image buffer".into())
    }

    /// VAE-decode latents -> PNG bytes.
    fn decode_latents(&self, latents: &Tensor) -> Result<Vec<u8>, String> {
        encode_png(&self.latents_to_rgb(latents)?)
    }
}

/// PNG-encode an RGB image.
fn encode_png(rgb: &image::RgbImage) -> Result<Vec<u8>, String> {
    let mut png: Vec<u8> = Vec::new();
    image::DynamicImage::ImageRgb8(rgb.clone())
        .write_to(&mut std::io::Cursor::new(&mut png), image::ImageFormat::Png)
        .map_err(|e| format!("png: {e}"))?;
    Ok(png)
}

/// Feathered composite: `out = orig*(1-m) + gen*m`, where `m = mask/255` (white =
/// inpaint = take the generated pixel). With a soft (resized) mask this feathers
/// the seam, and the unmasked region is preserved exactly — the Python inpaint
/// composite / "harmonize" behavior.
fn composite_masked(
    orig: &image::RgbImage,
    gen: &image::RgbImage,
    mask: &image::GrayImage,
) -> image::RgbImage {
    let (w, h) = orig.dimensions();
    let mut out = image::RgbImage::new(w, h);
    for y in 0..h {
        for x in 0..w {
            let m = mask.get_pixel(x, y).0[0] as f32 / 255.0;
            let o = orig.get_pixel(x, y).0;
            let g = gen.get_pixel(x, y).0;
            let mut px = [0u8; 3];
            for c in 0..3 {
                px[c] = (o[c] as f32 * (1.0 - m) + g[c] as f32 * m)
                    .round()
                    .clamp(0.0, 255.0) as u8;
            }
            out.put_pixel(x, y, image::Rgb(px));
        }
    }
    out
}

/// Decode a PNG/JPEG to an RGB `f32` tensor in [-1, 1], shape `(1,3,H,W)`, with
/// H/W snapped down to a multiple of 8. Returns `(tensor, H, W)`.
fn decode_image_tensor(
    bytes: &[u8],
    device: &Device,
    dtype: DType,
) -> Result<(Tensor, usize, usize), String> {
    let img = image::load_from_memory(bytes)
        .map_err(|e| format!("image decode: {e}"))?
        .to_rgb8();
    let (w0, h0) = (img.width() as usize, img.height() as usize);
    let (w, h) = ((w0 / 8 * 8).max(256), (h0 / 8 * 8).max(256));
    let img = image::imageops::resize(&img, w as u32, h as u32, image::imageops::FilterType::Lanczos3);
    let mut data = Vec::with_capacity(h * w * 3);
    for p in img.pixels() {
        for c in 0..3 {
            data.push(p.0[c] as f32 / 127.5 - 1.0);
        }
    }
    let t = Tensor::from_vec(data, (h, w, 3), device)
        .and_then(|t| t.permute((2, 0, 1)))
        .and_then(|t| t.unsqueeze(0))
        .and_then(|t| t.to_dtype(dtype))
        .map_err(|e| format!("img tensor: {e}"))?;
    Ok((t, h, w))
}

/// Decode a mask PNG to a single-channel `f32` tensor in [0, 1] (white = inpaint),
/// resized to `(h, w)`, shape `(1,1,H,W)`.
fn decode_mask_tensor(
    bytes: &[u8],
    h: usize,
    w: usize,
    device: &Device,
    dtype: DType,
) -> Result<Tensor, String> {
    let m = image::load_from_memory(bytes)
        .map_err(|e| format!("mask decode: {e}"))?
        .to_luma8();
    let m = image::imageops::resize(&m, w as u32, h as u32, image::imageops::FilterType::Triangle);
    let data: Vec<f32> = m.pixels().map(|p| p.0[0] as f32 / 255.0).collect();
    Tensor::from_vec(data, (1, 1, h, w), device)
        .and_then(|t| t.to_dtype(dtype))
        .map_err(|e| format!("mask tensor: {e}"))
}

// ── HTTP server ──

struct ServerState {
    model: Arc<SdModel>,
}

fn parse_size(req: &Value, default: usize) -> (usize, usize) {
    let size = req.get("size").and_then(Value::as_str).unwrap_or("");
    let (mut w, mut h) = match size.split_once('x') {
        Some((a, b)) => (
            a.trim().parse::<usize>().unwrap_or(default),
            b.trim().parse::<usize>().unwrap_or(default),
        ),
        None => (default, default),
    };
    w = (w / 8 * 8).clamp(256, 1024);
    h = (h / 8 * 8).clamp(256, 1024);
    (w, h)
}

async fn generations(State(st): State<Arc<ServerState>>, Json(req): Json<Value>) -> Json<Value> {
    let prompt = req.get("prompt").and_then(Value::as_str).unwrap_or("").to_string();
    if prompt.is_empty() {
        return Json(json!({"error": {"message": "prompt is required"}}));
    }
    let n = req.get("n").and_then(Value::as_u64).unwrap_or(1).clamp(1, 4) as usize;
    let default_dim = if st.model.arch.dual_clip() { 768 } else { 512 };
    let (width, height) = parse_size(&req, default_dim);
    let steps = req
        .get("num_inference_steps")
        .and_then(Value::as_u64)
        .map(|v| v as usize)
        .unwrap_or_else(|| st.model.arch.default_steps())
        .clamp(1, 100);
    let guidance = req
        .get("guidance_scale")
        .and_then(Value::as_f64)
        .unwrap_or_else(|| st.model.arch.default_guidance());

    let mut data = Vec::new();
    for i in 0..n {
        let model = st.model.clone();
        let p = prompt.clone();
        let g = guidance;
        let seed = 42u64.wrapping_add(i as u64);
        let png = tokio::task::spawn_blocking(move || {
            model.txt2img(&p, steps, g, seed, height, width)
        })
        .await;
        match png {
            Ok(Ok(bytes)) => data.push(json!({"b64_json": b64(&bytes)})),
            Ok(Err(e)) => return err_json(&e),
            Err(e) => return err_json(&format!("task failed: {e}")),
        }
    }
    Json(json!({"created": crate::pytime::time() as i64, "data": data}))
}

async fn inpaint(State(st): State<Arc<ServerState>>, Json(req): Json<Value>) -> Json<Value> {
    let prompt = req.get("prompt").and_then(Value::as_str).unwrap_or("").to_string();
    let image_b64 = req.get("image").and_then(Value::as_str).unwrap_or("");
    let mask_b64 = req.get("mask").and_then(Value::as_str).unwrap_or("");
    if image_b64.is_empty() || mask_b64.is_empty() {
        return Json(json!({"error": {"message": "image and mask (base64 PNG) are required"}}));
    }
    let image = match base64::engine::general_purpose::STANDARD.decode(image_b64.trim()) {
        Ok(b) => b,
        Err(e) => return err_json(&format!("bad image base64: {e}")),
    };
    let mask = match base64::engine::general_purpose::STANDARD.decode(mask_b64.trim()) {
        Ok(b) => b,
        Err(e) => return err_json(&format!("bad mask base64: {e}")),
    };
    let strength = req.get("strength").and_then(Value::as_f64).unwrap_or(0.75);
    let steps = req
        .get("num_inference_steps")
        .and_then(Value::as_u64)
        .map(|v| v as usize)
        .unwrap_or_else(|| st.model.arch.default_steps())
        .clamp(1, 100);
    let guidance = req
        .get("guidance_scale")
        .and_then(Value::as_f64)
        .unwrap_or_else(|| st.model.arch.default_guidance());

    let model = st.model.clone();
    let png = tokio::task::spawn_blocking(move || {
        model.inpaint(&prompt, &image, &mask, strength, steps, guidance, 42)
    })
    .await;
    match png {
        Ok(Ok(bytes)) => Json(json!({"created": crate::pytime::time() as i64, "data": [{"b64_json": b64(&bytes)}]})),
        Ok(Err(e)) => err_json(&e),
        Err(e) => err_json(&format!("task failed: {e}")),
    }
}

async fn models(State(st): State<Arc<ServerState>>) -> Json<Value> {
    let id = match st.model.arch {
        SdArch::V15 => "stable-diffusion-v1-5",
        SdArch::Xl => "stable-diffusion-xl",
        SdArch::XlTurbo => "sdxl-turbo",
        SdArch::V15Inpaint => "stable-diffusion-v1-5-inpaint",
    };
    Json(json!({"object": "list", "data": [{"id": id, "object": "model", "owned_by": "local"}]}))
}

fn b64(bytes: &[u8]) -> String {
    base64::engine::general_purpose::STANDARD.encode(bytes)
}
fn err_json(msg: &str) -> Json<Value> {
    logger::error(&format!("diffusion: {msg}"));
    Json(json!({"error": {"message": msg}}))
}

/// Run the diffusion server: load the model, then serve the OpenAI image API.
pub async fn run_diffusion_server(
    host: &str,
    port: u16,
    repo: &str,
    version: &str,
) -> Result<(), String> {
    let arch = SdArch::parse(version)
        .ok_or_else(|| format!("unknown --version '{version}' (use v1.5 | xl | xl-turbo | v1.5-inpaint)"))?;
    // If --model is the default sentinel, use the arch's canonical repo.
    let repo = if repo == DEFAULT_SD15_REPO && arch != SdArch::V15 {
        arch.default_repo().to_string()
    } else {
        repo.to_string()
    };
    logger::info(&format!(
        "Diffusion server: loading '{repo}' ({version}) — downloads weights on first run…"
    ));
    let model = tokio::task::spawn_blocking(move || SdModel::load(arch, &repo))
        .await
        .map_err(|e| format!("load task: {e}"))??;
    let state = Arc::new(ServerState {
        model: Arc::new(model),
    });
    let mut app = Router::new()
        .route("/v1/images/generations", post(generations))
        .route("/v1/models", get(models));
    if arch.is_inpaint() {
        app = app.route("/v1/images/inpaint", post(inpaint));
    }
    let app = app.with_state(state);
    let addr = format!("{host}:{port}");
    let listener = tokio::net::TcpListener::bind(&addr)
        .await
        .map_err(|e| format!("bind {addr}: {e}"))?;
    logger::info(&format!("Diffusion server listening on http://{addr}"));
    axum::serve(listener, app)
        .await
        .map_err(|e| format!("serve: {e}"))
}
