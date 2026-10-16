"""Tests for routes/editor_agent_routes.py (NL editor planning + bridge).

The route wires an injected planner callable (messages, owner) -> raw plan and always
falls back to a deterministic keyword planner, so the editor gets a usable plan even
without a model. Tests use a minimal FastAPI app (no full app import) and inject fake
planners to assert parse/vision/check-mode/bridge-validation behavior.
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import editor_agent_routes as eaar


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


def test_plan_uses_injected_planner_and_parses_steps(client):
    eaar.set_editor_planner(
        lambda messages, owner: json.dumps(
            {"reply": "ok", "steps": [{"op": "adjustments.apply", "params": {"type": "vibrance"}}]}
        )
    )
    r = client.post("/api/editor/plan", json={"prompt": "make it vivid", "state": {}})
    assert r.status_code == 200
    data = r.json()
    assert data["planner"] == "llm"
    assert data["steps"] == [{"op": "adjustments.apply", "params": {"type": "vibrance"}}]


def test_plan_sends_screenshot_as_vision_part(client):
    seen = {}

    def fake(messages, owner):
        seen["messages"] = messages
        return json.dumps({"reply": "ok", "steps": [{"op": "filters.apply", "params": {"id": "sharpen"}}]})

    eaar.set_editor_planner(fake)
    r = client.post(
        "/api/editor/plan",
        json={"prompt": "sharpen it", "state": {}, "screenshot": "data:image/png;base64,AAAA"},
    )
    assert r.status_code == 200
    content = seen["messages"][1]["content"]
    assert any(p.get("type") == "image_url" for p in content if isinstance(p, dict))
    assert any(
        p.get("type") == "image_url" and p["image_url"]["url"].startswith("data:image/")
        for p in content if isinstance(p, dict)
    )


def test_plan_without_screenshot_sends_plain_text(client):
    seen = {}

    def fake(messages, owner):
        seen["messages"] = messages
        return json.dumps({"reply": "ok", "steps": [{"op": "filters.apply", "params": {"id": "sharpen"}}]})

    eaar.set_editor_planner(fake)
    client.post("/api/editor/plan", json={"prompt": "sharpen it", "state": {}})
    assert isinstance(seen["messages"][1]["content"], str)


def test_plan_falls_back_when_planner_yields_nothing(client):
    eaar.set_editor_planner(lambda messages, owner: None)
    r = client.post("/api/editor/plan", json={"prompt": "make it black and white", "state": {}})
    data = r.json()
    assert data["planner"] == "fallback"
    assert any(op["op"] == "adjustments.apply" for op in data["steps"])


def test_plan_parses_dict_return_and_filters_unknown_ops(client):
    eaar.set_editor_planner(
        lambda messages, owner: {
            "reply": "x",
            "steps": [{"op": "evil.op"}, {"op": "text.add", "params": {"text": "hi"}}],
        }
    )
    data = client.post("/api/editor/plan", json={"prompt": "add text", "state": {}}).json()
    assert [s["op"] for s in data["steps"]] == ["text.add"]


def test_plan_awaits_async_planner(client):
    async def fake(messages, owner):
        return json.dumps({"reply": "ok", "steps": [{"op": "history.mark", "params": {}}]})

    eaar.set_editor_planner(fake)
    data = client.post("/api/editor/plan", json={"prompt": "mark", "state": {}}).json()
    assert data["steps"][0]["op"] == "history.mark"


def test_check_mode_reports_done_when_model_completes(client):
    eaar.set_editor_planner(lambda messages, owner: json.dumps({"done": True, "reply": "looks good"}))
    data = client.post(
        "/api/editor/plan",
        json={"prompt": "make it warm", "state": {"iteration": 2}, "mode": "check",
              "screenshot": "data:image/png;base64,AAAA"},
    ).json()
    assert data["done"] is True
    assert data["steps"] == []


def test_check_mode_returns_remaining_steps(client):
    eaar.set_editor_planner(
        lambda messages, owner: json.dumps(
            {"done": False, "reply": "more contrast", "steps": [{"op": "adjustments.apply", "params": {}}]}
        )
    )
    data = client.post(
        "/api/editor/plan",
        json={"prompt": "make it vivid", "state": {"iteration": 1}, "mode": "check"},
    ).json()
    assert data.get("done") is not True
    assert data["steps"][0]["op"] == "adjustments.apply"


def test_check_mode_with_unparseable_planner_stops_loop(client):
    eaar.set_editor_planner(lambda messages, owner: "not json at all")
    data = client.post(
        "/api/editor/plan", json={"prompt": "x", "state": {}, "mode": "check"}
    ).json()
    assert data["done"] is True
    assert data["steps"] == []


def test_normalize_steps_drops_ops_outside_catalog():
    assert eaar._normalize_steps({"op": "evil.op"}) == []
    assert eaar._normalize_steps(
        {"steps": [{"op": "text.add"}, {"op": "nope"}, {"not": "a step"}]}
    ) == [{"op": "text.add", "params": {}}]


def test_bridge_command_only_queues_known_ops(client):
    good = client.post("/api/editor/bridge/command", json={"session": "s", "op": "screenshot"}).json()
    assert good["queued"]
    bad = client.post("/api/editor/bridge/command", json={"session": "s", "op": "evil.op"}).json()
    assert bad["queued"] == []


def test_bridge_command_accepts_selection_export_mask(client):
    # The generative tools fetch the current selection via this op; if it is not
    # in the catalog the bridge drops the command and the mask comes back empty.
    assert "selection.exportMask" in eaar._OP_SET
    ok = client.post(
        "/api/editor/bridge/command",
        json={"session": "s", "op": "selection.exportMask", "wait": False},
    ).json()
    assert ok["queued"], ok


def test_set_editor_planner_is_used_by_setup_when_set():
    sentinel = lambda messages, owner: None  # noqa: E731
    eaar.set_editor_planner(sentinel)
    app = FastAPI()
    app.include_router(eaar.setup_editor_agent_routes())
    assert eaar._EDITOR_PLANNER is sentinel


def _recipe_ops(steps):
    out = []
    for step in steps:
        params = step.get("params") or {}
        if step.get("op") in ("adjustments.apply", "adjustments.applyMasked"):
            out.append((step.get("op"), params.get("type")))
        elif step.get("op") in ("filters.apply", "filters.applyMasked"):
            out.append((step.get("op"), params.get("id")))
        else:
            out.append((step.get("op"), None))
    return out


def test_agent_path_enforces_v3_recipes_over_a_timid_model_plan(client, monkeypatch):
    """The /api/editor/agent path (not just /plan) must follow the style guide.

    The sampling model returns one mild brightness-contrast step for every
    generic intent; the guide is authoritative, so the calibrated recipe ops
    must appear in the agent response steps.
    """
    weak = [{"op": "adjustments.apply",
             "params": {"type": "brightness-contrast",
                        "params": {"brightness": 10, "contrast": 10}}}]
    eaar.set_editor_planner(lambda messages, owner: json.dumps({"reply": "ok", "steps": weak}))

    async def _fake_wait(fut, timeout=None, **kwargs):  # noqa: ANN001
        return {"ok": True, "result": {"width": 100, "height": 100}}

    monkeypatch.setattr(eaar.asyncio, "wait_for", _fake_wait)

    cases = {
        "restore this old photo": {("photo.autoTone", None),
                                   ("adjustments.apply", "white-balance"),
                                   ("adjustments.apply", "vibrance")},
        "make it pretty": {("photo.autoTone", None),
                           ("adjustments.apply", "vibrance"),
                           ("filters.apply", "clarity")},
        "make it pop": {("photo.autoTone", None),
                        ("adjustments.apply", "vibrance"),
                        ("filters.apply", "clarity")},
        "make it look like summer": {("adjustments.apply", "white-balance")},
    }
    for prompt, required in cases.items():
        resp = client.post("/api/editor/agent",
                           json={"prompt": prompt, "state": {"width": 100, "height": 100}})
        assert resp.status_code == 200, (prompt, resp.text)
        data = resp.json()
        ops = set(_recipe_ops(data["steps"]))
        assert required <= ops, (prompt, ops)
        if prompt != "make it look like summer":
            assert len(data["steps"]) >= 3, (prompt, data["steps"])


def test_agent_path_low_key_lighting_uses_auto_tone_plus_one_shadow_lift(client, monkeypatch):
    """Round-6 case 5: 'fix the lighting' on a night photo.

    The model returned a single adjustments.apply step while claiming an
    auto-tone pass; the guide must supply photo.autoTone + one shadows lift,
    and the executed steps must contain the auto tone.
    """
    weak = [{"op": "adjustments.apply",
             "params": {"type": "shadows-highlights",
                        "params": {"shadows": 20, "highlights": -10}}}]
    eaar.set_editor_planner(lambda messages, owner: json.dumps(
        {"reply": "A single auto-tone pass.", "steps": weak}))

    async def _fake_wait(fut, timeout=None, **kwargs):  # noqa: ANN001
        return {"ok": True, "result": {"width": 900, "height": 600}}

    monkeypatch.setattr(eaar.asyncio, "wait_for", _fake_wait)

    resp = client.post("/api/editor/agent",
                       json={"prompt": "fix the lighting", "state": {"width": 900, "height": 600}})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    ops = _recipe_ops(data["steps"])
    assert ("photo.autoTone", None) in ops, ops
    shadow = [s for s in data["steps"] if s.get("params", {}).get("type") == "shadows-highlights"]
    assert shadow and shadow[0]["params"]["params"]["highlights"] <= 0, data["steps"]
    assert len(data["steps"]) == 2, data["steps"]


def test_agent_path_reply_describes_executed_ops_not_the_plan(client, monkeypatch):
    """A reply must not claim an edit (auto tone / white balance) that the
    executed steps do not contain; it is rewritten from the real steps."""
    eaar.set_editor_planner(lambda messages, owner: json.dumps({
        "reply": "Applied a single auto-tone pass and white balance.",
        "steps": [{"op": "adjustments.apply",
                   "params": {"type": "vibrance", "params": {"vibrance": 20}}}],
    }))

    async def _fake_wait(fut, timeout=None, **kwargs):  # noqa: ANN001
        return {"ok": True, "result": {"width": 100, "height": 100}}

    monkeypatch.setattr(eaar.asyncio, "wait_for", _fake_wait)

    resp = client.post("/api/editor/agent",
                       json={"prompt": "boost the saturation", "state": {"width": 100, "height": 100}})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    reply = data["reply"].lower()
    assert "auto" not in reply, data["reply"]
    assert "white balance" not in reply, data["reply"]
    assert "vibrance" in reply, data["reply"]


def _install_provider_and_fake_tools(monkeypatch, *, failing=False):
    """Provider is available and the AI content tools resolve instantly."""
    monkeypatch.setattr(eaar, "_image_provider_available", lambda owner: True)

    class _FakeTool:
        async def execute(self, content, ctx):  # noqa: ANN001
            if failing:
                return ("ai_edit_image", {"exit_code": 1, "error": "provider exploded"})
            return (
                "ai_edit_image",
                {
                    "exit_code": 0,
                    "image_id": "gen-1",
                    "image_url": "/api/generated-image/gen-1.png",
                    "placed": True,
                    "output": "applied",
                },
            )

    from src.agent_tools import editor_tools

    monkeypatch.setattr(editor_tools, "AiEditImageTool", _FakeTool)
    monkeypatch.setattr(editor_tools, "GenerateImageLayerTool", _FakeTool)

    async def _fake_wait(fut, timeout=None, **kwargs):  # noqa: ANN001
        return {"ok": True, "result": {"width": 100, "height": 100}}

    monkeypatch.setattr(eaar.asyncio, "wait_for", _fake_wait)


def test_agent_path_routes_generative_intents_when_provider_present(client, monkeypatch):
    """Gen-6 bug 4: with an image provider, object-removal / background-change
    prompts must use ai_edit_image, and plain 'blur the background' stays a blur."""
    _install_provider_and_fake_tools(monkeypatch)

    def _ai_ops(data):
        return {s.get("op") for s in data["steps"]}

    r = client.post("/api/editor/agent",
                    json={"prompt": "remove the distractions", "state": {"width": 100, "height": 100}})
    assert r.status_code == 200, r.text
    assert "ai_edit_image" in _ai_ops(r.json()), r.json()["steps"]

    r = client.post("/api/editor/agent",
                    json={"prompt": "make the background nicer", "state": {"width": 100, "height": 100}})
    assert r.status_code == 200, r.text
    assert "ai_edit_image" in _ai_ops(r.json()), r.json()["steps"]

    r = client.post("/api/editor/agent",
                    json={"prompt": "add a full moon in the sky", "state": {"width": 100, "height": 100}})
    assert r.status_code == 200, r.text
    assert "ai_edit_image" in _ai_ops(r.json()), r.json()["steps"]

    r = client.post("/api/editor/agent",
                    json={"prompt": "remove the person on the left", "state": {"width": 100, "height": 100}})
    assert r.status_code == 200, r.text
    assert "ai_edit_image" in _ai_ops(r.json()), r.json()["steps"]

    r = client.post("/api/editor/agent",
                    json={"prompt": "create a new image of a full moon", "state": {"width": 100, "height": 100}})
    assert r.status_code == 200, r.text
    assert "generate_image" in _ai_ops(r.json()), r.json()["steps"]

    r = client.post("/api/editor/agent",
                    json={"prompt": "blur the background", "state": {"width": 100, "height": 100}})
    assert r.status_code == 200, r.text
    assert _ai_ops(r.json()) & {"ai_edit_image", "generate_image"} == set(), r.json()["steps"]


def test_agent_path_recolor_is_exactly_object_mask_plus_masked_hue(client, monkeypatch):
    """#125/#127: on the agent path a recolour must be EXACTLY
    [selection.samText, adjustments.applyMasked] — the VLM planner used to
    append whole-image adjustments.apply after the masked hue shift."""
    _install_provider_and_fake_tools(monkeypatch)
    eaar.set_editor_planner(
        lambda messages, owner: json.dumps({
            "reply": "recolor",
            "steps": [
                {"op": "selection.samText", "params": {"text": "red car"}},
                {"op": "adjustments.applyMasked",
                 "params": {"mode": "hue-saturation", "hue": -120, "sourceHue": 0}},
                {"op": "adjustments.apply", "params": {"type": "vibrance", "params": {"vibrance": 20}}},
                {"op": "adjustments.apply", "params": {"type": "brightness-contrast", "params": {"brightness": 15}}},
            ],
        })
    )
    r = client.post("/api/editor/agent",
                    json={"prompt": "make only the red car blue",
                          "session": "s1", "editor_session": "s1",
                          "state": {"width": 100, "height": 100}})
    assert r.status_code == 200, r.text
    ops = [s["op"] for s in r.json()["steps"]]
    assert ops == ["selection.samText", "adjustments.applyMasked"], ops
    assert "selection.fill" not in ops


def test_agent_path_strips_global_grades_after_a_masked_recolor(client, monkeypatch):
    """Even when the deterministic recolour special does not fire, a planner's
    masked-recolour plan must not keep trailing whole-image adjustments."""
    _install_provider_and_fake_tools(monkeypatch)
    eaar.set_editor_planner(
        lambda messages, owner: json.dumps({
            "reply": "tweak",
            "steps": [
                {"op": "selection.samText", "params": {"text": "the object"}},
                {"op": "adjustments.applyMasked",
                 "params": {"mode": "hue-saturation", "hue": 90, "sourceHue": 200}},
                {"op": "adjustments.apply", "params": {"type": "vibrance", "params": {"vibrance": 20}}},
                {"op": "photo.autoTone", "params": {}},
            ],
        })
    )
    r = client.post("/api/editor/agent",
                    json={"prompt": "do a quick tweak",
                          "session": "s2", "editor_session": "s2",
                          "state": {"width": 100, "height": 100}})
    assert r.status_code == 200, r.text
    ops = [s["op"] for s in r.json()["steps"]]
    assert ops == ["selection.samText", "adjustments.applyMasked"], ops


def test_agent_path_reports_failure_when_a_generative_step_fails(client, monkeypatch):
    """Gen-6 bug 5: a failed generative step must return ok:false with the real
    error, never ok:true."""
    _install_provider_and_fake_tools(monkeypatch, failing=True)
    r = client.post("/api/editor/agent",
                    json={"prompt": "remove the distractions", "state": {"width": 100, "height": 100}})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is False, data
    assert "provider exploded" in data["reply"], data["reply"]
    assert any(
        r.get("op") in ("ai_edit_image", "generate_image") and r.get("ok") is False
        for r in data["results"]
    ), data["results"]


def test_bridge_default_timeout_allows_slow_generative_steps():
    """Gen-6 bug 2: the bridge/agent waits must exceed the provider latency."""
    assert eaar._BRIDGE_DEFAULT_TIMEOUT >= 300


def test_inpaint_proxy_decodes_data_url_base64():
    """Gen-6 bug 1: the image endpoints must accept both raw base64 and a
    'data:...;base64,' data URL (the editor tools send data URLs)."""
    import base64

    from routes.gallery import gallery_routes as gr

    raw = b"\x89PNG\r\n\x1a\n payload"
    data_url = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    assert gr._b64_bytes(data_url) == raw
    assert gr._b64_bytes(base64.b64encode(raw).decode("ascii")) == raw
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        gr._b64_bytes("")


def test_generative_routing_uses_instruction_vs_masked():
    """Qwen-Image-Edit instruction approach: most generative edits are whole-image
    instruction edits (no mask); masked inpaint only for a clear regional object
    removal; generate_image only for a brand-new image."""
    from routes.editor_agent_routes import _generative_mode, _generative_steps

    bg = _generative_steps("replace", "make the background nicer")
    assert len(bg) == 1 and bg[0]["op"] == "ai_edit_image"
    assert bg[0]["params"]["mode"] == "instruction"
    assert "keep" in bg[0]["params"]["prompt"].lower()  # names what must stay

    add = _generative_steps("add", "add a full moon in the sky")
    assert add[-1]["op"] == "ai_edit_image" and add[-1]["params"]["mode"] == "instruction"

    cleanup = _generative_steps("remove", "remove the distractions")
    assert cleanup[-1]["op"] == "ai_edit_image" and cleanup[-1]["params"]["mode"] == "instruction"

    masked = _generative_steps("remove", "remove the person on the left")
    assert masked[0]["op"] == "selection.subject"  # tight mask first
    assert masked[-1]["op"] == "ai_edit_image" and masked[-1]["params"]["mode"] == "masked"
    assert _generative_mode("remove", "remove the person on the left") == "masked"

    fresh = _generative_steps("add", "create a new image of a full moon")
    assert fresh[-1]["op"] == "generate_image"


@pytest.mark.parametrize(
    "prompt",
    [
        "add wildflowers",
        "make it snowy",
        "make it autumn",
        "add sunglasses to her",
        "add a rainbow",
        "generative fill the sky",
    ],
)
def test_generative_content_intents_never_route_to_adjustments(prompt):
    """Bench round 4: adding elements, season/weather changes and an explicit
    "generative fill" must be a whole-image instruction edit (steps 8, keep
    clause), never a colour adjustment/filter."""
    deterministic = [
        {"op": "adjustments.apply", "params": {"type": "brightness-contrast", "params": {"brightness": 10}}}
    ]
    kind = eaar._generative_kind(prompt)
    assert kind in ("add", "replace"), (prompt, kind)
    steps = eaar._ensure_generative(prompt, list(deterministic))
    assert not any(s.get("op") == "adjustments.apply" for s in steps), steps
    ai = [s for s in steps if s.get("op") == "ai_edit_image"]
    assert ai, steps
    assert ai[-1]["params"]["mode"] == "instruction"
    assert ai[-1]["params"].get("steps") == 8
    assert "keep" in ai[-1]["params"]["prompt"].lower()


@pytest.mark.parametrize(
    "prompt", ["make it black and white", "blur the background", "add a vignette", "make it look like summer"]
)
def test_tone_intents_still_do_not_route_to_generative(prompt):
    assert eaar._generative_kind(prompt) is None


def test_season_prompt_names_the_season_and_keeps_subject():
    params = eaar._generative_step("replace", "make it snowy")["params"]
    low = params["prompt"].lower()
    assert "snow" in low and "keep" in low and params["mode"] == "instruction" and params["steps"] == 8


def test_removal_and_expand_use_eight_steps():
    """Gen-6 (C/D): removal and expand edits ask for 8 sampler steps (the server
    default of 4 is too weak) and naming what to remove / where to fill."""
    from routes.editor_agent_routes import _generative_step, _plan_format_steps

    rm = _generative_step("remove", "remove the distractions")["params"]
    assert rm["steps"] == 8, rm
    assert "landscape" in rm["prompt"].lower(), rm["prompt"]

    masked = _generative_step("remove", "remove the person on the left")["params"]
    assert masked["steps"] == 8 and masked["mode"] == "masked", masked

    ext = _plan_format_steps("extend the canvas to 16:9", {"width": 900, "height": 600}, True)[0]["params"]
    assert ext["steps"] == 8 and ext["feather"] is True, ext


def test_extend_plan_uses_instruction_expand_with_feather():
    from routes.editor_agent_routes import _plan_format_steps

    steps = _plan_format_steps("extend the canvas to 16:9", {"width": 900, "height": 600}, True)
    assert steps and steps[0]["op"] == "ai.generativeExpand"
    p = steps[0]["params"]
    assert p.get("mode") == "instruction" and p.get("feather") is True
    assert "border" in p.get("prompt", "").lower()


def test_ai_edit_image_schema_exposes_mode():
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    schema = next(
        s["function"] for s in FUNCTION_TOOL_SCHEMAS if s["function"]["name"] == "ai_edit_image"
    )
    mode = schema["parameters"]["properties"]["mode"]
    assert set(mode["enum"]) == {"instruction", "masked"}


def test_ai_edit_image_tool_instruction_needs_no_mask_masked_does(monkeypatch):
    """Instruction mode sends a full-white mask (whole-image edit); masked mode
    still requires a real selection and errors when none is available."""
    import asyncio
    import base64
    import io

    from PIL import Image

    import src.agent_tools.editor_tools as et

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 20, 30)).save(buf, "PNG")
    img = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    captured = {}

    async def _fake_post(path, payload, owner, timeout=120):
        captured["path"] = path
        captured["payload"] = payload
        return {"image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}

    async def _no_mask(owner, session):
        return None

    async def _no_place(*a, **k):
        return True

    monkeypatch.setattr(et, "_post", _fake_post)
    monkeypatch.setattr(et, "_fetch_selection_mask", _no_mask)
    monkeypatch.setattr(et, "_place_layer", _no_place)
    monkeypatch.setattr(et, "_persist_png", lambda *a, **k: {})
    monkeypatch.setattr(et, "_provider_available", lambda owner: True)

    tool = et.AiEditImageTool()
    # Instruction mode: no mask supplied, still succeeds and sends a full mask.
    desc, res = asyncio.run(tool.execute(
        json.dumps({"op": "generative", "mode": "instruction", "prompt": "nice beach", "image": img}),
        {"owner": "u"},
    ))
    assert res.get("exit_code") == 0, res
    assert captured["payload"].get("mode") == "instruction"
    assert str(captured["payload"].get("mask", "")).startswith("data:image/png;base64,")

    # Masked mode without any selection and no grounding box: fall back to
    # whole-image instruction mode instead of placing an empty/transparent layer.
    captured.clear()
    desc, res = asyncio.run(tool.execute(
        json.dumps({"op": "inpaint", "mode": "masked", "prompt": "remove it", "image": img}),
        {"owner": "u"},
    ))
    assert res.get("exit_code") == 0, res
    assert captured["payload"].get("mode") == "instruction"

    # Masked mode with a grounding box (OWL-ViT) builds a dilated box mask and
    # stays in masked mode.
    async def _ground(owner, image, label):
        return [0.0, 0.0, 8.0, 8.0]

    monkeypatch.setattr(et, "_ground_box", _ground)
    captured.clear()
    desc, res = asyncio.run(tool.execute(
        json.dumps({"op": "inpaint", "mode": "masked", "prompt": "remove the person on the left", "image": img}),
        {"owner": "u"},
    ))
    assert res.get("exit_code") == 0, res
    assert captured["payload"].get("mode") == "masked"
    assert str(captured["payload"].get("mask", "")).startswith("data:image/png;base64,")


def test_place_layer_forwards_edit_mode(monkeypatch):
    """Instruction-mode results must tell the client to build a difference mask."""
    import asyncio

    import src.agent_tools.editor_tools as et

    seen = {}

    async def _fake_post(path, payload, owner, timeout=120):
        seen["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(et, "_post", _fake_post)

    asyncio.run(et._place_layer("data:image/png;base64,AA", "AI inpaint", "u", "s1", mode="instruction"))
    params = seen["payload"]["params"]
    assert params["mode"] == "instruction"
    assert params["differenceMask"] is True

    asyncio.run(et._place_layer("data:image/png;base64,AA", "AI inpaint", "u", "s1", masked=True, mode="masked"))
    params = seen["payload"]["params"]
    assert params["mode"] == "masked"
    assert params["differenceMask"] is False

    # No mode (e.g. generate_image) => keys omitted so the client keeps its behavior.
    asyncio.run(et._place_layer("data:image/png;base64,AA", "Generated image", "u", "s1"))
    params = seen["payload"]["params"]
    assert "mode" not in params and "differenceMask" not in params


def test_plan_route_forces_generative_over_a_deterministic_model_plan(client):
    """/plan must agree with /agent: a generative intent is never left on a
    deterministic substitute even if the model proposes one."""
    eaar.set_editor_planner(
        lambda messages, owner: {"reply": "ok", "steps": [{"op": "history.mark", "params": {}}]}
    )
    try:
        for prompt in ("remove the person on the left", "add a full moon in the sky",
                       "make the background nicer"):
            r = client.post("/api/editor/plan", json={"prompt": prompt, "state": {"width": 100, "height": 100}})
            assert r.status_code == 200, r.text
            ops = [s["op"] for s in r.json()["steps"]]
            assert "ai_edit_image" in ops, (prompt, r.json()["steps"])
        r = client.post("/api/editor/plan", json={"prompt": "create a new image of a full moon",
                                                  "state": {"width": 100, "height": 100}})
        assert "generate_image" in [s["op"] for s in r.json()["steps"]], r.json()["steps"]
    finally:
        eaar.set_editor_planner(None)


def test_scene_replacement_prompt_names_the_scene_and_uses_eight_steps():
    """Gen-6 case 2: a background/scene replacement must name concrete scene
    details and ask for 8 sampler steps, not a bare 'a beach'."""
    from routes.editor_agent_routes import _generative_steps

    steps = _generative_steps("replace", "put her on a beach")
    ai = steps[-1]
    assert ai["op"] == "ai_edit_image", steps
    params = ai["params"]
    assert params["mode"] == "instruction", params
    assert params.get("steps") == 8, params
    low = params["prompt"].lower()
    assert "beach" in low and ("sand" in low or "sea" in low), params["prompt"]
    assert "keep" in low, params["prompt"]
    # Deterministic template, passed to the provider unmodified.
    assert params["prompt"].startswith(
        "Replace the entire background behind the person with"), params["prompt"]
    assert params["prompt"].endswith("Keep the person exactly the same."), params["prompt"]

    generic = _generative_steps("replace", "make the background nicer")[-1]["params"]
    assert generic.get("steps") == 8, generic
    assert "background" in generic["prompt"].lower(), generic


def test_scene_prompt_overrides_a_vague_model_ai_step():
    """A model-written ai_edit_image prompt must not drop the scene details: the
    background-replacement intent forces the templated scene prompt."""
    from routes.editor_agent_routes import _ensure_generative

    model_steps = [{"op": "ai_edit_image",
                    "params": {"op": "generative", "mode": "instruction",
                               "prompt": "put her outside somewhere"}}]
    out = _ensure_generative("put her on a beach", model_steps)
    ai = [s for s in out if s.get("op") == "ai_edit_image"][-1]
    prompt = ai["params"]["prompt"]
    assert prompt.startswith("Replace the entire background behind the person with")
    assert prompt.endswith("Keep the person exactly the same.")
    assert "beach" in prompt.lower() and "sand" in prompt.lower()
    assert ai["params"]["mode"] == "instruction" and ai["params"].get("mask") is None


def test_agent_path_masked_ai_step_receives_canvas_screenshot(client, monkeypatch):
    """Gen-6 case 1: after a selection step, the masked ai_edit_image must still
    receive the canvas image (from the request screenshot), not fail with
    'needs an image'."""
    import base64

    from src.agent_tools import editor_tools

    seen = {}

    class _FakeAI:
        async def execute(self, content, ctx):  # noqa: ANN001
            seen["args"] = json.loads(content)
            return ("ai_edit_image", {"exit_code": 0, "image_id": "g", "placed": True, "output": "ok"})

    monkeypatch.setattr(editor_tools, "AiEditImageTool", _FakeAI)
    monkeypatch.setattr(eaar, "_image_provider_available", lambda owner: True)

    async def _fake_wait(fut, timeout=None, **kwargs):  # noqa: ANN001
        return {"ok": True, "result": {"width": 100, "height": 100}}

    monkeypatch.setattr(eaar.asyncio, "wait_for", _fake_wait)

    eaar.set_editor_planner(
        lambda messages, owner: json.dumps(
            {
                "reply": "ok",
                "steps": [
                    {"op": "selection.subject", "params": {"label": "person"}},
                    {
                        "op": "ai_edit_image",
                        "params": {"op": "inpaint", "mode": "masked", "prompt": "remove the person on the left"},
                    },
                ],
            }
        )
    )
    shot = "data:image/png;base64," + base64.b64encode(b"canvas").decode("ascii")
    r = client.post(
        "/api/editor/agent",
        json={
            "prompt": "remove the person on the left",
            "session": "s1",
            "editor_session": "s1",
            "state": {"width": 100, "height": 100},
            "screenshot": shot,
        },
    )
    assert r.status_code == 200, r.text
    assert seen.get("args"), r.json()
    assert seen["args"].get("image") == shot, seen["args"]
    assert seen["args"].get("mode") == "masked", seen["args"]


def _bridge_app():
    app = FastAPI()
    app.include_router(eaar.setup_editor_agent_routes())
    return app


async def _asgi_post(app, path, payload):
    sent = []
    scope = {"type": "http", "method": "POST", "path": path, "query_string": b"",
             "headers": [(b"content-type", b"application/json")]}
    data = json.dumps(payload).encode()
    recv = [{"type": "http.request", "body": data, "more_body": False}]

    async def receive():
        return recv.pop(0) if recv else {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.body" and msg.get("body"):
            sent.append(msg["body"])

    await app(scope, receive, send)
    return b"".join(sent)


def test_bridge_roundtrip_subscribe_command_result():
    """URGENT regression: a real subscribe -> command -> result round trip must
    deliver the command to the SSE subscriber and resolve the waiting /command."""
    import asyncio

    app = _bridge_app()

    async def scenario():
        out, stop = [], asyncio.Event()

        async def receive():
            await stop.wait()
            return {"type": "http.disconnect"}

        async def send(msg):
            if msg["type"] == "http.response.body" and msg.get("body"):
                out.append(msg["body"])

        sse = asyncio.create_task(app(
            {"type": "http", "method": "GET", "path": "/api/editor/bridge/events",
             "query_string": b"session=S", "headers": []}, receive, send))
        for _ in range(200):
            if any(b": connected" in b for b in out):
                break
            await asyncio.sleep(0.01)

        cmd_result = {}

        async def issue():
            cmd_result["body"] = await _asgi_post(
                app, "/api/editor/bridge/command",
                {"session": "S", "steps": [{"op": "document.info"}], "wait": True, "timeout": 5})

        cmd_task = asyncio.create_task(issue())
        cmd_id = None
        for _ in range(300):
            for b in out:
                for line in b.decode().splitlines():
                    if line.startswith("data:"):
                        cmd_id = json.loads(line[5:].strip())["id"]
                        break
                if cmd_id:
                    break
            if cmd_id:
                break
            await asyncio.sleep(0.01)
        assert cmd_id, out
        await _asgi_post(app, "/api/editor/bridge/result",
                         {"id": cmd_id, "ok": True, "result": {"width": 10, "height": 20}})
        await asyncio.wait_for(cmd_task, 5)
        body = json.loads(cmd_result["body"].decode())
        assert body.get("ok") is True and body.get("result") == {"width": 10, "height": 20}, body
        stop.set()
        try:
            await asyncio.wait_for(sse, 2)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    asyncio.run(scenario())


def test_bridge_broadcasts_to_every_subscriber():
    """URGENT regression: with two live subscribers on the same session (EventSource
    reconnects overlap), a command must reach BOTH, never be eaten by one."""
    import asyncio

    app = _bridge_app()

    async def scenario():
        outs = {"A": [], "B": []}
        stops = {"A": asyncio.Event(), "B": asyncio.Event()}

        def subscriber(name):
            async def receive():
                await stops[name].wait()
                return {"type": "http.disconnect"}

            async def send(msg):
                if msg["type"] == "http.response.body" and msg.get("body"):
                    outs[name].append(msg["body"])

            return asyncio.create_task(app(
                {"type": "http", "method": "GET", "path": "/api/editor/bridge/events",
                 "query_string": b"session=D", "headers": []}, receive, send))

        tasks = [subscriber("A"), subscriber("B")]
        for _ in range(200):
            if all(any(b": connected" in b for b in outs[n]) for n in ("A", "B")):
                break
            await asyncio.sleep(0.01)

        await _asgi_post(app, "/api/editor/bridge/command",
                         {"session": "D", "steps": [{"op": "screenshot"}], "wait": False})
        for _ in range(300):
            if all(any(b"data:" in b for b in outs[n]) for n in ("A", "B")):
                break
            await asyncio.sleep(0.01)

        for name in ("A", "B"):
            assert any(b'"op": "screenshot"' in b for b in outs[name]), (name, outs)
        for e in stops.values():
            e.set()
        for t in tasks:
            try:
                await asyncio.wait_for(t, 2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

    asyncio.run(scenario())


def test_editor_image_models_route_shape():
    """The editor model selector reads the available image models from here."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    app = FastAPI()
    app.include_router(eaar.setup_editor_agent_routes())
    c = TestClient(app)
    r = c.get("/api/editor/image-models")
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data.get("models"), list)
    for m in data["models"]:
        assert set(("id", "label", "kind", "provider")).issubset(m)
        assert m["kind"] in ("edit", "generate")


