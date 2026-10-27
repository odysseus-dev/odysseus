import json
import os
import re
import time
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

from src.constants import DATA_DIR


HF_COLLECTIONS_URL = "https://huggingface.co/api/collections"
HF_MODELS_URL = "https://huggingface.co/api/models"
HW_FIT_CACHE_DIR = Path(DATA_DIR) / "hwfit"
MLX_COMMUNITY_CACHE = HW_FIT_CACHE_DIR / "mlx_community_models.json"
HF_COLLECTION_MODELS_CACHE = HW_FIT_CACHE_DIR / "hf_collection_models.json"
HF_COLLECTION_TTL_SECONDS = 24 * 3600


HF_COLLECTION_SOURCES = (
    {"key": "unsloth_gguf", "owner": "unsloth", "provider": "unsloth", "gguf_only": True},
    {"key": "bartowski_gguf", "owner": "bartowski", "provider": "bartowski", "gguf_only": True},
    {
        "key": "mlx_community",
        "owner": "mlx-community",
        "provider": "mlx-community",
        "repo_prefix": "mlx-community/",
        "mlx_only": True,
    },
    {
        "key": "zai_org",
        "owner": "zai-org",
        "provider": "zai-org",
    },
    {
        "key": "deepseek_ai",
        "owner": "deepseek-ai",
        "provider": "deepseek-ai",
    },
    {
        "key": "minimax_ai",
        "owner": "MiniMaxAI",
        "provider": "MiniMaxAI",
    },
    {
        "key": "qwen",
        "owner": "Qwen",
        "provider": "Qwen",
    },
    {
        "key": "stepfun_ai",
        "owner": "stepfun-ai",
        "provider": "stepfun-ai",
    },
    {
        "key": "google",
        "owner": "google",
        "provider": "google",
    },
    {
        "key": "openai",
        "owner": "openai",
        "provider": "openai",
    },
    {
        "key": "mistralai",
        "owner": "mistralai",
        "provider": "mistralai",
    },
    {
        "key": "meta_llama",
        "owner": "meta-llama",
        "provider": "meta-llama",
    },
    {
        "key": "nousresearch",
        "owner": "NousResearch",
        "provider": "NousResearch",
    },
    {
        "key": "moonshotai",
        "owner": "moonshotai",
        "provider": "moonshotai",
    },
    {
        "key": "mllama",
        "owner": "mllama",
        "provider": "mllama",
    },
)


def _format_params(raw):
    try:
        n = int(raw or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return "", 0
    if n >= 1_000_000_000_000:
        return f"{n / 1_000_000_000_000:.3g}T", n
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.4g}B", n
    if n >= 1_000_000:
        return f"{n / 1_000_000:.4g}M", n
    if n >= 1_000:
        return f"{n / 1_000:.4g}K", n
    return str(n), n


def _parse_params_from_name(repo_id):
    name = (repo_id or "").rsplit("/", 1)[-1]
    active = None
    m_active = re.search(r"[-_][Aa](\d+(?:\.\d+)?)[Bb](?![a-zA-Z])", name)
    if m_active:
        active = int(float(m_active.group(1)) * 1_000_000_000)
        name = name[: m_active.start()] + name[m_active.end() :]
    total = None
    for m in re.finditer(r"(\d+(?:\.\d+)?)[Bb](?![a-zA-Z])", name):
        total = int(float(m.group(1)) * 1_000_000_000)
        break
    if total is None:
        for m in re.finditer(r"(\d+(?:\.\d+)?)[Mm](?![a-zA-Z])", name):
            total = int(float(m.group(1)) * 1_000_000)
            break
    return total or 0, active


