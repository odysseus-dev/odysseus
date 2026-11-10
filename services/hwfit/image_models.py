"""Image generation model registry and VRAM fitting for Cookbook."""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from src.constants import DATA_DIR

# Image models are discovered from HuggingFace collections/search and local cache.
# Keep this empty: source-coded repo IDs become hidden recommendations.
IMAGE_MODEL_REGISTRY: list[dict[str, Any]] = []

HF_IMAGE_COLLECTIONS = [
    "stabilityai/image",
    "stabilityai/stable-diffusion-35",
    "black-forest-labs/flux2",
]

HF_MLX_IMAGE_COLLECTIONS = [
    "mlx-community/flux2-klein-mlx",
    "mlx-community/inpainting-mlx",
    "mlx-community/ddcolor-mlx",
    "mlx-community/boogu-image-01-mlx",
]

HF_MLX_IMAGE_REPO_SEEDS: list[str] = []
HF_IMAGE_REPO_SEEDS: list[str] = []

_HF_COLLECTION_CACHE = {"ts": 0.0, "models": []}
_HF_COLLECTION_TTL = 30 * 60
_IMAGE_COLLECTION_DISK_CACHE = Path(DATA_DIR) / "hwfit" / "image_collection_models.json"
_IMAGE_COLLECTION_DISK_TTL = 24 * 3600
_HF_VARIANT_CACHE: dict[str, dict[str, str]] = {}
_HF_SEARCH_DISABLED_UNTIL = 0.0


def _repo_display_name(repo_id: str) -> str:
    name = str(repo_id or "").split("/")[-1]
    return name.replace("-", " ").replace("_", " ").strip() or repo_id


def _provider_from_repo(repo_id: str) -> str:
    owner = str(repo_id or "").split("/", 1)[0].lower()
    return {
        "stabilityai": "Stability AI",
        "black-forest-labs": "Black Forest Labs",
        "tongyi-mai": "Tongyi",
        "qwen": "Qwen",
        "mlx-community": "mlx-community",
    }.get(owner, owner.replace("-", " ").title() if owner else "HuggingFace")


def _infer_capabilities(item: dict[str, Any], repo_id: str) -> list[str]:
    tasks = set()
    pipeline = str(item.get("pipeline_tag") or "").strip().lower()
    if pipeline:
        tasks.add(pipeline)
    for provider in item.get("availableInferenceProviders") or []:
        if isinstance(provider, dict) and provider.get("task"):
            tasks.add(str(provider["task"]).strip().lower())
    text = f"{repo_id} {' '.join(tasks)}".lower()
    caps = []
    if "image-to-image" in tasks or "edit" in text or "inpaint" in text:
        caps.append("image-editing")
    if "inpaint" in text:
        caps.append("inpainting")
    if "text-to-image" in tasks or not caps:
        caps.append("text-to-image")
    return caps


def _estimate_image_model(repo_id: str) -> dict[str, Any]:
    text = str(repo_id or "").lower()
    params_b = 8.0
    param_match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*b(?:\b|[-_])", text)
    if param_match:
        params_b = max(0.01, float(param_match.group(1)))
    if any(k in text for k in ("mi-gan", "big-lama", "lama-")):
        return {"params_b": 0.01, "bf16": 1.0, "fp8": 0.7, "q4": 0.5, "quality": 65, "speed": 98, "quant": "BF16"}
    quant = "BF16"
    if any(k in text for k in ("4bit", "q4", "nf4")):
        quant = "Q4"
    elif "fp8" in text or "8bit" in text:
        quant = "FP8"
    bf16 = max(1.0, round(params_b * 2.6 + 3.0, 1))
    fp8 = max(0.7, round(params_b * 1.35 + 2.0, 1))
    q4 = max(0.5, round(params_b * 0.8 + 1.5, 1))
    speed = max(35, min(95, int(98 - params_b * 3)))
    quality = max(60, min(88, int(70 + min(params_b, 18) * 0.8)))
    return {"params_b": params_b, "bf16": bf16, "fp8": fp8, "q4": q4, "quality": quality, "speed": speed, "quant": quant}


