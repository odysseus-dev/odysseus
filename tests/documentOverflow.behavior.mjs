import test from 'node:test';
import assert from 'node:assert/strict';
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

globalThis._syncOverflow = undefined;
await import('../frontend/src/features/documents/ui/header/overflow.js');

test('overflow initializes, tracks recency, and preserves the sync callback contract', () => {
  assert.equal(globalThis._syncOverflow, undefined);
  globalThis.initActionOverflow();
  assert.equal(frames.length, 1);
  frames.shift()();
  assert.equal(globalThis._syncOverflow instanceof Function, true);
  const installedSync = globalThis._syncOverflow;
  assert.equal(wrapper.style.display, '');
  assert.equal(copy.classList.contains('doc-collapsed'), false);
  assert.equal(exportButton.classList.contains('doc-collapsed'), false);
  assert.equal(save.classList.contains('doc-collapsed'), true);

  save.click();
  assert.deepEqual(JSON.parse(localStorage.getItem('odysseus-doc-actions-recent')), ['doc-save-btn']);
  globalThis._syncOverflow();
  assert.equal(globalThis._syncOverflow, installedSync);
  assert.equal(save.classList.contains('doc-collapsed'), false);
  assert.equal(exportButton.classList.contains('doc-collapsed'), true);

  toggle.click();
  assert.equal(menu.classList.contains('open'), true);
  assert.equal(menu.parentElement, body);
  document.dispatchEvent?.({ type: 'click' });
});
