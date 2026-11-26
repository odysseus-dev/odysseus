// Agent runs started from a note, against the stub model: the per-item
// button on a card and in the window, Ask with a preset, the whole note and
// free text,
// the card's Agent tag and the window's "Agent chat" landing on the run. Every
// run is offered manage_notes (scoped server-side to its note; edits are
// covered in notesAgentEditFlow.mjs), gets the note only as fenced context, never
// as the user's turn, has a change it asks for held for approval, leaves the note's text alone
// when it was not asked to change it, and nothing agent-related shows on a
// note shared with me.
import assert from 'node:assert/strict';
import path from 'node:path';
import {
  createAccount, lastUserText, modelRequests, openNotes, refreshNotes, runItemAgent, tapOrClick, toolNames, until, untilNote,
} from './notesHarness.mjs';

const evidence = process.env.NOTES_NATIVE_EVIDENCE_DIR;
const CONTENT = 'Plan the weekend\n\n- [ ] book a cabin\n- [ ] pack boots';

/** The admin owner's only endpoint is the stub, so it is the default chat model. */
export async function useStubModel(owner) {
  const created = await owner.api.post('/api/model-endpoints', { multipart: {
    name: 'Stub', base_url: `${process.env.NOTES_STUB_ORIGIN}/v1`, pinned_models: 'stub-model', supports_tools: 'true',
  } });
  assert.equal(created.ok(), true, await created.text());
  const chat = await (await owner.api.get('/api/default-chat')).json();
  assert.equal(chat.model, 'stub-model', JSON.stringify(chat));
}

const GUARD_OPEN = '<<<UNTRUSTED_SOURCE_DATA>>>';
const textOf = content => (Array.isArray(content) ? content.map(part => part.text || '').join(' ') : String(content || '')).replace(/\r\n/g, '\n');
/** The untrusted-context blocks a request carried, joined. */
const untrustedText = body => (body.messages || []).map(m => textOf(m.content)).filter(text => text.includes(GUARD_OPEN)).join('\n');

/**
 * The first completion request whose user turn carried `prompt` and whose
 * untrusted context carried every string in `context`, once the run reached
 * the model. The user turn never carries the note itself.
 */
async function requestFor(prompt, context = []) {
  const found = await until(
    () => modelRequests().filter(body => lastUserText(body).includes(prompt) && context.every(part => untrustedText(body).includes(part))),
    list => list.length > 0, `a model request for ${JSON.stringify(prompt.slice(0, 60))}`, 20000,
  );
  const request = found[0];
  for (const part of context) assert.equal(lastUserText(request).includes(part), false, `the user turn carries ${JSON.stringify(part)}`);
  return request;
}

async function landedOn(page, sessionId, text) {
  await page.locator('#notes-pane').waitFor({ state: 'detached' });
  await page.waitForFunction(id => window.sessionModule?.getCurrentSessionId() === id, sessionId);
  await page.locator('#chat-history').getByText(text, { exact: false }).first().waitFor();
}

const reopenPanel = async page => {
  await page.evaluate(() => window.notesModule.openPanel());
  await page.locator('#notes-pane .notes-pane-body').waitFor();
};

