import json
import os
import re

QUANT_HIERARCHY = ["Q8_0", "Q6_K", "Q5_K_M", "Q4_K_M", "Q3_K_M", "Q2_K"]

QUANT_BPP = {
    "F32": 4.0, "F16": 2.0, "BF16": 2.0, "FP8": 1.0,
    "Q8_0": 1.05, "Q6_K": 0.80, "Q5_K_M": 0.68,
    "Q4_K_M": 0.58, "Q4_0": 0.58, "Q3_K_M": 0.48, "Q2_K": 0.37,
    "AWQ-4bit": 0.50, "AWQ-8bit": 1.0,
    "GPTQ-Int4": 0.50, "GPTQ-Int8": 1.0,
    "mlx-4bit": 0.55, "mlx-8bit": 1.0, "mlx-6bit": 0.75,
}

QUANT_SPEED_MULT = {
    "F16": 0.6, "BF16": 0.6, "FP8": 0.85,
    "Q8_0": 0.8, "Q6_K": 0.95, "Q5_K_M": 1.0,
    "Q4_K_M": 1.15, "Q4_0": 1.15, "Q3_K_M": 1.25, "Q2_K": 1.35,
    "AWQ-4bit": 1.2, "AWQ-8bit": 0.85,
    "GPTQ-Int4": 1.2, "GPTQ-Int8": 0.85,
    "mlx-4bit": 1.15, "mlx-8bit": 0.85, "mlx-6bit": 1.0,
}

QUANT_QUALITY_PENALTY = {
    "F16": 0.0, "BF16": 0.0, "FP8": 0.0,
    "Q8_0": 0.0, "Q6_K": -1.0, "Q5_K_M": -2.0,
    "Q4_K_M": -5.0, "Q4_0": -5.0, "Q3_K_M": -8.0, "Q2_K": -12.0,
    "AWQ-4bit": -3.0, "AWQ-8bit": 0.0,
    "GPTQ-Int4": -3.0, "GPTQ-Int8": 0.0,
    "mlx-4bit": -4.0, "mlx-8bit": 0.0, "mlx-6bit": -1.0,
}

QUANT_BYTES_PER_PARAM = {
    "F16": 2.0, "BF16": 2.0, "FP8": 1.0,
    "Q8_0": 1.0, "Q6_K": 0.75, "Q5_K_M": 0.625,
    "Q4_K_M": 0.5, "Q4_0": 0.5, "Q3_K_M": 0.375, "Q2_K": 0.25,
    "AWQ-4bit": 0.5, "AWQ-8bit": 1.0,
    "GPTQ-Int4": 0.5, "GPTQ-Int8": 1.0,
    "mlx-4bit": 0.5, "mlx-8bit": 1.0, "mlx-6bit": 0.75,
}

# Pre-quantized formats that should NOT go through the GGUF quant hierarchy
PREQUANTIZED_PREFIXES = ("AWQ-", "GPTQ-", "mlx-", "FP8")


def is_prequantized(model):
    q = model.get("quantization", "")
    return any(q.startswith(p) for p in PREQUANTIZED_PREFIXES)


def params_b(model):
    raw = model.get("parameters_raw")
    if raw and raw > 0:
        return raw / 1_000_000_000.0

    pc = model.get("parameter_count", "")
    if pc:
        pc = pc.strip().upper()
        m = re.match(r"^([\d.]+)\s*([BKMGT]?)$", pc)
        if m:
            val = float(m.group(1))
            suffix = m.group(2)
            if suffix == "B":
                return val
            elif suffix == "M":
                return val / 1000.0
            elif suffix == "K":
                return val / 1_000_000.0
            elif suffix == "T":
                return val * 1000.0
            else:
                # No unit. A bare number this size is conventionally a millions
                # count (e.g. "355" = 355M), NOT billions — otherwise a 355M
                # model would sort as 355B and leap above every 7B/70B model.
                # A genuine billions figure carries a "B" suffix and is handled
                # above; very large bare values are raw parameter counts.
                if val >= 1_000_000:
                    return val / 1_000_000_000.0  # raw count
                if val >= 1000:
                    return val / 1000.0           # thousands of millions? treat as millions
                return val / 1000.0               # e.g. "355" → 0.355B
    return 0.0


def estimate_memory_gb(model, quant, ctx):
    """Estimate VRAM needed to serve a model. All weights must be loaded,
    even for MoE (all experts live in memory, only active ones compute per token).
    KV cache scales with active params for MoE (only active experts have KV state)."""
    pb = params_b(model)
    bpp = QUANT_BPP.get(quant, 0.58)
    kv_params = _active_params_b(model)
    return pb * bpp + 0.000008 * kv_params * ctx + 0.5


def _active_params_b(model):
    """For MoE: active params per token (affects KV cache and speed, not total VRAM).
    For dense: same as total params."""
    if model.get("is_moe") and model.get("active_parameters"):
        return model["active_parameters"] / 1_000_000_000.0
    return params_b(model)


def best_quant_for_budget(model, budget_gb, ctx):
    """Find best quant that fits in budget_gb of VRAM.
    Pre-quantized models (AWQ/GPTQ/MLX) use their native quant only.
    Returns (quant, ctx, mem_gb) or (None, None, None).
    """
    if is_prequantized(model):
        q = model.get("quantization", "Q4_K_M")
        mem = estimate_memory_gb(model, q, ctx)
        if mem <= budget_gb:
            return q, ctx, mem
        # Try halving context
        cur_ctx = ctx // 2
        while cur_ctx >= 1024:
            mem = estimate_memory_gb(model, q, cur_ctx)
            if mem <= budget_gb:
                return q, cur_ctx, mem
            cur_ctx //= 2
        return None, None, None

    # GGUF: try best quality first, then fall back
    for q in QUANT_HIERARCHY:
        mem = estimate_memory_gb(model, q, ctx)
        if mem <= budget_gb:
            return q, ctx, mem

    cur_ctx = ctx // 2
    while cur_ctx >= 1024:
        for q in QUANT_HIERARCHY:
            mem = estimate_memory_gb(model, q, cur_ctx)
            if mem <= budget_gb:
                return q, cur_ctx, mem
        cur_ctx //= 2

    return None, None, None


def infer_use_case(model):
    name = model.get("name", "").lower()
    uc = model.get("use_case", "").lower()
    combined = name + " " + uc

    if any(k in combined for k in ("embedding", "embed", "bge")):
        return "embedding"
    if any(k in combined for k in ("tts", "text-to-speech", "speech-synthesis", "cosyvoice", "parler")):
        return "tts"
    if any(k in combined for k in ("stt", "speech-to-text", "whisper", "transcri", "asr")):
        return "stt"
    if "code" in combined:
        return "coding"
    if any(k in combined for k in ("vision", "multimodal", "vlm", "vl-")):
        return "multimodal"
    if any(k in combined for k in ("reason", "chain-of-thought", "deepseek-r1")):
        return "reasoning"
    if any(k in combined for k in ("chat", "instruction")):
        return "chat"
    return "general"


_models_cache = None

def get_models():
    global _models_cache
    if _models_cache is None:
        data_path = os.path.join(os.path.dirname(__file__), "data", "hf_models.json")
        try:
            with open(data_path, encoding="utf-8") as f:
                _models_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _models_cache = []
    return _models_cache


def model_catalog_path():
    return os.path.join(os.path.dirname(__file__), "data", "hf_models.json")
