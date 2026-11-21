/** The note window's editable body: rendered markdown edited in place, a formatting toolbar, and a raw markdown source mode. */
import { serializeNodes, joinSegments, splitMarkdownBlocks, linkDestination } from '../domain/markdownSerializer.js';
import { preserveSource } from '../domain/sourceFidelity.js';
import { MAX_INDENT, toggleTask } from '../domain/markdownTasks.js';
import { createImageMenu, imageFiles, missingPlaceholder, removeImage, setImageWidth } from './noteImages.js';

const BLOCK_TAGS = /^(P|DIV|H[1-6]|LI|PRE|BLOCKQUOTE)$/;
// Blocks a line of text cannot be joined into (see backspaceUnderFixedBlock).
const FIXED_BLOCKS = /^(PRE|TABLE)$/;
// The clipboard type a copy from a note carries: the selection as markdown,
// so a cut island or link pastes back as itself, not as its visible text.
const NOTE_MARKDOWN_MIME = 'text/x-odysseus-note-markdown';
// What the serializer made of a block when it was rendered, the baseline an
// edit is measured against. It depends only on the source, so it is kept: a
// re-render after a save, a tick or a re-read does not serialize again.
const BASELINE_CACHE_SIZE = 4000;
const baselineCache = new Map();
// A stored block holds no blank line outside a fence (a blank line ends a
// block), so the blank lines the serializer puts between blocks are new. To
// measure an edit they are written as a line no note has: matched against
// the source, a blank line would pair with one inside a fence and send the
// typed text there. They become blank lines once the edit is written.
const GAP = '\u0000\u2029';
const serializeEdit = nodes => serializeNodes(nodes, { gap: GAP });
const ungap = text => text.split('\n').map(line => (line === GAP ? '' : line)).join('\n');

/** The serializer's reading of a freshly rendered block, cached per source. */
export function blockBaseline(blockSource, nodes, { key = '', serialize = serializeEdit } = {}) {
  const cacheKey = `${key}\u0000${blockSource}`;
  const known = baselineCache.get(cacheKey);
  if (known !== undefined) return known;
  const { lines, lossy } = serialize(nodes);
  const baseline = { text: lines.join('\n'), lossy };
  if (baselineCache.size >= BASELINE_CACHE_SIZE) baselineCache.delete(baselineCache.keys().next().value);
  baselineCache.set(cacheKey, baseline);
  return baseline;
}

/**
 * The markdown to store for an edited block. What the edit kept is written as
 * the source wrote it (see sourceFidelity.js). When the editor cannot write
 * the block at all, the source is kept as it is rather than saving something
 * lossy, and `keep` gets the editor's best reading of the edit, so the caller
 * can say so and hold it on this device instead of dropping it unseen.
 */
export function editedBlockMarkdown(block, nodes, { render, serialize = serializeEdit, warn = (...args) => console.warn(...args), keep = () => {} }) {
  const { lines, lossy } = serialize(nodes);
  if (lossy || block.baseline?.lossy) {
    warn('notes: a block the editor cannot write was kept as stored', block.source.length);
    keep(ungap(lines.join('\n')));
    return block.source;
  }
  const edited = lines.join('\n');
  if (!block.baseline) return ungap(edited);
  return ungap(preserveSource({ source: block.source, baseline: block.baseline.text, edited, render: markdown => render(ungap(markdown)) }));
}
/**
 * What to tell the user when fewer images came back than were sent, or ''.
 * The server refuses an upload that comes within seconds of a few others
 * (429): that one says to wait, since trying again at once fails the same way.
 */
export function uploadFailureMessage(sent, stored, { rateLimited = false } = {}) {
  if (stored >= sent) return '';
  if (rateLimited) return 'Image upload failed: too many uploads in a few seconds. Wait a moment and try again.';
  return stored ? 'Some images could not be uploaded' : 'Image upload failed';
}

const ICONS = {
  ul: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><circle cx="3.5" cy="6" r="1.2" fill="currentColor"/><circle cx="3.5" cy="12" r="1.2" fill="currentColor"/><circle cx="3.5" cy="18" r="1.2" fill="currentColor"/></svg>',
  check: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>',
  link: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
  image: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
  sketch: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19l7-7 3 3-7 7-3-3z"/><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"/><path d="M2 2l7.586 7.586"/><circle cx="11" cy="11" r="2"/></svg>',
  highlight: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l-6 6v3h9l3-3"/><path d="M22 12l-4.6 4.6a2 2 0 0 1-2.8 0l-5.2-5.2a2 2 0 0 1 0-2.8L14 4"/></svg>',
  hr: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="12" x2="20" y2="12"/></svg>',
  source: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>',
};

