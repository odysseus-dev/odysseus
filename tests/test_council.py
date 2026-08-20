# tests/test_council.py
"""Unit tests for Council of Models routes and logic."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routes.council_routes import setup_council_routes, CouncilMember, CouncilDiscussRequest

app = FastAPI()
app.include_router(setup_council_routes())
client = TestClient(app)


def test_council_presets_crud(tmp_path, monkeypatch):
    """Test getting and saving council presets."""
    test_presets_file = str(tmp_path / "council_presets.json")
    monkeypatch.setattr("routes.council_routes.COUNCIL_PRESETS_FILE", test_presets_file)

    # 1. Get initial presets
    res = client.get("/api/council/presets")
    assert res.status_code == 200
    data = res.json()
    assert "presets" in data
    assert isinstance(data["presets"], list)

    # 2. Save a preset
    preset_data = {
        "presets": [
            {
                "id": "preset-test-1",
                "name": "Test Council Lineup",
                "members": [
                    {"name": "Joana", "model": "qwen2.5:7b", "persona": "Architect"},
                    {"name": "Roseann", "model": "gpt-4o", "persona": "Critic"},
                ],
            }
        ]
    }
    save_res = client.post("/api/council/presets", json=preset_data)
    assert save_res.status_code == 200
    assert save_res.json().get("ok") is True

    # 3. Retrieve and verify
    get_res = client.get("/api/council/presets")
    assert get_res.status_code == 200
    presets = get_res.json()["presets"]
    assert any(p["id"] == "preset-test-1" for p in presets)


def test_council_history_crud(tmp_path, monkeypatch):
    """Test council deliberation history."""
    test_hist_file = str(tmp_path / "council_history.json")
    monkeypatch.setattr("routes.council_routes.COUNCIL_HISTORY_FILE", test_hist_file)

    # 1. Save history entry
    hist_entry = {
        "topic": "Why is Laravel good?",
        "members": [
            {"id": "m-1", "name": "Joana", "model": "qwen"},
            {"id": "m-2", "name": "Roseann", "model": "gemini"},
        ],
        "rounds": 2,
        "roundData": {
            "1": {"m-1": "Laravel provides great DX.", "m-2": "Laravel has strong ecosystem."},
            "2": {"m-1": "I agree with Roseann about ecosystem.", "m-2": "Joana makes a good point on DX."},
        },
        "synthesis": "The Council unanimously agrees Laravel excels in DX and ecosystem.",
    }
    save_res = client.post("/api/council/history", json=hist_entry)
    assert save_res.status_code == 200
    item_id = save_res.json().get("id")
    assert item_id is not None

    # 2. Verify history retrieval
    get_res = client.get("/api/council/history")
    assert get_res.status_code == 200
    items = get_res.json()["history"]
    assert any(h.get("id") == item_id for h in items)

    # 3. Delete history item
    del_res = client.delete(f"/api/council/history/{item_id}")
    assert del_res.status_code == 200
    assert del_res.json().get("ok") is True


def test_council_validation_member_bounds():
    """Test validation of min 2 and max 6 members."""
    # Less than 2 members -> should fail validation (422)
    res_single = client.post("/api/council/discuss", json={
        "topic": "Test topic",
        "members": [{"name": "Solo", "model": "gpt-4o"}],
        "rounds": 2,
    })
    assert res_single.status_code == 422

    # More than 6 members -> should fail validation (422)
    seven_members = [{"name": f"Member {i}", "model": f"model-{i}"} for i in range(7)]
    res_seven = client.post("/api/council/discuss", json={
        "topic": "Test topic",
        "members": seven_members,
        "rounds": 2,
    })
    assert res_seven.status_code == 422
