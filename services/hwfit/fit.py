import re

from services.hwfit.models import (
    params_b, estimate_memory_gb, infer_use_case,
    get_models, is_prequantized, _active_params_b, QUANT_BYTES_PER_PARAM,
    QUANT_SPEED_MULT, QUANT_QUALITY_PENALTY,
)

GPU_BANDWIDTH = {
    "5090": 1792, "5080": 960, "5070 ti": 896, "5070": 672, "5060 ti": 448, "5060": 256,
    "4090": 1008, "4080 super": 736, "4080": 717, "4070 ti super": 672, "4070 ti": 504, "4070 super": 504, "4070": 504, "4060 ti": 288, "4060": 272,
    "3090 ti": 1008, "3090": 936, "3080 ti": 912, "3080": 760, "3070 ti": 608, "3070": 448, "3060 ti": 448, "3060": 360,
    "2080 ti": 616, "2080 super": 496, "2080": 448, "2070 super": 448, "2070": 448, "2060 super": 448, "2060": 336,
    "1660 ti": 288, "1660 super": 336, "1660": 192, "1650 super": 192, "1650": 128,
    "h100 sxm": 3350, "h100": 2039, "h200": 4800, "a100 sxm": 2039, "a100": 1555,
    "l40s": 864, "l40": 864, "l4": 300, "a10g": 600, "a10": 600, "t4": 320,
    "v100 sxm": 900, "v100": 897, "a6000": 768, "a5000": 768, "a4000": 448,
    "7900 xtx": 960, "7900 xt": 800, "7900 gre": 576, "7800 xt": 624, "7700 xt": 432, "7600": 288,
    "6950 xt": 576, "6900 xt": 512, "6800 xt": 512, "6800": 512, "6700 xt": 384, "6600 xt": 256, "6600": 224,
    "mi300x": 5300, "mi300": 5300, "mi250x": 3277, "mi250": 3277, "mi210": 1638, "mi100": 1229,
    "9070 xt": 624, "9070": 488, "9060 xt": 322, "9060": 322,
    # Apple Silicon unified-memory bandwidth (GB/s). Keyed off the chip name
    # reported by sysctl machdep.cpu.brand_string (e.g. "Apple M4 Max"). Listed
    # before the bare "m_" keys matters less than length-sorting (done below),
    # which guarantees "m4 max" is tried before "m4".
    "m1 ultra": 800, "m1 max": 400, "m1 pro": 200, "m1": 68,
    "m2 ultra": 800, "m2 max": 400, "m2 pro": 200, "m2": 100,
    "m3 ultra": 800, "m3 max": 300, "m3 pro": 150, "m3": 100,
    "m4 max": 546, "m4 pro": 273, "m4": 120,
    "m5 max": 546, "m5 pro": 273, "m5": 150,
}

# Pre-sort keys by length descending for correct substring matching
_BW_KEYS_SORTED = sorted(GPU_BANDWIDTH.keys(), key=len, reverse=True)

# metal: backstop for Apple Silicon chips not in GPU_BANDWIDTH (e.g. a future
# M5) — the named chips above take the accurate bandwidth path instead.
FALLBACK_K = {"cuda": 220, "rocm": 180, "metal": 150, "cpu_x86": 70, "cpu_arm": 90}

USE_CASE_WEIGHTS = {
    "general":    (0.45, 0.30, 0.15, 0.10),
    "coding":     (0.50, 0.20, 0.15, 0.15),
    "reasoning":  (0.55, 0.15, 0.15, 0.15),
    "chat":       (0.40, 0.35, 0.15, 0.10),
    "multimodal": (0.50, 0.20, 0.15, 0.15),
    "embedding":  (0.30, 0.40, 0.20, 0.10),
    "tts":        (0.40, 0.35, 0.15, 0.10),
    "stt":        (0.40, 0.35, 0.15, 0.10),
}

