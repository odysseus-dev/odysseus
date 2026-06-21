from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EMAIL_LIBRARY_JS = (ROOT / "static" / "js" / "emailLibrary.js").read_text(encoding="utf-8")
EMAIL_INBOX_JS = (ROOT / "static" / "js" / "emailInbox.js").read_text(encoding="utf-8")
SW_JS = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")


def test_pc_email_reader_windows_have_hidden_dom_budget():
    assert "const _EMAIL_DESKTOP_READER_DOM_LIMIT = 6;" in EMAIL_LIBRARY_JS
    assert "function _isDesktopEmailReaderBudgetEnabled()" in EMAIL_LIBRARY_JS
    assert "return !_isOdysseusAndroidApp() && window.innerWidth > 768;" in EMAIL_LIBRARY_JS
    assert "function _enforceEmailReaderDomBudget(activeId = '')" in EMAIL_LIBRARY_JS
    assert ".modal[id^=\"email-reader-\"], .modal[id^=\"email-window-\"]" in EMAIL_LIBRARY_JS
    assert "if (victim.id === activeId || !victim.hidden) continue;" in EMAIL_LIBRARY_JS
    assert "Modals.close(id);" in EMAIL_LIBRARY_JS
    assert "_stampEmailReaderModal(modal);" in EMAIL_LIBRARY_JS
    assert "_scheduleEmailReaderDomBudget(modalId);" in EMAIL_LIBRARY_JS
    assert "_scheduleEmailReaderDomBudget(winId);" in EMAIL_LIBRARY_JS


def test_email_reader_budget_rechecks_when_reader_is_restored():
    assert "function _ensureEmailReaderDomBudgetWatcher()" in EMAIL_LIBRARY_JS
    assert "window.addEventListener('odysseus:modal-opened'" in EMAIL_LIBRARY_JS
    assert "if (_isEmailReaderModalId(id)) _scheduleEmailReaderDomBudget(id);" in EMAIL_LIBRARY_JS


def test_email_tab_grid_observer_does_not_retain_closed_library_grid():
    assert "function _disconnectEmailGridTabObserver()" in EMAIL_LIBRARY_JS
    assert "try { _emailTabGridObserver.disconnect(); } catch (_) {}" in EMAIL_LIBRARY_JS
    assert "if (document.getElementById('email-lib-modal'))" in EMAIL_LIBRARY_JS
    assert "_disconnectEmailGridTabObserver();" in EMAIL_LIBRARY_JS
    close_block = EMAIL_LIBRARY_JS[
        EMAIL_LIBRARY_JS.index("export function closeEmailLibrary()"):
        EMAIL_LIBRARY_JS.index("// Make a modal draggable")
    ]
    assert "_disconnectEmailGridTabObserver();" in close_block


def test_email_inbox_refresh_and_header_binding_are_idempotent():
    assert "let _emailEventsBound = false;" in EMAIL_INBOX_JS
    assert "let _unreadRefreshTimer = null;" in EMAIL_INBOX_JS
    assert "let _refreshUnreadInFlight = false;" in EMAIL_INBOX_JS
    assert "header.dataset.emailInboxHeaderBound !== '1'" in EMAIL_INBOX_JS
    assert "composeBtn.dataset.emailInboxComposeBound !== '1'" in EMAIL_INBOX_JS
    assert "if (_emailEventsBound) return;" in EMAIL_INBOX_JS
    assert "if (!_unreadRefreshTimer) _unreadRefreshTimer = setInterval(_refreshUnreadCount, 60000);" in EMAIL_INBOX_JS
    assert "if (_refreshUnreadInFlight) return;" in EMAIL_INBOX_JS
    assert "_refreshUnreadInFlight = false;" in EMAIL_INBOX_JS


def test_email_lag_guard_cache_bumped():
    assert "const CACHE_NAME = 'odysseus-v407';" in SW_JS
