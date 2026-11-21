// The owner's note window as a keyboard and touch user meets it: where focus
// goes, and how big its controls are.
import assert from 'node:assert/strict';
import { openNotes, pngOf, tapOrClick, until, untilNote } from './notesHarness.mjs';

const WINDOW = '#note-window-modal';
const openById = async (page, id) => {
  await page.evaluate(id => void window.notesModule.openNote(id, { focus: null }), id);
  await page.locator(`${WINDOW} .note-window-body`).waitFor();
};
const closeWindow = async page => {
  await page.evaluate(() => document.querySelector('#note-window-modal .modal-close')?.click());
  await page.locator(WINDOW).waitFor({ state: 'detached' });
};
/** A tag and a reminder set elsewhere reach the open window with the next re-read, and the window's next tag edit keeps them. */
/** A card opens from the keyboard, and the phone's search and quick-add inputs are big enough that iOS does not zoom on focus. */
async function checkCardKeyboard({ browser, owner, size }) {
  const api = owner.api;
  const note = await (await api.post('/api/notes', { data: { title: '', content: `Untitled by keyboard ${size}` } })).json();
  const { context, page } = await openNotes(browser, owner, size);
  const title = page.locator(`.note-card[data-note-id="${note.id}"] .note-card-title`);
  await title.focus();
  assert.equal(await title.getAttribute('aria-label'), 'Untitled note');
  await page.keyboard.press('Enter');
  await page.locator(`${WINDOW}[data-note-id="${note.id}"] .note-window-body`).waitFor();
  await closeWindow(page);
  if (size === 'phone') {
    const sizes = await page.evaluate(() => ['#notes-search', '.notes-quick-input'].map(s => getComputedStyle(document.querySelector(s)).fontSize));
    assert.deepEqual(sizes, ['16px', '16px'], 'no input under 16px on a phone');
  }
  await context.close();
  await api.delete(`/api/notes/${note.id}`);
}

const activeIs = (page, selector) => page.evaluate(selector => !!document.activeElement?.matches(selector), selector);

/** Focus comes back to the card after closing (Escape in the body or the tag input, or the close button), after Delete, and to the body after Escape in the link menu. */
async function checkFocusReturns({ browser, owner, size }) {
  const mobile = size === 'phone';
  const api = owner.api;
  const make = async title => (await api.post('/api/notes', { data: { title, content: 'Focus' } })).json();
  const [first, second] = [await make(`Focus A ${size}`), await make(`Focus B ${size}`)];
  const { context, page } = await openNotes(browser, owner, size);
  const handle = id => `.note-card[data-note-id="${id}"] .note-card-title`;
  const openByKeyboard = async id => {
    await page.locator(handle(id)).focus();
    await page.keyboard.press('Enter');
    await page.locator(`${WINDOW}[data-note-id="${id}"] .note-window-body`).waitFor();
  };
  const backOnCard = async (id, label) => {
    await page.waitForFunction(selector => document.activeElement?.matches(selector), handle(id), { timeout: 5000 })
      .catch(async () => assert.fail(`${label}: focus is on ${await page.evaluate(() => document.activeElement?.outerHTML?.slice(0, 120))}`));
  };

  await openByKeyboard(first.id);
  // The phone sheet has no close button (it is swiped down); close it the way the shared modal code does.
  if (mobile) await closeWindow(page);
  else { await page.locator(`${WINDOW} .note-window-body`).focus(); await page.keyboard.press('Escape'); }
  await page.locator(WINDOW).waitFor({ state: 'detached' });
  await backOnCard(first.id, 'after closing');

  await openByKeyboard(first.id);
  await page.locator(`${WINDOW} .note-window-tag-input`).focus();
  // With tags on other notes, focus shows suggestions: Escape closes those first.
  if (await page.locator('.note-tag-menu').isVisible()) {
    await page.keyboard.press('Escape');
    await page.locator('.note-tag-menu').waitFor({ state: 'detached' });
    assert.equal(await page.locator(WINDOW).count(), 1, 'the first Escape only closes the suggestions');
  }
  await page.keyboard.press('Escape');
  await page.locator(WINDOW).waitFor({ state: 'detached' });
  await backOnCard(first.id, 'after Escape in the tag input');

  await openByKeyboard(first.id);
  await page.locator(`${WINDOW} .note-window-body`).focus();
  await page.locator(`${WINDOW} .note-window-toolbar [data-md="link"]`).click();
  await page.locator('.note-link-menu .note-link-input').waitFor();
  await page.waitForFunction(() => document.activeElement?.classList.contains('note-link-input'));
  await page.keyboard.press('Escape');
  await page.locator('.note-link-menu').waitFor({ state: 'detached' });
  assert.equal(await activeIs(page, `${WINDOW} .note-window-body`), true, 'Escape in the link menu returns to the body');
  await closeWindow(page);

  // Delete: focus moves to the card that took the deleted one's place.
  const order = await page.locator('#notes-pane .note-card[data-note-id]').evaluateAll(cards => cards.map(c => c.dataset.noteId));
  const doomed = order.indexOf(first.id) < order.indexOf(second.id) ? first : second;
  const survivor = doomed === first ? second : first;
  const at = order.indexOf(doomed.id);
  const expected = order[at + 1] || order[at - 1];
  await openByKeyboard(doomed.id);
  await page.locator(`${WINDOW} [data-act="delete"]`).click();
  await page.locator('#styled-confirm-ok').click();
  await page.locator(WINDOW).waitFor({ state: 'detached' });
  await backOnCard(expected, 'after Delete');
  await context.close();
  await api.delete(`/api/notes/${survivor.id}`);
  await api.delete(`/api/notes/${doomed.id}`).catch(() => {});
}