def test_ai_edit_image_tool_forwards_and_reports_model(monkeypatch):
    """A picked model is forwarded to the provider (_model) and reported back."""
    import asyncio
    import base64
    import io

    from PIL import Image

    import src.agent_tools.editor_tools as et

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 20, 30)).save(buf, "PNG")
    img = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    captured = {}

    async def _fake_post(path, payload, owner, timeout=120):
        captured["payload"] = payload
        return {"image": img, "model": "qwen-image-edit-2511"}

    async def _no_place(*a, **k):
        return True

    monkeypatch.setattr(et, "_post", _fake_post)
    monkeypatch.setattr(et, "_place_layer", _no_place)
    monkeypatch.setattr(et, "_persist_png", lambda *a, **k: {})
    monkeypatch.setattr(et, "_provider_available", lambda owner: True)

    _desc, res = asyncio.run(et.AiEditImageTool().execute(
        json.dumps({"op": "generative", "mode": "instruction", "prompt": "beach",
                    "image": img, "model": "qwen-image-edit-2511", "place": False}),
        {"owner": "u"},
    ))
    assert res.get("exit_code") == 0, res
    assert captured["payload"].get("_model") == "qwen-image-edit-2511", captured["payload"]
    assert res.get("model") == "qwen-image-edit-2511", res


