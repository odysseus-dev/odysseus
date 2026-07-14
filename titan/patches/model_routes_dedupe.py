"""Monkey-patch model list dedupe — local paths vs basenames must not double-list."""

from __future__ import annotations

import logging

log = logging.getLogger("titan.model-dedupe")


def _model_canonical_key(model_id: str) -> str:
    m = (model_id or "").strip().replace("\\", "/")
    if not m:
        return ""
    base = m.rsplit("/", 1)[-1].lower()
    if (
        m.startswith("/")
        or m.startswith("file:")
        or "/snapshots/" in m
        or base.endswith((".gguf", ".safetensors", ".bin"))
    ):
        return f"@file:{base}"
    return m.lower()


def _canonical_model_id(model_id: str) -> str:
    m = (model_id or "").strip().replace("\\", "/")
    if not m:
        return m
    base = m.rsplit("/", 1)[-1]
    if (
        m.startswith("/")
        or "/snapshots/" in m
        or (base.endswith((".gguf", ".safetensors", ".bin")) and "/" in m)
    ):
        return base
    return m


def _merge_model_ids(*lists):
    out, seen = [], set()
    for ids in lists:
        for m in ids or []:
            if not isinstance(m, str) or not m.strip():
                continue
            key = _model_canonical_key(m)
            if key in seen:
                continue
            seen.add(key)
            out.append(_canonical_model_id(m))
    return out


def apply_model_routes_dedupe_patch() -> None:
    import routes.model_routes as mr

    mr._model_canonical_key = _model_canonical_key
    mr._canonical_model_id = _canonical_model_id
    mr._merge_model_ids = _merge_model_ids
    log.debug("model_routes dedupe patch applied")
