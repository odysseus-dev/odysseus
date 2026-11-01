"""Quick Edit support for the Photoshop Filter menu's specialty filters.

The `filters.apply {id}` control op already carried the full Filter-menu alias
map (pixelate, emboss, twirl, pinch, spherize, ripple, solarize, find-edges,
crystallize, noise, despeckle, unsharp mask, ...), but the Quick Edit client
fast path never routed them, so "pixelate the image" fell through to
"No built-in quick action matched". These tests pin the new deterministic
branch in galleryEditor.js `_executeQuickEditLocal`.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static" / "js" / "galleryEditor.js").read_text(encoding="utf-8")
COVERAGE = (ROOT / "docs" / "QUICKEDIT_COVERAGE.md").read_text(encoding="utf-8")


def test_specialty_filter_branch_exists():
    assert "// --- Filter menu: specialty filters (deterministic, no model call) ---" in EDITOR
    assert "const hit = SPECIALTY_FILTERS.find(([re]) => (re ? re.test(p) : _specNoise));" in EDITOR
    assert "_runQuickControlOp('filters.apply', { id: hit[1] });" in EDITOR
    assert "_setAiCommandStatus(`Applied the ${hit[2]} filter.`, 'done');" in EDITOR


def test_specialty_filter_maps_every_id():
    for filter_id in (
        "unsharp-mask",
        "pixelate-mosaic",
        "pixelate-crystallize",
        "stylize-emboss",
        "stylize-find-edges",
        "stylize-solarize",
        "distort-twirl",
        "distort-pinch",
        "distort-spherize",
        "distort-ripple",
        "noise-despeckle",
        "noise-add",
    ):
        assert f"'{filter_id}'" in EDITOR, filter_id


def test_noise_requires_an_adding_verb():
    assert "\\b(?:add|apply|give|more)\\b[^.]*\\b(?:film\\s+)?(?:noise|grain)\\b" in EDITOR
    assert "|| /\\bfilm\\s+grain\\b/.test(p);" in EDITOR


def test_filter_branch_guards_against_generative_and_ordering():
    assert "if (hit && !_looksGenerative(p)) {" in EDITOR
    vignette_idx = EDITOR.index("// Vignette / darken-edges.")
    filter_idx = EDITOR.index("// --- Filter menu: specialty filters (deterministic, no model call) ---")
    punch_idx = EDITOR.index('// "punchy / pop" → vibrance')
    assert vignette_idx < filter_idx < punch_idx


def test_filters_apply_implements_every_id():
    apply_idx = EDITOR.index("'filters.apply': {")
    block = EDITOR[apply_idx:apply_idx + 4000]
    for filter_id in (
        "unsharp-mask",
        "pixelate-mosaic",
        "pixelate-crystallize",
        "stylize-emboss",
        "stylize-find-edges",
        "stylize-solarize",
        "distort-twirl",
        "distort-pinch",
        "distort-spherize",
        "distort-ripple",
        "noise-despeckle",
        "noise-add",
    ):
        assert filter_id in block, filter_id


def test_coverage_row_marked_ok():
    assert "| Filter → every filter (30+) | \"pixelate the image\" | `filters.apply` | ✔ | ✔ | ✔ | OK" in COVERAGE
