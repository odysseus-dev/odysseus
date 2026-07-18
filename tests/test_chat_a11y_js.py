r"""Accessibility contracts for the chat composer and the live "thinking" timer.

Two behaviours are pinned here:

1. The live "Thinking..." elapsed timer must honour ``prefers-reduced-motion``.
   It previously self-perpetuated a ``requestAnimationFrame`` loop that rewrote
   the header text ~60x/second for the whole thinking duration and ignored the
   user's motion preference. It now runs on a ``matchMedia``-gated
   ``setInterval`` (reduced-motion -> 1Hz whole seconds; coarse pointer ->
   250ms; fine pointer -> 100ms) and never calls ``requestAnimationFrame``.

2. The icon-only send/stop button must expose an accessible name. Its ``title``
   is kept in sync per mode, but ``title`` alone is not reliably announced by
   screen readers on a ``<button>``, so the live title is mirrored into
   ``aria-label``.

``chat.js`` pulls in browser globals and cannot be imported under node, so each
test lifts the exact production source of the relevant helper out of
``static/js/chat.js`` and executes it inside a stubbed harness. That runs the
real code, not a reimplementation, and fails if the fix is silently reverted.
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CHAT_JS = _REPO / "static" / "js" / "chat.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node binary not on PATH"
)


def _chat_src() -> str:
    return _CHAT_JS.read_text(encoding="utf-8")


def _slice_braced(src: str, anchor: str) -> str:
    """Return ``anchor`` plus the balanced ``{ ... }`` block that follows it."""
    i = src.index(anchor)
    brace = src.index("{", i)
    depth = 0
    for j in range(brace, len(src)):
        c = src[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[i : j + 1]
    raise AssertionError(f"unbalanced block after {anchor!r}")


def _slice_timer_setup(src: str) -> str:
    """The live-timer setup: from the start marker through the setInterval call."""
    start = src.index("var _thinkTimerStart = Date.now();")
    end_kw = "_thinkTimerId = setInterval(_tickThinkTimer,"
    e = src.index(end_kw, start)
    e = src.index(";", e) + 1
    return src[start:e]


def _run_node(script: str) -> dict:
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"node failed:\nSTDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}"
    )
    return json.loads(result.stdout.splitlines()[-1])


def _run_timer(reduced_motion: bool, coarse: bool = False) -> dict:
    src = _chat_src()
    fmt = _slice_braced(src, "function _formatThinkStats(seconds, tokenCount)")
    timer = _slice_timer_setup(src)
    harness = (
        textwrap.dedent(
            """
            const RM = __RM__, COARSE = __COARSE__;
            let rafCalls = 0;
            const intervalDelays = [];
            const cleared = [];
            globalThis.requestAnimationFrame = () => { rafCalls++; return 1; };
            globalThis.cancelAnimationFrame = () => {};
            globalThis.setInterval = (fn, ms) => { intervalDelays.push(ms); return 7; };
            globalThis.clearInterval = (id) => { cleared.push(id); };
            globalThis.matchMedia = (q) => ({
              matches: q.includes('prefers-reduced-motion') ? RM
                     : q.includes('pointer: coarse') ? COARSE
                     : false,
            });
            let _liveThinkTokenCount = 0;
            const _liveThinkTimerEl = { isConnected: true, textContent: '' };
            __FMT__
            __TIMER__
            const text = _liveThinkTimerEl.textContent;
            // Once the timer element detaches, the next tick must stop its own
            // interval (the short-thinking teardown path relies on this).
            _liveThinkTimerEl.isConnected = false;
            _tickThinkTimer();
            console.log(JSON.stringify({ rafCalls, intervalDelays, text, cleared }));
            """
        )
        .replace("__RM__", "true" if reduced_motion else "false")
        .replace("__COARSE__", "true" if coarse else "false")
        .replace("__FMT__", fmt)
        .replace("__TIMER__", timer)
    )
    return _run_node(harness)


def _run_aria() -> dict:
    fn = _slice_braced(_chat_src(), "const _wireSendBtnAria = (btn) =>")
    harness = textwrap.dedent(
        """
        let observerCb = null;
        globalThis.MutationObserver = class {
          constructor(cb) { observerCb = cb; }
          observe() {}
        };
        function makeBtn(title) {
          const attrs = { title };
          return {
            dataset: {},
            _attrs: attrs,
            getAttribute(k) { return k in attrs ? attrs[k] : null; },
            setAttribute(k, v) { attrs[k] = v; },
          };
        }
        __FN__
        const btn = makeBtn('Send message');
        const wired = _wireSendBtnAria(btn);
        const seeded = btn.getAttribute('aria-label');
        // A mode switch updates the title; the observer mirrors it to aria-label.
        btn._attrs.title = 'Stop generation';
        if (observerCb) observerCb();
        const afterSwitch = btn.getAttribute('aria-label');
        // Idempotent: wiring the same button again is a no-op that still succeeds.
        const rewired = _wireSendBtnAria(btn);
        // A missing button is handled without throwing.
        const nullBtn = _wireSendBtnAria(null);
        console.log(JSON.stringify({ wired, seeded, afterSwitch, rewired, nullBtn }));
        """
    ).replace("__FN__", fn)
    return _run_node(harness)


def test_thinking_timer_uses_interval_not_raf():
    out = _run_timer(reduced_motion=False, coarse=False)
    assert out["rafCalls"] == 0            # the animation loop is gone
    assert out["intervalDelays"] == [100]  # fine-pointer desktop cadence
    assert "." in out["text"]              # tenths readout when motion is allowed


def test_thinking_timer_honours_reduced_motion():
    out = _run_timer(reduced_motion=True)
    assert out["rafCalls"] == 0            # never animates under reduced-motion
    assert out["intervalDelays"] == [1000]  # 1Hz whole-second cadence
    assert "." not in out["text"]          # whole seconds, no sub-second churn
    assert out["text"].endswith("s")


def test_thinking_timer_coarse_pointer_cadence():
    out = _run_timer(reduced_motion=False, coarse=True)
    assert out["intervalDelays"] == [250]
    assert out["rafCalls"] == 0


def test_thinking_timer_tick_self_clears_when_detached():
    out = _run_timer(reduced_motion=True)
    # The interval id (7 from the stub) is cleared once the element detaches.
    assert 7 in out["cleared"]


def test_send_button_exposes_accessible_name():
    out = _run_aria()
    assert out["wired"] is True
    assert out["seeded"] == "Send message"          # aria-label seeded from title
    assert out["afterSwitch"] == "Stop generation"  # observer keeps it in sync
    assert out["rewired"] is True                   # idempotent re-wire
    assert out["nullBtn"] is False                  # null-safe


def test_chat_source_keeps_reduced_motion_guard():
    # Source-level guard so the fix cannot be silently reverted to the
    # requestAnimationFrame loop that ignored prefers-reduced-motion.
    src = _chat_src()
    assert "_thinkTimerRAF" not in src
    assert "matchMedia('(prefers-reduced-motion: reduce)')" in src
