"""SpecTracer ingest — store and serve element-context bundles from the extension.

The SpecTracer Chrome extension captures UI element context (hierarchy,
selector, classes, position, console/event tail) and posts it here via
POST /api/operator/spec-trace. Traces are ephemeral dev scratch context:
retained for 24 hours or the last 50 traces, whichever is smaller.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional

from services.operator.core import CAP_SPEC_TRACER, envelope

logger = logging.getLogger(__name__)

MAX_BUNDLE_BYTES = 256 * 1024
DEFAULT_MAX_TRACES = 50
DEFAULT_MAX_AGE_HOURS = 24


def _max_traces() -> int:
    try:
        return int(os.environ.get("OPERATOR_TRACE_MAX_COUNT") or DEFAULT_MAX_TRACES)
    except ValueError:
        return DEFAULT_MAX_TRACES


def _max_age() -> timedelta:
    try:
        hours = float(os.environ.get("OPERATOR_TRACE_MAX_AGE_HOURS") or DEFAULT_MAX_AGE_HOURS)
    except ValueError:
        hours = DEFAULT_MAX_AGE_HOURS
    return timedelta(hours=hours)


def _element_summary(bundle: Dict[str, Any]) -> Optional[str]:
    """Short human label: 'button.btn-primary — Join Beta'."""
    element = bundle.get("element")
    if isinstance(element, str):
        name = element
    elif isinstance(element, dict):
        name = element.get("tag") or element.get("name") or ""
    else:
        name = ""
    classes = bundle.get("classes")
    if isinstance(classes, list) and classes:
        name = f"{name}.{classes[0]}" if name else f".{classes[0]}"
    label = bundle.get("label") or ""
    summary = " — ".join(p for p in (name, label) if p)
    return summary[:200] or None


def purge_expired(db) -> int:
    """Delete traces beyond the retention window/count. Returns rows removed."""
    from core.database import OperatorTrace, utcnow_naive

    removed = 0
    cutoff = utcnow_naive() - _max_age()
    removed += (
        db.query(OperatorTrace)
        .filter(OperatorTrace.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    keep = _max_traces()
    ids = [
        row.id
        for row in db.query(OperatorTrace.id)
        .order_by(OperatorTrace.created_at.desc())
        .offset(keep)
        .all()
    ]
    if ids:
        removed += (
            db.query(OperatorTrace)
            .filter(OperatorTrace.id.in_(ids))
            .delete(synchronize_session=False)
        )
    return removed


def store_trace(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a context bundle. Raises ValueError('too_large') over the cap."""
    from core.database import OperatorTrace, SessionLocal

    raw = json.dumps(bundle, ensure_ascii=False)
    if len(raw.encode("utf-8")) > MAX_BUNDLE_BYTES:
        raise ValueError("too_large")

    db = SessionLocal()
    try:
        row = OperatorTrace(
            id=uuid.uuid4().hex,
            page_url=(bundle.get("page") or {}).get("url") if isinstance(bundle.get("page"), dict) else bundle.get("page_url") or bundle.get("url"),
            element_summary=_element_summary(bundle),
            bundle_version=str(bundle.get("bundle_version") or ""),
            bundle=raw,
        )
        db.add(row)
        purge_expired(db)
        db.commit()
        return {"trace_id": row.id}
    finally:
        db.close()


def _row_meta(row, now) -> Dict[str, Any]:
    age = max(0, int((now - row.created_at).total_seconds()))
    return {
        "trace_id": row.id,
        "page_url": row.page_url,
        "element": row.element_summary,
        "age_seconds": age,
    }


def list_traces(limit: int = 10) -> Dict[str, Any]:
    from core.database import OperatorTrace, SessionLocal, utcnow_naive

    limit = max(1, min(int(limit or 10), _max_traces()))
    db = SessionLocal()
    try:
        purge_expired(db)
        db.commit()
        rows = (
            db.query(OperatorTrace)
            .order_by(OperatorTrace.created_at.desc())
            .limit(limit)
            .all()
        )
        now = utcnow_naive()
        traces = [_row_meta(r, now) for r in rows]
        return envelope(CAP_SPEC_TRACER, True, data={"traces": traces, "count": len(traces)})
    finally:
        db.close()


def get_trace(trace_id: Optional[str] = None) -> Dict[str, Any]:
    """Fetch one bundle by id, or the most recent when trace_id is None."""
    from core.database import OperatorTrace, SessionLocal, utcnow_naive

    db = SessionLocal()
    try:
        purge_expired(db)
        db.commit()
        query = db.query(OperatorTrace)
        if trace_id:
            row = query.filter(OperatorTrace.id == trace_id).one_or_none()
        else:
            row = query.order_by(OperatorTrace.created_at.desc()).first()
        if row is None:
            reason = "trace_not_found" if trace_id else "no_traces"
            return envelope(
                CAP_SPEC_TRACER, False, reason=reason,
                hint="Send a capture from the SpecTracer extension first.",
            )
        try:
            bundle = json.loads(row.bundle)
        except json.JSONDecodeError:
            bundle = {"raw": row.bundle}
        meta = _row_meta(row, utcnow_naive())
        return envelope(CAP_SPEC_TRACER, True, data={**meta, "bundle": bundle})
    finally:
        db.close()
