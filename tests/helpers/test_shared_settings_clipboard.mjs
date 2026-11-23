import assert from 'node:assert/strict';
import test from 'node:test';

import { copyText } from '../../frontend/src/shared/platform/clipboard.js';

function installClipboard(t, { secure = true, writeText, copy = () => true, select } = {}) {
  const events = [];
  const field = {
    value: '',
    style: {},
    setAttribute(name, value) { events.push(['attribute', name, value]); },
    focus() { events.push(['focus']); },
    select() {
      events.push(['select']);
      select?.();
    },
    setSelectionRange(start, end) { events.push(['range', start, end]); },
    remove() { events.push(['remove']); },
  };
  const globals = {
    window: { isSecureContext: secure },
    navigator: { clipboard: writeText && { writeText } },
    document: {
      createElement(name) {
        events.push(['create', name]);
        return field;
      },
      body: {
        appendChild(node) {
          assert.equal(node, field);
          events.push(['append', node.value]);
        },
      },
      execCommand(command) {
        events.push(['command', command]);
        return copy();
      },
    },
  };
  for (const [name, value] of Object.entries(globals)) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, { value, configurable: true });
    t.after(() => {
      if (previous) Object.defineProperty(globalThis, name, previous);
      else delete globalThis[name];
    });
  }
  return events;
}

test('empty input avoids both clipboard paths', async t => {
  const events = installClipboard(t, { writeText() { assert.fail('Unexpected write'); } });
  for (const value of ['', null, undefined, 0, false]) {
    assert.equal(await copyText(value), false);
  }
  assert.deepEqual(events, []);
});

test('secure clipboard succeeds without focusing a temporary field', async t => {
  const writes = [];
  const events = installClipboard(t, { async writeText(value) { writes.push(value); } });
  assert.equal(await copyText(42), true);
  assert.deepEqual(writes, ['42']);
  assert.deepEqual(events, []);
});

test('rejected clipboard write falls back to focusing, selecting and removing the field', async t => {
  const writes = [];
  const events = installClipboard(t, {
    async writeText(value) {
      writes.push(value);
      throw new Error('clipboard unavailable');
    },
  });
  assert.equal(await copyText('a&b'), true);
  assert.deepEqual(writes, ['a&b']);
  assert.deepEqual(events, [
    ['create', 'textarea'], ['attribute', 'readonly', 'readonly'],
    ['append', 'a&b'], ['focus'], ['select'], ['range', 0, 3],
    ['command', 'copy'], ['remove'],
  ]);
});

test('insecure context skips the browser clipboard and returns false when copy is declined', async t => {
  const events = installClipboard(t, {
    secure: false,
    writeText() { assert.fail('Clipboard API must be bypassed'); },
    copy: () => false,
  });
  assert.equal(await copyText('text'), false);
  assert.deepEqual(events.slice(-2), [['command', 'copy'], ['remove']]);
});

test('copy command exceptions return false and still remove the field', async t => {
  const events = installClipboard(t, { copy() { throw new Error('copy disabled'); } });
  assert.equal(await copyText('text'), false);
  assert.deepEqual(events.slice(-2), [['command', 'copy'], ['remove']]);
});

test('selection failures remove the field and preserve the original rejection', async t => {
  const error = new Error('selection unavailable');
  const events = installClipboard(t, { select() { throw error; } });
  await assert.rejects(copyText('text'), err => err === error);
  assert.deepEqual(events.slice(-2), [['select'], ['remove']]);
});
