/** The note window's editable body: rendered markdown edited in place, a formatting toolbar, and a raw markdown source mode. */
import { serializeNodes, joinSegments, splitMarkdownBlocks, sourceNeedsMarkdownMode, editLosesSource } from '../domain/markdownSerializer.js';
import { MAX_INDENT, toggleTask } from '../domain/markdownTasks.js';

const LOCKED_HINT = 'This part can only be edited in Markdown source';
const BLOCK_TAGS = /^(P|DIV|H[1-6]|LI|PRE|BLOCKQUOTE)$/;
// Blocks a line of text cannot be joined into (see backspaceUnderFixedBlock).
const FIXED_BLOCKS = /^(PRE|TABLE)$/;
const ICONS = {
  ul: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><circle cx="3.5" cy="6" r="1.2" fill="currentColor"/><circle cx="3.5" cy="12" r="1.2" fill="currentColor"/><circle cx="3.5" cy="18" r="1.2" fill="currentColor"/></svg>',
  check: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>',
  link: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
  image: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
  sketch: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19l7-7 3 3-7 7-3-3z"/><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"/><path d="M2 2l7.586 7.586"/><circle cx="11" cy="11" r="2"/></svg>',
  hr: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="12" x2="20" y2="12"/></svg>',
  source: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>',
};

export function toolbarHtml() {
  const button = (action, label, inner) => `<button type="button" data-md="${action}" title="${label}" aria-label="${label}">${inner}</button>`;
  return `<div class="md-toolbar-items">
      ${button('bold', 'Bold', '<b>B</b>')}${button('italic', 'Italic', '<i>I</i>')}${button('strike', 'Strikethrough', '<s>S</s>')}
      <span class="md-toolbar-sep"></span>
      ${button('h1', 'Heading', '<b>H1</b>')}${button('h2', 'Subheading', '<b>H2</b>')}
      <span class="md-toolbar-sep"></span>
      ${button('ul', 'Bullet list', ICONS.ul)}${button('ol', 'Numbered list', '<span>1.</span>')}${button('check', 'Checklist', ICONS.check)}
      <span class="md-toolbar-sep"></span>
      ${button('link', 'Link', ICONS.link)}${button('hr', 'Divider', ICONS.hr)}${button('image', 'Insert image', ICONS.image)}${button('sketch', 'Sketch', ICONS.sketch)}
      <span class="md-toolbar-sep"></span>
      ${button('source', 'Markdown source', ICONS.source)}
    </div>`;
}

// A touch that moves further than this is a scroll or a drag, not a tap.
const TAP_SLOP_PX = 10;
// A press held longer than this on a link is a long press (selection,
// context menu), not a tap that opens it.
const LINK_TAP_MAX_MS = 500;

/**
 * Act on a tap of `selector` inside `root` without `root` taking focus (on a
 * phone, focus opens the keyboard). A mouse or pen press is cancelled on
 * pointerdown and the caller acts on click. A touch acts here, on touchend,
 * which is cancelled so no click or focus follows: WebKit drops the click of
 * a touch whose pointerdown was cancelled, so cancelling pointerdown for touch
 * made these taps do nothing in Safari.
 */
function bindTap(root, selector, act, listen = (target, type, fn, options) => target.addEventListener(type, fn, options), { holdMouse = true, maxMs = Infinity } = {}) {
  let start = null;
  if (holdMouse) listen(root, 'pointerdown', event => { if (event.pointerType !== 'touch' && event.target.closest(selector)) event.preventDefault(); });
  listen(root, 'touchstart', event => {
    const target = event.touches.length === 1 ? event.target.closest?.(selector) : null;
    start = target ? { target, x: event.touches[0].clientX, y: event.touches[0].clientY, at: event.timeStamp } : null;
  }, { passive: true });
  listen(root, 'touchend', event => {
    const begun = start;
    start = null;
    const touch = event.changedTouches[0];
    if (!begun || !touch || !event.cancelable || Math.hypot(touch.clientX - begun.x, touch.clientY - begun.y) > TAP_SLOP_PX) return;
    // A tap an earlier binding already took (an image inside a link) is not acted on twice.
    if (event.timeStamp - begun.at > maxMs || event.defaultPrevented) return;
    event.preventDefault();
    act(begun.target, { x: touch.clientX, y: touch.clientY });
  });
}

/** http(s), mailto, tel, or a path on this site; a bare domain gets https://. Anything else is refused. */
export function normaliseLinkUrl(value) {
  const url = String(value || '').trim();
  if (!url) return '';
  if (/^(https?:\/\/|mailto:|tel:)/i.test(url)) return url;
  // A path on this site or an anchor, rebuilt from its first character so
  // what is returned always starts with that `/` or `#`.
  if (/^\/(?!\/)/.test(url)) return `/${url.slice(1)}`;
  if (url.startsWith('#')) return `#${url.slice(1)}`;
  if (/^[\w-]+(\.[\w-]+)+(:\d+)?(\/\S*)?$/i.test(url)) return `https://${url}`;
  return '';
}

