import test from 'node:test';
import assert from 'node:assert/strict';
import { loadMarkdown } from './streaming/markdownHarness.mjs';
import { parseHtml, templateDocument } from './helpers/miniDom.mjs';
import { serializeNodes } from '../frontend/src/features/notes/domain/markdownSerializer.js';
import { createExternalImagesSetting, EXTERNAL_IMAGES_PREF } from '../frontend/src/features/notes/application/externalImages.js';

// "Show external images in notes", the viewer's setting. Off, an image from
// another site is a link and nothing in the note can make the page fetch it.
// On, a markdown image with an https: address renders as an image that sends
// no referrer and no credentials; everything else stays as it was.
const { mdToHtml } = await loadMarkdown({ templates: templateDocument() });
const NOTES = { tasks: 'interactive', images: 'notes', dialect: 'notes' };
const off = (md, extra = {}) => mdToHtml(md, { ...NOTES, ...extra });
const on = (md, extra = {}) => mdToHtml(md, { ...NOTES, ...extra, externalImages: true });
const images = html => parseHtml(html).querySelectorAll('*').filter(el => el.nodeName === 'IMG');
// The nodes the editor puts in place of an edited image (richBody's renderedNodes).
const renderedNodes = html => {
  const nodes = parseHtml(html).childNodes;
  return nodes.length === 1 && nodes[0].nodeName === 'P' ? nodes[0].childNodes : nodes;
};
const externalSrcs = html => images(html).map(img => img.getAttribute('src')).filter(src => src && /^(https?:)?\/\//i.test(src));

const TRACKER = 'https://images.invalid/md.png?q=1';

test('off: an external image is a link, and the page has no external src', () => {
  for (const render of [off, md => off(md, { externalImages: false }), md => off(md, { sourceMap: true })]) {
    const html = render(`![tracker](${TRACKER})`);
    assert.deepEqual(externalSrcs(html), [], html);
    assert.match(html, /<a [^>]*href="https:\/\/images\.invalid\/md\.png\?q=1"/, html);
  }
});

test('on: a markdown https: image renders as an image with no referrer, no credentials, loaded lazily', () => {
  for (const extra of [{}, { sourceMap: true }, { mediaNote: 'n1' }]) {
    const [img] = images(on(`![tracker](${TRACKER})`, extra));
    assert.ok(img, JSON.stringify(extra));
    assert.equal(img.getAttribute('src'), TRACKER);
    assert.equal(img.getAttribute('alt'), 'tracker');
    assert.equal(img.getAttribute('referrerpolicy'), 'no-referrer');
    assert.equal(img.getAttribute('loading'), 'lazy');
    assert.equal(img.getAttribute('crossorigin'), null);
    assert.match(img.getAttribute('class'), /\bmd-note-external\b/);
    // No mark is carried: only the renderer builds it, raw HTML is sanitized.
    assert.equal(img.getAttribute('data-ody-external'), null);
  }
  // A size written as the title is kept, as for an upload.
  const [sized] = images(on('![](https://images.invalid/raw.png "w=33")'));
  assert.equal(sized.getAttribute('data-w'), '33');
});

test('on: only https: is shown; every other address stays a link', () => {
  for (const url of ['http://images.invalid/a.png', '//images.invalid/a.png', 'javascript:alert(1)', 'data:text/html;base64,PHN2Zz4=', 'ftp://images.invalid/a.png']) {
    const html = on(`![x](${url})`);
    assert.deepEqual(externalSrcs(html), [], `${url} -> ${html}`);
    assert.equal(images(html).some(img => /images\.invalid|javascript|text\/html/i.test(img.getAttribute('src') || '')), false, html);
  }
  // An SVG data: URL is part of the note, not an address: it shows either way, never as an external image.
  const svg = '![x](data:image/svg+xml;base64,PHN2Zz4=)';
  assert.equal(on(svg), off(svg));
  assert.deepEqual(externalSrcs(on(svg)), []);
  // Uploads are unchanged by the setting.
  const upload = '/api/upload/0123456789abcdef0123456789abcdef.png';
  assert.equal(on(`![](${upload})`), off(`![](${upload})`));
});

test('on: raw HTML in a note still cannot fetch from another site', () => {
  const payloads = [
    '<img src="https://images.invalid/raw.png">',
    '<details><summary>more</summary><img src="https://images.invalid/details.png"></details>',
    // A forged mark does not help: raw HTML always meets the note sanitizer.
    '<img class="md-note-image md-note-external" data-ody-external="0" src="https://images.invalid/forged.png">',
    '<img data-ody-external="" src="https://images.invalid/forged-empty.png">',
    '<p style="background:url(https://images.invalid/bg.png)">x</p>',
  ];
  for (const md of payloads) {
    const html = on(md);
    assert.deepEqual(externalSrcs(html), [], `${md} -> ${html}`);
    for (const el of parseHtml(html).querySelectorAll('*')) {
      for (const name of ['src', 'srcset', 'poster', 'style']) assert.equal(/images\.invalid/.test(el.getAttribute(name) || ''), false, `${md} -> ${html}`);
    }
  }
});

test('on: the editor stamps the image so it is written back exactly as stored', () => {
  for (const md of [
    `![tracker](${TRACKER})`,
    '![](https://images.invalid/raw.png "w=33")',
    '![a \\*b\\* c](https://images.invalid/esc.png)',
    'Before ![inline](https://images.invalid/i.png "Title") after',
  ]) {
    const root = parseHtml(on(md, { sourceMap: true }));
    assert.ok(root.querySelectorAll('*').some(el => / md-note-external /.test(` ${el.getAttribute('class')} `)), md);
    assert.equal(serializeNodes(root.childNodes).lines.join('\n'), md);
  }
});

test("on: a new description or address from the image's menu replaces only that image", () => {
  // What openImageLinkMenu does for a shown image: render the new markdown and put it in place.
  const md = `See ![tracker](${TRACKER}) here`;
  const root = parseHtml(on(md, { sourceMap: true }));
  const [p] = root.childNodes;
  const img = p.childNodes.find(n => n.nodeName === 'IMG');
  const [replacement] = renderedNodes(on('![tracking pixel](https://images.invalid/new.png)', { sourceMap: true }));
  assert.equal(replacement.nodeName, 'IMG');
  p.childNodes[p.childNodes.indexOf(img)] = replacement;
  replacement.parentNode = p;
  assert.equal(serializeNodes(root.childNodes).lines.join('\n'), 'See ![tracking pixel](https://images.invalid/new.png) here');
  // An http: address is kept, as the link it then shows as.
  const [link] = renderedNodes(on('![old](http://images.invalid/old.png)', { sourceMap: true }));
  assert.equal(link.nodeName, 'A');
  assert.equal(serializeNodes(parseHtml(on('![old](http://images.invalid/old.png)', { sourceMap: true })).childNodes).lines.join('\n'), '![old](http://images.invalid/old.png)');
});

function fakePrefs(initial) {
  const calls = [];
  let stored = initial;
  const request = async (url, init = {}) => {
    calls.push([init.method || 'GET', url, init.body ? JSON.parse(init.body) : null]);
    if (init.method === 'PUT') { stored = JSON.parse(init.body).value; return { ok: true, json: async () => ({ value: stored }) }; }
    return { ok: true, json: async () => ({ key: EXTERNAL_IMAGES_PREF, value: stored }) };
  };
  return { request, calls, stored: () => stored };
}

test('the setting is off until the stored value is exactly true', async () => {
  for (const [value, expected] of [[undefined, false], [null, false], ['true', false], [1, false], [false, false], [true, true]]) {
    const prefs = fakePrefs(value);
    const setting = createExternalImagesSetting({ origin: 'http://x', request: prefs.request });
    assert.equal(setting.enabled(), false, 'before load');
    assert.equal(await setting.load(), expected, JSON.stringify(value));
    assert.deepEqual(prefs.calls[0], ['GET', `http://x/api/prefs/${EXTERNAL_IMAGES_PREF}`, null]);
  }
  // A failed read leaves it off.
  const failing = createExternalImagesSetting({ origin: 'http://x', request: async () => { throw new Error('offline'); } });
  assert.equal(await failing.load(), false);
});

test('saving stores the per-user pref and tells the notes to redraw', async () => {
  const prefs = fakePrefs(undefined);
  const changes = [];
  const setting = createExternalImagesSetting({ origin: 'http://x', request: prefs.request, onChange: value => changes.push(value) });
  await setting.save(true);
  assert.equal(prefs.stored(), true);
  assert.equal(setting.enabled(), true);
  await setting.save(true);
  await setting.save(false);
  assert.deepEqual(changes, [true, false], 'one redraw per change');
  assert.deepEqual(prefs.calls.map(([method, , body]) => [method, body?.value]), [['PUT', true], ['PUT', true], ['PUT', false]]);
  // A refused save keeps the old value.
  const refused = createExternalImagesSetting({ origin: 'http://x', request: async () => ({ ok: false }) });
  await assert.rejects(refused.save(true));
  assert.equal(refused.enabled(), false);
});
