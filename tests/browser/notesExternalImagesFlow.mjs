// "Show external images in notes", the viewer's setting, off by default.
// Off, an external image is a link and the page never requests it. Turned on
// from Settings, it shows in the card and the note window, and the request
// carries no referrer and no cookie. A collaborator who keeps it off never
// requests the image the owner sees. Opening, editing around it, and editing
// it from its menu save only what changed.
import assert from 'node:assert/strict';
import path from 'node:path';
import { createAccount, getNote, openNotes, pngOf, tapOrClick, until, untilNote } from './notesHarness.mjs';

const evidence = process.env.NOTES_NATIVE_EVIDENCE_DIR;
const PREF = 'notes_show_external_images';
/** Serve a real PNG for the made-up image host, so a shown image loads, and record every request to it. */
async function watchImages(context, page) {
  const png = await pngOf(page, '#3f6a9a');
  const seen = [];
  await context.route(/^https?:\/\/images\.invalid\//, async route => {
    const request = route.request();
    seen.push({ url: request.url(), headers: await request.allHeaders() });
    await route.fulfill({ status: 200, contentType: 'image/png', body: png });
  });
  return seen;
}

const setPref = async (api, value) => assert.equal((await api.put(`/api/prefs/${PREF}`, { data: { value } })).ok(), true);
const pref = async api => (await (await api.get(`/api/prefs/${PREF}`)).json()).value;