def test_agent_path_injects_selected_model_into_ai_step(client, monkeypatch):
    """The /api/editor/agent body `model` reaches the AI step tool payload."""
    monkeypatch.setattr(eaar, "_image_provider_available", lambda owner: True)
    captured = {}

    class _CapturingTool:
        async def execute(self, content, ctx):  # noqa: ANN001
            captured["content"] = json.loads(content)
            return ("ai_edit_image", {"exit_code": 0, "image_id": "g1", "placed": True,
                                      "output": "ok", "model": "qwen-image-edit-2511"})

    from src.agent_tools import editor_tools

    monkeypatch.setattr(editor_tools, "AiEditImageTool", _CapturingTool)
    monkeypatch.setattr(editor_tools, "GenerateImageLayerTool", _CapturingTool)

    async def _fake_wait(fut, timeout=None, **kwargs):  # noqa: ANN001
        return {"ok": True, "result": {"width": 100, "height": 100}}

    monkeypatch.setattr(eaar.asyncio, "wait_for", _fake_wait)

    r = client.post("/api/editor/agent",
                    json={"prompt": "put her on a beach", "state": {"width": 100, "height": 100},
                          "model": "qwen-image-edit-2511"})
    assert r.status_code == 200, r.text
    assert captured.get("content", {}).get("model") == "qwen-image-edit-2511", captured
    data = r.json()
    assert data["results"][-1]["result"].get("model") == "qwen-image-edit-2511", data


