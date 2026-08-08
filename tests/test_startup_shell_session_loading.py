"""Regression guards for decoupling the application shell from /api/sessions.

The browser modules are DOM-heavy, so these focused source assertions protect the
startup ordering and fallback invariants without introducing a new JS test stack.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
SESSIONS_JS = (ROOT / "static" / "js" / "sessions.js").read_text(encoding="utf-8")
INIT_JS = (ROOT / "static" / "js" / "init.js").read_text(encoding="utf-8")


def _slice(source: str, start: str, end: str) -> str:
    start_at = source.index(start)
    end_at = source.index(end, start_at)
    return source[start_at:end_at]


def test_shell_reveal_is_scheduled_before_initial_session_hydration():
    startup = _slice(APP_JS, "function startOdysseusApp()", "const runNonCriticalStartup")

    reveal_at = startup.index("revealApplicationShellAfterPaint();")
    session_gate = startup.index("if (sessionModule)", reveal_at)
    sessions_at = startup.index("sessionModule.loadSessions()", session_gate)
    assert reveal_at < session_gate < sessions_at

    finalizer = _slice(startup, ".finally(() => {", "  } else {")
    assert "settleInitialSessionListLoadingState();" in finalizer
    assert "removeApplicationLoader();" in finalizer
    assert "window._odysseusRouteOpener" in finalizer


def test_shell_reveal_waits_for_paint_and_makes_loader_non_blocking():
    helper = _slice(
        APP_JS,
        "function revealApplicationShellAfterPaint()",
        "function removeApplicationLoader()",
    )
    inert = _slice(
        APP_JS,
        "function _makeApplicationLoaderInert(loader)",
        "function revealApplicationShellAfterPaint()",
    )

    assert helper.count("requestAnimationFrame") == 2
    assert "loader.dataset.shellRevealed = 'true';" in inert
    assert "loader.setAttribute('aria-hidden', 'true');" in inert
    assert "loader.style.pointerEvents = 'none';" in inert
    assert "loader.style.opacity = '0';" in inert
    assert ".remove()" not in inert


def test_loader_node_is_removed_only_after_session_hydration_settles():
    removal = _slice(
        APP_JS,
        "function removeApplicationLoader()",
        "function settleInitialSessionListLoadingState()",
    )
    startup = _slice(APP_JS, "function startOdysseusApp()", "const runNonCriticalStartup")

    assert "setTimeout(() => loader.remove(), 300);" in removal
    assert startup.index("sessionModule.loadSessions()") < startup.index("removeApplicationLoader();")



def test_startup_composer_sentinel_survives_until_sessions_settle():
    preserve = _slice(
        SESSIONS_JS,
        "function _shouldPreserveStartupComposer(msgInput)",
        "function _clearComposerUnlessStartupTyped(msgInput)",
    )
    startup = _slice(APP_JS, "function startOdysseusApp()", "const runNonCriticalStartup")

    assert "document.getElementById('app-loader')" in preserve
    assert "window.__odysseusComposerUserEdited" in preserve
    assert "msgInput.addEventListener('input'" in INIT_JS
    assert "window.__odysseusComposerUserEdited = !!msgInput.value;" in INIT_JS
    assert startup.index("sessionModule.loadSessions()") < startup.index("removeApplicationLoader();")

def test_session_loading_state_is_local_to_the_chat_list():
    session_list_start = INDEX_HTML.index('<div id="session-list" role="listbox"')
    session_list_end = INDEX_HTML.index("</div>", session_list_start)
    bootstrap = INDEX_HTML[session_list_start:session_list_end]

    assert 'id="session-list-loading"' in bootstrap
    assert 'role="option"' in bootstrap
    assert 'aria-live="polite"' in bootstrap
    assert 'aria-atomic="true"' in bootstrap
    assert 'data-session-list-status' in bootstrap
    assert "Loading chats…" in bootstrap

    settle = _slice(
        APP_JS,
        "function settleInitialSessionListLoadingState()",
        "function startOdysseusApp()",
    )
    assert settle.count("requestAnimationFrame(() =>") == 2
    assert "document.getElementById('session-list-loading')" in settle
    assert "Chats unavailable" in settle


def test_five_second_fail_open_reveals_shell_without_removing_startup_sentinel():
    loader_script_start = INDEX_HTML.index("var iv=setInterval")
    loader_script_end = INDEX_HTML.index("</script>", loader_script_start)
    fallback = INDEX_HTML[loader_script_start:loader_script_end]

    assert "clearInterval(iv)" in fallback
    assert "l.dataset.shellRevealed='true'" in fallback
    assert "l.setAttribute('aria-hidden','true')" in fallback
    assert "l.style.pointerEvents='none'" in fallback
    assert "l.style.opacity='0'" in fallback
    assert "l.remove()" not in fallback


def test_loader_accessibility_and_app_cache_bust_are_kept_in_sync():
    assert 'id="app-loader" role="status" aria-live="polite"' in INDEX_HTML
    assert 'aria-label="Loading Odysseus"' in INDEX_HTML
    assert '<span class="a11y-visually-hidden">Loading Odysseus</span>' in INDEX_HTML
    assert 'id="loader-wave" aria-hidden="true"' in INDEX_HTML
    assert INDEX_HTML.count("/static/app.js?v=20260807startupshell1") == 2
