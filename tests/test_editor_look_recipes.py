import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static/js/editor/look-recipes.js").as_uri()
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")


def _recipe_name(prompt: str):
    source = textwrap.dedent(
        f"""
        import {{ lookRecipeSteps }} from {json.dumps(MODULE)};
        const r = lookRecipeSteps({json.dumps(prompt)});
        process.stdout.write(JSON.stringify(r ? {{ name: r.name, ops: r.steps.map(s => s.op) }} : null));
        """
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def _recipe_steps(prompt: str, strength=None):
    opts = "" if strength is None else f", {{ strength: {strength} }}"
    source = textwrap.dedent(
        f"""
        import {{ lookRecipeSteps }} from {json.dumps(MODULE)};
        const r = lookRecipeSteps({json.dumps(prompt)}{opts});
        process.stdout.write(JSON.stringify(r ? r.steps : null));
        """
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def test_named_looks_map_to_deterministic_recipes():
    for prompt in [
        "make it look like a Wes Anderson film",
        "make it look like a National Geographic photo",
        "make it look like a Kodachrome photo",
        "make it look like an Ansel Adams black and white landscape",
        "make it look like a 1970s film photograph",
        "make it look like a vintage Polaroid",
        "make it look like a film noir",
        "give it a cinematic teal and orange grade",
        "make it look like a golden hour portrait",
        "make it a moody Scandinavian scene",
    ]:
        recipe = _recipe_name(prompt)
        assert recipe is not None, prompt
        # Every step must be one of the supported deterministic control ops.
        assert all(op in {"photo.autoTone", "adjustments.apply", "filters.apply"} for op in recipe["ops"]), prompt
        assert any(op == "adjustments.apply" for op in recipe["ops"]), prompt


def test_content_looks_route_to_the_model_not_a_recipe():
    for prompt in [
        "give it a Blade Runner neon night look",
        "make it a neon-lit city street",
        "give it a Studio Ghibli animated look",
        "make it look like an anime",
        "turn it into an oil painting",
        "make it look like a watercolour",
    ]:
        assert _recipe_name(prompt) is None, prompt


def test_strength_scales_the_recipe():
    base = _recipe_steps("make it look like a Kodachrome photo")
    assert base is not None
    strong = _recipe_steps("make it look like a Kodachrome photo", strength=2)
    assert strong is not None
    # The vibrance-style adjustment must scale up with strength.
    def _max_numeric(steps):
        best = 0.0
        for s in steps:
            if s.get("op") != "adjustments.apply":
                continue
            for v in (s.get("params", {}).get("params", {}) or {}).values():
                if isinstance(v, (int, float)):
                    best = max(best, abs(float(v)))
        return best
    assert _max_numeric(strong) > _max_numeric(base)
    assert _max_numeric(strong) >= 2 * _max_numeric(base) - 1


def test_non_look_prompts_are_not_intercepted():
    for prompt in [
        "rotate 90",
        "brighten the photo",
        "make it warmer",
        "remove the background",
        "add a man to the scene",
        "make it look snowy",
        "make it a square",
    ]:
        assert _recipe_name(prompt) is None, prompt


def test_general_look_deriver_handles_unknown_named_looks():
    recipe = _recipe_name("give it a warm dreamy aesthetic")
    assert recipe is not None
    ops = recipe["ops"]
    assert "photo.autoTone" in ops
    assert "adjustments.apply" in ops


def test_editor_wires_the_look_recipe_branch():
    assert "import { lookRecipeSteps } from './editor/look-recipes.js';" in EDITOR
    assert "const _lookRec = lookRecipeSteps(prompt, { strength: _lookStrength() });" in EDITOR
    assert "for (const step of _lookRec.steps) {" in EDITOR
    # Content-change looks route to the model (creative repaint), not a grade.
    assert r"neon|blade\\s*runner|bloom|holograph" in EDITOR
    ai_settings = (ROOT / "static/js/editor/ai-settings.js").read_text(encoding="utf-8")
    assert 'id="ge-look-strength"' in ai_settings
    assert "localStorage.setItem('ge-look-strength'" in ai_settings
