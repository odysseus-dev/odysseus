"""Persist and retrieve the dynamic hardware-fit model catalog."""

from __future__ import annotations

import re
from typing import Iterable
from uuid import uuid4

from sqlalchemy.orm import Session

from core.database import DiscoveredModel, get_db_session, utcnow_naive
from services.hwfit.models import get_models as get_static_models
from services.hwfit.models import infer_quantization_from_name
from services.hwfit.models import params_b


SOURCE_PRIORITY = {
    "hf_trending": 1,
    "ollama": 2,
    "lmstudio": 3,
    "local_gguf": 4,
}

HF_TRENDING_PIPELINES = ("text-generation", "image-text-to-text", "any-to-any")
HF_TRENDING_REFRESH_INTERVAL_S = 6 * 60 * 60
_EXCLUDE_TAG_SUBSTRINGS = (
    "lora",
    "adapter",
    "peft",
    "qlora",
    "dataset",
    "embeddings",
    "merge",
    "control-lora",
    "diffusion-lora",
    "stable-diffusion-lora",
    "text-classification",
    "token-classification",
    "feature-extraction",
    "sentence-similarity",
)
_EXCLUDE_NAME_SUBSTRINGS = (
    "lora",
    "adapter",
    "peft",
    "qlora",
    "embedding",
    "embed-",
    "dataset",
)


def normalize_catalog_entry(entry: dict, source: str | None = None) -> dict:
    """Return a ranker-compatible catalog entry with normalized source fields."""
    model = dict(entry or {})
    resolved_source = source or model.get("_source") or model.get("source") or "hf_trending"
    model["_source"] = resolved_source
    model["source"] = resolved_source
    if not model.get("quantization") and model.get("quant"):
        model["quantization"] = model["quant"]
    if not model.get("quant") and model.get("quantization"):
        model["quant"] = model["quantization"]
    if "params_b" not in model:
        model["params_b"] = params_b(model) or None
    return model


def upsert_discovered_model(db: Session, entry: dict, source: str | None = None) -> DiscoveredModel:
    """Insert or update one discovered model using source-priority semantics."""
    model = normalize_catalog_entry(entry, source=source)
    name = str(model.get("name") or "").strip()
    if not name:
        raise ValueError("discovered model entry requires a name")

    now = utcnow_naive()
    incoming_source = model.get("_source") or "hf_trending"
    row = db.query(DiscoveredModel).filter(DiscoveredModel.name == name).first()
    if row is None:
        row = DiscoveredModel(id=str(uuid4()), name=name)
        db.add(row)
        should_replace = True
    else:
        should_replace = _source_priority(incoming_source) >= _source_priority(row.source)

    if should_replace:
        _apply_entry(row, model)
    else:
        _fill_missing_entry(row, model)
    row.last_seen = now
    db.flush()
    return row


def upsert_discovered_models(db: Session, entries: Iterable[dict], source: str | None = None) -> list[DiscoveredModel]:
    """Upsert multiple discovered models and return the affected rows."""
    rows = []
    for entry in entries:
        rows.append(upsert_discovered_model(db, entry, source=source))
    return rows


def seed_static_catalog(db: Session) -> int:
    """Seed the discovered-model table from hf_models.json when it is empty."""
    if db.query(DiscoveredModel.id).first() is not None:
        return 0
    rows = upsert_discovered_models(db, get_static_models(), source="hf_trending")
    return len(rows)


def get_discovered_catalog(db: Session, seed_if_empty: bool = True) -> list[dict]:
    """Return persisted catalog rows as dictionaries accepted by rank_models()."""
    if seed_if_empty:
        seed_static_catalog(db)
    rows = db.query(DiscoveredModel).order_by(DiscoveredModel.name.asc()).all()
    return [discovered_model_to_dict(row) for row in rows]


def get_catalog_or_static(seed_if_empty: bool = True) -> list[dict]:
    """Return the DB-backed catalog, falling back to static JSON on DB errors."""
    try:
        with get_db_session() as db:
            catalog = get_discovered_catalog(db, seed_if_empty=seed_if_empty)
            return catalog or [normalize_catalog_entry(m, source="hf_trending") for m in get_static_models()]
    except Exception:
        return [normalize_catalog_entry(m, source="hf_trending") for m in get_static_models()]


