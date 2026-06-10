from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HWFIT = ROOT / "static/js/cookbook-hwfit.js"
DOWNLOAD = ROOT / "static/js/cookbookDownload.js"
STYLE = ROOT / "static/style.css"


def test_missing_gguf_message_is_reused_before_download_click():
    download = DOWNLOAD.read_text(encoding="utf-8")
    hwfit = HWFIT.read_text(encoding="utf-8")

    assert "export function _missingGgufMessage" in download
    assert "import { _missingGgufMessage } from './cookbookDownload.js';" in hwfit
    assert "function _missingGgufDownload(model, backend)" in hwfit

    render_list = hwfit.split("export function _hwfitRenderList", 1)[1].split(
        "// Click row",
        1,
    )[0]
    assert "const missingGguf = _missingGgufDownload(m, detectedBackend?.backend);" in render_list
    assert "hwfit-gguf-dot" in render_list
    assert "_missingGgufMessage(m)" in render_list

    panel = hwfit.split("export function _expandModelRow", 1)[1]
    assert panel.index("hwfit-panel-note") < panel.index("hwfit-panel-actions")
    assert "const missingGgufAttrs = missingGguf ? ` disabled title=" in panel
    assert "hwfit-quickrun-btn" in panel
    assert "missingGguf ? ' disabled' : ''" in panel
    assert "_missingGgufMessage(modelData)" in panel


def test_missing_gguf_warning_marker_has_theme_style():
    style = STYLE.read_text(encoding="utf-8")

    assert ".hwfit-gguf-dot" in style
    assert "var(--yellow" in style
    assert "border-radius: 50%" in style
