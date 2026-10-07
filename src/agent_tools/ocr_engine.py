"""Bounded local OCR primitives shared by Odysseus media tools."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re

_OCR_QUERY_RE = re.compile(r"(?:\b(?:ocr|text|words?|labels?|numbers?|numbered|subtitle|receipt)\b|文字|文本|字幕|编号|数字|标签|票据)", re.I)
_NUMERIC_QUERY_RE = re.compile(r"(?:\b(?:numbers?|numbered|digits?)\b|编号|数字)", re.I)

def query_requests_ocr(query: object) -> bool:
    return bool(_OCR_QUERY_RE.search(str(query or "")))

def query_requests_numbers(query: object) -> bool:
    return bool(_NUMERIC_QUERY_RE.search(str(query or "")))

@lru_cache(maxsize=1)
def _engine():
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise RuntimeError("local OCR requires the optional rapidocr and onnxruntime packages") from exc
    return RapidOCR()

def extract_image_text(path: Path, *, include_layout: bool = False, numeric_only: bool = False,
                       min_confidence: float = 0.5, max_results: int = 512) -> dict:
    result = _engine()(str(path))
    lines, accepted = [], 0
    boxes = [] if result.boxes is None else result.boxes
    texts = [] if result.txts is None else result.txts
    scores = [] if result.scores is None else result.scores
    for box, raw_text, raw_score in zip(boxes, texts, scores):
        text, score = str(raw_text).strip(), float(raw_score)
        if not text or score < min_confidence or (numeric_only and not any(c.isdigit() for c in text)):
            continue
        accepted += 1
        if len(lines) >= max_results:
            continue
        points = [[round(float(x), 1), round(float(y), 1)] for x, y in box]
        line = {"t": text, "p": round(score, 3), "xy": [round(sum(p[0] for p in points)/len(points), 1), round(sum(p[1] for p in points)/len(points), 1)]}
        if include_layout:
            line["box"] = points
        lines.append(line)
    return {"legend": {"t": "text", "p": "confidence", "xy": "pixel center"}, "count": accepted,
            "returned": len(lines), "truncated": accepted > len(lines), "lines": lines}
