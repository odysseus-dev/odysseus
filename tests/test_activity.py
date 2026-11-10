"""Tests for src/activity.py — in-flight heavy-image-op counters (safe restart)."""
from src import activity


def test_image_op_counter_increments_and_decrements():
    assert activity.heavy_image_ops_active() == 0
    activity.begin_image_op("a")
    activity.begin_image_op("a")
    assert activity.heavy_image_ops_active() == 2
    activity.end_image_op("a")
    assert activity.heavy_image_ops_active() == 1
    activity.end_image_op("a")
    assert activity.heavy_image_ops_active() == 0
    # never goes negative
    activity.end_image_op("a")
    assert activity.heavy_image_ops_active() == 0


def test_image_op_context_manager_and_busy():
    assert activity.busy() is False
    with activity.image_op("/api/image/inpaint"):
        assert activity.busy() is True
        snap = activity.activity()
        assert snap["image_ops"] == 1
        assert snap["image_ops_by_name"].get("/api/image/inpaint") == 1
    assert activity.busy() is False
    assert activity.activity()["image_ops"] == 0


def test_image_op_released_on_exception():
    try:
        with activity.image_op("boom"):
            raise RuntimeError("x")
    except RuntimeError:
        pass
    assert activity.heavy_image_ops_active() == 0
