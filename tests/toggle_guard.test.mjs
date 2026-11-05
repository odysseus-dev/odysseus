// Behavioral coverage for the client-side shell-toggle guard and the
// mode-scoped toggle persistence helper.
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  canonicalToggleName,
  isBlockedAgentToggle,
  isToggleOn,
} from '../static/js/toggleGuard.js';
import {
  loadToggleState,
  saveToggleState,
  setToolToggle,
} from '../static/js/storage.js';

function installLocalStorage() {
  const data = new Map();
  globalThis.localStorage = {
    getItem(key) { return data.has(key) ? data.get(key) : null; },
    setItem(key, value) { data.set(key, String(value)); },
    removeItem(key) { data.delete(key); },
  };
  return data;
}

test('blocks shell enable across aliases, case, whitespace, and truthy spellings', () => {
  const names = ['bash', 'shell', 'terminal', 'BASH', ' Shell ', 'Terminal\t'];
  const states = [true, 1, 'true', '1', 'on', 'yes', 'enable', 'enabled', 'ON'];
  for (const name of names) {
    for (const state of states) {
      assert.equal(
        isBlockedAgentToggle({ toggle_name: name, state }),
        true,
        `${JSON.stringify(name)} / ${JSON.stringify(state)}`,
      );
    }
  }
});

test('allows shell disable and every non-shell toggle', () => {
  for (const state of [false, 0, 'false', '0', 'off', 'no', '', null, undefined]) {
    assert.equal(
      isBlockedAgentToggle({ toggle_name: 'bash', state }),
      false,
      String(state),
    );
  }
  for (const name of ['web', 'rag', 'research', 'incognito', 'document_editor']) {
    assert.equal(
      isBlockedAgentToggle({ toggle_name: name, state: true }),
      false,
      name,
    );
  }
});

test('normalizes toggle names and strict boolean state', () => {
  assert.equal(canonicalToggleName(' Shell '), 'bash');
  assert.equal(canonicalToggleName('Terminal'), 'bash');
  assert.equal(canonicalToggleName('WEB'), 'web');
  assert.equal(isToggleOn('false'), false);
  assert.equal(isToggleOn('off'), false);
  assert.equal(isToggleOn('true'), true);
  assert.equal(isToggleOn('on'), true);
  assert.equal(isToggleOn(1), true);
  assert.equal(isToggleOn(0), false);
});

test('persists web/bash under the current mode and other toggles bare', () => {
  installLocalStorage();
  saveToggleState({ mode: 'agent' });

  setToolToggle('bash', false);
  setToolToggle('web', true);
  setToolToggle('research', true);

  assert.deepEqual(loadToggleState(), {
    mode: 'agent',
    bash_agent: false,
    web_agent: true,
    research: true,
  });
});

test('an explicit mode overrides the stored mode', () => {
  installLocalStorage();
  saveToggleState({ mode: 'agent' });

  setToolToggle('bash', true, 'chat');

  assert.equal(loadToggleState().bash_chat, true);
  assert.equal(loadToggleState().bash_agent, undefined);
});
