"""Bounded local OCR primitives shared by Odysseus media tools."""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import re
import tempfile

_OCR_QUERY_RE = re.compile(r"(?:\b(?:ocr|text|words?|labels?|numbers?|numbered|subtitle|receipt)\b|文字|文本|字幕|编号|数字|标签|票据)", re.I)
_NUMERIC_QUERY_RE = re.compile(r"(?:\b(?:numbers?|numbered|digits?)\b|编号|数字)", re.I)
_CJK_RE = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")
_NO_LEAD_SPACE = set(",.;:!?%)]}\u3001\u3002\uff0c\uff1b\uff1a\uff01\uff1f")
_NO_TRAIL_SPACE = set("([{$\u3008\u300a\u300c\u300e\uff08")
_MAX_SIDE_DEFAULT = 2400

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

def _prepare_image(path: Path) -> tuple[str, str | None]:
    """Downscale huge camera images; return (path_to_read, temp_path_or_None)."""
    try:
        max_side = int(os.getenv("ODYSSEUS_OCR_MAX_SIDE", str(_MAX_SIDE_DEFAULT)))
    except ValueError:
        max_side = _MAX_SIDE_DEFAULT
    if max_side <= 0:
        return str(path), None
    try:
        from PIL import Image, ImageOps
    except Exception:
        return str(path), None
    try:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            width, height = img.size
            if max(width, height) <= max_side:
                return str(path), None
            scale = max_side / float(max(width, height))
            resized = img.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.LANCZOS)
            if resized.mode not in ("RGB", "L"):
                resized = resized.convert("RGB")
            handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            handle.close()
            resized.save(handle.name, "PNG")
            resized.close()
            return handle.name, handle.name
    except Exception:
        return str(path), None

def _box_metrics(box):
    points = [(float(x), float(y)) for x, y in box]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), max(xs), min(ys), max(ys), sum(xs) / len(xs), sum(ys) / len(ys), max(1.0, max(ys) - min(ys))

def _same_line(a, b) -> bool:
    overlap = min(a["max_y"], b["max_y"]) - max(a["min_y"], b["min_y"])
    return overlap >= 0.4 * min(a["h"], b["h"]) or abs(a["cy"] - b["cy"]) <= 0.5 * min(a["h"], b["h"])

def _needs_space(prev: str, nxt: str) -> bool:
    if not prev or not nxt:
        return False
    if prev[-1] in _NO_TRAIL_SPACE or nxt[0] in _NO_LEAD_SPACE:
        return False
    if _CJK_RE.match(prev[-1]) or _CJK_RE.match(nxt[0]):
        return False
    return True

def _join_line(parts: list[str]) -> str:
    out = ""
    for raw in parts:
        text = re.sub(r"[ \t]+", " ", str(raw or "")).strip()
        if not text:
            continue
        out = text if not out else (out + (" " if _needs_space(out, text) else "") + text)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\s+([,.;:!?%)\]])", r"\1", out)
    return out.strip()

def _reading_text(entries: list[dict]) -> str:
    rows: list[dict] = []
    for entry in sorted(entries, key=lambda item: (item["cy"], item["min_x"])):
        for row in rows:
            if _same_line(entry, row):
                row["items"].append(entry)
                row["cy"] = sum(item["cy"] for item in row["items"]) / len(row["items"])
                row["h"] = max(row["h"], entry["h"])
                row["max_y"] = max(row["max_y"], entry["max_y"])
                row["min_y"] = min(row["min_y"], entry["min_y"])
                break
        else:
            rows.append({"cy": entry["cy"], "h": entry["h"], "min_y": entry["min_y"], "max_y": entry["max_y"], "items": [entry]})
    rows.sort(key=lambda row: row["cy"])
    lines = []
    for row in rows:
        row["items"].sort(key=lambda item: item["min_x"])
        text = _join_line([item["t"] for item in row["items"]])
        if text:
            lines.append(text)
    return "\n".join(lines)

def extract_image_text(path: Path, *, include_layout: bool = False, numeric_only: bool = False,
                       min_confidence: float = 0.5, max_results: int = 512) -> dict:
    read_path, temp_path = _prepare_image(path)
    try:
        result = _engine()(read_path)
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
    lines, entries, accepted = [], [], 0
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
        metrics = _box_metrics(box)
        line = {"t": text, "p": round(score, 3), "xy": [round(metrics[4], 1), round(metrics[5], 1)]}
        if include_layout:
            line["box"] = points
        lines.append(line)
        entries.append({"t": text, "p": score, "min_x": metrics[0], "max_x": metrics[1],
                        "min_y": metrics[2], "max_y": metrics[3], "cy": metrics[5], "h": metrics[6]})
    text = _reading_text(entries)
    return {"legend": {"t": "text", "p": "confidence", "xy": "pixel center"}, "count": accepted,
            "returned": len(lines), "truncated": accepted > len(lines), "lines": lines, "text": text}