SPEED_TARGET = {
    "general": 40, "coding": 40, "multimodal": 40, "chat": 40,
    "reasoning": 25, "embedding": 200, "tts": 40, "stt": 40,
}

CONTEXT_TARGET = {
    "general": 4096, "chat": 4096, "coding": 8192,
    "reasoning": 8192, "multimodal": 4096, "embedding": 512,
    "tts": 2048, "stt": 2048,
}


def _lookup_bandwidth(gpu_name):
    if not isinstance(gpu_name, str) or not gpu_name:
        return None
    gn = gpu_name.lower()
    for key in _BW_KEYS_SORTED:
        if key in gn:
            return GPU_BANDWIDTH[key]
    return None


def _estimate_speed(model, quant, run_mode, system, offload_frac=0.0):
    """Estimate tok/s. Uses active params for MoE (only active experts run per token).

    offload_frac (0..1): fraction of the model's weights that spill to system RAM
    (CPU) because they don't fit VRAM. Generation reads every active weight per
    token, so when part lives in CPU RAM the per-token time is dominated by the
    slow path. We model effective bandwidth as a blend of GPU VRAM bandwidth and
    system-RAM bandwidth weighted by what's where — far more accurate than a flat
    "halve it" for partial offload, which under/over-shoots depending on amount.
    Calibrated against a measured RX 9060 XT: DeepSeek-Coder-V2-Lite Q4_K_M with
    light offload → ~59 t/s est vs 59.8 measured.
    """
    pb = _active_params_b(model)
    is_moe = model.get("is_moe", False)
    bw = _lookup_bandwidth(system.get("gpu_name"))
    backend = system.get("backend", "cpu_x86")

    if bw and run_mode in ("gpu", "cpu_offload"):
        bpp = QUANT_BYTES_PER_PARAM.get(quant, 0.5)
        model_gb = pb * bpp
        if model_gb <= 0:
            return 0.0
        efficiency = 0.55
        if run_mode == "cpu_offload":
            # Dual-channel DDR4-3200 ≈ 50 GB/s; DDR5 systems higher, but be
            # conservative since offloaded MoE is also compute-bound on CPU.
            cpu_bw = 55.0
            frac = min(max(offload_frac, 0.0), 1.0)
            # If we don't know the fraction (legacy callers pass 0 with
            # cpu_offload), assume a meaningful spill so we don't overestimate.
            if frac <= 0.0:
                frac = 0.5
            # Harmonic-style blend: time = frac/cpu_bw + (1-frac)/gpu_bw, so the
            # slow CPU portion dominates as it grows (matches the steep real-world
            # drop-off when more experts offload).
            eff_bw = 1.0 / (frac / cpu_bw + (1.0 - frac) / bw)
            raw_tps = (eff_bw / model_gb) * efficiency
            return raw_tps * (0.8 if is_moe else 1.0)
        # Fully on GPU.
        raw_tps = (bw / model_gb) * efficiency
        return raw_tps * (0.8 if is_moe else 1.0)

    k = FALLBACK_K.get(backend, 70)
    if pb <= 0:
        return 0.0
    sm = QUANT_SPEED_MULT.get(quant, 1.0)
    return k / pb * sm


def _architecture_bonus(model):
    name = (model.get("name") or "").lower()
    arch = (model.get("architecture") or "").lower()
    text = f"{name} {arch}"

    # Keep this intentionally small: hardware fit and speed still matter, but
    # current model families should not be scored the same as older Qwen2/LLama
    # era entries just because the parameter count is similar.
    if "qwen3.6" in text or "qwen3_6" in text:
        return 9
    if "qwen3.5" in text or "qwen3_5" in text:
        return 8
    if "qwen3-next" in text or "qwen3_next" in text:
        return 6
    if "qwen3" in text or arch.startswith("qwen3"):
        return 4
    if "qwen2.5" in text or "qwen2_5" in text:
        return 2
    return 0