# ---------------------------------------------------------------------------
# Editor agent/planner model (local Qwen3.5-9B) resolution + reporting.
# ---------------------------------------------------------------------------

def test_editor_agent_models_route_shape(client):
    """The agent-model picker reads the local planner model + hosted fallback."""
    r = client.get("/api/editor/agent-models")
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data.get("models"), list)
    assert isinstance(data.get("probe"), dict)
    for m in data["models"]:
        assert set(("id", "label", "source", "vision")).issubset(m)
        assert m["source"] in ("local", "hosted")


def test_list_editor_agent_models_includes_base_url():
    import asyncio

    import src.editor_agent_model as eam

    models = asyncio.run(eam.list_editor_agent_models(None))
    assert any(m["source"] == "local" for m in models), models
    for m in models:
        assert "base_url" in m


def test_plan_reports_agent_model(client):
    eaar.set_editor_planner(
        lambda messages, owner: json.dumps(
            {"reply": "ok", "steps": [{"op": "filters.apply", "params": {"id": "sharpen"}}]}
        )
    )
    r = client.post("/api/editor/plan",
                    json={"prompt": "sharpen it", "state": {}, "agent_model": "MYPLANNER"})
    assert r.status_code == 200, r.text
    assert r.json().get("agent_model") == "MYPLANNER", r.json()