def _infer_quant(repo_id, source):
    name = (repo_id or "").rsplit("/", 1)[-1].lower()
    if source.get("mlx_only"):
        if "8bit" in name or "8-bit" in name:
            return "mlx-8bit"
        if "6bit" in name or "6-bit" in name:
            return "mlx-6bit"
        if "5bit" in name or "5-bit" in name:
            return "mlx-5bit"
        if "3bit" in name or "3-bit" in name:
            return "mlx-3bit"
        if re.search(r"(^|[-_/])bf16($|[-_/])", name):
            return "BF16"
        return "mlx-4bit"
    if "awq" in name and ("8bit" in name or "8-bit" in name or "int8" in name):
        return "AWQ-8bit"
    if "awq" in name or "4bit" in name or "4-bit" in name:
        return "AWQ-4bit"
    if "gptq" in name and ("8bit" in name or "8-bit" in name or "int8" in name):
        return "GPTQ-Int8"
    if "gptq" in name:
        return "GPTQ-Int4"
    if "mxfp4" in name or "nvfp4" in name or re.search(r"(^|[-_/])fp4($|[-_/])", name):
        return "FP4-MoE-Mixed"
    if "mxfp8" in name or re.search(r"(^|[-_/])fp8($|[-_/])", name):
        return "FP8-Mixed"
    if "gguf" in name or "q4_k" in name or "q4-k" in name:
        return "Q4_K_M"
    if re.search(r"(^|[-_/])bf16($|[-_/])", name):
        return "BF16"
    return "BF16"


def _quant_bytes_per_param(quant):
    return {
        "BF16": 2.2,
        "FP8": 1.15,
        "FP8-Mixed": 1.15,
        "FP4-MoE-Mixed": 0.62,
        "AWQ-4bit": 0.62,
        "AWQ-8bit": 1.15,
        "GPTQ-Int4": 0.62,
        "GPTQ-Int8": 1.15,
        "Q4_K_M": 0.62,
        "mlx-8bit": 1.25,
        "mlx-6bit": 0.95,
        "mlx-5bit": 0.82,
        "mlx-4bit": 0.70,
        "mlx-3bit": 0.55,
    }.get(quant, 2.2)


def _infer_context(repo_id, pipeline_tag):
    text = f"{repo_id or ''} {pipeline_tag or ''}".lower()
    if any(k in text for k in ("whisper", "asr", "speech-recognition", "tts", "audio", "image", "video", "diffusion")):
        return 4096
    if any(k in text for k in ("glm-5.2", "deepseek-v4", "minimax-m3")):
        return 1_000_000
    if any(k in text for k in ("qwen3", "glm", "deepseek", "minimax")):
        return 32768
    return 32768


def _infer_use_case(repo_id, pipeline_tag):
    text = f"{repo_id or ''} {pipeline_tag or ''}".lower()
    if any(k in text for k in ("whisper", "asr", "speech-recognition", "transcrib")):
        return "stt"
    if any(k in text for k in ("tts", "text-to-speech", "kokoro", "audio")):
        return "tts"
    if any(k in text for k in ("image-text", "vision", "vlm", "vl-", "ocr", "multimodal")):
        return "multimodal"
    if any(k in text for k in ("code", "coder")):
        return "coding"
    if any(k in text for k in ("reason", "thinking", "thinker", "r1")):
        return "reasoning"
    return "general"


def _entry_from_collection_item(collection, item, source):
    repo_id = item.get("id") or ""
    if item.get("type") != "model" or not repo_id:
        return None
    repo_prefix = source.get("repo_prefix")
    if repo_prefix and not repo_id.startswith(repo_prefix):
        return None
    raw_params = item.get("numParameters") or 0
    active = None
    if not raw_params:
        raw_params, active = _parse_params_from_name(repo_id)
    param_label, raw_params = _format_params(raw_params)
    if not raw_params:
        return None

    quant = _infer_quant(repo_id, source)
    pipeline_tag = item.get("pipeline_tag") or ""
    min_ram = round((raw_params / 1_000_000_000) * _quant_bytes_per_param(quant) + 0.8, 1)
    last_modified = item.get("lastModified") or collection.get("lastUpdated") or ""
    release_date = ""
    if last_modified:
        try:
            release_date = parsedate_to_datetime(last_modified).date().isoformat()
        except Exception:
            release_date = str(last_modified)[:10]

    entry = {
        "name": repo_id,
        "provider": source.get("provider") or repo_id.split("/", 1)[0],
        "parameter_count": param_label,
        "parameters_raw": raw_params,
        "min_ram_gb": min_ram,
        "recommended_ram_gb": round(min_ram * 1.3 + 0.5, 1),
        "min_vram_gb": 0.0 if source.get("mlx_only") else min_ram,
        "quantization": quant,
        "context_length": _infer_context(repo_id, pipeline_tag),
        "use_case": _infer_use_case(repo_id, pipeline_tag),
        "capabilities": ["mlx"] if source.get("mlx_only") else ["vllm", "sglang"],
        "pipeline_tag": pipeline_tag,
        "architecture": "",
        "hf_downloads": int(item.get("downloads") or 0),
        "hf_likes": int(item.get("likes") or 0),
        "release_date": release_date,
        "format": "mlx" if source.get("mlx_only") else "safetensors",
        "collection": collection.get("title") or "",
        "description": collection.get("description") or "",
        "_discovered": True,
        "_source": "hf_collections",
        "_source_owner": source.get("owner") or "",
    }
    if source.get("mlx_only"):
        entry["mlx_only"] = True
    if quant == "Q4_K_M":
        entry["is_gguf"] = True
        entry["format"] = "gguf"
        entry["capabilities"] = ["llama.cpp"]
    if active:
        entry["is_moe"] = True
        entry["active_parameters"] = active
    return entry


