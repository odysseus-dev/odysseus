import assert from 'node:assert/strict';
import test from 'node:test';

const store = new Map();
globalThis.localStorage = {
  getItem: (key) => (store.has(key) ? store.get(key) : null),
  setItem: (key, value) => { store.set(key, value); },
  removeItem: (key) => { store.delete(key); },
};

const {
  TOOL_ORDER_DEFAULT,
  mergeToolOrder,
  readToolOrder,
} = await import('../static/js/sidebar-reorder.js');

const SIDEBAR_KEYS = new Set(TOOL_ORDER_DEFAULT.filter((key) => key !== 'email'));

test('mergeToolOrder keeps rail-only keys when reordering sidebar', () => {
  const prev = ['calendar', 'email', 'cookbook', 'gallery'];
  const sidebarReorder = ['gallery', 'calendar', 'cookbook'];
  const merged = mergeToolOrder(prev, sidebarReorder, SIDEBAR_KEYS);
  assert.deepEqual(merged.slice(0, 4), ['gallery', 'email', 'calendar', 'cookbook']);
});

test('mergeToolOrder appends unknown keys from default order', () => {
  const prev = ['notes', 'tasks'];
  const merged = mergeToolOrder(prev, ['tasks', 'notes'], SIDEBAR_KEYS);
  for (const key of TOOL_ORDER_DEFAULT) {
    assert.ok(merged.includes(key), `missing ${key}`);
  }
});

test('readToolOrder returns default when storage empty', () => {
  store.clear();
  assert.deepEqual(readToolOrder(), [...TOOL_ORDER_DEFAULT]);
});

test('readToolOrder restores saved order across sessions', () => {
  store.clear();
  localStorage.setItem('ody_sidebar_tool_order_v1', JSON.stringify(['tasks', 'notes', 'cookbook']));
  assert.deepEqual(readToolOrder().slice(0, 3), ['tasks', 'notes', 'cookbook']);
});