def test_agent_reports_agent_model(client, monkeypatch):
    monkeypatch.setattr(eaar, "_image_provider_available", lambda owner: True)
    eaar.set_editor_planner(
        lambda messages, owner: json.dumps(
            {"reply": "ok", "steps": [{"op": "adjustments.apply", "params": {"type": "vibrance"}}]}
        )
    )

    async def _fake_wait(fut, timeout=None, **kwargs):  # noqa: ANN001
        try:
            fut.close()
        except Exception:
            pass
        return {"ok": True, "result": {"width": 100, "height": 100}}

    monkeypatch.setattr(eaar.asyncio, "wait_for", _fake_wait)
    r = client.post("/api/editor/agent",
                    json={"prompt": "make it vivid", "state": {"width": 100, "height": 100},
                          "agent_model": "MYPLANNER"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("agent_model") == "MYPLANNER", data
    assert data["trace"][-1].get("agent_model") == "MYPLANNER" or data["trace"][1].get("agent_model") == "MYPLANNER", data


def test_editor_agent_model_override_is_first_candidate(monkeypatch):
    import src.editor_agent_model as eam

    monkeypatch.setattr(eam, "local_bases", lambda: ["http://local1"])
    monkeypatch.setattr(eam, "local_model", lambda: "LMODEL")
    cands = eam._candidates(None, "http://ovr::OVERRIDE")
    assert cands[0] == {"base": "http://ovr", "model": "OVERRIDE", "headers": None,
                        "source": "override"}
    assert any(c["source"] == "local" and c["base"] == "http://local1" for c in cands)


def test_editor_agent_model_uses_vision_first(monkeypatch):
    import asyncio

    import src.editor_agent_model as eam

    calls = []

    async def fake_post(base, model, messages, headers, timeout, max_tokens=1024):  # noqa: ANN001
        calls.append((base, eam._has_image(messages)))
        return "PLAN" if base == "http://local1" and eam._has_image(messages) else None

    monkeypatch.setattr(eam, "_candidates", lambda owner, override: [
        {"base": "http://local1", "model": "m1", "headers": None, "source": "local"}])
    monkeypatch.setattr(eam, "_post_chat", fake_post)

    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "x"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]}]
    res = asyncio.run(eam.plan_with_editor_agent(msgs, None))
    assert res == ("PLAN", "m1", "local"), res
    assert calls == [("http://local1", True)]


