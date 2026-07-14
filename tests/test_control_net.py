"""Tests for ControlNet contract helpers."""

import base64
import io

from PIL import Image

from titan.control_net import normalize_control, resolve_control_for_scheduler
from titan.image_proposal import build_proposal, proposal_to_scheduler_body


def _tiny_png_b64() -> str:
    img = Image.new("RGB", (8, 8), color=(120, 80, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_normalize_control_requires_image_source():
    assert normalize_control(None) is None
    assert normalize_control({"type": "canny"}) is None


def test_normalize_control_defaults():
    out = normalize_control({"b64": _tiny_png_b64(), "weight": 0.5})
    assert out["type"] == "canny"
    assert out["weight"] == 0.5
    assert out["preprocess"] is True


def test_build_proposal_carries_control():
    prop = build_proposal(
        {
            "prompt": "scene",
            "control": {"type": "canny", "b64": _tiny_png_b64(), "weight": 0.4},
        }
    )
    assert prop["control"]["weight"] == 0.4
    body = proposal_to_scheduler_body(prop)
    assert body["control"]["type"] == "canny"


def test_resolve_control_for_scheduler_from_b64():
    resolved = resolve_control_for_scheduler({"type": "raw", "b64": _tiny_png_b64(), "preprocess": False})
    assert resolved and resolved.get("b64")
    assert len(resolved["b64"]) > 20
