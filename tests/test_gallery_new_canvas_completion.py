"""Exercise the shipped New canvas callback with the editor's lifecycle helper."""
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("scenario,expected_landing,expected_errors", [
    ("created", 0, 0),
    ("canceled", 1, 0),
    ("failed", 1, 1),
    ("pending-created", 0, 0),
    ("pending-canceled", 1, 0),
])
def test_new_canvas_completion(scenario, expected_landing, expected_errors):
    script = r"""
const fs = require('node:fs'), vm = require('node:vm'), acorn = require('acorn');
const source = fs.readFileSync('static/js/gallery.js', 'utf8');
const tree = acorn.parse(source, { ecmaVersion: 'latest', sourceType: 'module' });
let callback, lifecycle;
function walk(node) {
  if (!node || typeof node !== 'object') return;
  if (node.type === 'FunctionDeclaration' && node.id?.name === 'isEditorOpen') lifecycle = node;
  if (node.type === 'CallExpression' && node.callee?.property?.name === 'addEventListener') {
    const target = node.callee.object;
    if (target?.callee?.property?.name === 'getElementById'
        && target.arguments[0]?.value === 'gallery-editor-new'
        && node.arguments[0]?.value === 'click') callback = node.arguments[1];
  }
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(walk);
    else if (value && typeof value === 'object') walk(value);
  }
}
walk(tree);
if (!callback || !lifecycle) throw new Error('Missing actual gallery callback/helper');
const scenario = SCENARIO;
let mounted = false, landing = 0, errors = 0, release;
const gate = new Promise(resolve => { release = resolve; });
const context = {
  _editorLoading: false,
  _editorMod: { isEditorOpen: () => mounted },
  // _buildEditor assigns the class to the root itself; this descendant
  // query correctly has no match. Native app checks cover the actual DOM.
  document: { querySelector(selector) {
    if (selector !== '#gallery-editor-container .gallery-editor') throw new Error(selector);
    return null;
  } },
  console: { error: () => { errors++; } },
  _renderEditorLanding: () => { landing++; },
  openEditor: async (...args) => {
    if (JSON.stringify(args) !== JSON.stringify([null, null, null, 'New canvas'])) throw new Error('Wrong open request');
    context._editorLoading = true;
    if (scenario.startsWith('pending-')) await gate;
    context._editorLoading = false;
    mounted = scenario.endsWith('created');
    if (scenario === 'failed') throw new Error('Owned setup failure');
  },
};
context.isEditorOpen = vm.runInNewContext('(' + source.slice(lifecycle.start, lifecycle.end) + ')', context);
const handler = vm.runInNewContext('(' + source.slice(callback.start, callback.end) + ')', context);
(async () => {
  const pending = handler();
  const landingBeforeSettlement = landing;
  release(); await pending;
  console.log(JSON.stringify({ landing, errors, mounted, landingBeforeSettlement }));
})().catch(error => { console.error(error); process.exitCode = 1; });
""".replace("SCENARIO", json.dumps(scenario))
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, text=True, capture_output=True, check=True,
    )
    observed = json.loads(result.stdout)
    assert observed["landingBeforeSettlement"] == 0
    assert observed["errors"] == expected_errors
    assert observed["mounted"] == scenario.endswith("created")
    assert observed["landing"] == expected_landing