async def fetch_hf_trending_catalog(limit: int = 300, pipelines: Iterable[str] | None = None) -> list[dict]:
    """Fetch Hugging Face trending LLM entries in ranker-compatible form."""
    import httpx

    selected_pipelines = tuple(pipelines or HF_TRENDING_PIPELINES)
    pool_size = max(limit, 100)
    raw: list[dict] = []
    seen_repos: set[str] = set()
    async with httpx.AsyncClient(timeout=15) as client:
        for pipeline in selected_pipelines:
            url = (
                "https://huggingface.co/api/models"
                f"?sort=trendingScore&direction=-1&limit={pool_size}&filter={pipeline}"
            )
            response = await client.get(url)
            if response.status_code != 200:
                continue
            for item in response.json():
                repo_id = item.get("modelId") or item.get("id") or ""
                if not repo_id or repo_id in seen_repos:
                    continue
                seen_repos.add(repo_id)
                raw.append(item)

    raw.sort(key=lambda item: item.get("trendingScore", 0) or 0, reverse=True)
    allowed_pipelines = set(selected_pipelines)
    entries = []
    for item in raw:
        entry = _hf_api_item_to_catalog_entry(item, allowed_pipelines)
        if not entry:
            continue
        entries.append(entry)
        if len(entries) >= limit:
            break
    return entries


async def refresh_hf_trending_catalog(limit: int = 300) -> int:
    """Fetch HF trending models and upsert them into the discovered catalog."""
    entries = await fetch_hf_trending_catalog(limit=limit)
    if not entries:
        return 0
    with get_db_session() as db:
        rows = upsert_discovered_models(db, entries, source="hf_trending")
        db.commit()
        return len(rows)


def refresh_live_catalog_sources(host: str = "") -> int:
    """Scan live local sources and persist them into the discovered catalog."""
    from services.hwfit.lmstudio_catalog import fetch_lmstudio_models
    from services.hwfit.local_scanner import scan_local_gguf
    from services.hwfit.ollama_catalog import fetch_ollama_models

    entries = []
    if not host:
        entries.extend(scan_local_gguf())
    entries.extend(fetch_lmstudio_models(host=host))
    entries.extend(fetch_ollama_models(host=host))
    if not entries:
        return 0
    with get_db_session() as db:
        rows = upsert_discovered_models(db, entries)
        db.commit()
        return len(rows)


def discovered_model_to_dict(row: DiscoveredModel) -> dict:
    """Convert one persisted catalog row into a ranker-compatible dictionary."""
    model = {
        "name": row.name,
        "provider": row.provider,
        "parameter_count": row.parameter_count,
        "params_b": row.params_b,
        "quantization": row.quantization or "",
        "quant": row.quantization or "",
        "pipeline_tag": row.pipeline_tag,
        "source": row.source,
        "_source": row.source,
        "gguf_sources": row.gguf_sources or [],
        "capabilities": row.capabilities or [],
        "context_length": row.context_length,
        "context": row.context_length,
        "release_date": row.release_date or "",
        "backend": row.backend or "",
        "local_path": row.local_path or "",
        "mmproj_path": row.mmproj_path or "",
        "architecture": row.architecture or "",
        "format": row.format or "",
    }
    if model["gguf_sources"]:
        model["is_gguf"] = True
    return model


def _apply_entry(row: DiscoveredModel, model: dict) -> None:
    """Overwrite a row with incoming normalized model metadata."""
    row.provider = model.get("provider")
    row.parameter_count = model.get("parameter_count")
    row.params_b = model.get("params_b")
    row.quantization = model.get("quantization") or model.get("quant")
    row.pipeline_tag = model.get("pipeline_tag")
    row.source = model.get("_source") or model.get("source") or "hf_trending"
    row.gguf_sources = list(model.get("gguf_sources") or [])
    row.capabilities = list(model.get("capabilities") or [])
    row.context_length = _int_or_none(model.get("context_length") or model.get("context"))
    row.release_date = model.get("release_date") or ""
    row.backend = model.get("backend") or ""
    row.local_path = model.get("local_path") or ""
    row.mmproj_path = model.get("mmproj_path") or ""
    row.architecture = model.get("architecture") or ""
    row.format = model.get("format") or ""


