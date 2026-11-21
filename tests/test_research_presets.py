import inspect
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.research.research_routes import setup_research_routes
from src.deep_research import DeepResearcher
from src.research_presets import (
    build_deep_researcher_kwargs,
    get_research_preset,
    list_research_presets,
    recommend_research_preset,
)


def test_research_presets_cover_small_medium_large_and_match_deep_researcher_sig():
    presets = list_research_presets()
    assert [p["tier"] for p in presets] == ["small", "medium", "large"]

    sig_params = set(inspect.signature(DeepResearcher.__init__).parameters.keys())
    for preset in presets:
        kwargs = preset["deep_researcher_params"]
        assert set(kwargs.keys()).issubset(sig_params)
        assert kwargs["min_rounds"] <= kwargs["max_rounds"]
        assert kwargs["extraction_concurrency"] >= 1

    assert presets[0]["deep_researcher_params"]["max_rounds"] < presets[1]["deep_researcher_params"]["max_rounds"]
    assert presets[1]["deep_researcher_params"]["max_rounds"] < presets[2]["deep_researcher_params"]["max_rounds"]


def test_recommend_research_preset_scales_with_vram_ram_and_model_size():
    small = recommend_research_preset(vram_gb=8.0)
    assert small["tier"] == "small"
    assert small["deep_researcher_params"]["extraction_concurrency"] == 1

    medium = recommend_research_preset(vram_gb=24.0, model_params_b=32.0)
    assert medium["tier"] == "medium"

    large = recommend_research_preset(vram_gb=80.0, model_params_b=70.0)
    assert large["tier"] == "large"

    # Large GPU running a small 8B model caps to small preset rather than over-allocating
    capped = recommend_research_preset(vram_gb=48.0, model_params_b=8.0)
    assert capped["tier"] == "small"


def test_build_deep_researcher_kwargs_instantiates_deep_researcher():
    kwargs = build_deep_researcher_kwargs("large", max_rounds=12)
    researcher = DeepResearcher(
        llm_endpoint="http://localhost:8080/v1",
        llm_model="Qwen/Qwen3-235B-A22B",
        **kwargs,
    )
    assert researcher.max_rounds == 12
    assert researcher.max_time == 600


def test_research_presets_route():
    from unittest.mock import MagicMock
    app = FastAPI()
    app.include_router(setup_research_routes(MagicMock()))
    client = TestClient(app)

    resp = client.get("/api/research/presets", params={"vram_gb": 8})
    assert resp.status_code == 200
    data = resp.json()
    assert data["recommended"]["tier"] == "small"
    assert len(data["presets"]) == 3

    specific = client.get("/api/research/presets", params={"tier": "large"})
    assert specific.status_code == 200
    assert specific.json()["preset"]["tier"] == "large"

    bad = client.get("/api/research/presets", params={"tier": "xl"})
    assert bad.status_code == 400
