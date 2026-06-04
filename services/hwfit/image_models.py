"""Image generation model registry and VRAM fitting for Cookbook."""

# Curated registry of image generation models supported by diffusers.
# ONLY verified HuggingFace repo IDs.
# VRAM estimates are for inference (single image generation).
IMAGE_MODEL_REGISTRY = [
    # ── Z-Image (Alibaba Tongyi) ──
    {
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
    },
    {
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
    },
    # ── Qwen Image ──
    {
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
    },
    {
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
    },
    {
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
    },
    # ── Stable Diffusion (dedicated inpainting) ──
    {
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
    },
    {
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
    },
    # ── FLUX ──
    {
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
    },
    {
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
    },
    # ── Stable Diffusion ──
    {
        "id": "stabilityai/stable-diffusion-3.5-medium",
        "name": "SD 3.5 Medium",
        "provider": "Stability AI",
        "params_b": 2.5,
        "vram_bf16": 12.0,
        "vram_fp8": 7.0,
        "vram_q4": None,
        "default_quant": "BF16",
        "quant_repos": {
            "FP8": "Comfy-Org/stable-diffusion-3.5-fp8",
        },
        "capabilities": ["text-to-image"],
        "description": "2.5B lightweight, fast. Fits almost any GPU.",
        "quality": 75,
        "speed": 95,
        "released": "2024-10",
    },
    {
        "id": "stabilityai/stable-diffusion-3.5-large",
        "name": "SD 3.5 Large",
        "provider": "Stability AI",
        "params_b": 8.1,
        "vram_bf16": 22.0,
        "vram_fp8": 12.0,
        "vram_q4": None,
        "default_quant": "BF16",
        "quant_repos": {
            "FP8": "Comfy-Org/stable-diffusion-3.5-fp8",
        },
        "capabilities": ["text-to-image"],
        "description": "8B high quality. Good balance of speed and quality.",
        "quality": 85,
        "speed": 70,
        "released": "2024-10",
    },
    {
        "id": "stabilityai/stable-diffusion-3.5-large-turbo",
        "name": "SD 3.5 Large Turbo",
        "provider": "Stability AI",
        "params_b": 8.1,
        "vram_bf16": 22.0,
        "vram_fp8": 12.0,
        "vram_q4": None,
        "default_quant": "BF16",
        "quant_repos": {
            "FP8": "Comfy-Org/stable-diffusion-3.5-fp8",
        },
        "capabilities": ["text-to-image"],
        "description": "Distilled for few-step inference. Fastest large SD.",
        "quality": 80,
        "speed": 92,
        "released": "2024-10",
    },
    {
        "id": "stabilityai/stable-diffusion-xl-base-1.0",
        "name": "SDXL",
        "provider": "Stability AI",
        "params_b": 3.5,
        "vram_bf16": 10.0,
        "vram_fp8": 6.0,
        "vram_q4": None,
        "default_quant": "BF16",
        "quant_repos": {},
        "capabilities": ["text-to-image"],
        "description": "Classic workhorse. Huge LoRA ecosystem. Fits 8GB+.",
        "quality": 72,
        "speed": 90,
        "released": "2023-07",
    },
    # ── Hunyuan ──
    {
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
    },
    {
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
    },
]


def get_image_models():
    """Return the image model registry."""
    return IMAGE_MODEL_REGISTRY


def rank_image_models(system, search=None, sort="fit"):
    """Score and rank image models against detected hardware.

    Returns list of models with fit info (vram needed, fits, recommended quant).
    """
    if not isinstance(system, dict):
        system = {}
    gpu_vram = system.get("gpu_vram_gb", 0) or 0
    has_gpu = system.get("has_gpu", False)
    results = []

    for model in IMAGE_MODEL_REGISTRY:
        # Filter by search
        if isinstance(search, str) and search:
            s = search.lower()
            if s not in model["name"].lower() and s not in model["id"].lower() and s not in model.get("description", "").lower():
                continue

        # Determine best quant that fits
        quant = None
        vram_needed = None
        fits = False
        quant_repo = None

        if has_gpu and gpu_vram > 0:
            # Try BF16 first, then FP8, then Q4
            for q, vram_key in [("BF16", "vram_bf16"), ("FP8", "vram_fp8"), ("Q4", "vram_q4")]:
                v = model.get(vram_key)
                if v is not None and v <= gpu_vram * 0.90:  # 10% headroom
                    quant = q
                    vram_needed = v
                    fits = True
                    quant_repo = model.get("quant_repos", {}).get(q)
                    break
            # If nothing fits, show what it needs
            if not fits:
                quant = model["default_quant"]
                vram_needed = model.get("vram_bf16", 0)

        # Fit label
        if not has_gpu:
            fit = "no_gpu"
            fit_label = "No GPU"
        elif fits:
            headroom = gpu_vram - vram_needed
            if headroom > gpu_vram * 0.3:
                fit = "perfect"
                fit_label = "Perfect"
            elif headroom > gpu_vram * 0.1:
                fit = "good"
                fit_label = "Good"
            else:
                fit = "tight"
                fit_label = "Tight"
        else:
            fit = "no_fit"
            fit_label = "Too large"

        # Score: quality * speed * fit bonus
        score = model["quality"] * 0.6 + model["speed"] * 0.2
        if fit == "perfect":
            score += 20
        elif fit == "good":
            score += 10
        elif fit == "tight":
            score += 5
        elif fit == "no_fit":
            score -= 30

        results.append({
            "id": model["id"],
            "name": model["name"],
            "provider": model["provider"],
            "params_b": model["params_b"],
            "vram_needed": vram_needed,
            "quant": quant,
            "quant_repo": quant_repo,
            "fits": fits,
            "fit": fit,
            "fit_label": fit_label,
            "quality": model["quality"],
            "speed": model["speed"],
            "score": round(score, 1),
            "capabilities": model["capabilities"],
            "description": model["description"],
            "released": model.get("released", ""),
        })

    # Sort
    if sort == "quality":
        results.sort(key=lambda x: (-x["quality"], -x["score"]))
    elif sort == "speed":
        results.sort(key=lambda x: (-x["speed"], -x["score"]))
    elif sort == "vram":
        results.sort(key=lambda x: (x["vram_needed"] or 999, -x["score"]))
    else:  # fit (default)
        results.sort(key=lambda x: (-x["score"],))

    return results
