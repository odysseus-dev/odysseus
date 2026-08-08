from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_EMAIL_LIBRARY = _REPO / "static" / "js" / "emailLibrary.js"


def _source() -> str:
    return _EMAIL_LIBRARY.read_text(encoding="utf-8")


def _function_source(name: str) -> str:
    """Return one top-level JS function using balanced braces."""
    text = _source()
    markers = (f"function {name}", f"async function {name}", f"export function {name}", f"export async function {name}")
    starts = [text.find(marker) for marker in markers]
    starts = [start for start in starts if start >= 0]
    assert starts, f"missing function {name}"
    start = min(starts)
    paren = text.index("(", start)
    paren_depth = 0
    quote = None
    escaped = False
    for index in range(paren, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"', "`"):
            quote = char
        elif char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth -= 1
            if paren_depth == 0:
                brace = text.index("{", index)
                break
    else:
        raise AssertionError(f"unterminated signature {name}")
    depth = 0
    quote = None
    escaped = False
    template_depth = 0
    for index in range(brace, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote and template_depth == 0:
                quote = None
            elif quote == "`" and char == "$" and index + 1 < len(text) and text[index + 1] == "{":
                template_depth += 1
            elif quote == "`" and char == "}" and template_depth:
                template_depth -= 1
            continue
        if char in ("'", '"', "`"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    raise AssertionError(f"unterminated function {name}")


def test_prewarm_is_genuine_idle_only_and_single_flight():
    scheduler = _function_source("_scheduleEmailPrewarm")

    assert "if (_libPrewarmPromise) return _libPrewarmPromise;" in scheduler
    assert "typeof window.requestIdleCallback !== 'function'" in scheduler
    assert "return Promise.resolve(false);" in scheduler
    assert "window.requestIdleCallback((deadline)" in scheduler
    assert "!deadline.didTimeout" in scheduler
    assert "deadline.timeRemaining() > 0" in scheduler

    idle_callback = scheduler.index("window.requestIdleCallback((deadline)")
    assert "Promise.resolve()" in scheduler
    task_start = scheduler.index("task({ signal: controller.signal, generation })")
    assert idle_callback < task_start, "network work must only be reachable from the idle callback"


def test_prewarm_skips_hidden_and_foreground_work():
    guard = _function_source("_canRunEmailPrewarm")

    assert "state._libOpen" in guard
    assert "state._libLoading" in guard
    assert "_libSearchInFlight" in guard
    assert "document.visibilityState !== 'visible'" in guard
    assert "!_isChatInteractionBusy()" in guard


def test_prewarm_selects_only_last_used_or_default_account():
    chooser = _function_source("_chooseEmailPrewarmAccountId")
    prewarm = _function_source("_prewarmEmailViews")

    assert "_rememberedEmailAccountId()" in chooser
    assert "a.enabled !== false" in chooser
    assert "a.is_default" in chooser
    assert "enabled[0]" in chooser

    assert "for (" not in prewarm
    assert "orderedAccountIds" not in prewarm
    assert "slice(0, 4)" not in prewarm
    assert "/api/email/folders" not in prewarm
    assert "/api/email/unread-state" not in prewarm
    assert prewarm.count("/api/email/list") == 1


def test_prewarm_is_bounded_to_the_interactive_initial_page_size():
    text = _source()
    prewarm = _function_source("_prewarmEmailViews")

    assert "const _LIB_INITIAL_PAGE_SIZE = 100;" in text
    assert "limit: _LIB_INITIAL_PAGE_SIZE" in prewarm
    assert text.count("limit=${_LIB_INITIAL_PAGE_SIZE}&offset=${offsetAtStart}") == 2
    assert "limit: 100" not in prewarm


def test_open_cancels_scheduled_or_inflight_prewarm_first():
    cancel = _function_source("_cancelEmailPrewarm")
    open_library = _function_source("openEmailLibrary")

    assert "clearTimeout(_libPrewarmDelayTimer)" in cancel
    assert "window.cancelIdleCallback(_libPrewarmIdleHandle)" in cancel
    assert "_libPrewarmAbortController?.abort()" in cancel
    assert "_libPrewarmGeneration += 1" in cancel
    assert open_library.index("_cancelEmailPrewarm();") < open_library.index("state._libOpen = true;")


def test_unread_warm_joins_the_same_idle_single_flight_gate():
    unread_entry = _function_source("prewarmUnreadEmails")
    unread_work = _function_source("_prewarmUnreadEmailsNow")

    assert "_scheduleEmailPrewarm(" in unread_entry
    assert "fetch(" not in unread_entry
    assert "_ensureEmailAccountsForPrewarm({ signal, generation })" in unread_work
    assert "signal" in unread_work
    assert "Math.min(20" in unread_work