def test_editor_agent_model_caption_fallback(monkeypatch):
    import asyncio

    import src.editor_agent_model as eam

    async def fake_post(base, model, messages, headers, timeout, max_tokens=1024):  # noqa: ANN001
        return None if eam._has_image(messages) else "PLAN"

    async def fake_caption(url, owner=None, timeout=90):  # noqa: ANN001
        return "a beach at sunset"

    monkeypatch.setattr(eam, "_candidates", lambda owner, override: [
        {"base": "http://local1", "model": "m1", "headers": None, "source": "local"}])
    monkeypatch.setattr(eam, "_post_chat", fake_post)
    monkeypatch.setattr(eam, "caption_image", fake_caption)

    msgs = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]}]
    res = asyncio.run(eam.plan_with_editor_agent(msgs, None))
    assert res == ("PLAN", "m1", "local+caption"), res


def test_editor_agent_model_returns_none_when_all_fail(monkeypatch):
    import asyncio

    import src.editor_agent_model as eam

    async def fake_post(*a, **k):  # noqa: ANN001
        return None

    async def fake_caption(*a, **k):  # noqa: ANN001
        return None

    monkeypatch.setattr(eam, "_candidates", lambda owner, override: [
        {"base": "b", "model": "m", "headers": None, "source": "local"}])
    monkeypatch.setattr(eam, "_post_chat", fake_post)
    monkeypatch.setattr(eam, "caption_image", fake_caption)

    msgs = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]}]
    assert asyncio.run(eam.plan_with_editor_agent(msgs, None)) is None


def test_plan_special_recolor_overrides_a_model_plan(client):
    # A deterministic recolour must win over any model-written plan
    # (no LLM variance / no samText+placeImage substitution).
    eaar.set_editor_planner(lambda messages, owner: json.dumps({
        "reply": "ok",
        "steps": [{"op": "adjustments.apply", "params": {"type": "vibrance"}}]}))
    r = client.post("/api/editor/plan", json={
        "prompt": "make only the red car blue",
        "state": {"width": 900, "height": 600}})
    assert r.status_code == 200
    data = r.json()
    assert data["planner"] == "deterministic"
    assert [s["op"] for s in data["steps"]] == [
        "selection.samText", "adjustments.applyMasked"]
    assert "selection.fill" not in [s["op"] for s in data["steps"]]


def test_self_check_endpoint_parses_retry(client, monkeypatch):
    import src.editor_agent_model as eam

    async def _retry(*a, **k):  # noqa: ANN001
        return "RETRY: brighten the subject"

    monkeypatch.setattr(eam, "_post_chat", _retry)
    r = client.post("/api/editor/self-check", json={
        "before": "data:image/png;base64,AA",
        "after": "data:image/png;base64,AA",
        "prompt": "did it get brighter?"})
    assert r.status_code == 200
    data = r.json()
    assert data["done"] is False
    assert "brighten" in data["retry_hint"]
    assert "RETRY" in data["text"]


def test_self_check_endpoint_parses_done(client, monkeypatch):
    import src.editor_agent_model as eam

    async def _done(*a, **k):  # noqa: ANN001
        return "DONE"

    monkeypatch.setattr(eam, "_post_chat", _done)
    r = client.post("/api/editor/self-check", json={
        "before": "data:image/png;base64,AA",
        "after": "data:image/png;base64,AA",
        "prompt": "ok?"})
    assert r.status_code == 200
    assert r.json()["done"] is True


def test_self_check_endpoint_unavailable_when_vlm_down(client, monkeypatch):
    import src.editor_agent_model as eam

    async def _none(*a, **k):  # noqa: ANN001
        return None

    monkeypatch.setattr(eam, "_post_chat", _none)
    r = client.post("/api/editor/self-check", json={
        "before": "data:image/png;base64,AA",
        "after": "data:image/png;base64,AA",
        "prompt": "ok?"})
    assert r.status_code == 200
    data = r.json()
    assert data["done"] is True
    assert data.get("reason") == "unavailable"


