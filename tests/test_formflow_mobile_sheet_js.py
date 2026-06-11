"""Regression pins for FormFlow mobile bottom-sheet wiring."""

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def test_formflow_imports_panel_sheet_and_wires_grabber():
    src = (_REPO / "static/js/formflow.js").read_text(encoding="utf-8")
    assert "from './panelSheet.js'" in src
    assert "notes-mobile-grabber" in src
    assert "wireSwipeDismiss" in src
    assert "formflow-view" in src
    assert "closePanel('down')" in src
    assert "rail-formflow" in src


def test_panel_sheet_exports_shared_helpers():
    src = (_REPO / "static/js/panelSheet.js").read_text(encoding="utf-8")
    assert "export function wireSwipeDismiss" in src
    assert "export function collapseSidebarForMobileSheet" in src
    assert "export function isMobileSheet" in src


def test_formflow_route_serves_spa():
    app_src = (_REPO / "app.py").read_text(encoding="utf-8")
    assert 'async def serve_formflow' in app_src
    assert "return await serve_index(request)" in app_src.split("serve_formflow")[1].split("async def")[0]
