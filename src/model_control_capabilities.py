"""Resolve per-model controls from cached canonical capability evidence."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from src import model_capabilities as mc


SUPPORTED_EVIDENCE_STATUSES = frozenset({mc.ASSERTION_CLAIMED, mc.ASSERTION_VERIFIED})


def serialize_catalog_records(models: Any) -> str | None:
    """Serialize records attached to a provider catalog, if it supplied any."""
    records = getattr(models, "capability_records", None)
    if records is None:
        return None
    return json.dumps([record.to_dict() for record in records])


def parse_catalog_records(value: Any) -> list[dict[str, Any]]:
    try:
        records = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return []
    return [dict(record) for record in records or [] if isinstance(record, Mapping)] if isinstance(records, list) else []


def record_for_model(records: Any, model: str) -> dict[str, Any] | None:
    model_id = str(model or "").strip()
    if not model_id:
        return None
    matches = [record for record in parse_catalog_records(records) if record.get("model_id") == model_id]
    return matches[0] if len(matches) == 1 else None


def allowed_control_values(record: Any, control: str) -> tuple[str, ...]:
    if not isinstance(record, Mapping):
        return ()
    for item in record.get("deterministic_controls") or []:
        if not isinstance(item, Mapping) or item.get("control") != control:
            continue
        if item.get("status") not in SUPPORTED_EVIDENCE_STATUSES:
            continue
        evidence = item.get("evidence")
        if not isinstance(evidence, Mapping):
            continue
        values: list[str] = []
        for raw in evidence.get("allowed_values") or []:
            value = str(raw or "").strip().lower().replace("-", "_")
            if re.fullmatch(r"[a-z][a-z0-9_]{0,31}", value) and value not in values:
                values.append(value)
        return tuple(values)
    return ()


def resolve_cached_model_record(
    *,
    endpoint_id: str | None,
    endpoint_url: str,
    model: str,
) -> dict[str, Any] | None:
    """Resolve one exact record, failing closed on missing or conflicting rows."""
    from core.database import ModelEndpoint, SessionLocal

    db = SessionLocal()
    try:
        query = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
        if endpoint_id:
            rows = query.filter(ModelEndpoint.id == endpoint_id).all()
        else:
            from src.endpoint_resolver import build_chat_url

            requested = str(endpoint_url or "").strip().rstrip("/")
            rows = [
                row
                for row in query.all()
                if requested in {
                    str(row.base_url or "").strip().rstrip("/"),
                    build_chat_url(row.base_url or "").strip().rstrip("/"),
                }
            ]
    finally:
        db.close()

    records = [
        record_for_model(getattr(row, "cached_model_capabilities", None), model)
        for row in rows
    ]
    records = [record for record in records if record is not None]
    if not records:
        return None
    encoded = {json.dumps(record, sort_keys=True, separators=(",", ":")) for record in records}
    return records[0] if len(encoded) == 1 else None