def _quality_score(model, quant, use_case):
    pb = params_b(model)
    if pb < 1:
        base = 30
    elif pb < 3:
        base = 45
    elif pb < 7:
        base = 60
    elif pb < 10:
        base = 75
    elif pb < 20:
        base = 82
    elif pb < 40:
        base = 89
    else:
        base = 95

    name_lower = model.get("name", "").lower()
    if "qwen" in name_lower:
        base += 2
    if "deepseek" in name_lower:
        base += 3
    if "llama" in name_lower:
        base += 2
    if "mistral" in name_lower or "mixtral" in name_lower:
        base += 1
    if "gemma" in name_lower:
        base += 1

    base += _architecture_bonus(model)
    base += QUANT_QUALITY_PENALTY.get(quant, 0)

    model_uc = infer_use_case(model)
    if model_uc == "coding" and use_case == "coding":
        base += 6
    elif model_uc == "coding" and use_case in ("general", "chat"):
        # Coder-specialized models are still useful generally, but they should
        # not dominate the default scan. If the user wants code, the Coding
        # filter gives them the boost above.
        base -= 10
    if model_uc == "reasoning" and use_case == "reasoning" and pb >= 13:
        base += 5
    elif model_uc == "reasoning" and use_case == "chat":
        base -= 4
    if model_uc == "multimodal" and use_case == "multimodal":
        base += 6

    return max(0, min(100, base))


def _speed_score(tps, use_case):
    target = SPEED_TARGET.get(use_case, 40)
    return max(0, min(100, (tps / target) * 100))


def _fit_score(required, available):
    if required > available:
        return 0
    if available <= 0:
        return 0
    ratio = required / available
    if ratio <= 0.5:
        return 60 + (ratio / 0.5) * 40
    if ratio <= 0.8:
        return 100
    if ratio <= 0.9:
        return 70
    return 50


def _context_score(ctx, use_case):
    target = CONTEXT_TARGET.get(use_case, 4096)
    if ctx >= target:
        return 100
    if ctx >= target / 2:
        return 70
    return 30


def _try_quant_at(model, quant, ctx, gpu_vram, available_ram):
    """Try a specific quant at a given context. Returns (run_mode, quant, ctx, mem) or None."""
    mem = estimate_memory_gb(model, quant, ctx)
    if gpu_vram > 0 and mem <= gpu_vram:
        return "gpu", quant, ctx, mem
    if gpu_vram > 0 and mem <= available_ram:
        return "cpu_offload", quant, ctx, mem
    if gpu_vram <= 0 and mem <= available_ram:
        return "cpu_only", quant, ctx, mem
    # Try halving context
    cur_ctx = ctx // 2
    while cur_ctx >= 1024:
        mem = estimate_memory_gb(model, quant, cur_ctx)
        if gpu_vram > 0 and mem <= gpu_vram:
            return "gpu", quant, cur_ctx, mem
        if mem <= available_ram:
            return ("cpu_offload" if gpu_vram > 0 else "cpu_only"), quant, cur_ctx, mem
        cur_ctx //= 2
    return None


def _quant_bits(q):
    """Approximate bit-width of a quant label so GGUF quant tiers (Q4/Q8/…) can
    be matched against prequantized formats (AWQ 4, AWQ-8bit, FP8, GPTQ-4bit…).
    Returns 0 when unknown (caller treats unknown as "don't filter")."""
    qu = (q or "").upper().replace("-", "").replace("_", "").replace(" ", "")
    # GGUF k-quants + float formats
    if qu.startswith("Q8") or "FP8" in qu or "INT8" in qu or qu.startswith("W8"):
        return 8
    if qu.startswith("Q4") or qu.startswith("IQ4") or "FP4" in qu or "NF4" in qu or "INT4" in qu or qu.startswith("W4"):
        return 4
    if qu.startswith("Q2") or qu.startswith("IQ2"):
        return 2
    if qu.startswith("Q3") or qu.startswith("IQ3"):
        return 3
    if qu.startswith("Q5"):
        return 5
    if qu.startswith("Q6"):
        return 6
    if qu.startswith("F16") or qu.startswith("BF16") or qu.startswith("F32"):
        return 16
    # Prequantized formats: pull the bit-width digit (AWQ4 / AWQ4BIT / GPTQ8 / 4BIT / INT8 ...)
    m = re.search(r"(?:AWQ|GPTQ|MLX|EXL2|BNB|INT|W)(\d{1,2})", qu) or re.search(r"(\d{1,2})BIT", qu)
    if m:
        b = int(m.group(1))
        if 2 <= b <= 16:
            return b
    return 0


