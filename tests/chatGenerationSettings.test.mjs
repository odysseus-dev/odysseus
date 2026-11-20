import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const chat = await readFile(new URL('../static/js/chat.js', import.meta.url), 'utf8');
const start = chat.indexOf('  async function _saveChatGenerationSettings(change)');
const end = chat.indexOf('export async function compactCurrentChatContext', start);
assert.ok(start > 0 && end > start, 'generation-settings save source not found');
const source = chat.slice(start, end);

function makeHeader(thinkingMode, storedMode) {
  const session = { id: 's1', thinking_mode: storedMode, temperature_override: 1.2, max_tokens_override: 4096 };
  const requests = [];
  const factory = new Function('session', 'requests', 'thinkingMode', `
    let _contextHeaderData = { thinking_mode: thinkingMode, temperature_override: null, max_tokens_override: null };
    const _resolveCurrentSessionId = async () => session.id;
    const _liveSessionModule = () => ({ getSessions: () => [session] });
    const uiModule = { showError: message => { throw new Error(message); } };
    const fetch = async (url, options) => {
      const body = JSON.parse(options.body);
      requests.push({ url, body });
      // The endpoint preserves omitted settings and returns the current stored values.
      Object.assign(session, body);
      return { ok: true, json: async () => ({ ...session }) };
    };
    ${source}
    return _saveChatGenerationSettings;
  `);
  return { save: factory(session, requests, thinkingMode), session, requests };
}

for (const [label, headerMode, storedMode] of [
  ['effort-only context reports off', 'off', 'effort:low'],
  ['header predates a newer effort pick', 'effort:low', 'effort:high'],
]) {
  for (const change of [{ temperature_override: 0.7 }, { max_tokens_override: null }]) {
    test(`${label}: ${Object.keys(change)[0]} saves only the edited setting`, async () => {
      const h = makeHeader(headerMode, storedMode);
      assert.equal(await h.save(change), true);
      assert.deepEqual(h.requests, [{ url: '/api/session/s1/generation-settings', body: change }]);
      assert.equal(h.session.thinking_mode, storedMode);
      assert.equal(h.session.temperature_override, change.temperature_override ?? 1.2);
      assert.equal(h.session.max_tokens_override, 'max_tokens_override' in change ? null : 4096);
    });
  }
}

for (const mode of ['on', 'off']) {
  test(`an intentional binary Thinking toggle still saves ${mode}`, async () => {
    const h = makeHeader(mode === 'on' ? 'off' : 'on', mode === 'on' ? 'off' : 'on');
    assert.equal(await h.save({ thinking_mode: mode }), true);
    assert.deepEqual(h.requests[0].body, { thinking_mode: mode });
    assert.equal(h.session.thinking_mode, mode);
    assert.equal(h.session.temperature_override, 1.2);
    assert.equal(h.session.max_tokens_override, 4096);
  });
}