/** On a phone the window is a sheet that slides up from the bottom: wait until it has stopped moving before measuring anything in it. */
async function settledSheet(page) {
  let last = null;
  await until(() => page.evaluate(() => {
    const q = s => document.querySelector(`#note-window-modal ${s}`).getBoundingClientRect();
    return { top: Math.round(q('.note-window-content').top), footer: q('.note-window-footer').bottom, vh: innerHeight };
  }), v => { const still = last && last.top === v.top; last = v; return still && v.footer <= v.vh + 1; }, 'the settled sheet', 5000);
}

/** The toolbar is one tab stop with arrow-key movement. On a phone it is one row that scrolls sideways (6cb0dfcb: wrapped, it took three rows of the note), and swiping it brings every button fully on screen where a tap lands on it. */
async function checkToolbar({ browser, owner, size }) {
  const api = owner.api;
  const note = await (await api.post('/api/notes', { data: { title: `Toolbar ${size}`, content: 'Tools' } })).json();
  const { context, page } = await openNotes(browser, owner, size);
  await openById(page, note.id);
  const toolbar = page.locator(`${WINDOW} .note-window-toolbar`);
  assert.equal(await toolbar.getAttribute('role'), 'toolbar');
  const stops = () => toolbar.locator('[data-md]').evaluateAll(buttons => buttons.filter(b => b.tabIndex === 0).map(b => b.dataset.md));
  assert.deepEqual(await stops(), ['bold'], 'one tab stop');
  if (size === 'phone') {
    await settledSheet(page);
    // Every button fully on screen and hit-testable (a tap lands on it), at the strip's current scroll position.
    const shown = () => toolbar.evaluate(bar => {
      const box = bar.getBoundingClientRect();
      const vw = document.documentElement.clientWidth;
      return Array.from(bar.querySelectorAll('[data-md]')).filter(b => {
        const r = b.getBoundingClientRect();
        if (!(r.width > 0 && r.left >= Math.max(box.left, 0) - 0.5 && r.right <= Math.min(box.right, vw) + 0.5 && r.top >= box.top - 0.5 && r.bottom <= box.bottom + 0.5)) return false;
        const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return at === b || b.contains(at);
      }).map(b => b.dataset.md);
    });
    const all = await toolbar.locator('[data-md]').evaluateAll(buttons => buttons.map(b => b.dataset.md));
    const rows = await toolbar.locator('[data-md]').evaluateAll(buttons => new Set(buttons.map(b => Math.round(b.getBoundingClientRect().top))).size);
    assert.equal(rows, 1, 'the toolbar is one row');
    // Sideways strip: swipe it with real input, not scrollIntoView (which also moves an overflow:hidden strip a finger cannot).
    const seen = new Set(await shown());
    const bar = await toolbar.boundingBox();
    await page.mouse.move(bar.x + bar.width / 2, bar.y + bar.height / 2);
    for (let i = 0; i < 20 && seen.size < all.length; i++) {
      await page.mouse.wheel(120, 0);
      await page.waitForTimeout(50);
      for (const md of await shown()) seen.add(md);
    }
    assert.deepEqual(all.filter(md => !seen.has(md)), [], 'every toolbar button is on screen and tappable at some point of a sideways swipe');
  } else {
    await toolbar.locator('[data-md="bold"]').focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await activeIs(page, '[data-md="italic"]'), true, 'ArrowRight moves on');
    assert.deepEqual(await stops(), ['italic'], 'the tab stop follows focus');
    await page.keyboard.press('End');
    assert.equal(await activeIs(page, '[data-md="source"]'), true, 'End goes to the last button');
    await page.keyboard.press('ArrowRight');
    assert.equal(await activeIs(page, '[data-md="bold"]'), true, 'and wraps round');
    await page.keyboard.press('Tab');
    assert.equal(await activeIs(page, `${WINDOW} [data-md]`), false, 'Tab leaves the toolbar');
  }
  await closeWindow(page);
  await context.close();
  await api.delete(`/api/notes/${note.id}`);
}

