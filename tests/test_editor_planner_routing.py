"""Planner routing regression suite (HARNESS-owned).

Turns PHOTOSHOP's bench prompt families (vague, restyle, bg-remove/scene,
tone/auto, generative, structural) into assertions on the ROUTE CLASS the
server planner picks, so a future planner change cannot silently re-break
routing. The classes are:

  ask_user       -> the planner is unsure; it must ask with 2-3 choices and
                    apply NO operations (never a silent tone default)
  deterministic  -> a deterministic editor op / recipe (adjustments, filters,
                    crop, text, rotate, fill, recolour ...)
  ai_edit_image  -> a generative image-model edit (content add/remove/replace,
                    restyle, look)
  generate_image -> text-to-image for a brand-new image

These use the pure routing helpers (``_plan_special`` / ``_generative_kind`` /
``_fallback_plan``), so no model or network call is made.
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import editor_agent_routes as eaar

_STATE = {"width": 900, "height": 600}
_PROVIDER = True


@pytest.fixture(autouse=True)
def _reset_planner():
    eaar.set_editor_planner(None)
    yield
    eaar.set_editor_planner(None)


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(eaar.setup_editor_agent_routes())
    return TestClient(app)


def _classify(prompt, provider=_PROVIDER):
    """Return (route_class, ops) for a prompt using the deterministic helpers."""
    special = eaar._plan_special(prompt, _STATE, provider)
    if special:
        steps = list(special[0])
    else:
        fallback = eaar._fallback_plan(prompt)
        if fallback.get("ask_user"):
            return "ask_user", []
        steps = list(fallback.get("steps") or [])
    ops = [str(s.get("op") or "") for s in steps]
    if "generate_image" in ops:
        return "generate_image", ops
    if "ai_edit_image" in ops:
        return "ai_edit_image", ops
    return "deterministic", ops


# (prompt, expected route class)
BENCH_ROUTING = [
    # vague -> ask (never a silent tone tweak)
    ("do the thing", "ask_user"),
    ("give it some oomph", "ask_user"),
    ("make it better", "ask_user"),
    # whole-image restyle -> model edit
    ("make it look like an oil painting", "ai_edit_image"),
    ("turn it into an anime", "ai_edit_image"),
    ("make it a comic book illustration", "ai_edit_image"),
    ("make it look like cyberpunk", "ai_edit_image"),
    # background / scene replacement -> model edit
    ("put her on a beach", "ai_edit_image"),
    ("make the background nicer", "ai_edit_image"),
    ("make it snowy", "ai_edit_image"),
    # generative content add / remove -> model edit
    ("add a full moon in the sky", "ai_edit_image"),
    ("remove the distractions", "ai_edit_image"),
    ("remove the person on the left", "ai_edit_image"),
    # brand-new image -> text-to-image
    ("create a new image of a full moon", "generate_image"),
    ("generate an image of a sunset", "generate_image"),
    ("make a logo", "generate_image"),
    # tone / auto / look / structural -> deterministic
    ("make it black and white", "deterministic"),
    ("make it pop", "deterministic"),
    ("make it look professional", "deterministic"),
    ("make only the red car blue", "deterministic"),
    ("make a 1080x1350 Instagram post from this", "deterministic"),
    ("add a title TOKYO with a drop shadow", "deterministic"),
    ("extend the canvas to 16:9", "deterministic"),
    ("save as PSD with layers", "deterministic"),
    ("fill the white area with red", "deterministic"),
    ("rotate the whole image 90 degrees", "deterministic"),
]


@pytest.mark.parametrize("prompt,expected", BENCH_ROUTING)
def test_bench_prompts_route_to_expected_class(prompt, expected):
    route_class, ops = _classify(prompt)
    assert route_class == expected, f"{prompt!r} -> {route_class} {ops} (want {expected})"


def test_ask_plans_offer_two_to_three_choices():
    for prompt in ("do the thing", "give it some oomph", "make it better"):
        plan = eaar._fallback_plan(prompt)
        ask = plan.get("ask_user")
        assert isinstance(ask, dict), prompt
        opts = ask.get("options") or []
        assert 2 <= len(opts) <= 3, (prompt, opts)
        assert plan.get("steps") == []


def test_object_removal_uses_selection_plus_model_edit():
    _cls, ops = _classify("remove the person on the left")
    assert "ai_edit_image" in ops
    assert any(op.startswith("selection.") for op in ops), ops


def test_recolour_never_uses_a_flat_fill_or_a_global_tint():
    # Every recolour selects the object by description and either shifts the
    # masked hue or asks the image model for the masked region — never a flat
    # selection.fill and never a whole-image adjustments.apply.
    for prompt in ("make only the red car blue", "make the red car white", "make the flowers bright pink"):
        _cls, ops = _classify(prompt)
        assert "selection.samText" in ops, (prompt, ops)
        assert "selection.fill" not in ops, (prompt, ops)
        assert "adjustments.apply" not in ops, (prompt, ops)


def test_chromatic_recolour_is_a_deterministic_masked_hue_shift():
    cls, ops = _classify("make only the red car blue")
    assert cls == "deterministic", ops
    assert ops == ["selection.samText", "adjustments.applyMasked"], ops


def test_new_image_request_from_scratch_vs_edit_of_current_photo():
    # A photo edit ("make it a <style>") must NOT be treated as a new image.
    assert eaar._generative_kind("make it a comic book illustration") == "restyle"
    assert eaar._is_new_image_request("make it a comic book illustration") is False
    # A from-scratch generation still is.
    assert eaar._is_new_image_request("create an illustration of a cat") is True
    assert eaar._generative_kind("create an illustration of a cat") == "add"


def test_plan_route_returns_deterministic_recolour(client):
    eaar.set_editor_planner(lambda messages, owner: json.dumps({"reply": "ok", "steps": []}))
    r = client.post("/api/editor/plan", json={"prompt": "make only the red car blue", "state": _STATE})
    assert r.status_code == 200
    data = r.json()
    assert data["planner"] == "deterministic"
    assert [s["op"] for s in data["steps"]] == ["selection.samText", "adjustments.applyMasked"]


def test_plan_route_asks_for_a_vague_prompt(client):
    eaar.set_editor_planner(lambda messages, owner: json.dumps({"reply": "ok", "steps": []}))
    r = client.post("/api/editor/plan", json={"prompt": "do the thing", "state": _STATE})
    assert r.status_code == 200
    data = r.json()
    assert data.get("ask_user"), data
    assert data.get("steps") == []


def test_plan_route_routes_restyle_to_model_edit(client):
    eaar.set_editor_planner(lambda messages, owner: json.dumps({"reply": "ok", "steps": []}))
    r = client.post("/api/editor/plan", json={"prompt": "turn it into an anime", "state": _STATE})
    assert r.status_code == 200
    ops = [s["op"] for s in r.json()["steps"]]
    assert "ai_edit_image" in ops, ops
