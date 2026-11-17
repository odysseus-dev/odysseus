"""GET /api/hwfit/models must not 500 on a non-numeric gpu_count.

The handler did `n = int(gpu_count)` with no guard, so `?gpu_count=abc` (or any
non-integer) raised ValueError -> HTTP 500. A malformed count is now ignored,
matching how the neighbouring gpu_group param is already parsed.

The count parsing runs only after the empty-catalog early return, so these
tests rank the authored publication catalog against a fixed two-GPU system and
spy on rank_models to prove the parsing path actually ran.
"""
import functools
from copy import deepcopy

import pytest

from routes.hwfit_routes import setup_hwfit_routes
from tests.hwfit_publication_fixtures import publication_catalog  # noqa: F401

SYSTEM = {
    "has_gpu": True,
    "backend": "cuda",
    "gpu_name": "Synthetic GPU",
    "gpu_vram_gb": 24.0,
    "gpu_count": 2,
    "gpus": [
        {"index": 0, "name": "Synthetic GPU", "vram_gb": 12.0},
        {"index": 1, "name": "Synthetic GPU", "vram_gb": 12.0},
    ],
    "gpu_groups": [{"name": "Synthetic GPU", "vram_each": 12.0, "count": 2, "indices": [0, 1], "vram_total": 24.0}],
    "available_ram_gb": 32.0,
    "total_ram_gb": 32.0,
}


def _get_models():
    router = setup_hwfit_routes()
    for route in router.routes:
        if getattr(route, "path", "").endswith("/models") and "GET" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError("hwfit /models route not found")


@pytest.fixture
def ranked_systems(publication_catalog, monkeypatch):
    """Fix detection and record every system the handler hands to rank_models."""
    from services.hwfit import fit, hardware

    monkeypatch.setattr(hardware, "detect_system", lambda **_: deepcopy(SYSTEM))
    calls = []
    real_rank = fit.rank_models

    @functools.wraps(real_rank)
    def spy(system, **kwargs):
        calls.append(deepcopy(system))
        return real_rank(system, **kwargs)

    monkeypatch.setattr(fit, "rank_models", spy)
    return calls


def _assert_ranked(result, ranked_systems):
    assert isinstance(result, dict)
    assert "error" not in result
    assert isinstance(result["models"], list) and result["models"]
    # Reaching rank_models means the empty-catalog return was not taken and the
    # manual-hardware and gpu_count parsing ran first.
    assert len(ranked_systems) == 1
    assert ranked_systems[0] == result["system"]
    return result["system"]


def test_non_numeric_gpu_count_does_not_raise(ranked_systems):
    handler = _get_models()
    # Previously raised ValueError (HTTP 500); now degrades to a normal ranking.
    system = _assert_ranked(handler(gpu_count="abc"), ranked_systems)
    # Ignored like an omitted count: the auto pool keeps both GPUs and no
    # explicit-count GPU-only pin is applied.
    assert system["detected_gpu_count"] == 2
    assert system["active_group"]["use_count"] == 2
    assert system["gpu_count"] == 2
    assert "gpu_only" not in system


def test_numeric_gpu_count_still_accepted(ranked_systems):
    handler = _get_models()
    system = _assert_ranked(handler(gpu_count="0"), ranked_systems)
    # 0 switches to RAM-only ranking.
    assert system["detected_gpu_count"] == 2
    assert system["has_gpu"] is False
    assert system["gpu_count"] == 0
    assert system["gpu_only"] is False
    assert "active_group" not in system


def test_non_numeric_manual_gpu_count_does_not_raise(ranked_systems):
    # manual_gpu_count is the other count param on this endpoint (the hardware
    # simulator in _apply_manual_hardware). A non-numeric value must also degrade
    # (default to 1) rather than 500, so the endpoint's count parsing is fully
    # covered.
    handler = _get_models()
    system = _assert_ranked(handler(manual_mode="gpu", manual_gpu_count="abc"), ranked_systems)
    assert system["manual_hardware"] is True
    assert system["gpu_name"] == "Simulated CUDA GPU"
    assert system["gpu_count"] == 1
    assert system["gpu_groups"][0]["count"] == 1
    assert system["detected_gpu_count"] == 1


def test_empty_catalog_returns_before_count_parsing(ranked_systems, monkeypatch):
    # Control: without catalog rows the handler stops before the parsing above,
    # which is why the tests in this module must rank the authored catalog.
    from services.hwfit import models

    monkeypatch.setattr(models, "get_models", lambda: [])
    result = _get_models()(gpu_count="abc", manual_mode="gpu", manual_gpu_count="abc")
    assert result["models"] == []
    assert "Model catalog is empty" in result["error"]
    assert ranked_systems == []
    assert "detected_gpu_count" not in result["system"]