export async function checkExternalImages({ browser, owner, size, collaborator }) {
  const mobile = size === 'phone';
  const shown = `https://images.invalid/shown-${size}.png?q=1`;
  const SOURCE = `Look ${size}\n\n![tracker](${shown})\n\n<img src="https://images.invalid/raw-${size}.png">\n\nhttp: ![plain](http://images.invalid/plain-${size}.png)`;
  await setPref(owner.api, false);
  await setPref(collaborator.api, false);
  const note = await (await owner.api.post('/api/notes', { data: { title: `External ${size}`, content: SOURCE } })).json();
  assert.equal((await owner.api.post(`/api/notes/${note.id}/share`, { data: { users: [collaborator.username], permission: 'view' } })).ok(), true);

  // Off: a link in the card and in the window, and no request at all.
  const { context, page } = await openNotes(browser, owner, size);
  const seen = await watchImages(context, page);
  await page.reload();
  await page.waitForFunction(() => window.notesModule?.openPanel);
  await page.evaluate(() => window.notesModule.openPanel());
  const card = page.locator(`.note-card[data-note-id="${note.id}"]`);
  await card.waitFor();
  assert.equal(await card.locator(`a[href="${shown}"]`).count(), 1, 'off: the card shows a link');
  assert.equal(await card.locator('img[src*="images.invalid"]').count(), 0, 'off: no image in the card');
  const win = page.locator('#note-window-modal');
  const body = win.locator('.note-window-body');
  await tapOrClick(mobile, card.locator('.note-card-title'));
  await body.waitFor();
  assert.equal(await body.locator(`a[data-md-image][href="${shown}"]`).count(), 1, 'off: the window shows the link chip');
  assert.equal(await body.locator('img[src*="images.invalid"]').count(), 0);
  await page.evaluate(() => document.querySelector('#note-window-modal .modal-close').click());
  await body.waitFor({ state: 'detached' });
  assert.deepEqual(seen.map(r => r.url), [], 'off: nothing was requested');

  // On, from Settings > Appearance.
  await page.evaluate(async () => (await import('/static/js/settings.js')).open('appearance'));
  const toggle = page.locator('#set-notes-external-images');
  await toggle.waitFor({ state: 'attached' });
  assert.equal(await toggle.isChecked(), false, 'the setting starts off');
  await toggle.evaluate(el => el.scrollIntoView({ block: 'center' }));
  if (evidence) await page.screenshot({ path: path.join(evidence, `external-setting-${size}.png`), animations: 'disabled' });
  await tapOrClick(mobile, page.locator('label.admin-switch:has(#set-notes-external-images) .admin-slider'));
  await until(() => pref(owner.api), v => v === true, 'the stored setting');
  await page.keyboard.press('Escape');
  await page.evaluate(() => window.notesModule.openPanel());

  // The card redraws with the image as its thumbnail.
  const thumb = card.locator(`img[src="${shown}"]`);
  await thumb.waitFor();
  assert.equal(await thumb.getAttribute('referrerpolicy'), 'no-referrer');
  await until(() => thumb.evaluate(img => img.complete && img.naturalWidth), w => w === 160, 'the card image loaded');
  if (evidence) await card.screenshot({ path: path.join(evidence, `external-card-${size}.png`), animations: 'disabled' });

  // The window shows it too; the raw <img> and the http: one stay as they were.
  await tapOrClick(mobile, card.locator('.note-card-title'));
  await body.waitFor();
  const img = body.locator('img.md-note-external');
  await img.waitFor();
  assert.equal(await img.getAttribute('src'), shown);
  assert.equal(await img.getAttribute('referrerpolicy'), 'no-referrer');
  assert.equal(await img.getAttribute('crossorigin'), null);
  await until(() => img.evaluate(el => el.complete && el.naturalWidth), w => w === 160, 'the window image loaded');
  assert.equal(await body.locator(`a[data-md-image][href="http://images.invalid/plain-${size}.png"]`).count(), 1, 'http: stays a link');
  if (evidence) await win.locator('.note-window-content').screenshot({ path: path.join(evidence, `external-window-${size}.png`), animations: 'disabled' });

  // Only the https: markdown image was requested, with no referrer and no cookie.
  assert.deepEqual([...new Set(seen.map(r => r.url))], [shown]);
  for (const { headers } of seen) {
    assert.equal(headers.referer, undefined, `no referrer: ${JSON.stringify(headers)}`);
    assert.equal(headers.cookie, undefined, `no cookie: ${JSON.stringify(headers)}`);
  }

  // Typing next to it saves only the typing.
  await body.locator('p', { hasText: `Look ${size}` }).first().evaluate(p => {
    const text = p.firstChild;
    p.closest('[contenteditable="true"]').focus();
    const range = document.createRange();
    range.setStart(text, text.nodeValue.length);
    range.collapse(true);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  });
  await page.keyboard.type(' here');
  let expected = SOURCE.replace(`Look ${size}`, `Look ${size} here`);
  await untilNote(owner.api, note.id, n => n?.content === expected, 'the typed text, the rest as stored');

  // Its menu edits the description, and the image stays an image.
  await page.evaluate(() => document.activeElement?.blur());
  await tapOrClick(mobile, img);
  const menu = page.locator('.note-image-link-menu');
  await menu.waitFor();
  assert.equal(await menu.locator('.note-link-input').inputValue(), shown);
  await menu.locator('.note-link-title').fill('tracking pixel');
  if (evidence) await page.screenshot({ path: path.join(evidence, `external-menu-${size}.png`), animations: 'disabled' });
  await tapOrClick(mobile, menu.locator('[data-act="apply"]'));
  expected = expected.replace(`![tracker](${shown})`, `![tracking pixel](${shown})`);
  await untilNote(owner.api, note.id, n => n?.content === expected, 'the new description');
  assert.equal(await body.locator('img.md-note-external').getAttribute('alt'), 'tracking pixel');
  assert.equal((await getNote(owner.api, note.id)).note.content, expected);
  await context.close();

  // The collaborator has it off: the shared note shows a link and nothing is requested.
  const theirs = await openNotes(browser, collaborator, size);
  const theirSeen = await watchImages(theirs.context, theirs.page);
  await theirs.page.reload();
  await theirs.page.waitForFunction(() => window.notesModule?.openPanel);
  await theirs.page.evaluate(() => window.notesModule.openPanel());
  const theirCard = theirs.page.locator(`.note-card[data-note-id="${note.id}"]`);
  await theirCard.waitFor();
  assert.equal(await theirCard.locator(`a[href="${shown}"]`).count(), 1, 'the collaborator sees a link');
  await tapOrClick(mobile, theirCard.locator('.note-card-title'));
  await theirs.page.locator('#note-window-modal .note-window-body').waitFor();
  assert.equal(await theirs.page.locator('#note-window-modal img[src*="images.invalid"]').count(), 0);
  assert.deepEqual(theirSeen, [], 'the collaborator requested nothing');
  // Turned on for them, the same note shows the image to them.
  await setPref(collaborator.api, true);
  await theirs.page.reload();
  await theirs.page.waitForFunction(() => window.notesModule?.openPanel);
  await theirs.page.evaluate(() => window.notesModule.openPanel());
  await theirs.page.locator(`.note-card[data-note-id="${note.id}"] img[src="${shown}"]`).waitFor();
  if (evidence) await theirs.page.screenshot({ path: path.join(evidence, `external-shared-${size}.png`), animations: 'disabled' });
  await theirs.context.close();

  await setPref(owner.api, false);
  await setPref(collaborator.api, false);
  await owner.api.delete(`/api/notes/${note.id}`);
}

export async function externalImagesCollaborator(owner) {
  return createAccount(owner, 'external-viewer');
}
