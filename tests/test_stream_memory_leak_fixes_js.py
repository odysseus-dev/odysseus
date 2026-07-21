"""Regression guards for PR #4661 ("fix(ui): prevent browser OOM during long
agent interactions") — pins the fixes from gprocunier's review plus a
follow-up review pass that found additional teardown/leak bugs and corrected
two mistakes in the first proposed fix plan:

1. `_purgeStaleBackgroundStreams` must never abort a live/running background
   stream just because the Map exceeds MAX_BACKGROUND_STREAMS (it used to
   evict-by-insertion-order and call `abortCtrl.abort()` on whatever was
   oldest, live or not).
2. The background-error `catch` branch must free the same resources
   (`accumulated`, `sourcesHtml`, `abortCtrl`) that the `[DONE]`
   background-completion path already frees.
3. `_trimChatHistoryDOM` must never remove a live/streaming/unpersisted node
   — but it must also not stop sweeping at the first protected node it finds
   (a single long turn can have every one of its nodes `.streaming`, and
   bailing out entirely would defeat the OOM cap).
4. The normal (non-error, non-stop) stream finalize path must sweep
   `.agent-thread-node.running` nodes for leftover `_waveInterval`/
   `_elapsedTicker` intervals, matching the user-Stop handler and the error
   path.
5. `_teardownLiveThinking` centralizes RAF/timer teardown for the live
   thinking box and must be invoked at every terminal transition: a clean
   `</think>` close, the `[DONE]` handler while still thinking, a new
   `agent_step` (before `roundText` resets), and in the streaming `catch`
   block — critically *before* `_renderStream()` there, since `_renderStream()`
   overwrites the thinking box's DOM with a placeholder when a think tag is
   still unclosed, which would make a later flush a silent no-op.
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "static/js/chat.js"


def _function_body(name: str) -> str:
    text = SRC.read_text(encoding="utf-8")
    match = re.search(rf"\n\s*function\s+{name}\([^)]*\)\s*\{{", text)
    assert match, f"{name} not found"

    start = match.end()
    depth = 1
    i = start
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    assert depth == 0, f"{name} body did not close"
    return text[start : i - 1]


def _full_source() -> str:
    return SRC.read_text(encoding="utf-8")


# ── 1. Background-stream cap must not abort live streams ──────────────────

def test_purge_stale_background_streams_no_longer_evicts_live_streams():
    body = _function_body("_purgeStaleBackgroundStreams")

    # The old while-loop evicted the oldest entry regardless of status and
    # called abort() on it — that's the bug being removed.
    assert "while (_backgroundStreams.size" not in body
    assert ".abort()" not in body

    # The completed/error sweep must remain intact.
    assert "entry.status === 'completed' || entry.status === 'error'" in body
    assert "_backgroundStreams.delete(sid)" in body


# ── 2. Bug C: background-error catch must free resources ──────────────────

def test_background_error_branch_frees_resources_like_done_path():
    text = _full_source()
    marker = "bgErr.status = 'error';"
    idx = text.index(marker)
    tail = text[idx : idx + 600]

    assert "bgErr.accumulated = '';" in tail
    assert "bgErr.sourcesHtml = '';" in tail
    assert "bgErr.abortCtrl = null;" in tail


# ── 3. Bug B: normal finalize path sweeps running tool nodes ───────────────

def test_normal_finalize_sweeps_running_tool_nodes_for_leaked_intervals():
    text = _full_source()
    marker = "document.querySelectorAll('.agent-thread.streaming').forEach(t => t.classList.remove('streaming'));"
    occurrences = [m.start() for m in re.finditer(re.escape(marker), text)]
    assert len(occurrences) >= 2

    # At least one occurrence (the normal finalize path) must be immediately
    # followed by the running-node interval sweep, same as the user-Stop
    # handler and the error path already do.
    found_sweep_after = False
    for start in occurrences:
        tail = text[start : start + 700]
        if "_waveInterval" in tail and "_elapsedTicker" in tail and "agent-thread-node.running" in tail:
            found_sweep_after = True
            break
    assert found_sweep_after, "no '.streaming' removal site sweeps running tool nodes afterward"


# ── 4. _trimChatHistoryDOM protects live nodes without stalling the sweep ──

def test_protected_history_node_covers_all_required_cases():
    body = _function_body("_isProtectedHistoryNode")

    assert "'streaming'" in body
    assert "'agent-thinking-dots'" in body
    assert ".agent-thread.streaming" in body
    assert ".agent-thread-node.running" in body
    assert "#doc-stream-indicator" in body


def test_trim_chat_history_dom_skips_protected_nodes_without_stopping():
    body = _function_body("_trimChatHistoryDOM")

    assert "_isProtectedHistoryNode(el)" in body
    # The walk must `continue` past a protected node (skip it and keep
    # sweeping), never `break` out of the loop entirely — a `break` here
    # would mean one streaming node early in a long turn stalls the whole
    # sweep and defeats the OOM cap.
    protected_check = body.index("_isProtectedHistoryNode(el)")
    following = body[protected_check : protected_check + 120]
    assert "continue" in following
    assert "break" not in following


# ── 5. _teardownLiveThinking centralizes RAF/timer teardown ────────────────

def test_teardown_live_thinking_cancels_raf_and_render_timer():
    body = _function_body("_teardownLiveThinking")

    assert "cancelAnimationFrame(_thinkTimerRAF)" in body
    assert "_thinkTimerRAF = 0" in body
    assert "clearTimeout(_thinkRenderTimer)" in body
    assert "_thinkRenderTimer = null" in body
    assert "_extractLiveThinkText(roundText)" in body
    assert "mdToHtml" in body
    assert "textContent = thinkText" in body


def test_extract_live_think_text_is_shared_not_duplicated():
    body = _function_body("_extractLiveThinkText")
    # The extraction chain itself lives only inside the shared helper.
    assert "normalizeThinkingMarkup" in body
    assert r"replace(/^\s*Thinking(?:\s+Process)?:\s*/i, '')" in body

    text = _full_source()
    # The mid-stream debounce tick must call the shared extractor rather than
    # re-implementing the same regex chain inline.
    assert "_liveThinkInner.textContent = _extractLiveThinkText(roundText);" in text
    # The old inline duplicate (setTimeout body reassigning thinkText line by
    # line) must be gone from the debounce tick specifically.
    debounce_marker = "_thinkRenderTimer = setTimeout(function() {"
    debounce_idx = text.index(debounce_marker)
    debounce_chunk = text[debounce_idx : debounce_idx + 400]
    assert "normalizeThinkingMarkup(_streamDisplayText(roundText))" not in debounce_chunk
    assert "_extractLiveThinkText(roundText)" in debounce_chunk


def test_done_handler_finalizes_thinking_box_while_still_open():
    text = _full_source()
    marker = "// Force-close thinking if still open (model never output boundary)"
    idx = text.index(marker)
    tail = text[idx : idx + 400]
    assert "_teardownLiveThinking(true)" in tail


def test_clean_think_close_uses_shared_teardown_helper():
    text = _full_source()
    marker = "// Thinking ended — smooth transition: update header, pause, then collapse"
    idx = text.index(marker)
    tail = text[idx : idx + 300]
    assert "_teardownLiveThinking(true)" in tail
    # The old ad hoc cancelAnimationFrame + raw textContent-read finalize
    # logic should no longer be duplicated here.
    assert "var _finalThinkText = _liveThinkInner.textContent;" not in tail


def test_agent_step_finalizes_thinking_before_round_text_resets():
    text = _full_source()
    marker = "} else if (json.type === 'agent_step') {"
    idx = text.index(marker)
    # Grab enough of the handler body to see both the teardown call and the
    # later `roundText = '';` reset.
    chunk = text[idx : idx + 2400]
    assert "_teardownLiveThinking(true)" in chunk
    assert "roundText = '';" in chunk
    assert chunk.index("_teardownLiveThinking(true)") < chunk.index("roundText = '';")
    # Must also run before _renderStream(), which would otherwise clobber the
    # thinking box's DOM if a think tag is still unclosed.
    assert chunk.index("_teardownLiveThinking(true)") < chunk.index("_renderStream();")


def test_catch_block_finalizes_thinking_before_render_stream_clobbers_it():
    text = _full_source()
    marker = "    } catch (err) {\n"
    idx = text.index(marker)
    chunk = text[idx : idx + 700]
    assert "_teardownLiveThinking(true)" in chunk
    assert "_renderStream();" in chunk
    # CRITICAL ordering: teardown must run before _renderStream(), which
    # overwrites contentEl.innerHTML with a "Thinking (N lines)" placeholder
    # when a think tag is still unclosed — flushing after that point would
    # write into an already-detached node and be a no-op.
    assert chunk.index("_teardownLiveThinking(true)") < chunk.index("_renderStream();")
