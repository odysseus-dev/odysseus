// streamingRenderer.js
//
// The DOM shell for incremental streaming markdown rendering. One instance owns
// the DOM of one streaming assistant message and is the only thing that writes to
// it while it streams.
//
// It keeps the message as two regions, separated by an invisible comment marker so
// the rendered blocks are direct children of the container (no wrapper elements to
// disturb CSS):
//
//     [ finalized block, frozen ][ finalized block, frozen ] <!--tail--> [ live tail ]
//
//   - Finalized blocks are rendered once and never touched again — so code-block
//     hover buttons can't flicker and code is highlighted exactly once.
//   - The live tail (the still-growing trailing block) is re-rendered each token,
//     except an open code fence, which streams in append-mode (text appended to a
//     stable <pre>, highlighted once when it closes).
//
// All the "is this safe to freeze?" logic lives in the pure segmenter; this file
// is deliberately mechanical. If anything throws, it latches into a full-re-render
// fallback so a bug can never produce broken output — only today's behavior.

import { splitFinalized, describeOpenFence } from './streamingSegmenter.js';

// Compile-time escape hatch: set to false to force the plain full-re-render path.
// (The per-instance try/catch `degraded` fallback below is the runtime safety net.)
const ENABLED = true;

export function createStreamRenderer(contentEl, { render, hljs } = {}) {
  let started = false;
  let tailMarker = null; // finalized nodes precede it; live-tail nodes follow it
  let committedLen = 0; // chars of source already frozen
  let lastText = ''; // most recent full text (for finalize)
  let tailShownLen = 0; // rendered-text length of the live tail (drives token fade)
  let appendMode = null; // { codeText: Text, appendedLen } while an open fence streams
  let degraded = !ENABLED; // true once we fall back to full re-render

  function start() {
    contentEl.textContent = '';
    tailMarker = document.createComment('tail');
    contentEl.appendChild(tailMarker);
    started = true;
  }

  function highlight(root) {
    if (hljs) root.querySelectorAll('pre code').forEach((b) => hljs.highlightElement(b));
  }

  function clearTail() {
    while (tailMarker.nextSibling) tailMarker.nextSibling.remove();
  }

  // Render `src` and freeze the nodes before the tail marker. Highlighting happens
  // here, once, on the detached fragment before the nodes are ever shown.
  function freeze(src) {
    const holder = document.createElement('div');
    holder.innerHTML = render(src);
    highlight(holder);
    while (holder.firstChild) contentEl.insertBefore(holder.firstChild, tailMarker);
  }

  // Re-render the live tail. An open trailing fence streams in append-mode.
  function renderTail(tailText) {
    const fence = tailText ? describeOpenFence(tailText) : null;
    if (fence) {
      appendOpenFence(tailText, fence);
      return;
    }
    appendMode = null;
    clearTail();
    if (!tailText) {
      tailShownLen = 0;
      return;
    }
    const holder = document.createElement('div');
    holder.innerHTML = render(tailText);
    fadeNewText(holder, tailShownLen);
    tailShownLen = holder.textContent.length;
    while (holder.firstChild) contentEl.appendChild(holder.firstChild);
  }

  // Stream the body of an unterminated code fence by appending only the new
  // characters to a stable <pre><code> text node — no re-parse, no re-highlight.
  function appendOpenFence(tailText, fence) {
    if (!appendMode) {
      clearTail();
      const pre = document.createElement('pre');
      const code = document.createElement('code');
      if (fence.lang) code.className = `language-${fence.lang}`;
      const textNode = document.createTextNode('');
      code.appendChild(textNode);
      pre.appendChild(code);
      contentEl.appendChild(pre);
      appendMode = { codeText: textNode, appendedLen: 0 };
      tailShownLen = 0; // code is never faded; prose after the fence fades fresh
    }
    const code = tailText.slice(fence.contentStart);
    if (code.length > appendMode.appendedLen) {
      appendMode.codeText.appendData(code.slice(appendMode.appendedLen));
      appendMode.appendedLen = code.length;
    }
  }

  // Wrap tail text past `prevLen` characters in <span class="token-new"> for the
  // streaming fade-in. Skips code (<pre>) and thinking blocks (.thinking-content).
  // Note: the original chat.js helper checked `.think-content`, a class that exists
  // nowhere in the app, so thinking text used to fade; matching the real
  // `.thinking-content` corrects that. Operates on the detached fragment before insertion.
  function fadeNewText(container, prevLen) {
    if (!prevLen) return;
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    let count = 0;
    const toWrap = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      const len = node.textContent.length;
      if (count + len <= prevLen) {
        count += len;
        continue;
      }
      toWrap.push({ node, splitAt: count < prevLen ? prevLen - count : 0 });
      count += len;
    }
    for (const { node, splitAt } of toWrap) {
      const parent = node.parentNode;
      if (!parent || parent.closest('pre, .thinking-content')) continue;
      const target = splitAt > 0 ? node.splitText(splitAt) : node;
      const span = document.createElement('span');
      span.className = 'token-new';
      parent.replaceChild(span, target);
      span.appendChild(target);
    }
  }

  function fullRender(fullText) {
    contentEl.innerHTML = render(fullText);
    highlight(contentEl);
  }

  // Render the latest full source text.
  //
  // PRECONDITION: callers must pass append-only text — each call's `fullText` must
  // extend the previous one with the already-seen prefix UNCHANGED. Finalized
  // blocks are frozen and never re-rendered, so a feed that rewrites earlier text
  // would leave stale frozen blocks (corrected only by the next full re-render).
  // chat.js satisfies this: its stripToolBlocks output only strips not-yet-finalized
  // trailing tool syntax, never text that has already been frozen.
  function update(fullText) {
    lastText = fullText;
    if (degraded) {
      fullRender(fullText);
      return;
    }
    try {
      // Self-heal: if our DOM was replaced out from under us — chat.js writes
      // contentEl.innerHTML directly for thinking indicators and tool blocks, and
      // finalize() removes the marker — our tail marker is no longer a child of the
      // container. Rebuild from scratch so we never append onto foreign content or
      // touch a detached marker.
      if (started && (!tailMarker || tailMarker.parentNode !== contentEl)) {
        started = false;
        committedLen = 0;
        tailShownLen = 0;
        appendMode = null;
      }
      if (!started) start();
      const next = splitFinalized(fullText, render, committedLen);
      if (next > committedLen) {
        freeze(fullText.slice(committedLen, next));
        committedLen = next;
        appendMode = null; // whatever was streaming is now frozen
        tailShownLen = 0;
      }
      renderTail(fullText.slice(committedLen));
    } catch (err) {
      degraded = true;
      console.error('streamingRenderer: falling back to full render', err);
      fullRender(fullText);
    }
  }

  // Stream finished: freeze whatever is left canonically and flatten away the
  // marker so the container holds exactly what a single full render would produce.
  // chat.js currently re-renders the finished message from source for its own
  // reasons and so doesn't call this, but it completes the renderer's lifecycle and
  // is exercised by the tests.
  function finalize() {
    if (degraded) return;
    try {
      if (!started) start();
      clearTail();
      appendMode = null;
      const rest = lastText.slice(committedLen);
      if (rest.trim()) freeze(rest);
      tailMarker.remove();
      tailMarker = null;
      committedLen = lastText.length;
    } catch (err) {
      degraded = true;
      console.error('streamingRenderer: falling back to full render', err);
      fullRender(lastText);
    }
  }

  return { update, finalize };
}