export async function checkAgentActions({ browser, owner, admin, size }) {
  const mobile = size === 'phone';
  const title = `Weekend ${size}`;
  const note = await (await owner.api.post('/api/notes', { data: { title, content: CONTENT } })).json();
  const { context, page } = await openNotes(browser, owner, size);
  const card = page.locator(`.note-card[data-note-id="${note.id}"]`);
  await card.waitFor();
  const win = page.locator('#note-window-modal');
  const linked = () => untilNote(owner.api, note.id, n => !!n?.agent_session_id, 'the linked run');

  // Per item, on a card (hover only, so desktop). The todo and the note
  // travel as context; the user turn is the request alone.
  const ITEM = 'Help me with the focus todo of the attached note.';
  const noteContext = [`Title: ${title}`, 'Plan the weekend', '- [ ] book a cabin'];
  if (!mobile) {
    const row = card.locator('li.md-task').filter({ hasText: 'book a cabin' });
    const opacity = locator => locator.evaluate(el => Number(getComputedStyle(el).opacity));
    await page.mouse.move(0, 0);
    assert.equal(await opacity(row.locator('.note-task-agent')), 0, 'hidden until the row is hovered');
    // Hover again on each poll: a card redrawn under a still mouse is not :hover.
    await until(async () => { await row.hover(); return opacity(row.locator('.note-task-agent')); }, value => value > 0, 'the button on hover');
    // The click asks about the item first; the run starts from there.
    await row.locator('.note-task-agent').click();
    const itemMenu = page.locator('.note-ask-menu');
    await itemMenu.waitFor();
    assert.equal(await itemMenu.getAttribute('aria-label'), 'Ask about this item');
    assert.match(await itemMenu.textContent(), /book a cabin/);
    await itemMenu.locator('[data-act="solve"]').click();
    const toast = page.locator('#toast').filter({ hasText: 'Agent working in the background' });
    await toast.waitFor();
    // The toast's Open chat lands on the run, like the tag below.
    await toast.locator('.toast-action-btn').filter({ hasText: 'Open chat' }).click();
    const sid = (await linked()).agent_session_id;
    await landedOn(page, sid, ITEM);
    await reopenPanel(page);
    const request = await requestFor(ITEM, [...noteContext, 'Focus todo: book a cabin']);
    assert.equal(toolNames(request).includes('manage_notes'), true, `manage_notes not offered: ${toolNames(request)}`);
    assert.ok(toolNames(request).length > 0, 'the run is an agent run with tools');

    // The card's Agent tag lands on that run.
    await card.locator('.note-card-agent-tag').waitFor();
    if (evidence) await page.screenshot({ path: path.join(evidence, 'agent-card-desktop.png'), animations: 'disabled' });
    await card.locator('.note-card-agent-tag').click();
    await landedOn(page, sid, ITEM);
    await page.locator('#chat-history').getByText('Stub reply:').first().waitFor();
    await reopenPanel(page);
  }

  // The row buttons are not content: opening the window, putting the caret
  // in the body and closing it writes nothing.
  const writes = [];
  const recordWrite = r => { if (r.method() !== 'GET' && new URL(r.url()).pathname.startsWith('/api/notes')) writes.push(`${r.method()} ${new URL(r.url()).pathname}`); };
  page.on('request', recordWrite);
  await tapOrClick(mobile, card.locator('.note-card-title'));
  await win.locator('li.md-task .note-task-agent').first().waitFor({ state: 'attached' });
  await win.locator('.md-task-text').first().click();
  await page.keyboard.press('End');
  await page.evaluate(() => document.querySelector('#note-window-modal .modal-close').click());
  await win.waitFor({ state: 'detached' });
  await page.evaluate(() => fetch('/api/health').then(r => r.status));
  page.off('request', recordWrite);
  assert.deepEqual(writes, [], 'the row buttons never reach a save');

  // Per item, in the window: the second row.
  await tapOrClick(mobile, card.locator('.note-card-title'));
  await win.locator('.note-window-body').waitFor();
  const before = (await untilNote(owner.api, note.id, n => !!n, 'the note')).agent_session_id || null;
  const rowInWindow = win.locator('li.md-task').filter({ hasText: 'pack boots' });
  if (mobile) {
    // No hover on a touch screen: cards drop the button, the window dims it.
    assert.equal(await card.locator('.note-task-agent').evaluateAll(list => list.every(el => getComputedStyle(el).display === 'none')), true);
    assert.equal(await rowInWindow.locator('.note-task-agent').evaluate(el => getComputedStyle(el).opacity), '0.4');
  }
  if (!mobile) await rowInWindow.hover();
  await runItemAgent(mobile, rowInWindow.locator('.note-task-agent'));
  assert.equal(toolNames(await requestFor(ITEM, [...noteContext, 'Focus todo: pack boots'])).includes('manage_notes'), true);
  await untilNote(owner.api, note.id, n => n?.agent_session_id && n.agent_session_id !== before, 'the window run re-pointing the link');

  // Ask: a preset, then free text.
  const ask = win.locator('[data-act="ask"]');
  await tapOrClick(mobile, ask);
  const menu = page.locator('.note-ask-menu');
  await menu.waitFor();
  if (evidence) await page.screenshot({ path: path.join(evidence, `agent-ask-${size}.png`), animations: 'disabled' });
  await tapOrClick(mobile, menu.locator('[data-preset="1"]'));
  const summary = await requestFor('Summarize this note.\n\nThe note is attached as the selected note.', noteContext);
  assert.equal(toolNames(summary).includes('manage_notes'), true);
  // The whole note, from the top of the same popover (the card menu that also
  // offers it is hidden). The note is linked by now, so it reads Re-run.
  await tapOrClick(mobile, ask);
  const solve = menu.locator('[data-act="solve"]');
  assert.match(await solve.textContent(), /^Re-run agentthe whole note$/);
  await tapOrClick(mobile, solve);
  const whole = await requestFor('Help me get this note done.', noteContext);
  assert.equal(toolNames(whole).includes('manage_notes'), true);
  await tapOrClick(mobile, ask);
  await menu.locator('.note-ask-input').fill(`What goes with boots ${size}?`);
  await menu.locator('.note-ask-input').press('Enter');
  const free = await requestFor(`What goes with boots ${size}?\n\nThe note is attached as the selected note.`, noteContext);
  assert.equal(toolNames(free).includes('manage_notes'), true);
  // The run's context carries the owner's skills index, which keeps the tool
  // gate armed for changes (this private note alone would not arm it; see
  // notesFocusFlow.mjs): a calendar write the model asks for is held, never run.
  await tapOrClick(mobile, ask);
  await menu.locator('.note-ask-input').fill(`GATETEST boots-${size} add to my calendar`);
  await menu.locator('.note-ask-input').press('Enter');
  const gated = await requestFor(`GATETEST boots-${size}`, noteContext);
  assert.ok(toolNames(gated).includes('manage_calendar'), `manage_calendar offered: ${toolNames(gated)}`);
  // The run asks for approval instead of writing: no event appears, and if
  // the model hears back about the call it is the hold, never a success.
  const gateEvents = async () => {
    const res = await owner.api.get('/api/calendar/events?start=2030-01-01T00:00:00&end=2030-01-04T00:00:00');
    return ((await res.json()).events || []).filter(event => String(event.summary || '').startsWith('GATETEST'));
  };
  for (let i = 0; i < 10; i += 1) {
    assert.deepEqual(await gateEvents(), [], 'the held calendar write never ran');
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  for (const body of modelRequests()) {
    for (const m of (body.messages || []).filter(m => m.role === 'tool' && m.tool_call_id === 'call_gate_1')) {
      assert.match(textOf(m.content), /APPROVAL REQUIRED|BLOCKED|approval/i, textOf(m.content));
    }
  }
  // Each run re-points the note's link; the free-text run was the last one.
  const sessionNamed = async prefix => {
    const listed = await (await owner.api.get('/api/sessions')).json();
    return (Array.isArray(listed) ? listed : listed.sessions || []).find(s => String(s.name || '').startsWith(prefix))?.id;
  };
  const latest = await until(() => sessionNamed(`Agent: GATETEST boots-${size}`), Boolean, 'the Ask session');
  await untilNote(owner.api, note.id, n => n?.agent_session_id === latest, 'the link to the latest run');

  // A task typed now gets its button once it is saved.
  await page.evaluate(() => {
    const text = [...document.querySelectorAll('#note-window-modal .note-window-body li.md-task .md-task-text')].pop();
    const range = document.createRange(); range.selectNodeContents(text); range.collapse(false);
    getSelection().removeAllRanges(); getSelection().addRange(range); text.closest('[contenteditable="true"]').focus();
  });
  await page.keyboard.press('Enter');
  await page.keyboard.type(`rent a car ${size}`);
  const typed = `${CONTENT}\n- [ ] rent a car ${size}`;
  await untilNote(owner.api, note.id, n => n?.content === typed, 'the typed task');
  await win.locator('li.md-task').filter({ hasText: `rent a car ${size}` }).locator('.note-task-agent').waitFor({ state: 'attached' });

  // "Agent chat" in the footer lands on the latest run. On a phone the footer
  // steps aside while the text has focus: done typing first.
  if (mobile) await page.evaluate(() => document.activeElement?.blur());
  const agentChat = win.locator('[data-act="agent-chat"]');
  await agentChat.waitFor();
  await tapOrClick(mobile, agentChat);
  await landedOn(page, latest, `GATETEST boots-${size}`);

  // None of the runs changed the note's text: it is what was typed, byte for byte.
  assert.equal((await (await owner.api.get(`/api/notes/${note.id}`)).json()).content, typed, 'note content byte-identical after the runs');

  // A done row has no button, on the card or in the window.
  const ticked = await (await owner.api.post('/api/notes', { data: { title: `Done rows ${size}`, content: '- [x] already done\n- [ ] still open' } })).json();
  await reopenPanel(page);
  await refreshNotes(page);
  const doneCard = page.locator(`.note-card[data-note-id="${ticked.id}"]`);
  await doneCard.waitFor();
  const shown = list => list.evaluateAll(els => els.map(el => getComputedStyle(el).display !== 'none'));
  if (!mobile) {
    await doneCard.locator('li.md-task').first().hover();
    assert.deepEqual(await shown(doneCard.locator('li.md-task .note-task-agent')), [false, true], 'card: hidden on the done row only');
  }
  await tapOrClick(mobile, doneCard.locator('.note-card-title'));
  await win.locator('li.md-task .note-task-agent').first().waitFor({ state: 'attached' });
  assert.deepEqual(await shown(win.locator('li.md-task .note-task-agent')), [false, true], 'window: hidden on the done row only');
  await page.evaluate(() => document.querySelector('#note-window-modal .modal-close')?.click());
  await win.waitFor({ state: 'detached' });
  await owner.api.delete(`/api/notes/${ticked.id}`);

  // A note shared with me: no row buttons, no tag, no Ask, no Agent chat.
  const friend = await createAccount(admin, `agent-friend-${size}`);
  const theirs = await (await friend.api.post('/api/notes', { data: { title: `Theirs ${size}`, content: '- [ ] their task' } })).json();
  const grant = await friend.api.post(`/api/notes/${theirs.id}/share`, { data: { users: [owner.username], permission: 'edit' } });
  assert.equal(grant.ok(), true, await grant.text());
  await reopenPanel(page);
  await refreshNotes(page);
  const shared = page.locator(`.note-card[data-note-id="${theirs.id}"]`);
  await shared.waitFor();
  if (!mobile) await shared.locator('li.md-task').hover();
  assert.equal(await shared.locator('.note-task-agent, .note-card-agent-tag').count(), 0, 'no agent affordance on a shared card');
  await tapOrClick(mobile, shared.locator('.note-card-title'));
  await win.locator('.note-window-body').waitFor();
  assert.equal(await win.locator('.note-task-agent').count(), 0);
  assert.equal(await win.locator('[data-act="ask"]:not(.hidden), [data-act="agent-chat"]:not(.hidden)').count(), 0);
  await page.evaluate(() => document.querySelector('#note-window-modal .modal-close')?.click());
  await context.close();
  await friend.api.delete(`/api/notes/${theirs.id}`);
}