def _params_b_from_item(item: dict[str, Any]) -> float | None:
    raw = item.get("numParameters")
    if isinstance(raw, (int, float)) and raw > 0:
        return max(0.01, round(float(raw) / 1_000_000_000.0, 3))
    return None


def _mlx_quantize_estimate(repo_id: str, est: dict[str, Any]) -> dict[str, Any]:
    text = str(repo_id or "").lower()
    out = dict(est)
    if "3bit" in text or "4bit" in text or "q4" in text:
        out["quant"] = "Q4"
        out["bf16"] = None
        out["fp8"] = None
    elif "8bit" in text:
        out["quant"] = "FP8"
        out["bf16"] = None
    elif "6bit" in text or "5bit" in text:
        out["quant"] = "Q4"
        out["bf16"] = None
        out["fp8"] = out.get("fp8") or out.get("q4")
    elif "bf16" in text or "fp16" in text:
        out["quant"] = "BF16"
        out["fp8"] = None
        out["q4"] = None
    return out


def _collection_item_to_model(item: dict[str, Any], collection_title: str = "", mlx_only: bool = False) -> dict[str, Any] | None:
    repo_id = str(item.get("id") or "").strip()
    if "/" not in repo_id:
        return None
    typ = str(item.get("type") or item.get("itemType") or "model").lower()
    if typ not in {"", "model"}:
        return None
    est = _estimate_image_model(repo_id)
    item_params_b = _params_b_from_item(item)
    if item_params_b is not None:
        est = {
            **est,
            "params_b": item_params_b,
            "bf16": max(0.5, round(item_params_b * 2.4 + 0.8, 1)),
            "fp8": max(0.5, round(item_params_b * 1.3 + 0.5, 1)),
            "q4": max(0.4, round(item_params_b * 0.8 + 0.4, 1)),
        }
    if mlx_only:
        est = _mlx_quantize_estimate(repo_id, est)
    caps = _infer_capabilities(item, repo_id)
    gated = item.get("gated")
    desc_bits = []
    if collection_title:
        desc_bits.append(f"HF collection: {collection_title}.")
    if gated:
        desc_bits.append("Gated on HuggingFace.")
    out = {
        "id": repo_id,
        "name": _repo_display_name(repo_id),
        "provider": _provider_from_repo(repo_id),
        "params_b": est["params_b"],
        "vram_bf16": est["bf16"],
        "vram_fp8": est["fp8"],
        "vram_q4": est["q4"],
        "default_quant": est["quant"],
        "quant_repos": {},
        "capabilities": caps,
        "description": " ".join(desc_bits).strip() or "Imported from HuggingFace collection.",
        "quality": est["quality"],
        "speed": est["speed"],
        "released": "",
    }
    # Optional catalog metadata may identify a non-default runtime package.
    # Keep this data-driven: the fitter must not infer private/model-specific
    # dependencies from repository names.
    dependency_package = item.get("dependency_package") or item.get("runtime_dependency")
    if isinstance(dependency_package, str) and dependency_package.strip():
        out["dependency_package"] = dependency_package.strip()
    if mlx_only:
        out["mlx_only"] = True
        out["description"] = (out["description"] + " Apple Silicon / MLX only.").strip()
    return out