def test_self_check_endpoint_unavailable_without_images(client):
    r = client.post("/api/editor/self-check", json={"prompt": "ok?"})
    assert r.status_code == 200
    data = r.json()
    assert data["done"] is True
    assert data.get("reason") == "unavailable"


# ── Round: planner-unsure asks; whole-image restyle is a model edit ──────────
def test_plan_asks_when_no_intent_matches(client):
    """A prompt mapped to no concrete intent must ASK (2-3 concrete choices),
    never silently apply a default photo.autoTone/adjustments/filters stack."""
    eaar.set_editor_planner(lambda messages, owner: None)
    r = client.post("/api/editor/plan", json={"prompt": "give it some oomph", "state": {}})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["steps"] == [], data
    ask = data.get("ask_user")
    assert isinstance(ask, dict), data
    assert 2 <= len(ask["options"]) <= 3, ask
    for s in data["steps"]:
        assert s.get("op") not in {"adjustments.apply", "filters.apply", "photo.autoTone"}


def test_agent_path_asks_when_no_intent_matches(client):
    eaar.set_editor_planner(lambda messages, owner: None)
    r = client.post("/api/editor/agent",
                    json={"prompt": "give it some oomph", "session": "s1",
                          "editor_session": "s1", "state": {"width": 100, "height": 100}})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("ask_user_pending") is True, data
    assert data["steps"] == [], data
    assert data["results"] == [], data
    ask = data.get("ask_user")
    assert isinstance(ask, dict), data
    assert 2 <= len(ask["options"]) <= 3, ask


@pytest.mark.parametrize("prompt", [
    "make it look like an oil painting",
    "turn this into an anime",
    "make it a watercolour",
    "pixel art this",
    "make it surreal",
    "restyle as a comic",
])
def test_restyle_prompts_classified_as_model_edit(prompt):
    assert eaar._generative_kind(prompt) == "restyle"


@pytest.mark.parametrize("prompt", [
    "make it look like an oil painting",
    "turn this into an anime",
    "make it a watercolour",
])
def test_plan_routes_restyle_to_ai_edit_image(client, prompt):
    eaar.set_editor_planner(lambda messages, owner: None)
    data = client.post("/api/editor/plan", json={"prompt": prompt, "state": {}}).json()
    ops = [s["op"] for s in data["steps"]]
    assert ops == ["ai_edit_image"], data
    params = data["steps"][0]["params"]
    assert params["mode"] == "instruction", params
    assert "exactly the same" in params["prompt"], params


def test_restyle_does_not_hijack_tone_or_new_image(client):
    """Restyle must not grab tone prompts, nor a brand-new image request."""
    for prompt in ("make it black and white", "blur the background", "add a vignette",
                   "make it look snowy", "make it warmer"):
        assert eaar._generative_kind(prompt) != "restyle", prompt
    assert eaar._generative_kind("create an illustration of a cat") == "add"


def test_agent_path_forces_restyle_over_a_local_adjustment_plan(client, monkeypatch):
    _install_provider_and_fake_tools(monkeypatch)
    eaar.set_editor_planner(
        lambda messages, owner: json.dumps({
            "reply": "vibrance",
            "steps": [{"op": "adjustments.apply",
                       "params": {"type": "vibrance", "params": {"vibrance": 20}}}],
        })
    )
    r = client.post("/api/editor/agent",
                    json={"prompt": "make it look like an oil painting", "session": "s1",
                          "editor_session": "s1", "state": {"width": 100, "height": 100}})
    assert r.status_code == 200, r.text
    ops = [s["op"] for s in r.json()["steps"]]
    assert "ai_edit_image" in ops, r.json()["steps"]
    assert "adjustments.apply" not in ops, r.json()["steps"]


def test_plan_asks_when_model_returns_only_a_generic_tone_plan(client):
    """Even when the model returns a plan, a bare whole-image tone stack for an
    unspecified prompt is a guess -> ask (2-3 concrete choices)."""
    eaar.set_editor_planner(lambda messages, owner: json.dumps({
        "reply": "punchy",
        "steps": [
            {"op": "photo.autoTone", "params": {}},
            {"op": "adjustments.apply", "params": {"type": "vibrance", "params": {"vibrance": 25}}},
            {"op": "filters.apply", "params": {"id": "clarity"}},
        ],
    }))
    data = client.post("/api/editor/plan", json={"prompt": "give it some oomph", "state": {}}).json()
    assert data["steps"] == [], data
    ask = data.get("ask_user")
    assert isinstance(ask, dict), data
    assert 2 <= len(ask["options"]) <= 3, ask


def test_plan_keeps_a_generic_tone_plan_when_a_concrete_intent_matched(client):
    eaar.set_editor_planner(lambda messages, owner: json.dumps({
        "reply": "punchy",
        "steps": [
            {"op": "photo.autoTone", "params": {}},
            {"op": "adjustments.apply", "params": {"type": "vibrance", "params": {"vibrance": 25}}},
        ],
    }))
    data = client.post("/api/editor/plan", json={"prompt": "make it pop", "state": {}}).json()
    assert data["steps"], data
    assert not data.get("ask_user"), data


def test_agent_path_asks_when_model_returns_only_a_generic_tone_plan(client):
    eaar.set_editor_planner(lambda messages, owner: json.dumps({
        "reply": "punchy", "steps": [{"op": "photo.autoTone", "params": {}}]}))
    r = client.post("/api/editor/agent",
                    json={"prompt": "give it some oomph", "session": "s1",
                          "editor_session": "s1", "state": {"width": 100, "height": 100}})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("ask_user_pending") is True, data
    assert data["steps"] == [], data


# ── Strict ops schema (structured outputs) ──────────────────────────────────

def test_plan_schema_is_a_strict_ops_enum():
    props = eaar.PLAN_SCHEMA["properties"]
    enum = props["steps"]["items"]["properties"]["op"]["enum"]
    # New model-facing ops plus the deterministic catalog.
    for op in ("generate_edit", "select_object", "ask_user", "adjustments.apply",
               "filters.apply", "selection.samText"):
        assert op in enum, op
    assert props["steps"]["items"]["required"] == ["op"]
    assert eaar.PLAN_RESPONSE_FORMAT["type"] == "json_schema"
    assert eaar.PLAN_RESPONSE_FORMAT["json_schema"]["schema"] is eaar.PLAN_SCHEMA


def test_validate_plan_translates_model_ops_to_execution_ops():
    gen = eaar._validate_plan(
        {"reply": "r", "steps": [{"op": "generate_edit",
                                  "params": {"prompt": "make it snowy",
                                             "mode": "instruction"}}]})
    assert gen["steps"][0]["op"] == "ai_edit_image"
    sel = eaar._validate_plan(
        {"reply": "r", "steps": [{"op": "select_object", "params": {"text": "the red car"}}]})
    assert sel["steps"][0]["op"] == "selection.samText"
    assert sel["steps"][0]["params"]["text"] == "the red car"