export function toolbarHtml() {
  const button = (action, label, inner) => `<button type="button" data-md="${action}" title="${label}" aria-label="${label}">${inner}</button>`;
  return `<div class="md-toolbar-items">
      ${button('bold', 'Bold', '<b>B</b>')}${button('italic', 'Italic', '<i>I</i>')}${button('underline', 'Underline', '<u>U</u>')}${button('strike', 'Strikethrough', '<s>S</s>')}
      ${button('highlight', 'Highlight', ICONS.highlight)}${button('sup', 'Superscript', '<span>x<sup>2</sup></span>')}${button('sub', 'Subscript', '<span>x<sub>2</sub></span>')}
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
export function bindTap(root, selector, act, listen = (target, type, fn, options) => target.addEventListener(type, fn, options), { holdMouse = true, maxMs = Infinity } = {}) {
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
  if (/^(\/(?!\/)|#)/.test(url)) return url;
  if (/^[\w-]+(\.[\w-]+)+(:\d+)?(\/\S*)?$/i.test(url)) return `https://${url}`;
  return '';
}

export function createRichBody({ renderMarkdown, bindMenuDismiss, dismissOrRemove, pickImageFile, uploadImages, sketch, showError, portalZ = () => null, externalImages = () => false }) {
  const imageMenu = createImageMenu({ bindMenuDismiss, dismissOrRemove, portalZ });

  function attach({ body, toolbar, source, onEdit, onToggle, onEscape, onFlush = () => {}, mediaNote = () => null, decorate = () => {}, onRenderError = () => {} }) {
    // A shared note loads its uploads through the note (see mdToHtml's
    // mediaNote). The body renders with the source stamps the serializer
    // reads; whether two texts render the same is judged without them.
    // Whether external images show is read once: every render of this
    // editor, the ones an edit is compared against included, agrees.
    const showExternal = !!externalImages();
    const render = markdown => renderMarkdown(markdown, mediaNote(), { sourceMap: true, externalImages: showExternal });
    const renderPlain = markdown => renderMarkdown(markdown, mediaNote(), { externalImages: showExternal });
    const baselineKey = () => `${mediaNote() || ''}|${showExternal ? 'ext' : ''}`;
    let blocks = [];
    let snapshots = [];
    let mode = 'rich';
    let edited = false;
    let insertedGroups = 0;
    // How to take back the last image insert or multi-line paste, until the
    // next edit. Both are DOM work the browser's undo stack never saw, so
    // Cmd/Ctrl+Z takes them out here instead of undoing the typing before.
    let lastInsert = null;
    const disposers = [];
    const listen = (target, type, fn, options) => { target.addEventListener(type, fn, options); disposers.push(() => target.removeEventListener(type, fn, options)); };
    const groupOf = node => (node?.nodeType === 1 && node.dataset.mdb != null ? Number(node.dataset.mdb) : null);
    // Chrome injected after render ([data-md-skip], the per-task agent
    // button) is not part of a block: an untouched block stays untouched.
    // An island's insides are typeset after render (math, a diagram), so
    // only its source says whether it changed.
    const outer = node => {
      if (node.nodeType !== 1) return node.textContent;
      if (!node.matches('.md-island') && !node.querySelector('[data-md-skip], .md-island')) return node.outerHTML;
      const copy = node.cloneNode(true);
      copy.querySelectorAll('[data-md-skip]').forEach(skipped => skipped.remove());
      [copy, ...copy.querySelectorAll('.md-island')].forEach(island => { if (island.matches('.md-island')) island.replaceChildren(); });
      return copy.outerHTML;
    };
    const html = nodes => nodes.map(outer).join('');
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

    // An image takes focus, so its size menu opens from the keyboard too.
    const focusableImage = img => { img.tabIndex = 0; };

    function renderBlock(block, index) {
      const markup = render(block.source);
      const template = document.createElement('template');
      template.innerHTML = markup;
      template.content.querySelectorAll('li.md-task[data-task-index]').forEach(li => {
        li.dataset.taskIndex = String(block.taskOffset + Number(li.dataset.taskIndex));
      });
      const nodes = blockNodes(template.content);
      for (const node of nodes) {
        node.dataset.mdb = String(index);
        node.querySelectorAll('pre button').forEach(button => button.remove());
        node.querySelectorAll('.md-task-box').forEach(box => box.setAttribute('contenteditable', 'false'));
        node.querySelectorAll('img.md-note-image').forEach(focusableImage);
        islandsIn(node).forEach(markIsland);
      }
      block.baseline = blockBaseline(block.source, nodes, { key: baselineKey() });
      snapshots[index] = html(nodes);
      return nodes;
    }

    const islandsIn = node => (node.matches?.('.md-island') ? [node] : Array.from(node.querySelectorAll?.('.md-island') || []));
    // An island is one character of the text: it can be selected, deleted,
    // cut and pasted, and a click edits its source.
    const markIsland = island => {
      island.setAttribute('contenteditable', 'false');
      island.setAttribute('role', 'button');
      island.title = 'Edit';
    };

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
      body.toggleAttribute('data-empty', !nodes.length);
      decorate(body);
    }

    /**
     * The note as markdown to store, and `unsaved`: when a block's edit could
     * not be written and its stored text was kept instead, the whole note with
     * the editor's best reading of that edit (null otherwise).
     */
    function read() {
      if (mode === 'source') return { content: source.value, unsaved: null };
      const segments = [];
      const kept = [];
      let lost = false;
      const emitted = new Set();
      let previous = null;
      let node = body.firstChild;
      while (node) {
        const group = groupOf(node);
        if (group === null) {
          const run = [];
          const above = node.previousSibling;
          while (node && groupOf(node) === null) { run.push(node); node = node.nextSibling; }
          // Typed under a block of another kind: a blank line between them.
          const { lines } = serializeNodes(run, { after: above });
          if (lines.length) { segments.push({ group: previous, lines }); kept.push({ group: previous, lines }); }
          continue;
        }
        const nodes = groupNodes(node);
        node = nodes[nodes.length - 1].nextSibling;
        const first = !emitted.has(group) && blocks[group];
        let segment;
        let reading = null;
        if (first && html(nodes) === snapshots[group]) segment = { group, source: blocks[group].source };
        else if (first) {
          const keep = text => { lost = true; reading = { group, lines: text.split('\n') }; };
          segment = { group, lines: editedBlockMarkdown(blocks[group], nodes, { render: renderPlain, keep }).split('\n') };
        } else segment = { group, lines: serializeNodes(nodes).lines };
        segments.push(segment);
        kept.push(reading || segment);
        emitted.add(group);
        previous = group;
      }
      return { content: joinSegments(segments), unsaved: lost ? joinSegments(kept) : null };
    }
    const getMarkdown = () => read().content;

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
    // Where the caret is, as a top-level block and a text offset in it, so it
    // can be put back after the body is drawn again (a collaborator's change).
    // A whole list or table is one block, so in there the caret is kept per
    // list item, or per table row and cell.
    // Text in a non-editable part (a task box, an island) is not counted, nor,
    // for an item or a row, the text of another item or row inside it.
    const UNIT = 'li, tr';
    function caretText(block) {
      const unit = block.matches?.(UNIT);
      const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT, {
        acceptNode: node => (node.parentElement.closest('[contenteditable="false"], [data-md-skip]') || (unit && node.parentElement.closest(UNIT) !== block)
          ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
      });
      const nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      return nodes;
    }
    function offsetIn(block, node, offset) {
      const range = document.createRange();
      range.setStart(block, 0);
      range.setEnd(node, offset);
      let count = 0;
      for (const text of caretText(block)) {
        if (!range.intersectsNode(text)) continue;
        count += text === range.endContainer ? range.endOffset : text.length;
      }
      return count;
    }
    function pointIn(block, offset) {
      for (const text of caretText(block)) {
        if (offset <= text.length) return { node: text, offset };
        offset -= text.length;
      }
      return null;
    }
    function saveCaret() {
      if (mode === 'source') return { source: true, start: source.selectionStart, end: source.selectionEnd };
      const sel = selection();
      if (!sel?.rangeCount || !body.contains(sel.anchorNode) || !body.contains(sel.focusNode)) return null;
      const anchorBlock = topLevel(sel.anchorNode), focusBlock = topLevel(sel.focusNode);
      if (!anchorBlock || !focusBlock) return null;
      const children = Array.from(body.children);
      const mark = (block, node, offset) => {
        const element = node.nodeType === 1 ? node : node.parentElement;
        const unit = element.closest(UNIT);
        if (!unit || !block.contains(unit)) return { index: children.indexOf(block), text: block.textContent, offset: offsetIn(block, node, offset) };
        const units = unitsLike(unit), at = units.indexOf(unit);
        const cell = unit.nodeName === 'TR' ? element.closest('td, th') : null;
        return {
          index: children.indexOf(block), text: block.textContent,
          unit: unit.nodeName, at, key: unitKey(unit), before: at > 0 ? unitKey(units[at - 1]) : null,
          cell: cell ? Array.from(unit.cells).indexOf(cell) : -1,
          offset: offsetIn(cell || unit, node, offset),
        };
      };
      return { anchor: mark(anchorBlock, sel.anchorNode, sel.anchorOffset), focus: mark(focusBlock, sel.focusNode, sel.focusOffset) };
    }
    const unitsLike = unit => Array.from(body.querySelectorAll(unit.nodeName === 'TR' ? 'tr' : 'li'));
    const unitKey = unit => caretText(unit).map(text => text.data).join('');
    // The same item or row if it is still there unchanged, the nearest one to
    // its old place (items added or removed above it move it). Else the one
    // now after the item that was above it, else the one now at its place.
    function unitAt(mark) {
      const units = unitsLike({ nodeName: mark.unit });
      if (!units.length) return null;
      const keyAt = i => (units[i] ? unitKey(units[i]) : undefined);
      const near = (index, test) => {
        for (let distance = 0; distance < units.length; distance++) {
          for (const i of [index - distance, index + distance]) if (units[i] && test(i)) return units[i];
        }
        return null;
      };
      const before = i => mark.before === null ? i === 0 : keyAt(i - 1) === mark.before;
      let unit = near(mark.at, i => keyAt(i) === mark.key && before(i)) || near(mark.at, i => keyAt(i) === mark.key);
      if (!unit && mark.before !== null) unit = near(mark.at, before);
      return unit || units[Math.min(mark.at, units.length - 1)];
    }
    function unitPoint(mark) {
      const unit = unitAt(mark);
      if (!unit) return null;
      const holder = mark.cell >= 0 && unit.cells?.length ? unit.cells[Math.min(mark.cell, unit.cells.length - 1)] : unit;
      const point = pointIn(holder, mark.offset);
      if (point) return point;
      const texts = caretText(holder);
      const last = texts[texts.length - 1];
      if (last) return { node: last, offset: last.length };
      const text = holder.querySelector(':scope > .md-task-text') || holder;
      return { node: text, offset: 0 };
    }
    // The same block if it is still there unchanged (blocks added or removed
    // above it move it), else the block now at its place. The offset is kept,
    // or the end of the block when the block got shorter or is gone.
    function caretPoint(mark) {
      const children = Array.from(body.children);
      if (!children.length) return { node: body, offset: 0 };
      const inUnit = mark.unit && unitPoint(mark);
      if (inUnit) return inUnit;
      let block = null;
      for (let distance = 0; distance < children.length && !block; distance++) {
        block = [children[mark.index - distance], children[mark.index + distance]].find(child => child?.textContent === mark.text) || null;
      }
      block ||= children[Math.min(mark.index, children.length - 1)];
      const point = pointIn(block, mark.offset);
      if (point) return point;
      const texts = caretText(block);
      const last = texts[texts.length - 1];
      return last ? { node: last, offset: last.length } : { node: block, offset: block.childNodes.length };
    }
    function restoreCaret(saved) {
      if (!saved) return;
      if (saved.source) {
        if (mode !== 'source') return;
        const length = source.value.length;
        source.setSelectionRange(Math.min(saved.start, length), Math.min(saved.end, length));
        return;
      }
      if (mode !== 'rich') return;
      try {
        const anchor = caretPoint(saved.anchor), focus = caretPoint(saved.focus);
        selection().setBaseAndExtent(anchor.node, anchor.offset, focus.node, focus.offset);
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
        for (const child of Array.from(li.childNodes)) if (!child.classList?.contains('md-task-box') && !child.hasAttribute?.('data-md-skip')) text.appendChild(child);
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
      const intact = group !== null && blocks[group] && html(nodes) === snapshots[group];
      setDone(li, done);
      if (intact && li.dataset.taskIndex != null) {
        try { blocks[group].source = toggleTask(blocks[group].source, Number(li.dataset.taskIndex) - blocks[group].taskOffset).content; } catch {}
        snapshots[group] = html(nodes);
        blocks[group].baseline = blockBaseline(blocks[group].source, nodes, { key: baselineKey() });
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
    // ---- images ----
    // The block the caret is in, remembered before a picker, an upload or the
    // sketch sheet takes the focus away.
    function caretAnchor() {
      const block = currentBlock();
      return block ? topLevel(block) : null;
    }
    /** Insert images as new blocks after `anchor` (or at the end), then save. */
    function insertImages(urls, anchor) {
      const list = urls.filter(Boolean);
      if (!list.length || mode !== 'rich' || !body.isConnected) return;
      let after = anchor && anchor.parentNode === body ? anchor : null;
      const inserted = [];
      for (const url of list) {
        const p = document.createElement('p');
        // A group no source block has: the image is its own markdown block,
        // separated from its neighbours by a blank line.
        p.dataset.mdb = String(blocks.length + (++insertedGroups));
        const img = document.createElement('img');
        img.className = 'md-note-image';
        img.alt = '';
        img.src = url;
        focusableImage(img);
        p.appendChild(img);
        if (after) after.after(p); else body.appendChild(p);
        after = p;
        inserted.push(p);
      }
      body.removeAttribute('data-empty');
      edit();
      lastInsert = () => { inserted.forEach(node => node.remove()); return true; };
      onFlush();
    }
    const updateEmpty = () => body.toggleAttribute('data-empty', !body.textContent.trim() && !body.querySelector('img, hr, li'));
    function undoInsert() {
      const undo = lastInsert;
      lastInsert = null;
      if (!undo()) return false;
      updateEmpty();
      edit();
      onFlush();
      return true;
    }
    async function uploadFiles(files, anchor) {
      if (!files.length) return;
      const { urls, rateLimited } = await uploadImages(files);
      const failed = uploadFailureMessage(files.length, urls.length, { rateLimited });
      if (failed) showError(failed);
      insertImages(urls, anchor);
    }
    // Resize and remove are edits: a body that is not editable (view-only, or
    // a locked block) leaves its images alone.
    const editableImage = img => mode === 'rich' && body.isContentEditable && img.isContentEditable;
    // A placeholder for an image that did not load stands for the image: its menu edits or removes it.
    const missingImage = box => {
      const img = box?.previousElementSibling;
      return img && img.matches('img.md-note-image') && editableImage(img) ? img : null;
    };
    function openImageMenu(img, at, { keyboard = false } = {}) {
      imageMenu.open(img, {
        at,
        keyboard,
        // After a removal the image is gone: the caret goes back to the body.
        returnFocus: () => (img.isConnected ? img.focus() : body.focus()),
        onResize: width => {
          if (!img.isConnected) return;
          lastInsert = null;
          setImageWidth(img, width);
          edit();
          onFlush();
        },
        onRemove: () => {
          if (!img.isConnected) return;
          lastInsert = null;
          removeImage(img);
          if (keyboard) body.focus();
          updateEmpty();
          edit();
          onFlush();
        },
      });
    }

    // A popover under `anchor` in the note's menu style. `fields` are text
    // inputs; `apply` gets their values and returns false to keep it open.
    function openMenu(anchor, { title, className, fields, actions = [], apply, initialFocus = 0 }) {
      const sel = selection();
      const saved = sel && sel.rangeCount && body.contains(sel.anchorNode) ? sel.getRangeAt(0).cloneRange() : null;
      document.querySelectorAll('.note-link-menu').forEach(dismissOrRemove);
      const menu = document.createElement('div');
      menu.className = `note-reminder-menu note-link-menu ${className}`;
      const title_ = document.createElement('div');
      title_.className = 'note-reminder-menu-title';
      title_.textContent = title;
      menu.appendChild(title_);
      const inputs = fields.map(field => {
        const picker = document.createElement('div');
        picker.className = 'note-reminder-menu-picker';
        const input = document.createElement(field.multiline ? 'textarea' : 'input');
        if (!field.multiline) input.type = field.type || 'text';
        input.className = `note-tag-input ${field.className || ''}`.trim();
        input.placeholder = field.placeholder || '';
        input.setAttribute('aria-label', field.label);
        input.spellcheck = false;
        input.autocomplete = 'off';
        input.value = field.value || '';
        if (field.multiline) input.rows = Math.min(8, Math.max(2, input.value.split('\n').length));
        picker.appendChild(input);
        menu.appendChild(picker);
        return input;
      });
      const buttons = [{ act: 'apply', label: 'Apply', confirm: true }, ...actions].map(action => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `note-reminder-menu-item${action.confirm ? ' note-reminder-menu-confirm' : ''}`;
        button.dataset.act = action.act;
        const span = document.createElement('span');
        span.textContent = action.label;
        button.appendChild(span);
        menu.appendChild(button);
        return [button, action];
      });
      const z = portalZ();
      if (z) menu.style.zIndex = String(z);
      document.body.appendChild(menu);
      const rect = anchor.getBoundingClientRect();
      menu.style.top = `${Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - menu.offsetHeight - 8))}px`;
      menu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - menu.offsetWidth - 8))}px`;
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
      const run = () => {
        const then = apply(inputs.map(input => input.value), inputs);
        if (then === false) return;
        close();
        restore();
        if (typeof then === 'function') then();
      };
      inputs.forEach((input, index) => input.addEventListener('keydown', event => {
        event.stopPropagation();
        if (event.key === 'Enter' && (!fields[index].multiline || event.metaKey || event.ctrlKey)) { event.preventDefault(); run(); }
      }));
      for (const [button, action] of buttons) {
        button.addEventListener('click', () => {
          if (action.act === 'apply') { run(); return; }
          close();
          restore();
          action.run();
        });
      }
      requestAnimationFrame(() => inputs[initialFocus]?.focus());
      return { saved };
    }

    const TITLED_DEST_RE = /^(\S+)\s+"([^"]*)"\s*$/;
    // A link's target and title as the note wrote them.
    function linkTarget(a) {
      const written = a.dataset.mdDest;
      if (written == null) return { url: a.getAttribute('href') || '', title: a.getAttribute('title') || '' };
      const titled = TITLED_DEST_RE.exec(written.trim());
      return titled ? { url: titled[1], title: titled[2] } : { url: written.trim(), title: '' };
    }
    // A target kept as written is stored whatever its scheme, but only one the
    // link menu would accept becomes an href: the rest stay inert.
    function setLinkTarget(a, url, title) {
      if (normaliseLinkUrl(url) === url) a.setAttribute('href', url); else a.removeAttribute('href');
      if (title) a.setAttribute('title', title); else a.removeAttribute('title');
      a.dataset.mdDest = linkDestination(url, title);
    }

    function openLinkMenu(anchor) {
      const existing = caretElement()?.closest('a');
      if (existing?.hasAttribute('data-md-image')) { openImageLinkMenu(existing, anchor); return; }
      const current = existing ? linkTarget(existing) : { url: '', title: '' };
      const { saved } = openMenu(anchor, {
        title: 'Link',
        className: '',
        fields: [
          { label: 'Link address', type: 'url', className: 'note-link-input', placeholder: 'https://', value: current.url },
          { label: 'Link title', className: 'note-link-title', placeholder: 'Title (optional)', value: current.title },
        ],
        actions: existing ? [{ act: 'remove', label: 'Remove link', run: () => {
          const range = document.createRange();
          range.selectNodeContents(existing);
          const s = selection();
          s.removeAllRanges();
          s.addRange(range);
          command('unlink');
          edit();
        } }] : [],
        apply: ([value, title], [input]) => {
          // An untouched target is kept as written, whatever its scheme.
          const unchanged = existing && value.trim() === current.url;
          const url = unchanged ? current.url : normaliseLinkUrl(value);
          if (!url) { input.classList.add('invalid'); input.focus(); return false; }
          return () => {
            const t = title.trim().replace(/"/g, "'");
            if (existing) setLinkTarget(existing, url, t);
            else if (!saved || saved.collapsed) {
              const a = document.createElement('a');
              setLinkTarget(a, url, t);
              a.textContent = url;
              if (saved) saved.insertNode(a); else body.appendChild(a);
              placeCaret(a);
            } else {
              command('createLink', url);
              const a = caretElement()?.closest('a');
              if (a) setLinkTarget(a, url, t);
            }
            edit();
          };
        },
      });
    }

    // An external image shown as an image (the viewer's setting) is edited
    // like its link: address and description. It has no size menu, because
    // its size is part of the target as written.
    function openAnyImageMenu(img, at) {
      if (!img.hasAttribute('data-md-image')) { openImageMenu(img, at); return; }
      openImageLinkMenu(img, at ? { getBoundingClientRect: () => ({ left: at.x, bottom: at.y }) } : img);
    }

    // An image from elsewhere shows as a link and is never fetched, or, with
    // the setting on, as the image; its address and description are edited here.
    function openImageLinkMenu(a, anchor) {
      const current = linkTarget(a);
      const shown = a.nodeName === 'IMG';
      openMenu(anchor, {
        title: 'Image link',
        className: 'note-image-link-menu',
        fields: [
          { label: 'Image address', type: 'url', className: 'note-link-input', placeholder: 'https://', value: current.url },
          { label: 'Image description', className: 'note-link-title', placeholder: 'Description', value: a.dataset.mdAlt || '' },
        ],
        actions: [{ act: 'remove', label: 'Remove image', run: () => { if (shown) removeImage(a); else a.remove(); updateEmpty(); edit(); } }],
        apply: ([value, alt], [input]) => {
          const url = value.trim() === current.url ? current.url : normaliseLinkUrl(value);
          if (!url || !/^https?:\/\//i.test(url) && value.trim() !== current.url) { input.classList.add('invalid'); input.focus(); return false; }
          if (shown) {
            // Rendered again from its markdown: a new address is shown or
            // kept as a link by the same rules as the rest of the note.
            return () => {
              const description = alt.replace(/[\]\n]/g, ' ').trim();
              const unchanged = url === current.url && description === (a.dataset.mdAlt || '');
              if (unchanged) return;
              const nodes = renderedNodes(`![${description}](${linkDestination(url, current.title)})`);
              missingPlaceholder(a)?.remove();
              a.replaceWith(...nodes);
              nodes.forEach(node => { if (node.nodeName === 'IMG') focusableImage(node); });
              edit();
            };
          }
          return () => {
            setLinkTarget(a, url, current.title);
            a.dataset.mdAlt = alt.replace(/[\]\n]/g, ' ').trim();
            a.textContent = a.dataset.mdAlt || url;
            edit();
          };
        },
      });
    }

    // ---- islands ----
    // The rendering of `markdown` as nodes to put where an island was: the
    // island itself when it still is one, else its inline content.
    function renderedNodes(markdown) {
      const template = document.createElement('template');
      template.innerHTML = render(markdown);
      const nodes = Array.from(template.content.childNodes).filter(node => node.nodeType === 1 || node.textContent);
      const inner = nodes.length === 1 && nodes[0].nodeName === 'P' ? Array.from(nodes[0].childNodes) : nodes;
      inner.forEach(node => { if (node.nodeType === 1) islandsIn(node).forEach(markIsland); });
      return inner;
    }
    function openIslandMenu(island, at) {
      if (mode !== 'rich' || !body.isContentEditable) return;
      const anchor = at ? { getBoundingClientRect: () => ({ left: at.x, bottom: at.y }) } : island;
      openMenu(anchor, {
        title: 'Markdown',
        className: 'note-island-menu',
        fields: [{ label: 'Markdown', multiline: true, className: 'note-island-input', value: island.dataset.mdSrc || '' }],
        actions: [{ act: 'remove', label: 'Remove', run: () => { island.remove(); updateEmpty(); edit(); } }],
        apply: ([value]) => () => {
          if (!island.isConnected) return;
          const text = value.replace(/\r\n/g, '\n');
          if (text === island.dataset.mdSrc) return;
          if (!text.trim()) island.remove();
          else island.replaceWith(...renderedNodes(text));
          updateEmpty();
          edit();
          decorate(body);
        },
      });
    }

    function setMode(next) {
      if (next === mode) return;
      if (next === 'source') {
        // An edit the rich editor could not write is shown here as typed, so
        // it can be fixed and saved from the source.
        const { content, unsaved } = read();
        source.value = unsaved ?? content;
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
    }

    async function runAction(action, button) {
      if (action === 'source') { setMode(mode === 'source' ? 'rich' : 'source'); return; }
      if (mode === 'source') return;
      body.focus();
      if (action === 'link') { openLinkMenu(button); return; }
      if (action === 'image') { const anchor = caretAnchor(); const file = await pickImageFile(); if (file) uploadFiles([file], anchor); return; }
      if (action === 'sketch') { const anchor = caretAnchor(); sketch(url => insertImages([url], anchor)); return; }
      try { command('styleWithCSS', false); } catch {}
      if (action === 'bold') command('bold');
      else if (action === 'italic') command('italic');
      else if (action === 'underline') command('underline');
      else if (action === 'sup') command('superscript');
      else if (action === 'sub') command('subscript');
      else if (action === 'highlight') toggleHighlight();
      else if (action === 'strike') command('strikeThrough');
      else if (action === 'h1') toggleHeading(1);
      else if (action === 'h2') toggleHeading(2);
      else if (action === 'ul' || action === 'ol') list(action);
      else if (action === 'check') checklist();
      else if (action === 'hr') command('insertHorizontalRule');
      edit();
    }

    // execCommand has no highlight that writes <mark>: wrap the selection, or
    // unwrap the highlight the caret is in.
    function toggleHighlight() {
      const sel = selection();
      if (!sel || !sel.rangeCount || !body.contains(sel.anchorNode)) return;
      const inside = caretElement()?.closest('mark');
      if (inside && body.contains(inside)) {
        inside.replaceWith(...inside.childNodes);
        return;
      }
      const range = sel.getRangeAt(0);
      if (range.collapsed) return;
      const mark = document.createElement('mark');
      mark.appendChild(range.extractContents());
      mark.querySelectorAll('mark').forEach(inner => inner.replaceWith(...inner.childNodes));
      range.insertNode(mark);
      sel.removeAllRanges();
      const after = document.createRange();
      after.selectNodeContents(mark);
      sel.addRange(after);
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
      if (before.toString().replace(/\u200b/g, '') || before.cloneContents().querySelector?.('img, .md-island')) return false;
      const cells = above.nodeName === 'TABLE' ? above.querySelectorAll('th, td') : [];
      const target = above.nodeName === 'TABLE' ? cells[cells.length - 1] : above.querySelector('code') || above;
      if (!target) return false;
      if (!block.textContent.replace(/\u200b/g, '').trim() && !block.querySelector('img, .md-island')) {
        block.remove();
        edit();
      }
      placeCaret(target);
      return true;
    }

    function onKeydown(event) {
      event.stopPropagation();
      const img = event.target.closest?.('img.md-note-image');
      if (img && img.hasAttribute('data-md-image') && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault();
        if (editableImage(img)) openImageLinkMenu(img, img);
        return;
      }
      if (img && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault();
        if (editableImage(img)) openImageMenu(img, null, { keyboard: true });
        return;
      }
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
      if (mod && !event.shiftKey && !event.altKey && (event.key === 'b' || event.key === 'i' || event.key === 'u')) {
        event.preventDefault();
        command({ b: 'bold', i: 'italic', u: 'underline' }[event.key]);
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
      if (mod && event.shiftKey && !event.altKey && event.key.toLowerCase() === 'h') {
        event.preventDefault();
        toggleHighlight();
        edit();
        return;
      }
      const island = event.target.closest?.('.md-island');
      if (island && body.contains(island) && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault();
        openIslandMenu(island);
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

    function insertPlainText(text) {
      if (text && !pasteLines(text)) command('insertText', text);
      edit();
    }

    // Several lines go in as one change to the page. The browser's insertText
    // splits a paragraph per line and restyles the whole page after each
    // split (the app's :has() rules), so 100 lines froze the tab for seconds.
    // The lines split the block the way Enter does: the text after the caret
    // moves to the last line, a task line makes new tasks. A caret in code, a
    // table or an island is left to the browser.
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
    const blank = node => !node.textContent && !node.querySelector('img, br, .md-island');
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
      if (!block || !/^(P|DIV|H[1-6]|LI)$/.test(block.nodeName) || block.closest('pre, table, .md-island') || !container.contains(startNode)) {
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
      if (!tail.textContent && !tail.querySelector('img, .md-island')) tail.replaceChildren();
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
    // the serializer would flatten is not imported: paste is text only. An
    // image file is uploaded and inserted like the toolbar's image button.
    // Markdown copied from a note (see copyMarkdown) pastes back rendered, so
    // a cut island or link moves as itself. It goes through the same renderer
    // as any note.
    function insertMarkdown(markdown) {
      const sel = selection();
      if (!sel || !sel.rangeCount || !body.contains(sel.anchorNode)) return false;
      const nodes = renderedNodes(markdown);
      if (!nodes.length) return false;
      const range = sel.getRangeAt(0);
      range.deleteContents();
      const fragment = document.createDocumentFragment();
      nodes.forEach(node => fragment.appendChild(node));
      const last = fragment.lastChild;
      range.insertNode(fragment);
      const after = document.createRange();
      after.setStartAfter(last);
      after.collapse(true);
      sel.removeAllRanges();
      sel.addRange(after);
      updateEmpty();
      edit();
      decorate(body);
      return true;
    }
    listen(body, 'paste', event => {
      event.preventDefault();
      if (!body.isContentEditable) return;
      const clipboard = event.clipboardData || window.clipboardData;
      const files = imageFiles(clipboard);
      if (files.length) { uploadFiles(files, caretAnchor()); return; }
      const markdown = clipboard?.getData(NOTE_MARKDOWN_MIME);
      if (markdown && insertMarkdown(markdown)) return;
      insertPlainText(clipboard?.getData('text/plain') || '');
    });
    function copyMarkdown(event, cut) {
      const sel = selection();
      if (!sel || sel.isCollapsed || !sel.rangeCount || !body.contains(sel.anchorNode) || !event.clipboardData) return;
      const fragment = sel.getRangeAt(0).cloneContents();
      const { lines, lossy } = serializeNodes(fragment.childNodes);
      if (lossy) return;
      event.preventDefault();
      event.clipboardData.setData('text/plain', sel.toString());
      event.clipboardData.setData(NOTE_MARKDOWN_MIME, lines.join('\n'));
      if (cut && body.isContentEditable) { command('delete'); edit(); }
    }
    listen(body, 'copy', event => copyMarkdown(event, false));
    listen(body, 'cut', event => copyMarkdown(event, true));
    listen(body, 'dragover', event => {
      if (Array.from(event.dataTransfer?.types || []).includes('Files')) event.preventDefault();
    });
    listen(body, 'drop', event => {
      event.preventDefault();
      if (!body.isContentEditable) return;
      const range = document.caretRangeFromPoint?.(event.clientX, event.clientY);
      if (range) { const sel = selection(); sel.removeAllRanges(); sel.addRange(range); }
      const files = imageFiles(event.dataTransfer);
      if (files.length) { uploadFiles(files, caretAnchor()); return; }
      insertPlainText(event.dataTransfer?.getData('text/plain') || '');
    });
    // Tapping a checkbox or an image must not focus the body, which would
    // open the on-screen keyboard on a phone.
    bindTap(body, '.md-task-box, img.md-note-image, .md-island, .note-image-missing', (target, at) => {
      if (target.matches('.md-task-box')) onToggle(target.closest('li.md-task'));
      else if (target.matches('.md-island')) openIslandMenu(target, at);
      else if (target.matches('.note-image-missing')) { if (missingImage(target)) openAnyImageMenu(missingImage(target), at); }
      else if (editableImage(target)) openAnyImageMenu(target, at);
    }, listen);
    // Same policy as the link popover: a target it would refuse never opens.
    const openLink = link => {
      if (normaliseLinkUrl(link.getAttribute('href'))) window.open(link.href, '_blank', 'noopener,noreferrer');
    };
    listen(body, 'click', event => {
      const box = event.target.closest('.md-task-box');
      if (box) { event.preventDefault(); onToggle(box.closest('li.md-task')); return; }
      const img = event.target.closest('img.md-note-image') || missingImage(event.target.closest('.note-image-missing'));
      if (img && editableImage(img)) { event.preventDefault(); openAnyImageMenu(img, { x: event.clientX, y: event.clientY }); return; }
      const island = event.target.closest('.md-island');
      if (island && body.contains(island)) { event.preventDefault(); openIslandMenu(island, { x: event.clientX, y: event.clientY }); return; }
      const link = event.target.closest('a[href]');
      if (!link || !body.contains(link)) return;
      // An image shown as a link (one from elsewhere) is not loaded unless
      // asked: only Cmd or Ctrl+click opens it, a plain click puts the caret in it.
      if (link.hasAttribute('data-md-image')) {
        if (event.metaKey || event.ctrlKey) { event.preventDefault(); openLink(link); }
        return;
      }
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
    bindTap(body, 'a[href]:not([data-md-image])', openLink, listen, { holdMouse: false, maxMs: LINK_TAP_MAX_MS });
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

    return {
      setContent,
      /** The selection, to put back with restoreCaret() once setContent() has drawn the body again. */
      saveCaret,
      restoreCaret,
      getMarkdown,
      read,
      setMode,
      /** The toolbar's Insert image, for callers outside the window (quick-add's photo button). */
      pickImage: () => runAction('image'),
      flipTask,
      taskItems,
      decorate: () => { if (mode === 'rich') decorate(body); },
      get mode() { return mode; },
      get edited() { return edited; },
      focusEnd() { if (mode === 'source') { source.focus(); return; } body.focus(); placeCaret(body); },
      destroy() { disposers.splice(0).forEach(dispose => dispose()); document.querySelectorAll('.note-link-menu').forEach(dismissOrRemove); imageMenu.closeAll(); },
    };
  }

  return { attach };
}
