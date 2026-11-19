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

// `toggleAt` flips the reduce-motion setting at that time (ms), firing any
// change listener the clock registered, as matchMedia does.
function simulate({{ hz, seconds, reduced = false, disconnectAfter = Infinity, gapAt = null, toggleAt = null }}) {{
  const period = 1000 / hz;
  let queue = [];
  const raf = (cb) => queue.push(cb);
  let listeners = [];
  let registered = 0;
  const reduceMotion = {{ matches: reduced, addEventListener: (_t, fn) => {{ listeners.push(fn); registered++; }} }};
  const canvas = {{ isConnected: true }};
  const ks = [];
  const paints = [];
  runBgEffect(canvas, (k, paint) => {{
    ks.push(k);
    paints.push(paint);
    if (ks.length >= disconnectAfter) canvas.isConnected = false;
  }}, {{ raf, reduceMotion }});
  const settled = ks.length;
  const settledPaints = paints.filter(Boolean).length;
  let toggled = toggleAt === null;
  for (let t = period; t <= seconds * 1000 + 1e-6; t += period) {{
    if (!toggled && t >= toggleAt) {{
      toggled = true;
      reduceMotion.matches = !reduceMotion.matches;
      const fire = listeners; listeners = [];
      for (const fn of fire) fn();
    }}
    const now = gapAt !== null && t >= gapAt ? t + 1000 : t;
    const due = queue; queue = [];
    for (const cb of due) cb(now);
  }}
  // The longest run of unpainted steps between two painted ones: what a
  // single synchronous burst of state stepping can cost.
  let run = 0, longestUnpainted = 0;
  for (const p of paints) {{ run = p ? 0 : run + 1; longestUnpainted = Math.max(longestUnpainted, run); }}
  return {{ draws: ks.length, settled, settledPaints, sumK: ks.reduce((a, b) => a + b, 0),
            maxK: Math.max(0, ...ks), pending: queue.length, listeners: registered,
            paints: paints.filter(Boolean).length, unpainted: paints.filter(p => p === false).length,
            lastPainted: paints[paints.length - 1] === true, longestUnpainted }};
}}

const out = {{
  fps: BG_EFFECT_FPS,
  hz60: simulate({{ hz: 60, seconds: 2 }}),
  hz120: simulate({{ hz: 120, seconds: 2 }}),
  hz144: simulate({{ hz: 144, seconds: 2 }}),
  stopped: simulate({{ hz: 60, seconds: 2, disconnectAfter: 5 }}),
  reduced: simulate({{ hz: 60, seconds: 2, reduced: true }}),
  reducedThenOff: simulate({{ hz: 60, seconds: 2, reduced: true, toggleAt: 1000 }}),
  onThenReduced: simulate({{ hz: 60, seconds: 2, toggleAt: 1000 }}),
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
def test_every_animated_frame_is_painted():
    out = _run()
    for hz in ("hz60", "hz120", "hz144"):
        assert out[hz]["unpainted"] == 0, (hz, out[hz])


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_reduced_motion_paints_one_settled_frame_then_holds_still():
    # Review on #6513: settling used to paint the full canvas 90 times in one
    # synchronous loop. The state still steps 90 frames so rain and petals are
    # spread out, but only the last step touches the canvas.
    out = _run()
    reduced = out["reduced"]
    assert reduced["settled"] == 90
    assert reduced["settledPaints"] == 1
    assert reduced["lastPainted"] is True
    assert reduced["draws"] == 90  # nothing after the settled frame
    assert reduced["pending"] == 0
    assert reduced["listeners"] == 1  # resumes if the setting is turned off


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_turning_reduced_motion_off_resumes_the_animation():
    out = _run()
    resumed = out["reducedThenOff"]
    assert resumed["settledPaints"] == 1
    # One settled frame, then about 30 painted frames in the second second.
    assert 1 + 28 <= resumed["paints"] <= 1 + 31, resumed
    assert resumed["unpainted"] == 89  # only the settling steps
    assert resumed["pending"] == 1  # still animating


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_turning_reduced_motion_on_settles_with_one_paint():
    out = _run()
    switched = out["onThenReduced"]
    assert switched["unpainted"] == 89
    assert switched["longestUnpainted"] == 89
    assert switched["lastPainted"] is True
    assert switched["pending"] == 0  # holds still
    assert switched["listeners"] == 1


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
