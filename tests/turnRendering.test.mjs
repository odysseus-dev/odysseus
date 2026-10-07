import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';

let browser;
const source = await readFile(new URL('../static/js/turnRendering.js', import.meta.url), 'utf8');
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { await browser?.close(); });

async function run(html, action) {
  const page = await browser.newPage();
  try {
    await page.setContent(`<style>.agent-thread-content { display:none } .agent-thread-node.open .agent-thread-content { display:block }</style><main id="history">${html}</main>`);
    await page.evaluate(async source => {
      const { createTurnRendering, startsContinuationRound } = await import('data:text/javascript;base64,' + btoa(source));
      window.createTurnRendering = createTurnRendering;
      window.startsContinuationRound = startsContinuationRound;
      const root = document.querySelector('#history');
      const start = document.createComment('live-stream-turn');
      root.prepend(start);
      window.owner = createTurnRendering({ root, start });
    }, source);
    return await page.evaluate(action);
  } finally { await page.close(); }
}

test('round one reuses the initial bubble and later rounds start continuations', async () => {
  const result = await run('<div class="msg-ai"><div class="body"></div></div>', () => ({
    first: startsContinuationRound({ type: 'agent_step', round: 1 }),
    second: startsContinuationRound({ type: 'agent_step', round: 2 }),
    nonStep: startsContinuationRound({ type: 'tool_start', round: 2 }),
    classification: startsContinuationRound({ type: 'agent_step', stage: 'email_task_scope' }),
    providerStatus: startsContinuationRound({ type: 'agent_step', stage: 'provider_request', round: 2 }),
    noRound: startsContinuationRound({ type: 'agent_step' }),
    invalidRound: startsContinuationRound({ type: 'agent_step', round: 'unknown' }),
  }));
  assert.deepEqual(result, {
    first: false, second: true, nonStep: false, classification: false,
    providerStatus: false, noRound: false, invalidRound: false,
  });
});

test('plain streamed prose preserves node identity, selection and focus', async () => {
  const result = await run('<div class="msg-ai"><div class="body"><p>Hello <strong>world</strong>.</p></div><button>Copy</button></div>', () => {
    const body = document.querySelector('.body');
    const strong = body.querySelector('strong');
    const button = document.querySelector('button');
    button.focus();
    const selection = getSelection();
    selection.selectAllChildren(strong);
    const rendered = owner.render({ body, html: '<p>Hello <strong>world</strong>.</p>', raw: 'Hello **world**.', messageId: '42' });
    return { ...rendered, same: strong === body.querySelector('strong'), selection: selection.toString(), focused: document.activeElement === button, id: body.parentElement.dataset.dbId };
  });
  assert.deepEqual(result, { accepted: true, changed: false, same: true, selection: 'world', focused: true, id: '42' });
});

test('matching raw accumulator does not excuse an empty visible answer', async () => {
  const result = await run('<div class="msg-ai" data-raw="Notes"><div class="body"></div></div>', () => {
    const body = document.querySelector('.body');
    const rendered = owner.render({ body, html: '<p>Notes</p>', raw: 'Notes' });
    return { ...rendered, visible: body.innerText };
  });
  assert.deepEqual(result, { accepted: true, changed: true, visible: 'Notes' });
});

test('hidden or collapsed note text is not evidence of a visible answer', async () => {
  for (const content of ['<span hidden>Notes</span>', '<span style="visibility:hidden">Notes</span>', '<span style="opacity:0">Notes</span>', '<details><summary>Output</summary><p>Notes</p></details>']) {
    const result = await run(`<div class="msg-ai"><div class="body">${content}</div></div>`, () => {
      const body = document.querySelector('.body');
      const before = owner.isVisible(body, '<p>Notes</p>');
      owner.render({ body, html: '<p>Notes</p>', raw: 'Notes' });
      return { before, after: owner.isVisible(body, '<p>Notes</p>'), visible: body.innerText };
    });
    assert.deepEqual(result, { before: false, after: true, visible: 'Notes' });
  }
});

