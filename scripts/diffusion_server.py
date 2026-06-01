#!/usr/bin/env python3
"""Minimal OpenAI-compatible image generation API server using diffusers.

Serves /v1/images/generations and /v1/models for compatibility with
Odysseus's image generation tool.

Usage:
    python3 scripts/diffusion_server.py --model /path/to/model --port 8100
"""
import os
import sys
import importlib
import importlib.machinery
# Block xformers — create a fake module that reports as not installed
_fake = type(sys)("xformers")
_fake.__version__ = "0.0.0"
_fake.__spec__ = importlib.machinery.ModuleSpec("xformers", None)
_fake.__path__ = []
sys.modules["xformers"] = _fake
sys.modules["xformers.ops"] = type(sys)("xformers.ops")
sys.modules["xformers.ops.fmha"] = type(sys)("xformers.ops.fmha")

import argparse
import base64
import io
import json
import logging
import time
from pathlib import Path

from contextlib import asynccontextmanager

import torch
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("diffusion_server")

_pipe = None
_model_id = ""
DTYPE_MAP = {"bfloat16": torch.bfloat16, "float16": torch.float16, "float32": torch.float32}
_args = None


@asynccontextmanager
async def lifespan(application):
    load_model()
    yield


app = FastAPI(title="Diffusion Server", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ImageRequest(BaseModel):
    model: str = ""
    prompt: str
    n: int = 1
    size: str = "1024x1024"
    quality: str = "medium"
    response_format: str = "b64_json"


def _fix_meta_tensors(pipe, dtype):
    """Replace any meta tensors with real zero tensors on CPU so .to(cuda) works."""
    for name, component in pipe.components.items():
        if not hasattr(component, 'parameters'):
            continue
        fixed = 0
        for pname, param in component.named_parameters():
            if param.device.type == 'meta':
                with torch.no_grad():
                    new_param = torch.zeros(param.shape, dtype=dtype, device='cpu')
                    # Walk to the actual module holding this param
                    parts = pname.split('.')
                    mod = component
                    for p in parts[:-1]:
                        mod = getattr(mod, p)
                    setattr(mod, parts[-1], torch.nn.Parameter(new_param, requires_grad=param.requires_grad))
                    fixed += 1
        if fixed:
            logger.info(f"  Fixed {fixed} meta tensors in {name}")


def load_model():
    global _pipe, _model_id
    import diffusers

    model_path = _args.model
    _model_id = Path(model_path).name
    dtype_map = {"bfloat16": torch.bfloat16, "float16": torch.float16, "float32": torch.float32}
    torch_dtype = dtype_map.get(_args.dtype, torch.bfloat16)
    use_offload = _args.cpu_offload

    logger.info(f"Loading model from {model_path} (dtype={_args.dtype}, offload={use_offload})...")

    # Ensure HF token is available for gated repos
    _hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if _hf_token:
        logger.info("HF token found in environment")
        # Login so all huggingface_hub calls use the token
        try:
            from huggingface_hub import login
            login(token=_hf_token, add_to_git_credential=False)
            logger.info("Logged in to HuggingFace Hub")
        except Exception as e:
            logger.warning(f"HF login failed: {e}")
    else:
        logger.warning("No HF_TOKEN set — gated models will fail")

    # Detect pipeline class from model_index.json
    model_index = Path(model_path) / "model_index.json"
    pipeline_cls = None
    cls_name_from_index = ""
    if model_index.exists():
        try:
            idx = json.loads(model_index.read_text(encoding="utf-8"))
            cls_name_from_index = idx.get("_class_name", "")
            if hasattr(diffusers, cls_name_from_index):
                pipeline_cls = getattr(diffusers, cls_name_from_index)
                logger.info(f"Detected pipeline class: {cls_name_from_index}")
            else:
                logger.warning(f"model_index.json says {cls_name_from_index} but not in diffusers")
        except Exception as e:
            logger.warning(f"Could not parse model_index.json: {e}")

    # Build candidate list: detected class first, then DiffusionPipeline (auto-detect from model_index.json)
    # Only try Flux-specific pipelines if model name suggests Flux
    candidates = []
    if pipeline_cls:
        candidates.append((pipeline_cls, pipeline_cls.__name__))
    # DiffusionPipeline reads model_index.json and auto-selects the right pipeline
    candidates.append((diffusers.DiffusionPipeline, "DiffusionPipeline"))
    # Flux-specific fallbacks only if model name hints at Flux
    _model_lower = Path(model_path).name.lower()
    if "flux" in _model_lower:
        for name in ("Flux2Pipeline", "FluxPipeline"):
            cls = getattr(diffusers, name, None)
            if cls and cls not in [c for c, _ in candidates]:
                candidates.append((cls, name))

    def _cleanup():
        import gc; gc.collect()
        try:
            torch.cuda.empty_cache()
            logger.debug("GPU cache cleared")
        except Exception as e:
            logger.debug(f"GPU cache clear failed: {e}")

    def _load_pipe(cls, name):
        """Try loading pipeline, handling meta tensor issues."""
        global _pipe

        # First try normal load
        try:
            _pipe = cls.from_pretrained(model_path, torch_dtype=torch_dtype)
        except Exception as e:
            logger.warning(f"{name} from_pretrained failed: {e}")
            _pipe = None
            _cleanup()
            return False

        # Materialize any meta tensors before moving to device
        _fix_meta_tensors(_pipe, torch_dtype)

        if use_offload:
            try:
                _pipe.enable_model_cpu_offload()
                logger.info(f"Loaded as {name} with CPU offload")
                return True
            except Exception as e:
                logger.warning(f"{name} + cpu_offload failed: {e}")
                _pipe = None
                _cleanup()
                return False

        # Try full CUDA
        try:
            _pipe = _pipe.to("cuda")
            logger.info(f"Loaded as {name} on CUDA")
            return True
        except Exception as e:
            logger.warning(f"{name} + .to(cuda) failed: {e}")
            _pipe = None
            _cleanup()

        if not use_offload:
            logger.error(f"{name} doesn't fit in VRAM. Use --cpu-offload to enable offloading.")
            return False

        # OOM — reload and try with CPU offload
        try:
            logger.info(f"Reloading {name} with CPU offload...")
            _pipe = cls.from_pretrained(model_path, torch_dtype=torch_dtype)
            _fix_meta_tensors(_pipe, torch_dtype)
            _pipe.enable_model_cpu_offload()
            logger.info(f"Loaded as {name} with CPU offload")
            return True
        except Exception as e:
            logger.warning(f"{name} + cpu_offload reload failed: {e}")
            _pipe = None
            _cleanup()

        # Last resort — sequential offload
        try:
            logger.info(f"Reloading {name} with sequential CPU offload...")
            _pipe = cls.from_pretrained(model_path, torch_dtype=torch_dtype)
            _fix_meta_tensors(_pipe, torch_dtype)
            _pipe.enable_sequential_cpu_offload()
            logger.info(f"Loaded as {name} with sequential CPU offload")
            return True
        except Exception as e:
            logger.warning(f"{name} + sequential offload failed: {e}")
            _pipe = None
            _cleanup()

        return False

    loaded = False
    for cls, name in candidates:
        if _load_pipe(cls, name):
            loaded = True
            break

    # Last resort: override unknown pipeline class
    if not loaded and cls_name_from_index and not hasattr(diffusers, cls_name_from_index):
        for fallback in ("Flux2Pipeline", "FluxPipeline", "StableDiffusionPipeline"):
            fb_cls = getattr(diffusers, fallback, None)
            if fb_cls and fb_cls not in [c for c, _ in candidates]:
                logger.info(f"Overriding {cls_name_from_index} -> {fallback}")
                if _load_pipe(fb_cls, fallback):
                    loaded = True
                    break

    # Last resort: try from_single_file for raw safetensors / ckpt models
    if not loaded:
        # Find the single-file weight (safetensors preferred, then ckpt/bin)
        single_file = None
        from huggingface_hub import hf_hub_download, list_repo_files
        # Check if it's a HF repo with a single safetensors file
        try:
            files = list_repo_files(model_path)
            sf_files = [f for f in files if f.endswith('.safetensors') and '/' not in f]
            ckpt_files = [f for f in files if f.endswith(('.ckpt', '.bin')) and '/' not in f]
            target = sf_files[0] if sf_files else (ckpt_files[0] if ckpt_files else None)
            if target:
                logger.info(f"Downloading single file: {target}")
                single_file = hf_hub_download(model_path, target)
        except Exception as e:
            logger.warning(f"Could not list repo files for single-file fallback: {e}")
        # Also check local path
        if not single_file:
            local_path = Path(model_path)
            if local_path.is_dir():
                for ext in ('.safetensors', '.ckpt', '.bin'):
                    matches = list(local_path.glob(f'*{ext}'))
                    if matches:
                        single_file = str(matches[0])
                        break
            elif local_path.is_file():
                single_file = str(local_path)

        if single_file:
            logger.info(f"Trying from_single_file with: {single_file}")
            # Detect model family from path/filename to prioritize the right pipeline + config
            _path_lower = (model_path + "/" + (single_file or "")).lower()
            _SD35_CONFIGS = ["stabilityai/stable-diffusion-3.5-large", "stabilityai/stable-diffusion-3.5-medium"]
            _SD3_CONFIGS = ["stabilityai/stable-diffusion-3-medium-diffusers"]
            _FLUX2_CONFIGS = ["black-forest-labs/FLUX.2-dev"]
            _FLUX_CONFIGS = ["black-forest-labs/FLUX.1-schnell", "black-forest-labs/FLUX.1-dev"]
            _SDXL_CONFIGS = ["stabilityai/stable-diffusion-xl-base-1.0"]

            # Build ordered pipeline candidates based on model name hints
            _pipeline_configs = []
            if "sd3.5" in _path_lower or "stable-diffusion-3.5" in _path_lower:
                _pipeline_configs.append(("StableDiffusion3Pipeline", _SD35_CONFIGS))
            elif "sd3" in _path_lower or "stable-diffusion-3" in _path_lower:
                _pipeline_configs.append(("StableDiffusion3Pipeline", _SD3_CONFIGS + _SD35_CONFIGS))
            elif "flux.2" in _path_lower or "flux2" in _path_lower:
                _pipeline_configs.append(("Flux2Pipeline", _FLUX2_CONFIGS))
                _pipeline_configs.append(("FluxPipeline", _FLUX_CONFIGS))
            elif "flux" in _path_lower:
                _pipeline_configs.append(("FluxPipeline", _FLUX_CONFIGS))
                _pipeline_configs.append(("Flux2Pipeline", _FLUX2_CONFIGS))
            elif "sdxl" in _path_lower or "xl" in _path_lower:
                _pipeline_configs.append(("StableDiffusionXLPipeline", _SDXL_CONFIGS))
            # Always add all pipelines as fallbacks
            _pipeline_configs.extend([
                ("Flux2Pipeline", _FLUX2_CONFIGS),
                ("StableDiffusion3Pipeline", _SD35_CONFIGS + _SD3_CONFIGS),
                ("FluxPipeline", _FLUX_CONFIGS),
                ("StableDiffusionXLPipeline", _SDXL_CONFIGS + [None]),
                ("StableDiffusionPipeline", [None]),
            ])
            # Deduplicate while preserving order
            _seen = set()
            _deduped = []
            for item in _pipeline_configs:
                if item[0] not in _seen:
                    _seen.add(item[0])
                    _deduped.append(item)
            _pipeline_configs = _deduped
            # Pre-download config files (json/txt only) so from_single_file doesn't choke
            def _ensure_config_local(repo_id):
                """Download only config files from a repo, return local path or None."""
                try:
                    from huggingface_hub import snapshot_download
                    local = snapshot_download(
                        repo_id,
                        allow_patterns=["*.json", "*.txt", "**/*.json", "**/*.txt"],
                        ignore_patterns=["*.safetensors", "*.bin", "*.ckpt", "*.pt", "*.msgpack", "*.h5", "*.onnx", "*.png", "*.jpg", "*.md"],
                        token=_hf_token,
                        local_files_only=False,
                    )
                    logger.info(f"Config files cached for {repo_id} at {local}")
                    return local
                except Exception as e1:
                    logger.warning(f"Could not download configs from {repo_id}: {e1}")
                    # Try without allow_patterns (some hf_hub versions have bugs with filters on gated repos)
                    try:
                        from huggingface_hub import snapshot_download as _sd2
                        local = _sd2(
                            repo_id,
                            ignore_patterns=["*.safetensors", "*.bin", "*.ckpt", "*.pt", "*.msgpack", "*.h5", "*.onnx"],
                            token=_hf_token,
                            local_files_only=False,
                        )
                        logger.info(f"Config files cached (no filter) for {repo_id} at {local}")
                        return local
                    except Exception as e2:
                        logger.warning(f"Retry without allow_patterns also failed for {repo_id}: {e2}")
                        return None

            for cls_name, configs in _pipeline_configs:
                if loaded:
                    break
                cls = getattr(diffusers, cls_name, None)
                if not cls or not hasattr(cls, 'from_single_file'):
                    continue
                for config in configs:
                    try:
                        kwargs = {"torch_dtype": torch_dtype}
                        if config:
                            # Use local path instead of repo ID so diffusers doesn't re-download
                            local_config = _ensure_config_local(config)
                            if not local_config:
                                continue
                            kwargs["config"] = local_config
                            logger.info(f"Trying {cls_name}.from_single_file with config={config}")
                        _pipe = cls.from_single_file(single_file, **kwargs)
                        _fix_meta_tensors(_pipe, torch_dtype)
                        if use_offload:
                            _pipe.enable_model_cpu_offload()
                            logger.info(f"Loaded as {cls_name} (single file, config={config}) with CPU offload")
                        else:
                            _pipe = _pipe.to("cuda")
                            logger.info(f"Loaded as {cls_name} (single file, config={config}) on CUDA")
                        loaded = True
                        break
                    except Exception as e:
                        logger.warning(f"{cls_name}.from_single_file (config={config}) failed: {e}")
                        _pipe = None
                        _cleanup()

    if not loaded:
        raise RuntimeError(f"Could not load model from {model_path}. Check diffusers version and model format.")

    # Memory optimizations
    if _args.attention_slicing:
        try:
            _pipe.enable_attention_slicing()
            logger.info("Attention slicing enabled")
        except Exception:
            pass
    if _args.vae_slicing:
        try:
            _pipe.enable_vae_slicing()
            logger.info("VAE slicing enabled")
        except Exception:
            pass

    logger.info(f"Model loaded: {_model_id}")

    # Load LoRA weights if specified
    if _args.lora:
        for lora_path in _args.lora.split(','):
            lora_path = lora_path.strip()
            if not lora_path:
                continue
            try:
                lora_name = Path(lora_path).stem
                _pipe.load_lora_weights(lora_path, adapter_name=lora_name)
                logger.info(f"Loaded LoRA: {lora_name} from {lora_path}")
            except Exception as e:
                logger.warning(f"Failed to load LoRA {lora_path}: {e}")
        # Set LoRA scale
        try:
            _pipe.set_adapters([Path(p.strip()).stem for p in _args.lora.split(',') if p.strip()],
                              adapter_weights=[_args.lora_scale] * len([p for p in _args.lora.split(',') if p.strip()]))
            logger.info(f"LoRA scale set to {_args.lora_scale}")
        except Exception as e:
            logger.debug(f"Could not set adapter weights: {e}")


@app.get("/v1/models")
def list_models():
    return {
        "data": [
            {
                "id": _model_id,
                "object": "model",
                "owned_by": "local",
            }
        ]
    }


@app.post("/v1/images/generations")
def generate_image(req: ImageRequest):
    if _pipe is None:
        return {"error": "Model not loaded"}

    # Parse size
    try:
        w, h = req.size.split("x")
        width, height = int(w), int(h)
    except Exception:
        width, height = _args.width, _args.height

    # Map quality to num_inference_steps
    default_steps = _args.steps or 8
    steps_map = {"low": 4, "medium": default_steps, "high": 20, "auto": 12}
    steps = steps_map.get(req.quality, default_steps)

    logger.info(f"Generating: {req.prompt[:80]}... ({width}x{height}, {steps} steps)")
    start = time.time()

    # Detect if pipeline is inpaint-only (requires image + mask)
    _is_inpaint_pipe = 'inpaint' in type(_pipe).__name__.lower()

    images = []
    for _ in range(req.n):
        if _is_inpaint_pipe:
            # Inpaint pipelines need an image + mask — create blank ones for txt2img
            from PIL import Image as _PILGen
            _blank = _PILGen.new('RGB', (width, height), (128, 128, 128))
            _mask = _PILGen.new('L', (width, height), 255)  # full white = regenerate everything
            result = _pipe(
                prompt=req.prompt,
                image=_blank,
                mask_image=_mask,
                width=width,
                height=height,
                num_inference_steps=steps,
                guidance_scale=3.5,
            )
        else:
            result = _pipe(
                prompt=req.prompt,
                width=width,
                height=height,
                num_inference_steps=steps,
                guidance_scale=3.5,
            )
        img = result.images[0]

        # Convert to base64
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        images.append({"b64_json": b64})

    elapsed = time.time() - start
    logger.info(f"Generated {req.n} image(s) in {elapsed:.1f}s")

    return {
        "created": int(time.time()),
        "data": images,
    }


class InpaintRequest(BaseModel):
    image: str  # base64 PNG
    mask: str   # base64 PNG (white = inpaint area)
    prompt: str
    width: int = 0
    height: int = 0
    steps: int = 0
    strength: float = 0.75  # how much to change (0=nothing, 1=full regeneration)
    feather: int = 8  # mask edge feathering in pixels


_inpaint_pipe = None
_img2img_pipe = None

def _get_inpaint_pipe():
    """Lazy-load an inpaint or img2img pipeline from the same model."""
    global _inpaint_pipe, _img2img_pipe
    if _inpaint_pipe:
        return _inpaint_pipe, 'inpaint'
    if _img2img_pipe:
        return _img2img_pipe, 'img2img'

    import diffusers
    model_path = _args.model
    torch_dtype = DTYPE_MAP.get(_args.dtype, torch.bfloat16)

    # Check if the main pipeline IS already an inpaint pipeline
    pipe_cls_name = type(_pipe).__name__
    if 'inpaint' in pipe_cls_name.lower():
        _inpaint_pipe = _pipe
        logger.info(f"Main pipeline is already inpaint: {pipe_cls_name}")
        # Also try to get img2img from it
        try:
            img2img_cls_name = pipe_cls_name.replace('Inpaint', 'Img2Img')
            img2img_cls = getattr(diffusers, img2img_cls_name, None)
            if img2img_cls:
                _img2img_pipe = img2img_cls.from_pipe(_pipe)
                logger.info(f"Also loaded img2img from inpaint pipe: {img2img_cls_name}")
        except Exception as e:
            logger.debug(f"Could not create img2img from inpaint: {e}")
        return _inpaint_pipe, 'inpaint'

    # Try loading a dedicated inpaint pipeline from the same components
    inpaint_names = [
        pipe_cls_name.replace('Pipeline', 'InpaintPipeline'),
        'StableDiffusion3InpaintPipeline',
        'StableDiffusionXLInpaintPipeline',
        'StableDiffusionInpaintPipeline',
    ]
    for name in inpaint_names:
        cls = getattr(diffusers, name, None)
        if cls:
            try:
                _inpaint_pipe = cls.from_pipe(_pipe)
                logger.info(f"Loaded inpaint pipeline: {name}")
                return _inpaint_pipe, 'inpaint'
            except Exception as e:
                logger.debug(f"{name} from_pipe failed: {e}")

    # Try img2img pipeline
    img2img_names = [
        pipe_cls_name.replace('Pipeline', 'Img2ImgPipeline'),
        'StableDiffusion3Img2ImgPipeline',
        'StableDiffusionXLImg2ImgPipeline',
        'StableDiffusionImg2ImgPipeline',
    ]
    torch_dtype = DTYPE_MAP.get(_args.dtype, torch.bfloat16)
    harmonize_gpu = _args.harmonize_gpu
    for name in img2img_names:
        cls = getattr(diffusers, name, None)
        if cls:
            try:
                if harmonize_gpu is not None:
                    # Load fresh on separate GPU
                    logger.info(f"Loading {name} on cuda:{harmonize_gpu}...")
                    _img2img_pipe = cls.from_pretrained(_args.model, torch_dtype=torch_dtype)
                    _img2img_pipe = _img2img_pipe.to(f"cuda:{harmonize_gpu}")
                else:
                    _img2img_pipe = cls.from_pipe(_pipe, torch_dtype=torch_dtype)
                logger.info(f"Loaded img2img pipeline: {name}" + (f" on cuda:{harmonize_gpu}" if harmonize_gpu is not None else ""))
                return _img2img_pipe, 'img2img'
            except Exception as e:
                logger.debug(f"{name} failed: {e}")
                try:
                    # Some pipelines need from_pretrained instead of from_pipe
                    _img2img_pipe = cls.from_pretrained(_args.model, torch_dtype=torch_dtype)
                    if _args.cpu_offload:
                        _img2img_pipe.enable_model_cpu_offload()
                    else:
                        _img2img_pipe = _img2img_pipe.to("cuda")
                    logger.info(f"Loaded img2img pipeline (from_pretrained): {name}")
                    return _img2img_pipe, 'img2img'
                except Exception as e2:
                    logger.debug(f"{name} from_pretrained also failed: {e2}")

    logger.warning("No inpaint or img2img pipeline available — will use txt2img fallback")
    return None, None


@app.post("/v1/images/inpaint")
def inpaint_image(req: InpaintRequest):
    """Inpaint masked region. Tries: native inpaint → img2img+composite → txt2img+composite."""
    if _pipe is None:
        return {"error": "Model not loaded"}

    from PIL import Image as PILImage

    # Decode input image and mask
    img_bytes = base64.b64decode(req.image)
    mask_bytes = base64.b64decode(req.mask)
    init_image = PILImage.open(io.BytesIO(img_bytes)).convert("RGB")
    mask_image = PILImage.open(io.BytesIO(mask_bytes)).convert("L")

    # Feather value — applied after cropping to avoid edge clipping
    feather = max(0, min(60, req.feather))

    width = req.width or init_image.width
    height = req.height or init_image.height

    default_steps = _args.steps or 12
    steps = req.steps or default_steps

    logger.info(f"Inpainting: {req.prompt[:80]}... ({width}x{height}, {steps} steps)")
    start = time.time()

    strength = max(0.1, min(1.0, req.strength))

    # Try to get a dedicated inpaint or img2img pipeline
    alt_pipe, alt_type = _get_inpaint_pipe()

    # SDXL inpaint expects ~1024 on the short side. Running at canvas
    # native resolution can produce grey / muted output when the model's
    # latent grid is far larger than what it was trained on. Cap to a
    # model-friendly box (multiples of 8), inpaint there, upscale back.
    max_side = 1024
    scale = min(max_side / max(width, height), 1.0)
    work_w = max(64, ((int(width  * scale) + 7) // 8) * 8)
    work_h = max(64, ((int(height * scale) + 7) // 8) * 8)
    work_init = init_image.resize((work_w, work_h), PILImage.LANCZOS)
    work_mask = mask_image.resize((work_w, work_h), PILImage.BILINEAR)
    logger.info(f"Inpaint working size: {work_w}x{work_h} (from {width}x{height})")

    # SDXL VAE in fp16/bfloat16 commonly produces NaN/overflow that
    # decodes to flat grey output. Upcast the VAE to fp32 before the
    # call; cheap (only the VAE decode runs in fp32, the heavy UNet
    # stays in the requested dtype). One-time per pipeline.
    if alt_pipe is not None and not getattr(alt_pipe, '_ge_vae_upcast', False):
        try:
            alt_pipe.upcast_vae()
            alt_pipe._ge_vae_upcast = True
            logger.info("Upcast VAE to fp32 to avoid grey-output bug")
        except Exception as e:
            logger.warning(f"Could not upcast VAE: {e}")

    try:
        if alt_type == 'inpaint' and alt_pipe:
            # Use dedicated inpaint pipeline. guidance_scale 7.5 is the
            # SDXL default — the previous 3.5 was producing muted / grey
            # results, especially on style-transfer prompts with large
            # masks.
            logger.info("Using dedicated inpaint pipeline")
            result = alt_pipe(
                prompt=req.prompt,
                image=work_init,
                mask_image=work_mask,
                width=work_w,
                height=work_h,
                num_inference_steps=steps,
                strength=strength,
                guidance_scale=7.5,
            )
        elif alt_type == 'img2img' and alt_pipe:
            raise TypeError("Skip to img2img fallback")
        else:
            # Try the main pipeline with inpaint args
            result = _pipe(
                prompt=req.prompt,
                image=work_init,
                mask_image=work_mask,
                width=work_w,
                height=work_h,
                num_inference_steps=steps,
                strength=strength,
                guidance_scale=7.5,
            )
    except TypeError:
        # Pipeline doesn't support native inpainting — use crop-to-mask + img2img + composite
        # This preserves context by only regenerating the masked region with surrounding padding
        import numpy as np
        logger.info(f"Pipeline doesn't support inpainting — using crop+img2img (strength={strength}) + composite")

        mask_resized = mask_image.resize((width, height))
        init_resized = init_image.resize((width, height))
        mask_arr = np.array(mask_resized)

        # Find bounding box of the mask
        ys, xs = np.where(mask_arr > 10)
        if len(xs) == 0 or len(ys) == 0:
            logger.warning("Empty mask — returning original image")
            buf = io.BytesIO()
            init_resized.save(buf, format="PNG")
            return {"image": base64.b64encode(buf.getvalue()).decode(), "elapsed": 0}

        x1, y1, x2, y2 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())

        # Add generous padding (50% of mask size, min 64px) so model sees surrounding context
        pad_x = max(64, int((x2 - x1) * 0.5))
        pad_y = max(64, int((y2 - y1) * 0.5))
        cx1 = max(0, x1 - pad_x)
        cy1 = max(0, y1 - pad_y)
        cx2 = min(width, x2 + pad_x)
        cy2 = min(height, y2 + pad_y)

        # Make crop square and round to multiple of 64 (SD3 VAE requirement)
        crop_size = max(cx2 - cx1, cy2 - cy1)
        crop_size = max(256, ((crop_size + 63) // 64) * 64)  # min 256, round up to 64
        # Center the square crop on the mask center
        cx_mid = (cx1 + cx2) // 2
        cy_mid = (cy1 + cy2) // 2
        cx1 = max(0, cx_mid - crop_size // 2)
        cy1 = max(0, cy_mid - crop_size // 2)
        cx2 = min(width, cx1 + crop_size)
        cy2 = min(height, cy1 + crop_size)
        # Adjust if we hit image edges
        if cx2 - cx1 < crop_size:
            cx1 = max(0, cx2 - crop_size)
        if cy2 - cy1 < crop_size:
            cy1 = max(0, cy2 - crop_size)
        cw = cx2 - cx1
        ch = cy2 - cy1

        logger.info(f"Mask bbox: ({x1},{y1})-({x2},{y2}), crop region: ({cx1},{cy1})-({cx2},{cy2}) = {cw}x{ch}")

        # Crop the original image and mask to the region
        crop_img = init_resized.crop((cx1, cy1, cx2, cy2))
        crop_mask = mask_resized.crop((cx1, cy1, cx2, cy2))

        # Use img2img pipeline if available, otherwise fall back
        _i2i_pipe = alt_pipe if alt_type == 'img2img' else None
        # Ensure crop image is properly sized (multiple of 8)
        crop_img = crop_img.resize((cw, ch))
        try:
            if _i2i_pipe:
                logger.info(f"Using img2img pipeline on crop ({cw}x{ch})")
                result = _i2i_pipe(
                    prompt=req.prompt,
                    image=crop_img,
                    num_inference_steps=steps,
                    strength=strength,
                    guidance_scale=7.0,
                )
            else:
                # Try main pipeline with image arg
                result = _pipe(
                    prompt=req.prompt,
                    image=crop_img,
                    num_inference_steps=steps,
                    strength=strength,
                    guidance_scale=3.5,
                )
            generated_crop = result.images[0].resize((cw, ch))
        except TypeError:
            # No img2img support at all — txt2img on crop size
            logger.info("No img2img support — txt2img on crop region")
            result = _pipe(
                prompt=req.prompt,
                width=cw,
                height=ch,
                num_inference_steps=steps,
                guidance_scale=3.5,
            )
            generated_crop = result.images[0].resize((cw, ch))

        # Apply feathering to the cropped mask for soft blending edges
        if feather > 0:
            from PIL import ImageFilter
            # PIL GaussianBlur radius is ~half of CSS blur pixels, so multiply
            blur_radius = feather * 1.5
            crop_mask = crop_mask.filter(ImageFilter.GaussianBlur(radius=blur_radius))
            logger.info(f"Applied {feather}px feather (PIL radius={blur_radius:.0f}) to crop mask")

        # Composite: blend generated crop into original using the feathered mask
        orig_arr = np.array(init_resized).astype(float)
        gen_full = orig_arr.copy()
        crop_gen_arr = np.array(generated_crop).astype(float)
        crop_mask_arr = np.array(crop_mask) / 255.0

        # Blend only in the crop region
        region = gen_full[cy1:cy2, cx1:cx2]
        blended_region = region * (1 - crop_mask_arr[:, :, None]) + crop_gen_arr * crop_mask_arr[:, :, None]
        gen_full[cy1:cy2, cx1:cx2] = blended_region

        result_img = PILImage.fromarray(gen_full.astype(np.uint8))

        buf = io.BytesIO()
        result_img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        elapsed = time.time() - start
        logger.info(f"Inpaint (crop+composite) done in {elapsed:.1f}s")
        return {"image": b64, "elapsed": round(elapsed, 2)}

    img = result.images[0]
    # Upscale back to the canvas size if we worked at a smaller resolution.
    if (img.width, img.height) != (width, height):
        img = img.resize((width, height), PILImage.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    elapsed = time.time() - start
    logger.info(f"Inpaint done in {elapsed:.1f}s")
    return {"image": b64, "elapsed": round(elapsed, 2)}


class HarmonizeRequest(BaseModel):
    image: str  # base64 PNG
    prompt: str
    # Two-stage harmonize:
    #   1) Reinhard color transfer inside `body_mask` (matches L*a*b* mean/std
    #      of the masked region to the unmasked surroundings). Pixel-sharp.
    #   2) Optional narrow inpaint on `seam_mask` (alpha edge band) to fix
    #      jagged cutouts and seams. Only the edge band is regenerated.
    color_match: float = 0.65  # 0..1 — how much of the color shift to apply
    seam_fix: float = 0.0      # 0..1 — strength of the seam inpaint pass
    body_mask: str | None = None  # base64 PNG, white = layer body
    seam_mask: str | None = None  # base64 PNG, white = layer alpha edge band
    steps: int = 0
    # Legacy fields (older clients): if `mask` is sent without body/seam,
    # we treat it as body_mask. `strength` maps to color_match.
    mask: str | None = None
    strength: float | None = None
    max_side: int = 1024


def _rgb_to_lalphabeta(rgb_f):
    """RGB → L*alpha*beta (Ruderman et al., the colour space Reinhard's
    original paper used). Pure numpy — no cv2. Input/output: float32 arrays
    of shape (..., 3); input in 0..255, output unbounded log-RGB-style."""
    import numpy as np
    eps = 1.0
    # Linearise to LMS cone space
    M_rgb2lms = np.array([
        [0.3811, 0.5783, 0.0402],
        [0.1967, 0.7244, 0.0782],
        [0.0241, 0.1288, 0.8444],
    ], dtype=np.float32)
    lms = rgb_f @ M_rgb2lms.T
    lms = np.log(np.maximum(lms, eps))
    # LMS → L*alpha*beta
    M_lms2lab = np.array([
        [1.0/np.sqrt(3),  1.0/np.sqrt(3),  1.0/np.sqrt(3)],
        [1.0/np.sqrt(6),  1.0/np.sqrt(6), -2.0/np.sqrt(6)],
        [1.0/np.sqrt(2), -1.0/np.sqrt(2),  0.0          ],
    ], dtype=np.float32)
    return lms @ M_lms2lab.T


def _lalphabeta_to_rgb(lab):
    """Inverse of _rgb_to_lalphabeta. Returns RGB float32 in 0..255 (clipped)."""
    import numpy as np
    M_lab2lms = np.array([
        [np.sqrt(3)/3.0,  np.sqrt(6)/6.0,  np.sqrt(2)/2.0],
        [np.sqrt(3)/3.0,  np.sqrt(6)/6.0, -np.sqrt(2)/2.0],
        [np.sqrt(3)/3.0, -np.sqrt(6)/3.0,  0.0          ],
    ], dtype=np.float32)
    lms = lab @ M_lab2lms.T
    lms = np.exp(lms)
    M_lms2rgb = np.array([
        [ 4.4679, -3.5873,  0.1193],
        [-1.2186,  2.3809, -0.1624],
        [ 0.0497, -0.2439,  1.2045],
    ], dtype=np.float32)
    rgb = lms @ M_lms2rgb.T
    return np.clip(rgb, 0, 255)


def _reinhard_color_transfer(source_rgb, body_mask_l, blend: float = 1.0):
    """Match the masked region's color statistics to the unmasked
    surroundings using Reinhard's L*alpha*beta transfer. Pure numpy.

    `blend` (0..1) controls how much of the shift to apply.
    """
    import numpy as np
    from PIL import Image as _PILImg

    src_np = np.asarray(source_rgb).astype(np.float32)  # H,W,3 in 0..255
    h, w, _ = src_np.shape

    mask_np = np.asarray(body_mask_l).astype(np.float32) / 255.0
    if mask_np.shape != (h, w):
        return source_rgb

    interior = mask_np > 0.5
    exterior = mask_np < 0.05
    if interior.sum() < 100 or exterior.sum() < 100:
        return source_rgb

    lab = _rgb_to_lalphabeta(src_np)
    in_pix = lab[interior]
    out_pix = lab[exterior]

    in_mean, in_std = in_pix.mean(axis=0), in_pix.std(axis=0) + 1e-6
    out_mean, out_std = out_pix.mean(axis=0), out_pix.std(axis=0) + 1e-6

    shifted = lab.copy()
    shifted[interior] = (lab[interior] - in_mean) * (out_std / in_std) + out_mean
    rgb_shifted = _lalphabeta_to_rgb(shifted)

    # Lerp source ↔ shifted, weighted by mask × blend so the edge of the
    # mask fades back to source smoothly.
    m3 = (mask_np * blend)[..., None]
    out = src_np * (1 - m3) + rgb_shifted * m3
    return _PILImg.fromarray(np.clip(out, 0, 255).astype(np.uint8), mode='RGB')


def _decode_mask_b64(b64_str, target_size):
    """Decode a base64-encoded grayscale PNG. Returns PIL 'L' image at
    `target_size`, or None if empty/invalid."""
    if not b64_str:
        return None
    try:
        from PIL import Image as _PILImg
        m = _PILImg.open(io.BytesIO(base64.b64decode(b64_str))).convert("L")
        if m.size != target_size:
            m = m.resize(target_size, _PILImg.BILINEAR)
        if not m.getbbox():
            return None
        return m
    except Exception as e:
        logger.warning(f"Harmonize: bad mask: {e}")
        return None


@app.post("/v1/images/harmonize")
def harmonize_image(req: HarmonizeRequest):
    """Two-stage layer harmonization.

    Stage 1 — Reinhard color transfer inside `body_mask`: matches the
    masked region's L*a*b* mean/std to the unmasked surroundings. Pixel-
    sharp, no model regen. Controlled by `color_match` (0..1).

    Stage 2 — Optional narrow inpaint on `seam_mask` (alpha edge band):
    only the band is regenerated; layer interiors stay identical to the
    color-shifted result. Controlled by `seam_fix` (0..1). Skipped if
    `seam_fix=0` or no inpaint pipeline is available.

    Backwards compat: if only `mask` is provided (no body/seam), it's
    treated as body_mask. `strength` (old field) maps to `color_match`.
    """
    if _pipe is None:
        return {"error": "Model not loaded"}

    from PIL import Image as PILImage

    img_bytes = base64.b64decode(req.image)
    source_full = PILImage.open(io.BytesIO(img_bytes)).convert("RGB")
    orig_w, orig_h = source_full.size

    # Resolve old-vs-new field names.
    body_b64 = req.body_mask or req.mask
    seam_b64 = req.seam_mask
    color_match = req.color_match
    if req.strength is not None:
        color_match = req.strength
    color_match = max(0.0, min(1.0, color_match))
    seam_fix = max(0.0, min(1.0, req.seam_fix))

    body_mask_full = _decode_mask_b64(body_b64, (orig_w, orig_h))
    seam_mask_full = _decode_mask_b64(seam_b64, (orig_w, orig_h))

    # If neither mask was supplied: legacy whole-image fallback. The user
    # didn't tell us where the seams are, so we can't do targeted blending.
    if body_mask_full is None and seam_mask_full is None:
        logger.info("Harmonize: no masks — falling back to legacy whole-image path")
        return _legacy_whole_image_harmonize(req, source_full)

    logger.info(
        f"Harmonize: color_match={color_match:.2f} seam_fix={seam_fix:.2f} "
        f"body_mask={'y' if body_mask_full else 'n'} seam_mask={'y' if seam_mask_full else 'n'}"
    )
    start = time.time()

    # ── Stage 1: Reinhard color transfer (pixel-sharp, no regen) ──
    if body_mask_full is not None and color_match > 0.01:
        try:
            stage1 = _reinhard_color_transfer(source_full, body_mask_full, blend=color_match)
        except Exception as e:
            logger.warning(f"Harmonize stage 1 failed, skipping: {e}")
            stage1 = source_full
    else:
        stage1 = source_full

    # ── Stage 2: narrow seam inpaint (only the alpha edge band) ──
    final = stage1
    if seam_mask_full is not None and seam_fix > 0.01:
        alt_pipe, alt_type = _get_inpaint_pipe()
        is_inpaint_main = 'inpaint' in type(_pipe).__name__.lower()
        inpaint_pipe = alt_pipe if alt_type == 'inpaint' else (_pipe if is_inpaint_main else None)
        if inpaint_pipe is None:
            logger.info("Harmonize: seam_fix requested but no inpaint pipe — skipping stage 2")
        else:
            try:
                max_side = req.max_side or 1024
                scale = min(max_side / orig_w, max_side / orig_h, 1.0)
                w = ((int(orig_w * scale) + 63) // 64) * 64
                h = ((int(orig_h * scale) + 63) // 64) * 64
                init_small = stage1.resize((w, h), PILImage.LANCZOS)
                seam_small = seam_mask_full.resize((w, h), PILImage.BILINEAR)
                # Cap the inpaint strength — seam_fix=1.0 → strength=0.50,
                # so even max setting can't fully redraw the band.
                inpaint_strength = max(0.10, min(0.50, seam_fix * 0.50))
                steps = req.steps or (_args.steps or 12)
                logger.info(f"Harmonize stage 2: seam inpaint at {w}x{h}, strength={inpaint_strength:.2f}")
                result = inpaint_pipe(
                    prompt=req.prompt,
                    image=init_small,
                    mask_image=seam_small,
                    width=w,
                    height=h,
                    num_inference_steps=max(steps, 20),
                    strength=inpaint_strength,
                    guidance_scale=7.0,
                )
                ai_small = result.images[0]
                ai_full = ai_small.resize((orig_w, orig_h), PILImage.LANCZOS) if (w, h) != (orig_w, orig_h) else ai_small
                # Composite back using the seam mask as alpha — outside the
                # seam band stays pixel-identical to stage1.
                final = PILImage.composite(ai_full, stage1, seam_mask_full)
            except Exception as e:
                logger.warning(f"Harmonize stage 2 failed, returning stage 1 only: {e}")
                final = stage1

    buf = io.BytesIO()
    final.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    elapsed = time.time() - start
    logger.info(f"Harmonize done in {elapsed:.1f}s")
    return {"image": b64, "elapsed": round(elapsed, 2)}


def _legacy_whole_image_harmonize(req, source_full):
    """Old behaviour: no masks supplied → run img2img on the entire image.
    Kept for cases where the client wants a global re-render."""
    from PIL import Image as PILImage

    orig_w, orig_h = source_full.size
    max_side = req.max_side or 1024
    scale = min(max_side / orig_w, max_side / orig_h, 1.0)
    width = ((int(orig_w * scale) + 63) // 64) * 64
    height = ((int(orig_h * scale) + 63) // 64) * 64
    init_image = source_full.resize((width, height), PILImage.LANCZOS)
    steps = req.steps or (_args.steps or 12)
    strength = req.strength if req.strength is not None else 0.30
    strength = max(0.1, min(0.9, strength))

    alt_pipe, alt_type = _get_inpaint_pipe()
    i2i_pipe = _img2img_pipe if _img2img_pipe else (alt_pipe if alt_type == 'img2img' else None)

    start = time.time()
    try:
        if i2i_pipe:
            result = i2i_pipe(
                prompt=req.prompt, image=init_image,
                num_inference_steps=steps, strength=strength, guidance_scale=7.0,
            )
        else:
            result = _pipe(
                prompt=req.prompt, image=init_image,
                num_inference_steps=steps, strength=strength, guidance_scale=7.0,
            )
    except TypeError:
        result = _pipe(
            prompt=req.prompt, width=width, height=height,
            num_inference_steps=steps, guidance_scale=7.0,
        )

    img = result.images[0]
    if (orig_w, orig_h) != (width, height):
        img = img.resize((orig_w, orig_h), PILImage.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    elapsed = time.time() - start
    logger.info(f"Legacy harmonize done in {elapsed:.1f}s")
    return {"image": b64, "elapsed": round(elapsed, 2)}


@app.get("/health")
def health():
    return {"status": "ok", "model": _model_id}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="Path to diffusers model")
    parser.add_argument("--lora", type=str, default=None, help="Path to LoRA weights (.safetensors). Can specify multiple comma-separated.")
    parser.add_argument("--lora-scale", type=float, default=1.0, help="LoRA weight scale (0.0-2.0)")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--dtype", default="bfloat16", choices=["bfloat16", "float16", "float32"])
    parser.add_argument("--device-map", default=None, help="Device map strategy (unused, kept for compat)")
    parser.add_argument("--steps", type=int, default=0, help="Default inference steps (0=auto)")
    parser.add_argument("--width", type=int, default=1024, help="Default output width")
    parser.add_argument("--height", type=int, default=1024, help="Default output height")
    parser.add_argument("--cpu-offload", action="store_true", help="Enable model CPU offload")
    parser.add_argument("--attention-slicing", action="store_true", help="Enable attention slicing")
    parser.add_argument("--vae-slicing", action="store_true", help="Enable VAE slicing")
    parser.add_argument("--harmonize-gpu", type=int, default=None, help="GPU index for harmonize/img2img (default: same as main)")
    _args = parser.parse_args()

    app.state.model_path = _args.model
    uvicorn.run(app, host=_args.host, port=_args.port)