def _native_quant(model):
    native_quant = model.get("quantization", "Q4_K_M")
    name = (model.get("name") or "").lower()
    fmt = (model.get("format") or "").lower()
    text = f"{name} {fmt}"
    if "nvfp4" in text:
        return "NVFP4"
    if re.search(r"(^|[-_/])fp8($|[-_/\s])", text):
        return "FP8"
    if "gptq" in text:
        m = re.search(r"(?:gptq|int|w)(?:[-_]?)(\d{1,2})(?:bit)?", text)
        # Canonical catalog label is "GPTQ-Int4"/"GPTQ-Int8" (see models.py
        # QUANT_BPP / QUANT_QUALITY_PENALTY keys); "GPTQ-4bit" misses both
        # maps, so BPP and the quality penalty silently fall to defaults.
        return f"GPTQ-Int{m.group(1)}" if m else "GPTQ-Int4"
    if "awq" in text:
        m = re.search(r"(?:awq|int|w)(?:[-_]?)(\d{1,2})(?:bit)?", text)
        # Catalog keys are "AWQ-4bit"/"AWQ-8bit"; bare "AWQ" misses the maps.
        return f"AWQ-{m.group(1)}bit" if m else "AWQ-4bit"
    if "mlx" in text:
        m = re.search(r"mlx[-_]?(\d{1,2})bit", text)
        return f"mlx-{m.group(1)}bit" if m else native_quant
    if not (model.get("is_gguf") or model.get("gguf_sources")) and re.search(r"(^|[-_/])(?:int)?8bit($|[-_/\s])", text):
        return "INT8"
    return native_quant