test('structured note list renders once across final response and saved completion', async () => {
  const result = await run('<div class="agent-thread"><div class="agent-thread-node open"><button>Notes</button><div class="agent-thread-content">Raw evidence</div></div></div><div class="msg-ai"><div class="body">Draft</div></div>', () => {
    const body = document.querySelector('.body');
    const thread = document.querySelector('.agent-thread');
    const button = thread.querySelector('button');
    button.focus();
    const html = '<ul><li><a href="#note-1">First</a></li><li><a href="#note-2">Second</a></li></ul>';
    const first = owner.render({ body, html, raw: 'canonical notes' });
    const list = body.firstChild;
    const second = owner.render({ body, html, raw: 'canonical notes', messageId: '99' });
    return { first: first.changed, second: second.changed, same: list === body.firstChild, count: body.querySelectorAll('li').length, thread: thread === document.querySelector('.agent-thread'), open: thread.querySelector('.agent-thread-node').classList.contains('open'), focus: document.activeElement === button, visible: body.innerText };
  });
  assert.equal(result.first, true);
  assert.equal(result.second, false);
  assert.equal(result.same, true);
  assert.equal(result.count, 2);
  assert.equal(result.thread, true);
  assert.equal(result.open, true);
  assert.equal(result.focus, true);
  assert.match(result.visible, /First[\s\S]*Second/);
});

test('same titles with missing or wrong note links must be repaired', async () => {
  for (const content of ['<p>First</p>', '<p><a href="#note-wrong">First</a></p>']) {
    const result = await run(`<div class="msg-ai"><div class="body">${content}</div></div>`, () => {
      const body = document.querySelector('.body');
      const result = owner.render({ body, html: '<p><a href="#note-1">First</a></p>', raw: '[First](#note-1)' });
      return { ...result, href: body.querySelector('a').getAttribute('href') };
    });
    assert.deepEqual(result, { changed: true, accepted: true, href: '#note-1' });
  }
});

test('structured list must have list semantics, not just matching title text', async () => {
  const result = await run('<div class="msg-ai"><div class="body"><a href="#note-1">First</a></div></div>', () => {
    const body = document.querySelector('.body');
    const rendered = owner.render({ body, html: '<ul><li><a href="#note-1">First</a></li></ul>', raw: '- [First](#note-1)' });
    return { ...rendered, items: body.querySelectorAll('ul > li').length };
  });
  assert.deepEqual(result, { changed: true, accepted: true, items: 1 });
});

test('a hidden turn cannot claim that its canonical answer is visible', async () => {
  const result = await run('<div class="msg-ai" hidden><div class="body">Notes</div></div>', () => {
    const body = document.querySelector('.body');
    return owner.render({ body, html: '<p>Notes</p>', raw: 'Notes' });
  });
  assert.deepEqual(result, { changed: true, accepted: false });
});

test('deduplicates answer bodies only, preserving preamble and tool evidence', async () => {
  const result = await run('<div class="msg-ai"><div class="body">Looking up notes.</div></div><div class="msg-ai"><div class="body"><p>Notes</p></div></div><div class="agent-thread"><details open><summary>Output</summary>Notes</details></div><div class="msg-ai"><div class="body" id="final"></div></div>', () => {
    owner.render({ body: document.querySelector('#final'), html: '<p>Notes</p>', raw: 'Notes' });
    return { answers: [...document.querySelectorAll('.body')].map(el => el.innerText), evidence: document.querySelector('details').open };
  });
  assert.deepEqual(result, { answers: ['Looking up notes.', 'Notes'], evidence: true });
});

test('completion never consumes the next user turn or another stream', async () => {
  const result = await run('<div class="msg-ai"><div class="body" id="own">Notes</div></div><div class="msg-user">Next question</div><div class="msg-ai"><div class="body" id="next">Notes</div></div>', () => {
    owner.render({ body: document.querySelector('#own'), html: '<p>Notes</p>', raw: 'Notes' });
    const rejected = owner.render({ body: document.querySelector('#next'), html: '<p>Overwrite</p>', raw: 'Overwrite' });
    return { ...rejected, next: document.querySelector('#next').innerText };
  });
  assert.deepEqual(result, { changed: false, accepted: false, next: 'Notes' });
});

