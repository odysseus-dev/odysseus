#!/usr/bin/env python3
"""
add_hwfit_models.py — bulk-add Hugging Face models to the hwfit catalog
(services/hwfit/data/hf_models.json).

Adds:
  * every model from one or more HF authors (e.g. cyankiwi's AWQ quants)
  * any explicitly-listed repos

Metadata is taken from the HF Hub `list_models(full=True)` response plus the
repo name (which encodes the param size, e.g. "Qwen3.6-35B-A3B"). Param-less
names fall back, in order, to the parent `base_model:` tag, the repo's
`config.json` (computed from `hidden_size` / `num_hidden_layers` / MoE
fields), and finally a per-repo `model_info()` call to read safetensors.

Re-runnable: merges by `name`, leaving existing entries untouched unless
--overwrite is passed. Writes a .bak first.

Usage:
    python3 scripts/add_hwfit_models.py
"""
import json
import os
import re
import sys
from datetime import datetime

from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.utils import EntryNotFoundError, RepositoryNotFoundError

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "services", "hwfit", "data", "hf_models.json")
DATA_PATH = os.path.abspath(DATA_PATH)

AUTHORS = ["cyankiwi"]
# Specific repos to add (in addition to the authors above). Optional explicit
# overrides {repo: {field: value}} for things the name/metadata can't convey.
EXTRA_REPOS = {
    "deepseek-ai/DeepSeek-V4-Flash":            {"parameter_count": "168B", "quantization": "Q4_K_M"},
    "MiniMaxAI/MiniMax-M2.7":                   {"parameter_count": "228.7B", "quantization": "Q4_K_M"},
    "bullerwins/MiniMax-M2.7-REAP-172B-fp8":    {"parameter_count": "172B", "quantization": "FP8"},
    "cyankiwi/MiniMax-M2.7-AWQ-4bit":           {"parameter_count": "228.7B", "quantization": "AWQ-4bit"},
}

# Tags that are not architecture names.
_GENERIC_TAGS = {
    "transformers", "safetensors", "conversational", "text-generation",
    "image-text-to-text", "text-generation-inference", "endpoints_compatible",
    "autotrain_compatible", "compressed-tensors", "gguf", "mlx", "vllm", "4-bit",
    "8-bit", "awq", "gptq", "fp8", "fp4", "nvfp4", "mxfp4", "nf4",
    "quantized", "chat",
}

api = HfApi()


def _parse_params(name):
    """Return (parameters_raw, active_parameters_or_None) from a repo name.
    Handles dense ("27B") and MoE ("235B-A22B") naming."""
    base = name.split("/")[-1]
    active = None
    m_active = re.search(r"-[Aa](\d+\.?\d*)[Bb](?![a-zA-Z])", base)
    if m_active:
        active = int(float(m_active.group(1)) * 1e9)
        base_wo = base[:m_active.start()] + base[m_active.end():]
    else:
        base_wo = base
    # First "<num>B" token that is a plausible size. Case-insensitive b, but the
    # negative lookahead means "8bit"/"4bit" are NOT treated as "8B"/"4B".
    total = None
    for m in re.finditer(r"(\d+\.?\d*)[Bb](?![a-zA-Z])", base_wo):
        total = int(float(m.group(1)) * 1e9)
        break
    return total, active


