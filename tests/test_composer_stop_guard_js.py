"""Pin the send/stop button's Stop-vs-Send decision (static/js/composerStopGuard.js).

Regression guard for: hitting **Stop** must never clear the freshly typed draft
in the composer. The composer uses one `<button type="submit">` for both Send and
Stop; `handleChatSubmit` only enters the (draft-preserving) Stop branch when
`shouldTreatStopClick()` is true. If that decision were keyed off the
`isStreaming` flag alone, a click on a button that is *visually* in Stop mode
while the flag has not re-armed (e.g. a run resumed after a page refresh) would
fall through to the send path — which clears `#message` and fires the draft as a
new message.

Driven through `node --input-type=module` so we exercise the real JS without a
full Vitest/Jest setup (same approach as test_composer_arrow_up_recall_js.py).
Skips when `node` is not installed rather than failing.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_GUARD = _REPO / "static" / "js" / "composerStopGuard.js"
_GUARD_URL = _GUARD.as_uri()
_CHAT = _REPO / "static" / "js" / "chat.js"
_HAS_NODE = shutil.which("node") is not None

_HARNESS = r"""
import { shouldTreatStopClick } from 'GUARD_PATH';

function btn(mode) {
  // mode === undefined → no dataset.mode at all
  return mode === undefined ? { dataset: {} } : { dataset: { mode } };
}

const cases = CASES_JSON;
const results = cases.map((c) => {
  let submitBtn;
  if (c.btn === 'null') submitBtn = null;
  else if (c.btn === 'nodataset') submitBtn = {};
  else submitBtn = btn(c.mode);
  return shouldTreatStopClick(c.isStreaming, submitBtn);
});
console.log(JSON.stringify(results));
""".replace("GUARD_PATH", _GUARD_URL)


def _run(cases: list) -> list:
    js = _HARNESS.replace("CASES_JSON", json.dumps(cases))
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_streaming_flag_set_is_stop():
    # Classic case: flag set → always Stop, regardless of button mode.
    assert _run([{"isStreaming": True, "mode": "streaming"}]) == [True]
    assert _run([{"isStreaming": True, "mode": ""}]) == [True]
    assert _run([{"isStreaming": True, "btn": "null"}]) == [True]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_button_visually_streaming_is_stop_even_when_flag_false():
    # The core regression: button shows Stop (dataset.mode === 'streaming') but
    # the streaming flag hasn't re-armed (resume-after-refresh). Must be Stop so
    # the typed draft is preserved, not cleared+sent.
    assert _run([{"isStreaming": False, "mode": "streaming"}]) == [True]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_idle_button_and_flag_false_is_send():
    # Genuine send: not streaming and button is in a send-capable mode.
    assert _run([{"isStreaming": False, "mode": ""}]) == [False]
    assert _run([{"isStreaming": False, "mode": "newchat"}]) == [False]
    assert _run([{"isStreaming": False, "mode": "mic"}]) == [False]
    assert _run([{"isStreaming": False, "mode": "recording"}]) == [False]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_missing_button_or_dataset_defaults_to_send_when_not_streaming():
    # Defensive: a null button or one without dataset.mode must not crash and,
    # absent the streaming flag, must NOT be treated as Stop.
    assert _run([{"isStreaming": False, "btn": "null"}]) == [False]
    assert _run([{"isStreaming": False, "btn": "nodataset"}]) == [False]
    assert _run([{"isStreaming": False, "mode": None}]) == [False]


def test_chat_js_stop_branch_uses_guard_not_bare_flag():
    """Static guard: the Stop branch in chat.js must call shouldTreatStopClick,
    not re-introduce a bare `if (isStreaming)` discriminator that would let a
    resume-mode click fall through to the send path and wipe the draft.
    """
    src = _CHAT.read_text(encoding="utf-8")
    assert "import { shouldTreatStopClick } from './composerStopGuard.js';" in src, (
        "chat.js must import the stop-guard helper"
    )
    # The guarded stop branch must exist.
    assert re.search(r"if \(shouldTreatStopClick\(isStreaming, submitBtn\)\) \{", src), (
        "the Stop branch must be guarded by shouldTreatStopClick(isStreaming, submitBtn)"
    )
    # And the old bare-flag stop discriminator must be gone.
    assert "if (isStreaming) {" not in src, (
        "the bare `if (isStreaming) {` stop discriminator must be replaced by the guard"
    )
