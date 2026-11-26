# src/live_vision.py
"""Local-only object labels for transient webcam frames.

This module keeps each frame in memory. It sends the frame only to a pinned
ONNX detector that runs inside the Odysseus container. It never writes a
frame, thumbnail, upload row, gallery row, chat row, or secure copy.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import threading
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

MODEL_REVISION = "4c9a10a995f309a4183e74e0f5e9963418e7462d"
MODEL_ID = "yolos-tiny:quantized-onnx@local"
MODEL_SHA256 = "82b5c1de8aaaffd763c67d67b09b742a1c53628acfee3077af3fa59233e86b6b"
MODEL_BYTES = 9661148
MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / "yolos-tiny" / MODEL_REVISION
MODEL_PATH = MODEL_DIR / "model_quantized.onnx"
CONFIG_PATH = MODEL_DIR / "config.json"

SHORT_EDGE = 512
LONG_EDGE = 1333
MOD_SIZE = 16
SCORE_THRESHOLD = 0.25
NMS_IOU = 0.50
MAX_DETECTIONS = 8

MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 1, 3)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 1, 3)

_session_lock = threading.Lock()
_infer_lock = threading.Lock()
_session = None
_id2label: dict[str, str] = {}


def _get_session():
    global _session, _id2label
    if _session is not None:
        return _session
    with _session_lock:
        if _session is not None:
            return _session

        if not MODEL_PATH.is_file() or MODEL_PATH.stat().st_size != MODEL_BYTES:
            raise RuntimeError("local vision model artifact is missing or has the wrong size")
        digest = hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()
        if digest != MODEL_SHA256:
            raise RuntimeError("local vision model artifact failed checksum verification")

        with CONFIG_PATH.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
        labels = config.get("id2label") or {}
        if not isinstance(labels, dict) or not labels:
            raise RuntimeError("local vision model labels are unavailable")
        _id2label = {str(key): str(value) for key, value in labels.items()}

        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.log_severity_level = 3
        _session = ort.InferenceSession(
            str(MODEL_PATH),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        return _session


def _resize_dimensions(width: int, height: int) -> tuple[int, int]:
    """Return YOLOS resize dimensions as (height, width)."""
    size = SHORT_EDGE
    min_side = float(min(width, height))
    max_side = float(max(width, height))
    raw_size = None
    if max_side / min_side * size > LONG_EDGE:
        raw_size = LONG_EDGE * min_side / max_side
        size = int(round(raw_size))

    if width < height:
        out_width = size
        out_height = (
            int(raw_size * height / width)
            if raw_size is not None
            else int(size * height / width)
        )
    elif (height <= width and height == size) or (width <= height and width == size):
        out_height, out_width = height, width
    else:
        out_height = size
        out_width = (
            int(raw_size * width / height)
            if raw_size is not None
            else int(size * width / height)
        )

    out_width = max(MOD_SIZE, out_width - (out_width % MOD_SIZE))
    out_height = max(MOD_SIZE, out_height - (out_height % MOD_SIZE))
    return out_height, out_width


def _preprocess(frame: bytes) -> tuple[np.ndarray, int, int, int, int, float, float]:
    with Image.open(io.BytesIO(frame)) as image:
        rgb = image.convert("RGB")
    width, height = rgb.size
    out_height, out_width = _resize_dimensions(width, height)
    resized = rgb.resize((out_width, out_height), Image.Resampling.BILINEAR)

    array = np.asarray(resized, dtype=np.float32) / 255.0
    array = (array - MEAN) / STD

    side = max(out_height, out_width)
    padded = np.zeros((side, side, 3), dtype=np.float32)
    padded[:out_height, :out_width] = array
    pixels = padded.transpose(2, 0, 1)[None].astype(np.float32)
    return pixels, width, height, side, out_width, (out_width / width), (out_height / height)


def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
    order = np.argsort(-scores)
    kept: list[int] = []
    while order.size:
        current = int(order[0])
        kept.append(current)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(boxes[current, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[current, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[current, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[current, 3], boxes[rest, 3])
        iw = np.maximum(0.0, xx2 - xx1)
        ih = np.maximum(0.0, yy2 - yy1)
        intersection = iw * ih
        area_current = max(0.0, boxes[current, 2] - boxes[current, 0]) * max(
            0.0, boxes[current, 3] - boxes[current, 1]
        )
        area_rest = np.maximum(0.0, boxes[rest, 2] - boxes[rest, 0]) * np.maximum(
            0.0, boxes[rest, 3] - boxes[rest, 1]
        )
        union = area_current + area_rest - intersection
        iou = np.divide(intersection, union, out=np.zeros_like(intersection), where=union > 0)
        order = rest[iou < threshold]
    return kept


def _detect(frame: bytes) -> list[dict]:
    session = _get_session()
    pixels, width, height, side, _, scale_x, scale_y = _preprocess(frame)
    logits, raw_boxes = session.run(None, {"pixel_values": pixels})

    probabilities = np.exp(logits[0] - logits[0].max(axis=1, keepdims=True))
    probabilities /= probabilities.sum(axis=1, keepdims=True)
    class_probabilities = probabilities[:, :-1]
    class_ids = class_probabilities.argmax(axis=1)
    scores = class_probabilities.max(axis=1)

    center_x, center_y, box_width, box_height = raw_boxes[0].T
    x1 = (center_x - box_width / 2) * side
    y1 = (center_y - box_height / 2) * side
    x2 = (center_x + box_width / 2) * side
    y2 = (center_y + box_height / 2) * side

    boxes: list[np.ndarray] = []
    box_scores: list[float] = []
    labels: list[str] = []
    for index in np.argsort(-scores)[:48]:
        score = float(scores[index])
        if score < SCORE_THRESHOLD:
            continue
        label = _id2label.get(str(int(class_ids[index])), "")
        if not label or label.lower() == "n/a":
            continue
        box = np.array(
            [x1[index] / scale_x, y1[index] / scale_y, x2[index] / scale_x, y2[index] / scale_y],
            dtype=np.float32,
        )
        box[:2] = np.maximum(box[:2], 0.0)
        box[2:] = np.minimum(box[2:], [width, height])
        if box[2] <= box[0] + 2 or box[3] <= box[1] + 2:
            continue
        boxes.append(box)
        box_scores.append(score)
        labels.append(label)

    if not boxes:
        return []

    box_array = np.vstack(boxes)
    score_array = np.asarray(box_scores, dtype=np.float32)
    kept = _nms(box_array, score_array, NMS_IOU)[:MAX_DETECTIONS]
    detections: list[dict] = []
    for index in kept:
        box = box_array[index]
        detections.append(
            {
                "label": labels[index],
                "score": round(float(score_array[index]), 3),
                "box": [
                    round(float(box[0] / width), 4),
                    round(float(box[1] / height), 4),
                    round(float(box[2] / width), 4),
                    round(float(box[3] / height), 4),
                ],
            }
        )
    return detections


def _detect_locked(frame: bytes) -> list[dict]:
    with _infer_lock:
        return _detect(frame)


async def label_jpeg_frame(
    frame: bytes,
    owner: str | None = None,
    *,
    timeout: float = 8.0,
) -> dict:
    """Return short object labels for one JPEG frame in memory.

    The owner and timeout parameters are accepted for route compatibility.
    The detector is local to the Odysseus container and has no cloud path.
    """
    del owner, timeout
    started = time.monotonic()
    detections = await asyncio.to_thread(_detect_locked, frame)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {
        "labels": [item["label"] for item in detections],
        "detections": detections,
        "model": MODEL_ID,
        "elapsed_ms": elapsed_ms,
        "local_only": True,
    }
