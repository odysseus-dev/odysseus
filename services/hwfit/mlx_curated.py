"""Curated metadata for well-known mlx-community repos.

The HuggingFace API cannot supply parameter counts for MLX builds: the figure it
reports comes from safetensors.total, which counts PACKED tensors, so a 4-bit
repo reports roughly a quarter of its real parameter count (Qwen3-0.6B-4bit
reports ~93M for a 596M model). Everything hwfit computes — VRAM fit, speed,
score — hangs off the parameter count, so an uncorrected MLX row ranks as a much
smaller model than it is. There is no API to fix this with; the table in
data/mlx_curated_models.json is maintained by hand.
"""

import json
import os

_CURATED_PATH = os.path.join(os.path.dirname(__file__), "data", "mlx_curated_models.json")
_curated_cache = None

# Bytes per parameter for an MLX quant tier, matching services.hwfit.models.
_MLX_BPP = {3: 0.42, 4: 0.55, 5: 0.65, 6: 0.75, 8: 1.0}


def load_curated_mlx_models():
    """The curated table, keyed by exact repo id. Empty dict if unreadable."""
    global _curated_cache
    if _curated_cache is None:
        try:
            with open(_CURATED_PATH, encoding="utf-8") as f:
                loaded = json.load(f)
            models = loaded.get("models") if isinstance(loaded, dict) else None
            _curated_cache = {
                k: v for k, v in (models or {}).items() if isinstance(v, dict)
            }
        except (OSError, ValueError, AttributeError):
            _curated_cache = {}
    return _curated_cache


def reset_curated_cache():
    global _curated_cache
    _curated_cache = None


def curated_mlx_entry(repo_id):
    """Curated row for a repo id, or None when it isn't in the table."""
    if not repo_id or not isinstance(repo_id, str):
        return None
    return load_curated_mlx_models().get(repo_id.strip())


def apply_curated_mlx_metadata(model):
    """Overwrite a catalog row's size/quant/context from the curated table.

    A no-op for every repo that isn't curated, so the normal HF-derived path is
    untouched. Mutates and returns `model` (catalog rows are dicts built fresh
    per load, and the caller already treats them as owned).
    """
    if not isinstance(model, dict):
        return model
    curated = curated_mlx_entry(model.get("name"))
    if not curated:
        return model

    params_b = curated.get("params_b")
    bits = curated.get("bits")
    if params_b:
        model["parameters_raw"] = int(float(params_b) * 1_000_000_000)
        model["parameter_count"] = f"{float(params_b):.4g}B"
    if bits:
        model["quantization"] = f"mlx-{int(bits)}bit"
    if curated.get("context"):
        model["context_length"] = int(curated["context"])

    # Size the RAM budget off the bundle itself when we know it — a measured
    # download beats bytes-per-param arithmetic. Fall back to the tier's BPP.
    # `size_gb` is what the serve-profile path reads for model weights.
    weights_gb = curated.get("download_gb")
    if not weights_gb and params_b and bits:
        weights_gb = float(params_b) * _MLX_BPP.get(int(bits), 0.55)
    if weights_gb:
        model["min_ram_gb"] = round(float(weights_gb) + 0.8, 1)
        model["recommended_ram_gb"] = round(model["min_ram_gb"] * 1.3 + 0.5, 1)
        model["size_gb"] = round(float(weights_gb), 2)
    model["_mlx_curated"] = True
    return model
