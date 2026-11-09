"""Regression coverage for transient toast polish and accurate welcome tips."""

from pathlib import Path
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parent.parent


def test_notification_history_does_not_clutter_navigation_or_capture_toasts():
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    ui = (ROOT / "static/js/ui.js").read_text(encoding="utf-8")
    css = app_css()

    assert 'id="rail-notifications"' not in html
    assert 'id="sidebar-notifications-btn"' not in html
    assert "data-notification-history-trigger" not in html
    assert "odysseus.notification-history" not in ui
    assert "_recordNotification" not in ui
    assert ".notification-history-panel" not in css


def test_action_hint_is_part_of_action_button_and_close_is_grouped_beside_it():
    ui = (ROOT / "static/js/ui.js").read_text(encoding="utf-8")

    hint = ui.index("btn.appendChild(hint)")
    action_group = ui.index("actionGroup.appendChild(btn)", hint)
    close_group = ui.index("actionGroup.appendChild(closeBtn)", action_group)
    toast_group = ui.index("toastEl.appendChild(actionGroup)", close_group)

    assert hint < action_group < close_group < toast_group


def test_welcome_tips_are_plain_and_brief():
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    css = app_css()

    assert "Tip: Ctrl+K searches chats." in html
    assert "Tip: Ctrl+Alt+B toggles the sidebar." in html
    assert "Tip: Open a chat’s actions menu to rename, archive, or delete it." in html
    assert "Tip: Long-press a chat for actions." in html
    assert "Right-click a session" not in html
    assert "memory options" not in html
    assert 'id="memory-session-option"' not in html
    assert "welcome-tip-label" not in html
    assert "welcome-tip-text" not in html
    assert "el.textContent = tips[" in html
    assert "Open Notifications in the sidebar" not in html
    assert "#welcome-screen .welcome-tip" in css
    assert 'id="welcome-tip" role="note"' in html
    assert "opacity:0.88" in css


def test_sidebar_shortcut_avoids_browser_bookmark_binding():
    keyboard = (ROOT / "static/js/keyboard-shortcuts.js").read_text(encoding="utf-8")
    settings = (ROOT / "static/js/settings.js").read_text(encoding="utf-8")

    assert "toggle_sidebar: 'ctrl+alt+b'" in keyboard
    assert "toggle_sidebar: 'ctrl+alt+b'" in settings
    assert "kb.toggle_sidebar" in keyboard
    assert "el('hamburger-btn')" in keyboard
    assert "=== 'ctrl+b'" in keyboard
    assert "=== 'ctrl+b'" in settings
