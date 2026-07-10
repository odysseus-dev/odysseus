"""Regression coverage for reopening Settings after closing its minimized chip."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_JS = (ROOT / "static" / "js" / "settings.js").read_text(encoding="utf-8")


def _function_source(signature: str, next_marker: str) -> str:
    start = SETTINGS_JS.index(signature)
    end = SETTINGS_JS.index(next_marker, start + len(signature))
    return SETTINGS_JS[start:end].replace("export function", "function", 1)


OPEN_SOURCE = _function_source("export function open(tab)", "\nexport function close()")
CLOSE_SOURCE = _function_source("export function close()", "\n// Handle redirect back")


@pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")
def test_chip_close_callback_cannot_hide_reopened_settings():
    script = textwrap.dedent(
        """
        const makeClassList = (initial = []) => {
          const values = new Set(initial);
          return {
            add(...names) { names.forEach((name) => values.add(name)); },
            remove(...names) { names.forEach((name) => values.delete(name)); },
            contains(name) { return values.has(name); },
            toggle(name, force) {
              const enabled = force === undefined ? !values.has(name) : !!force;
              if (enabled) values.add(name); else values.delete(name);
              return enabled;
            },
          };
        };

        const listeners = new Map();
        const content = {
          classList: makeClassList(),
          addEventListener(type, handler, options = {}) {
            const entries = listeners.get(type) || [];
            entries.push({ handler, once: !!options.once });
            listeners.set(type, entries);
          },
          removeEventListener(type, handler) {
            const entries = listeners.get(type) || [];
            listeners.set(type, entries.filter((entry) => entry.handler !== handler));
          },
          dispatchEvent(event) {
            for (const entry of [...(listeners.get(event.type) || [])]) {
              entry.handler(event);
              if (entry.once) this.removeEventListener(event.type, entry.handler);
            }
          },
        };

        const modalEl = {
          classList: makeClassList(),
          querySelector(selector) {
            return selector.includes('modal-content') ? content : null;
          },
          querySelectorAll() { return []; },
        };

        const timers = [];
        globalThis.setTimeout = (handler) => { timers.push(handler); return timers.length; };
        globalThis.clearTimeout = () => {};
        globalThis.document = { body: { classList: makeClassList() } };
        globalThis.window = {};

        let initialized = true;
        let _closeGen = 0;
        const ADMIN_TABS = new Set();
        const initAll = () => {};
        const syncAppearanceCheckboxes = () => {};
        const resetWindowPlacement = () => {};
        const syncAdminVisibility = () => {};
        const syncAppearanceOpacity = () => {};
        const refreshAiModelEndpoints = () => {};
        """
    )
    script += OPEN_SOURCE
    script += CLOSE_SOURCE
    script += textwrap.dedent(
        """

        close();
        const closeWasScheduled = content.classList.contains('modal-closing');

        // modalManager.close() synchronously closes a minimized chip and
        // removes the exit-animation class before Settings' animation ends.
        modalEl.classList.add('hidden');
        content.classList.remove('modal-closing');
        open();

        // Both abandoned close paths may fire after the new open: the old
        // animation listener sees the entrance animation and the fallback
        // timeout may run immediately afterward. Neither may hide Settings.
        content.dispatchEvent({ type: 'animationend', target: content });
        for (const handler of timers.splice(0)) handler();

        const listenersAfterReopen = (listeners.get('animationend') || []).length;

        console.log(JSON.stringify({
          closeWasScheduled,
          listenersAfterReopen,
          visibleAfterOpenAnimation: !modalEl.classList.contains('hidden'),
        }));
        """
    )

    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {
        "closeWasScheduled": True,
        "listenersAfterReopen": 0,
        "visibleAfterOpenAnimation": True,
    }
