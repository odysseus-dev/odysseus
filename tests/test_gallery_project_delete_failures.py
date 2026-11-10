"""Exercise shipped project-delete callbacks and state transitions with IO ports."""
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("plan,remaining,selected,errors", [
    ({"kind": "single", "outcomes": [200]}, ["b", "c"], [], 0),
    ({"kind": "single", "outcomes": [500]}, ["a", "b", "c"], [], 1),
    ({"kind": "single", "outcomes": [403]}, ["a", "b", "c"], [], 1),
    ({"kind": "single", "outcomes": ["network"]}, ["a", "b", "c"], [], 1),
    ({"kind": "single", "outcomes": [], "cancel": True}, ["a", "b", "c"], [], 0),
    ({"kind": "bulk", "outcomes": [200, 200]}, ["c"], [], 0),
    ({"kind": "bulk", "outcomes": [200, 500]}, ["b", "c"], ["b"], 1),
    ({"kind": "bulk", "outcomes": [500, 403]}, ["a", "b", "c"], ["a", "b"], 1),
    ({"kind": "bulk", "outcomes": [200, "network"]}, ["b", "c"], ["b"], 1),
    ({"kind": "bulk", "outcomes": [], "cancel": True}, ["a", "b", "c"], ["a", "b"], 0),
    ({"kind": "bulk", "outcomes": [200, 500], "pending": True}, ["b", "c"], ["b"], 1),
    ({"kind": "bulk", "outcomes": [200, 200], "pending": True, "lateSelection": True}, ["c"], ["c"], 0),
], ids=["single-success", "single-http-500", "single-http-403", "single-network", "single-cancel",
        "bulk-success", "bulk-partial", "bulk-http-failures", "bulk-network", "bulk-cancel",
        "pending-partial", "late-selection"])
def test_project_delete_outcomes(plan, remaining, selected, errors):
    script = r"""
const fs = require('node:fs'), vm = require('node:vm'), acorn = require('acorn');
const source = fs.readFileSync('static/js/gallery.js', 'utf8');
const tree = acorn.parse(source, {ecmaVersion: 'latest', sourceType: 'module'});
let single, helper, wiring;
function walk(node) {
  if (!node || typeof node !== 'object') return;
  if (node.type === 'FunctionDeclaration' && node.id.name === '_deleteEditorDrafts') helper = node;
  if (node.type === 'FunctionDeclaration' && node.id.name === '_draftsWireOnce') wiring = node;
  if (node.type === 'CallExpression' && node.callee?.property?.name === 'addEventListener'
      && node.arguments[0]?.value === 'click' && node.arguments[1]
      && source.slice(node.arguments[1].start, node.arguments[1].end).includes("'Delete this project?'")) single = node.arguments[1];
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(walk);
    else if (value && typeof value === 'object') walk(value);
  }
}
walk(tree);
if (!single || !wiring) throw new Error('Missing shipped project callbacks');
const plan = __PLAN__, listeners = {}, calls = [], failures = [], cards = {};
const server = new Set(['a', 'b', 'c']);
for (const id of server) cards[id] = {dataset: {draftId: id}, removing: false,
  classList: {add(name) { if (name === 'gallery-draft-removing') cards[id].removing = true; }}};
let resolveGate;
const gate = new Promise(resolve => {resolveGate = resolve});
const context = {
  API_BASE: 'http://owned.invalid', _draftsWired: false, _draftsSearch: '',
  _draftsCache: ['a','b','c'].map(id => ({id, name: id})),
  _draftsSelected: new Set(plan.kind === 'bulk' ? ['a','b'] : []),
  _draftsSelectMode: plan.kind === 'bulk',
  window: {},
  btn: {dataset: {draftId: 'a'}, closest: () => cards.a},
  document: {
    getElementById(id) {
      if (id === 'gallery-editor-drafts-grid') return {
        querySelectorAll: () => Object.values(cards),
        querySelector: selector => cards[selector.match(/data-draft-id="([^"]+)"/)[1]],
      };
      return {addEventListener: (event, callback) => {listeners[id + ':' + event] = callback;}};
    },
    querySelectorAll: () => [],
  },
  uiModule: {styledConfirm: async () => !plan.cancel, showError: text => failures.push(text)},
  setTimeout: callback => {callback(); return 1},
  _draftsPaint: () => {},
  _renderEditorDrafts: async () => {
    context._draftsCache = context._draftsCache.filter(d => server.has(d.id));
  },
  fetch: async (url, options) => {
    if (options.method !== 'DELETE' || options.credentials !== 'same-origin') throw new Error('Wrong request');
    const index = calls.length, id = decodeURIComponent(url.split('/').at(-1)); calls.push(id);
    if (plan.pending) await gate;
    const outcome = plan.outcomes[index];
    if (outcome === 'network') throw new Error('Owned network rejection');
    if (outcome >= 200 && outcome < 300) server.delete(id);
    return {ok: outcome >= 200 && outcome < 300, status: outcome};
  },
};
vm.createContext(context);
if (helper) vm.runInContext(source.slice(helper.start, helper.end), context);
vm.runInContext(source.slice(wiring.start, wiring.end), context);
context._draftsWireOnce();
const handler = plan.kind === 'single'
  ? vm.runInContext('(' + source.slice(single.start, single.end) + ')', context)
  : listeners['gallery-editor-drafts-bulk-delete:click'];
(async () => {
  const pending = handler({stopPropagation() {}});
  for (let n=0;n<5;n++) await Promise.resolve();
  const before = {remaining: context._draftsCache.map(d => d.id), selected: [...context._draftsSelected],
    removing: Object.values(cards).some(card => card.removing)};
  if (plan.lateSelection) context._draftsSelected.add('c');
  resolveGate(); await pending;
  console.log(JSON.stringify({remaining: context._draftsCache.map(d => d.id),
    selected: [...context._draftsSelected], selectMode: context._draftsSelectMode, errors: failures,
    calls, server: [...server], before}));
})().catch(error => {console.error(error);process.exitCode=1});
""".replace("__PLAN__", json.dumps(plan))
    result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, check=True)
    observed = json.loads(result.stdout)
    assert observed["remaining"] == remaining
    assert observed["server"] == remaining
    assert observed["selected"] == selected
    assert observed["selectMode"] is bool(selected)
    assert len(observed["errors"]) == errors
    if errors:
        assert "Please try again" in observed["errors"][0]
    assert observed["calls"] == ([] if plan.get("cancel") else ["a", "b"] if plan["kind"] == "bulk" else ["a"])
    if plan.get("pending"):
        assert observed["before"] == {
            "remaining": ["a", "b", "c"], "selected": ["a", "b"], "removing": False,
        }