test('deduplication retains an earlier round’s thinking node without a second answer', async () => {
  const result = await run('<div class="msg-ai"><div class="body"><div class="thinking-section">Reasoning</div><p>Notes</p></div></div><div class="msg-ai"><div class="body" id="final"></div></div>', () => {
    const thinking = document.querySelector('.thinking-section');
    owner.render({ body: document.querySelector('#final'), html: '<p>Notes</p>', raw: 'Notes' });
    return { sameThinking: thinking === document.querySelector('.thinking-section'), answers: [...document.querySelectorAll('.body p')].map(el => el.innerText) };
  });
  assert.deepEqual(result, { sameThinking: true, answers: ['Notes'] });
});

test('detached turn rejects late saved responses', async () => {
  const result = await run('<div class="msg-ai"><div class="body">Original</div></div>', () => {
    const body = document.querySelector('.body');
    document.querySelector('#history').firstChild.remove();
    const result = owner.render({ body, html: '<p>Stale</p>', raw: 'Stale' });
    return { ...result, text: body.innerText };
  });
  assert.deepEqual(result, { changed: false, accepted: false, text: 'Original' });
});

test('a changed saved answer patches only the answer and preserves viewport anchor', async () => {
  const result = await run('<div style="height:1800px">Earlier history</div><div class="agent-thread">Tools</div><div class="msg-ai"><div class="body">Wrong draft</div></div>', () => {
    scrollTo(0, 200);
    const scroll = scrollY;
    const thread = document.querySelector('.agent-thread');
    const body = document.querySelector('.body');
    owner.render({ body, html: '<p>Corrected answer</p>', raw: 'Corrected answer' });
    return { sameThread: thread === document.querySelector('.agent-thread'), sameBody: body === document.querySelector('.body'), sameScroll: scrollY === scroll, visible: body.innerText };
  });
  assert.deepEqual(result, { sameThread: true, sameBody: true, sameScroll: true, visible: 'Corrected answer' });
});

test('structured ownership replaces all turn prose but preserves other turns and tool nodes', async () => {
  const result = await run('<div class="msg-ai" id="previous"><div class="body">Previous turn</div></div><div class="msg-ai" id="draft"><div class="body"><div class="thinking-section">Reasoning</div><p>Draft list</p></div></div><div class="agent-thread">Tool evidence</div><div class="msg-ai"><div class="body" id="answer">Different draft</div></div><div class="msg-user">Next question</div><div class="msg-ai" id="next"><div class="body">Next answer</div></div>', () => {
    const root = document.querySelector('#history');
    root.insertBefore(root.firstChild, document.querySelector('#draft'));
    const thread = document.querySelector('.agent-thread');
    const thinking = document.querySelector('.thinking-section');
    const body = document.querySelector('#answer');
    const event = { type: 'final_response', render_owner: 'structured' };
    const accepted = owner.accepts(event);
    owner.render({ body, html: '<ul><li>Canonical notes</li></ul>', raw: 'Canonical notes', render_owner: event.render_owner });
    return { accepted, text: root.innerText, sameThread: thread === document.querySelector('.agent-thread'), sameThinking: thinking === document.querySelector('.thinking-section'), visible: owner.isVisible(body, '<ul><li>Canonical notes</li></ul>') };
  });
  assert.equal(result.accepted, true);
  assert.equal(result.visible, true);
  assert.equal(result.sameThread, true);
  assert.equal(result.sameThinking, true);
  assert.doesNotMatch(result.text, /Draft list|Different draft/);
  assert.match(result.text, /Previous turn[\s\S]*Canonical notes[\s\S]*Next answer/);
});

test('structured ownership rejects late prose regardless of delta owner and streamed final conflicts', async () => {
  const result = await run('<div class="msg-ai"><div class="body"></div></div>', () => {
    const body = document.querySelector('.body');
    owner.render({ body, html: '<p>Canonical</p>', raw: 'Canonical', render_owner: 'structured' });
    const paragraph = body.firstChild;
    const events = [
      { delta: 'Late prose', render_owner: 'streamed' },
      { delta: 'Late prose', render_owner: 'structured' },
      { delta: 'Legacy prose' },
      { type: 'final_response', content: 'Conflicting answer', render_owner: 'streamed' },
      { type: 'final_response', content: 'Legacy answer' },
      { type: 'agent_step', round: 2 },
      { type: 'teacher_takeover' },
    ];
    const decisions = events.map(event => {
      const accepted = owner.accepts(event);
      if (accepted) body.append(event.delta || event.content || 'New round');
      return accepted;
    });
    const conflictingRender = owner.render({ body, html: '<p>Overwrite</p>', raw: 'Overwrite', render_owner: 'streamed' });
    return { decisions, conflictingRender, same: paragraph === body.firstChild, text: body.innerText };
  });
  assert.deepEqual(result, { decisions: Array(7).fill(false), conflictingRender: { changed: false, accepted: false }, same: true, text: 'Canonical' });
});

