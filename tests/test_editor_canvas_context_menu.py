import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static/js/editor/canvas-context-menu.js").as_uri()
STATE = (ROOT / "static/js/editor/state.js").as_uri()


def run_node(body: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", textwrap.dedent(body)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def labels(items):
    out = []
    for item in items:
        if item.get("type") == "sep":
            out.append("—")
        else:
            out.append(item["label"])
    return out


def menu_for(tool: str, selection: bool = False):
    return run_node(
        f"""
        import {{ state }} from {json.dumps(STATE)};
        import {{ buildCanvasMenuItems }} from {json.dumps(MODULE)};
        state.editorOpen = true;
        state.tool = {json.dumps(tool)};
        state.wandMask = {json.dumps({"w": 1, "h": 1} if selection else None)};
        state.brushPixel = false;
        const items = buildCanvasMenuItems(state.tool);
        const out = items.map((i) => ({{ type: i.type || 'item', label: i.label || '', disabled: !!i.disabled, hint: i.hint || '' }}));
        console.log(JSON.stringify(out));
        """
    )


def test_paint_tool_menu_has_brush_controls():
    items = menu_for("brush")
    ls = labels(items)
    assert "Increase Brush Size" in ls
    assert "Decrease Brush Size" in ls
    assert "Brush Preset" in ls
    assert "Pixel Brush" in ls
    # Common edit actions are always available.
    assert "Undo" in ls and "Redo" in ls
    assert "Rotate 90° CW" in ls


def test_pixel_brush_entry_is_checked_when_pixel_mode_active():
    items = run_node(
        f"""
        import {{ state }} from {json.dumps(STATE)};
        import {{ buildCanvasMenuItems }} from {json.dumps(MODULE)};
        state.tool = 'brush';
        state.brushPixel = true;
        const items = buildCanvasMenuItems('brush');
        const pixel = items.find((i) => i.label === 'Pixel Brush');
        console.log(JSON.stringify({{ checked: !!pixel.checked }}));
        """
    )
    assert items["checked"] is True


def test_selection_tool_menu_has_selection_ops():
    ls = labels(menu_for("marquee", selection=True))
    assert "Select All" in ls
    assert "Deselect" in ls
    assert "Inverse Selection" in ls
    assert "Feather…" in ls
    assert "Expand" in ls
    assert "Contract" in ls


def test_selection_items_disabled_without_selection():
    items = menu_for("brush", selection=False)
    by_label = {i["label"]: i for i in items if i["type"] == "item"}
    assert by_label["Cut"]["disabled"] is True
    assert by_label["Copy"]["disabled"] is True
    assert by_label["Paste"]["disabled"] is False


def test_crop_and_transform_tools_have_context_actions():
    crop = labels(menu_for("crop"))
    assert "Apply Crop" in crop
    assert "Cancel Crop" in crop
    transform = labels(menu_for("transform"))
    assert "Commit Transform" in transform
    assert "Cancel Transform" in transform


def test_context_menu_is_wired_into_canvas_events():
    events = (ROOT / "static/js/editor/canvas-events.js").read_text()
    assert "import { wireCanvasContextMenu } from './canvas-context-menu.js';" in events
    assert "wireCanvasContextMenu(canvasArea, { signal });" in events
    module = (ROOT / "static/js/editor/canvas-context-menu.js").read_text()
    assert "export function buildCanvasMenuItems(" in module
    assert "export function openCanvasContextMenu(" in module
    assert "window.geEditorControl?.invoke(" in module
    assert "canvas.addEventListener('contextmenu'" in module


def test_context_menu_styles_exist():
    css = (ROOT / "static/css/13-image-editor.css").read_text()
    assert ".ge-canvas-menu {" in css
    assert ".ge-canvas-menu-item" in css
    assert ".ge-canvas-menu-sub" in css
