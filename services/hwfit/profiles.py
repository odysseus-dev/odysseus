"""Compute intelligent llama.cpp serve profiles from detected hardware.

Given a system (VRAM/RAM/arch) and a model, produce 1-4 ready-to-launch
profiles — Quality / Balanced / Speed — with concrete llama.cpp flags
(n_gpu_layers, n_cpu_moe, cache-type, context). This turns the by-hand tuning
(how many MoE layers fit on the GPU, when to spend VRAM on a q8 KV cache vs more
context, how much headroom to leave for a vision encoder) into a formula.

Pure/deterministic — no benchmarking, no I/O. Reuses the same VRAM math as
fit.py/models.py so "what the Cookbook recommends" and "what it serves" agree.

NOTE: token/s figures are NOT computed here — real speed on partial-offload MoE
is CPU-bound and not reliably predictable from specs. The UI labels profiles by
their tradeoff (Quality/Balanced/Speed), and the VRAM fit (the part that decides
whether it even loads) is what's computed from real numbers.
"""

from services.hwfit.models import (
    QUANT_BPP,
    params_b,
    _active_params_b,
    is_prequantized,
)

# GGUF KV-cache cost per token, in bytes-per-active-billion-param, by cache type.
# q4_0 is ~half of q8_0 is ~half of f16. The 8e-6 base in estimate_memory_gb is
# the q8_0-ish figure; scale from there.
_KV_FACTOR = {"q4_0": 0.5, "q8_0": 1.0, "f16": 2.0}

# Quant ladder from highest quality/size down. A profile that wants "best quant
# that fits fully on GPU" walks this until one fits.
_QUANT_LADDER = ["Q8_0", "Q6_K", "Q5_K_M", "Q4_K_M", "Q3_K_M", "Q2_K"]


def _weights_gb(model, quant, fixed_gb=None):
    """VRAM for the full weights. When fixed_gb is given (serving a specific GGUF
    file already on disk), use its real size — the quant is whatever the file is,
    not something we get to pick."""
    if fixed_gb and fixed_gb > 0:
        return float(fixed_gb)
    return params_b(model) * QUANT_BPP.get(quant, 0.58)


def _kv_gb(model, ctx, kv_type):
    """KV-cache VRAM at a context length and cache type."""
    kv_params = _active_params_b(model)
    return 0.000008 * kv_params * ctx * _KV_FACTOR.get(kv_type, 1.0)


def _n_layers(model):
    """Best-effort total transformer block count (for n-cpu-moe math)."""
    for k in ("num_hidden_layers", "n_layers", "num_layers", "block_count"):
        v = model.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    # Fallback heuristic by size — most MoE/dense LLMs land 28-64 layers.
    pb = params_b(model)
    if pb >= 60:
        return 64
    if pb >= 25:
        return 48
    if pb >= 12:
        return 40
    return 32


def _cpu_moe_for_budget(model, quant, kv_gb, vram_budget_gb, fixed_gb=None):
    """How many MoE layers must move to CPU so weights+KV fit vram_budget_gb.

    Returns (n_cpu_moe, fits_fully). When the model already fits, n_cpu_moe=0.
    Each offloaded layer frees roughly weights/n_layers of VRAM. We only model
    this for MoE (where --n-cpu-moe applies); dense models just report whether
    they fit at the given n_gpu_layers=999.
    """
    weights = _weights_gb(model, quant, fixed_gb)
    needed = weights + kv_gb + 0.6  # +0.6 GB runtime/compute buffers
    if needed <= vram_budget_gb:
        return 0, True
    if not model.get("is_moe"):
        # Dense: no per-expert offload knob; either it fits or it spills via -ngl.
        return 0, False
    layers = _n_layers(model)
    per_layer = weights / max(layers, 1)
    overflow = needed - vram_budget_gb
    import math
    n = math.ceil(overflow / max(per_layer, 1e-6))
    n = max(0, min(n, layers))   # clamp
    return n, False