def _next_link(header):
    if not header:
        return None
    m = re.search(r'<([^>]+)>;\s*rel="next"', header)
    return m.group(1) if m else None


def _gguf_downloads(repo_id, siblings):
    """Supported weight files, with complete shard groups and exact quant IDs."""
    from services.hwfit.models import QUANT_HIERARCHY
    quants = QUANT_HIERARCHY + ["Q4_0", "BF16", "F16", "F32"]
    quant_re = re.compile(r"(?:^|[-_/])(" + "|".join(quants) + r")(?=[-_.\/]|$)", re.I)
    groups = {}
    for sibling in siblings or []:
        filename = sibling.get("rfilename", "") if isinstance(sibling, dict) else ""
        if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9._/-]+\.gguf", filename, re.I):
            continue
        if filename.startswith("/") or any(part in (".", "..") for part in filename.split("/")):
            continue
        if re.search(r"(?:^|/)(?:mmproj|mtp|imatrix|projector)(?:[-_./]|$)", filename, re.I):
            continue
        match = quant_re.search(filename)
        if not match:
            continue
        quant = match.group(1).upper()
        shard = re.search(r"-(\d{5})-of-(\d{5})\.gguf$", filename, re.I)
        key = (quant, filename[:shard.start()] if shard else filename)
        groups.setdefault(key, []).append((filename, shard))
    sources = {}
    for (quant, _), files in sorted(groups.items()):
        files.sort(key=lambda item: item[0])
        first, shard = files[0]
        source = {"repo": repo_id, "file": first, "quant": quant}
        if shard:
            total = int(shard.group(2))
            numbers = {int(s.group(1)) for _, s in files if s and int(s.group(2)) == total}
            if total != len(numbers) or numbers != set(range(1, total + 1)):
                continue
            source["include"] = first[:shard.start()] + f"-*-of-{total:05d}.gguf"
        sources.setdefault(quant, source)
    order = ["Q4_K_M", "Q4_0"] + quants
    return [sources.pop(q) for q in order if q in sources]


