"""Lightweight, bounded performance instrumentation for email operations.

Measures fetching (`list`), searching (`search`), body loading (`read_body`),
attachment metadata/downloads (`attachments_list`, `attachment_download`),
sending (`send`), and manual frontend refresh behavior (`refresh`) without
persisting PII, subjects, or email addresses.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
from typing import Any, Deque, Dict, Optional

DEFAULT_WINDOW_SIZE = 256

KNOWN_OPERATIONS = (
    "list",
    "refresh",
    "search",
    "read_body",
    "attachments_list",
    "attachment_download",
    "send",
)


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = rank - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


@dataclass
class _OpStats:
    samples_ms: Deque[float] = field(default_factory=lambda: deque(maxlen=DEFAULT_WINDOW_SIZE))
    count: int = 0
    error_count: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    cache_bypasses: int = 0
    last_ms: float = 0.0
    last_cache_status: Optional[str] = None


class EmailPerfTracker:
    """Thread-safe sliding-window latency and cache-efficiency collector."""

    def __init__(self, window_size: int = DEFAULT_WINDOW_SIZE) -> None:
        self._window_size = max(16, int(window_size))
        self._lock = threading.Lock()
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._ops: Dict[str, _OpStats] = {
            op: _OpStats(samples_ms=deque(maxlen=self._window_size))
            for op in KNOWN_OPERATIONS
        }

    def record(
        self,
        operation: str,
        duration_ms: float,
        *,
        cache_status: Optional[str] = None,
        ok: bool = True,
    ) -> None:
        op_key = (operation or "other").strip().lower()
        val = max(0.0, float(duration_ms))
        with self._lock:
            st = self._ops.get(op_key)
            if st is None:
                st = _OpStats(samples_ms=deque(maxlen=self._window_size))
                self._ops[op_key] = st
            st.count += 1
            if not ok:
                st.error_count += 1
            st.samples_ms.append(val)
            st.last_ms = round(val, 2)
            st.last_cache_status = cache_status
            if cache_status == "hit":
                st.cache_hits += 1
            elif cache_status == "miss":
                st.cache_misses += 1
            elif cache_status == "bypass":
                st.cache_bypasses += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            operations: Dict[str, Any] = {}
            total_requests = 0
            total_errors = 0
            total_hits = 0
            total_misses = 0

            for op, st in self._ops.items():
                vals = sorted(st.samples_ms)
                avg_ms = round(sum(vals) / len(vals), 2) if vals else 0.0
                p50_ms = round(_percentile(vals, 50.0), 2)
                p95_ms = round(_percentile(vals, 95.0), 2)
                max_ms = round(vals[-1], 2) if vals else 0.0
                cache_lookups = st.cache_hits + st.cache_misses
                hit_rate = round(st.cache_hits / cache_lookups, 4) if cache_lookups > 0 else None

                operations[op] = {
                    "count": st.count,
                    "error_count": st.error_count,
                    "window_samples": len(vals),
                    "avg_ms": avg_ms,
                    "p50_ms": p50_ms,
                    "p95_ms": p95_ms,
                    "max_ms": max_ms,
                    "last_ms": st.last_ms,
                    "cache": {
                        "hits": st.cache_hits,
                        "misses": st.cache_misses,
                        "bypasses": st.cache_bypasses,
                        "hit_rate": hit_rate,
                        "last_status": st.last_cache_status,
                    },
                }
                total_requests += st.count
                total_errors += st.error_count
                total_hits += st.cache_hits
                total_misses += st.cache_misses

            overall_lookups = total_hits + total_misses
            return {
                "started_at": self._started_at,
                "window_size": self._window_size,
                "summary": {
                    "total_operations": total_requests,
                    "total_errors": total_errors,
                    "cache_hits": total_hits,
                    "cache_misses": total_misses,
                    "overall_cache_hit_rate": (
                        round(total_hits / overall_lookups, 4) if overall_lookups > 0 else None
                    ),
                },
                "operations": operations,
            }

    def reset(self) -> Dict[str, Any]:
        with self._lock:
            self._started_at = datetime.now(timezone.utc).isoformat()
            for st in self._ops.values():
                st.samples_ms.clear()
                st.count = 0
                st.error_count = 0
                st.cache_hits = 0
                st.cache_misses = 0
                st.cache_bypasses = 0
                st.last_ms = 0.0
                st.last_cache_status = None
        return self.snapshot()


EMAIL_PERF = EmailPerfTracker()


def record_email_perf(
    operation: str,
    duration_ms: float,
    *,
    cache_status: Optional[str] = None,
    ok: bool = True,
) -> None:
    EMAIL_PERF.record(operation, duration_ms, cache_status=cache_status, ok=ok)