def compute_serve_profiles(system, model, serve_weights_gb=None, serve_quant=None):
    """Return a list of profile dicts for llama.cpp serving of `model` on `system`.

    Each profile: {key, label, quant, n_gpu_layers, n_cpu_moe, cache_type, ctx,
                   est_vram_gb, fits, note}. Empty list if no GGUF path makes
    sense (caller should fall back to manual flags).

    DOWNLOAD mode (default): the quant isn't chosen yet, so profiles vary it
    (Quality=Q6, Balanced=Q4, Speed=Q2…) to show download options.

    SERVE mode (serve_weights_gb set): a specific GGUF file already exists on
    disk — its quant is FIXED. Profiles then keep that quant/size and differ only
    in the actual serving knobs (n_cpu_moe, KV-cache type, context). serve_quant
    is the file's quant label (e.g. "Q4_K_M") just for display.
    """
    vram = float(system.get("gpu_vram_gb") or 0)
    if vram <= 0:
        return []

    serve_mode = bool(serve_weights_gb and serve_weights_gb > 0)

    # Never propose more context than the model was trained for — asking llama.cpp
    # for ctx > n_ctx_train triggers a "training context overflow" and, with a
    # quantized KV cache, an oversized allocation that can crash the GPU
    # (radv/amdgpu ErrorDeviceLost). Cap every profile at the model's real limit.
    model_ctx_max = 0
    for k in ("context_length", "max_position_embeddings", "n_ctx_train", "context"):
        v = model.get(k)
        if isinstance(v, (int, float)) and v > 0:
            model_ctx_max = int(v)
            break
    if model_ctx_max <= 0:
        model_ctx_max = 131072  # conservative default when the catalog omits it

    # Vision models need headroom for the image encoder (~1 GB on top of weights).
    is_vision = bool(
        model.get("is_multimodal") or model.get("vision") or model.get("mmproj")
        or "vl" in str(model.get("name", "")).lower()
    )
    headroom = 1.1 if is_vision else 0.4
    budget = max(vram - headroom, 1.0)

    # Prequantized (AWQ/GPTQ/FP8) served via GGUF fallback use a fixed ~Q4 quant;
    # GGUF models can pick their quant. Pick a sensible per-profile quant.
    fixed_quant = model.get("quantization") if is_prequantized(model) else None

    is_moe = bool(model.get("is_moe"))

    def _pick_quant(prefer, require_full_fit):
        """Choose a quant for a profile.

        - fixed_quant (AWQ/GPTQ/FP8 served via GGUF): always that.
        - require_full_fit=True (Speed): walk DOWN from `prefer` to the best quant
          whose weights fit fully on the GPU (no offload) — fastest.
        - require_full_fit=False (Quality on MoE): keep `prefer` even if it must
          offload experts to CPU; that's the whole point of n-cpu-moe on a card
          too small to hold the weights. For dense models we can't offload
          per-expert, so fall back to the largest fully-fitting quant.
        """
        if fixed_quant:
            return fixed_quant
        start = _QUANT_LADDER.index(prefer) if prefer in _QUANT_LADDER else 3
        if require_full_fit or not is_moe:
            for q in _QUANT_LADDER[start:]:
                if _weights_gb(model, q) + 0.6 <= budget:
                    return q
            return _QUANT_LADDER[-1]
        # MoE quality: keep the preferred (big) quant; offload handles overflow.
        return prefer

    if serve_mode:
        # Fixed file on disk — quant can't change. Vary only the serving knobs.
        fq = serve_quant or model.get("quantization") or "GGUF"
        specs = [
            # key, label, prefer_quant, full_fit, kv_type, ctx, note
            ("quality", "Quality", fq, False, "q8_0", 131072,
             "Sharp q8 KV cache + full context. Best long-context accuracy; offloads MoE layers to CPU if needed."),
            ("balanced", "Balanced", fq, False, "q4_0", 131072,
             "Compact q4 KV at full context — good speed/quality mix."),
            ("speed", "Speed", fq, False, "q4_0", 32768,
             "Trimmed context + light KV for the fastest tokens/s."),
        ]
    else:
        specs = [
            # key, label, prefer_quant, full_fit, kv_type, ctx, note
            ("quality", "Quality", "Q6_K", False, "q8_0", 131072,
             "Biggest quant + sharp q8 KV cache. Best answers; offloads MoE layers to CPU if needed."),
            ("balanced", "Balanced", "Q4_K_M", False, "q4_0", 131072,
             "Q4 weights + compact q4 KV. Good speed/quality mix at full context."),
            ("speed", "Speed", "Q4_K_M", True, "q4_0", 32768,
             "Smallest offload + trimmed context for the fastest tokens/s."),
        ]

    profiles = []
    for key, label, prefer_q, full_fit, kv_type, ctx, note in specs:
        # In serve mode the quant is fixed (the file's); in download mode we pick.
        quant = prefer_q if serve_mode else _pick_quant(prefer_q, full_fit)
        # Shrink context if even the chosen KV won't fit alongside weights.
        # Start from the smaller of the profile's target and the model's limit.
        cur_ctx = min(ctx, model_ctx_max)
        # Floor the context-shrink loop at 8192, but never above the model's own
        # trained limit. A model with a sub-8192 context (e.g. a 2048-token
        # SmolLM) starts below 8192, so a hard-coded 8192 guard skipped the loop
        # entirely and produced NO profile — the serve UI then fell back to
        # manual flags even though the model fits the GPU trivially.
        ctx_floor = min(8192, model_ctx_max)
        while cur_ctx >= ctx_floor:
            kv = _kv_gb(model, cur_ctx, kv_type)
            n_cpu_moe, fits = _cpu_moe_for_budget(model, quant, kv, budget, fixed_gb=serve_weights_gb)
            est = _weights_gb(model, quant, serve_weights_gb) + kv + 0.6
            # If a non-MoE model can't fit even fully offloaded, try less context.
            if model.get("is_moe") or fits or cur_ctx <= ctx_floor:
                profiles.append({
                    "key": key,
                    "label": label,
                    "quant": quant,
                    "n_gpu_layers": 999,
                    "n_cpu_moe": n_cpu_moe,
                    "cache_type": kv_type,
                    "ctx": cur_ctx,
                    # When experts offload, GPU-resident VRAM tops out at the
                    # budget (weights beyond it live in system RAM), so cap the
                    # estimate at `budget`, not the full card — this also leaves
                    # the vision-encoder headroom visible in the number.
                    "est_vram_gb": round(min(est, budget), 1),
                    # For MoE we treat it as fitting via offload; report whether
                    # it fit WITHOUT offload as the "clean" flag.
                    "fits": fits or bool(model.get("is_moe")),
                    "offloads": n_cpu_moe > 0,
                    "note": note,
                })
                break
            cur_ctx //= 2

    # De-dupe identical profiles (e.g. tiny model where all three collapse to the
    # same all-GPU config) — keep the first/highest-quality label.
    seen = set()
    deduped = []
    for p in profiles:
        sig = (p["quant"], p["n_cpu_moe"], p["cache_type"], p["ctx"])
        if sig in seen:
            continue
        seen.add(sig)
        deduped.append(p)
    return deduped