def analyze_model(model, system, target_quant=None, scoring_use_case=None, target_context=None):
    pb = params_b(model)
    if pb <= 0:
        return None

    model_use_case = infer_use_case(model)
    score_use_case = scoring_use_case or "general"
    has_gpu = system.get("has_gpu", False)
    gpu_vram = (system.get("gpu_vram_gb") or 0) if has_gpu else 0
    gpu_count = system.get("gpu_count", 1) or 1
    single_gpu_vram = gpu_vram / gpu_count if gpu_count > 1 else gpu_vram
    available_ram = system.get("available_ram_gb", 0)
    # When the user has explicitly picked a GPU config (not RAM mode), they want
    # to see what runs ON the GPU(s) — not big models that only "fit" by spilling
    # most layers to system RAM. Zeroing the offload budget makes _try_quant_at
    # take only its GPU branches (fit on VRAM, shrinking context if needed),
    # otherwise return None. Fixes "96 GB GPU still lists a 175 GB model".
    gpu_only = bool(system.get("gpu_only")) and has_gpu and gpu_vram > 0
    eff_ram = 0 if gpu_only else available_ram
    is_moe = model.get("is_moe", False)
    model_ctx = model.get("context_length", 4096) or 4096
    try:
        target_context = int(target_context or 0)
    except (TypeError, ValueError):
        target_context = 0
    ctx = min(model_ctx, target_context) if target_context > 0 else model_ctx

    native_quant = _native_quant(model)
    preq = is_prequantized(model)

    # GGUF models can't be sharded across GPUs — use single GPU VRAM
    is_gguf = bool(model.get("gguf_sources"))
    quant_upper = (native_quant or "").upper()
    is_gguf_quant = any(quant_upper.startswith(p) for p in ("Q2", "Q3", "Q4", "Q5", "Q6", "Q8", "IQ", "F16", "F32"))
    # Single-GPU VRAM only applies to GGUF/dense builds (llama.cpp can't shard
    # across GPUs). Prequantized formats (AWQ/GPTQ/FP8) are served sharded by
    # vLLM across all GPUs, so they get the FULL multi-GPU VRAM — even when the
    # model also lists a GGUF alternate download (gguf_sources).
    if (is_gguf or is_gguf_quant) and not preq:
        effective_vram = single_gpu_vram
    else:
        effective_vram = gpu_vram

    native_gpu_only = preq and not native_quant.startswith("mlx-")

    # Determine which quant to evaluate at
    native_quant_prefixes = (
        "AWQ-", "GPTQ-", "FP8", "FP4", "NVFP4", "MXFP4", "NF4",
        "INT4", "INT8", "W4A16", "W8A8", "W8A16",
    )

    if preq:
        # Native HF/vLLM quantized repos come at a fixed format. If the user
        # picked a GGUF quant tier (Q4/Q8/etc.), do not treat same-bit
        # AWQ/GPTQ/FP8/FP4 builds as equivalent; those formats are separate
        # serving paths and only appear when explicitly selected or unfiltered.
        if target_quant:
            if not any(target_quant.startswith(p) for p in native_quant_prefixes):
                return None
            _tb, _nb = _quant_bits(target_quant), _quant_bits(native_quant)
            if _tb and _nb and _tb != _nb:
                return None
        quant_to_try = native_quant
    elif target_quant:
        # User picked a specific quant
        quant_to_try = target_quant
    elif gpu_count >= 2:
        # Multi-GPU box: vLLM/SGLang can't serve GGUF Q* quants (those are
        # llama.cpp-only). Default non-prequantized models to BF16 so the row
        # is meaningful on a multi-GPU rig. If BF16 doesn't fit, the model
        # surfaces as too_tight — better than showing a Q4 row the user
        # can't actually serve with vLLM on >1 GPU.
        quant_to_try = "BF16"
    else:
        # Default: Q4_K_M (user's stated preference) — kept for single-GPU
        # and RAM modes where llama.cpp serving is the natural path.
        quant_to_try = "Q4_K_M"

    # Multi-GPU filter: skip the row if the resolved quant is a GGUF tier
    # (Q*/IQ-prefixed) — vLLM/SGLang can't serve those, so showing them on
    # a 2+ GPU rig just clutters the list with unservable candidates.
    if gpu_count >= 2 and quant_to_try and not target_quant and quant_to_try.upper().startswith(("Q2", "Q3", "Q4", "Q5", "Q6", "Q8", "IQ")):
        return None

    result = _try_quant_at(model, quant_to_try, ctx, effective_vram, 0 if native_gpu_only else eff_ram)

    if result is None:
        # Model doesn't fit on the user's current hardware. Surface it
        # anyway with a "too_tight" badge instead of silently dropping
        # it — without this, editing the hardware config to try LARGER
        # tiers never revealed the bigger models, because they were
        # filtered out before the user could see what would fit. The
        # client already knows how to render too_tight (red row).
        oversized_required = estimate_memory_gb(model, quant_to_try, ctx)
        return {
            "name": model.get("name"),
            "provider": model.get("provider"),
            "parameter_count": model.get("parameter_count"),
            "params_b": round(pb, 1),
            "is_moe": is_moe,
            "use_case": model_use_case,
            "fit_level": "too_tight",
            "run_mode": "no_fit",
            "quant": quant_to_try,
            "context": ctx,
            "required_gb": round(oversized_required, 1),
            "speed_tps": 0,
            "score": 0,
            "scores": {"quality": 0, "speed": 0, "fit": 0, "context": 0},
            "gguf_sources": model.get("gguf_sources", []),
            "context_length": model_ctx,
            "target_context": target_context or None,
        }

    run_mode, quant, fit_ctx, required_gb = result

    # Determine fit level
    budget = effective_vram if run_mode == "gpu" else available_ram
    if required_gb > budget:
        return None
    if run_mode == "gpu":
        rec = model.get("recommended_ram_gb") or required_gb
        if rec <= gpu_vram:
            fit_level = "perfect"
        elif gpu_vram >= required_gb * 1.2:
            fit_level = "good"
        else:
            fit_level = "marginal"
    elif run_mode == "cpu_offload":
        fit_level = "good" if available_ram >= required_gb * 1.2 else "marginal"
    else:
        fit_level = "marginal"

    # Fraction of the model that spills to CPU RAM (drives the offload speed
    # model). When offloading, anything beyond the GPU's VRAM lives in system RAM.
    offload_frac = 0.0
    if run_mode == "cpu_offload" and required_gb > 0 and effective_vram > 0:
        offload_frac = max(0.0, (required_gb - effective_vram) / required_gb)
    tps = _estimate_speed(model, quant, run_mode, system, offload_frac=offload_frac)

    q_score = _quality_score(model, quant, score_use_case)
    s_score = _speed_score(tps, score_use_case)
    f_score = _fit_score(required_gb, budget)
    c_score = _context_score(fit_ctx, score_use_case)

    wq, ws, wf, wc = USE_CASE_WEIGHTS.get(score_use_case, (0.45, 0.30, 0.15, 0.10))
    composite = q_score * wq + s_score * ws + f_score * wf + c_score * wc

    return {
        "name": model.get("name"),
        "provider": model.get("provider"),
        "parameter_count": model.get("parameter_count"),
        "params_b": round(pb, 1),
        "is_moe": is_moe,
        "use_case": model_use_case,
        "fit_level": fit_level,
        "run_mode": run_mode,
        "quant": quant,
        "context": fit_ctx,
        "required_gb": round(required_gb, 1),
        "speed_tps": round(tps, 1),
        "score": round(composite, 1),
        "scores": {
            "quality": round(q_score, 1),
            "speed": round(s_score, 1),
            "fit": round(f_score, 1),
            "context": round(c_score, 1),
        },
        "gguf_sources": model.get("gguf_sources", []),
        "context_length": model_ctx,
        "release_date": model.get("release_date", ""),
        "target_context": target_context or None,
    }


