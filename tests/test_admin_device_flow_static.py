"""Static regressions for Add Models provider device-flow UX."""

from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent
_INDEX = (_REPO / "static" / "index.html").read_text(encoding="utf-8")
_ADMIN = (_REPO / "static" / "js" / "admin.js").read_text(encoding="utf-8")


def _between(src: str, start: str, end: str) -> str:
    start_idx = src.index(start)
    end_idx = src.index(end, start_idx)
    return src[start_idx:end_idx]


def test_copilot_and_chatgpt_subscription_are_dropdown_device_auth_options():
    assert 'value="copilot" data-logo="github" data-auth-flow="copilot">GitHub Copilot' in _INDEX
    assert 'value="chatgpt-subscription" data-logo="openai" data-auth-flow="chatgpt-subscription">ChatGPT Subscription' in _INDEX
    assert 'id="adm-deviceAuthStatus"' in _INDEX


def test_provider_selection_is_inert_and_add_button_starts_device_flow():
    change_block = _between(_ADMIN, "provider.addEventListener('change'", "urlInput.addEventListener('input'")
    add_block = _between(_ADMIN, "el('adm-epAddBtn').addEventListener('click'", "async function _startProviderDeviceAuth")

    assert "_startProviderDeviceAuth" not in change_block
    assert "_startProviderDeviceAuth(deviceAuthProvider" in add_block


def test_google_add_omits_auto_refresh_mode_for_backend_manual_default():
    refresh_helper = _between(
        _ADMIN,
        "function _modelRefreshModeForApiEndpoint",
        "function _normalizeBaseUrl",
    )
    add_block = _between(
        _ADMIN,
        "el('adm-epAddBtn').addEventListener('click'",
        "async function _startProviderDeviceAuth",
    )

    assert "generativelanguage.googleapis.com" in refresh_helper
    assert "return '';" in refresh_helper
    assert "_modelRefreshModeForApiEndpoint(url, endpointKind)" in add_block
    assert "if (refreshMode) fd.append('model_refresh_mode', refreshMode)" in add_block


def test_device_auth_selection_disables_and_dims_api_test_button():
    form_block = _between(_ADMIN, "function _setApiFormForProvider()", "function _renderPickerMenu()")

    assert "testBtn.disabled = true" in form_block
    assert "testBtn.style.opacity = '0.45'" in form_block
    assert "testBtn.style.cursor = 'not-allowed'" in form_block
    assert "testBtn.disabled = false" in form_block
    assert "testBtn.style.opacity = ''" in form_block
    assert "testBtn.style.cursor = ''" in form_block


def test_device_auth_keeps_manual_auth_button_without_auto_opening_tab():
    auth_block = _between(_ADMIN, "async function _startProviderDeviceAuth", "// Local \"Add\" button")

    assert "Authorize with OpenAI" in auth_block
    assert "Authorize on GitHub" in auth_block
    assert "adm-copilot-panel" in auth_block
    assert "adm-device-auth-copy" in auth_block
    assert "openWindow: () => {}" in auth_block
    assert "A new tab opened" not in auth_block


def test_loud_oauth_copy_and_removed_button_hooks_do_not_return():
    forbidden = [
        "Click Add to start",
        "uses account sign-in",
        "Uses ChatGPT/Codex OAuth, not an OpenAI API key.",
        "adm-chatgptStatus",
        "adm-chatgptConnectBtn",
        "adm-copilotConnectBtn",
        "adm-copilotStatus",
    ]
    for needle in forbidden:
        assert needle not in _INDEX
        assert needle not in _ADMIN
