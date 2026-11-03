from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "static/css/13-image-editor.css"


def _css():
    return CSS.read_text(encoding="utf-8")


def test_mobile_scroll_fade_is_wide_enough_to_read():
    # An 18px fade over a ~40px tool icon read as a hard clip in QA. The
    # affordance must be wide enough to actually look like a fade.
    css = _css()
    assert "mask-image: linear-gradient(to right, transparent 0, #000 42px, #000 calc(100% - 42px), transparent 100%)" in css
    assert "-webkit-mask-image: linear-gradient(to right, transparent 0, #000 42px, #000 calc(100% - 42px), transparent 100%)" in css


def test_scroll_fade_edge_variants_use_42px():
    # At the left end only the right side fades, at the right end only the
    # left side fades — both must match the wider 42px ramp.
    css = _css()
    assert "mask-image: linear-gradient(to right, #000 0, #000 calc(100% - 42px), transparent 100%)" in css
    assert "mask-image: linear-gradient(to right, transparent 0, #000 42px, #000 100%)" in css


def test_scroll_fade_only_applies_to_marked_rails():
    # The fade is opt-in (ge-scroll-fade toggled by attachScrollAffordance) and
    # scoped to the two mobile rails, not every toolbar.
    css = _css()
    assert ".ge-topbar-left.ge-scroll-fade," in css
    assert ".ge-toolbar-scroll.ge-scroll-fade {" in css
