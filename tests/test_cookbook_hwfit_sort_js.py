"""Regression tests for client-side Cookbook model sorting (#6478)."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required to evaluate ES module syntax/behavior")
def test_cookbook_hwfit_sort_reorders_cached_models_without_network_fetch():
    repo_root = Path(__file__).resolve().parents[1]
    js_path = repo_root / "static" / "js" / "cookbook-hwfit.js"
    source = js_path.read_text(encoding="utf-8")

    assert "sort.addEventListener('change', () => _rerenderHwfitFromCacheOrFetch());" in source
    assert "function _rerenderHwfitFromCacheOrFetch()" in source

    script = r"""
import fs from 'node:fs';
import vm from 'node:vm';

let code = fs.readFileSync(__JS_PATH__, 'utf8');
code = code
  .replace(/^import\s+[\s\S]*?from\s+['"][^'"]+['"];?\s*$/gm, '')
  .replace(/\bexport\s+(?=async\s+function|function|let|const)/g, '');

const fetchCalls = [];
const sortEl = {
  value: 'score',
  dataset: { reverse: '0' },
  listeners: {},
  addEventListener(evt, fn) { this.listeners[evt] = fn; },
};
const sortableCols = [
  {
    dataset: { sort: 'speed' },
    listeners: {},
    addEventListener(evt, fn) { this.listeners[evt] = fn; },
  },
];
const listEl = {
  innerHTML: '',
  classList: { remove() {}, add() {} },
  offsetWidth: 0,
  querySelector() { return null; },
  querySelectorAll(sel) {
    if (sel === '.hwfit-header .hwfit-sortable') return sortableCols;
    return [];
  },
};
const elements = {
  'hwfit-sort': sortEl,
  'hwfit-list': listEl,
  'hwfit-usecase': { value: 'general', dataset: {}, closest() { return null; }, addEventListener() {} },
  'hwfit-engine': { value: '', dataset: {}, closest() { return null; }, addEventListener() {} },
  'hwfit-quant': { value: '', dataset: {}, addEventListener() {} },
  'hwfit-context': { value: '3', dataset: {}, addEventListener() {} },
  'hwfit-context-label': { textContent: '' },
  'hwfit-search': { value: '', dataset: {}, addEventListener() {} },
};

const sandbox = {
  console,
  setTimeout,
  clearTimeout,
  URLSearchParams,
  localStorage: { getItem() { return null; }, setItem() {} },
  window: {},
  document: {
    getElementById(id) { return elements[id] || null; },
    querySelectorAll() { return []; },
  },
  _envState: { remoteHost: '', servers: [] },
  _persistEnvState() {},
  _serverByVal() { return null; },
  _currentServerValue() { return 'local'; },
  _applyServerSelectColor() {},
  _syncServerSelectColors() {},
  _detectBackend() { return { backend: 'llamacpp', label: 'llama.cpp' }; },
  _downloadSourceRepo(m) { return { repo: m.name, kind: 'GGUF' }; },
  esc(s) { return String(s ?? ''); },
  modelLogo() { return ''; },
  spinnerModule: { createWhirlpool() { return { element: {}, destroy() {} }; } },
  fetchCalls,
  sortEl,
  listEl,
  sortableCols,
  fetch(url) { fetchCalls.push(url); throw new Error('Network fetch must not run on sort'); },
};

vm.createContext(sandbox);
vm.runInContext(code + `
_hwfitCache = {
  system: { backend: 'cuda', gpu_vram_gb: 24, total_ram_gb: 64 },
  models: [
    { name: 'org/small-fast', score: 60, required_gb: 4.0, speed_tps: 120, params_b: 7, context: 8192, fit_level: 'perfect', quant: 'Q4_K_M', release_date: '2026-01-01' },
    { name: 'org/large-smart', score: 92, required_gb: 18.0, speed_tps: 35, params_b: 32, context: 32768, fit_level: 'good', quant: 'Q4_K_M', release_date: '2026-03-01' },
  ],
};

_hwfitInit();

// 1. Trigger #hwfit-sort select change -> VRAM ascending
sortEl.value = 'vram';
sortEl.dataset.reverse = '1';
sortEl.listeners.change();

if (fetchCalls.length !== 0) {
  throw new Error('Expected 0 fetch calls after sort select change, got ' + fetchCalls.length);
}
let idxSmall = listEl.innerHTML.indexOf('data-model="org/small-fast"');
let idxLarge = listEl.innerHTML.indexOf('data-model="org/large-smart"');
if (idxSmall === -1 || idxLarge === -1 || idxSmall > idxLarge) {
  throw new Error('Expected org/small-fast before org/large-smart after VRAM ascending sort');
}

// 2. Trigger column header click -> Speed descending (default) then ascending (reverse)
sortableCols[0].listeners.click({ target: { closest() { return null; } } });
if (sortEl.value !== 'speed' || sortEl.dataset.reverse !== '0' || fetchCalls.length !== 0) {
  throw new Error('Expected speed descending sort from cache without fetch');
}
sortableCols[0].listeners.click({ target: { closest() { return null; } } });
if (sortEl.dataset.reverse !== '1' || fetchCalls.length !== 0) {
  throw new Error('Expected speed ascending sort from cache without fetch');
}
idxSmall = listEl.innerHTML.indexOf('data-model="org/small-fast"');
idxLarge = listEl.innerHTML.indexOf('data-model="org/large-smart"');
if (idxLarge > idxSmall) {
  throw new Error('Expected slower org/large-smart (35 t/s) before faster org/small-fast (120 t/s) on ascending speed sort');
}
console.log('OK');
`, sandbox);
""".replace("__JS_PATH__", json.dumps(js_path.as_posix()))

    proc = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, f"Node verification failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    assert "OK" in proc.stdout
