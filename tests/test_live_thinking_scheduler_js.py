"""Regression coverage for bounded live-thinking DOM work in chat.js."""

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CHAT = _REPO / "static/js/chat.js"
_HAS_NODE = shutil.which("node") is not None


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_live_thinking_scheduler_behavior():
    result = subprocess.run(
        ["node", "--test", "tests/live_thinking_scheduler.test.mjs"],
        cwd=_REPO,
        capture_output=True,
        timeout=30,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"node --test failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


def test_live_thinking_hot_path_has_no_full_markdown_reparse_or_raf_timer():
    source = _CHAT.read_text(encoding="utf-8")
    hot_start = source.index("} else if (hasUnclosedThink && isThinking) {")
    hot_end = source.index("} else if (!hasUnclosedThink && isThinking) {", hot_start)
    hot_path = source[hot_start:hot_end]

    assert "_queueLiveThinking(_extractLiveThinkingText(roundText));" in hot_path
    assert "mdToHtml" not in hot_path
    assert "innerHTML" not in hot_path
    assert "requestAnimationFrame(_tickThinkTimer)" not in source
    assert "_liveThinkReducedMotion ? 1000 : 250" in source
    assert "target.style.whiteSpace = 'pre-wrap';" in source
    assert "_liveThinkInner.style.whiteSpace = '';" in source


def test_terminal_paths_close_protocol_markup_flush_and_cancel_pending_work():
    source = _CHAT.read_text(encoding="utf-8")

    helper_start = source.index("function _closeOpenThinkingMarkup()")
    helper = source[helper_start:source.index("function _replyAfterClosedThinking", helper_start)]
    assert "accumulated += '</think>';" in helper
    assert "roundText += '</think>';" in helper
    assert "currentAccumulated = accumulated;" in helper
    assert "_thinkOpen = false;" in helper

    assert source.count("_finalizeLiveThinking(_extractLiveThinkingText(roundText, true), true);") >= 3
    done_start = source.index("if (data === '[DONE]')")
    assert "_closeOpenThinkingMarkup();" in source[done_start:done_start + 220]
    for marker in ("} else if (json.type === 'tool_start') {", "} else if (json.type === 'agent_step') {"):
        start = source.index(marker)
        block_head = source[start:start + 220]
        assert block_head.index("_closeOpenThinkingMarkup();") < block_head.index("if (_isBg) continue;")

    catch_start = source.index("    } catch (err) {")
    catch_block = source[catch_start:source.index("    } finally {", catch_start)]
    assert "_closeOpenThinkingMarkup();" in catch_block
    assert "_finalizeLiveThinking(_extractLiveThinkingText(roundText, true), true);" in catch_block
    assert catch_block.index("_finalizeLiveThinking") < catch_block.index("_renderStream();")
    finally_block = source[source.index("    } finally {", catch_start):]
    assert "_cancelLiveThinkingWork();" in finally_block