test('repeated structured snapshots are idempotent and a newer snapshot can correct the answer', async () => {
  const result = await run('<div class="msg-ai"><div class="body">Draft</div></div>', () => {
    const body = document.querySelector('.body');
    const first = { body, html: '<p>Canonical</p>', raw: 'Canonical', render_owner: 'structured' };
    owner.render(first);
    const paragraph = body.firstChild;
    const repeated = owner.render(first);
    const same = paragraph === body.firstChild;
    const corrected = owner.render({ ...first, html: '<p>Corrected</p>', raw: 'Corrected' });
    const saved = owner.render({ body, html: '<p>Corrected</p>', raw: 'Corrected', messageId: '42' });
    return { repeated, same, corrected, saved, text: body.innerText, id: body.parentElement.dataset.dbId };
  });
  assert.deepEqual(result, { repeated: { changed: false, accepted: true }, same: true, corrected: { changed: true, accepted: true }, saved: { changed: false, accepted: true }, text: 'Corrected', id: '42' });
});

test('streamed ownership preserves distinct rounds and structured ownership does not leak to another turn', async () => {
  const result = await run('<div class="msg-ai"><div class="body">Preamble</div></div><div class="msg-ai"><div class="body" id="answer">Answer</div></div>', () => {
    const root = document.querySelector('#history');
    const body = document.querySelector('#answer');
    const streamed = owner.accepts({ delta: 'Answer', render_owner: 'streamed' });
    owner.render({ body, html: '<p>Answer</p>', raw: 'Answer', render_owner: 'streamed' });
    const preamble = root.innerText.includes('Preamble');
    owner.render({ body, html: '<p>Canonical</p>', raw: 'Canonical', render_owner: 'structured' });
    const start = document.createComment('live-stream-turn');
    root.append(start);
    const next = createTurnRendering({ root, start });
    return { streamed, preamble, old: owner.accepts({ delta: 'old' }), next: next.accepts({ delta: 'New turn', render_owner: 'streamed' }) };
  });
  assert.deepEqual(result, { streamed: true, preamble: true, old: false, next: true });
});

test('explicit turn replacement resumes legitimate synthesis after an intermediate structured answer', async () => {
  const result = await run('<div class="agent-thread">Tools</div><div class="msg-ai"><div class="body"></div></div>', () => {
    const body = document.querySelector('.body');
    const thread = document.querySelector('.agent-thread');
    owner.render({ body, html: '<p>Intermediate notes</p>', raw: 'Intermediate notes', render_owner: 'structured' });
    const first = { delta: 'Synthesis ', render_owner: 'streamed', replacement_scope: 'turn' };
    const resumed = owner.accepts(first);
    if (resumed) {
      owner.render({ body, html: '', raw: '', render_owner: first.render_owner, replacement_scope: first.replacement_scope });
      body.append(first.delta);
    }
    const second = { delta: 'continues', render_owner: 'streamed' };
    const appended = owner.accepts(second);
    if (appended) body.append(second.delta);
    return { resumed, appended, text: body.innerText, sameThread: thread === document.querySelector('.agent-thread') };
  });
  assert.deepEqual(result, { resumed: true, appended: true, text: 'Synthesis continues', sameThread: true });
});

