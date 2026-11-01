from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "static/js/editor/layer-panel.js").read_text(encoding="utf-8")
SORT = (ROOT / "static/js/dragSort.js").read_text(encoding="utf-8")


def test_layer_rows_are_draggable_from_anywhere():
    block = PANEL[PANEL.index("dragSortModule.enable('ge-layers-list'"):]
    block = block[: block.index("onReorder:")]
    # The whole row (grip, thumbnail, name) starts a reorder once the
    # pointer moves past the threshold; interactive controls are excluded.
    assert "threshold: 4" in block
    assert "ignoreSelector:" in block
    assert "handleSelector: '.ge-layer-drag'" not in block


def test_drag_sort_waits_for_the_movement_threshold():
    assert "threshold: Math.max(0, Number(options.threshold) || 0)," in SORT
    assert "pending = { item, startY: e.clientY };" in SORT
    assert "Math.abs(e.clientY - pending.startY) < config.threshold" in SORT
    assert "pending = null;" in SORT