/** A task typed in the window: its checkbox is announced with the task's text, and the text still saves as plain markdown. */
async function checkNewTaskName({ browser, owner, size }) {
  const api = owner.api;
  const note = await (await api.post('/api/notes', { data: { title: `Task name ${size}`, content: 'Intro' } })).json();
  const { context, page } = await openNotes(browser, owner, size);
  await openById(page, note.id);
  const body = page.locator(`${WINDOW} .note-window-body`);
  await body.click();
  await page.keyboard.press('End');
  await page.keyboard.press('Enter');
  await page.locator(`${WINDOW} .note-window-toolbar [data-md="check"]`).click();
  await page.keyboard.type('Buy milk');
  await page.keyboard.press('Enter');
  await page.keyboard.type('Call home');
  const named = page.locator(WINDOW).getByRole('checkbox', { name: 'Buy milk' });
  await named.waitFor();
  assert.equal(await page.locator(WINDOW).getByRole('checkbox', { name: 'Call home' }).count(), 1);
  const saved = await untilNote(api, note.id, n => /Call home/.test(n?.content || ''), 'the typed tasks');
  // Typed blocks of different kinds are written a blank line apart (2028da2d).
  assert.equal(saved.content, 'Intro\n- [ ] Buy milk\n- [ ] Call home');
  await closeWindow(page);
  await context.close();
  await api.delete(`/api/notes/${note.id}`);
}

/** One rule styles the window's icon buttons, and its text uses the notes stylesheet's pixel sizes. */
async function checkWindowStyles({ browser, owner, size }) {
  if (size === 'phone') return;
  const api = owner.api;
  const note = await (await api.post('/api/notes', { data: { title: `Styles ${size}`, content: 'Styled' } })).json();
  const { context, page } = await openNotes(browser, owner, size);
  await openById(page, note.id);
  const styles = await page.evaluate(() => {
    const pick = el => { const c = getComputedStyle(el); return [c.width, c.height, c.borderRadius, c.color].join(' '); };
    const q = s => document.querySelector(`#note-window-modal ${s}`);
    return {
      buttons: [q('.note-window-remind'), q('[data-act="archive"]'), q('[data-act="delete"]')].map(pick),
      fonts: ['.note-window-title', '.note-window-body', '.note-window-source'].map(s => getComputedStyle(q(s)).fontSize),
    };
  });
  assert.equal(new Set(styles.buttons).size, 1, `icon buttons differ: ${JSON.stringify(styles.buttons)}`);
  assert.match(styles.buttons[0], /^30px 30px 7px /);
  assert.deepEqual(styles.fonts, ['16px', '14px', '13px']);
  await closeWindow(page);
  await context.close();
  await api.delete(`/api/notes/${note.id}`);
}

