from pathlib import Path


CSS = Path("static/style.css").read_text(encoding="utf-8")
UI_JS = Path("static/js/ui.js").read_text(encoding="utf-8")


def test_prompt_bar_exposes_desktop_resize_handle():
    assert "resize: vertical;" in CSS
    assert "max-height: min(60vh, 600px);" in CSS


def test_auto_resize_preserves_a_manually_chosen_height():
    assert "textarea._manualResizeHeight = height;" in UI_JS
    assert "const manualHeight = textarea._manualResizeHeight || 0;" in UI_JS
    assert "const maxHeight = Math.max(autoMaxHeight, manualHeight);" in UI_JS


def test_manual_resize_observer_ignores_our_own_programmatic_changes():
    # The textarea animates its height via a CSS transition, so a programmatic
    # resize reaches the target over several frames. Without guarding against
    # our own changes the observer mistakes those transition frames for a drag
    # and locks in a tall floor, leaving the box stuck large after a long
    # message is sent. autoResize must flag programmatic changes and the
    # observer must bail out on them.
    assert "if (textarea._programmaticResize) return;" in UI_JS
    assert "textarea._programmaticResize = true;" in UI_JS
    # The flag is set before the height is mutated so it precedes any callback
    # the mutation schedules.
    flag_pos = UI_JS.index("textarea._programmaticResize = true;")
    height_pos = UI_JS.index("textarea.style.height = newHeight + 'px';")
    assert flag_pos < height_pos
