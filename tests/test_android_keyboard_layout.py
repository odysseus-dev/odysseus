from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANDROID_MANIFEST = (ROOT / "android/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
MAIN_ACTIVITY = (
    ROOT
    / "android"
    / "app"
    / "src"
    / "main"
    / "java"
    / "com"
    / "odysseus"
    / "simplesignal"
    / "MainActivity.java"
).read_text(encoding="utf-8")
APP_JS = (ROOT / "static/app.js").read_text(encoding="utf-8")
INIT_JS = (ROOT / "static/js/init.js").read_text(encoding="utf-8")
SIDEBAR_LAYOUT_JS = (ROOT / "static/js/sidebar-layout.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static/style.css").read_text(encoding="utf-8")


def test_android_keyboard_does_not_resize_or_pan_whole_webview():
    assert 'android:windowSoftInputMode="adjustNothing"' in ANDROID_MANIFEST
    assert "getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_NOTHING);" in MAIN_ACTIVITY
    assert "public void hideKeyboard()" in MAIN_ACTIVITY
    assert "InputMethodManager" in MAIN_ACTIVITY
    assert "imm.hideSoftInputFromWindow" in MAIN_ACTIVITY


def test_android_webview_lifts_composer_with_keyboard_inset():
    assert "function initAndroidKeyboardInsets()" in INIT_JS
    assert "root.classList.add('android-webview');" in INIT_JS
    assert "--android-keyboard-inset" in INIT_JS
    assert "root.classList.toggle('android-keyboard-open', inset > 0);" in INIT_JS
    assert "window.visualViewport.addEventListener('resize', schedule);" in INIT_JS
    assert "odysseus:keyboard-inset-change" in INIT_JS

    assert "html.android-webview body" in STYLE_CSS
    assert "height: 100vh;" in STYLE_CSS
    assert "html.android-webview.android-keyboard-open .chat-container" in STYLE_CSS
    assert "padding-bottom: calc(10px + var(--android-keyboard-inset, 0px));" in STYLE_CSS
    assert "html.android-webview.android-keyboard-open .chat-history" in STYLE_CSS


def test_menus_dismiss_mobile_keyboard_before_opening():
    assert "function dismissSoftKeyboard()" in APP_JS
    assert "window.OdysseusAndroid?.hideKeyboard?.();" in APP_JS
    assert "plusBtn.addEventListener('pointerdown'" in APP_JS
    assert "const dismissedKeyboard = dismissSoftKeyboard();" in APP_JS
    assert "keyboardDismissSettleDelay()" in APP_JS

    assert "function _dismissSoftKeyboard()" in SIDEBAR_LAYOUT_JS
    assert "window.OdysseusAndroid?.hideKeyboard?.();" in SIDEBAR_LAYOUT_JS
    assert "if (_dismissSoftKeyboard())" in SIDEBAR_LAYOUT_JS
    assert "setTimeout(() => {" in SIDEBAR_LAYOUT_JS
