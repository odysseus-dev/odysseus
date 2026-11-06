"""`vlm_verified` reports a real round trip, never a declared capability.

Two different claims must not be conflated:

  * `modality: "vision"` — the model says it takes images. Catalog truth.
  * `vlm_verified` — this checkpoint has actually answered a question about an
    image on this host, recorded by scripts/smoke_mlx_vlm.py after a real
    request. A server that came up healthy proves nothing: that is precisely
    the bug this whole path exists to prevent, and it would be reintroduced by
    treating "launched" as "works".
"""

import json

import pytest

from services.hwfit import vision
from services.hwfit.fit import analyze_model
from services.hwfit.models import _normalize_model_entry


@pytest.fixture(autouse=True)
def _clear_cache():
    vision._VERIFIED_CACHE.update({"mtime": None, "models": {}})
    yield
    vision._VERIFIED_CACHE.update({"mtime": None, "models": {}})


def _metal_system():
    return {
        "has_gpu": True, "backend": "metal", "gpu_name": "Apple M4 Max",
        "gpu_vram_gb": 96.0, "gpu_count": 1,
        "available_ram_gb": 89.6, "total_ram_gb": 128.0, "unified_memory": True,
    }


def _vision_model(name="mlx-community/Qwen2.5-VL-3B-Instruct-4bit"):
    return _normalize_model_entry({
        "name": name,
        "pipeline_tag": "image-text-to-text",
        "parameter_count": "3B",
        "parameters_raw": 3_000_000_000,
        "quantization": "mlx-4bit",
        "context_length": 32768,
        "format": "mlx",
    })


def _point_at(tmp_path, monkeypatch, payload):
    path = tmp_path / "mlx_vlm_verified.json"
    if payload is not None:
        path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path))
    return path


def test_no_file_means_nothing_is_verified(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch, None)
    assert vision.verified_vlm_models() == {}
    fit = analyze_model(_vision_model(), _metal_system())
    assert fit["modality"] == "vision"      # the model still declares it
    assert fit["vlm_verified"] is False     # but nothing has been proven


def test_a_recorded_checkpoint_is_reported_verified(tmp_path, monkeypatch):
    name = "mlx-community/Qwen2.5-VL-3B-Instruct-4bit"
    _point_at(tmp_path, monkeypatch, {name: {"verified_at": "2026-10-19T23:12:17+00:00"}})
    fit = analyze_model(_vision_model(name), _metal_system())
    assert fit["vlm_verified"] is True


def test_verification_is_per_checkpoint_not_per_family(tmp_path, monkeypatch):
    # The 3B having worked says nothing about the 72B, or about a different
    # quantization of the same model.
    _point_at(tmp_path, monkeypatch,
              {"mlx-community/Qwen2.5-VL-3B-Instruct-4bit": {"verified_at": "x"}})
    other = analyze_model(_vision_model("mlx-community/Qwen2.5-VL-72B-Instruct-4bit"),
                          _metal_system())
    assert other["modality"] == "vision"
    assert other["vlm_verified"] is False


def test_a_corrupt_state_file_is_not_fatal(tmp_path, monkeypatch):
    path = _point_at(tmp_path, monkeypatch, None)
    path.write_text("{ this is not json", encoding="utf-8")
    assert vision.verified_vlm_models() == {}
    assert analyze_model(_vision_model(), _metal_system())["vlm_verified"] is False


def test_the_file_is_reread_when_it_changes(tmp_path, monkeypatch):
    name = "mlx-community/Qwen2.5-VL-3B-Instruct-4bit"
    path = _point_at(tmp_path, monkeypatch, {})
    assert vision.verified_vlm_models() == {}
    # The smoke script records a pass while the app is running; the next read
    # must see it rather than serve a stale cache.
    path.write_text(json.dumps({name: {"verified_at": "y"}}), encoding="utf-8")
    import os
    os.utime(path, (0, 0))  # force a different mtime, whatever the clock did
    assert name in vision.verified_vlm_models()
