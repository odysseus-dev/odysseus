"""Safe chart artifact tool for agent-generated charts.

The model supplies a declarative JSON chart spec. This module validates and
normalizes that spec, then returns it to the frontend as ``chart_spec``. Chart
rendering happens in the browser so the result can use the active Odysseus
theme and stay interactive without executing model-written Python code.
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional


_CHART_TYPES = {"line", "scatter", "bar", "area", "histogram", "pie"}
_MAX_SERIES = 12
_MAX_POINTS = 10_000
_MAX_LABEL_CHARS = 160


def _parse_spec(content: str) -> Dict[str, Any]:
    raw = (content or "").strip()
    if not raw:
        raise ValueError("plot_chart expects a JSON object with chart data")
    try:
        spec = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("plot_chart expects valid JSON, not Python code") from exc
    if not isinstance(spec, dict):
        raise ValueError("plot_chart expects a JSON object")
    return spec


def _text(value: Any, default: str = "", max_len: int = _MAX_LABEL_CHARS) -> str:
    if value is None:
        return default
    text = str(value).strip()
    if len(text) > max_len:
        text = text[: max_len - 1] + "..."
    return text


def _float(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean values are not valid numeric chart data")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"non-numeric chart value: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError("chart values must be finite numbers")
    return number


def _numbers(values: Any, field: str, *, required: bool = True) -> List[float]:
    if values is None:
        if required:
            raise ValueError(f"{field} is required")
        return []
    if not isinstance(values, list):
        raise ValueError(f"{field} must be an array")
    return [_float(v) for v in values]


def _x_values(values: Any, fallback_count: int) -> List[Any]:
    if values is None:
        return list(range(1, fallback_count + 1))
    if not isinstance(values, list):
        raise ValueError("x must be an array")
    out: List[Any] = []
    for value in values:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append(_float(value))
        else:
            out.append(_text(value, max_len=80))
    return out


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _series(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_series = spec.get("series")
    if raw_series is None:
        y = _numbers(_first_present(spec.get("y"), spec.get("values")), "y")
        raw_series = [{
            "name": spec.get("label") or spec.get("name") or "Series 1",
            "x": _first_present(spec.get("x"), spec.get("labels")),
            "y": y,
        }]
    if not isinstance(raw_series, list) or not raw_series:
        raise ValueError("series must be a non-empty array")
    if len(raw_series) > _MAX_SERIES:
        raise ValueError(f"plot_chart supports at most {_MAX_SERIES} series")

    parsed: List[Dict[str, Any]] = []
    total_points = 0
    shared_x = _first_present(spec.get("x"), spec.get("labels"))
    for idx, item in enumerate(raw_series, start=1):
        if not isinstance(item, dict):
            raise ValueError("each series must be an object")
        y = _numbers(_first_present(item.get("y"), item.get("values")), f"series[{idx}].y")
        if not y:
            raise ValueError(f"series[{idx}].y must contain at least one value")
        x = _x_values(_first_present(item.get("x"), item.get("labels"), shared_x), len(y))
        if len(x) != len(y):
            raise ValueError(f"series[{idx}] x and y arrays must have the same length")
        total_points += len(y)
        if total_points > _MAX_POINTS:
            raise ValueError(f"plot_chart supports at most {_MAX_POINTS} total points")
        entry = {
            "name": _text(item.get("name") or item.get("label") or f"Series {idx}", max_len=80),
            "x": x,
            "y": y,
        }
        color = _text(item.get("color"), max_len=40)
        marker = _text(item.get("marker"), max_len=8)
        if color:
            entry["color"] = color
        if marker:
            entry["marker"] = marker
        parsed.append(entry)
    return parsed


def _chart_type(spec: Dict[str, Any]) -> str:
    chart_type = _text(spec.get("chart_type") or spec.get("type"), "line", max_len=40).lower()
    if chart_type not in _CHART_TYPES:
        allowed = ", ".join(sorted(_CHART_TYPES))
        raise ValueError(f"Unsupported chart_type {chart_type!r}; use one of {allowed}")
    return chart_type


def _size(spec: Dict[str, Any]) -> Optional[Dict[str, float]]:
    raw = spec.get("size")
    if not isinstance(raw, dict):
        return None
    width = min(max(_float(raw.get("width", 760)), 320), 1400)
    height = min(max(_float(raw.get("height", 420)), 220), 900)
    return {"width": width, "height": height}


def _normalize_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    chart_type = _chart_type(spec)
    normalized: Dict[str, Any] = {
        "version": 1,
        "chart_type": chart_type,
        "title": _text(spec.get("title"), f"{chart_type.title()} chart", max_len=180),
        "x_label": _text(spec.get("x_label") or spec.get("xlabel"), max_len=120),
        "y_label": _text(spec.get("y_label") or spec.get("ylabel"), max_len=120),
        "grid": spec.get("grid", True) is not False,
        "legend": spec.get("legend", True) is not False,
    }
    size = _size(spec)
    if size:
        normalized["size"] = size

    if chart_type == "pie":
        values = _numbers(_first_present(spec.get("values"), spec.get("y")), "values")
        if not values:
            raise ValueError("values must contain at least one value for pie charts")
        if any(v < 0 for v in values) or sum(values) <= 0:
            raise ValueError("pie chart values must be non-negative and sum to more than zero")
        labels_raw = _first_present(spec.get("labels"), spec.get("x"), [])
        if not isinstance(labels_raw, list):
            raise ValueError("labels must be an array for pie charts")
        if labels_raw and len(labels_raw) != len(values):
            raise ValueError("labels and values arrays must have the same length")
        labels_source = labels_raw or list(range(1, len(values) + 1))
        normalized["labels"] = [_text(v, max_len=80) for v in labels_source]
        normalized["values"] = values
    elif chart_type == "histogram":
        values = _numbers(_first_present(spec.get("values"), spec.get("y")), "values")
        if not values:
            raise ValueError("values must contain at least one value for histogram charts")
        if len(values) > _MAX_POINTS:
            raise ValueError(f"plot_chart supports at most {_MAX_POINTS} total points")
        normalized["values"] = values
        normalized["bins"] = int(min(max(_float(spec.get("bins", 20)), 1), 100))
    else:
        normalized["series"] = _series(spec)

    return normalized


async def do_plot_chart(
    content: str,
    *,
    session_id: Optional[str] = None,
    owner: Optional[str] = None,
) -> Dict[str, Any]:
    del session_id, owner
    try:
        chart_spec = _normalize_spec(_parse_spec(content))
        chart_type = chart_spec["chart_type"]
        title = chart_spec.get("title") or f"{chart_type.title()} chart"
        return {
            "results": f"Prepared interactive {chart_type} chart: {title}",
            "chart_spec": chart_spec,
            "exit_code": 0,
        }
    except Exception as exc:
        return {"error": f"plot_chart failed: {exc}", "exit_code": 1}