def _version_key(name):
    """Parse the model's version number from its display name so equal-score
    rows can break ties in favor of the newer release (e.g. M2.7 > M2.5).
    Returns a float; 0.0 for names with no recognizable version. The regex
    grabs the FIRST 'word-with-digits' pattern after a hyphen/underscore,
    so e.g. 'MiniMax-M2.7' -> 2.7, 'Qwen3.6-35B' -> 3.6, 'M2' -> 2.0."""
    import re as _re
    if not name:
        return 0.0
    # Match the version-marker word: a letter followed by a number with
    # optional decimal, e.g. M2.7, V4, Pro3. Take the first hit; ignore
    # "B" param-count suffixes (Qwen3-235B should yield 3, not 235).
    for m in _re.finditer(r"[A-Za-z](\d+(?:\.\d+)?)(?![A-Za-z])", name):
        val = m.group(1)
        # Skip param-count tokens (e.g. "235B" gives "235" but the next
        # char would be "B" — already excluded by the negative lookahead).
        try:
            f = float(val)
        except ValueError:
            continue
        # Heuristic: bare integers >= 100 are almost certainly param counts
        # (1B/3B/8B/70B/235B…), not version numbers. Skip them.
        if "." not in val and f >= 100:
            continue
        return f
    return 0.0


