import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { SourceTextModule, SyntheticModule } from 'node:vm';
import { installDom, mount, createElement } from './documentLaneDom.mjs';

const { body } = installDom();
const frames = [];
globalThis.requestAnimationFrame = callback => { frames.push(callback); return frames.length; };
globalThis.setTimeout = callback => { callback(); return 1; };

const actions = mount('doc-editor-actions');
const wrapper = mount('doc-overflow-wrapper');
const toggle = mount('doc-overflow-toggle', 'button');
const menu = mount('doc-overflow-menu');
const copy = createElement('button');
copy.id = 'doc-copy-btn';
copy.classList.add('doc-collapsible-btn');
copy.innerHTML = '<b>copy</b>';
copy.title = 'Copy';
const exportButton = createElement('button');
exportButton.id = 'doc-export-btn';
exportButton.classList.add('doc-collapsible-btn');
exportButton.innerHTML = '<b>export</b>';
exportButton.title = 'Export';
const save = createElement('button');
save.id = 'doc-save-btn';
save.classList.add('doc-collapsible-btn');
save.title = 'Save';
actions.appendChild(copy);
actions.appendChild(exportButton);
actions.appendChild(save);

delete globalThis._syncOverflow;
const entryPath = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../frontend/src/features/documents/ui/header/overflow.js');
const source = fs.readFileSync(entryPath, 'utf8');
const entrySource = source + '\nexport { initActionOverflow };';
const entryUrl = pathToFileURL(entryPath).href;
const entry = new SourceTextModule(entrySource, { identifier: entryUrl });
await entry.link(async (specifier) => {
  assert.equal(specifier, '../exportDocx.js', 'overflow should only depend on the export menu owner');
  const dependencyUrl = pathToFileURL(path.resolve(path.dirname(entryPath), specifier)).href;
  const dependency = new SyntheticModule(['showExportMenu'], function () {
    this.setExport('showExportMenu', () => {});
  }, { identifier: dependencyUrl });
  await dependency.link(() => { throw new Error('Unexpected export menu dependency'); });
  return dependency;
});
await entry.evaluate();
const overflowModule = entry.namespace;

test('overflow initializes, tracks recency, and preserves the sync callback contract', () => {
  assert.equal(Object.hasOwn(globalThis, '_syncOverflow'), false);
  overflowModule.initActionOverflow();
  assert.equal(frames.length, 1);
  frames.shift()();
  assert.equal(overflowModule._syncOverflow instanceof Function, true);
  assert.equal(Object.hasOwn(globalThis, '_syncOverflow'), false);
  const installedSync = overflowModule._syncOverflow;
  assert.equal(wrapper.style.display, '');
  assert.equal(copy.classList.contains('doc-collapsed'), false);
  assert.equal(exportButton.classList.contains('doc-collapsed'), false);
  assert.equal(save.classList.contains('doc-collapsed'), true);

  save.click();
  assert.deepEqual(JSON.parse(localStorage.getItem('odysseus-doc-actions-recent')), ['doc-save-btn']);
  overflowModule._syncOverflow();
  assert.equal(overflowModule._syncOverflow, installedSync);
  assert.equal(save.classList.contains('doc-collapsed'), false);
  assert.equal(exportButton.classList.contains('doc-collapsed'), true);

  toggle.click();
  assert.equal(menu.classList.contains('open'), true);
  assert.equal(menu.parentElement, body);
  document.dispatchEvent?.({ type: 'click' });
});
