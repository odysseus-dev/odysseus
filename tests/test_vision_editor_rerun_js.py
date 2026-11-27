"""Executable regression for the vision editor's Re-run button (PR #5968).

Drives the real _openVisionEditor from static/js/chatRenderer.js under Node
with a minimal DOM: when the editor's initial GET /vision fails, Re-run must
still be enabled, and clicking it sends the forced request and fills the
textarea with the new description.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "static" / "js" / "chatRenderer.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

_HARNESS = r"""
const source = await (await import('node:fs/promises')).readFile(process.argv[2], 'utf8');
const start = source.indexOf('let _visionEditorEl = null;');
const end = source.indexOf('// Tool call syntax patterns', start);
if (start < 0 || end < 0) throw new Error('vision editor markers not found');

class El {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.disabled = false;
    this.value = ''; this.placeholder = ''; this.className = ''; this._label = null; this._html = ''; }
  set innerHTML(html) { this._html = html; const m = /class="vision-btn-label">([^<]*)</.exec(html);
    this._label = m ? { textContent: m[1] } : null; }
  get innerHTML() { return this._html; }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  querySelector(sel) { return sel === '.vision-btn-label' ? this._label : null; }
  focus() {} remove() { this.removed = true; }
  async click() { for (const fn of this.listeners.click || []) await fn({ target: this }); }
  get label() { return this._label && this._label.textContent; }
}
const created = [];
const document = {
  createElement: (tag) => { const e = new El(tag); created.push(e); return e; },
  body: new El('body'), addEventListener() {}, removeEventListener() {},
};
const responses = JSON.parse(process.argv[3]);
const requests = [];
const fetch = async (url, opts) => {
  requests.push(url);
  const r = responses.shift();
  if (r === 'reject') throw new Error('network down');
  return { ok: r.ok, status: r.status || 200, json: async () => r.body };
};
const toasts = [], errors = [];
const uiModule = { showToast: (t) => toasts.push(t), showError: (t) => errors.push(t) };
const window = { chatModule: {} };
const open = new Function('document', 'fetch', 'uiModule', 'window',
  source.slice(start, end) + ';return _openVisionEditor;')(document, fetch, uiModule, window);

open({ id: 'att1' }, new El('div'));
const settle = () => new Promise((r) => setTimeout(r, 0));
await settle(); await settle();
const ta = created.find((e) => e.tag === 'textarea');
const rerun = created.find((e) => e.tag === 'button' && /Re-run/.test(e.innerHTML));
const save = created.find((e) => e.tag === 'button' && e.label === 'Save');
const afterFailedLoad = { rerunDisabled: rerun.disabled, saveDisabled: save.disabled, taDisabled: ta.disabled };
await rerun.click();
console.log(JSON.stringify({ afterFailedLoad, requests, text: ta.value, rerunDisabled: rerun.disabled,
  taDisabled: ta.disabled, label: rerun.label, toasts, errors }));
"""


def _run(responses):
    proc = subprocess.run(
        ["node", "--input-type=module", "-", str(_SRC), json.dumps(responses)],
        input=_HARNESS, capture_output=True, text=True, cwd=_REPO, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("first_load", [{"ok": False, "status": 503, "body": {}}, "reject"])
def test_rerun_recovers_after_the_initial_load_fails(first_load):
    out = _run([first_load, {"ok": True, "body": {"text": "A red bicycle against a brick wall."}}])
    # The failed load leaves the editor usable, Re-run included.
    assert out["afterFailedLoad"] == {"rerunDisabled": False, "saveDisabled": False, "taDisabled": False}
    # Re-run sends the forced request and shows the new description.
    assert out["requests"] == ["/api/upload/att1/vision", "/api/upload/att1/vision?force=1"]
    assert out["text"] == "A red bicycle against a brick wall."
    assert out["rerunDisabled"] is False and out["taDisabled"] is False
    assert out["toasts"] == ["Re-ran vision model"] and out["errors"] == []


def test_failed_rerun_restores_the_button_and_reports_the_error():
    out = _run([{"ok": True, "body": {"text": "old"}}, {"ok": False, "status": 500, "body": {}}])
    assert out["text"] == "old"
    assert out["rerunDisabled"] is False and out["label"] != "Running…"
    assert out["errors"] == ["Re-run failed: HTTP 500"]


def test_four_editor_actions_fit_inside_the_panel_at_phone_widths():
    """With Re-run added there are four actions; at phone widths they must
    wrap inside the panel rather than overflow or clip (PR #5968 review)."""
    from tests.helpers.stylesheets import stylesheet_link_tags

    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const out = {};
      for (const width of [320, 360, 390, 768]) {
        const page = await browser.newPage({ viewport: { width, height: 800 } });
        await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/spinner.js`);
        const svg = '<svg width="12" height="12" viewBox="0 0 24 24"></svg>';
        const btn = (label, extra = '') =>
          `<button type="button" class="vision-editor-btn${extra}">${svg}<span class="vision-btn-label">${label}</span></button>`;
        await page.setContent(`__ODY_STYLESHEETS__
          <div class="vision-editor-overlay"><div class="vision-editor-panel">
            <textarea class="vision-editor-text" rows="10"></textarea>
            <div class="vision-editor-actions">
              ${btn('Close')}${btn('Re-run')}${btn('Save', ' vision-editor-btn-primary')}${btn('Regenerate message', ' vision-editor-btn-primary')}
            </div></div></div>`);
        out[width] = await page.evaluate(() => {
          const panel = document.querySelector('.vision-editor-panel').getBoundingClientRect();
          const buttons = [...document.querySelectorAll('.vision-editor-btn')];
          const rects = buttons.map((b) => b.getBoundingClientRect());
          const inside = rects.every((r) => r.left >= panel.left - 0.5 && r.right <= panel.right + 0.5);
          const clipped = buttons.some((b) => b.scrollWidth > b.clientWidth + 1);
          const overlap = rects.some((a, i) => rects.some((b, j) => i < j &&
            a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5));
          return { inside, clipped, overlap, fitsViewport: panel.right <= window.innerWidth };
        });
        await page.close();
      }
      console.log(JSON.stringify(out));
      await browser.close();
    """
    script = script.replace("__ODY_STYLESHEETS__", stylesheet_link_tags())
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_REPO, check=False, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    for width, layout in json.loads(result.stdout).items():
        assert layout == {"inside": True, "clipped": False, "overlap": False, "fitsViewport": True}, (width, layout)