def _fetch_hf_image_collection_models() -> list[dict[str, Any]]:
    now = time.time()
    if now - float(_HF_COLLECTION_CACHE.get("ts") or 0) < _HF_COLLECTION_TTL:
        return list(_HF_COLLECTION_CACHE.get("models") or [])
    # Reuse the last successful discovery across process restarts. A stale
    # catalog is preferable to blocking the first image-tab render on several
    # sequential Hugging Face requests; a later refresh replaces it.
    if not _HF_COLLECTION_CACHE.get("models"):
        try:
            cached = json.loads(_IMAGE_COLLECTION_DISK_CACHE.read_text(encoding="utf-8"))
            cached_models = cached.get("models") if isinstance(cached, dict) else None
            cached_ts = float(cached.get("fetched_at") or 0) if isinstance(cached, dict) else 0
            if isinstance(cached_models, list) and cached_models:
                _HF_COLLECTION_CACHE["ts"] = cached_ts
                _HF_COLLECTION_CACHE["models"] = cached_models
                if now - cached_ts < _IMAGE_COLLECTION_DISK_TTL:
                    return list(cached_models)
        except (OSError, ValueError, TypeError):
            pass
    models: list[dict[str, Any]] = []
    for slug, mlx_only in [(slug, False) for slug in HF_IMAGE_COLLECTIONS] + [(slug, True) for slug in HF_MLX_IMAGE_COLLECTIONS]:
        url = f"https://huggingface.co/api/collections/{slug}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Odysseus-Cookbook/1.0"})
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:
            continue
        title = str(data.get("title") or slug)
        for item in data.get("items") or []:
            if isinstance(item, dict):
                model = _collection_item_to_model(item, title, mlx_only=mlx_only)
                if model:
                    models.append(model)
    if models:
        _HF_COLLECTION_CACHE["ts"] = now
        _HF_COLLECTION_CACHE["models"] = models
        try:
            _IMAGE_COLLECTION_DISK_CACHE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _IMAGE_COLLECTION_DISK_CACHE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"fetched_at": now, "models": models}), encoding="utf-8")
            tmp.replace(_IMAGE_COLLECTION_DISK_CACHE)
        except OSError:
            pass
        return list(models)
    # Preserve stale results if the network is unavailable. The in-memory
    # timestamp prevents every subsequent ranking request from retrying it.
    if _HF_COLLECTION_CACHE.get("models"):
        _HF_COLLECTION_CACHE["ts"] = now
        return list(_HF_COLLECTION_CACHE["models"])
    _HF_COLLECTION_CACHE["ts"] = now
    return []


def _hf_model_search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    global _HF_SEARCH_DISABLED_UNTIL
    now = time.time()
    if now < _HF_SEARCH_DISABLED_UNTIL:
        return []
    url = "https://huggingface.co/api/models?" + urllib.parse.urlencode({
        "search": query,
        "limit": str(limit),
    })
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Odysseus-Cookbook/1.0"})
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return data if isinstance(data, list) else []
    except Exception:
        _HF_SEARCH_DISABLED_UNTIL = now + 10 * 60
        return []


def _variant_score(candidate: dict[str, Any], base_repo: str, want: str) -> float:
    rid = str(candidate.get("id") or candidate.get("modelId") or "")
    text = " ".join([
        rid,
        str(candidate.get("library_name") or ""),
        str(candidate.get("pipeline_tag") or ""),
        " ".join(str(t) for t in candidate.get("tags") or []),
    ]).lower()
    base = base_repo.lower()
    base_short = base_repo.rsplit("/", 1)[-1].lower()
    if want == "gguf" and "gguf" not in text:
        return -1
    if want == "fp8" and not any(k in text for k in ("fp8", "nvfp4", "mxfp8", "mxfp4")):
        return -1
    score = float(candidate.get("downloads") or 0) / 1000.0 + float(candidate.get("likes") or 0)
    if f"base_model:{base}" in text or f"base_model:quantized:{base}" in text:
        score += 10000
    elif base_short and base_short in rid.lower():
        score += 1000
    else:
        score -= 200
    if "diffusers" in text:
        score += 50
    if str(candidate.get("private")).lower() == "true":
        score -= 10000
    return score


def _best_variant_repo(base_repo: str, want: str) -> str:
    base_short = str(base_repo or "").rsplit("/", 1)[-1]
    candidates = _hf_model_search(f"{base_short} {want}", limit=12)
    scored = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        rid = str(item.get("id") or item.get("modelId") or "").strip()
        if "/" not in rid or rid.lower() == base_repo.lower():
            continue
        score = _variant_score(item, base_repo, want)
        if score >= 0:
            scored.append((score, rid))
    scored.sort(reverse=True)
    return scored[0][1] if scored else ""


def _should_discover_variants(repo_id: str) -> bool:
    return False


