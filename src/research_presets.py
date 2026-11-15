"""Deep Research parameter presets for small, medium, and large local systems."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

RESEARCH_PRESETS: dict[str, dict[str, Any]] = {
    "small": {
        "tier": "small",
        "label": "Small Local System (<= 8 GB VRAM / 16 GB RAM)",
        "description": (
            "Tuned for 3B-9B quantized models on single consumer GPUs or tight unified memory. "
            "Serializes page extraction and trims context chunks to prevent KV-cache thrashing."
        ),
        "hardware_profile": {
            "min_vram_gb": 4.0,
            "recommended_vram_gb": 8.0,
            "recommended_ram_gb": 16.0,
            "recommended_param_range_b": [3.0, 9.0],
            "recommended_quantization": "Q4_K_M / IQ4_XS",
            "recommended_context_length": 16384,
            "example_models": [
                "Qwen/Qwen3-8B",
                "google/gemma-4-E4B-it",
                "meta-llama/Llama-3.1-8B-Instruct",
            ],
        },
        "deep_researcher_params": {
            "max_rounds": 4,
            "min_rounds": 1,
            "max_empty_rounds": 1,
            "max_urls_per_round": 2,
            "extraction_concurrency": 1,
            "max_content_chars": 8000,
            "max_report_tokens": 4096,
            "synthesis_window": 6,
            "planning_timeout": 90,
            "query_timeout": 90,
            "extraction_timeout": 120,
            "max_time": 240,
        },
    },
    "medium": {
        "tier": "medium",
        "label": "Medium Local System (12-24 GB VRAM / 32-64 GB RAM)",
        "description": (
            "Balanced depth and throughput for 12B-32B models on RTX 3090/4090, RX 7900 XTX, "
            "or 32-64 GB Apple Silicon systems."
        ),
        "hardware_profile": {
            "min_vram_gb": 12.0,
            "recommended_vram_gb": 24.0,
            "recommended_ram_gb": 32.0,
            "recommended_param_range_b": [12.0, 32.0],
            "recommended_quantization": "Q4_K_M / Q5_K_M / FP8",
            "recommended_context_length": 32768,
            "example_models": [
                "Qwen/Qwen3-14B",
                "Qwen/Qwen3-32B",
                "google/gemma-4-26B-A4B-it",
            ],
        },
        "deep_researcher_params": {
            "max_rounds": 6,
            "min_rounds": 2,
            "max_empty_rounds": 2,
            "max_urls_per_round": 3,
            "extraction_concurrency": 2,
            "max_content_chars": 14000,
            "max_report_tokens": 8192,
            "synthesis_window": 10,
            "planning_timeout": 90,
            "query_timeout": 120,
            "extraction_timeout": 90,
            "max_time": 360,
        },
    },
    "large": {
        "tier": "large",
        "label": "Large Local System (>= 48 GB VRAM / Workstation / Multi-GPU)",
        "description": (
            "High-breadth, multi-round synthesis for 70B+ dense or large MoE models on multi-GPU "
            "rigs, RTX 6000 Ada, or 96-192 GB Apple Silicon workstations."
        ),
        "hardware_profile": {
            "min_vram_gb": 48.0,
            "recommended_vram_gb": 48.0,
            "recommended_ram_gb": 96.0,
            "recommended_param_range_b": [48.0, 250.0],
            "recommended_quantization": "Q4_K_M / Q6_K / FP8 / BF16",
            "recommended_context_length": 65536,
            "example_models": [
                "Qwen/Qwen3-235B-A22B",
                "meta-llama/Llama-3.3-70B-Instruct",
                "deepseek-ai/DeepSeek-R1-Distill-Llama-70B",
            ],
        },
        "deep_researcher_params": {
            "max_rounds": 10,
            "min_rounds": 3,
            "max_empty_rounds": 2,
            "max_urls_per_round": 5,
            "extraction_concurrency": 4,
            "max_content_chars": 24000,
            "max_report_tokens": 16384,
            "synthesis_window": 16,
            "planning_timeout": 120,
            "query_timeout": 150,
            "extraction_timeout": 120,
            "max_time": 600,
        },
    },
}


def list_research_presets() -> list[dict[str, Any]]:
    """Return ordered Deep Research presets (`small`, `medium`, `large`)."""
    return [deepcopy(RESEARCH_PRESETS[k]) for k in ("small", "medium", "large")]


def get_research_preset(tier: str) -> dict[str, Any]:
    """Return a deep copy of the named preset tier (`small`, `medium`, or `large`)."""
    key = (tier or "").strip().lower()
    if key not in RESEARCH_PRESETS:
        raise ValueError(f"Unknown Deep Research preset tier '{tier}'. Expected one of: small, medium, large")
    return deepcopy(RESEARCH_PRESETS[key])


def recommend_research_preset(
    *,
    vram_gb: float | None = None,
    ram_gb: float | None = None,
    model_params_b: float | None = None,
) -> dict[str, Any]:
    """Recommend `small`, `medium`, or `large` Deep Research preset from hardware/model specs."""
    tier = "medium"
    rationale_parts: list[str] = []

    effective_mem = None
    if vram_gb is not None and vram_gb > 0:
        effective_mem = float(vram_gb)
        rationale_parts.append(f"{effective_mem:.1f} GB VRAM")
    elif ram_gb is not None and ram_gb > 0:
        # Unified / CPU memory leaves ~70% headroom for model + KV cache
        effective_mem = float(ram_gb) * 0.70
        rationale_parts.append(f"{float(ram_gb):.1f} GB system RAM (~{effective_mem:.1f} GB effective)")

    if effective_mem is not None:
        if effective_mem < 11.5:
            tier = "small"
        elif effective_mem < 44.0:
            tier = "medium"
        else:
            tier = "large"

    if model_params_b is not None and model_params_b > 0:
        params = float(model_params_b)
        rationale_parts.append(f"{params:.1f}B model")
        if params <= 10.0:
            model_tier = "small"
        elif params <= 40.0:
            model_tier = "medium"
        else:
            model_tier = "large"

        order = {"small": 0, "medium": 1, "large": 2}
        if effective_mem is None:
            tier = model_tier
        else:
            # Never exceed what either hardware memory or model capacity safely supports
            tier = min((tier, model_tier), key=lambda t: order[t])

    if not rationale_parts:
        rationale_parts.append("default balanced local profile")

    preset = get_research_preset(tier)
    preset["recommendation_rationale"] = ", ".join(rationale_parts)
    return preset


def build_deep_researcher_kwargs(
    tier: str = "medium",
    **overrides: Any,
) -> dict[str, Any]:
    """Return `DeepResearcher` keyword arguments for `tier` with optional overrides."""
    preset = get_research_preset(tier)
    params = dict(preset["deep_researcher_params"])
    for key, val in overrides.items():
        if val is not None:
            params[key] = val
    return params
