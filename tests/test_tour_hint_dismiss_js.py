"""Exercise tutorial dismissal against the real shared Escape stack."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which('node'), reason='node unavailable')


def test_tutorial_hints_dismiss_one_at_a_time_and_release_every_close_path():
    stack = (ROOT / 'static/js/escMenuStack.js').read_text()
    helper = (ROOT / 'static/js/tourHintDismiss.js').read_text().split('\n', 1)[1]
    script = stack + '\n' + helper + r'''
import assert from 'node:assert/strict';
const timers = new Map();
let nextTimer = 0;
globalThis.setTimeout = (fn, delay) => { const id = ++nextTimer; timers.set(id, {fn, delay}); return id; };
globalThis.clearTimeout = id => timers.delete(id);
function hint() {
  const listeners = new Map();
  const classes = new Set();
  return {
    isConnected: true,
    classList: {add: name => classes.add(name), contains: name => classes.has(name)},
    querySelector: () => ({
      addEventListener: (type, fn) => listeners.set(type, fn),
      removeEventListener: type => listeners.delete(type),
    }),
    click: () => listeners.get('click')?.(),
    remove() { this.isConnected = false; },
  };
}
let windowClosed = 0;
registerEscapeLayer(() => windowClosed++);
const first = hint(), second = hint();
let firstClosed = 0, secondClosed = 0;
bindTourHintDismiss(first, {timeout: 14000, onDismiss: () => firstClosed++});
bindTourHintDismiss(second, {timeout: 6500, onDismiss: () => secondClosed++});
assert.equal(dismissTopEscapeLayer(), true);
assert.equal(secondClosed, 1);
assert.equal(firstClosed, 0);
assert.equal(windowClosed, 0);
assert.equal(second.classList.contains('tour-hint-out'), true);
second._dismiss();
second.click();
assert.equal(secondClosed, 1);
assert.equal([...timers.values()].some(t => t.delay === 6500), false);
first.click();
assert.equal(firstClosed, 1);
assert.equal(_openMenuCount(), 1);
assert.equal([...timers.values()].some(t => t.delay === 14000), false);
assert.equal(dismissTopEscapeLayer(), true);
assert.equal(windowClosed, 1);
const timed = hint();
let timedClosed = 0;
bindTourHintDismiss(timed, {timeout: 100, onDismiss: () => timedClosed++});
[...timers.values()].find(t => t.delay === 100).fn();
assert.equal(timedClosed, 1);
assert.equal(dismissTopEscapeLayer(), false);
const detached = hint();
bindTourHintDismiss(detached);
detached.remove();
assert.equal(dismissTopEscapeLayer(), false);
'''
    result = subprocess.run(['node', '--input-type=module'], input=script,
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