def _discover_quant_repos(repo_id: str, need_fp8: bool = True, need_gguf: bool = True) -> dict[str, str]:
    key = str(repo_id or "").strip()
    if not key:
        return {}
    cache_key = f"{key.lower()}|fp8={int(need_fp8)}|gguf={int(need_gguf)}"
    if cache_key in _HF_VARIANT_CACHE:
        return dict(_HF_VARIANT_CACHE[cache_key])
    found: dict[str, str] = {}
    if need_fp8:
        fp8 = _best_variant_repo(key, "fp8")
        if fp8:
            found["FP8"] = fp8
    if need_gguf:
        gguf = _best_variant_repo(key, "gguf")
        if gguf:
            # The image-model fitter's smallest bucket is Q4; most HF image GGUF
            # repos expose Q4/Q5/Q8 files under one repo, so use it as the low-VRAM
            # download source while preserving the explicit GGUF label for callers.
            found["Q4"] = gguf
            found["GGUF"] = gguf
    _HF_VARIANT_CACHE[cache_key] = found
    return dict(found)


def _merge_quant_repos(model: dict[str, Any]) -> dict[str, Any]:
    out = dict(model)
    existing = dict(out.get("quant_repos") or {})
    repo_id = str(out.get("id") or "")
    if _should_discover_variants(repo_id):
        discovered = _discover_quant_repos(
            repo_id,
            need_fp8="FP8" not in existing,
            need_gguf="Q4" not in existing and "GGUF" not in existing,
        )
        for k, v in discovered.items():
            existing.setdefault(k, v)
    out["quant_repos"] = existing
    return out


def get_image_models():
    """Return the image model registry."""
    merged = [_merge_quant_repos(m) for m in IMAGE_MODEL_REGISTRY]
    seen = {str(m.get("id") or "").lower() for m in merged if isinstance(m, dict)}
    for model in _fetch_hf_image_collection_models():
        key = str(model.get("id") or "").lower()
        if key and key not in seen:
            merged.append(_merge_quant_repos(model))
            seen.add(key)
    return merged


def _is_apple_image_system(system: dict[str, Any]) -> bool:
    backend = str(system.get("backend") or "").lower()
    gpu_name = str(system.get("gpu_name") or "").lower()
    cpu_name = str(system.get("cpu_name") or "").lower()
    platform = str(system.get("platform") or "").lower()
    return (
        bool(system.get("unified_memory"))
        or backend in {"metal", "mps", "apple"}
        or "apple" in gpu_name
        or "apple" in cpu_name
        or platform == "darwin"
    )


def rank_image_models(system, search=None, sort="fit"):
    """Score and rank image models against detected hardware.

    Returns list of models with fit info (vram needed, fits, recommended quant).
    """
    if not isinstance(system, dict):
        system = {}
    gpu_vram = system.get("gpu_vram_gb", 0) or 0
    has_gpu = system.get("has_gpu", False)
    ram_gb = system.get("available_ram_gb") or system.get("total_ram_gb") or 0
    budget_gb = gpu_vram if has_gpu and gpu_vram > 0 else ram_gb
    budget_kind = "gpu" if has_gpu and gpu_vram > 0 else "ram"
    apple_system = _is_apple_image_system(system)
    results = []

    for model in get_image_models():
        if apple_system and not (model.get("mlx_only") or model.get("apple_ok")):
            continue
        if model.get("mlx_only") and not apple_system:
            continue
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

        if budget_gb > 0:
            # Try BF16 first, then FP8, then Q4
            for q, vram_key in [("BF16", "vram_bf16"), ("FP8", "vram_fp8"), ("Q4", "vram_q4")]:
                v = model.get(vram_key)
                if v is not None and v <= budget_gb * 0.90:  # 10% headroom
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
        if budget_gb <= 0:
            fit = "no_gpu"
            fit_label = "No GPU"
        elif fits:
            headroom = budget_gb - vram_needed
            if headroom > budget_gb * 0.3:
                fit = "perfect"
                fit_label = "Perfect"
            elif headroom > budget_gb * 0.1:
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
            "fit_budget": budget_kind,
            "quality": model["quality"],
            "speed": model["speed"],
            "score": round(score, 1),
            "capabilities": model["capabilities"],
            "description": model["description"],
            "released": model.get("released", ""),
            "dependency_package": model.get("dependency_package", ""),
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