/** The hit area around each control, found by hit-testing outwards from its centre (a pseudo-element target has no rect of its own). */
const hitAreas = (page, selectors) => page.evaluate(selectors => {
  const out = {};
  for (const [name, selector] of Object.entries(selectors)) {
    const el = document.querySelector(selector);
    if (!el) { out[name] = null; continue; }
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const hits = (x, y) => { const at = document.elementFromPoint(x, y); return !!at && (at === el || el.contains(at)); };
    const reach = (dx, dy) => { let d = 0; while (d < 40 && hits(cx + dx * (d + 1), cy + dy * (d + 1))) d += 1; return d; };
    out[name] = { w: reach(-1, 0) + reach(1, 0) + 1, h: reach(0, -1) + reach(0, 1) + 1 };
  }
  return out;
}, selectors);

/** On a phone every control in the window can be hit in at least a 44px square. */
async function checkTouchTargets({ browser, owner, size }) {
  if (size !== 'phone') return;
  const api = owner.api;
  const note = await (await api.post('/api/notes', { data: { title: `Targets ${size}`, content: '- [ ] first\n- [ ] second', label: 'alpha beta' } })).json();
  const { context, page } = await openNotes(browser, owner, size);
  await openById(page, note.id);
  await settledSheet(page);
  const W = WINDOW;
  const areas = await hitAreas(page, {
    remind: `${W} .note-window-remind`, bold: `${W} .note-window-toolbar [data-md="bold"]`,
    archive: `${W} [data-act="archive"]`, remove: `${W} [data-act="delete"]`,
    dotNone: `${W} .note-color-dot[data-color=""]`, dotRed: `${W} .note-color-dot[data-color="red"]`, dotCustom: `${W} .note-color-dot[data-color="custom"]`,
    tagRemove: `${W} .note-tag-rm`, taskBox: `${W} .md-task-box`,
  });
  // The toolbar scrolls sideways on a phone: bring its last button into view before measuring it.
  await page.locator(`${W} .note-window-toolbar [data-md="source"]`).evaluate(b => b.scrollIntoView({ block: 'nearest', inline: 'nearest' }));
  Object.assign(areas, await hitAreas(page, { source: `${W} .note-window-toolbar [data-md="source"]` }));
  console.log(JSON.stringify({ touchTargets: areas }));
  for (const [name, area] of Object.entries(areas)) {
    assert.ok(area && area.w >= 44 && area.h >= 44, `${name}: ${JSON.stringify(area)}`);
  }
  await closeWindow(page);
  await context.close();
  await api.delete(`/api/notes/${note.id}`);
}

