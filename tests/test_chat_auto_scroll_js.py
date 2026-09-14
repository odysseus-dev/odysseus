"""Exercise the transcript auto-scroll controller with a manually pumped frame clock."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parent.parent
_MODULE_URL = (_REPO / "static" / "js" / "chatAutoScroll.js").as_uri()
_HAS_NODE = shutil.which("node") is not None


_HARNESS = r"""
import { createChatAutoScroller } from 'MODULE_URL';

function makeCase({ scrollHeight, clientHeight, scrollTop = 0, compact = false }) {
  const frames = new Map();
  let nextFrame = 1;
  const box = { scrollHeight, clientHeight, scrollTop, isConnected: true };
  const scroller = createChatAutoScroller({
    getBox: () => box,
    requestFrame(callback) {
      const id = nextFrame++;
      frames.set(id, callback);
      return id;
    },
    cancelFrame(id) { frames.delete(id); },
    isCompactViewport: () => compact,
  });
  return {
    box,
    scroller,
    pump(limit = 200) {
      let count = 0;
      while (frames.size && count < limit) {
        const current = [...frames.entries()];
        frames.clear();
        for (const [, callback] of current) callback();
        count += 1;
      }
      return count;
    },
    pending: () => frames.size,
  };
}

const results = {};

{
  const c = makeCase({ scrollHeight: 1600, clientHeight: 500 });
  c.scroller.scroll();
  const frames = c.pump();
  results.largeGap = { scrollTop: c.box.scrollTop, frames, pending: c.pending() };
}

{
  const c = makeCase({ scrollHeight: 900, clientHeight: 500 });
  c.scroller.scroll();
  c.pump(2);
  c.box.scrollHeight = 1800;
  const framesAfterGrowth = c.pump();
  results.dynamicGrowth = { scrollTop: c.box.scrollTop, framesAfterGrowth, pending: c.pending() };
}

{
  const c = makeCase({ scrollHeight: 1600, clientHeight: 500 });
  c.scroller.scroll();
  c.pump(2);
  const beforeDisable = c.box.scrollTop;
  c.scroller.setEnabled(false);
  c.pump();
  results.disabled = {
    beforeDisable,
    afterDisable: c.box.scrollTop,
    enabled: c.scroller.isEnabled(),
    animating: c.scroller.isAnimating(),
  };
}

console.log(JSON.stringify(results));
""".replace("MODULE_URL", _MODULE_URL)


def _run_harness() -> dict:
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=_HARNESS,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_large_stream_growth_reaches_bottom_without_a_distance_cutoff():
    result = _run_harness()["largeGap"]
    assert result["scrollTop"] == pytest.approx(1100, abs=1)
    assert result["frames"] > 1
    assert result["pending"] == 0


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_inflight_scroll_tracks_tool_card_and_reply_growth():
    result = _run_harness()["dynamicGrowth"]
    assert result["scrollTop"] == pytest.approx(1300, abs=1)
    assert result["framesAfterGrowth"] > 1
    assert result["pending"] == 0


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_user_scroll_disable_cancels_the_active_animation():
    result = _run_harness()["disabled"]
    assert result["afterDisable"] == result["beforeDisable"]
    assert result["enabled"] is False
    assert result["animating"] is False


def test_tool_completion_schedules_the_next_activity_bubble_without_delay():
    source = (_REPO / "static" / "js" / "chat.js").read_text(encoding="utf-8")
    tool_output = source.split("} else if (json.type === 'tool_output') {", 1)[1].split(
        "} else if (json.type === 'doc_stream_open') {", 1
    )[0]
    assert "_scheduleThinkingSpinner(0);" in tool_output


def test_programmatic_scroll_frames_do_not_turn_off_auto_scroll():
    source = (_REPO / "static" / "app.js").read_text(encoding="utf-8")
    handler = source.split("// Scrolling", 1)[1].split(
        "// Close all footer popups", 1
    )[0]
    assert "else if (!uiModule.isAutoScrollAnimating())" in handler


def test_history_and_composer_use_the_same_horizontal_track():
    css = (_REPO / "static" / "style.css").read_text(encoding="utf-8")
    history = css.split(".chat-history {", 1)[1].split("}", 1)[0]
    assert "padding-left: max(0px, calc((100% - var(--chat-max)) / 2));" in history
    assert "padding-right: max(0px, calc((100% - var(--chat-max)) / 2));" in history


def test_auto_scroll_module_is_available_in_the_offline_app_shell():
    service_worker = (_REPO / "static" / "sw.js").read_text(encoding="utf-8")
    assert "'/static/js/chatAutoScroll.js'," in service_worker
