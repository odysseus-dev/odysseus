from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_login_page_loads_i18n_and_marks_core_fields():
    html = _read("static/login.html")

    assert 'src="/static/js/i18n.js"' in html
    assert 'data-i18n="auth.username"' in html
    assert 'data-i18n="auth.password"' in html
    assert 'data-i18n-title="auth.rememberMe"' in html
    assert 'data-i18n-aria-label="auth.rememberMe"' in html
    assert 'data-i18n-aria-label="auth.showPassword"' in html
    assert 'data-i18n="auth.signIn"' in html


def test_app_shell_loads_i18n_and_marks_core_controls():
    html = _read("static/index.html")

    assert 'src="/static/js/i18n.js"' in html
    assert 'data-i18n-title="chat.newChat"' in html
    assert 'data-i18n-placeholder="chat.placeholder"' in html
    assert 'data-i18n-title="chat.webSearch"' in html
    assert 'data-i18n-title="chat.shellAccess"' in html
    assert 'data-i18n="chat.agent"' in html
    assert 'data-i18n="chat.chat"' in html


def test_settings_language_selector_is_present_and_persists_choice():
    html = _read("static/index.html")
    settings_js = _read("static/js/settings.js")

    assert 'id="set-language"' in html
    assert 'data-i18n="settings.language"' in html
    assert "function initLanguageSelector()" in settings_js
    assert "window.i18n.setLanguage" in settings_js
    assert "fetch('/api/prefs/language', { method: 'PUT'" in settings_js