test('corrected research replaces earlier prose while preserving tools and markdown', async () => {
  const result = await run('<div class="msg-ai"><div class="body">Unfinished news draft</div></div><div class="agent-thread">Search activity</div><div class="msg-ai"><div class="body" id="answer">Corrected answer</div></div>', () => {
    const body = document.querySelector('#answer');
    const thread = document.querySelector('.agent-thread');
    owner.render({body, html: '<h3>Politics</h3><p><strong>Cabinet changes</strong>: supported details.</p><p><a href="https://example.org">Original report</a></p>', raw: '### Politics\n**Cabinet changes**: supported details.', render_owner: 'streamed', replacement_scope: 'turn'});
    return {bubbles: document.querySelectorAll('.msg-ai').length, sameThread: thread === document.querySelector('.agent-thread'), heading: body.querySelector('h3')?.textContent, bold: body.querySelector('strong')?.textContent, link: body.querySelector('a')?.getAttribute('href'), oldDraft: document.body.textContent.includes('Unfinished news draft')};
  });
  assert.deepEqual(result, {bubbles: 1, sameThread: true, heading: 'Politics', bold: 'Cabinet changes', link: 'https://example.org', oldDraft: false});
});

test('a scoped streamed final can replace structured content but thinking cannot transfer ownership', async () => {
  const result = await run('<div class="msg-ai"><div class="body"></div></div>', () => {
    const body = document.querySelector('.body');
    owner.render({ body, html: '<p>Notes</p>', raw: 'Notes', render_owner: 'structured' });
    const thinking = owner.accepts({ delta: 'Reasoning', thinking: true, render_owner: 'streamed', replacement_scope: 'turn' });
    const rendered = owner.render({ body, html: '<p>Synthesis</p>', raw: 'Synthesis', render_owner: 'streamed', replacement_scope: 'turn' });
    return { thinking, rendered, text: body.innerText };
  });
  assert.deepEqual(result, { thinking: false, rendered: { changed: true, accepted: true }, text: 'Synthesis' });
});

test('settling ordinary prose clears every turn marker without replacing streamed nodes or touching the next turn', async () => {
  const result = await run('<div class="msg-ai streaming"><div class="body"><p>First round</p><span class="streaming">tail</span></div></div><div class="agent-thread streaming">Tools</div><div class="msg-ai streaming"><div class="body"><p>Final round</p></div></div><div class="msg-user">Next turn</div><div class="msg-ai streaming" id="next">Still streaming</div>', () => {
    const paragraphs = [...document.querySelectorAll('.body p')];
    const thread = document.querySelector('.agent-thread');
    owner.settle();
    owner.settle();
    return {
      same: paragraphs.every(p => p.isConnected), sameThread: thread === document.querySelector('.agent-thread'),
      remaining: [...document.querySelectorAll('.streaming')].map(node => node.id),
      prose: paragraphs.map(p => p.innerText),
    };
  });
  assert.deepEqual(result, { same: true, sameThread: true, remaining: ['next'], prose: ['First round', 'Final round'] });
});

test('structured stable render removes hidden drafts and streaming markers even when canonical DOM already matches', async () => {
  const result = await run('<div class="msg-ai streaming" hidden><div class="body">Hidden draft</div></div><div class="msg-ai streaming"><div class="body"><div class="thinking-section streaming">Reasoning</div><p>Old draft</p></div></div><div class="agent-thread streaming">Tools</div><div class="msg-ai streaming"><div class="body" id="final"><p>Canonical</p></div></div>', () => {
    const body = document.querySelector('#final');
    const paragraph = body.firstChild;
    const rendered = owner.render({ body, html: '<p>Canonical</p>', raw: 'Canonical', render_owner: 'structured' });
    return { rendered, same: paragraph === body.firstChild, markers: document.querySelectorAll('.streaming').length, text: document.querySelector('#history').textContent };
  });
  assert.deepEqual(result, { rendered: { changed: false, accepted: true }, same: true, markers: 0, text: 'ReasoningToolsCanonical' });
});

test('terminal cleanup removes transient rounds introduced after a structured snapshot', async () => {
  const result = await run('<div class="msg-ai"><div class="body">Draft</div></div>', () => {
    const body = document.querySelector('.body');
    owner.render({ body, html: '<p>Canonical</p>', raw: 'Canonical', render_owner: 'structured' });
    const canonical = body.firstChild;
    const transient = document.createElement('div');
    transient.className = 'msg msg-ai streaming';
    transient.innerHTML = '<div class="body">Late transient draft</div>';
    document.querySelector('#history').append(transient);
    owner.settle();
    return { same: canonical === body.firstChild, transient: transient.isConnected, markers: document.querySelectorAll('.streaming').length };
  });
  assert.deepEqual(result, { same: true, transient: false, markers: 0 });
});
