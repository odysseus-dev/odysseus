// Own the final answer within one live turn. Transport and Markdown parsing
// stay with the caller; neither accumulated text nor tool names prove that an
// answer reached the screen. Only the answer DOM can establish that.
const AUXILIARY = '.thinking-section, .sources-section, .rag-sources, .msg-footer, .code-copy-btn';
const STRUCTURE = 'ul, ol, li, table, tr, th, td, pre, code, h1, h2, h3, h4, h5, h6, blockquote, strong, em';
const EMPTY = '["",[],[]]';
const normalize = text => text.replace(/\s+/g, ' ').trim();

export function startsContinuationRound(event) {
  if (!event || event.type !== 'agent_step' || event.stage) return false;
  const round = Number(event.round);
  // Only an explicit later round needs a new bubble. Status-only steps (such
  // as task classification) and round 1 belong to the processing bubble;
  // replacing it replays its entrance animation just before the first token.
  return Number.isInteger(round) && round > 1;
}

function signature(root, live) {
  const doc = root.ownerDocument;
  const view = doc.defaultView;
  function included(el) {
    if (el.closest(AUXILIARY)) return false;
    for (let parent = el; parent; parent = parent.parentElement) {
      if (parent.hidden || parent.getAttribute('aria-hidden') === 'true') return false;
      if (parent.tagName === 'DETAILS' && !parent.open && !parent.querySelector('summary')?.contains(el)) return false;
      if (live) {
        const style = view.getComputedStyle(parent);
        if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse' || style.opacity === '0') return false;
      }
    }
    return !live || (el.isConnected && el.getClientRects().length > 0);
  }
  let text = '';
  const walker = doc.createTreeWalker(root, 4); // SHOW_TEXT
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (!included(node.parentElement)) continue;
    if (live && node.textContent.trim()) {
      const range = doc.createRange();
      range.selectNodeContents(node);
      if (!range.getClientRects().length) continue;
    }
    text += node.textContent;
  }
  const targets = [...root.querySelectorAll('a[href], img[src]')]
    .filter(included)
    .map(el => [el.tagName, el.getAttribute('href') || el.getAttribute('src'), normalize(el.textContent), el.getAttribute('alt') || '']);
  const structure = [...root.querySelectorAll(STRUCTURE)].filter(included).map(el => el.tagName);
  return JSON.stringify([normalize(text), targets, structure]);
}

export function createTurnRendering({ root, start }) {
  let renderOwner = 'streamed';
  let structuredBody = null;

  // Unscoped prose cannot override a structured answer. The bridge may
  // explicitly restart streamed synthesis after an intermediate snapshot.
  function accepts(event) {
    if (start.parentNode !== root) return false;
    if (event.render_owner === 'streamed' && event.replacement_scope === 'turn'
        && (event.type === 'final_response' || (event.delta && !event.thinking))) {
      renderOwner = 'streamed';
      structuredBody = null;
    }
    if (event.type === 'final_response') {
      const incoming = event.render_owner || 'streamed';
      if (!['streamed', 'structured'].includes(incoming)) return false;
      if (renderOwner === 'structured' && incoming !== 'structured') return false;
      renderOwner = incoming;
      return true;
    }
    return renderOwner === 'streamed' && (!event.render_owner || event.render_owner === 'streamed');
  }

  function elements() {
    if (start.parentNode !== root) return [];
    const result = [];
    for (let node = start.nextSibling; node; node = node.nextSibling) {
      if (node.nodeType === 8 && /^(live|canonical)-stream-turn$/.test(node.data)) break;
      if (node.nodeType !== 1) continue;
      if (node.matches('.msg-user')) break;
      result.push(node);
    }
    return result;
  }

  function bodies() {
    return elements().filter(node => node.matches('.msg-ai')).map(node => node.querySelector('.body')).filter(Boolean);
  }

  function removeAnswer(body) {
    const retained = [...body.querySelectorAll('.thinking-section, .sources-section, .rag-sources')]
      .filter(el => !el.parentElement.closest(AUXILIARY));
    const holder = body.closest('.msg-ai');
    if (retained.length) {
      body.replaceChildren(...retained);
      holder.dataset.raw = '';
      holder.querySelector('.msg-footer')?.remove();
    } else {
      holder.remove();
    }
  }

  function settle() {
    const candidates = bodies();
    if (renderOwner === 'structured' && candidates.includes(structuredBody)) {
      candidates.filter(body => body !== structuredBody).forEach(removeAnswer);
    }
    for (const element of elements()) {
      element.classList.remove('streaming');
      element.querySelectorAll('.streaming').forEach(node => node.classList.remove('streaming'));
    }
  }

  function expected(html) {
    const fragment = root.ownerDocument.createElement('div');
    fragment.innerHTML = html;
    return signature(fragment, false);
  }

  function isVisible(body, html) {
    const wanted = expected(html);
    return wanted !== EMPTY && bodies().includes(body) && signature(body, true) === wanted;
  }

  function render({ body, html, raw, messageId, render_owner = renderOwner, replacement_scope }) {
    const candidates = bodies();
    if (!candidates.includes(body)) return { changed: false, accepted: false };
    if (!accepts({ type: 'final_response', render_owner, replacement_scope })) return { changed: false, accepted: false };
    const wanted = expected(html);
    const changed = !isVisible(body, html);
    if (changed) body.innerHTML = html;
    const holder = body.closest('.msg-ai');
    holder.dataset.raw = raw;
    if (messageId) holder.dataset.dbId = messageId;
    // A structured snapshot replaces prose across this turn, including a
    // different draft in an earlier round. Streamed ownership only deduplicates
    // exact answers. Tool evidence and thinking/sources retain their nodes.
    if (wanted !== EMPTY || replacement_scope === 'turn') {
      for (const other of candidates) {
        if (other === body || (renderOwner !== 'structured' && replacement_scope !== 'turn' && signature(other, true) !== wanted)) continue;
        removeAnswer(other);
      }
    }
    const accepted = isVisible(body, html);
    if (accepted && renderOwner === 'structured') {
      structuredBody = body;
      settle();
    }
    return { changed, accepted };
  }

  return { isVisible, render, accepts, settle };
}