def _fill_missing_entry(row: DiscoveredModel, model: dict) -> None:
    """Fill sparse fields without demoting a higher-priority source row."""
    row.provider = row.provider or model.get("provider")
    row.parameter_count = row.parameter_count or model.get("parameter_count")
    row.params_b = row.params_b or model.get("params_b")
    row.quantization = row.quantization or model.get("quantization") or model.get("quant")
    row.pipeline_tag = row.pipeline_tag or model.get("pipeline_tag")
    row.gguf_sources = row.gguf_sources or list(model.get("gguf_sources") or [])
    row.capabilities = row.capabilities or list(model.get("capabilities") or [])
    row.context_length = row.context_length or _int_or_none(model.get("context_length") or model.get("context"))
    row.release_date = row.release_date or model.get("release_date") or ""
    row.backend = row.backend or model.get("backend") or ""
    row.local_path = row.local_path or model.get("local_path") or ""
    row.mmproj_path = row.mmproj_path or model.get("mmproj_path") or ""
    row.architecture = row.architecture or model.get("architecture") or ""
    row.format = row.format or model.get("format") or ""


def _source_priority(source: str | None) -> int:
    """Return catalog source precedence."""
    return SOURCE_PRIORITY.get(source or "", 0)


def _int_or_none(value: object) -> int | None:
    """Convert a numeric-looking value to int, otherwise None."""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _hf_api_item_to_catalog_entry(item: dict, allowed_pipelines: set[str]) -> dict | None:
    """Convert one Hugging Face API item into a persisted catalog entry."""
    repo_id = str(item.get("modelId") or item.get("id") or "").strip()
    if not repo_id:
        return None
    tags = item.get("tags") or []
    pipeline_tag = item.get("pipeline_tag") or ""
    if pipeline_tag and pipeline_tag not in allowed_pipelines:
        return None
    if _is_excluded_hf_repo(repo_id, tags):
        return None

    parameter_count = _infer_parameter_count(repo_id)
    quantization = infer_quantization_from_name(repo_id)
    tag_text = " ".join(str(tag) for tag in tags)
    is_gguf = "gguf" in repo_id.lower() or "gguf" in tag_text.lower()
    capabilities = []
    if pipeline_tag in {"image-text-to-text", "any-to-any"}:
        capabilities.append("vision")

    entry = {
        "name": repo_id,
        "provider": repo_id.split("/", 1)[0] if "/" in repo_id else "Hugging Face",
        "parameter_count": parameter_count,
        "quantization": quantization or "",
        "pipeline_tag": pipeline_tag,
        "source": "hf_trending",
        "_source": "hf_trending",
        "gguf_sources": [{"repo": repo_id, "kind": "GGUF"}] if is_gguf else [],
        "capabilities": capabilities,
        "release_date": item.get("createdAt") or "",
        "backend": "llamacpp" if is_gguf else "",
    }
    if is_gguf:
        entry["is_gguf"] = True
    return entry


def _infer_parameter_count(repo_id: str) -> str:
    """Infer a catalog parameter count like 7B from a Hugging Face repo id."""
    match = re.search(r"(?i)(?:^|[-_/])(\d+(?:\.\d+)?)\s*([BM])(?:$|[-_/])", repo_id)
    if not match:
        return ""
    return f"{float(match.group(1)):g}{match.group(2).upper()}"


def _is_excluded_hf_repo(repo_id: str, tags: list[str]) -> bool:
    """Return True for HF artifacts that are not standalone runnable models."""
    text = repo_id.lower()
    if any(fragment in text for fragment in _EXCLUDE_NAME_SUBSTRINGS):
        return True
    tag_text = " ".join(str(tag).lower() for tag in tags)
    return any(fragment in tag_text for fragment in _EXCLUDE_TAG_SUBSTRINGS)
