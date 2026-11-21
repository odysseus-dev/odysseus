// Every part of a note is edited in place: an aligned table cell, a formula
// (an island, edited as its own markdown), underline from the keyboard or the
// toolbar, and a mailto: link through the link menu. Each edit saves only
// what it changed; the rest of the note keeps its bytes.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { getNote, openNotes, tapOrClick, untilNote } from './notesHarness.mjs';

const evidence = process.env.NOTES_NATIVE_EVIDENCE_DIR;

const SOURCE = [
  'Shopping\n| Item | Qty |\n|:-----|----:|\n| Milk |   2 |',
  'Area $x^2$ here',
  'Write [me](mailto:me@example.org) soon',
  'Plain words to underline',
  '~~~ text\nkept  \n~~~',
].join('\n\n');

/** Select `text` inside the element `selector` names, in the window body. */
const select = (page, selector, text, { collapseToEnd = false } = {}) => page.evaluate(({ selector, text, collapseToEnd }) => {
  const host = document.querySelector(selector);
  const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const at = node.nodeValue.indexOf(text);
    if (at < 0) continue;
    host.closest('[contenteditable="true"]').focus();
    const range = document.createRange();
    range.setStart(node, at);
    range.setEnd(node, at + text.length);
    if (collapseToEnd) range.collapse(false);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    return true;
  }
  return false;
}, { selector, text, collapseToEnd });

export async function checkEditableInPlace({ browser, owner, size }) {
  const mobile = size === 'phone';
  const { context, page } = await openNotes(browser, owner, size);
  const api = owner.api;
  const win = page.locator('#note-window-modal');
  const body = win.locator('.note-window-body');
  const note = await (await api.post('/api/notes', { data: { title: `Editable ${size}`, content: SOURCE } })).json();
  await page.evaluate(id => void window.notesModule.openNote(id, { focus: null }), note.id);
  await body.waitFor();
  let expected = SOURCE;
  const saved = label => untilNote(api, note.id, n => n?.content === expected, label);

  // Nothing is locked and nothing points at the source view.
  assert.equal(await body.locator('[contenteditable="false"]:not(.md-island):not(.md-task-box)').count(), 0, 'no locked block');
  assert.equal(await win.locator('.note-window-hint').count(), 0, 'no markdown-only hint');
  assert.equal(await win.getByText('can only be edited', { exact: false }).count(), 0);
  assert.equal(await body.locator('td[data-align="right"]').count(), 1, 'the table keeps its alignment');

  // An aligned table cell, typed into.
  assert.ok(await select(page, '#note-window-modal .note-window-body td', 'Milk', { collapseToEnd: true }));
  await page.keyboard.type(' oat');
  expected = expected.replace('| Milk |   2 |', '| Milk oat |   2 |');
  await saved('the edited table cell');

  // The formula is an island: a click opens its markdown, in place.
  const island = body.locator('.md-island').first();
  if (mobile) {
    // Playwright's tap refuses an uneditable span inside contenteditable
    // (it reports the paragraph as intercepting), although the page's own
    // hit test at that point is the formula: tap the point itself.
    await island.scrollIntoViewIfNeeded();
    const box = await island.boundingBox();
    assert.equal(await page.evaluate(([x, y]) => !!document.elementFromPoint(x, y)?.closest('.md-island'), [box.x + box.width / 2, box.y + box.height / 2]), true);
    await page.touchscreen.tap(box.x + box.width / 2, box.y + box.height / 2);
  } else await island.click();
  const islandMenu = page.locator('.note-island-menu');
  await islandMenu.waitFor();
  const islandInput = islandMenu.locator('textarea');
  assert.equal(await islandInput.inputValue(), '$x^2$');
  if (evidence) await page.screenshot({ path: path.join(evidence, `editable-island-${size}.png`), animations: 'disabled' });
  await islandInput.fill('$y^3$');
  await tapOrClick(mobile, islandMenu.locator('[data-act="apply"]'));
  expected = expected.replace('$x^2$', '$y^3$');
  await saved('the edited formula');
  assert.equal(await body.locator('.md-island').first().getAttribute('data-md-src'), '$y^3$');

  // Underline: the keyboard on a desktop, the toolbar on a phone.
  assert.ok(await select(page, '#note-window-modal .note-window-body', 'underline'));
  if (mobile) await win.locator('[data-md="underline"]').tap();
  else await page.keyboard.press(process.platform === 'darwin' ? 'Meta+u' : 'Control+u');
  expected = expected.replace('Plain words to underline', 'Plain words to <u>underline</u>');
  await saved('the underline');
  assert.equal(await body.locator('u').textContent(), 'underline');

  // A mailto: link, edited through the link menu, title included.
  assert.ok(await select(page, '#note-window-modal .note-window-body a', 'me', { collapseToEnd: true }));
  await tapOrClick(mobile, win.locator('[data-md="link"]'));
  const linkMenu = page.locator('.note-link-menu');
  await linkMenu.waitFor();
  assert.equal(await linkMenu.locator('.note-link-input').inputValue(), 'mailto:me@example.org');
  await linkMenu.locator('.note-link-input').fill('mailto:you@example.org');
  await linkMenu.locator('.note-link-title').fill('You');
  if (evidence) await page.screenshot({ path: path.join(evidence, `editable-link-${size}.png`), animations: 'disabled' });
  await tapOrClick(mobile, linkMenu.locator('[data-act="apply"]'));
  expected = expected.replace('[me](mailto:me@example.org)', '[me](mailto:you@example.org "You")');
  await saved('the edited link');

  // Everything else is the stored source, byte for byte.
  assert.equal((await getNote(api, note.id)).note.content, expected);
  if (evidence) {
    fs.mkdirSync(evidence, { recursive: true });
    await win.locator('.note-window-content').screenshot({ path: path.join(evidence, `editable-window-${size}.png`), animations: 'disabled' });
  }
  await context.close();
}
