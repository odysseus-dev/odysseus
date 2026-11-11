"""Guard the only entry point to the Cookbook → Dependencies recipe panel.

The recipe panel (model picker, Pip/Docker toggle, commands and Run) is
rendered hidden, and only the `[data-dep-recipe-toggle]` caret shows it.
The HW Fit and serve-card deep links (`openCookbookDependencies(..., {
expandRecipe })`) open it by clicking that same caret. When the caret was
dropped from the row markup, nothing could open the panel (#6747).
"""
import re
from pathlib import Path

JS_DIR = Path(__file__).resolve().parent.parent / "static" / "js"
COOKBOOK_JS = JS_DIR / "cookbook.js"
DIAGNOSIS_JS = JS_DIR / "cookbook-diagnosis.js"


def _row_renderer(source: str) -> str:
    start = source.index("const hasRecipe = RECIPE_BACKENDS.has(pkg.name);")
    end = source.index("+ recipePanel;", start)
    return source[start:end]


def test_recipe_rows_render_the_panel_toggle():
    row = _row_renderer(COOKBOOK_JS.read_text(encoding="utf-8"))

    caret = re.search(r"const recipeCaret = hasRecipe\s*\?\s*`([^`]*)`\s*:\s*'';", row)
    assert caret, "recipe rows must render a caret, or the recipe panel cannot be opened"
    assert 'data-dep-recipe-toggle="${esc(pkg.name)}"' in caret.group(1)
    assert 'aria-expanded="false"' in caret.group(1)
    assert "+ recipeCaret" in row


def test_toggle_handler_and_deep_link_use_the_rendered_attribute():
    cookbook = COOKBOOK_JS.read_text(encoding="utf-8")
    diagnosis = DIAGNOSIS_JS.read_text(encoding="utf-8")

    assert "list.querySelectorAll('[data-dep-recipe-toggle]')" in cookbook
    assert "row.querySelector('[data-dep-recipe-toggle]')" in diagnosis
