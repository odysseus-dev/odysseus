import test from 'node:test';
import assert from 'node:assert/strict';
import { createColors } from '../frontend/src/features/notes/ui/colors.js';

const { _bgImageUrl, _customColorStyle, _dotBg } = createColors({ api: {} });
const UPLOAD = '0123456789abcdef0123456789abcdef';

test('a picture background is an upload path on this site, whatever was stored', () => {
  assert.equal(_bgImageUrl(`bg:/api/upload/${UPLOAD}`), `/api/upload/${UPLOAD}`);
  assert.equal(_bgImageUrl(`bg:http://notes.test:7000/api/upload/${UPLOAD}.png`), `/api/upload/${UPLOAD}.png`);
  for (const bad of [
    'bg:https://tracker.example/p.png',
    `bg:/api/upload/${UPLOAD}');background:url(https://tracker.example/x`,
    `bg:/api/upload/${UPLOAD}" onmouseover="alert(1)`,
    'bg:javascript:alert(1)',
    'red',
    null,
  ]) {
    assert.equal(_bgImageUrl(bad), '', String(bad));
    assert.equal(_customColorStyle(bad), '', String(bad));
  }
  const style = _customColorStyle(`bg:/api/upload/${UPLOAD}`);
  assert.match(style, new RegExp(`url\\('/api/upload/${UPLOAD}'\\)`));
  assert.equal(/["<>]/.test(style), false);
  assert.equal(_dotBg('custom', `bg:/api/upload/${UPLOAD}') , url('https://tracker.example/x`).includes('tracker'), false);
});

test('an upload refused for coming too fast says so; other failures do not', async () => {
  const uploadUrl = id => `/api/upload/${id}`;
  const answer = res => createColors({ api: { upload: async () => res, uploadUrl } });
  assert.deepEqual(await answer({ ok: false, status: 429 })._uploadImages([{}]), { urls: [], rateLimited: true });
  assert.deepEqual(await answer({ ok: false, status: 500 })._uploadImages([{}]), { urls: [], rateLimited: false });
  assert.deepEqual(await answer({ ok: true, status: 200, json: async () => ({ files: [{ id: 'a' }, null] }) })._uploadImages([{}, {}]), { urls: ['/api/upload/a'], rateLimited: false });
  const offline = createColors({ api: { upload: async () => { throw new TypeError('offline'); }, uploadUrl } });
  assert.deepEqual(await offline._uploadImages([{}]), { urls: [], rateLimited: false });
  assert.deepEqual(await answer({ ok: false, status: 429 })._uploadImageFiles([{}]), [], 'the background picker still gets a list');
});
