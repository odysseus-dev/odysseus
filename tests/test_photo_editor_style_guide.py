"""Unit tests for the shared photo-editor style guide and its server wiring.

The guide (``src/editor_style_guide.py``) is the single source of truth for
intent -> ops routing, calibrated strengths and the low-key / temperature /
portrait / ambiguity rules. It feeds both the server planner and (via
``GET /api/editor/style-guide``) the browser fallback.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.editor_agent_routes as eaar
from src import editor_style_guide as guide


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


def _ops(steps):
    return [s["op"] for s in steps]


def test_new_ops_are_in_the_server_op_catalog():
    # If a new retouch/selection op is missing from OP_CATALOG, _parse_plan
    # filters a valid plan step out and the edit silently disappears.
    for name in guide.NEW_OP_NAMES:
        assert name in eaar.OP_CATALOG
    for name in ("photo.autoTone", "retouch.skin", "retouch.teeth",
                 "selection.face", "selection.background"):
        assert name in eaar._OP_SET


def test_portrait_skin_intent_is_a_multi_step_plan():
    plan = guide.fallback_plan("can you fix the skin a bit, look my best")
    assert plan is not None
    # No leading whole-image auto tone: it washes out the face. Go straight to a
    # gentle face retouch, then clear the selection.
    assert _ops(plan["steps"]) == [
        "selection.face", "retouch.skin", "selection.deselect",
    ]
    skin = plan["steps"][1]["params"]
    assert 0 < skin["smooth"] <= 14
    assert 0 < skin["opacity"] <= 0.45
    # No global lift anywhere in the portrait chain (client never brightens skin).
    assert not any("autoTone" in op or "brightness" in op or "exposure" in op
                   for op in _ops(plan["steps"]))


def test_portrait_intent_does_not_lead_with_auto_tone():
    for prompt in ("make me look better", "fix the skin", "fix my teeth",
                   "smooth my complexion", "portrait touch up"):
        plan = guide.fallback_plan(prompt)
        assert plan is not None, prompt
        assert "photo.autoTone" not in _ops(plan["steps"]), prompt


def test_teeth_intent_selects_face_then_whitens():
    plan = guide.fallback_plan("fix my teeth")
    assert _ops(plan["steps"]) == ["selection.face", "retouch.teeth"]
    assert plan["steps"][1]["params"]["desat"] > 0


def test_background_intent_is_selection_then_masked_blur():
    plan = guide.fallback_plan("blur the background / make the background nicer")
    ops = _ops(plan["steps"])
    assert ops == ["selection.background", "filters.applyMasked"]
    assert plan["steps"][1]["params"]["id"] == "blur-gaussian"


def test_temperature_sign_is_cool_negative_warm_positive():
    cool = guide.fallback_plan("make it moodier")
    assert cool["steps"][0]["params"] == {
        "type": "white-balance", "params": {"temperature": -25}}
    warm = guide.fallback_plan("make it warmer, golden summer feel")
    assert warm["steps"][0]["params"]["params"]["temperature"] == 25


def test_lighting_intent_uses_auto_tone_then_single_shadow_lift():
    plan = guide.fallback_plan("fix the lighting, it's too dark")
    assert _ops(plan["steps"]) == ["photo.autoTone", "adjustments.apply"]
    inner = plan["steps"][1]["params"]
    assert inner["type"] == "shadows-highlights"
    assert inner["params"]["highlights"] <= 0
    # The sanctioned low-key pair, never a stack of unspecified lifts.
    assert len(plan["steps"]) == 2


def test_ambiguous_prompt_asks_the_user_with_concrete_options():
    plan = guide.fallback_plan("clean this up")
    assert plan["steps"] == []
    ask = plan["ask_user"]
    assert ask["question"]
    assert 2 <= len(ask["options"]) <= 3
    assert all(o.get("label") for o in ask["options"])
    assert guide.is_ambiguous("clean this up") is True
    # A concrete defect is not ambiguous.
    assert guide.is_ambiguous("fix the lighting") is False


def test_collapse_global_lifts_replaces_a_stack():
    # With an auto tone present, extra brightness/exposure lifts are dropped
    # (the auto tone is the single sanctioned lift).
    with_auto = [
        {"op": "photo.autoTone", "params": {}},
        {"op": "adjustments.apply",
         "params": {"type": "brightness-contrast", "params": {"brightness": 30}}},
        {"op": "adjustments.apply",
         "params": {"type": "exposure", "params": {"exposure": 20}}},
    ]
    out = guide.collapse_global_lifts(with_auto)
    assert len(out) == 1
    assert out[0]["op"] == "photo.autoTone"
    # Without an auto tone, a stack collapses to one safe shadows-highlights.
    no_auto = [
        {"op": "adjustments.apply",
         "params": {"type": "brightness-contrast", "params": {"brightness": 30}}},
        {"op": "adjustments.apply",
         "params": {"type": "exposure", "params": {"exposure": 20}}},
    ]
    out2 = guide.collapse_global_lifts(no_auto)
    assert len(out2) == 1
    assert out2[0]["params"]["type"] == "shadows-highlights"
    assert out2[0]["params"]["params"]["highlights"] <= 0
    # The sanctioned low-key pair (auto tone + one shadows-highlights) is kept.
    pair = [
        {"op": "photo.autoTone", "params": {}},
        {"op": "adjustments.apply",
         "params": {"type": "shadows-highlights",
                    "params": {"shadows": 30, "highlights": -15}}},
    ]
    assert guide.collapse_global_lifts(pair) == pair


def test_collapse_global_lifts_clamps_a_single_brightness_lift():
    steps = [{"op": "adjustments.apply",
              "params": {"type": "brightness-contrast", "params": {"brightness": 80}}}]
    out = guide.collapse_global_lifts(steps)
    assert out[0]["params"]["params"]["brightness"] <= 25


def test_llm_plan_parse_collapses_lifts_and_keeps_new_ops():
    raw = (
        '{"reply": "x", "steps": ['
        '{"op": "retouch.skin", "params": {"smooth": 20}},'
        '{"op": "photo.autoTone", "params": {}},'
        '{"op": "adjustments.apply", "params": {"type": "exposure", "params": {"exposure": 25}}}'
        ']}'
    )
    parsed = eaar._parse_plan(raw)
    ops = _ops(parsed["steps"])
    assert "retouch.skin" in ops
    # Every global lift that trails retouch.skin is dropped (the client guard
    # "never brighten skin"), so nothing but the retouch step remains.
    assert ops == ["retouch.skin"]


def test_editor_agent_returns_ask_user_for_ambiguous_prompt(client):
    eaar.set_editor_planner(lambda messages, owner: '{"reply": "ok", "steps": []}')
    resp = client.post("/api/editor/agent", json={"prompt": "clean this up"})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ask_user_pending") is True
    assert body["steps"] == []
    assert 2 <= len(body["ask_user"]["options"]) <= 3


def test_editor_plan_returns_ask_user_for_ambiguous_prompt(client):
    eaar.set_editor_planner(lambda messages, owner: None)
    resp = client.post("/api/editor/plan", json={"prompt": "clean this up"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["steps"] == []
    assert body.get("ask_user")


def test_style_guide_endpoint_serves_the_shared_source(client):
    resp = client.get("/api/editor/style-guide")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == guide.STYLE_GUIDE_VERSION
    assert {o["op"] for o in data["new_ops"]} == set(guide.NEW_OP_NAMES)
    assert data["intents"]
    assert "temperature" in data["rules"]["temperature_sign"].lower() or \
        "negative" in data["rules"]["temperature_sign"]
    # The prompt guidance states the sign rule explicitly.
    assert "NEGATIVE" in data["guidance"] and "POSITIVE" in data["guidance"]


def test_prompts_embed_the_style_guidance():
    assert guide.guidance() in eaar._SYSTEM_PROMPT
    assert guide.guidance() in eaar._CHECK_SYSTEM_PROMPT


@pytest.mark.parametrize("prompt", [
    "make it pretty", "make it pop", "professional", "film look",
])
def test_generic_look_intent_is_a_decisive_multi_step_plan(prompt):
    plan = guide.fallback_plan(prompt)
    assert plan is not None
    ops = _ops(plan["steps"])
    assert ops[0] == "photo.autoTone"
    assert len(ops) >= 3
    # A visible vibrance lift and a local-contrast filter are present.
    assert any(s["op"] == "adjustments.apply" and s["params"].get("type") == "vibrance"
               for s in plan["steps"])
    assert any(s["op"] == "filters.apply" and s["params"].get("id") == "clarity"
               for s in plan["steps"])


def test_restore_intent_never_large_exposure_lift():
    plan = guide.fallback_plan("restore this old photo")
    assert plan is not None
    ops = _ops(plan["steps"])
    assert ops[0] == "photo.autoTone"
    assert "filters.apply" in ops  # sharpen
    # Colour recovery present.
    assert any(s["op"] == "adjustments.apply" and s["params"].get("type") == "white-balance"
               for s in plan["steps"])
    # No exposure lift at all, and the contrast lift keeps brightness neutral.
    assert not any(s["params"].get("type") == "exposure" for s in plan["steps"])
    for s in plan["steps"]:
        if s["params"].get("type") == "brightness-contrast":
            assert s["params"]["params"].get("brightness", 0) == 0


def test_strengthen_promotes_a_timid_plan_to_the_recipe():
    timid = [{"op": "adjustments.apply",
              "params": {"type": "brightness-contrast", "params": {"brightness": 8}}}]
    promoted = guide.strengthen("make it pop", timid)
    assert promoted != timid
    assert _ops(promoted)[0] == "photo.autoTone"
    assert len(promoted) >= 3
    # A prompt with no recipe leaves the plan untouched.
    assert guide.strengthen("nonsense prompt zzz", timid) == timid


def test_enforce_guards_removes_trailing_lift_after_skin():
    steps = [
        {"op": "selection.face", "params": {}},
        {"op": "retouch.skin", "params": {"smooth": 24}},
        {"op": "photo.autoTone", "params": {}},
    ]
    guarded = guide.enforce_guards(steps)
    assert _ops(guarded) == ["selection.face", "retouch.skin"]
    stacked = [
        {"op": "adjustments.apply",
         "params": {"type": "brightness-contrast", "params": {"brightness": 20}}},
        {"op": "adjustments.apply",
         "params": {"type": "exposure", "params": {"exposure": 20}}},
    ]
    collapsed = guide.enforce_guards(stacked)
    assert len(collapsed) == 1
    assert collapsed[0]["params"]["type"] == "shadows-highlights"


def test_plan_route_follows_the_recipe_for_generic_look(client):
    import json as _json
    eaar.set_editor_planner(lambda messages, owner: _json.dumps({
        "reply": "ok",
        "steps": [{"op": "adjustments.apply",
                   "params": {"type": "brightness-contrast", "params": {"brightness": 8}}}],
    }))
    resp = client.post("/api/editor/plan", json={"prompt": "make it pop"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["steps"][0]["op"] == "photo.autoTone"
    assert len(data["steps"]) >= 3
    assert data["planner"].endswith("+guide")


def test_guidance_documents_look_and_restore_recipes():
    text = guide.guidance()
    low = text.lower()
    assert "clarity" in low and "vignette" in low
    assert "vibrance" in low
    assert "restore" in low and "exposure" in low
    assert "autoTone" in text


def test_plan_special_format_crops_and_resizes():
    special = eaar._plan_special(
        "make a 1080x1350 Instagram post from this", {"width": 900, "height": 600}, False)
    assert special is not None
    steps, reply, note = special
    assert _ops(steps) == ["document.crop", "document.resizeCanvas"]
    assert "1080" in reply and "1350" in reply
    assert set(steps[0]["params"]) >= {"x", "y", "width", "height"}
    assert steps[1]["params"]["width"] == 1080 and steps[1]["params"]["height"] == 1350


def test_plan_special_text_add_with_shadow_is_not_generative():
    assert eaar._generative_kind("add a title TOKYO with a drop shadow") is None
    special = eaar._plan_special("add a title TOKYO with a drop shadow", {}, False)
    assert special is not None
    steps, reply, note = special
    assert _ops(steps) == ["text.add", "layers.addStyle"]
    assert steps[0]["params"]["text"] == "TOKYO"
    assert steps[1]["params"]["type"] == "drop-shadow"
    assert "drop shadow" in reply.lower()


def test_plan_special_extend_resizes_without_provider_generative_with():
    steps, reply, note = eaar._plan_special(
        "extend the canvas to 16:9", {"width": 900, "height": 600}, False)
    assert _ops(steps) == ["document.resizeCanvas"]
    assert "provider" in note.lower()
    steps2, _, note2 = eaar._plan_special(
        "extend the canvas to 16:9", {"width": 900, "height": 600}, True)
    assert _ops(steps2) == ["ai.generativeExpand"]
    assert note2 == ""


def test_plan_special_psd_export():
    special = eaar._plan_special("save as PSD with layers", {}, False)
    assert special is not None and _ops(special[0]) == ["document.savePsd"]


def test_background_replacement_is_generative_not_blur():
    assert eaar._generative_kind("put her on a beach") == "replace"
    assert eaar._generative_kind("remove the background and put her on a beach") == "replace"


def test_verified_reply_corrects_a_false_size_claim():
    corrected = eaar._verified_reply(
        "make a 1080x1350 Instagram post",
        [{"op": "history.mark", "params": {}}],
        [{"ok": True}],
        {"width": 600, "height": 600},
        "Exported your 900x600 as a 1080x1350 Instagram post.",
    )
    assert "600" in corrected
    assert "still" in corrected.lower() or "didn't apply" in corrected.lower()
    passed = eaar._verified_reply(
        "make a 1080x1350 Instagram post",
        [{"op": "document.crop", "params": {}}, {"op": "document.resizeCanvas", "params": {}}],
        [{"ok": True}, {"ok": True}],
        {"width": 1080, "height": 1350},
        "Setting the canvas to 1080\u00d71350.",
    )
    assert "1080" in passed and "1350" in passed


def test_plan_endpoint_returns_deterministic_format_steps(client, monkeypatch):
    eaar.set_editor_planner(lambda messages, owner: {"reply": "Exporting.", "steps": []})
    monkeypatch.setattr(eaar, "_image_provider_available", lambda owner: False)
    resp = client.post("/api/editor/plan", json={
        "prompt": "make a 1080x1350 Instagram post",
        "state": {"width": 900, "height": 600},
    })
    data = resp.json()
    assert data["planner"] == "deterministic"
    assert _ops(data["steps"])[:2] == ["document.crop", "document.resizeCanvas"]


def test_style_guide_serves_format_text_psd_rules():
    rules = guide.as_json()["rules"]
    assert "format" in rules and "text" in rules and "psd" in rules
    assert guide.STYLE_GUIDE_VERSION >= 3


# ── Quick Edit coverage: structural ops (server planner gaps) ──────────
# Every prompt that failed the live Quick Edit coverage audit because the
# server planner emitted a generic adjustments/transform step instead of the
# exact structural op the browser supports.
_STRUCTURAL_PROMPTS = [
    ("fill the white area with red", "fill.flood"),          # 1
    ("flood fill the sky with blue", "fill.flood"),          # 2
    ("make the text bigger", "text.edit"),                   # 3
    ("make the title red", "text.edit"),                     # 4
    ("add a colour overlay", "layers.addStyle"),             # 8
    ("align the logo to the centre", "layers.align"),        # 9
    ("align the layer to the left", "layers.align"),         # 10
    ("rotate only this layer 90 degrees", "layer.rotate"),   # 11
    ("flip this layer horizontally", "layer.flip"),          # 12
    ("rotate the whole image 90 degrees", "document.rotate"),  # 13
    ("flip the canvas vertically", "document.flip"),         # 14
    ("copy the selection", "selection.copy"),                # 19
    ("paste", "selection.paste"),                            # 20
    ("flatten the image", "layers.flatten"),                 # 40
    ("merge the layer down", "layers.mergeDown"),            # 41
]


@pytest.mark.parametrize("prompt,expected_op", _STRUCTURAL_PROMPTS)
def test_structural_plan_routes_to_the_exact_op(prompt, expected_op):
    plan = guide.structural_plan(prompt)
    assert plan is not None, prompt
    assert plan["planner"] == "deterministic"
    assert expected_op in _ops(plan["steps"]), (prompt, _ops(plan["steps"]))


@pytest.mark.parametrize("prompt,expected_op", _STRUCTURAL_PROMPTS)
def test_plan_special_emits_structural_ops(prompt, expected_op):
    # The server override path (_plan_special) must return the structural step,
    # otherwise a wrong model plan suppresses the browser fallback.
    special = eaar._plan_special(prompt, {"width": 900, "height": 600}, True)
    assert special is not None, prompt
    steps, reply, _ = special
    assert expected_op in _ops(steps), (prompt, _ops(steps))
    assert reply


def test_structural_ops_are_in_the_server_op_catalog():
    for name in ("fill.flood", "selection.fill", "text.edit", "layers.align",
                 "layer.rotate", "layer.flip", "document.rotate", "document.flip",
                 "selection.copy", "selection.paste", "layers.flatten",
                 "layers.mergeDown"):
        assert name in eaar._OP_SET, name


def test_structural_plan_ignores_non_structural_intents():
    for prompt in ("make it brighter", "blur the background", "make it punchy",
                   "restore this old photo", "smooth the skin", "sharpen it",
                   "add the title \"Summer\"", "duplicate this layer"):
        assert guide.structural_plan(prompt) is None, prompt


def test_structural_params_match_the_client():
    # Canonical params must match static/js/editor/galleryEditor.js control ops.
    fill = guide.structural_plan("fill the white area with red")["steps"][0]
    assert fill["op"] == "fill.flood" and fill["params"]["color"] == "#ff0000"
    sel = guide.structural_plan("fill the selection with blue")["steps"][0]
    assert sel["op"] == "selection.fill" and sel["params"]["color"] == "#0000ff"
    align = guide.structural_plan("align the layer to the left")["steps"][0]
    assert align["params"]["mode"] == "left"
    rot = guide.structural_plan("rotate only this layer 90 degrees")["steps"][0]
    assert rot["params"]["degrees"] == 90
    flip = guide.structural_plan("flip the canvas vertically")["steps"][0]
    assert flip["op"] == "document.flip" and flip["params"]["axis"] == "v"
    overlay = guide.structural_plan("add a colour overlay")["steps"][0]
    assert overlay["params"]["type"] == "color-overlay"
    edit = guide.structural_plan("make the text bigger")["steps"][0]
    assert edit["op"] == "text.edit" and edit["params"]["size"] > 0


def test_plan_route_returns_structural_steps(client):
    resp = client.post("/api/editor/plan", json={
        "prompt": "flatten the image",
        "state": {"width": 900, "height": 600},
    })
    data = resp.json()
    assert data["planner"] == "deterministic"
    assert "layers.flatten" in _ops(data["steps"])


def test_style_guide_serves_structural_rule():
    rules = guide.as_json()["rules"]
    assert "structural" in rules
    assert guide.STYLE_GUIDE_VERSION >= 5


# ── Targeted recolour + named reference looks ──────────────────────────

_RECOLOR_CASES = [
    ("make only the red car blue", "red"),
    ("recolour the red rock to teal", "red"),
    ("change the yellow car to red", "yellow"),
]


@pytest.mark.parametrize("prompt,source", _RECOLOR_CASES)
def test_targeted_recolor_selects_object_then_masked_hue(prompt, source):
    plan = guide.recolor_plan(prompt)
    assert plan is not None
    assert _ops(plan["steps"]) == ["selection.samText", "adjustments.applyMasked"]
    sel, adj = plan["steps"]
    assert sel["params"]["text"]                    # object description for the mask
    assert sel["params"]["fallbackColor"] == guide.color_for_name(source)
    assert adj["params"]["mode"] == "hue-saturation"
    assert "hue" in adj["params"] and isinstance(adj["params"]["hue"], int)


def test_recolor_never_emits_selection_fill_or_a_global_tint():
    for prompt, _ in _RECOLOR_CASES:
        ops = _ops(guide.recolor_plan(prompt)["steps"])
        # A flat fill destroys shading; a whole-image tint is not targeted.
        assert "selection.fill" not in ops
        assert "adjustments.apply" not in ops  # exact op, not applyMasked


def test_targeted_recolor_hue_delta_is_source_to_target():
    # red (hue 0) -> blue (hue 240): shortest rotation is -120 degrees.
    plan = guide.recolor_plan("make only the red car blue")
    assert plan["steps"][1]["params"]["hue"] == -120


def test_recolor_masked_hue_carries_source_hue():
    # red has hue 0; the client uses sourceHue as the rotation center so
    # neighbouring colours (chrome, windows, highlights) keep their own hue.
    plan = guide.recolor_plan("make only the red car blue")
    assert plan["steps"][1]["params"]["sourceHue"] == 0


def test_enforce_guards_drops_global_lift_after_masked_recolor():
    # A recolour keeps shading; a later whole-image tone lift would tint the
    # rest of the photo, so enforce_guards must drop global lifts after it.
    steps = [
        {"op": "selection.samText", "params": {"text": "red car"}},
        {"op": "adjustments.applyMasked",
         "params": {"mode": "hue-saturation", "hue": -120, "sourceHue": 0}},
        {"op": "adjustments.apply",
         "params": {"type": "brightness-contrast", "params": {"brightness": 20}}},
        {"op": "photo.autoTone", "params": {}},
    ]
    assert _ops(guide.enforce_guards(steps)) == ["selection.samText", "adjustments.applyMasked"]


def test_enforce_guards_drops_whole_image_grade_after_masked_recolor():
    # Not just tone "lifts": ANY whole-image adjustment (vibrance/saturation/
    # autoTone) after a masked recolour re-grades the photo and undoes the
    # "only this object" intent, so enforce_guards must drop them all.
    steps = [
        {"op": "selection.samText", "params": {"text": "red car"}},
        {"op": "adjustments.applyMasked",
         "params": {"mode": "hue-saturation", "hue": -120, "sourceHue": 0}},
        {"op": "adjustments.apply",
         "params": {"type": "vibrance", "params": {"vibrance": 20}}},
        {"op": "adjustments.apply",
         "params": {"type": "hue-saturation", "params": {"saturation": 12}}},
        {"op": "photo.autoTone", "params": {}},
    ]
    assert _ops(guide.enforce_guards(steps)) == ["selection.samText", "adjustments.applyMasked"]


def test_enforce_guards_keeps_grades_when_there_is_no_recolor():
    # A recipe/look (no masked recolour) still keeps its whole-image grades.
    steps = [
        {"op": "photo.autoTone", "params": {}},
        {"op": "adjustments.apply", "params": {"type": "vibrance", "params": {"vibrance": 18}}},
        {"op": "adjustments.apply",
         "params": {"type": "brightness-contrast", "params": {"brightness": 6, "contrast": -10}}},
    ]
    assert _ops(guide.enforce_guards(steps)) == ["photo.autoTone", "adjustments.apply"]


def test_recolor_to_white_uses_the_image_model_not_a_hue_shift():
    # A white/black/grey target cannot be reached by a hue rotation.
    plan = guide.recolor_plan("make the red car white")
    assert plan is not None
    assert _ops(plan["steps"]) == ["selection.samText", "ai_edit_image"]
    assert plan["steps"][1]["params"]["mode"] == "masked"


def test_recolor_with_single_colour_uses_masked_image_model():
    plan = guide.recolor_plan("make the flowers bright pink")
    assert plan is not None
    assert _ops(plan["steps"]) == ["selection.samText", "ai_edit_image"]
    assert plan["steps"][0]["params"]["text"] == "flowers"
    params = plan["steps"][1]["params"]
    assert params["mode"] == "masked"
    assert params["steps"] == 8


def test_recolor_does_not_hijack_unrelated_colour_prompts():
    for prompt in ("make it blue", "fill the white area with red",
                   "make the title red", "make this black and white",
                   "blur the background"):
        assert guide.recolor_plan(prompt) is None


@pytest.mark.parametrize("prompt", [
    "make it look like a Wes Anderson film",
    "make it look like a film noir",
    "make it look like vintage Kodachrome",
])
def test_named_recipe_looks_are_adjustment_stacks(prompt):
    plan = guide.look_plan(prompt)
    assert plan is not None
    ops = _ops(plan["steps"])
    assert ops and "ai_edit_image" not in ops


@pytest.mark.parametrize("prompt", [
    "make it look like cyberpunk",
    "make it look like a Studio Ghibli film",
])
def test_strong_named_looks_use_instruction_mode(prompt):
    plan = guide.look_plan(prompt)
    assert plan is not None
    assert _ops(plan["steps"]) == ["ai_edit_image"]
    assert plan["steps"][0]["params"]["mode"] == "instruction"
    assert plan["steps"][0]["params"]["steps"] == 8


def test_structural_plan_dispatches_looks_and_recolor():
    wes = guide.structural_plan("make it look like a Wes Anderson film")
    assert "adjustments.apply" in _ops(wes["steps"])
    recolor = guide.structural_plan("make only the red car blue")
    assert _ops(recolor["steps"]) == ["selection.samText", "adjustments.applyMasked"]


def test_named_looks_do_not_hijack_generative_lookalikes():
    for prompt in ("make it look snowy", "make it look like summer"):
        assert guide.look_plan(prompt) is None


def test_plan_route_returns_targeted_recolor(client):
    resp = client.post("/api/editor/plan", json={
        "prompt": "make only the red car blue",
        "state": {"width": 900, "height": 600},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["planner"] == "deterministic"
    assert _ops(data["steps"]) == ["selection.samText", "adjustments.applyMasked"]
    assert "selection.fill" not in _ops(data["steps"])


def test_plan_route_returns_named_look(client):
    resp = client.post("/api/editor/plan", json={
        "prompt": "make it look like a Wes Anderson film",
        "state": {"width": 900, "height": 600},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["planner"] == "deterministic"
    assert "adjustments.apply" in _ops(data["steps"])


def test_style_guide_serves_recolor_and_looks_rules():
    body = guide.as_json()
    assert "recolor" in body["rules"]
    assert "looks" in body["rules"]
    assert {lk["id"] for lk in body["looks"]} >= {
        "wes_anderson", "film_noir", "cyberpunk", "vintage_kodachrome",
        "studio_ghibli",
    }


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
