import test from 'node:test';
import assert from 'node:assert/strict';
import { createLayout } from '../frontend/src/features/notes/ui/layout.js';

// A grid of fake cards that records layout reads and style writes. A read
// that follows a write forces the browser to lay the page out again: on a
// few hundred cards, one forced layout per card froze the tab for minutes.
function fakeGrid(count, { grid = false } = {}) {
  const log = { forced: 0, reads: 0, writes: 0, dirty: false };
  const read = () => { log.reads += 1; if (log.dirty) { log.forced += 1; log.dirty = false; } };
  const write = () => { log.writes += 1; log.dirty = true; };
  const frames = [];
  const pane = { classList: { contains: name => grid && name === 'notes-view-grid' } };
  const cards = Array.from({ length: count }, (_, i) => {
    const style = new Proxy({}, { set(target, key, value) { write(); target[key] = value; return true; } });
    return {
      dataset: { noteId: `n${i}` },
      style,
      classList: { contains: name => name === 'note-card' },
      get scrollHeight() { read(); return 100 + i; },
      getBoundingClientRect() { read(); return { left: 0, top: i * 10 }; },
      closest: () => pane,
      addEventListener() {},
    };
  });
  const body = {
    isConnected: true,
    closest: () => pane,
    querySelector: () => null,
    querySelectorAll: selector => (selector === '.note-card' ? cards : []),
  };
  globalThis.document = { querySelector: () => body };
  globalThis.window = { matchMedia: () => ({ matches: false }) };
  globalThis.requestAnimationFrame = f => frames.push(f);
  globalThis.getComputedStyle = () => ({ display: 'none' });
  return { log, cards, body, frames };
}

test('reflow reads every card before moving any, so a render costs one layout', () => {
  const { log, frames } = fakeGrid(300);
  const layout = createLayout();
  // Every card was 25 px lower before the render.
  const prev = new Map(Array.from({ length: 300 }, (_, i) => [`n${i}`, { left: 0, top: i * 10 + 25 }]));
  log.dirty = true; // the render just replaced the markup
  layout._animateReflow(prev);
  assert.equal(log.reads, 300);
  assert.equal(log.forced, 1, 'one layout after the render, none per card');
  assert.equal(frames.length, 1, 'one frame plays every card back');
  frames[0]();
  assert.equal(log.forced, 1);
});

test('masonry reads every card height before it writes any span', () => {
  const { log, cards } = fakeGrid(300, { grid: true });
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  const layout = createLayout();
  log.dirty = true;
  layout._applyMasonry(globalThis.document.querySelector());
  assert.equal(log.forced, 1, 'one layout for all 300 cards');
  assert.equal(cards[5].style.gridRowEnd, 'span 29');
  // One card replaced in place is sized on its own.
  log.forced = 0;
  log.dirty = true;
  layout._layoutCard(cards[0], cards[1]);
  assert.equal(log.forced, 1);
  delete globalThis.ResizeObserver;
});