def fetch_gguf_models(source, timeout=20, max_pages=20):
    """Bulk metadata discovery; never download weights or query per base model."""
    params = urllib.parse.urlencode([
        ("author", source["owner"]), ("filter", "gguf"), ("limit", "1000"),
        *[("expand[]", key) for key in ("siblings", "gguf", "pipeline_tag", "downloads", "likes", "lastModified")],
    ])
    url = f"{HF_MODELS_URL}?{params}"
    rows = {}
    for _ in range(max_pages):
        if not url:
            break
        if not url.startswith(HF_MODELS_URL + "?"):
            raise ValueError("Unexpected Hugging Face models pagination URL")
        req = urllib.request.Request(url, headers={"User-Agent": "odysseus-hwfit/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
            url = _next_link(resp.headers.get("Link"))
        if not isinstance(payload, list):
            raise ValueError("Invalid Hugging Face models response")
        for model in payload:
            if not isinstance(model, dict):
                continue
            repo_id = model.get("id")
            if not isinstance(repo_id, str) or not repo_id.startswith(source["owner"] + "/"):
                continue
            if model.get("pipeline_tag") not in ("text-generation", "image-text-to-text"):
                continue
            downloads = _gguf_downloads(repo_id, model.get("siblings"))
            if not downloads:
                continue
            info = model.get("gguf")
            if not isinstance(info, dict):
                info = {}
            # Hub GGUF totals may describe the first auxiliary/projector file.
            # Prefer a size-bearing repo name, which also preserves MoE A*B.
            named_params, _ = _parse_params_from_name(repo_id)
            architecture = str(info.get("architecture") or "").lower()
            if not named_params and any(part in architecture for part in ("clip", "projector", "vision")):
                continue
            item = dict(model, type="model", numParameters=0 if named_params else info.get("total", 0))
            entry = _entry_from_collection_item({}, item, source)
            if entry:
                entry.update(is_gguf=True, format="gguf", capabilities=["llama.cpp"],
                             quantization=downloads[0]["quant"], gguf_sources=downloads,
                             _source="hf_models")
                rows.setdefault(repo_id, entry)
    return sorted(rows.values(), key=lambda row: row.get("hf_downloads", 0), reverse=True)


def fetch_collection_models(source, timeout=20, max_pages=20):
    params = urllib.parse.urlencode({
        "owner": source["owner"],
        "limit": "100",
        "expand": "true",
    })
    url = f"{HF_COLLECTIONS_URL}?{params}"
    models = {}
    pages = 0
    while url and pages < max_pages:
        req = urllib.request.Request(url, headers={"User-Agent": "odysseus-hwfit/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
            url = _next_link(resp.headers.get("Link"))
        pages += 1
        if not isinstance(payload, list):
            break
        for collection in payload:
            if not isinstance(collection, dict):
                continue
            for item in collection.get("items") or []:
                if not isinstance(item, dict):
                    continue
                entry = _entry_from_collection_item(collection, item, source)
                if entry and entry["name"] not in models:
                    models[entry["name"]] = entry
    rows = list(models.values())
    rows.sort(key=lambda x: (x.get("hf_downloads") or 0, x.get("release_date") or ""), reverse=True)
    return rows


def _load_cache(path):
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("models") if isinstance(data, dict) else data
        return rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []


def _write_cache(path, source, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": source,
        "fetched_at": int(time.time()),
        "count": len(rows),
        "models": rows,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_cached_mlx_community_models():
    return _load_cache(MLX_COMMUNITY_CACHE)


def load_cached_hf_collection_models():
    return _load_cache(HF_COLLECTION_MODELS_CACHE)


def _cache_fresh(path):
    try:
        return (time.time() - path.stat().st_mtime) < HF_COLLECTION_TTL_SECONDS
    except OSError:
        return False


def refresh_mlx_community_cache(force=False):
    if not force and _cache_fresh(MLX_COMMUNITY_CACHE):
        return load_cached_mlx_community_models()
    source = next(s for s in HF_COLLECTION_SOURCES if s["key"] == "mlx_community")
    rows = fetch_collection_models(source)
    _write_cache(MLX_COMMUNITY_CACHE, "https://huggingface.co/mlx-community/collections", rows)
    return rows


def refresh_hf_collection_models_cache(force=False):
    if not force and _cache_fresh(HF_COLLECTION_MODELS_CACHE):
        return load_cached_hf_collection_models()
    previous = load_cached_hf_collection_models()
    rows_by_name = {}
    succeeded = False
    for source in HF_COLLECTION_SOURCES:
        if source["key"] == "mlx_community":
            continue
        try:
            fetch = fetch_gguf_models if source.get("gguf_only") else fetch_collection_models
            for row in fetch(source):
                rows_by_name.setdefault(row["name"], row)
            succeeded = True
        except Exception:
            # Keep partial refreshes useful. A temporary DNS/provider issue for
            # one brand should not invalidate the other cached collection rows.
            for row in previous:
                if isinstance(row, dict) and row.get("_source_owner") == source["owner"] and row.get("name"):
                    rows_by_name.setdefault(row["name"], row)
    rows = sorted(
        rows_by_name.values(),
        key=lambda x: (x.get("hf_downloads") or 0, x.get("release_date") or ""),
        reverse=True,
    )
    if rows or succeeded:
        _write_cache(HF_COLLECTION_MODELS_CACHE, "https://huggingface.co", rows)
        return rows
    return load_cached_hf_collection_models()