SORT_KEYS = {
    # Score sort with version-aware tiebreaker — when two rows tie on
    # composite score (a common case for the SAME base model in different
    # versions, e.g. MiniMax-M2.5 vs M2.7 both at the same FP8 budget),
    # prefer the newer version. Without this, ties resolved to whatever
    # order they came out of the registry, which let older releases land
    # above newer ones in user-facing lists.
    "score": lambda r: (r["score"], _version_key(r.get("name") or "")),
    "speed": lambda r: r["speed_tps"],
    "vram": lambda r: r["required_gb"],
    "params": lambda r: r["params_b"],
    "context": lambda r: r["context"],
    # Newest first. release_date is an ISO-ish string ("2026-05-30"); plain
    # string sort is chronological. Missing dates sort last (empty < any date,
    # and we sort reverse=True for newest, so "" lands at the bottom).
    "newest": lambda r: r.get("release_date") or "",
}


def rank_models(system, use_case=None, limit=50, search=None, sort="score", quant=None, target_context=None, fit_only=False):
    """Rank all models against detected hardware. Returns sorted list of fit results.

    fit_only: when True, drop rows whose fit_level is "too_tight" (model doesn't
    actually fit on the chosen budget). When False (default), every model is
    shown — sorting by Param means highest-param PERIOD, even ones that won't
    run, so the user can see the truth.
    """
    models = get_models()
    results = []

    # Include image gen models only when explicitly filtered
    if use_case == "image_gen":
        try:
            from services.hwfit.image_models import rank_image_models
        except ImportError:
            rank_image_models = None
        if rank_image_models:
            img_results = rank_image_models(system, search=search)
        else:
            img_results = []
        for im in img_results:
            fit_map = {"perfect": "perfect", "good": "good", "tight": "marginal", "no_fit": "too_tight", "no_gpu": "too_tight"}
            results.append({
                "name": im["id"],
                "provider": im["provider"],
                "parameter_count": f"{im['params_b']}B",
                "params_b": im["params_b"],
                "is_moe": False,
                "use_case": "image_gen",
                "fit_level": fit_map.get(im["fit"], "too_tight"),
                "run_mode": "gpu" if im["fits"] else "no_fit",
                "quant": im.get("quant", "BF16"),
                "context": 0,
                "context_length": 0,
                "required_gb": round(im.get("vram_needed") or 0, 1),
                "speed_tps": 0,
                "score": float(im["score"]),
                "scores": {"quality": float(im["quality"]), "speed": float(im["speed"]), "fit": 0, "context": 0},
                "gguf_sources": [],
                "is_image_gen": True,
                "capabilities": im.get("capabilities", []),
                "description": im.get("description", ""),
            })
        if use_case == "image_gen":
            sort_fn = SORT_KEYS.get(sort, SORT_KEYS["score"])
            results.sort(key=sort_fn, reverse=True)  # see main path below
            return results[:limit]

    # If user picked a native prequantized format, filter to only those models.
    filter_native = quant and any(quant.startswith(p) for p in (
        "AWQ-", "GPTQ-", "FP8", "FP4", "NVFP4", "MXFP4", "NF4",
        "INT4", "INT8", "W4A16", "W8A8", "W8A16",
    ))

    system_backend = (system.get("backend") or "").lower()
    apple_silicon = system_backend in ("mps", "metal", "apple")
    rocm = system_backend == "rocm"

    # Consumer AMD Radeon (RDNA, gfx10/11/12): the practical local serving path
    # is GGUF via llama.cpp. vLLM/SGLang on ROCm are validated for datacenter
    # Instinct (CDNA, gfx9xx) but are unreliable on consumer RDNA — AWQ kernels
    # are largely unsupported there and FP8 needs out-of-tree patches. So treat
    # consumer RDNA like Apple Silicon (GGUF-only) and leave CDNA untouched.
    # Unknown family (no rocminfo) is left untouched to avoid hiding models from
    # a possibly-capable Instinct box on a misdetect.
    gpu_family = (system.get("gpu_family") or "").lower()
    consumer_amd = system_backend == "rocm" and gpu_family == "rdna"

    for m in models:
        native_q = _native_quant(m)

        # MLX needs the mlx_lm runtime, which Odysseus does not generate serve
        # commands for. Hide it on every backend, including Metal.
        if native_q.startswith("mlx-") or "mlx" in (m.get("name") or "").lower():
            continue

        # ROCm support for vLLM/SGLang quantized safetensors is too brittle to
        # recommend blindly in the default scan. Keep AWQ/GPTQ/FP8 discoverable
        # only when the user explicitly picks that format from the quant filter;
        # otherwise prefer GGUF/Q* entries that Odysseus can route through
        # llama.cpp/Ollama without pretending "fits VRAM" means "servable".
        if rocm and is_prequantized(m) and not filter_native:
            continue

        # On Apple Silicon the only serving engines are llama.cpp and Ollama,
        # both GGUF-only (vLLM/SGLang are CUDA/ROCm and don't run on macOS). So
        # a model is Metal-servable ONLY if it ships a real GGUF. Drop everything
        # else — raw safetensors repos (which the catalog still tags with a
        # default GGUF quant) and vLLM-only AWQ/GPTQ/FP8 builds alike. Without
        # this the Cookbook recommends models the Mac can't run; on CUDA these
        # stay visible because vLLM serves safetensors directly.
        #
        # Consumer AMD (RDNA) is the same story: GGUF via llama.cpp is the
        # servable path, so a model needs a real GGUF to be recommended.
        # Otherwise the Cookbook rates vLLM-only AWQ/GPTQ builds "GOOD" on a
        # Radeon that can't actually serve them.
        if (apple_silicon or consumer_amd) and not (m.get("is_gguf") or m.get("gguf_sources")):
            continue

        # Format filter: AWQ tab -> only AWQ models, FP4 tab -> FP4-family models, etc.
        if filter_native:
            if quant == "FP8" and native_q != "FP8":
                continue
            if quant == "FP4" and native_q not in ("FP4", "NVFP4", "MXFP4", "NF4"):
                continue
            if quant.startswith("AWQ") and not native_q.startswith("AWQ"):
                continue
            if quant.startswith("GPTQ") and not native_q.startswith("GPTQ"):
                continue
            if quant.startswith("NVFP4") and not native_q.startswith("NVFP4"):
                continue
            if quant in ("INT4", "INT8", "W4A16", "W8A8", "W8A16") and native_q != quant:
                continue

        if search:
            name = m.get("name", "").lower()
            provider = m.get("provider", "").lower()
            if search.lower() not in name and search.lower() not in provider:
                continue

        result = analyze_model(m, system, target_quant=quant, scoring_use_case=(use_case or "general"), target_context=target_context)
        if result is None:
            continue

        if use_case:
            model_uc = infer_use_case(m)
            if use_case != model_uc and use_case != "general":
                continue

        results.append(result)

    # Pick the visible SET by the REQUESTED column. Per-user feedback: sorting
    # by Param should show the highest-param models PERIOD, not just those that
    # already fit. Same for every other column. Models that don't fit are still
    # in the list with their fit_level marking the constraint, so the user can
    # see the truth instead of a quietly-truncated view. Score sort is unchanged
    # (it's the default ranking and naturally pushes non-fits to the bottom).
    if fit_only:
        # Hide rows that definitely don't fit (the "too_tight" badge) — user
        # explicitly asked for a Fit-only view.
        results = [r for r in results if r.get("fit_level") != "too_tight"]
    sort_fn = SORT_KEYS.get(sort, SORT_KEYS["score"])
    # Always sort descending then truncate top-N so each column shows the
    # global highest by that metric. Before, vram was special-cased
    # ascending → truncate kept the 50 SMALLEST models and "highest VRAM"
    # could never appear, breaking the column-click toggle.
    results.sort(key=sort_fn, reverse=True)
    results = results[:limit]
    return results