def test_validate_plan_normalizes_ai_edit_image_inner_op():
    # Regression: the :8097 model authors a restyle as ai_edit_image with NO inner
    # op; it must be repaired to op "generative" (instruction mode).
    no_inner = eaar._validate_plan({"reply": "r", "steps": [
        {"op": "ai_edit_image", "params": {"mode": "instruction", "prompt": "oil painting"}}]})
    assert no_inner["steps"][0]["params"]["op"] == "generative"
    assert no_inner["steps"][0]["params"]["mode"] == "instruction"
    # An invented inner op ("restyle") is repaired too.
    bad = eaar._validate_plan({"reply": "r", "steps": [
        {"op": "ai_edit_image", "params": {"op": "restyle", "mode": "instruction", "prompt": "anime"}}]})
    assert bad["steps"][0]["params"]["op"] == "generative"
    # generate_edit is translated AND normalized.
    ge = eaar._validate_plan({"reply": "r", "steps": [
        {"op": "generate_edit", "params": {"prompt": "restyle"}}]})
    assert ge["steps"][0]["op"] == "ai_edit_image"
    assert ge["steps"][0]["params"]["op"] == "generative"
    # A masked edit with no inner op becomes inpaint.
    masked = eaar._validate_plan({"reply": "r", "steps": [
        {"op": "ai_edit_image", "params": {"mode": "masked", "prompt": "remove the sign"}}]})
    assert masked["steps"][0]["params"]["op"] == "inpaint"
    assert masked["steps"][0]["params"]["mode"] == "masked"
    # A valid inner op is left untouched.
    keep = eaar._validate_plan({"reply": "r", "steps": [
        {"op": "ai_edit_image", "params": {"op": "remove-bg"}}]})
    assert keep["steps"][0]["params"]["op"] == "remove-bg"


def test_validate_plan_drops_a_single_bad_step_and_empty_plans():
    # A single unknown/hallucinated op is DROPPED; the valid steps survive.
    mixed = eaar._validate_plan(
        {"reply": "r", "steps": [{"op": "adjustments.apply", "params": {}}, {"op": "evil.op"}]})
    assert [s["op"] for s in mixed["steps"]] == ["adjustments.apply"]
    # A plan whose only step is invalid has nothing left -> None (ask_user upstream).
    assert eaar._validate_plan({"reply": "r", "steps": [{"op": "convert_to_grayscale"}]}) is None
    assert eaar._validate_plan({"reply": "r", "steps": []}) is None
    assert eaar._validate_plan("not a dict") is None


def test_validate_plan_ask_user_needs_two_to_three_choices():
    ok = eaar._validate_plan({"reply": "q", "steps": [],
                              "ask_user": {"question": "Which?", "options": ["A", "B"]}})
    assert ok["ask_user"]["options"] == [{"label": "A", "description": ""},
                                         {"label": "B", "description": ""}]
    trimmed = eaar._validate_plan({"reply": "q", "steps": [],
                                   "ask_user": {"question": "Which?", "options": ["A", "B", "C", "D"]}})
    assert len(trimmed["ask_user"]["options"]) == 3
    # Fewer than two choices is not a valid ask_user.
    assert eaar._validate_plan({"reply": "q", "steps": [],
                                "ask_user": {"question": "Which?", "options": ["only"]}}) is None


def test_plan_route_invalid_model_plan_asks_not_a_tone_default(client):
    # A hallucinated op invalidates the plan; the route must ask, never tweak.
    eaar.set_editor_planner(lambda messages, owner: json.dumps(
        {"reply": "made it gray", "steps": [{"op": "convert_to_grayscale", "params": {}}]}))
    data = client.post("/api/editor/plan", json={"prompt": "do the thing", "state": {}}).json()
    assert data.get("ask_user"), data
    assert data["steps"] == [], data


def test_plan_route_translates_generate_edit(client):
    eaar.set_editor_planner(lambda messages, owner: json.dumps(
        {"reply": "restyling", "steps": [{"op": "generate_edit",
                                          "params": {"prompt": "make it a watercolour",
                                                     "mode": "instruction"}}]}))
    data = client.post("/api/editor/plan",
                       json={"prompt": "do the thing", "state": {"width": 100, "height": 100}}).json()
    assert [s["op"] for s in data["steps"]] == ["ai_edit_image"], data


def test_restyle_keeps_a_model_chosen_generate_edit(client, monkeypatch):
    # With the strict schema + generate_edit available, the planner itself picks
    # the AI op for a restyle; the _RESTYLE_RE keyword list is only a fast path,
    # so a valid model generate_edit is NOT overwritten by the templated step.
    _install_provider_and_fake_tools(monkeypatch)
    eaar.set_editor_planner(lambda messages, owner: json.dumps(
        {"reply": "oil painting", "steps": [{"op": "generate_edit",
                                             "params": {"prompt": "render as an impressionist oil painting",
                                                        "mode": "instruction"}}]}))
    r = client.post("/api/editor/agent",
                    json={"prompt": "make it look like an oil painting", "session": "s1",
                          "editor_session": "s1", "state": {"width": 100, "height": 100}})
    assert r.status_code == 200, r.text
    data = r.json()
    assert [s["op"] for s in data["steps"]] == ["ai_edit_image"], data["steps"]


def test_agent_restyle_with_model_shape_repairs_inner_op(client, monkeypatch):
    """Regression (m2334): in AGENT mode the :8097 model authors a restyle as an
    ai_edit_image step WITHOUT a valid inner op, which used to be blocked. It must
    be repaired to op 'generative' and run (ok)."""
    _install_provider_and_fake_tools(monkeypatch)
    eaar.set_editor_planner(lambda messages, owner: json.dumps(
        {"reply": "painting", "steps": [{"op": "ai_edit_image",
                                         "params": {"mode": "instruction",
                                                    "prompt": "restyle as an oil painting"}}]}))
    r = client.post("/api/editor/agent",
                    json={"prompt": "make it look like an oil painting", "session": "s1",
                          "editor_session": "s1", "state": {"width": 100, "height": 100}})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True, data
    ai = [s for s in data["steps"] if s["op"] == "ai_edit_image"]
    assert ai and ai[0]["params"].get("op") == "generative", data["steps"]


def test_restyle_keyword_is_still_a_fast_path_when_model_returns_no_ai_op(client, monkeypatch):
    # Control: a model adjustment plan for a restyle is still forced to the
    # deterministic restyle step (the fast path), so the keyword list stays a
    # safety net.
    _install_provider_and_fake_tools(monkeypatch)
    eaar.set_editor_planner(lambda messages, owner: json.dumps(
        {"reply": "vivid", "steps": [{"op": "adjustments.apply",
                                      "params": {"type": "vibrance"}}]}))
    r = client.post("/api/editor/agent",
                    json={"prompt": "make it look like an oil painting", "session": "s1",
                          "editor_session": "s1", "state": {"width": 100, "height": 100}})
    data = r.json()
    assert [s["op"] for s in data["steps"]] == ["ai_edit_image"], data["steps"]


def test_editor_agent_plan_response_format_only_sent_to_local(monkeypatch):
    from src import editor_agent_model as eam

    calls = []

    async def _fake_post(base, model, messages, headers, timeout, **kwargs):
        calls.append((base, kwargs.get("response_format")))
        return json.dumps({"reply": "r", "steps": [{"op": "adjustments.apply",
                                                     "params": {"type": "vibrance"}}]})

    monkeypatch.setattr(eam, "_post_chat", _fake_post)
    monkeypatch.setattr(eam, "_candidates", lambda owner, override: [
        {"base": "http://100.69.120.65:8097/v1", "model": "Qwen/Qwen3.5-9B",
         "headers": None, "source": "local"},
        {"base": "https://api.deepseek.com/v1", "model": "deepseek-v4-flash",
         "headers": None, "source": "hosted"},
    ])
    import asyncio
    fmt = {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}}
    out = asyncio.run(eam.plan_with_editor_agent(
        [{"role": "user", "content": "x"}], response_format=fmt))
    assert out is not None
    # The local (first) candidate got the structured-output constraint; the
    # request short-circuits on the first success, so only the local call ran.
    assert calls and calls[0][0] == "http://100.69.120.65:8097/v1"
    assert calls[0][1] is fmt
    assert all(base != "https://api.deepseek.com/v1" for base, _ in calls)