export function createRichBody({ renderMarkdown, bindMenuDismiss, dismissOrRemove, pickImage, sketch }) {
  function attach({ body, toolbar, source, hint, onEdit, onToggle, onEscape, onRenderError = () => {} }) {
    let blocks = [];
    let snapshots = [];
    let mode = 'rich';
    let edited = false;
    // How to take back the last multi-line paste, until the next edit. It is
    // DOM work the browser's undo stack never saw, so Cmd/Ctrl+Z takes it out
    // here instead of undoing the typing before.
    let lastInsert = null;
    const disposers = [];
    const listen = (target, type, fn, options) => { target.addEventListener(type, fn, options); disposers.push(() => target.removeEventListener(type, fn, options)); };
    const groupOf = node => (node?.nodeType === 1 && node.dataset.mdb != null ? Number(node.dataset.mdb) : null);
    const html = nodes => nodes.map(node => node.outerHTML ?? node.textContent).join('');
    const edit = () => { edited = true; onEdit(); };

    function topLevel(node) {
      while (node && node.parentNode !== body) node = node.parentNode;
      return node && node.parentNode === body ? node : null;
    }
    function groupNodes(start) {
      const group = groupOf(start);
      if (group === null) return [start];
      let first = start, last = start;
      while (first.previousSibling && groupOf(first.previousSibling) === group) first = first.previousSibling;
      while (last.nextSibling && groupOf(last.nextSibling) === group) last = last.nextSibling;
      const nodes = [];
      for (let node = first; node; node = node.nextSibling) { nodes.push(node); if (node === last) break; }
      return nodes;
    }

    // The renderer leaves some lines as bare inline runs (a line that starts
    // with a link or an image). Give each line its own <p> so the browser has
    // a block to edit, and the serializer still sees one line per block.
    function blockNodes(fragment) {
      const out = [];
      let paragraph = null;
      const endLine = () => { paragraph = null; };
      for (const node of Array.from(fragment.childNodes)) {
        const inline = node.nodeType === 3 || /^(A|B|CODE|DEL|EM|I|IMG|S|SPAN|STRONG|BR)$/.test(node.nodeName);
        if (!inline) { endLine(); out.push(node); continue; }
        if (node.nodeType === 3) {
          const parts = node.nodeValue.split('\n');
          parts.forEach((part, i) => {
            if (i > 0) endLine();
            if (!part) return;
            if (!paragraph) { paragraph = document.createElement('p'); out.push(paragraph); }
            paragraph.appendChild(document.createTextNode(part));
          });
          continue;
        }
        if (!paragraph) { paragraph = document.createElement('p'); out.push(paragraph); }
        paragraph.appendChild(node);
      }
      // `<p><table>` from the renderer parses into empty paragraphs around the table.
      return out.filter(node => node.nodeType === 1 && !(node.nodeName === 'P' && !node.childNodes.length));
    }

    const normalise = markup => String(markup).replace(/>\s*\n\s*</g, '><').trim();

    function renderBlock(block, index) {
      const markup = renderMarkdown(block.source);
      const template = document.createElement('template');
      template.innerHTML = markup;
      template.content.querySelectorAll('li.md-task[data-task-index]').forEach(li => {
        li.dataset.taskIndex = String(block.taskOffset + Number(li.dataset.taskIndex));
      });
      const nodes = blockNodes(template.content);
      const { lines, lossy } = serializeNodes(nodes);
      const locked = lossy || sourceNeedsMarkdownMode(block.source) || editLosesSource(block.source, lines)
        || normalise(renderMarkdown(lines.join('\n'))) !== normalise(markup);
      for (const node of nodes) {
        node.dataset.mdb = String(index);
        node.querySelectorAll('pre button').forEach(button => button.remove());
        node.querySelectorAll('.md-task-box').forEach(box => box.setAttribute('contenteditable', 'false'));
        if (locked) {
          node.setAttribute('contenteditable', 'false');
          node.classList.add('note-md-locked');
          node.title = LOCKED_HINT;
        }
      }
      snapshots[index] = html(nodes);
      return nodes;
    }

    /** A note the renderer fails on opens in Markdown source, where nothing is lost, instead of half drawn. */
    function setContent(content) {
      edited = false;
      lastInsert = null;
      source.value = content || '';
      let nodes;
      try {
        blocks = splitMarkdownBlocks(content || '');
        snapshots = [];
        nodes = blocks.flatMap((block, index) => renderBlock(block, index));
      } catch (error) {
        blocks = [];
        snapshots = [];
        body.replaceChildren();
        mode = 'source';
        showMode();
        console.error('notes: the note could not be rendered, opened in Markdown source', error);
        onRenderError(error);
        return;
      }
      body.replaceChildren(...nodes);
      const locked = body.querySelector('.note-md-locked');
      hint.classList.toggle('hidden', !locked || mode === 'source');
      body.toggleAttribute('data-empty', !nodes.length);
    }

    function getMarkdown() {
      if (mode === 'source') return source.value;
      const segments = [];
      const emitted = new Set();
      let previous = null;
      let node = body.firstChild;
      while (node) {
        const group = groupOf(node);
        if (group === null) {
          const run = [];
          while (node && groupOf(node) === null) { run.push(node); node = node.nextSibling; }
          const { lines } = serializeNodes(run);
          if (lines.length) segments.push({ group: previous, lines });
          continue;
        }
        const nodes = groupNodes(node);
        node = nodes[nodes.length - 1].nextSibling;
        const intact = !emitted.has(group) && blocks[group] && (nodes.some(n => n.classList?.contains('note-md-locked')) || html(nodes) === snapshots[group]);
        segments.push(intact ? { group, source: blocks[group].source } : { group, lines: serializeNodes(nodes).lines });
        emitted.add(group);
        previous = group;
      }
      return joinSegments(segments);
    }

    // ---- caret helpers ----
    const selection = () => window.getSelection();
    function caretElement() {
      const sel = selection();
      const node = sel && sel.anchorNode;
      if (!node || !body.contains(node)) return null;
      return node.nodeType === 1 ? node : node.parentElement;
    }
    function placeCaret(node, atStart = false) {
      try {
        const range = document.createRange();
        range.selectNodeContents(node);
        range.collapse(atStart);
        const sel = selection();
        sel.removeAllRanges();
        sel.addRange(range);
      } catch {}
    }
    function currentBlock() {
      let node = caretElement();
      while (node && node !== body) {
        if (BLOCK_TAGS.test(node.nodeName)) return node;
        node = node.parentElement;
      }
      return null;
    }

    // ---- tasks ----
    function taskText(li) {
      let text = li.querySelector(':scope > .md-task-text');
      if (!text) {
        text = document.createElement('span');
        text.className = 'md-task-text';
        for (const child of Array.from(li.childNodes)) if (!child.classList?.contains('md-task-box')) text.appendChild(child);
        li.appendChild(text);
      }
      return text;
    }
    function makeTask(indent = 0) {
      const li = document.createElement('li');
      li.className = 'md-task';
      li.dataset.done = 'false';
      if (indent) li.dataset.indent = String(indent);
      li.innerHTML = '<span class="md-task-box" role="checkbox" aria-checked="false" aria-label="" tabindex="0" contenteditable="false"></span><span class="md-task-text"><br></span>';
      return li;
    }
    function setDone(li, done) {
      li.dataset.done = String(done);
      li.classList.toggle('md-task-done', done);
      li.querySelector('.md-task-box')?.setAttribute('aria-checked', String(done));
    }
    function splitList(list, item, replacement) {
      const after = list.cloneNode(false);
      while (item.nextSibling) after.appendChild(item.nextSibling);
      item.remove();
      list.after(...[replacement, after.children.length ? after : null].filter(Boolean));
      if (!list.children.length) list.remove();
    }
    function taskToParagraph(li) {
      const p = document.createElement('p');
      const text = taskText(li);
      while (text.firstChild) p.appendChild(text.firstChild);
      if (!p.firstChild) p.appendChild(document.createElement('br'));
      splitList(li.parentElement, li, p);
      placeCaret(p, true);
      return p;
    }
    function blockToTask(block) {
      if (block.nodeName === 'LI') {
        const li = makeTask(Number(block.dataset.indent) || 0);
        const text = li.querySelector('.md-task-text');
        const moved = Array.from(block.childNodes).filter(child => !/^(UL|OL)$/.test(child.nodeName));
        if (moved.some(child => child.textContent)) text.replaceChildren(...moved);
        block.replaceWith(li);
        placeCaret(text);
        return;
      }
      const li = makeTask();
      const text = li.querySelector('.md-task-text');
      if (block === body || block.nodeName === 'PRE') {
        const list = document.createElement('ul');
        list.className = 'md-tasklist';
        list.appendChild(li);
        (block === body ? body.appendChild.bind(body) : node => block.after(node))(list);
      } else {
        const moved = Array.from(block.childNodes).filter(child => child.nodeName !== 'BR');
        if (moved.length) text.replaceChildren(...moved);
        const list = document.createElement('ul');
        list.className = 'md-tasklist';
        list.appendChild(li);
        block.replaceWith(list);
      }
      placeCaret(text);
    }

    /** Flip a checkbox in place. An untouched block keeps its source exact, with the one task line changed. */
    function flipTask(li) {
      const done = li.dataset.done !== 'true';
      const top = topLevel(li);
      const group = groupOf(top);
      const nodes = top ? groupNodes(top) : [];
      const locked = nodes.some(n => n.classList?.contains('note-md-locked'));
      const intact = group !== null && blocks[group] && (locked || html(nodes) === snapshots[group]);
      setDone(li, done);
      if (intact && li.dataset.taskIndex != null) {
        try { blocks[group].source = toggleTask(blocks[group].source, Number(li.dataset.taskIndex) - blocks[group].taskOffset).content; } catch {}
        snapshots[group] = html(nodes);
      }
      return done;
    }
    const taskItems = () => Array.from(body.querySelectorAll('li.md-task'));

    // ---- formatting ----
    function command(name, value) {
      try { document.execCommand(name, false, value); } catch {}
    }
    function toggleHeading(level) {
      const block = currentBlock();
      command('formatBlock', block && block.nodeName === `H${level}` ? 'p' : `h${level}`);
    }
    function list(kind) {
      const block = currentBlock();
      if (block?.classList?.contains('md-task')) {
        const li = document.createElement('li');
        const text = taskText(block);
        while (text.firstChild) li.appendChild(text.firstChild);
        if (block.dataset.indent) li.dataset.indent = block.dataset.indent;
        block.replaceWith(li);
        placeCaret(li);
        if (kind === 'ol') command('insertOrderedList');
        return;
      }
      command(kind === 'ol' ? 'insertOrderedList' : 'insertUnorderedList');
    }
    // The lines a selection covers: the innermost text blocks it touches, in
    // order. Code blocks are left out, as a single line in one is.
    function selectedLines() {
      const sel = selection();
      if (!sel || !sel.rangeCount || sel.isCollapsed) return [];
      const range = sel.getRangeAt(0);
      if (!body.contains(range.commonAncestorContainer)) return [];
      return Array.from(body.querySelectorAll('p, div, h1, h2, h3, h4, h5, h6, li'))
        .filter(node => range.intersectsNode(node) && !node.closest('pre')
          && !Array.from(node.children).some(child => BLOCK_TAGS.test(child.nodeName) && child.nodeName !== 'PRE'));
    }
    function checklist() {
      const lines = selectedLines();
      if (lines.length > 1) {
        // Every selected line a task already: they go back to lines.
        // Otherwise each one becomes a task, in one list.
        if (lines.every(line => line.classList.contains('md-task'))) { lines.forEach(taskToParagraph); return; }
        let caret = null;
        for (const line of lines) {
          if (line.classList.contains('md-task')) { caret = line; continue; }
          blockToTask(line);
          caret = caretElement()?.closest('li.md-task') || caret;
          // A line that was a paragraph is now a list of its own: it joins
          // the task list right before it, so the lines stay one list.
          const list = caret?.parentElement;
          const before = list?.previousElementSibling;
          if (list?.matches('ul.md-tasklist') && list.children.length === 1 && before?.matches('ul.md-tasklist')) {
            before.appendChild(caret);
            list.remove();
          }
        }
        if (caret) placeCaret(taskText(caret));
        return;
      }
      const block = currentBlock();
      if (block?.classList?.contains('md-task')) { taskToParagraph(block); return; }
      blockToTask(block || body);
    }
    function insertBlockAfterCaret(node) {
      const block = currentBlock();
      const top = block ? topLevel(block) : null;
      if (top) top.after(node); else body.appendChild(node);
    }
    function insertImage(url) {
      if (!url) return;
      const p = document.createElement('p');
      const img = document.createElement('img');
      img.className = 'md-note-image';
      img.alt = '';
      img.src = url;
      p.appendChild(img);
      insertBlockAfterCaret(p);
      edit();
    }

    function openLinkMenu(anchor) {
      const sel = selection();
      const saved = sel && sel.rangeCount && body.contains(sel.anchorNode) ? sel.getRangeAt(0).cloneRange() : null;
      const existing = caretElement()?.closest('a');
      document.querySelectorAll('.note-link-menu').forEach(dismissOrRemove);
      const menu = document.createElement('div');
      menu.className = 'note-reminder-menu note-link-menu';
      menu.innerHTML = `<div class="note-reminder-menu-title">Link</div>
        <div class="note-reminder-menu-picker"><input type="url" class="note-tag-input note-link-input" placeholder="https://" spellcheck="false" autocomplete="off" /></div>
        <button type="button" class="note-reminder-menu-item note-reminder-menu-confirm" data-act="apply"><span>Apply</span></button>
        ${existing ? '<button type="button" class="note-reminder-menu-item" data-act="remove"><span>Remove link</span></button>' : ''}`;
      document.body.appendChild(menu);
      const rect = anchor.getBoundingClientRect();
      menu.style.top = `${Math.min(rect.bottom + 4, window.innerHeight - menu.offsetHeight - 8)}px`;
      menu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - menu.offsetWidth - 8))}px`;
      const input = menu.querySelector('input');
      input.value = existing?.getAttribute('href') || '';
      const restore = () => {
        body.focus();
        if (!saved) return;
        const s = selection();
        s.removeAllRanges();
        s.addRange(saved);
      };
      // Escape (or any close while the menu has focus) puts the caret back
      // where it was; a click elsewhere keeps the focus it gave.
      const close = bindMenuDismiss(menu, () => {
        const returnFocus = menu.contains(document.activeElement);
        menu.remove();
        if (returnFocus && body.isConnected) restore();
      });
      const apply = () => {
        const url = normaliseLinkUrl(input.value);
        if (!url) { input.classList.add('invalid'); input.focus(); return; }
        close();
        restore();
        if (existing) existing.setAttribute('href', url);
        else if (!saved || saved.collapsed) {
          // The URL goes in as its own text, then becomes a link the way a
          // selection does (createLink).
          const text = document.createTextNode(url);
          if (saved) saved.insertNode(text); else body.appendChild(text);
          const range = document.createRange();
          range.selectNodeContents(text);
          const sel = selection();
          sel.removeAllRanges();
          sel.addRange(range);
          command('createLink', url);
          placeCaret(text.parentNode?.closest?.('a') || text);
        } else command('createLink', url);
        edit();
      };
      input.addEventListener('keydown', event => {
        event.stopPropagation();
        if (event.key === 'Enter') { event.preventDefault(); apply(); }
      });
      menu.querySelector('[data-act="apply"]').addEventListener('click', apply);
      menu.querySelector('[data-act="remove"]')?.addEventListener('click', () => {
        close();
        restore();
        const range = document.createRange();
        range.selectNodeContents(existing);
        const s = selection();
        s.removeAllRanges();
        s.addRange(range);
        command('unlink');
        edit();
      });
      requestAnimationFrame(() => input.focus());
    }

    function setMode(next) {
      if (next === mode) return;
      if (next === 'source') {
        source.value = getMarkdown();
        mode = 'source';
      } else {
        mode = 'rich';
        setContent(source.value);
        // A source the renderer fails on keeps the window in source (see setContent).
        if (mode === 'rich') edited = true;
      }
      showMode();
      if (mode === 'source') rove(toolbar.querySelector('[data-md="source"]'));
      (mode === 'source' ? source : body).focus();
    }
    function showMode() {
      body.classList.toggle('hidden', mode === 'source');
      source.classList.toggle('hidden', mode !== 'source');
      toolbar.dataset.mode = mode;
      toolbar.querySelector('[data-md="source"]')?.classList.toggle('active', mode === 'source');
      hint.classList.toggle('hidden', mode === 'source' || !body.querySelector('.note-md-locked'));
    }

    async function runAction(action, button) {
      if (action === 'source') { setMode(mode === 'source' ? 'rich' : 'source'); return; }
      if (mode === 'source') return;
      body.focus();
      if (action === 'link') { openLinkMenu(button); return; }
      if (action === 'image') { insertImage(await pickImage()); return; }
      if (action === 'sketch') { sketch(url => insertImage(url)); return; }
      try { command('styleWithCSS', false); } catch {}
      if (action === 'bold') command('bold');
      else if (action === 'italic') command('italic');
      else if (action === 'strike') command('strikeThrough');
      else if (action === 'h1') toggleHeading(1);
      else if (action === 'h2') toggleHeading(2);
      else if (action === 'ul' || action === 'ol') list(action);
      else if (action === 'check') checklist();
      else if (action === 'hr') command('insertHorizontalRule');
      edit();
    }

    function indent(li, delta) {
      const next = Math.max(0, Math.min(MAX_INDENT, (Number(li.dataset.indent) || 0) + delta));
      if (next) li.dataset.indent = String(next); else delete li.dataset.indent;
      edit();
    }

    function caretAtStart(container) {
      const sel = selection();
      if (!sel || !sel.isCollapsed || !sel.rangeCount) return false;
      const range = document.createRange();
      range.selectNodeContents(container);
      range.setEnd(sel.anchorNode, sel.anchorOffset);
      return range.toString() === '';
    }

    // The browser joins a line to the code block or table above it on
    // Backspace: into the <pre> beside its <code>, or as a bare line under
    // the rows that reads back as one more row. Neither is a line of text the
    // note can hold, so Backspace at the start of the block under one moves
    // the caret to the end of it instead, as the left arrow would, and an
    // empty line there goes away.
    function backspaceUnderFixedBlock() {
      const sel = selection();
      if (!sel || !sel.isCollapsed || !sel.rangeCount || !body.contains(sel.anchorNode)) return false;
      const block = topLevel(sel.anchorNode);
      const above = block && block.previousElementSibling;
      if (!above || !FIXED_BLOCKS.test(above.nodeName)) return false;
      const before = document.createRange();
      before.selectNodeContents(block);
      before.setEnd(sel.anchorNode, sel.anchorOffset);
      if (before.toString().replace(/\u200b/g, '') || before.cloneContents().querySelector?.('img')) return false;
      const cells = above.nodeName === 'TABLE' ? above.querySelectorAll('th, td') : [];
      const target = above.nodeName === 'TABLE' ? cells[cells.length - 1] : above.querySelector('code') || above;
      if (!target) return false;
      if (!block.textContent.replace(/\u200b/g, '').trim() && !block.querySelector('img')) {
        block.remove();
        edit();
      }
      placeCaret(target);
      return true;
    }

    function onKeydown(event) {
      event.stopPropagation();
      const box = event.target.closest?.('.md-task-box');
      if (box && (event.key === ' ' || event.key === 'Enter')) {
        event.preventDefault();
        onToggle(box.closest('li.md-task'));
        return;
      }
      if (event.key === 'Escape') { event.preventDefault(); onEscape(); return; }
      const mod = event.metaKey || event.ctrlKey;
      if (mod && !event.shiftKey && !event.altKey && event.key.toLowerCase() === 'z' && lastInsert) {
        event.preventDefault();
        if (!undoInsert()) command('undo');
        return;
      }
      if (mod && !event.shiftKey && !event.altKey && (event.key === 'b' || event.key === 'i')) {
        event.preventDefault();
        command(event.key === 'b' ? 'bold' : 'italic');
        edit();
        return;
      }
      // A click opens a link, so the link menu needs its own way in (the
      // document editor uses the same key).
      if (mod && !event.shiftKey && !event.altKey && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        openLinkMenu(toolbar.querySelector('[data-md="link"]'));
        return;
      }
      const li = caretElement()?.closest('li');
      if (event.key === 'Tab' && li && body.contains(li)) {
        event.preventDefault();
        if (li.parentElement?.nodeName === 'UL') indent(li, event.shiftKey ? -1 : 1);
        return;
      }
      if (event.key === 'Backspace' && !li?.classList.contains('md-task') && backspaceUnderFixedBlock()) {
        event.preventDefault();
        return;
      }
      if (!li || !li.classList.contains('md-task')) return;
      const text = taskText(li);
      if (event.key === 'Backspace' && caretAtStart(text)) {
        event.preventDefault();
        taskToParagraph(li);
        edit();
        return;
      }
      if (event.key !== 'Enter' || event.shiftKey) return;
      event.preventDefault();
      if (!text.textContent.trim()) { taskToParagraph(li); edit(); return; }
      const next = makeTask(Number(li.dataset.indent) || 0);
      const sel = selection();
      if (sel && sel.rangeCount && text.contains(sel.anchorNode)) {
        const range = sel.getRangeAt(0).cloneRange();
        range.setEnd(text, text.childNodes.length);
        const rest = range.extractContents();
        if (rest.textContent) next.querySelector('.md-task-text').replaceChildren(rest);
      }
      li.after(next);
      placeCaret(next.querySelector('.md-task-text'), true);
      edit();
    }

    const updateEmpty = () => body.toggleAttribute('data-empty', !body.textContent.trim() && !body.querySelector('img, hr, li'));
    function undoInsert() {
      const undo = lastInsert;
      lastInsert = null;
      if (!undo()) return false;
      updateEmpty();
      edit();
      return true;
    }

    function insertPlainText(text) {
      if (text && !pasteLines(text)) command('insertText', text);
      edit();
    }

    // Several lines go in as one change to the page. The browser's insertText
    // splits a paragraph per line and restyles the whole page after each
    // split (the app's :has() rules), so 100 lines froze the tab for seconds.
    // The lines split the block the way Enter does: the text after the caret
    // moves to the last line, a task line makes new tasks. A caret in code or
    // a table is left to the browser.
    const INLINE_FORMAT = /^(B|STRONG|I|EM|U|S|DEL|STRIKE|MARK|CODE|SUB|SUP|SPAN|FONT)$/;
    const BARE_INLINE = /^(A|B|STRONG|I|EM|U|S|DEL|STRIKE|MARK|CODE|SUB|SUP|SPAN|FONT|IMG)$/;
    const sameFormat = (a, b) => a?.nodeType === 1 && b?.nodeType === 1 && INLINE_FORMAT.test(a.nodeName) && a.cloneNode(false).outerHTML === b.cloneNode(false).outerHTML;
    /** Join `right` onto `left` where a cut split one run of formatting in two; text nodes too when `text`. */
    function rejoin(left, right, text) {
      while (left && right && left !== right) {
        if (text && left.nodeType === 3 && right.nodeType === 3) { left.appendData(right.data); right.remove(); return; }
        if (!sameFormat(left, right)) return;
        const nextLeft = left.lastChild;
        const nextRight = right.firstChild;
        left.append(...right.childNodes);
        right.remove();
        left = nextLeft;
        right = nextRight;
      }
    }
    const placeholder = () => document.createElement('br');
    const blank = node => !node.textContent && !node.querySelector('img, br');
    function pasteLines(text) {
      let lines = text.replace(/\r\n?/g, '\n').split('\n');
      if (lines.length < 2 || mode !== 'rich') return false;
      let sel = selection();
      if (!sel || !sel.rangeCount || !body.contains(sel.anchorNode)) return false;
      const deleted = !sel.isCollapsed;
      if (deleted) command('delete');
      sel = selection();
      if (!sel.rangeCount) return false;
      let { startContainer: startNode, startOffset } = sel.getRangeAt(0);
      // An empty note, or a line typed straight into one, has no block yet:
      // its line becomes a paragraph first.
      const wrapped = currentBlock() ? null : wrapBareLine(startNode);
      if (wrapped && startNode === body) { startNode = wrapped; startOffset = 0; }
      const unwrap = () => { if (wrapped) wrapped.replaceWith(...Array.from(wrapped.childNodes).filter(node => node.nodeName !== 'BR' || wrapped.textContent)); };
      const block = currentBlockOf(startNode);
      const task = block?.classList.contains('md-task');
      const container = task ? taskText(block) : block;
      if (!block || !/^(P|DIV|H[1-6]|LI)$/.test(block.nodeName) || block.closest('pre, table') || !container.contains(startNode)) {
        unwrap();
        placeAt(startNode, startOffset);
        return false;
      }
      const textOf = shell => (task ? shell.querySelector('.md-task-text') : shell);
      // An empty bullet would be stored as a lone "-", which reads back as text.
      if (block.nodeName === 'LI' && !task) lines = lines.filter((line, i) => line || i === 0 || i === lines.length - 1);

      // Cut what follows the caret; it ends the last pasted line.
      const rest = document.createRange();
      rest.setStart(startNode, startOffset);
      rest.setEnd(container, container.childNodes.length);
      const tail = rest.extractContents();
      if (!tail.textContent && !tail.querySelector('img')) tail.replaceChildren();
      // The formatting the caret was in carries on through the pasted lines.
      const chain = [];
      for (let at = startNode.nodeType === 1 ? startNode : startNode.parentNode; at && at !== container; at = at.parentNode) {
        if (INLINE_FORMAT.test(at.nodeName)) chain.unshift(at);
      }
      const first = lines[0] ? document.createTextNode(lines[0]) : null;
      if (first && startNode.nodeType === 3) startNode.after(first);
      else if (first) startNode.insertBefore(first, startNode.childNodes[startOffset] || null);
      const firstBreak = blank(container) ? container.appendChild(placeholder()) : null;

      const heading = /^H[1-6]$/.test(block.nodeName);
      const added = lines.slice(1).map(line => {
        let shell;
        if (task) {
          shell = makeTask(Number(block.dataset.indent) || 0);
          shell.querySelector('.md-task-text').replaceChildren();
          shell.querySelector('.md-task-box').setAttribute('aria-label', line.replace(/\s+/g, ' ').trim());
        } else {
          // A heading split at its end goes on in paragraphs, as Enter does.
          shell = heading && !tail.childNodes.length ? document.createElement('p') : block.cloneNode(false);
          if (block.dataset.mdb != null) shell.dataset.mdb = block.dataset.mdb;
        }
        let inner = textOf(shell);
        for (const element of chain) inner = inner.appendChild(element.cloneNode(false));
        if (line) inner.appendChild(document.createTextNode(line));
        return { shell, inner };
      });
      const last = added[added.length - 1];
      const lastText = lines[lines.length - 1] ? last.inner.lastChild : null;
      const caret = lastText ? { node: lastText, offset: lastText.length } : { node: last.inner, offset: last.inner.childNodes.length };
      const lastBox = textOf(last.shell);
      const joinedAt = lastBox.lastChild;
      const tailFirst = tail.firstChild;
      lastBox.appendChild(tail);
      rejoin(joinedAt, tailFirst, false);
      const breaks = added.filter(({ shell }) => blank(shell)).map(({ inner }) => inner.appendChild(placeholder()));
      block.after(...added.map(({ shell }) => shell));
      placeAt(caret.node, caret.offset);
      updateEmpty();

      // Only the paste as it was left is taken back: an edit the input event
      // does not see (Enter in a task) leaves Cmd/Ctrl+Z to the browser.
      const shells = [block, ...added.map(({ shell }) => shell)];
      const typed = () => shells.map(node => {
        if (!node.querySelector('[data-md-skip]')) return node.textContent;
        const copy = node.cloneNode(true);
        copy.querySelectorAll('[data-md-skip]').forEach(skipped => skipped.remove());
        return copy.textContent;
      }).join('\n');
      const texts = typed();
      const intact = () => shells.every((node, i) => node.isConnected && (!i || shells[i - 1].nextSibling === node)) && typed() === texts;
      lastInsert = () => {
        if (!intact()) return false;
        added.slice(0, -1).forEach(({ shell }) => shell.remove());
        lastText?.remove();
        breaks.forEach(node => node.remove());
        for (let at = last.inner; at && at !== lastBox && !at.childNodes.length;) { const up = at.parentNode; at.remove(); at = up; }
        const moved = Array.from(lastBox.childNodes);
        last.shell.remove();
        first?.remove();
        firstBreak?.remove();
        const left = container.lastChild;
        container.append(...moved);
        rejoin(left, moved[0], true);
        if (!container.firstChild) container.appendChild(placeholder());
        unwrap();
        placeAt(startNode, startOffset);
        if (deleted) command('undo');
        return true;
      };
      return true;
    }
    function currentBlockOf(node) {
      for (let at = node?.nodeType === 1 ? node : node?.parentElement; at && at !== body; at = at.parentElement) {
        if (BLOCK_TAGS.test(at.nodeName)) return at;
      }
      return null;
    }
    function placeAt(node, offset) {
      if (!node?.isConnected) return;
      try {
        const range = document.createRange();
        range.setStart(node, Math.min(offset, node.nodeType === 3 ? node.length : node.childNodes.length));
        range.collapse(true);
        const sel = selection();
        sel.removeAllRanges();
        sel.addRange(range);
      } catch {}
    }
    /** The inline run around `node` straight in the body, in a new <p>; an empty body gets an empty one. */
    function wrapBareLine(node) {
      const p = document.createElement('p');
      if (node === body) {
        if (body.textContent.trim() || body.querySelector('img, hr, li, table, pre')) return null;
        body.replaceChildren(p);
        p.appendChild(document.createElement('br'));
        return p;
      }
      let top = node;
      while (top && top.parentNode !== body) top = top.parentNode;
      const bare = at => at && (at.nodeType === 3 || BARE_INLINE.test(at.nodeName));
      if (!bare(top)) return null;
      let from = top;
      let to = top;
      while (bare(from.previousSibling)) from = from.previousSibling;
      while (bare(to.nextSibling)) to = to.nextSibling;
      from.before(p);
      for (let at = from; at;) { const next = at === to ? null : at.nextSibling; p.appendChild(at); at = next; }
      return p;
    }

    listen(body, 'keydown', onKeydown);
    // A task's checkbox is named after its text; keep the name of the task
    // being typed in current.
    function relabelTask() {
      const li = caretElement()?.closest('li.md-task');
      const box = li?.querySelector(':scope > .md-task-box');
      if (box) box.setAttribute('aria-label', (taskText(li).textContent || '').replace(/\s+/g, ' ').trim());
    }
    listen(body, 'input', () => { lastInsert = null; updateEmpty(); relabelTask(); edit(); });
    // A pasted <img onerror> must never reach the live DOM, and rich markup
    // the serializer would flatten is not imported: paste is text only.
    listen(body, 'paste', event => {
      event.preventDefault();
      insertPlainText((event.clipboardData || window.clipboardData)?.getData('text/plain') || '');
    });
    listen(body, 'drop', event => {
      event.preventDefault();
      const text = event.dataTransfer?.getData('text/plain') || '';
      const range = document.caretRangeFromPoint?.(event.clientX, event.clientY);
      if (range) { const sel = selection(); sel.removeAllRanges(); sel.addRange(range); }
      insertPlainText(text);
    });
    // Tapping a checkbox must not focus the body, which would open the
    // on-screen keyboard on a phone.
    bindTap(body, '.md-task-box', target => onToggle(target.closest('li.md-task')), listen);
    // Same policy as the link popover: a target it would refuse never opens.
    const openLink = link => {
      if (normaliseLinkUrl(link.getAttribute('href'))) window.open(link.href, '_blank', 'noopener,noreferrer');
    };
    listen(body, 'click', event => {
      const box = event.target.closest('.md-task-box');
      if (box) { event.preventDefault(); onToggle(box.closest('li.md-task')); return; }
      const link = event.target.closest('a[href]');
      if (!link || !body.contains(link)) return;
      event.preventDefault();
      // A plain click opens a link, as Cmd or Ctrl+click does; the caret goes
      // in it with a click beside it or the arrow keys. A drag that selects
      // text, or a Shift or Alt click, stays an edit.
      if (event.shiftKey || event.altKey || !selection().isCollapsed) return;
      if (press && Math.hypot(event.clientX - press.x, event.clientY - press.y) > TAP_SLOP_PX) return;
      openLink(link);
    });
    // Where the last mouse press began, to tell a click from a short drag.
    let press = null;
    listen(body, 'pointerdown', event => { press = event.pointerType === 'touch' ? null : { x: event.clientX, y: event.clientY }; });
    // A tap opens the link without focusing the body (no keyboard).
    bindTap(body, 'a[href]', openLink, listen, { holdMouse: false, maxMs: LINK_TAP_MAX_MS });
    listen(source, 'input', edit);
    listen(source, 'keydown', event => {
      event.stopPropagation();
      if (event.key === 'Escape') { event.preventDefault(); onEscape(); }
    });
    toolbar.querySelectorAll('[data-md]').forEach(button => {
      // Keep the body's selection: formatting applies to what is selected.
      listen(button, 'mousedown', event => event.preventDefault());
      listen(button, 'click', event => { event.preventDefault(); event.stopPropagation(); runAction(button.dataset.md, button); });
    });
    // One tab stop for the whole toolbar; the arrow keys, Home and End move
    // between its buttons (in source mode, only Markdown source is live).
    const toolButtons = () => Array.from(toolbar.querySelectorAll('[data-md]')).filter(button => mode !== 'source' || button.dataset.md === 'source');
    const rove = target => {
      toolbar.querySelectorAll('[data-md]').forEach(button => { button.tabIndex = button === target ? 0 : -1; });
    };
    rove(toolButtons()[0]);
    listen(toolbar, 'focusin', event => { const button = event.target.closest('[data-md]'); if (button) rove(button); });
    listen(toolbar, 'keydown', event => {
      const items = toolButtons();
      const at = items.indexOf(event.target.closest('[data-md]'));
      if (at < 0) return;
      const next = { ArrowRight: at + 1, ArrowLeft: at - 1, Home: 0, End: items.length - 1 }[event.key];
      if (next === undefined) return;
      event.preventDefault();
      event.stopPropagation();
      items[(next + items.length) % items.length].focus();
    });
    listen(hint, 'click', event => { if (event.target.closest('[data-act="source"]')) setMode('source'); });

    return {
      setContent,
      getMarkdown,
      setMode,
      flipTask,
      taskItems,
      get mode() { return mode; },
      get edited() { return edited; },
      focusEnd() { if (mode === 'source') { source.focus(); return; } body.focus(); placeCaret(body); },
      destroy() { disposers.splice(0).forEach(dispose => dispose()); document.querySelectorAll('.note-link-menu').forEach(dismissOrRemove); },
    };
  }

  return { attach };
}
