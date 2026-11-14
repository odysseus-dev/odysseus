"""Canonical capability records observed from endpoint model-list probes.

Endpoint model lists are cached as bare IDs, so the canonical records a probe's
``/models`` payload yields (see :mod:`src.model_capability_readers`) are kept
here, in memory, keyed by endpoint authority. A re-probe replaces the
endpoint's records. Durable evidence storage is a separate roadmap slice
(#4147); until an endpoint has been probed in this process it has no records,
and callers treat that as unknown.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from src import model_capabilities as mc
from src.model_capability_readers import ModelCapabilityRecord, records_from_payload

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# {endpoint authority (host:port, lowercased): {model id: record}}
_records: Dict[str, Dict[str, ModelCapabilityRecord]] = {}


def _authority(url: Any) -> str:
    parsed = urlparse(str(url or "").strip())
    netloc = (parsed.netloc or "").lower()
    if not netloc:
        return ""
    path = (parsed.path or "").rstrip("/")
    for suffix in ("/chat/completions", "/completions", "/models", "/v1"):
        if path.endswith(suffix):
            path = path[:-len(suffix)].rstrip("/")
    return f"{netloc}{path.lower()}"


def record_models_payload(base_url: str, payload: Any, *, endpoint_kind: str = "") -> None:
    """Normalize a probed ``/models`` payload and replace the endpoint's records."""
    key = _authority(base_url)
    if not key:
        return
    try:
        records = records_from_payload(payload, base_url=base_url, endpoint_kind=endpoint_kind)
    except Exception as exc:  # a reader bug must never break the probe
        logger.warning("Capability normalization failed for a model list: %s", exc)
        records = ()
    with _lock:
        _records[key] = {record.model_id: record for record in records}


def record_for(model: str, base_url: Optional[str] = None) -> Optional[ModelCapabilityRecord]:
    """The record for ``model``, scoped to ``base_url`` when given.

    Without ``base_url`` (callers that only know the model id) the first
    endpoint listing the model wins.
    """
    with _lock:
        if base_url:
            key = _authority(base_url)
            rec = _records.get(key, {}).get(model)
            if rec is not None:
                return rec
            netloc = (urlparse(str(base_url)).netloc or "").lower()
            if netloc and netloc != key:
                rec = _records.get(netloc, {}).get(model)
                if rec is not None:
                    return rec
            return None
        for models in _records.values():
            if model in models:
                return models[model]
    return None


def reasoning_effort_levels(model: str, base_url: Optional[str] = None) -> Tuple[str, ...]:
    """Effort levels the canonical record claims for ``model``; ``()`` when unknown."""
    record = record_for(model, base_url)
    if record is None:
        return ()
    return mc.reasoning_effort_levels(record.deterministic_controls)


def clear() -> None:
    with _lock:
        _records.clear()
