"""Pin the shared clock for canvas background effects (static/js/bgEffectClock.js).

Driven through `node --input-type=module` (same approach as test_hex_to_rgb_js.py);
skips when `node` is not installed.

Regression (#4911): every background effect redrew a full-screen canvas on each
display refresh, and moved a fixed amount per refresh, so ProMotion (120 Hz)
screens did twice the GPU work and ran the animations at double speed.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CLOCK = _REPO / "static" / "js" / "bgEffectClock.js"
_HAS_NODE = shutil.which("node") is not None

# Simulates a display: requestAnimationFrame callbacks fire every `period` ms.
_HARNESS = """
import {{ runBgEffect, bgChance, bgCount, bgFade, BG_EFFECT_FPS }} from '{clock}';

function simulate({{ hz, seconds, reduced = false, disconnectAfter = Infinity, gapAt = null }}) {{
  const period = 1000 / hz;
  let queue = [];
  const raf = (cb) => queue.push(cb);
  const listeners = [];
  const reduceMotion = {{ matches: reduced, addEventListener: (_t, fn) => listeners.push(fn) }};
  const canvas = {{ isConnected: true }};
  const ks = [];
  runBgEffect(canvas, (k) => {{
    ks.push(k);
    if (ks.length >= disconnectAfter) canvas.isConnected = false;
  }}, {{ raf, reduceMotion }});
  const settled = ks.length;
  for (let t = period; t <= seconds * 1000 + 1e-6; t += period) {{
    const now = gapAt !== null && t >= gapAt ? t + 1000 : t;
    const due = queue; queue = [];
    for (const cb of due) cb(now);
  }}
  return {{ draws: ks.length, settled, sumK: ks.reduce((a, b) => a + b, 0),
            maxK: Math.max(0, ...ks), pending: queue.length, listeners: listeners.length }};
}}

const out = {{
  fps: BG_EFFECT_FPS,
  hz60: simulate({{ hz: 60, seconds: 2 }}),
  hz120: simulate({{ hz: 120, seconds: 2 }}),
  hz144: simulate({{ hz: 144, seconds: 2 }}),
  stopped: simulate({{ hz: 60, seconds: 2, disconnectAfter: 5 }}),
  reduced: simulate({{ hz: 60, seconds: 2, reduced: true }}),
  gap: simulate({{ hz: 60, seconds: 2, gapAt: 1000 }}),
  fade1: bgFade(0.18, 1),
  fade2: bgFade(0.02, 2),
  chanceYes: bgChance(0.12, 2, () => 0.2),
  chanceNo: bgChance(0.12, 2, () => 0.3),
  countLow: bgCount(0.6, 2, () => 0.3),
  countHigh: bgCount(0.6, 2, () => 0.1),
  countMean: (() => {{ let t = 0; for (let i = 0; i < 20000; i++) t += bgCount(0.6, 2); return t / 20000; }})(),
}};
console.log(JSON.stringify(out));
"""


def _run():
    js = _HARNESS.format(clock=_CLOCK.as_posix())
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_draws_at_the_capped_rate_on_any_display():
    out = _run()
    for hz in ("hz60", "hz120", "hz144"):
        per_second = out[hz]["draws"] / 2
        assert out["fps"] - 2 <= per_second <= out["fps"] + 1, (hz, out[hz])


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_motion_keeps_its_60hz_speed_on_any_display():
    # k sums to the number of 60 Hz frames elapsed: 120 over two seconds.
    out = _run()
    for hz in ("hz60", "hz120", "hz144"):
        assert 114 <= out[hz]["sumK"] <= 121, (hz, out[hz])


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_stops_when_the_canvas_is_removed():
    out = _run()
    assert out["stopped"]["draws"] == 5
    assert out["stopped"]["pending"] == 0


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_reduced_motion_settles_then_holds_still():
    out = _run()
    reduced = out["reduced"]
    assert reduced["settled"] == 90
    assert reduced["draws"] == 90  # nothing after the settled frame
    assert reduced["pending"] == 0
    assert reduced["listeners"] == 1  # resumes if the setting is turned off


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_a_long_gap_does_not_jump_the_scene():
    out = _run()
    assert out["gap"]["maxK"] == 4


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_fade_and_chance_scale_with_elapsed_frames():
    out = _run()
    assert out["fade1"] == pytest.approx(0.18)
    assert out["fade2"] == pytest.approx(1 - 0.98 ** 2)
    # 0.12 per frame over two frames is a 22.56% chance.
    assert out["chanceYes"] is True
    assert out["chanceNo"] is False


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_spawn_count_keeps_the_60hz_rate():
    # 0.6 per frame over two frames is 1.2 on average: one, plus a second 20% of the time.
    out = _run()
    assert out["countLow"] == 1
    assert out["countHigh"] == 2
    assert out["countMean"] == pytest.approx(1.2, abs=0.03)
