"""Cookbook running-tab UI regressions (#2829)."""
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def test_cookbook_task_menu_uses_bind_menu_dismiss():
    js = (_REPO / "static" / "js" / "cookbookRunning.js").read_text(encoding="utf-8")
    assert "bindMenuDismiss" in js
    assert "registerMenuDismiss" not in js