def _params_from_config(cfg):
    """Estimate (total, active) parameter counts from a HF config.json dict.

    Returns (None, None) when the architecture fields aren't usable. Covers:
      * explicit ``num_parameters`` / ``n_params`` (rare but authoritative)
      * dense transformers (LLaMA / Qwen / Mistral / GLM-dense / etc.) via
        embeddings + per-layer attention + MLP
      * MoE (Qwen3-MoE, GLM-4-MoE, DeepSeek-style) using ``num_experts`` or
        ``n_routed_experts`` (+ ``n_shared_experts``). Active count assumes
        ``num_experts_per_tok`` routed experts plus any shared experts.

    The estimate is intentionally coarse — within ~5-10% of the true count for
    standard decoder-only architectures — which is fine for the downstream
    ``min_vram_gb`` heuristic (it already buckets via ``parameter_count`` to
    one decimal place of "B").
    """
    if not isinstance(cfg, dict):
        return None, None

    # Authoritative fields first. Some custom configs embed the trained
    # parameter count directly.
    for key in ("num_parameters", "n_params", "total_params"):
        v = cfg.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return int(v), None

    def _i(key, default=None):
        v = cfg.get(key, default)
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    h = _i("hidden_size")
    L = _i("num_hidden_layers")
    if not h or not L:
        return None, None

    vocab = _i("vocab_size") or 0
    ffn = _i("intermediate_size") or (4 * h)
    n_heads = _i("num_attention_heads") or 0
    n_kv = _i("num_key_value_heads") or n_heads
    head_dim = _i("head_dim") or (h // n_heads if n_heads else h)

    # Attention: Q is hidden_size wide, KV is grouped (GQA / MQA).
    q_proj = h * (n_heads * head_dim if n_heads else h)
    kv_proj = 2 * h * (n_kv * head_dim if n_kv else h)
    o_proj = (n_heads * head_dim if n_heads else h) * h
    per_layer_attn = q_proj + kv_proj + o_proj

    # Dense MLP: gate + up + down (SwiGLU / GeGLU). Configs without a gate
    # (plain GELU) are within the noise floor of this estimate.
    per_layer_dense_mlp = 3 * h * ffn

    # MoE routing. Both naming conventions are seen in the wild.
    n_experts = _i("num_experts") or _i("n_routed_experts") or 0
    n_shared = _i("n_shared_experts") or 0
    n_active = _i("num_experts_per_tok") or 0
    moe_ffn = _i("moe_intermediate_size") or ffn
    # Some configs (GLM-4-MoE, DeepSeek-V3) keep the first K layers dense.
    first_dense = _i("first_k_dense_replace") or 0

    if n_experts > 0 and n_active > 0:
        moe_layers = max(0, L - first_dense)
        dense_layers = L - moe_layers
        per_expert = 3 * h * moe_ffn
        total_mlp = (
            dense_layers * per_layer_dense_mlp
            + moe_layers * (n_experts + n_shared) * per_expert
        )
        active_mlp = (
            dense_layers * per_layer_dense_mlp
            + moe_layers * (n_active + n_shared) * per_expert
        )
    else:
        total_mlp = L * per_layer_dense_mlp
        active_mlp = total_mlp

    embed = vocab * h
    # Untied output head doubles the embedding contribution.
    head = 0 if cfg.get("tie_word_embeddings", True) else vocab * h

    total = embed + head + L * per_layer_attn + total_mlp
    active = embed + head + L * per_layer_attn + active_mlp
    if total <= 0:
        return None, None
    if active == total or n_experts == 0:
        return int(total), None
    return int(total), int(active)


_CONFIG_CACHE = {}


def _fetch_config_json(repo_id):
    """Download and cache a repo's config.json. Returns a dict or None.

    Network / 404 / private-repo failures are swallowed — the caller already
    has a safetensors fallback below this. We rely on huggingface_hub's own
    on-disk cache so repeated script runs don't re-hit the Hub.
    """
    if repo_id in _CONFIG_CACHE:
        return _CONFIG_CACHE[repo_id]
    try:
        path = hf_hub_download(repo_id=repo_id, filename="config.json")
    except (EntryNotFoundError, RepositoryNotFoundError):
        _CONFIG_CACHE[repo_id] = None
        return None
    except Exception:
        # Network hiccup, gated repo, etc. — don't crash the bulk run.
        _CONFIG_CACHE[repo_id] = None
        return None
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        _CONFIG_CACHE[repo_id] = None
        return None
    _CONFIG_CACHE[repo_id] = cfg
    return cfg


def _base_model_tag(tags):
    """Return the `base_model:...` repo id from tags, if any."""
    for t in (tags or []):
        if t.startswith("base_model:"):
            return t.split(":")[-1]
    return None


def _quant_from_name(name):
    n = name.lower()
    if "nvfp4" in n:
        return "NVFP4"
    if "mxfp4" in n:
        return "MXFP4"
    if re.search(r"(^|[-_/])nf4($|[-_/])", n):
        return "NF4"
    if re.search(r"(^|[-_/])fp4($|[-_/])", n):
        return "FP4"
    if re.search(r"(^|[-_/])w4a16($|[-_/])", n):
        return "W4A16"
    if re.search(r"(^|[-_/])w8a8($|[-_/])", n):
        return "W8A8"
    if re.search(r"(^|[-_/])w8a16($|[-_/])", n):
        return "W8A16"
    is8 = "8bit" in n or "8-bit" in n or "int8" in n
    if "awq" in n:
        return "AWQ-8bit" if is8 else "AWQ-4bit"
    if "gptq" in n:
        return "GPTQ-Int8" if is8 else "GPTQ-Int4"
    if "mlx" in n:
        if "6bit" in n:
            return "mlx-6bit"
        return "mlx-8bit" if is8 else "mlx-4bit"
    if "nvfp4" in n:
        return "NVFP4"
    if "fp8" in n:
        return "FP8"
    if "int4" in n or "4bit" in n or "4-bit" in n:
        return "INT4"
    if "int8" in n or "8bit" in n or "8-bit" in n:
        return "INT8"
    return "Q4_K_M"


def _arch_from_tags(tags):
    for t in (tags or []):
        if ":" in t or t in _GENERIC_TAGS:
            continue
        if re.fullmatch(r"[a-z0-9_]+", t) and any(c.isalpha() for c in t):
            return t
    return ""


def _entry_from_modelinfo(mi, overrides):
    name = mi.id
    provider = name.split("/")[0]
    total, active = _parse_params(name)
    # If the name has no size but an override supplies one, use that.
    if total is None and overrides and overrides.get("parameter_count"):
        total, _ov_active = _parse_params("x/" + overrides["parameter_count"])
    # Next, try the base_model tag (the unquantized parent often names its size).
    if total is None:
        bm = _base_model_tag(getattr(mi, "tags", None))
        if bm:
            bt, ba = _parse_params(bm)
            if bt:
                total = bt
                if ba and active is None:
                    active = ba
    # Determine quant first — we need it to unpack the safetensors fallback.
    quant = _quant_from_name(name)
    # Next-to-last resort: parse config.json. This is robust against
    # parameter-less repo names (e.g. "GLM-4.5" with no "9B" suffix) where
    # both the regex and the base_model tag come up empty. We try this
    # before safetensors so non-standard names still resolve without a
    # per-repo manual override in EXTRA_REPOS. Source repo first (works for
    # unquantized models) then the quantized parent via base_model:.
    if total is None:
        config_targets = [name]
        bm = _base_model_tag(getattr(mi, "tags", None))
        if bm and bm != name:
            config_targets.append(bm)
        for target in config_targets:
            cfg = _fetch_config_json(target)
            if not cfg:
                continue
            ct, ca = _params_from_config(cfg)
            if ct:
                total = ct
                if ca and active is None:
                    active = ca
                break
    # Last resort: read safetensors element counts. For pre-quantized repos
    # (AWQ/GPTQ/MLX-Int4 etc.) the weights are packed: 8× 4-bit weights per
    # I32 element, 4× 8-bit weights per I32. The bare safetensors total
    # therefore undercounts real parameter count by the same factor, which
    # then feeds a wrong `min_vram_gb` downstream. Sum per-dtype and unpack
    # the packed I32 tensors so the catalog stores the true param count.
    if total is None:
        try:
            full = api.model_info(name, files_metadata=False)
            st = getattr(full, "safetensors", None)
            if st:
                params_by_dtype = getattr(st, "parameters", None) or {}
                if quant.endswith("4bit") or quant.endswith("Int4"):
                    pack_factor = 8
                elif quant.endswith("8bit") or quant.endswith("Int8") or quant in ("FP8", "NVFP4"):
                    pack_factor = 4
                else:
                    pack_factor = 1
                if params_by_dtype:
                    # I32/I64 hold the packed quantized weights; everything
                    # else (F16/BF16 scales, zeros, embeddings) is already at
                    # its real element count.
                    packed = sum(c for d, c in params_by_dtype.items() if d in ("I32", "I64"))
                    rest = sum(c for d, c in params_by_dtype.items() if d not in ("I32", "I64"))
                    total = packed * pack_factor + rest
                elif getattr(st, "total", None):
                    total = int(st.total) * pack_factor
        except Exception:
            pass
    if total is None:
        return None  # can't size it — skip
    pb = total / 1e9
    created = getattr(mi, "created_at", None)
    rel = created.strftime("%Y-%m-%d") if created else datetime.utcnow().strftime("%Y-%m-%d")
    # Rough RAM/VRAM hints (fit.py recomputes the real requirement from params+quant).
    _BPP = {"AWQ-4bit": 0.58, "GPTQ-Int4": 0.58, "mlx-4bit": 0.55, "mlx-6bit": 0.85,
            "AWQ-8bit": 1.1, "GPTQ-Int8": 1.1, "mlx-8bit": 1.1, "FP8": 1.1,
            "FP4": 0.58, "NVFP4": 0.58, "MXFP4": 0.58, "NF4": 0.58,
            "INT4": 0.58, "INT8": 1.1, "W4A16": 0.58, "W8A8": 1.1, "W8A16": 1.1,
            "Q4_K_M": 0.6}
    bpp = _BPP.get(quant, 0.6)
    vram = round(pb * bpp + 0.5, 1)
    entry = {
        "name": name,
        "provider": provider,
        "parameter_count": f"{round(pb, 1)}B",
        "parameters_raw": total,
        "min_ram_gb": max(1.0, round(vram * 0.6, 1)),
        "recommended_ram_gb": max(2.0, round(vram * 1.2, 1)),
        "min_vram_gb": vram,
        "quantization": quant,
        "context_length": 32768,
        "use_case": "General purpose",
        "capabilities": [],
        "pipeline_tag": getattr(mi, "pipeline_tag", None) or "text-generation",
        "architecture": _arch_from_tags(getattr(mi, "tags", None)),
        "hf_downloads": getattr(mi, "downloads", 0) or 0,
        "hf_likes": getattr(mi, "likes", 0) or 0,
        "release_date": rel,
        "_discovered": True,
    }
    if active:
        entry["is_moe"] = True
        entry["active_parameters"] = active
    entry.update(overrides or {})
    # If an override set parameter_count, keep parameters_raw consistent.
    if overrides and "parameter_count" in overrides and "parameters_raw" not in overrides:
        t2, _ = _parse_params("x/" + overrides["parameter_count"])
        if t2:
            entry["parameters_raw"] = t2
    return entry


def main():
    with open(DATA_PATH, encoding="utf-8") as f:
        catalog = json.load(f)
    by_name = {m["name"]: m for m in catalog}
    existing = set(by_name)

    overwrite = "--overwrite" in sys.argv
    to_add = {}

    # Authors
    for author in AUTHORS:
        print(f"Fetching author: {author} ...", flush=True)
        models = list(api.list_models(author=author, full=True, cardData=True))
        print(f"  {len(models)} repos", flush=True)
        for mi in models:
            if mi.id in existing and not overwrite:
                continue
            ov = EXTRA_REPOS.get(mi.id)
            entry = _entry_from_modelinfo(mi, ov)
            if entry:
                to_add[mi.id] = entry

    # Explicit extra repos (not covered by an author scan)
    for repo, ov in EXTRA_REPOS.items():
        if repo in to_add:
            continue
        if repo in existing and not overwrite:
            continue
        try:
            mi = api.model_info(repo, files_metadata=False)
        except Exception as e:
            print(f"  SKIP {repo}: {e}", flush=True)
            continue
        entry = _entry_from_modelinfo(mi, ov)
        if entry:
            to_add[repo] = entry

    if not to_add:
        print("Nothing new to add.")
        return

    # Backup + merge
    with open(DATA_PATH + ".bak", "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2)
    for name, entry in to_add.items():
        by_name[name] = entry
    merged = list(by_name.values())
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)

    print(f"\nAdded/updated {len(to_add)} models. Catalog now {len(merged)} (was {len(catalog)}).")
    for n in sorted(to_add)[:20]:
        e = to_add[n]
        print(f"  + {n}  [{e['parameter_count']}, {e['quantization']}]")
    if len(to_add) > 20:
        print(f"  ... and {len(to_add) - 20} more")


if __name__ == "__main__":
    main()