/** Quick-add's photo button opens a new note window and its image picker in one step; the chosen image lands in the new note. */
async function checkQuickAddPhoto({ browser, owner, size }) {
  const api = owner.api;
  const { context, page } = await openNotes(browser, owner, size);
  const png = await pngOf(page, '#9a3f6a');
  const before = new Set(((await (await api.get('/api/notes')).json()).notes || []).map(n => n.id));
  const [chooser] = await Promise.all([page.waitForEvent('filechooser', { timeout: 5000 }), page.locator('.notes-quick-add [data-action="photo"]').click()]);
  await page.locator(`${WINDOW} .note-window-body`).waitFor();
  await chooser.setFiles({ name: 'quick.png', mimeType: 'image/png', buffer: png });
  await page.locator(`${WINDOW} .note-window-body img.md-note-image`).waitFor();
  const created = await until(async () => ((await (await api.get('/api/notes')).json()).notes || []).find(n => !before.has(n.id) && /!\[\]\(\/api\/upload\//.test(n.content || '')),
    Boolean, 'the note quick-add created');
  await closeWindow(page);
  await context.close();
  await api.delete(`/api/notes/${created.id}`);
}

/** The quick bar has one way in, a note: what is typed names it, Enter moves to the body, and the toolbar's Checklist makes it a to-do list. The card shows the title and the task. */
async function checkQuickAddChecklist({ browser, owner, size }) {
  const api = owner.api;
  const { context, page } = await openNotes(browser, owner, size);
  const before = new Set(((await (await api.get('/api/notes')).json()).notes || []).map(n => n.id));
  const input = page.locator('.notes-quick-input');
  assert.equal(await input.getAttribute('placeholder'), 'Add a note…');
  assert.equal(await page.locator('.notes-quick-add button').count(), 1, 'the photo button is the only button left in the bar');
  await input.focus();
  await page.keyboard.type(`Groceries ${size}`);
  const title = page.locator(`${WINDOW} .note-window-title`);
  await title.waitFor();
  assert.equal(await title.inputValue(), `Groceries ${size}`);
  await page.keyboard.press('Enter');
  await tapOrClick(size === 'phone', page.locator(`${WINDOW} .note-window-toolbar [data-md="check"]`));
  await page.keyboard.type('Milk');
  const created = await until(async () => ((await (await api.get('/api/notes')).json()).notes || []).find(n => !before.has(n.id) && /Milk/.test(n.content || '')),
    Boolean, 'the note the quick bar created');
  assert.equal(created.title, `Groceries ${size}`);
  assert.equal(created.content, '- [ ] Milk');
  assert.equal(created.note_type, 'checklist', 'the type follows the content');
  await closeWindow(page);
  const card = page.locator(`.note-card[data-note-id="${created.id}"]`);
  assert.equal(await card.locator('.note-card-title').textContent(), `Groceries ${size}`);
  assert.equal(await card.locator('.note-checkbox').count(), 1, 'the card shows the task');
  await context.close();
  await api.delete(`/api/notes/${created.id}`);
}

/** Select all picks only my notes: a note shared with me stays unselected, and the count says so. */
/** The colour ring is the owner's: my red note has one, a red note shared with me does not. */
/** The link menu with the caret in plain text: the URL goes in as a link and is saved; a refused scheme is marked invalid and inserts nothing. */
async function checkLinkInsert({ browser, owner, size }) {
  const api = owner.api;
  const note = await (await api.post('/api/notes', { data: { title: `Link ${size}`, content: 'Link here' } })).json();
  const { context, page } = await openNotes(browser, owner, size);
  await openById(page, note.id);
  const body = page.locator(`${WINDOW} .note-window-body`);
  await body.evaluate(el => {
    const p = el.querySelector('p');
    const range = document.createRange(); range.selectNodeContents(p); range.collapse(false);
    getSelection().removeAllRanges(); getSelection().addRange(range); el.focus();
  });
  await page.keyboard.type(' ');
  const menu = async () => {
    await page.locator(`${WINDOW} .note-window-toolbar [data-md="link"]`).click();
    await page.locator('.note-link-menu .note-link-input').waitFor();
  };
  await menu();
  await page.locator('.note-link-input').fill('javascript:alert(1)');
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('.note-link-input.invalid').count(), 1, 'a refused scheme is marked invalid');
  await page.keyboard.press('Escape');
  await page.locator('.note-link-menu').waitFor({ state: 'detached' });
  await menu();
  await page.locator('.note-link-input').fill('https://example.com/qa');
  await page.keyboard.press('Enter');
  await body.locator('a[href="https://example.com/qa"]').waitFor();
  assert.equal(await body.locator('a[href^="javascript"]').count(), 0);
  await untilNote(api, note.id, n => n?.content.includes('example.com/qa'), 'the inserted link');
  assert.match((await (await api.get(`/api/notes/${note.id}`)).json()).content, /^Link here https:\/\/example\.com\/qa$/);
  await closeWindow(page);
  await context.close();
  await api.delete(`/api/notes/${note.id}`);
}

export async function checkWindowUi({ browser, owner, size }) {
  await checkLinkInsert({ browser, owner, size });
  await checkQuickAddPhoto({ browser, owner, size });
  await checkQuickAddChecklist({ browser, owner, size });
  await checkTouchTargets({ browser, owner, size });
  await checkWindowStyles({ browser, owner, size });
  await checkNewTaskName({ browser, owner, size });
  await checkToolbar({ browser, owner, size });
  await checkFocusReturns({ browser, owner, size });
  await checkCardKeyboard({ browser, owner, size });
}
