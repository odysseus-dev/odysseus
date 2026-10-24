from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_command_palette_module_shape():
    src = read("static/js/editor/build/command-palette.js")
    assert "export function createCommandPalette(" in src
    for token in [
        "ge-command-palette",
        "ge-command-panel",
        "ge-command-input",
        "ge-command-list",
        "ge-command-item",
        "ge-command-empty",
        "role",
        "aria-modal",
    ]:
        assert token in src, token


def test_gallery_wires_command_palette():
    src = read("static/js/galleryEditor.js")
    assert "import { createCommandPalette } from './editor/build/command-palette.js';" in src
    assert "createCommandPalette(" in src
    assert "commandPalette.toggle()" in src
    assert "state.editorOpen" in src
    assert "'k'" in src


def test_command_palette_css_present():
    src = read("static/css/13-image-editor.css")
    for token in [".ge-command-palette", ".ge-command-panel", ".ge-command-item.active", ".ge-command-empty"]:
        assert token in src, token


def test_command_palette_is_precached():
    src = read("static/sw.js")
    assert "/static/js/editor/build/command-palette.js" in src
