"""Tests for reference_images resolution."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from titan.reference_images import (
    aggregate_ip_weight,
    host_path_for_scheduler,
    resolve_one_reference,
    resolve_reference_images_for_scheduler,
)


def test_host_path_for_scheduler_maps_container_data():
    assert host_path_for_scheduler("/app/data/fugassa/x.png") == "/app/data/fugassa/x.png"
    import os

    os.environ["TITAN_HOST_DATA_DIR"] = "/host/titan/data"
    try:
        assert (
            host_path_for_scheduler("/app/data/fugassa/x.png")
            == "/host/titan/data/fugassa/x.png"
        )
    finally:
        os.environ.pop("TITAN_HOST_DATA_DIR", None)


def test_aggregate_ip_weight_explicit_and_mean():
    refs = [{"weight": 0.5}, {"weight": 0.9}]
    assert aggregate_ip_weight(refs, 0.3) == 0.3
    assert aggregate_ip_weight(refs, None) == pytest.approx(0.7)


def test_resolve_one_reference_b64():
    raw = base64.b64encode(b"fake").decode("ascii")
    out = resolve_one_reference({"b64": raw, "weight": 0.6}, owner=None)
    assert out is not None
    assert out["b64"] == raw
    assert out["weight"] == 0.6


def test_resolve_one_reference_path(tmp_path):
    img = tmp_path / "ref.png"
    img.write_bytes(b"\x89PNG\r\n")
    out = resolve_one_reference({"path": str(img)}, owner=None)
    assert out is not None
    assert Path(out["path"]) == img


def test_resolve_reference_images_for_scheduler_skips_missing():
    assert resolve_reference_images_for_scheduler([{"path": "/no/such/file.png"}]) == []


def test_normalize_reference_images_accepts_gallery_id():
    from titan.image_proposal import normalize_reference_images

    out = normalize_reference_images([{"gallery_id": "abc-123", "weight": 0.8}])
    assert len(out) == 1
    assert out[0]["gallery_id"] == "abc-123"
    assert out[0]["weight"] == 0.8
