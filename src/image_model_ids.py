"""Small helpers for recognizing image-generation model IDs."""

from __future__ import annotations

import math


_IMAGE_MODEL_PREFIXES = (
    "gpt-image",
    "dall-e",
    "chatgpt-image",
    "hidream",
    "qwen-image",
    "z-image",
    "flux",
    "stable-diffusion",
    "sdxl",
    "boogu",
    "krea-2",
)


def model_id_leaf(model_id: str) -> str:
    """Return the provider-stripped model id leaf in lowercase."""
    return str(model_id or "").strip().split("/")[-1].lower()


def image_edit_size(model_id: str, width: int, height: int) -> str:
    """Match source geometry within the fixed GPT Image 1 output sizes."""
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive")
    leaf = model_id_leaf(model_id)
    if leaf in {"gpt-image-1", "gpt-image-1-mini", "gpt-image-1.5",
                "gpt-5-image", "gpt-5-image-mini"}:
        sizes = ((1024, 1024), (1536, 1024), (1024, 1536))
        width, height = min(sizes, key=lambda candidate: (
            abs(math.log((candidate[0] / candidate[1]) / (width / height))),
            abs(candidate[0] * candidate[1] - width * height),
        ))
    return f"{width}x{height}"


def looks_like_image_generation_model(model_id: str) -> bool:
    """Return True when a model id should use image generation routes.

    API providers can namespace image models, e.g. ``openai/gpt-5-image``.
    Classify by the leaf so mixed endpoints can expose chat and image models
    without marking the whole endpoint as image-only.
    """
    mid = str(model_id or "").strip().lower()
    leaf = model_id_leaf(mid)
    if not leaf:
        return False
    if any(leaf.startswith(prefix) for prefix in _IMAGE_MODEL_PREFIXES):
        return True
    # Newer OpenAI image models use names like gpt-5-image instead of
    # gpt-image-1. Keep this pattern provider-agnostic.
    return (leaf.startswith("gpt-") and "-image" in leaf) or (
        leaf.startswith("gemini-") and "image" in leaf
    )
