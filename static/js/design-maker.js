/**
 * Design Maker — dedicated, Claude-Design-style design surface.
 *
 * A full-screen overlay (NOT the document editor) where a project groups
 * pages; each page is generated/edited by an LLM and previewed live in a
 * device-framed, sandboxed iframe. This replaces the in-editor design path,
 * whose textarea/autosave clobbered generated HTML and whose preview was
 * clipped to 820px.
 *
 * Backend contract (routes/design_routes.py, prefix /api):
 *   GET    /design/projects                 -> { projects:[...] }
 *   POST   /design/project   (name,prompt,model) -> { project_id, page_id?, job_id? }
 *   GET    /design/project/{pid}            -> full project (pages w/ content, comments, assets)
 *   PATCH  /design/project/{pid}            (name,settings,cover_page_id,archived)
 *   DELETE /design/project/{pid}
 *   POST   /design/project/{pid}/page (prompt,title,model) -> { page_id, job_id }
 *   POST   /design/page/{id}/generate (prompt,model,mode)  -> { job_id, page_id }
 *   PATCH  /design/page/{id} (title,order_index)
 *   DELETE /design/page/{id}
 *   POST   /design/page/{id}/comment (body,anchor) -> comment
 *   PATCH  /design/comment/{cid} (body,resolved)
 *   DELETE /design/comment/{cid}
 *   GET    /design/project/{pid}/export     -> zip
 *   GET    /design/job/{job_id}/stream      -> SSE { status, final, version?, error? }
 *
 * Generation runs as a background job streamed over SSE, mirroring Deep
 * Research, so long LLM calls never block the UI.
 */

const API_BASE = window.location.origin;

// The canvas preview loads each page from GET /api/design/page/{id}/render via
// an <iframe src> (NOT srcdoc): srcdoc inherits the SPA's CSP, which would block
// the design's Tailwind/Google-Fonts CDNs. Loading by URL lets the endpoint's
// own design-friendly CSP (set in core/middleware.py) apply instead, while
// sandbox="allow-scripts" keeps the document opaque-origin.

// Device presets — width drives the bezel; the canvas auto-fits by scaling.
const _DEVICES = {
  mobile:  { w: 375,  h: 812,  label: 'Mobile' },
  tablet:  { w: 768,  h: 1024, label: 'Tablet' },
  desktop: { w: 1440, h: 900,  label: 'Desktop' },
  fit:     { w: 0,    h: 0,    label: 'Ajustar' },  // w=0 -> fill canvas width
};

// ── Inline SVG icons (no Unicode emoji — CONTRIBUTING rule) ──
const ICON = {
  design: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="13.5" cy="6.5" r="2.5"/><circle cx="6" cy="12" r="2.5"/><circle cx="17" cy="14" r="2.5"/><path d="M12 22a10 10 0 1 1 10-10c0 1.66-1.34 3-3 3h-1a2 2 0 0 0-2 2 2 2 0 0 1-2 2"/></svg>',
  plus:   '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  close:  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>',
  back:   '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>',
  trash:  '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  regen:  '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M3 21v-5h5"/></svg>',
  download:'<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
  send:   '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>',
  pin:    '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>',
  tweak:  '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>',
  agent:  '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 4.6L18.5 9l-4.6 1.9L12 15l-1.9-4.1L5.5 9l4.6-1.4z"/><path d="M19 14l.8 2L22 17l-2.2.9L19 20l-.8-2.1L16 17l2.2-1z"/></svg>',
  check:  '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
  edit:   '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
  attach: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>',
  link:   '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
  image:  '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
  doc:    '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
  alignL: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="15" y2="12"/><line x1="3" y1="18" x2="18" y2="18"/></svg>',
  alignC: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="6" y1="12" x2="18" y2="12"/><line x1="4" y1="18" x2="20" y2="18"/></svg>',
  alignR: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="9" y1="12" x2="21" y2="12"/><line x1="6" y1="18" x2="21" y2="18"/></svg>',
};

// ── Module state ──
let _initialized = false;
let _open = false;
let _projects = [];          // library list
let _project = null;         // currently open project (full dict)
let _activePageId = null;
let _device = 'fit';
let _zoom = 1;               // multiplied on top of fit-scale
let _inspectorTab = 'agent'; // default to the live agent transcript
let _markupMode = false;     // canvas markup (anchored-comment) mode
let _tweaksOn = false;       // design's Tweaks panel visible
let _editMode = false;       // inline text editing (contentEditable) in the canvas
let _editSel = null;         // selected element's reported styles+ccId (edit mode property panel)
let _editingPageId = null;   // page captured when edit mode turned on (save target)
let _chat = [];              // conversation turns: [{ prompt, events:[] }] (in-memory per session)
let _pendingPromptLabel = '';// label for the next turn's user bubble
let _turnStartMs = 0;        // when the live turn started (for the elapsed timer)
let _progressTimer = null;   // 1s ticker that keeps the live turn feeling alive
let _progressTick = 0;       // ticks elapsed in the live turn
// Rotating sub-messages shown during the long single-shot build, so the chat
// never sits on one frozen line.
const _BUILD_MSGS = ['Construindo o design…', 'Compondo o layout e a hierarquia…', 'Escrevendo o HTML e os estilos…', 'Refinando a tipografia…', 'Ajustando cores e espaçamento…', 'Finalizando os detalhes…'];

function _startProgressTicker() {
  _turnStartMs = Date.now();
  _progressTick = 0;
  if (_progressTimer) clearInterval(_progressTimer);
  _progressTimer = setInterval(() => { _progressTick++; _renderTranscriptIfActive(); }, 1000);
}
function _stopProgressTicker() {
  if (_progressTimer) { clearInterval(_progressTimer); _progressTimer = null; }
}
function _fmtElapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}
let _canvasIframe = null;    // the live preview iframe (for postMessage bridge)
let _kind = '';              // start-screen design type (prototype/slides/document/wireframe)
let _dsId = '';              // selected design system id for the next generation
let _designSystems = [];     // cached design systems
let _templates = [];         // cached templates
let _libTab = 'projects';    // start-screen tab: projects | systems | templates
// Attachments — transient context for the NEXT generation request (not stored).
// Items: { kind:'image'|'text'|'url', name, dataUrl? | text? | url? }.
let _attachments = [];
const _ATTACH_MAX_IMAGES = 4;
const _ATTACH_TEXT_CHARS = 20000;

// Design types offered on the start screen (Animation intentionally excluded).
const _KINDS = [
  { id: 'prototype', label: 'Protótipo', device: 'fit', icon: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="14" rx="2"/><path d="M3 8h18"/><circle cx="6" cy="6" r="0.6" fill="currentColor"/></svg>', dir: 'Formato: protótipo de produto interativo (telas/app navegáveis, estados reais, interações que respondem ao clique).' },
  { id: 'slides', label: 'Slides', device: 'desktop', icon: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></svg>', dir: 'Formato: apresentação de slides (deck 16:9, um slide por seção, navegação por teclado/clique entre slides, escala para qualquer viewport, contador de slides).' },
  { id: 'document', label: 'Documento', device: 'fit', icon: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2h9l5 5v15H6z"/><path d="M15 2v5h5"/><path d="M9 13h7M9 17h7M9 9h3"/></svg>', dir: 'Formato: documento/relatório (layout editorial de leitura, largura de coluna confortável, hierarquia tipográfica forte, sem chrome de site).' },
  { id: 'wireframe', label: 'Wireframe', device: 'fit', icon: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 9v12"/></svg>', dir: 'Formato: wireframe de baixa fidelidade (estrutura, hierarquia e fluxo em tons de cinza; sem cor de marca nem imagens finais — use blocos e placeholders rotulados).' },
];
function _kindDirective(kind) {
  const k = _KINDS.find((x) => x.id === kind);
  return k ? (k.dir + '\n\n') : '';
}
let _models = null;          // cached /api/models items
let _stream = { es: null, jobId: null };
let _onDocKeydown = null;

// ---------------------------------------------------------------------------
// Tiny API helper. Backend uses Form(...) params, so writes send FormData.
// ---------------------------------------------------------------------------
async function _api(method, path, form) {
  const opts = { method, credentials: 'same-origin' };
  if (form) {
    const fd = new FormData();
    Object.entries(form).forEach(([k, v]) => {
      if (v !== undefined && v !== null) fd.append(k, v);
    });
    opts.body = fd;
  }
  const res = await fetch(API_BASE + '/api' + path, opts);
  let body = null;
  try { body = await res.json(); } catch (_) { body = null; }
  if (!res.ok) {
    const msg = (body && (body.error || body.detail)) || ('HTTP ' + res.status);
    throw new Error(msg);
  }
  return body;
}

function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ---------------------------------------------------------------------------
// Attachments — transient images/text/URLs folded into the next generation.
// The composer (start screen + editor footer) shares this control via a unique
// `prefix` so both surfaces can attach. No persistent storage (v1).
// ---------------------------------------------------------------------------
function _attachBarHTML(prefix) {
  return ''
    + '<input type="file" id="' + prefix + '-file" multiple accept="image/*,.txt,.md,.markdown,.csv,.json,.html" style="display:none" />'
    + '<button type="button" class="dm-iconbtn dm-bare" id="' + prefix + '-attach-file" title="Anexar imagem ou arquivo de texto">' + ICON.attach + '</button>'
    + '<button type="button" class="dm-iconbtn dm-bare" id="' + prefix + '-attach-url" title="Anexar URL (ex. GitHub)">' + ICON.link + '</button>';
}

function _wireAttachBar(prefix) {
  const file = document.getElementById(prefix + '-file');
  const fb = document.getElementById(prefix + '-attach-file');
  const ub = document.getElementById(prefix + '-attach-url');
  if (fb && file) fb.addEventListener('click', () => file.click());
  if (file) file.addEventListener('change', () => { _addFiles(file.files); file.value = ''; });
  if (ub) ub.addEventListener('click', _promptAttachUrl);
}

function _addFiles(fileList) {
  Array.prototype.slice.call(fileList || []).forEach((f) => {
    const reader = new FileReader();
    reader.onerror = () => { _setStatus('Falha ao ler ' + (f.name || 'arquivo'), false, true); };
    if (/^image\//.test(f.type)) {
      // Match the backend cap (~4MB/image) and skip extras over the image limit.
      if (f.size > 4 * 1024 * 1024) { alert('Imagem "' + (f.name || '') + '" é grande demais (máx 4MB).'); return; }
      if (_attachments.filter((a) => a.kind === 'image').length >= _ATTACH_MAX_IMAGES) { alert('Máximo de ' + _ATTACH_MAX_IMAGES + ' imagens.'); return; }
      reader.onload = () => {
        _attachments.push({ kind: 'image', name: f.name || 'imagem', dataUrl: String(reader.result || '') });
        _renderAttachChips();
      };
      reader.readAsDataURL(f);
    } else {
      reader.onload = () => {
        _attachments.push({ kind: 'text', name: f.name || 'texto', text: String(reader.result || '').slice(0, _ATTACH_TEXT_CHARS) });
        _renderAttachChips();
      };
      reader.readAsText(f);
    }
  });
}

function _promptAttachUrl() {
  const u = (window.prompt('Cole uma URL para usar como referência (ex. https://github.com/.../README.md):', '') || '').trim();
  if (!u) return;
  if (!/^https?:\/\//i.test(u)) { alert('A URL precisa começar com http:// ou https://'); return; }
  _attachments.push({ kind: 'url', name: u.replace(/^https?:\/\//i, '').slice(0, 60), url: u });
  _renderAttachChips();
}

function _clearAttachments() { _attachments = []; _renderAttachChips(); }

// Build the FormData fields the backend expects from the current attachments.
function _attachmentForm() {
  const out = {};
  const images = _attachments.filter((a) => a.kind === 'image' && a.dataUrl)
    .map((a) => a.dataUrl).slice(0, _ATTACH_MAX_IMAGES);
  if (images.length) out.images = JSON.stringify(images);
  const texts = _attachments.filter((a) => a.kind === 'text' && a.text)
    .map((a) => '### ' + (a.name || 'arquivo') + '\n' + a.text);
  if (texts.length) out.reference_text = texts.join('\n\n');
  const urls = _attachments.filter((a) => a.kind === 'url' && a.url).map((a) => a.url);
  if (urls.length) out.attachment_urls = JSON.stringify(urls);
  return out;
}

// Render attachment chips into every visible chips container (only one composer
// is mounted at a time, so this fills whichever is present).
function _renderAttachChips() {
  document.querySelectorAll('.dm-attach-chips').forEach((box) => {
    if (!_attachments.length) { box.innerHTML = ''; box.style.display = 'none'; return; }
    box.style.display = 'flex';
    box.innerHTML = _attachments.map((a, i) => {
      const ic = a.kind === 'image' ? ICON.image : (a.kind === 'url' ? ICON.link : ICON.doc);
      return '<span class="dm-chip-att">' + ic
        + '<span class="dm-chip-name" title="' + _esc(a.name) + '">' + _esc(a.name) + '</span>'
        + '<button type="button" class="dm-chip-x" data-i="' + i + '" title="Remover">' + ICON.close + '</button></span>';
    }).join('');
    box.querySelectorAll('.dm-chip-x').forEach((b) => b.addEventListener('click', () => {
      _attachments.splice(parseInt(b.dataset.i, 10), 1);
      _renderAttachChips();
    }));
  });
}

// ---------------------------------------------------------------------------
// Style — injected once. Uses theme CSS vars so it tracks light/dark and the
// F7 visual pass can lift this verbatim into static/style.css.
// ---------------------------------------------------------------------------
function _injectStyles() {
  if (document.getElementById('dm-styles')) return;
  const css = `
  /* The global .modal base sets pointer-events:none (so its backdrop doesn't
     block the chat) and align-items/justify-content:center (to center small
     modals). This is a full-screen surface, so re-enable pointer events and
     override the centering so .dm-shell stretches to the full viewport instead
     of collapsing to content height. */
  .dm-overlay { position: fixed; inset: 0; z-index: 9000; background: var(--bg); display: flex; align-items: stretch; justify-content: flex-start; pointer-events: auto; }
  .dm-overlay.hidden { display: none; }
  /* .dm-shell carries .modal-content so the global "click outside content
     dismisses the modal" handler (app.js) treats the whole surface as content
     and never auto-closes it. Override the .modal-content card styling (fixed
     width / max-height / padding / border / radius / shadow / scroll) so it
     fills the viewport instead. */
  .dm-overlay .modal-content.dm-shell { display: flex; flex-direction: column; flex: 1; min-width: 0; min-height: 0; height: 100%; width: 100%; max-width: none; max-height: none; padding: 0; border: none; border-radius: 0; box-shadow: none; overflow: hidden; background: var(--bg); animation: none; }
  /* Also carries .modal-header (so modalManager injects the minimize button);
     neutralize that base's space-between / margin / grab cursor. */
  .dm-topbar { display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-bottom: 1px solid var(--border); flex-shrink: 0; justify-content: flex-start; margin-bottom: 0; cursor: default; }
  .dm-topbar .dm-title { display: inline-flex; align-items: center; gap: 7px; font-weight: 600; font-size: 14px; color: var(--fg); }
  .dm-topbar .dm-proj-name { font-size: 13px; opacity: 0.85; background: transparent; border: 1px solid transparent; color: var(--fg); border-radius: 6px; padding: 3px 6px; min-width: 120px; }
  .dm-topbar .dm-proj-name:focus { border-color: var(--border); outline: none; }
  .dm-spacer { flex: 1; }
  .dm-iconbtn { display: inline-flex; align-items: center; justify-content: center; gap: 5px; background: transparent; border: 1px solid var(--border); color: var(--fg); border-radius: 7px; padding: 5px 9px; cursor: pointer; font-size: 12px; font-family: inherit; }
  .dm-iconbtn:hover { background: var(--hover-bg, rgba(255,255,255,0.06)); }
  .dm-iconbtn.dm-primary { background: var(--accent, var(--red)); border-color: var(--accent, var(--red)); color: #fff; }
  .dm-iconbtn.dm-primary:hover { filter: brightness(1.08); }
  .dm-iconbtn.dm-bare { border-color: transparent; padding: 5px 7px; }
  .dm-iconbtn:disabled { opacity: 0.45; cursor: default; }
  .dm-main { flex: 1; display: flex; min-height: 0; min-width: 0; position: relative; }
  .dm-inspector-toggle { display: none; }

  /* Library view */
  .dm-library { flex: 1; overflow: auto; padding: 22px; }
  .dm-lib-head { display: flex; align-items: center; gap: 12px; margin-bottom: 18px; }
  .dm-lib-head h2 { margin: 0; font-size: 18px; color: var(--fg); }
  /* Start screen ("What will you design today?") */
  .dm-home { max-width: 760px; margin: 10px auto 26px; text-align: center; }
  .dm-home-h { font-size: 28px; font-weight: 600; color: var(--fg); margin: 18px 0 18px; }
  .dm-home-composer { border: 1px solid var(--border); border-radius: 14px; background: var(--panel); padding: 12px; text-align: left; box-shadow: 0 6px 24px rgba(0,0,0,0.2); }
  .dm-home-composer textarea { width: 100%; box-sizing: border-box; resize: none; min-height: 56px; max-height: 160px; background: transparent; color: var(--fg); border: none; font-family: inherit; font-size: 15px; }
  .dm-home-composer textarea:focus { outline: none; }
  .dm-home-row { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
  /* Attachment chips (transient generation context: images / text / URLs) */
  .dm-attach-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
  .dm-chip-att { display: inline-flex; align-items: center; gap: 5px; max-width: 220px; border: 1px solid var(--border); background: var(--panel); color: var(--fg); border-radius: 14px; padding: 3px 5px 3px 9px; font-size: 11.5px; }
  .dm-chip-att svg { opacity: 0.7; flex-shrink: 0; }
  .dm-chip-att .dm-chip-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .dm-chip-x { display: inline-flex; align-items: center; justify-content: center; background: transparent; border: none; color: var(--color-muted, #888); cursor: pointer; padding: 1px; border-radius: 50%; }
  .dm-chip-x:hover { color: var(--red); }
  .dm-home-sub { font-size: 12px; opacity: 0.6; margin: 18px 0 10px; }
  .dm-kinds { display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; }
  /* height:auto + line-height override the global button height:32px rule,
     which otherwise clips the icon and pushes the label out of the card. */
  .dm-kind { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; width: 116px; height: auto; min-height: 84px; padding: 16px 8px; box-sizing: border-box; line-height: 1.2; border: 1px solid var(--border); border-radius: 12px; background: var(--panel); color: var(--fg); cursor: pointer; font-family: inherit; font-size: 12.5px; }
  .dm-kind:hover { border-color: var(--accent, var(--red)); }
  .dm-kind.sel { border-color: var(--accent, var(--red)); background: color-mix(in srgb, var(--accent, var(--red)) 14%, transparent); }
  .dm-kind svg { opacity: 0.8; }
  .dm-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }
  /* Library tabs (Projetos / Design systems / Templates) */
  .dm-lib-tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin-bottom: 16px; }
  .dm-lib-tabs button { background: transparent; border: none; border-bottom: 2px solid transparent; color: var(--fg); opacity: 0.6; padding: 8px 12px; cursor: pointer; font-family: inherit; font-size: 13px; height: auto; }
  .dm-lib-tabs button.active { opacity: 1; border-bottom-color: var(--accent, var(--red)); }
  .dm-list-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
  .dm-list-head span { flex: 1; font-size: 12px; opacity: 0.65; }
  .dm-rows { display: flex; flex-direction: column; gap: 6px; }
  .dm-row { display: flex; align-items: center; gap: 8px; border: 1px solid var(--border); border-radius: 9px; padding: 9px 11px; font-size: 13px; color: var(--fg); }
  .dm-row .grow { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .dm-ds-edit-form { max-width: 640px; }
  .dm-fld { display: flex; flex-direction: column; gap: 5px; font-size: 12px; opacity: 0.85; margin-bottom: 12px; }
  .dm-fld input, .dm-fld textarea { background: var(--hl-bg, #1e2228); color: var(--fg); border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; font-family: inherit; font-size: 13px; box-sizing: border-box; }
  .dm-fld textarea { resize: vertical; line-height: 1.5; }
  .dm-card { border: 1px solid var(--border); border-radius: 10px; overflow: hidden; cursor: pointer; background: var(--panel); display: flex; flex-direction: column; }
  .dm-card:hover { border-color: var(--accent, var(--red)); }
  .dm-card-thumb { height: 120px; background: var(--hl-bg, #1e2228); display: flex; align-items: center; justify-content: center; color: var(--color-muted, #888); }
  .dm-card-meta { padding: 9px 11px; display: flex; align-items: center; gap: 8px; }
  .dm-card-meta .grow { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; color: var(--fg); }
  .dm-card-meta .dm-sub { font-size: 11px; opacity: 0.55; }
  .dm-new-card { display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 8px; min-height: 170px; border-style: dashed; color: var(--color-muted, #888); }

  /* Editor columns */
  .dm-col-pages { width: 200px; flex-shrink: 0; border-right: 1px solid var(--border); display: flex; flex-direction: column; min-height: 0; }
  .dm-col-head { display: flex; align-items: center; padding: 9px 11px; font-size: 11px; letter-spacing: 0.04em; text-transform: uppercase; opacity: 0.6; color: var(--fg); }
  .dm-pages-list { flex: 1; overflow: auto; padding: 4px 6px; }
  .dm-page-row { display: flex; align-items: center; gap: 6px; padding: 7px 8px; border-radius: 7px; cursor: pointer; font-size: 13px; color: var(--fg); }
  .dm-page-row:hover { background: var(--hover-bg, rgba(255,255,255,0.05)); }
  .dm-page-row.active { background: color-mix(in srgb, var(--accent, var(--red)) 22%, transparent); }
  .dm-page-row .grow { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .dm-page-row .dm-del { opacity: 0; color: var(--color-muted, #888); }
  .dm-page-row:hover .dm-del { opacity: 0.7; }
  .dm-page-row .dm-del:hover { color: var(--red); opacity: 1; }

  /* Canvas */
  .dm-col-canvas { flex: 1; display: flex; flex-direction: column; min-width: 0; min-height: 0; }
  .dm-canvas-bar { display: flex; align-items: center; gap: 6px; padding: 7px 10px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
  .dm-seg { display: inline-flex; border: 1px solid var(--border); border-radius: 7px; overflow: hidden; }
  .dm-seg button { background: transparent; border: none; color: var(--fg); padding: 4px 9px; cursor: pointer; font-size: 12px; font-family: inherit; }
  .dm-seg button.active { background: var(--accent, var(--red)); color: #fff; }
  .dm-zoom-label { font-size: 11px; opacity: 0.6; min-width: 38px; text-align: center; }
  .dm-canvas-scroll { position: relative; flex: 1; overflow: auto; background: repeating-conic-gradient(var(--hl-bg, #1e2228) 0% 25%, transparent 0% 50%) 50% / 22px 22px; display: flex; align-items: flex-start; justify-content: center; padding: 22px; }
  /* Inline comment bubble (markup) — anchored at the picked element. */
  .dm-comment-pop { position: absolute; z-index: 12; background: var(--panel); border: 1px solid var(--border); border-radius: 10px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); padding: 10px; }
  .dm-cp-label { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--color-muted, #888); margin-bottom: 6px; }
  .dm-cp-label span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .dm-cp-text { width: 100%; box-sizing: border-box; resize: none; min-height: 54px; background: var(--hl-bg, #1e2228); color: var(--fg); border: 1px solid var(--border); border-radius: 7px; padding: 7px; font-family: inherit; font-size: 12px; }
  .dm-cp-text:focus { outline: none; border-color: var(--accent, var(--red)); }
  .dm-cp-actions { display: flex; justify-content: flex-end; gap: 6px; margin-top: 7px; }
  .dm-frame { position: relative; background: #fff; box-shadow: 0 8px 40px rgba(0,0,0,0.35); border-radius: 6px; overflow: hidden; transform-origin: top center; flex-shrink: 0; }
  .dm-frame iframe { border: 0; display: block; position: absolute; inset: 0; width: 100%; height: 100%; background: #fff; }
  .dm-empty { color: var(--color-muted, #888); text-align: center; max-width: 320px; margin: auto; font-size: 13px; line-height: 1.5; }

  /* Markup layer — sits above the iframe. pointer-events are off by default so
     the preview is interactive/scrollable; markup mode turns them on to capture
     a click and drop an anchored pin. Pins stay clickable regardless. */
  .dm-markup { position: absolute; inset: 0; z-index: 2; pointer-events: none; }
  .dm-markup.armed { pointer-events: auto; cursor: crosshair; background: rgba(0,0,0,0.02); }
  .dm-pin { position: absolute; width: 24px; height: 24px; margin: -12px 0 0 -12px; border-radius: 50% 50% 50% 2px; background: var(--accent, var(--red)); color: #fff; font-size: 12px; font-weight: 700; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 8px rgba(0,0,0,0.4); pointer-events: auto; cursor: pointer; border: 2px solid #fff; transform-origin: center; }
  .dm-pin.resolved { background: var(--color-muted, #888); opacity: 0.6; }
  .dm-markup-hint { position: absolute; top: 10px; left: 50%; transform: translateX(-50%); background: var(--panel); color: var(--fg); border: 1px solid var(--border); border-radius: 16px; padding: 5px 12px; font-size: 12px; pointer-events: none; z-index: 3; }
  .dm-iconbtn.dm-toggle-on { background: var(--accent, var(--red)); border-color: var(--accent, var(--red)); color: #fff; }
  .dm-comment.flash { animation: dm-flash 1.4s ease; }
  @keyframes dm-flash { 0%, 60% { box-shadow: 0 0 0 2px var(--accent, var(--red)); } 100% { box-shadow: none; } }
  .dm-version { display: flex; align-items: center; gap: 8px; border: 1px solid var(--border); border-radius: 8px; padding: 7px 9px; margin-bottom: 7px; font-size: 12px; color: var(--fg); }
  .dm-version .grow { flex: 1; min-width: 0; }
  .dm-version .dm-vsub { font-size: 11px; opacity: 0.6; }
  .dm-version.current { border-color: var(--accent, var(--red)); }

  /* Prompt footer */
  .dm-prompt { border-top: 1px solid var(--border); padding: 9px 10px; display: flex; flex-direction: column; gap: 7px; flex-shrink: 0; }
  .dm-prompt-row { display: flex; gap: 8px; align-items: flex-end; }
  .dm-prompt textarea { flex: 1; resize: none; min-height: 38px; max-height: 130px; background: var(--hl-bg, #1e2228); color: var(--fg); border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; font-family: inherit; font-size: 13px; }
  .dm-prompt textarea:focus { outline: none; border-color: var(--accent, var(--red)); }
  .dm-prompt-ctl { display: flex; align-items: center; gap: 8px; }
  .dm-model-select { background: var(--select-bg, var(--bg)); color: var(--select-fg, var(--fg)); border: 1px solid var(--border); border-radius: 7px; padding: 4px 8px; font-size: 12px; max-width: 230px; font-family: inherit; }
  .dm-status { font-size: 12px; opacity: 0.75; display: inline-flex; align-items: center; gap: 6px; }
  .dm-status.err { color: var(--red); opacity: 1; }
  .dm-spin { width: 12px; height: 12px; border: 2px solid var(--border); border-top-color: var(--accent, var(--red)); border-radius: 50%; animation: dm-spin 0.7s linear infinite; }
  @keyframes dm-spin { to { transform: rotate(360deg); } }

  /* Inspector */
  .dm-col-inspector { width: 270px; flex-shrink: 0; border-left: 1px solid var(--border); display: flex; flex-direction: column; min-height: 0; }
  .dm-tabs { display: flex; border-bottom: 1px solid var(--border); flex-shrink: 0; }
  .dm-tabs button { flex: 1; background: transparent; border: none; border-bottom: 2px solid transparent; color: var(--fg); opacity: 0.6; padding: 9px 4px; cursor: pointer; font-size: 12px; font-family: inherit; }
  .dm-tabs button.active { opacity: 1; border-bottom-color: var(--accent, var(--red)); }
  .dm-tab-body { flex: 1; overflow: auto; padding: 10px; }
  .dm-comment { border: 1px solid var(--border); border-radius: 8px; padding: 8px 9px; margin-bottom: 8px; font-size: 12px; color: var(--fg); }
  .dm-comment.resolved { opacity: 0.5; }
  .dm-comment .dm-c-body { white-space: pre-wrap; word-break: break-word; }
  .dm-comment .dm-c-actions { display: flex; gap: 8px; margin-top: 6px; }
  .dm-comment .dm-c-actions button { background: transparent; border: none; color: var(--color-muted, #888); cursor: pointer; font-size: 11px; padding: 0; font-family: inherit; }
  .dm-comment .dm-c-actions button:hover { color: var(--fg); }
  .dm-add-comment { display: flex; flex-direction: column; gap: 6px; margin-top: 6px; }
  .dm-add-comment textarea { resize: none; min-height: 48px; background: var(--hl-bg, #1e2228); color: var(--fg); border: 1px solid var(--border); border-radius: 7px; padding: 7px; font-family: inherit; font-size: 12px; }
  .dm-muted { color: var(--color-muted, #888); font-size: 12px; line-height: 1.5; }

  /* Chat turns (prompts + agent actions + outputs, accumulated) */
  .dm-turn { border-bottom: 1px solid var(--border); padding-bottom: 12px; margin-bottom: 12px; }
  .dm-turn:last-child { border-bottom: none; }
  .dm-turn-user { background: color-mix(in srgb, var(--accent, var(--red)) 14%, transparent); border: 1px solid var(--border); border-radius: 9px; padding: 7px 10px; font-size: 12.5px; color: var(--fg); white-space: pre-wrap; word-break: break-word; margin-bottom: 8px; }
  .dm-turn-out { font-size: 11px; color: var(--green, #50fa7b); margin-top: 8px; opacity: 0.85; }
  /* Agent transcript (ações / raciocínio / tarefas) */
  .dm-tx-phase { display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--fg); background: color-mix(in srgb, var(--accent, var(--red)) 12%, transparent); border-radius: 7px; padding: 7px 9px; margin-bottom: 10px; animation: dm-pulse 1.8s ease-in-out infinite; }
  .dm-tx-phase .grow { flex: 1; min-width: 0; }
  .dm-tx-time { font-variant-numeric: tabular-nums; opacity: 0.7; font-size: 11px; }
  @keyframes dm-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.78; } }
  .dm-spin-sm { width: 10px; height: 10px; border-width: 2px; }
  .dm-tx-todo.doing { opacity: 1; color: var(--fg); }
  .dm-tx-todo.doing .dm-spin { flex-shrink: 0; margin-top: 1px; }
  .dm-tx-h { font-size: 11px; letter-spacing: 0.04em; text-transform: uppercase; opacity: 0.55; margin: 12px 0 5px; color: var(--fg); }
  .dm-tx-brief { font-size: 12.5px; line-height: 1.5; color: var(--fg); }
  .dm-tx-sys { font-size: 11px; opacity: 0.6; margin-top: 5px; }
  .dm-tx-todos { display: flex; flex-direction: column; gap: 4px; }
  .dm-tx-todo { display: flex; align-items: flex-start; gap: 7px; font-size: 12px; color: var(--fg); opacity: 0.85; }
  .dm-tx-todo.done { opacity: 0.55; }
  .dm-tx-todo .dm-tx-dot { width: 12px; height: 12px; border: 1.5px solid var(--border); border-radius: 50%; flex-shrink: 0; margin-top: 1px; }
  .dm-tx-todo svg { color: var(--green, #50fa7b); flex-shrink: 0; margin-top: 1px; }

  /* Clarify card (shown in the canvas before a vague generation) */
  .dm-clarify { max-width: 460px; margin: auto; background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 18px; color: var(--fg); }
  .dm-clarify-h { font-size: 15px; font-weight: 600; margin-bottom: 14px; }
  .dm-clarify-q { margin-bottom: 14px; }
  .dm-clarify-label { font-size: 13px; margin-bottom: 7px; }
  .dm-clarify-opts { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
  .dm-chip { background: transparent; border: 1px solid var(--border); color: var(--fg); border-radius: 16px; padding: 4px 11px; font-size: 12px; cursor: pointer; font-family: inherit; }
  .dm-chip:hover { border-color: var(--accent, var(--red)); }
  .dm-chip.sel { background: var(--accent, var(--red)); border-color: var(--accent, var(--red)); color: #fff; }
  .dm-clarify-other { width: 100%; box-sizing: border-box; background: var(--hl-bg, #1e2228); color: var(--fg); border: 1px solid var(--border); border-radius: 7px; padding: 6px 9px; font-size: 12px; font-family: inherit; }
  .dm-clarify-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 6px; }

  /* Edit mode — per-element property panel (replaces the inspector tabs) */
  .dm-edit-panel { display: flex; flex-direction: column; gap: 4px; }
  .dm-ep-hint { margin-bottom: 8px; }
  .dm-ep-row { display: flex; flex-direction: column; gap: 6px; padding: 9px 0; border-bottom: 1px solid var(--border); }
  .dm-ep-row:last-of-type { border-bottom: none; }
  .dm-ep-row > label { font-size: 11px; letter-spacing: 0.03em; text-transform: uppercase; opacity: 0.6; color: var(--fg); }
  .dm-ep-size { display: flex; align-items: center; gap: 6px; }
  .dm-ep-size input { width: 64px; background: var(--hl-bg, #1e2228); color: var(--fg); border: 1px solid var(--border); border-radius: 7px; padding: 5px 8px; font-family: inherit; font-size: 13px; }
  .dm-ep-size input:focus { outline: none; border-color: var(--accent, var(--red)); }
  .dm-ep-unit { font-size: 11px; opacity: 0.55; }
  .dm-ep-swatches { display: flex; flex-wrap: wrap; gap: 6px; }
  .dm-ep-swatch { width: 22px; height: 22px; border-radius: 6px; border: 1px solid var(--border); cursor: pointer; padding: 0; }
  .dm-ep-swatch:hover { outline: 2px solid var(--accent, var(--red)); outline-offset: 1px; }
  .dm-ep-swatch:disabled { cursor: default; opacity: 0.4; }
  .dm-ep-hex { width: 110px; background: var(--hl-bg, #1e2228); color: var(--fg); border: 1px solid var(--border); border-radius: 7px; padding: 5px 8px; font-family: inherit; font-size: 12.5px; }
  .dm-ep-hex:focus { outline: none; border-color: var(--accent, var(--red)); }
  .dm-ep-align { align-self: flex-start; }
  .dm-ep-align button { display: inline-flex; align-items: center; justify-content: center; }
  .dm-ep-align button.active { background: var(--accent, var(--red)); color: #fff; }
  .dm-chip:disabled, .dm-model-select:disabled, .dm-iconbtn:disabled { opacity: 0.45; cursor: default; }
  .dm-ep-note { font-size: 11px; opacity: 0.5; line-height: 1.5; margin-top: 10px; color: var(--color-muted, #888); }

  @media (max-width: 900px) {
    .dm-col-pages { width: 130px; }
    /* Inspector becomes a slide-in drawer instead of vanishing, so comments /
       versions / files stay reachable on mobile. */
    .dm-col-inspector { position: absolute; top: 0; right: 0; bottom: 0; width: min(320px, 86vw); transform: translateX(102%); transition: transform 0.2s ease; z-index: 6; background: var(--bg); box-shadow: -4px 0 18px rgba(0,0,0,0.45); }
    .dm-overlay.dm-inspector-open .dm-col-inspector { transform: translateX(0); }
    .dm-inspector-toggle { display: inline-flex; }
  }
  `;
  const st = document.createElement('style');
  st.id = 'dm-styles';
  st.textContent = css;
  document.head.appendChild(st);
}

// ---------------------------------------------------------------------------
// Overlay construction (built once, kept in DOM, toggled via .hidden)
// ---------------------------------------------------------------------------
function _ensureOverlay() {
  let ov = document.getElementById('design-maker-overlay');
  if (ov) return ov;
  _injectStyles();
  ov = document.createElement('div');
  ov.id = 'design-maker-overlay';
  ov.className = 'modal dm-overlay hidden';
  ov.innerHTML = ''
    + '<div class="dm-shell modal-content">'
    + '  <div class="dm-topbar modal-header">'
    + '    <button class="dm-iconbtn dm-bare" id="dm-back" title="Voltar aos projetos" style="display:none">' + ICON.back + '</button>'
    + '    <span class="dm-title">' + ICON.design + '<span>Design Maker</span></span>'
    + '    <input class="dm-proj-name" id="dm-proj-name" placeholder="Design sem título" style="display:none" />'
    + '    <span class="dm-spacer"></span>'
    + '    <button class="dm-iconbtn dm-bare" id="dm-export" title="Exportar projeto (.zip)" style="display:none">' + ICON.download + '<span>Exportar</span></button>'
    + '    <button class="dm-iconbtn dm-bare close-btn" id="dm-close" title="Fechar (Esc)">' + ICON.close + '</button>'
    + '  </div>'
    + '  <div class="dm-main" id="dm-main"></div>'
    + '</div>';
  document.body.appendChild(ov);

  ov.querySelector('#dm-close').addEventListener('click', () => close());
  ov.querySelector('#dm-back').addEventListener('click', () => _openLibrary());
  ov.querySelector('#dm-export').addEventListener('click', _exportProject);
  ov.querySelector('#dm-proj-name').addEventListener('change', _renameProject);
  ov.addEventListener('click', (e) => { if (e.target === ov) { /* keep open; full-screen */ } });
  return ov;
}

function _setChrome(mode) {
  // mode: 'library' | 'editor'
  const ov = document.getElementById('design-maker-overlay');
  if (!ov) return;
  ov.querySelector('#dm-back').style.display = (mode === 'editor') ? '' : 'none';
  ov.querySelector('#dm-export').style.display = (mode === 'editor') ? '' : 'none';
  const nameEl = ov.querySelector('#dm-proj-name');
  if (mode === 'editor' && _project) {
    nameEl.style.display = '';
    nameEl.value = _project.name || '';
  } else {
    nameEl.style.display = 'none';
  }
}

// ---------------------------------------------------------------------------
// Library view
// ---------------------------------------------------------------------------
async function _openLibrary() {
  _project = null;
  _activePageId = null;
  _kind = '';
  _dsId = '';
  _attachments = [];
  _cancelStream();
  if (location.hash.startsWith('#design-')) {
    history.replaceState(null, '', location.pathname + location.search);
  }
  _setChrome('library');
  const main = document.getElementById('dm-main');
  const kindCards = _KINDS.map((k) =>
    '<button type="button" class="dm-kind" data-kind="' + k.id + '">' + k.icon + '<span>' + k.label + '</span></button>'
  ).join('');
  main.innerHTML = ''
    + '<div class="dm-library" id="dm-library">'
    + '  <div class="dm-home">'
    + '    <h1 class="dm-home-h">O que vamos criar hoje?</h1>'
    + '    <div class="dm-home-composer">'
    + '      <textarea id="dm-home-prompt" placeholder="Descreva o que criar — ex. \'landing page para um app de finanças, hero com CTA\'"></textarea>'
    + '      <div class="dm-home-row">'
    + '        <select class="dm-model-select" id="dm-home-model" aria-label="Modelo de geração"><option value="">(modelo padrão · Opus 4.8)</option></select>'
    + '        <select class="dm-model-select" id="dm-home-ds" aria-label="Design system"><option value="">Design system: nenhum</option></select>'
    + '        <select class="dm-model-select" id="dm-home-template" aria-label="Template"><option value="">Template: nenhum</option></select>'
    + '        ' + _attachBarHTML('dm-home')
    + '        <span style="flex:1"></span>'
    + '        <button class="dm-iconbtn dm-primary" id="dm-home-go">' + ICON.send + '<span>Criar</span></button>'
    + '      </div>'
    + '      <div class="dm-attach-chips" id="dm-home-chips" style="display:none"></div>'
    + '    </div>'
    + '    <div class="dm-home-sub">Comece por um tipo (opcional):</div>'
    + '    <div class="dm-kinds" id="dm-kinds">' + kindCards + '</div>'
    + '  </div>'
    + '  <div class="dm-lib-tabs" id="dm-lib-tabs">'
    + '    <button data-lt="projects" class="active">Projetos</button>'
    + '    <button data-lt="systems">Design systems</button>'
    + '    <button data-lt="templates">Templates</button>'
    + '  </div>'
    + '  <div id="dm-lib-content"><div class="dm-muted">Carregando…</div></div>'
    + '</div>';

  _populateModelSelect(document.getElementById('dm-home-model'));
  document.querySelectorAll('#dm-lib-tabs button').forEach((b) => {
    b.addEventListener('click', () => { _libTab = b.dataset.lt; _syncLibTabs(); _renderLibrary(); });
  });
  document.querySelectorAll('#dm-kinds .dm-kind').forEach((c) => {
    c.addEventListener('click', () => {
      const was = c.classList.contains('sel');
      document.querySelectorAll('#dm-kinds .dm-kind').forEach((x) => x.classList.remove('sel'));
      if (!was) c.classList.add('sel');
      const ta = document.getElementById('dm-home-prompt');
      if (ta) ta.focus();
    });
  });
  const homeGo = document.getElementById('dm-home-go');
  const homeTa = document.getElementById('dm-home-prompt');
  const submitHome = () => {
    const sel = document.querySelector('#dm-kinds .dm-kind.sel');
    _startFromHome((homeTa.value || ''), (document.getElementById('dm-home-model') || {}).value || '', sel ? sel.dataset.kind : '');
  };
  if (homeGo) homeGo.addEventListener('click', submitHome);
  if (homeTa) homeTa.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submitHome(); }
  });
  _wireAttachBar('dm-home');
  _renderAttachChips();

  try {
    const [projects, systems, templates] = await Promise.all([
      _api('GET', '/design/projects').catch(() => ({ projects: [] })),
      _api('GET', '/design/systems').catch(() => ({ systems: [] })),
      _api('GET', '/design/templates').catch(() => ({ templates: [] })),
    ]);
    _projects = (projects && projects.projects) || [];
    _designSystems = (systems && systems.systems) || [];
    _templates = (templates && templates.templates) || [];
  } catch (e) {
    _projects = [];
    const g = document.getElementById('dm-lib-content');
    if (g) g.innerHTML = '<div class="dm-muted">Erro ao carregar: ' + _esc(e.message) + '</div>';
    return;
  }
  _populateDsSelect(document.getElementById('dm-home-ds'));
  _populateTemplateSelect(document.getElementById('dm-home-template'));
  _renderLibrary();
}

function _syncLibTabs() {
  document.querySelectorAll('#dm-lib-tabs button').forEach((b) => {
    b.classList.toggle('active', b.dataset.lt === _libTab);
  });
}

function _populateDsSelect(sel) {
  if (!sel) return;
  sel.replaceChildren();
  const none = document.createElement('option');
  none.value = ''; none.textContent = 'Design system: nenhum';
  sel.appendChild(none);
  _designSystems.forEach((s) => {
    const o = document.createElement('option');
    o.value = s.id; o.textContent = 'DS: ' + (s.name || 'sem nome');
    sel.appendChild(o);
  });
}

function _populateTemplateSelect(sel) {
  if (!sel) return;
  sel.replaceChildren();
  const none = document.createElement('option');
  none.value = ''; none.textContent = 'Template: nenhum';
  sel.appendChild(none);
  _templates.forEach((t) => {
    const o = document.createElement('option');
    o.value = t.id; o.textContent = 'Template: ' + (t.name || 'sem nome');
    sel.appendChild(o);
  });
}

// Start a new project from the home composer, then run the agentic create flow.
async function _startFromHome(text, model, kind) {
  text = (text || '').trim();
  // Template selected → start from the saved HTML (instant, no LLM).
  const tplId = (document.getElementById('dm-home-template') || {}).value || '';
  if (tplId) return _startFromTemplate(tplId);
  if (!text) { const ta = document.getElementById('dm-home-prompt'); if (ta) ta.focus(); return; }
  _dsId = (document.getElementById('dm-home-ds') || {}).value || ''; // design system for this generation
  try {
    const res = await _api('POST', '/design/project', { name: text.slice(0, 60) });
    _kind = kind || '';
    const kdef = _KINDS.find((x) => x.id === _kind);
    if (kdef && kdef.device) _device = kdef.device; // open in the frame that fits the type
    await _openProject(res.project_id);
    const ta = document.getElementById('dm-prompt-text');
    const sel = document.getElementById('dm-model');
    if (ta) ta.value = text;
    if (sel && model) sel.value = model;
    _submitPrompt('auto');
  } catch (e) { alert('Erro ao criar projeto: ' + e.message); }
}

function _renderLibrary() {
  _syncLibTabs();
  if (_libTab === 'systems') return _renderSystemsList();
  if (_libTab === 'templates') return _renderTemplatesList();
  return _renderProjectsList();
}

function _renderProjectsList() {
  const grid = document.getElementById('dm-lib-content');
  if (!grid) return;
  grid.className = 'dm-grid';
  let html = ''
    + '<div class="dm-card dm-new-card" id="dm-new-project">' + ICON.plus + '<span>Novo projeto</span></div>';
  _projects.forEach((p) => {
    const pages = (p.page_count != null) ? (p.page_count + ' pág.') : '';
    html += ''
      + '<div class="dm-card" data-pid="' + _esc(p.id) + '">'
      + '  <div class="dm-card-thumb">' + ICON.design + '</div>'
      + '  <div class="dm-card-meta">'
      + '    <span class="grow" title="' + _esc(p.name) + '">' + _esc(p.name || 'Sem título') + '</span>'
      + '    <span class="dm-sub">' + _esc(pages) + '</span>'
      + '    <button class="dm-iconbtn dm-bare dm-del-project" title="Excluir" data-pid="' + _esc(p.id) + '">' + ICON.trash + '</button>'
      + '  </div>'
      + '</div>';
  });
  grid.innerHTML = html;

  grid.querySelector('#dm-new-project').addEventListener('click', _createProject);
  grid.querySelectorAll('.dm-card[data-pid]').forEach((card) => {
    card.addEventListener('click', (e) => {
      if (e.target.closest('.dm-del-project')) return;
      _openProject(card.dataset.pid);
    });
  });
  grid.querySelectorAll('.dm-del-project').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const pid = btn.dataset.pid;
      if (!confirm('Excluir este projeto e todas as suas páginas?')) return;
      try {
        await _api('DELETE', '/design/project/' + pid);
        _projects = _projects.filter((x) => x.id !== pid);
        _renderLibrary();
      } catch (err) { alert('Erro: ' + err.message); }
    });
  });
}

// ---- Design systems tab ----
function _renderSystemsList() {
  const c = document.getElementById('dm-lib-content');
  if (!c) return;
  c.className = '';
  let html = '<div class="dm-list-head"><span>Reutilize a identidade (fontes, cores, voz) entre projetos. Anexe um na criação para o design seguir a marca.</span>'
    + '<button class="dm-iconbtn dm-primary" id="dm-new-ds">' + ICON.plus + '<span>Novo design system</span></button></div>';
  if (!_designSystems.length) html += '<div class="dm-muted" style="padding:10px 0">Nenhum design system ainda.</div>';
  html += '<div class="dm-rows">';
  _designSystems.forEach((s) => {
    html += '<div class="dm-row" data-sid="' + _esc(s.id) + '">'
      + '<span class="grow">' + _esc(s.name || 'Sem nome') + '</span>'
      + '<button class="dm-iconbtn dm-bare dm-ds-edit" data-sid="' + _esc(s.id) + '">Editar</button>'
      + '<button class="dm-iconbtn dm-bare dm-ds-del" title="Excluir design system" aria-label="Excluir design system" data-sid="' + _esc(s.id) + '">' + ICON.trash + '</button>'
      + '</div>';
  });
  html += '</div>';
  c.innerHTML = html;
  c.querySelector('#dm-new-ds').addEventListener('click', () => _editDesignSystem(null));
  c.querySelectorAll('.dm-ds-edit').forEach((b) => b.addEventListener('click', () => _editDesignSystem(b.dataset.sid)));
  c.querySelectorAll('.dm-ds-del').forEach((b) => b.addEventListener('click', async () => {
    if (!confirm('Excluir este design system?')) return;
    try { await _api('DELETE', '/design/system/' + b.dataset.sid); _designSystems = _designSystems.filter((x) => x.id !== b.dataset.sid); _renderLibrary(); } catch (e) { alert('Erro: ' + e.message); }
  }));
}

async function _editDesignSystem(sid) {
  let sys = { name: '', spec: '' };
  if (sid) {
    try { sys = await _api('GET', '/design/system/' + sid); } catch (e) { alert('Erro: ' + e.message); return; }
  }
  const c = document.getElementById('dm-lib-content');
  c.className = '';
  c.innerHTML = ''
    + '<div class="dm-ds-edit-form">'
    + '  <div class="dm-list-head"><b>' + (sid ? 'Editar' : 'Novo') + ' design system</b></div>'
    + '  <label class="dm-fld">Nome<input type="text" id="dm-ds-name" value="' + _esc(sys.name || '') + '" placeholder="ex. Firebit UI"></label>'
    + '  <label class="dm-fld">Especificação (fontes, cores/oklch, voz, componentes, do/don\'ts)'
    + '    <textarea id="dm-ds-spec" rows="14" placeholder="Ex.: Tipografia: Space Grotesk (títulos) + Inter (corpo). Cores: tinta #14110F, papel oklch(0.97 0.01 90), acento elétrico oklch(0.72 0.19 145). Voz: direta, confiante. Componentes: botões retos, sem sombra…">' + _esc(sys.spec || '') + '</textarea></label>'
    + '  <div class="dm-clarify-actions"><button class="dm-iconbtn dm-bare" id="dm-ds-cancel">Cancelar</button>'
    + '    <button class="dm-iconbtn dm-primary" id="dm-ds-save">Salvar</button></div>'
    + '</div>';
  c.querySelector('#dm-ds-cancel').addEventListener('click', _renderLibrary);
  c.querySelector('#dm-ds-save').addEventListener('click', async () => {
    const name = (c.querySelector('#dm-ds-name').value || '').trim();
    const spec = (c.querySelector('#dm-ds-spec').value || '').trim();
    if (!name) { c.querySelector('#dm-ds-name').focus(); return; }
    try {
      if (sid) await _api('PATCH', '/design/system/' + sid, { name: name, spec: spec });
      else await _api('POST', '/design/system', { name: name, spec: spec });
      const data = await _api('GET', '/design/systems');
      _designSystems = (data && data.systems) || [];
      _populateDsSelect(document.getElementById('dm-home-ds'));
      _renderLibrary();
    } catch (e) { alert('Erro: ' + e.message); }
  });
}

// ---- Templates tab ----
function _renderTemplatesList() {
  const c = document.getElementById('dm-lib-content');
  if (!c) return;
  c.className = '';
  let html = '<div class="dm-list-head"><span>Pontos de partida salvos. Escolha um template na criação ou use aqui para abrir um novo projeto a partir dele.</span></div>';
  if (!_templates.length) html += '<div class="dm-muted" style="padding:10px 0">Nenhum template ainda. Salve um a partir de uma página (aba Arquivos do projeto).</div>';
  html += '<div class="dm-rows">';
  _templates.forEach((t) => {
    html += '<div class="dm-row" data-tid="' + _esc(t.id) + '">'
      + '<span class="grow">' + _esc(t.name || 'Sem nome') + (t.kind ? (' · ' + _esc(t.kind)) : '') + '</span>'
      + '<button class="dm-iconbtn dm-bare dm-tpl-use" data-tid="' + _esc(t.id) + '">Usar</button>'
      + '<button class="dm-iconbtn dm-bare dm-tpl-del" title="Excluir template" aria-label="Excluir template" data-tid="' + _esc(t.id) + '">' + ICON.trash + '</button>'
      + '</div>';
  });
  html += '</div>';
  c.innerHTML = html;
  c.querySelectorAll('.dm-tpl-use').forEach((b) => b.addEventListener('click', () => _startFromTemplate(b.dataset.tid)));
  c.querySelectorAll('.dm-tpl-del').forEach((b) => b.addEventListener('click', async () => {
    if (!confirm('Excluir este template?')) return;
    try { await _api('DELETE', '/design/template/' + b.dataset.tid); _templates = _templates.filter((x) => x.id !== b.dataset.tid); _renderLibrary(); } catch (e) { alert('Erro: ' + e.message); }
  }));
}

async function _startFromTemplate(tid) {
  const t = _templates.find((x) => x.id === tid);
  try {
    const res = await _api('POST', '/design/project', { name: (t && t.name) || 'Novo projeto' });
    const pg = await _api('POST', '/design/project/' + res.project_id + '/page/from-template', { template_id: tid });
    await _openProject(res.project_id, pg.page_id);
  } catch (e) { alert('Erro ao usar template: ' + e.message); }
}

async function _createProject() {
  const name = prompt('Nome do projeto:', 'Design sem título');
  if (name === null) return;
  try {
    const res = await _api('POST', '/design/project', { name: name.trim() || 'Design sem título' });
    await _openProject(res.project_id);
  } catch (e) { alert('Erro ao criar projeto: ' + e.message); }
}

// ---------------------------------------------------------------------------
// Editor view
// ---------------------------------------------------------------------------
async function _openProject(pid, focusPageId) {
  let data;
  try {
    data = await _api('GET', '/design/project/' + pid);
  } catch (e) { alert('Erro ao abrir projeto: ' + e.message); return; }
  _project = data;
  _chat = []; // fresh in-memory conversation for this project
  history.replaceState(null, '', location.pathname + location.search + '#design-' + pid);
  const pages = _project.pages || [];
  _activePageId = focusPageId || (pages[0] && pages[0].id) || null;
  _setChrome('editor');
  _renderEditor();
}

function _renderEditor() {
  _markupMode = false; _tweaksOn = false; _editMode = false; _editSel = null; // reset; rebuilt toolbar buttons start off
  const main = document.getElementById('dm-main');
  main.innerHTML = ''
    + '<div class="dm-col-pages">'
    + '  <div class="dm-col-head"><span class="grow" style="flex:1">Páginas</span>'
    + '    <button class="dm-iconbtn dm-bare" id="dm-add-page" title="Nova página">' + ICON.plus + '</button></div>'
    + '  <div class="dm-pages-list" id="dm-pages-list"></div>'
    + '</div>'
    + '<div class="dm-col-canvas">'
    + '  <div class="dm-canvas-bar">'
    + '    <div class="dm-seg" id="dm-device-seg"></div>'
    + '    <button class="dm-iconbtn dm-bare" id="dm-markup-toggle" title="Markup: clique num elemento do design para comentar">' + ICON.pin + '<span>Markup</span></button>'
    + '    <button class="dm-iconbtn dm-bare" id="dm-tweaks-toggle" title="Tweaks: ajustes ao vivo no design">' + ICON.tweak + '<span>Tweaks</span></button>'
    + '    <button class="dm-iconbtn dm-bare" id="dm-edit-toggle" title="Editar: edite o texto direto no design">' + ICON.edit + '<span>Editar</span></button>'
    + '    <span class="dm-spacer" style="flex:1"></span>'
    + '    <button class="dm-iconbtn dm-bare" id="dm-zoom-out" title="Reduzir">&#8722;</button>'
    + '    <span class="dm-zoom-label" id="dm-zoom-label">100%</span>'
    + '    <button class="dm-iconbtn dm-bare" id="dm-zoom-in" title="Ampliar">+</button>'
    + '    <button class="dm-iconbtn dm-bare dm-inspector-toggle" id="dm-inspector-toggle" title="Comentários / versões" aria-label="Abrir painel de comentários">' + ICON.pin + '</button>'
    + '  </div>'
    + '  <div class="dm-canvas-scroll" id="dm-canvas-scroll"></div>'
    + '  <div class="dm-prompt">'
    + '    <div class="dm-attach-chips" id="dm-prompt-chips" style="display:none"></div>'
    + '    <div class="dm-prompt-row">'
    + '      <textarea id="dm-prompt-text" placeholder="Descreva o que criar — ex. \'landing page para um app de finanças, hero com CTA\'"></textarea>'
    + '      <button class="dm-iconbtn dm-primary" id="dm-generate">' + ICON.send + '<span id="dm-generate-label">Gerar</span></button>'
    + '    </div>'
    + '    <div class="dm-prompt-ctl">'
    + '      <select class="dm-model-select" id="dm-model" aria-label="Modelo de geração"><option value="">(modelo padrão · Opus 4.8)</option></select>'
    + '      <button class="dm-iconbtn dm-bare" id="dm-regen" title="Regenerar do zero">' + ICON.regen + '<span>Regenerar</span></button>'
    + '      ' + _attachBarHTML('dm-prompt')
    + '      <span class="dm-spacer" style="flex:1"></span>'
    + '      <span class="dm-status" id="dm-status"></span>'
    + '    </div>'
    + '  </div>'
    + '</div>'
    + '<div class="dm-col-inspector">'
    + '  <div class="dm-tabs" id="dm-tabs">'
    + '    <button data-tab="agent" class="active">Chat</button>'
    + '    <button data-tab="comments">Comentários</button>'
    + '    <button data-tab="versions">Versões</button>'
    + '    <button data-tab="files">Arquivos</button>'
    + '  </div>'
    + '  <div class="dm-tab-body" id="dm-tab-body"></div>'
    + '</div>';

  // Device segmented control
  const seg = main.querySelector('#dm-device-seg');
  Object.entries(_DEVICES).forEach(([key, d]) => {
    const b = document.createElement('button');
    b.dataset.device = key;
    b.textContent = d.label;
    if (key === _device) b.classList.add('active');
    b.addEventListener('click', () => { _device = key; _zoom = 1; _renderCanvas(); _syncDeviceSeg(); });
    seg.appendChild(b);
  });

  main.querySelector('#dm-add-page').addEventListener('click', _newPageMode);
  main.querySelector('#dm-markup-toggle').addEventListener('click', _toggleMarkup);
  main.querySelector('#dm-tweaks-toggle').addEventListener('click', _toggleTweaks);
  main.querySelector('#dm-edit-toggle').addEventListener('click', _toggleEdit);
  main.querySelector('#dm-inspector-toggle').addEventListener('click', () => {
    const ov = document.getElementById('design-maker-overlay');
    if (ov) ov.classList.toggle('dm-inspector-open');
  });
  main.querySelector('#dm-zoom-in').addEventListener('click', () => { _zoom = Math.min(2, _zoom + 0.1); _renderCanvas(); });
  main.querySelector('#dm-zoom-out').addEventListener('click', () => { _zoom = Math.max(0.25, _zoom - 0.1); _renderCanvas(); });
  main.querySelector('#dm-generate').addEventListener('click', () => _submitPrompt('auto'));
  main.querySelector('#dm-regen').addEventListener('click', () => _submitPrompt('regen'));
  main.querySelector('#dm-prompt-text').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); _submitPrompt('auto'); }
  });
  main.querySelectorAll('#dm-tabs button').forEach((b) => {
    b.addEventListener('click', () => { _inspectorTab = b.dataset.tab; _syncTabs(); _renderInspector(); });
  });

  _populateModelSelect(main.querySelector('#dm-model'));
  _wireAttachBar('dm-prompt');
  _renderPages();
  _renderCanvas();
  _renderInspector();
  _syncGenerateLabel();
  _renderAttachChips();
}

function _syncDeviceSeg() {
  document.querySelectorAll('#dm-device-seg button').forEach((b) => {
    b.classList.toggle('active', b.dataset.device === _device);
  });
}
function _syncTabs() {
  document.querySelectorAll('#dm-tabs button').forEach((b) => {
    b.classList.toggle('active', b.dataset.tab === _inspectorTab);
  });
}

function _activePage() {
  if (!_project) return null;
  return (_project.pages || []).find((p) => p.id === _activePageId) || null;
}

function _renderPages() {
  const list = document.getElementById('dm-pages-list');
  if (!list) return;
  const pages = _project.pages || [];
  if (!pages.length) {
    list.innerHTML = '<div class="dm-muted" style="padding:8px">Nenhuma página ainda. Descreva uma e clique Gerar.</div>';
    return;
  }
  list.innerHTML = '';
  pages.forEach((p, i) => {
    const row = document.createElement('div');
    row.className = 'dm-page-row' + (p.id === _activePageId ? ' active' : '');
    row.innerHTML = '<span class="grow" title="' + _esc(p.title) + '">' + _esc(p.title || ('Página ' + (i + 1))) + '</span>'
      + '<button class="dm-iconbtn dm-bare dm-del" title="Excluir página">' + ICON.trash + '</button>';
    row.querySelector('.grow').addEventListener('click', () => {
      if (p.id === _activePageId) return;
      if (_editMode) _toggleEdit(); // exit edit first → saves the current page, restores tabs
      _activePageId = p.id; _renderPages(); _renderCanvas(); _renderInspector(); _syncGenerateLabel();
    });
    row.querySelector('.grow').addEventListener('dblclick', () => _renamePage(p));
    row.querySelector('.dm-del').addEventListener('click', (e) => { e.stopPropagation(); _deletePage(p); });
    list.appendChild(row);
  });
}

let _canvasKey = null; // pageId:version of the iframe currently mounted

function _renderCanvas() {
  const scroll = document.getElementById('dm-canvas-scroll');
  if (!scroll) return;
  const page = _activePage();
  const content = page && page.content;
  if (!page) {
    _canvasKey = null;
    scroll.innerHTML = '<div class="dm-empty">Selecione ou crie uma página, descreva o que quer no campo abaixo e clique <b>Gerar</b>.</div>';
    return;
  }
  if (!content) {
    _canvasKey = null;
    scroll.innerHTML = '<div class="dm-empty">Página vazia. Descreva o que criar abaixo e clique <b>Gerar</b>.</div>';
    return;
  }
  const dev = _DEVICES[_device] || _DEVICES.fit;
  const avail = scroll.clientWidth - 44; // minus padding
  let frameW, frameH, scale;
  if (_device === 'fit' || dev.w === 0) {
    frameW = Math.max(320, avail);
    frameH = Math.max(400, scroll.clientHeight - 44);
    scale = _zoom;
  } else {
    frameW = dev.w;
    frameH = dev.h;
    const fit = avail > 0 ? Math.min(1, avail / dev.w) : 1;
    scale = fit * _zoom;
  }
  // Reuse the existing iframe across device/zoom changes (no reload/flash);
  // only (re)load when the page or its version changes. The iframe loads via
  // src (NOT srcdoc) so the /render endpoint's CSP applies instead of the SPA's
  // — that's what lets the design's Tailwind/fonts CDNs through.
  const key = page.id + ':' + (page.version || 1);
  let frame = scroll.querySelector('.dm-frame');
  if (!frame || _canvasKey !== key) {
    scroll.innerHTML = '';
    frame = document.createElement('div');
    frame.className = 'dm-frame';
    const iframe = document.createElement('iframe');
    iframe.setAttribute('sandbox', 'allow-scripts allow-modals');
    iframe.setAttribute('title', 'Pré-visualização do design');
    iframe.src = API_BASE + '/api/design/page/' + page.id + '/render?v=' + (page.version || 1);
    iframe.addEventListener('load', () => {
      _canvasIframe = iframe;
      // Re-apply markup/tweaks/edit state and sync pins to the real elements.
      _postToFrame({ type: '__dm_markup', on: _markupMode });
      if (_tweaksOn) _postToFrame({ type: '__dm_tweaks', show: true });
      if (_editMode) _postToFrame({ type: '__dm_edit', on: true });
      _relocatePins();
    });
    frame.appendChild(iframe);
    _canvasIframe = iframe;
    const markup = document.createElement('div');
    markup.className = 'dm-markup';
    frame.appendChild(markup);
    scroll.appendChild(frame);
    _canvasKey = key;
  }
  frame.style.width = frameW + 'px';
  frame.style.height = frameH + 'px';
  frame.style.transform = 'scale(' + scale.toFixed(3) + ')';
  // Reserve the scaled footprint so the scroll container sizes correctly.
  frame.style.marginBottom = (frameH * scale - frameH) + 'px';
  _syncMarkupHint(frame);
  _renderPins(frame, scale);
  const zl = document.getElementById('dm-zoom-label');
  if (zl) zl.textContent = Math.round(scale * 100) + '%';
}

// ---------------------------------------------------------------------------
// Markup — element-anchored comments via the iframe bridge (injected in
// /render). The sandboxed design is opaque-origin, so we talk to it only via
// postMessage: arm markup mode, receive the clicked element's data-cc-id +
// label + center, and relocate pins on demand so they track real elements.
// ---------------------------------------------------------------------------
function _postToFrame(msg) {
  try {
    if (_canvasIframe && _canvasIframe.contentWindow) _canvasIframe.contentWindow.postMessage(msg, '*');
  } catch (_) {}
}

function _toggleMarkup() {
  _markupMode = !_markupMode;
  const btn = document.getElementById('dm-markup-toggle');
  if (btn) btn.classList.toggle('dm-toggle-on', _markupMode);
  // Markup and edit mode share the iframe's click/contentEditable handling, so
  // they're mutually exclusive — arming markup exits edit mode (which serializes
  // and saves the in-progress edits via the bridge's __dm_edit_html reply).
  if (_markupMode && _editMode) {
    _editMode = false;
    const eb = document.getElementById('dm-edit-toggle');
    if (eb) eb.classList.remove('dm-toggle-on');
    _postToFrame({ type: '__dm_edit', on: false });
    _setEditUI(false); // restore the normal inspector tabs
  }
  _postToFrame({ type: '__dm_markup', on: _markupMode });
  const frame = document.querySelector('.dm-frame');
  if (frame) _syncMarkupHint(frame);
}

function _toggleTweaks() {
  _tweaksOn = !_tweaksOn;
  const btn = document.getElementById('dm-tweaks-toggle');
  if (btn) btn.classList.toggle('dm-toggle-on', _tweaksOn);
  _postToFrame({ type: '__dm_tweaks', show: _tweaksOn });
}

function _toggleEdit() {
  _editMode = !_editMode;
  const btn = document.getElementById('dm-edit-toggle');
  if (btn) btn.classList.toggle('dm-toggle-on', _editMode);
  // Mutually exclusive with markup (see _toggleMarkup): arming edit disarms it.
  if (_editMode && _markupMode) {
    _markupMode = false;
    const mb = document.getElementById('dm-markup-toggle');
    if (mb) mb.classList.remove('dm-toggle-on');
    _postToFrame({ type: '__dm_markup', on: false });
    const frame = document.querySelector('.dm-frame');
    if (frame) _syncMarkupHint(frame);
  }
  // Turning OFF posts on:false, which makes the bridge serialize the cleaned
  // document and post it back as __dm_edit_html (handled in _onBridgeMessage).
  _postToFrame({ type: '__dm_edit', on: _editMode });
  _setEditUI(_editMode);
}

// Swap the inspector between its normal tabs (Chat/Comentários/Versões/Arquivos)
// and the "Edição" property panel. On (edit): hide the tabs and the chat, render
// the property panel into the tab body. Off: restore the tabs + active tab.
function _setEditUI(on) {
  const tabs = document.getElementById('dm-tabs');
  if (tabs) tabs.style.display = on ? 'none' : '';
  _editSel = null;
  _editingPageId = on ? _activePageId : _editingPageId; // remember which page is being edited
  if (on) {
    _renderEditPanel();
    // Make sure the panel is visible (inspector is a drawer on narrow screens).
    const ov = document.getElementById('design-maker-overlay');
    if (ov) ov.classList.add('dm-inspector-open');
  } else {
    _syncTabs();
    _renderInspector();
  }
}

// Safe families offered in the "Fonte" select. The selected element's current
// family is prepended (if not already present) so it's never lost.
const _EDIT_FONTS = ['Sora', 'Space Grotesk', 'Inter', 'Playfair Display', 'DM Sans', 'Georgia', 'system-ui', 'Arial', 'Helvetica', 'Times New Roman', 'Courier New', 'Verdana'];
// Six preset color swatches (neutral + accents) for the "Cor" row.
const _EDIT_SWATCHES = ['#000000', '#ffffff', '#ef4444', '#3b82f6', '#10b981', '#f59e0b'];

function _rgbToHex(c) {
  if (!c) return '';
  c = String(c).trim();
  if (c[0] === '#') return c;
  const m = c.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
  if (!m) return c;
  const h = (n) => ('0' + (parseInt(n, 10) & 255).toString(16)).slice(-2);
  return '#' + h(m[1]) + h(m[2]) + h(m[3]);
}

// Push a single style change to the selected element (bridge applies it live)
// and keep the local cache in sync so the controls stay consistent.
function _setStyle(prop, value) {
  if (!_editSel) return;
  _postToFrame({ type: '__dm_style', prop: prop, value: value });
  _editSel[prop] = (prop === 'fontSize') ? (parseInt(value, 10) || _editSel.fontSize) : value;
}

// Render the "Edição" property panel into the inspector body. Controls are
// disabled with a hint until a selection arrives via __dm_sel.
function _renderEditPanel() {
  const body = document.getElementById('dm-tab-body');
  if (!body) return;
  const sel = _editSel;
  const dis = sel ? '' : ' disabled';
  const fs = sel ? (parseInt(sel.fontSize, 10) || '') : ''; // coerce to int (never raw into innerHTML)
  const hex = sel ? _rgbToHex(sel.color) : '';
  const isBold = sel ? (parseInt(sel.fontWeight, 10) >= 600) : false;
  const curAlign = sel ? sel.textAlign : '';

  let fontOpts = '';
  const fonts = _EDIT_FONTS.slice();
  if (sel && sel.fontFamily && fonts.indexOf(sel.fontFamily) === -1) fonts.unshift(sel.fontFamily);
  fonts.forEach((f) => {
    const selAttr = (sel && sel.fontFamily === f) ? ' selected' : '';
    fontOpts += '<option value="' + _esc(f) + '"' + selAttr + '>' + _esc(f) + '</option>';
  });

  let swatches = '';
  _EDIT_SWATCHES.forEach((c) => {
    swatches += '<button type="button" class="dm-ep-swatch" data-color="' + c + '" style="background:' + c + '" title="' + c + '"' + dis + '></button>';
  });

  body.innerHTML = ''
    + '<div class="dm-edit-panel">'
    + '  <div class="dm-tx-h" style="margin-top:0">Edição</div>'
    + '  <div class="dm-muted dm-ep-hint" id="dm-ep-hint"' + (sel ? ' style="display:none"' : '') + '>Selecione um elemento no design para editar suas propriedades.</div>'
    + '  <div class="dm-ep-row">'
    + '    <label>Tamanho da fonte</label>'
    + '    <div class="dm-ep-size">'
    + '      <button type="button" class="dm-iconbtn dm-bare" id="dm-ep-fs-dec" title="Diminuir"' + dis + '>&#8722;</button>'
    + '      <input type="number" id="dm-ep-fs" min="6" max="400" step="1" value="' + fs + '"' + dis + '>'
    + '      <span class="dm-ep-unit">px</span>'
    + '      <button type="button" class="dm-iconbtn dm-bare" id="dm-ep-fs-inc" title="Aumentar"' + dis + '>+</button>'
    + '    </div>'
    + '  </div>'
    + '  <div class="dm-ep-row">'
    + '    <label>Cor</label>'
    + '    <div class="dm-ep-swatches">' + swatches + '</div>'
    + '    <input type="text" id="dm-ep-color" class="dm-ep-hex" placeholder="#000000" value="' + _esc(hex) + '"' + dis + '>'
    + '  </div>'
    + '  <div class="dm-ep-row">'
    + '    <label>Negrito</label>'
    + '    <button type="button" class="dm-chip' + (isBold ? ' sel' : '') + '" id="dm-ep-bold"' + dis + '>Negrito</button>'
    + '  </div>'
    + '  <div class="dm-ep-row">'
    + '    <label>Fonte</label>'
    + '    <select id="dm-ep-font" class="dm-model-select"' + dis + '>' + fontOpts + '</select>'
    + '  </div>'
    + '  <div class="dm-ep-row">'
    + '    <label>Alinhamento</label>'
    + '    <div class="dm-seg dm-ep-align" id="dm-ep-align">'
    + '      <button type="button" data-al="left" class="' + (curAlign === 'left' ? 'active' : '') + '" title="À esquerda"' + dis + '>' + ICON.alignL + '</button>'
    + '      <button type="button" data-al="center" class="' + (curAlign === 'center' ? 'active' : '') + '" title="Centralizado"' + dis + '>' + ICON.alignC + '</button>'
    + '      <button type="button" data-al="right" class="' + (curAlign === 'right' ? 'active' : '') + '" title="À direita"' + dis + '>' + ICON.alignR + '</button>'
    + '    </div>'
    + '  </div>'
    + '  <div class="dm-ep-note">Mover e redimensionar blocos (posição/tamanho) chegará em breve.</div>'
    + '</div>';

  if (!sel) return; // controls disabled; nothing to wire

  const fsInput = body.querySelector('#dm-ep-fs');
  const commitFs = () => {
    let v = parseInt(fsInput.value, 10);
    if (!isFinite(v)) return;
    v = Math.max(6, Math.min(400, v));
    fsInput.value = v;
    _setStyle('fontSize', v + 'px');
  };
  fsInput.addEventListener('change', commitFs);
  fsInput.addEventListener('input', commitFs);
  body.querySelector('#dm-ep-fs-dec').addEventListener('click', () => {
    let v = (parseInt(fsInput.value, 10) || _editSel.fontSize || 16) - 1;
    v = Math.max(6, v); fsInput.value = v; _setStyle('fontSize', v + 'px');
  });
  body.querySelector('#dm-ep-fs-inc').addEventListener('click', () => {
    let v = (parseInt(fsInput.value, 10) || _editSel.fontSize || 16) + 1;
    v = Math.min(400, v); fsInput.value = v; _setStyle('fontSize', v + 'px');
  });

  const hexInput = body.querySelector('#dm-ep-color');
  const commitColor = () => {
    const val = (hexInput.value || '').trim();
    if (/^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(val)) _setStyle('color', val);
  };
  hexInput.addEventListener('change', commitColor);
  body.querySelectorAll('.dm-ep-swatch').forEach((sw) => {
    sw.addEventListener('click', () => {
      const c = sw.dataset.color;
      hexInput.value = c;
      _setStyle('color', c);
    });
  });

  const boldBtn = body.querySelector('#dm-ep-bold');
  boldBtn.addEventListener('click', () => {
    const nowBold = parseInt(_editSel.fontWeight, 10) >= 600;
    const next = nowBold ? '400' : '700';
    boldBtn.classList.toggle('sel', !nowBold);
    _setStyle('fontWeight', next);
  });

  body.querySelector('#dm-ep-font').addEventListener('change', (e) => {
    _setStyle('fontFamily', e.target.value);
  });

  body.querySelectorAll('#dm-ep-align button').forEach((b) => {
    b.addEventListener('click', () => {
      body.querySelectorAll('#dm-ep-align button').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      _setStyle('textAlign', b.dataset.al);
    });
  });
}

function _syncMarkupHint(frame) {
  const markup = frame.querySelector('.dm-markup');
  if (!markup) return;
  let hint = markup.querySelector('.dm-markup-hint');
  if (_markupMode && !hint) {
    hint = document.createElement('div');
    hint.className = 'dm-markup-hint';
    hint.textContent = 'Clique num elemento do design para comentar';
    markup.appendChild(hint);
  } else if (!_markupMode && hint) { hint.remove(); }
}

function _anchoredComments() {
  const page = _activePage();
  if (!page) return [];
  return (_project.comments || []).filter((c) => c.page_id === page.id && c.anchor && typeof c.anchor.x === 'number');
}

function _renderPins(frame, scale) {
  const markup = frame.querySelector('.dm-markup');
  if (!markup) return;
  markup.querySelectorAll('.dm-pin').forEach((p) => p.remove());
  const counter = scale ? (1 / scale) : 1; // keep dots a constant on-screen size
  _anchoredComments().forEach((c, i) => {
    const pin = document.createElement('div');
    pin.className = 'dm-pin' + (c.resolved ? ' resolved' : '');
    pin.dataset.ccid = (c.anchor && c.anchor.ccId) || '';
    pin.style.left = (c.anchor.x * 100) + '%';
    pin.style.top = (c.anchor.y * 100) + '%';
    pin.style.transform = 'scale(' + counter.toFixed(3) + ')';
    pin.textContent = String(i + 1);
    pin.title = c.body || '';
    pin.setAttribute('role', 'button');
    pin.setAttribute('aria-label', 'Comentário ' + (i + 1) + ': ' + (c.body || ''));
    pin.addEventListener('click', (e) => {
      e.stopPropagation();
      _inspectorTab = 'comments';
      _syncTabs();
      _renderInspector();
      // On mobile the inspector is a drawer — open it so the comment is visible.
      const ov = document.getElementById('design-maker-overlay');
      if (ov) ov.classList.add('dm-inspector-open');
      _flashComment(c.id);
    });
    markup.appendChild(pin);
  });
}

function _relocatePins() {
  const ids = _anchoredComments().map((c) => c.anchor.ccId).filter(Boolean);
  if (ids.length) _postToFrame({ type: '__dm_relocate', ids: ids });
}

function _closeCommentComposer() {
  document.querySelectorAll('.dm-comment-pop').forEach((p) => p.remove());
}

// Inline comment bubble anchored at the picked element (replaces window.prompt).
function _showCommentComposer(d) {
  const scroll = document.getElementById('dm-canvas-scroll');
  const frame = scroll && scroll.querySelector('.dm-frame');
  if (!scroll || !frame) return;
  _closeCommentComposer();
  const sRect = scroll.getBoundingClientRect();
  const fRect = frame.getBoundingClientRect();
  const popW = 248;
  let px = (fRect.left - sRect.left) + d.x * fRect.width + scroll.scrollLeft + 14;
  let py = (fRect.top - sRect.top) + d.y * fRect.height + scroll.scrollTop;
  px = Math.max(8, Math.min(px, scroll.clientWidth - popW - 8 + scroll.scrollLeft));
  py = Math.max(scroll.scrollTop + 8, py);

  const pop = document.createElement('div');
  pop.className = 'dm-comment-pop';
  pop.style.left = px + 'px';
  pop.style.top = py + 'px';
  pop.style.width = popW + 'px';
  pop.innerHTML = ''
    + '<div class="dm-cp-label">' + ICON.pin + '<span>' + _esc(d.label || 'elemento') + '</span></div>'
    + '<textarea class="dm-cp-text" placeholder="Comentário…"></textarea>'
    + '<div class="dm-cp-actions">'
    + '  <button type="button" class="dm-iconbtn dm-bare dm-cp-cancel">Cancelar</button>'
    + '  <button type="button" class="dm-iconbtn dm-primary dm-cp-go">' + ICON.send + '<span>Comentar</span></button>'
    + '</div>';
  scroll.appendChild(pop);
  const ta = pop.querySelector('.dm-cp-text');
  setTimeout(() => ta.focus(), 30);

  const submit = async () => {
    const body = (ta.value || '').trim();
    const page = _activePage();
    if (!body || !page) { ta.focus(); return; }
    try {
      const anchor = { x: +(+d.x).toFixed(4), y: +(+d.y).toFixed(4), ccId: d.ccId || '', label: d.label || '' };
      const c = await _api('POST', '/design/page/' + page.id + '/comment',
        { body: body, anchor: JSON.stringify(anchor) });
      _project.comments = _project.comments || [];
      _project.comments.push(c);
      _closeCommentComposer();
      _renderCanvas();
      _renderInspector();
    } catch (err) { alert('Erro: ' + err.message); }
  };
  pop.querySelector('.dm-cp-go').addEventListener('click', submit);
  pop.querySelector('.dm-cp-cancel').addEventListener('click', _closeCommentComposer);
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit(); }
    else if (e.key === 'Escape') { e.preventDefault(); _closeCommentComposer(); }
  });
}

// Messages from the iframe bridge: element pick (create comment) + relocation.
async function _onBridgeMessage(e) {
  // Only trust messages from our own preview iframe — a third party / the
  // sandboxed (LLM-authored) document must not be able to drive the parent.
  if (_canvasIframe && e && e.source && e.source !== _canvasIframe.contentWindow) return;
  const d = (e && e.data) || {};
  if (d.type === '__dm_pick') {
    if (!_markupMode) return;
    if (!_activePage()) return;
    _showCommentComposer(d); // native inline bubble (no browser prompt)
  } else if (d.type === '__dm_sel') {
    // Element selected in edit mode — populate + enable the property panel.
    if (!_editMode) return;
    _editSel = Object.assign({ ccId: d.ccId }, d.styles || {});
    _renderEditPanel();
  } else if (d.type === '__dm_located' && d.pos) {
    const frame = document.querySelector('.dm-frame');
    if (!frame) return;
    frame.querySelectorAll('.dm-pin').forEach((pin) => {
      const p = d.pos[pin.dataset.ccid];
      if (p) { pin.style.left = (p.x * 100) + '%'; pin.style.top = (p.y * 100) + '%'; }
    });
  } else if (d.type === '__dm_edit_html') {
    // The bridge serialized the edited document on edit-mode exit — persist it
    // as a new version, then reload the canvas fresh from the saved content.
    // Target the page that was BEING edited (captured on edit-on) so a page
    // switch during the async save can't misroute the write.
    const pageId = _editingPageId || _activePageId;
    if (!pageId || !d.html) return;
    try {
      const res = await _api('POST', '/design/page/' + pageId + '/save-html', { html: d.html });
      const pg = _project && (_project.pages || []).find((p) => p.id === pageId);
      if (pg && res && res.version) pg.version = res.version;
      _canvasKey = null; // force the canvas iframe to reload from the saved version
      _renderCanvas();
      _refreshProject(pageId);
      _setStatus('Edição salva', false);
      setTimeout(() => {
        const s = document.getElementById('dm-status');
        if (s && s.textContent === 'Edição salva') _setStatus('', false);
      }, 2000);
    } catch (err) {
      _setStatus('Erro ao salvar edição: ' + err.message, false, true);
    }
  }
}

function _flashComment(cid) {
  const el = document.querySelector('.dm-comment[data-cid="' + cid + '"]');
  if (el) {
    el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    el.classList.remove('flash');
    void el.offsetWidth; // restart animation
    el.classList.add('flash');
  }
}

function _syncGenerateLabel() {
  const lbl = document.getElementById('dm-generate-label');
  if (!lbl) return;
  const page = _activePage();
  lbl.textContent = (page && page.content) ? 'Editar' : 'Gerar';
}

// ---------------------------------------------------------------------------
// Generation flow (project/page creation + SSE streaming)
// ---------------------------------------------------------------------------
function _newPageMode() {
  _activePageId = null;
  _renderPages();
  _renderCanvas();
  _renderInspector();
  _syncGenerateLabel();
  const ta = document.getElementById('dm-prompt-text');
  if (ta) { ta.placeholder = 'Descreva a nova página…'; ta.focus(); }
}

async function _submitPrompt(kind) {
  const ta = document.getElementById('dm-prompt-text');
  const sel = document.getElementById('dm-model');
  const text = (ta && ta.value || '').trim();
  const model = (sel && sel.value) || '';
  const page = _activePage();

  if (kind === 'regen' && page) {
    _pendingPromptLabel = text || 'Regenerar do zero';
    return _runGenerate('/design/page/' + page.id + '/generate',
      { prompt: text || 'Refaça este design inteiro do zero, mantendo a intenção, o conteúdo e a estrutura, mas melhore layout, tipografia e espaçamento.', model: model, mode: 'create' },
      page.id);
  }
  if (!text) { _setStatus('Descreva o que criar.', false, true); if (ta) ta.focus(); return; }
  _pendingPromptLabel = text; // user bubble for the chat turn

  const isCreate = !page || !page.content;
  if (isCreate) {
    // Fold the start-screen type (slides/doc/wireframe/…) into the brief.
    const sendText = _kindDirective(_kind) + text;
    // Clarify gate: let the agent ask focused questions when the brief is vague
    // (like Claude Design) before committing to a full generation.
    _setStatus('Analisando o pedido…', true);
    let questions = [];
    try {
      const r = await _api('POST', '/design/clarify', { prompt: sendText, model: model });
      questions = (r && r.questions) || [];
    } catch (_) { /* clarify is best-effort — proceed on failure */ }
    _setStatus('', false);
    if (questions.length) { _showClarify(sendText, model, questions); return; }
    return _startCreate(sendText, model);
  }

  // Existing design → scoped edit.
  await _runGenerate('/design/page/' + page.id + '/generate', Object.assign({ prompt: text, model: model, mode: 'edit' }, _attachmentForm()), page.id, ta);
}

async function _startCreate(text, model) {
  const ta = document.getElementById('dm-prompt-text');
  const page = _activePage();
  const att = _attachmentForm();
  try {
    if (!page) {
      _setStatus('Gerando…', true);
      const res = await _api('POST', '/design/project/' + _project.id + '/page', Object.assign({ prompt: text, model: model, design_system_id: _dsId }, att));
      if (ta) ta.value = '';
      _clearAttachments();
      await _refreshProject(res.page_id);
      _streamJob(res.job_id, res.page_id);
    } else {
      await _runGenerate('/design/page/' + page.id + '/generate', Object.assign({ prompt: text, model: model, mode: 'create', design_system_id: _dsId }, att), page.id, ta);
    }
  } catch (e) { _setStatus('Erro: ' + e.message, false, true); }
}

// Render the clarifying-questions card in the canvas area; on submit, fold the
// answers into the prompt and start the generation.
function _showClarify(text, model, questions) {
  const scroll = document.getElementById('dm-canvas-scroll');
  if (!scroll) return;
  _canvasKey = null; // force the canvas to rebuild after we leave this card
  let html = '<div class="dm-clarify"><div class="dm-clarify-h">Algumas perguntas rápidas</div>';
  questions.forEach((q, qi) => {
    html += '<div class="dm-clarify-q" data-qi="' + qi + '"><div class="dm-clarify-label">' + _esc(q.q) + '</div>';
    if ((q.options || []).length) {
      html += '<div class="dm-clarify-opts">';
      q.options.forEach((o) => { html += '<button type="button" class="dm-chip" data-val="' + _esc(o) + '">' + _esc(o) + '</button>'; });
      html += '</div>';
    }
    html += '<input type="text" class="dm-clarify-other" placeholder="Outro…" /></div>';
  });
  html += '<div class="dm-clarify-actions">'
    + '<button class="dm-iconbtn dm-bare" id="dm-clarify-skip">Pular e gerar</button>'
    + '<button class="dm-iconbtn dm-primary" id="dm-clarify-go">' + ICON.send + '<span>Gerar</span></button>'
    + '</div></div>';
  scroll.innerHTML = html;

  scroll.querySelectorAll('.dm-clarify-q').forEach((qel) => {
    qel.querySelectorAll('.dm-chip').forEach((ch) => ch.addEventListener('click', () => {
      qel.querySelectorAll('.dm-chip').forEach((x) => x.classList.remove('sel'));
      ch.classList.add('sel');
      const inp = qel.querySelector('.dm-clarify-other'); if (inp) inp.value = '';
    }));
  });
  const collect = () => questions.map((q, qi) => {
    const qel = scroll.querySelector('.dm-clarify-q[data-qi="' + qi + '"]');
    const sel = qel.querySelector('.dm-chip.sel');
    const other = ((qel.querySelector('.dm-clarify-other') || {}).value || '').trim();
    const ans = other || (sel ? sel.dataset.val : '');
    return ans ? ('- ' + q.q + ' ' + ans) : '';
  }).filter(Boolean).join('\n');

  scroll.querySelector('#dm-clarify-go').addEventListener('click', () => {
    const a = collect();
    _startCreate(a ? (text + '\n\nDetalhes:\n' + a) : text, model);
  });
  scroll.querySelector('#dm-clarify-skip').addEventListener('click', () => _startCreate(text, model));
}

async function _runGenerate(path, form, pageId, taEl, onDone) {
  try {
    _setStatus('Gerando…', true);
    const res = await _api('POST', path, form);
    if (taEl) taEl.value = '';
    // Clear attachments only when this submit actually carried them (so an
    // apply-comments / regen pass doesn't wipe still-pending attachments).
    if (form && (form.images || form.reference_text || form.attachment_urls)) _clearAttachments();
    _streamJob(res.job_id, pageId, onDone);
  } catch (e) { _setStatus('Erro: ' + e.message, false, true); }
}

function _streamJob(jobId, pageId, onDone) {
  _cancelStream();
  if (!jobId) { _setStatus('Erro: job não iniciado', false, true); return; }
  _chat.push({ prompt: _pendingPromptLabel || '', events: [] }); // new turn (history kept)
  _pendingPromptLabel = '';
  _showAgentTab();           // surface the live reasoning/tasks
  _startProgressTicker();    // keep the turn visibly progressing
  _setStatus('Gerando…', true);
  const es = new EventSource(API_BASE + '/api/design/job/' + jobId + '/stream');
  _stream = { es: es, jobId: jobId };
  es.onmessage = (e) => {
    let d;
    try { d = JSON.parse(e.data); } catch (_) { return; }
    if (d.type === 'event' && d.event) { _onAgentEvent(d.event, pageId); return; }
    if (d.final) {
      es.close();
      _stream = { es: null, jobId: null };
      _stopProgressTicker();
      if (d.status === 'done') {
        _setStatus('', false);
        // Run onDone (e.g. resolve applied comments) and THEN refresh, so the
        // refetch reflects the server-side changes instead of racing them.
        Promise.resolve()
          .then(() => (typeof onDone === 'function' ? onDone() : null))
          .catch(() => {})
          .finally(() => { _refreshProject(pageId); _renderTranscriptIfActive(); });
      } else {
        _setStatus('Erro: ' + (d.error || 'falhou'), false, true);
        _renderTranscriptIfActive();
      }
    }
  };
  es.onerror = () => {
    es.close();
    _stream = { es: null, jobId: null };
    _stopProgressTicker();
    _setStatus('Erro de conexão', false, true);
    _renderTranscriptIfActive();
  };
}

// Apply a streamed agent event: grow the transcript, advance status, and (on a
// new version) live-refresh the canvas so the design appears as it's built.
function _onAgentEvent(ev, pageId) {
  const turn = _chat[_chat.length - 1];
  if (turn) turn.events.push(ev);
  if (ev.type === 'phase') _setStatus(ev.text || 'Trabalhando…', true);
  if (ev.type === 'version' && ev.version) {
    const p = (_project && _project.pages || []).find((x) => x.id === pageId);
    if (p) { p.version = ev.version; p.content = p.content || ' '; }
    if (pageId === _activePageId) _renderCanvas();
  }
  _renderTranscriptIfActive();
}

function _cancelStream() {
  if (_stream.es) { try { _stream.es.close(); } catch (_) {} }
  _stream = { es: null, jobId: null };
  _stopProgressTicker();
}

async function _refreshProject(focusPageId) {
  if (!_project) return;
  try {
    const data = await _api('GET', '/design/project/' + _project.id);
    _project = data;
    if (focusPageId) _activePageId = focusPageId;
    if (!_activePage() && (_project.pages || []).length) _activePageId = _project.pages[0].id;
    _setChrome('editor');
    _renderPages();
    _renderCanvas();
    _renderInspector();
    _syncGenerateLabel();
  } catch (e) { _setStatus('Erro ao recarregar: ' + e.message, false, true); }
}

function _setStatus(text, busy, isErr) {
  const el = document.getElementById('dm-status');
  if (!el) return;
  el.className = 'dm-status' + (isErr ? ' err' : '');
  el.innerHTML = (busy ? '<span class="dm-spin"></span>' : '') + _esc(text);
  // Disable every action that can launch a job while one is in flight —
  // otherwise a second generate/regen/apply drops the first SSE stream while
  // its job still completes server-side, producing two version bumps.
  ['dm-generate', 'dm-regen', 'dm-apply-comments'].forEach((id) => {
    const b = document.getElementById(id);
    if (b) b.disabled = !!busy;
  });
}

// ---------------------------------------------------------------------------
// Page ops
// ---------------------------------------------------------------------------
async function _renamePage(page) {
  const t = prompt('Título da página:', page.title || '');
  if (t === null) return;
  try {
    await _api('PATCH', '/design/page/' + page.id, { title: t.trim() });
    page.title = t.trim() || page.title;
    _renderPages();
  } catch (e) { alert('Erro: ' + e.message); }
}

async function _deletePage(page) {
  if (!confirm('Excluir esta página?')) return;
  try {
    await _api('DELETE', '/design/page/' + page.id);
    _project.pages = (_project.pages || []).filter((p) => p.id !== page.id);
    if (_activePageId === page.id) _activePageId = (_project.pages[0] && _project.pages[0].id) || null;
    _renderPages();
    _renderCanvas();
    _renderInspector();
    _syncGenerateLabel();
  } catch (e) { alert('Erro: ' + e.message); }
}

async function _renameProject(e) {
  if (!_project) return;
  const name = (e.target.value || '').trim();
  if (!name || name === _project.name) return;
  try {
    await _api('PATCH', '/design/project/' + _project.id, { name: name });
    _project.name = name;
  } catch (err) { alert('Erro: ' + err.message); }
}

function _exportProject() {
  if (!_project) return;
  const a = document.createElement('a');
  a.href = API_BASE + '/api/design/project/' + _project.id + '/export';
  a.download = (_project.name || 'design').replace(/[^a-z0-9_-]+/gi, '_') + '.zip';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// ---------------------------------------------------------------------------
// Inspector (Comments / Versions / Files)
// ---------------------------------------------------------------------------
function _renderInspector() {
  const body = document.getElementById('dm-tab-body');
  if (!body) return;
  if (_inspectorTab === 'agent') return _renderTranscript(body);
  if (_inspectorTab === 'comments') return _renderComments(body);
  if (_inspectorTab === 'versions') return _renderVersions(body);
  return _renderFiles(body);
}

// ---------------------------------------------------------------------------
// Agent transcript — the live "ações, raciocínio, tarefas" (brief, todo list,
// self-critique) streamed from the design agent.
// ---------------------------------------------------------------------------
function _showAgentTab() {
  _inspectorTab = 'agent';
  _syncTabs();
  _renderInspector();
}

function _renderTranscriptIfActive() {
  if (_inspectorTab === 'agent') {
    const body = document.getElementById('dm-tab-body');
    if (body) _renderTranscript(body);
  }
}

function _renderTranscript(body) {
  if (!_chat.length) {
    body.innerHTML = '<div class="dm-muted">A conversa aparece aqui: cada pedido, o que o agente fez (direção, tarefas, auto-revisão) e o resultado. O histórico fica guardado durante a sessão.</div>';
    return;
  }
  const lastIdx = _chat.length - 1;
  const running = !!_stream.es;
  let html = '';
  _chat.forEach((turn, ti) => {
    html += _renderTurn(turn, running && ti === lastIdx);
  });
  body.innerHTML = html;
  // Auto-scroll to the latest turn only if the user is already near the bottom,
  // so reading history mid-generation isn't yanked down every tick.
  if (body.scrollHeight - body.scrollTop - body.clientHeight < 120) body.scrollTop = body.scrollHeight;
}

// Render one chat turn: the user bubble + the agent's brief/tasks/critique.
function _renderTurn(turn, isLive) {
  const ev = turn.events || [];
  const doneIdx = new Set(ev.filter((e) => e.type === 'todo_done').map((e) => e.index));
  const phases = ev.filter((e) => e.type === 'phase');
  const livePhase = phases.length ? (phases[phases.length - 1].phase || '') : '';
  let html = '<div class="dm-turn">';
  if (turn.prompt) html += '<div class="dm-turn-user">' + _esc(turn.prompt) + '</div>';

  if (isLive) {
    let cur = phases.length ? phases[phases.length - 1].text : 'Trabalhando…';
    // During the long single-shot build, rotate the sub-message so it never
    // looks frozen; always show a live elapsed timer.
    if (livePhase === 'build') cur = _BUILD_MSGS[Math.floor(_progressTick / 5) % _BUILD_MSGS.length];
    const elapsed = _turnStartMs ? _fmtElapsed(Date.now() - _turnStartMs) : '';
    html += '<div class="dm-tx-phase"><span class="dm-spin"></span><span class="grow">' + _esc(cur) + '</span>'
      + (elapsed ? '<span class="dm-tx-time">' + elapsed + '</span>' : '') + '</div>';
  }

  const brief = ev.find((e) => e.type === 'brief');
  if (brief) {
    html += '<div class="dm-tx-h">Direção</div><div class="dm-tx-brief">' + _esc(brief.text) + '</div>';
    const sys = brief.system || {};
    const line = Object.keys(sys).map((k) => sys[k]).filter(Boolean).map((v) => _esc(v)).join(' · ');
    if (line) html += '<div class="dm-tx-sys">' + line + '</div>';
  }

  const todosEv = ev.find((e) => e.type === 'todos');
  if (todosEv && (todosEv.items || []).length) {
    const items = todosEv.items;
    // While the build runs (no real completion events yet), animate the list
    // filling in (~1 task every 5s) so progress is visible; once real
    // todo_done events arrive, show the true state.
    const animate = isLive && doneIdx.size === 0 && livePhase === 'build';
    const cursor = animate ? Math.min(items.length - 1, Math.floor(_progressTick / 5)) : -1;
    html += '<div class="dm-tx-h">Tarefas</div><div class="dm-tx-todos">';
    items.forEach((t, i) => {
      let cls = '', icon = '<span class="dm-tx-dot"></span>';
      if (doneIdx.has(i) || (animate && i < cursor)) { cls = ' done'; icon = ICON.check; }
      else if (animate && i === cursor) { cls = ' doing'; icon = '<span class="dm-spin dm-spin-sm"></span>'; }
      html += '<div class="dm-tx-todo' + cls + '">' + icon + '<span>' + _esc(t) + '</span></div>';
    });
    html += '</div>';
  }

  const crit = ev.find((e) => e.type === 'critique');
  if (crit) {
    html += '<div class="dm-tx-h">Auto-revisão</div>';
    if (crit.verdict === 'ship' && !(crit.issues || []).length) {
      html += '<div class="dm-muted">Aprovado pelo crítico, sem ressalvas.</div>';
    } else {
      html += '<div class="dm-tx-todos">';
      (crit.issues || []).forEach((i) => {
        html += '<div class="dm-tx-todo done">' + ICON.check + '<span>' + _esc(i) + '</span></div>';
      });
      html += '</div>';
    }
  }

  const ver = ev.filter((e) => e.type === 'version').pop();
  if (ver && ver.version && !isLive) html += '<div class="dm-turn-out">Pronto · v' + _esc(ver.version) + '</div>';

  const err = ev.find((e) => e.type === 'error');
  if (err) html += '<div class="dm-status err" style="margin-top:8px">' + _esc(err.text || 'Erro') + '</div>';

  html += '</div>';
  return html;
}

function _renderComments(body) {
  const page = _activePage();
  if (!page) { body.innerHTML = '<div class="dm-muted">Selecione uma página para comentar.</div>'; return; }
  const comments = (_project.comments || []).filter((c) => c.page_id === page.id);
  // Number anchored comments to match the canvas pins.
  const anchored = comments.filter((c) => c.anchor && typeof c.anchor.x === 'number');
  const pinNum = new Map();
  anchored.forEach((c, i) => pinNum.set(c.id, i + 1));
  const openCount = comments.filter((c) => !c.resolved).length;

  let html = '';
  if (openCount) {
    html += '<button class="dm-iconbtn dm-primary" id="dm-apply-comments" style="width:100%;justify-content:center;margin-bottom:10px">'
      + ICON.regen + '<span>Aplicar ' + openCount + ' comentário' + (openCount > 1 ? 's' : '') + ' ao design</span></button>';
  }
  comments.forEach((c) => {
    const n = pinNum.get(c.id);
    const badge = n ? ('<span style="display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;background:var(--accent,var(--red));color:#fff;font-size:11px;font-weight:700;margin-right:6px;flex-shrink:0;">' + n + '</span>') : '';
    html += ''
      + '<div class="dm-comment' + (c.resolved ? ' resolved' : '') + '" data-cid="' + _esc(c.id) + '">'
      + '  <div class="dm-c-body" style="display:flex;align-items:flex-start">' + badge + '<span style="flex:1">' + _esc(c.body) + '</span></div>'
      + '  <div class="dm-c-actions">'
      + '    <button data-act="resolve">' + (c.resolved ? 'Reabrir' : 'Resolver') + '</button>'
      + '    <button data-act="delete">Excluir</button>'
      + '  </div>'
      + '</div>';
  });
  if (!comments.length) html += '<div class="dm-muted">Sem comentários. Use <b>Markup</b> na barra do canvas para fixar um ponto, ou escreva abaixo.</div>';
  html += ''
    + '<div class="dm-add-comment">'
    + '  <textarea id="dm-new-comment" placeholder="Adicionar comentário…"></textarea>'
    + '  <button class="dm-iconbtn" id="dm-add-comment-btn">' + ICON.plus + '<span>Comentar</span></button>'
    + '</div>';
  body.innerHTML = html;

  const apply = body.querySelector('#dm-apply-comments');
  if (apply) apply.addEventListener('click', _applyComments);
  body.querySelectorAll('.dm-comment').forEach((el) => {
    const cid = el.dataset.cid;
    el.querySelector('[data-act="resolve"]').addEventListener('click', () => _toggleComment(cid));
    el.querySelector('[data-act="delete"]').addEventListener('click', () => _deleteComment(cid));
  });
  body.querySelector('#dm-add-comment-btn').addEventListener('click', async () => {
    const ta = body.querySelector('#dm-new-comment');
    const txt = (ta.value || '').trim();
    if (!txt) return;
    try {
      const c = await _api('POST', '/design/page/' + page.id + '/comment', { body: txt });
      _project.comments = _project.comments || [];
      _project.comments.push(c);
      _renderComments(body);
    } catch (e) { alert('Erro: ' + e.message); }
  });
}

// Synthesize an edit prompt from the open comments and regenerate the page.
async function _applyComments() {
  const page = _activePage();
  if (!page) return;
  const comments = (_project.comments || []).filter((c) => c.page_id === page.id && !c.resolved);
  if (!comments.length) return;
  const anchored = comments.filter((c) => c.anchor && typeof c.anchor.x === 'number');
  const pinNum = new Map();
  anchored.forEach((c, i) => pinNum.set(c.id, i + 1));
  const lines = comments.map((c) => {
    const n = pinNum.get(c.id);
    const loc = n ? ('(ponto ' + n + ' em x=' + Math.round(c.anchor.x * 100) + '%, y=' + Math.round(c.anchor.y * 100) + '%) ') : '';
    return '- ' + loc + c.body;
  });
  const editPrompt = 'Aplique os seguintes comentários de revisão a este design, mantendo o restante intacto:\n' + lines.join('\n');
  // Resolve the comments ONLY after the edit actually succeeds — resolving
  // optimistically would lose them (and hide the apply button) if generation
  // fails. _refreshProject re-renders the inspector afterwards.
  const ids = comments.map((c) => c.id);
  const onDone = async () => {
    for (const cid of ids) {
      try {
        await _api('PATCH', '/design/comment/' + cid, { resolved: 'true' });
        const c = (_project.comments || []).find((x) => x.id === cid);
        if (c) c.resolved = true;
      } catch (_) {}
    }
  };
  _pendingPromptLabel = 'Aplicar ' + comments.length + ' comentário' + (comments.length > 1 ? 's' : '') + ' ao design';
  await _runGenerate('/design/page/' + page.id + '/generate',
    { prompt: editPrompt, model: (document.getElementById('dm-model') || {}).value || '', mode: 'edit' },
    page.id, null, onDone);
}

async function _toggleComment(cid) {
  const c = (_project.comments || []).find((x) => x.id === cid);
  if (!c) return;
  try {
    const upd = await _api('PATCH', '/design/comment/' + cid, { resolved: (!c.resolved).toString() });
    c.resolved = upd.resolved;
    _renderInspector();
  } catch (e) { alert('Erro: ' + e.message); }
}

async function _deleteComment(cid) {
  try {
    await _api('DELETE', '/design/comment/' + cid);
    _project.comments = (_project.comments || []).filter((x) => x.id !== cid);
    _renderInspector();
  } catch (e) { alert('Erro: ' + e.message); }
}

async function _renderVersions(body) {
  const page = _activePage();
  if (!page) { body.innerHTML = '<div class="dm-muted">Selecione uma página.</div>'; return; }
  body.innerHTML = '<div class="dm-muted">Carregando histórico…</div>';
  let versions;
  try {
    const data = await _api('GET', '/design/page/' + page.id + '/versions');
    versions = (data && data.versions) || [];
  } catch (e) { body.innerHTML = '<div class="dm-muted">Erro: ' + _esc(e.message) + '</div>'; return; }
  if (!versions.length) { body.innerHTML = '<div class="dm-muted">Sem versões ainda. Gere o design para criar a v1.</div>'; return; }
  const cur = page.version || versions[0].version_number;
  let html = '';
  versions.forEach((v) => {
    const isCur = v.version_number === cur;
    const kb = v.length ? (Math.round(v.length / 1024) + ' KB') : '';
    html += ''
      + '<div class="dm-version' + (isCur ? ' current' : '') + '">'
      + '  <div class="grow"><b>v' + v.version_number + '</b>' + (isCur ? ' · atual' : '')
      + '    <div class="dm-vsub">' + _esc(v.summary || v.source || '') + (kb ? (' · ' + kb) : '') + '</div></div>'
      + (isCur ? '' : '  <button class="dm-iconbtn dm-bare" data-revert="' + _esc(v.id) + '">Reverter</button>')
      + '</div>';
  });
  body.innerHTML = html;
  body.querySelectorAll('[data-revert]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!confirm('Reverter para esta versão? (cria uma nova versão com este conteúdo)')) return;
      try {
        await _api('POST', '/design/page/' + page.id + '/revert', { version_id: btn.dataset.revert });
        await _refreshProject(page.id);
        _inspectorTab = 'versions'; _syncTabs(); _renderInspector();
      } catch (e) { alert('Erro: ' + e.message); }
    });
  });
}

function _renderFiles(body) {
  const page = _activePage();
  const assets = (_project && _project.assets) || [];
  let html = '<button class="dm-iconbtn" id="dm-export-2" style="width:100%;justify-content:center;margin-bottom:8px">' + ICON.download + '<span>Exportar projeto (.zip)</span></button>';
  if (page && page.content) {
    html += '<button class="dm-iconbtn dm-bare" id="dm-dl-page" style="width:100%;justify-content:center;margin-bottom:8px">' + ICON.download + '<span>Baixar página atual (.html)</span></button>';
    html += '<button class="dm-iconbtn dm-bare" id="dm-save-tpl" style="width:100%;justify-content:center;margin-bottom:8px">' + ICON.plus + '<span>Salvar página como template</span></button>';
    html += '<button class="dm-iconbtn dm-bare" id="dm-gen-ds" style="width:100%;justify-content:center;margin-bottom:10px">' + ICON.tweak + '<span>Gerar design system deste projeto</span></button>';
  }
  if (assets.length) {
    html += '<div style="margin-top:8px">';
    assets.forEach((a) => { html += '<div class="dm-muted" style="padding:3px 0">' + _esc(a.name || a.kind) + '</div>'; });
    html += '</div>';
  } else {
    html += '<div class="dm-muted">Cada página é exportada como um HTML standalone. Assets adicionais (CSS/JS/imagens) chegam numa etapa futura.</div>';
  }
  body.innerHTML = html;
  const b = body.querySelector('#dm-export-2');
  if (b) b.addEventListener('click', _exportProject);
  const dl = body.querySelector('#dm-dl-page');
  if (dl && page) dl.addEventListener('click', () => _downloadPage(page));
  const st = body.querySelector('#dm-save-tpl');
  if (st && page) st.addEventListener('click', () => _saveAsTemplate(page));
  const gd = body.querySelector('#dm-gen-ds');
  if (gd) gd.addEventListener('click', _generateDesignSystem);
}

async function _saveAsTemplate(page) {
  const name = prompt('Nome do template:', _project ? (_project.name + ' template') : 'Template');
  if (name === null || !name.trim()) return;
  try {
    await _api('POST', '/design/template', { name: name.trim(), kind: _kind || '', from_page_id: page.id });
    if (window.uiToast) window.uiToast('Template salvo'); else alert('Template salvo.');
  } catch (e) { alert('Erro: ' + e.message); }
}

async function _generateDesignSystem() {
  if (!_project) return;
  if (!confirm('Extrair um design system (fontes, cores, voz) a partir deste projeto? Pode levar alguns segundos.')) return;
  _setStatus('Extraindo design system…', true);
  try {
    const r = await _api('POST', '/design/system/from-project/' + _project.id, { name: _project.name + ' system' });
    _setStatus('', false);
    alert('Design system "' + (r.name || 'novo') + '" criado. Disponível na tela inicial → Design systems.');
  } catch (e) { _setStatus('Erro: ' + e.message, false, true); }
}

function _downloadPage(page) {
  const a = document.createElement('a');
  a.href = API_BASE + '/api/design/page/' + page.id + '/render?dl=' + (page.version || 1);
  a.download = (page.title || 'page').replace(/[^a-z0-9_-]+/gi, '_') + '.html';
  document.body.appendChild(a); a.click(); a.remove();
}

// ---------------------------------------------------------------------------
// Model dropdown — reuse /api/models (built via DOM; ids are untrusted)
// ---------------------------------------------------------------------------
async function _populateModelSelect(sel) {
  if (!sel) return;
  try {
    if (!_models) {
      const res = await fetch(API_BASE + '/api/models', { credentials: 'same-origin' });
      const data = await res.json();
      _models = (data && data.items) || [];
    }
    // Keep the default first option, append the rest.
    _models.forEach((ep) => {
      const disp = ep.models_display || ep.models || [];
      (ep.models || []).forEach((mid, i) => {
        const epId = (ep.endpoint_id !== null && ep.endpoint_id !== undefined) ? ('@' + ep.endpoint_id) : '';
        const o = document.createElement('option');
        o.value = mid + epId;
        o.textContent = (disp[i] || mid) + (ep.endpoint_name ? (' · ' + ep.endpoint_name) : '');
        sel.appendChild(o);
      });
    });
  } catch (_) { /* keep just the default option on failure */ }
}

// ---------------------------------------------------------------------------
// Open / close / toggle
// ---------------------------------------------------------------------------
export function open(projectId) {
  _ensureOverlay();
  const ov = document.getElementById('design-maker-overlay');
  const wasOpen = _open;
  ov.classList.remove('hidden');
  _open = true;
  const btn = document.getElementById('tool-design-maker-btn');
  if (btn) btn.classList.add('active');

  // Bind the Esc handler only once — open() can be re-entered (chat design_open
  // event, #design- hashchange) while already open; rebinding would orphan the
  // previous listener so close() couldn't fully detach it.
  if (!wasOpen) {
    _onDocKeydown = (e) => {
      if (e.key === 'Escape' && _open) { e.preventDefault(); close(); }
    };
    document.addEventListener('keydown', _onDocKeydown);
  }

  if (projectId) _openProject(projectId);
  else _openLibrary();
}

export function close() {
  const ov = document.getElementById('design-maker-overlay');
  if (ov) ov.classList.add('hidden');
  _open = false;
  _cancelStream();
  if (_onDocKeydown) { document.removeEventListener('keydown', _onDocKeydown); _onDocKeydown = null; }
  const btn = document.getElementById('tool-design-maker-btn');
  if (btn) btn.classList.remove('active');
  if (location.hash.startsWith('#design-')) {
    history.replaceState(null, '', location.pathname + location.search);
  }
}

export function toggle(projectId) {
  if (_open) close(); else open(projectId);
}

export function isOpen() { return _open; }

// ---------------------------------------------------------------------------
// Init — self-wire buttons + modalManager + hash routing
// ---------------------------------------------------------------------------
export function init() {
  if (_initialized) return;
  _initialized = true;

  const wire = () => {
    const toolBtn = document.getElementById('tool-design-maker-btn');
    if (toolBtn && !toolBtn._dmWired) { toolBtn._dmWired = true; toolBtn.addEventListener('click', () => toggle()); }
    const railBtn = document.getElementById('rail-design');
    if (railBtn && !railBtn._dmWired) { railBtn._dmWired = true; railBtn.addEventListener('click', () => toggle()); }
  };
  wire();
  // Buttons may be injected after this module loads (privilege gating, etc.).
  setTimeout(wire, 500);
  setTimeout(wire, 1500);

  // Bridge messages from the sandboxed preview iframe (element pick + relocate).
  window.addEventListener('message', _onBridgeMessage);

  // Refit the canvas on window resize (debounced) so "Ajustar"/scaled device
  // frames track the new viewport without needing another render trigger.
  let _rzT = null;
  window.addEventListener('resize', () => {
    if (!_open) return;
    clearTimeout(_rzT);
    _rzT = setTimeout(() => { if (_open && _activePage()) _renderCanvas(); }, 150);
  });

  // Open directly from a #design-<id> hash (deep link / reload).
  const _fromHash = () => {
    const h = location.hash || '';
    if (h.startsWith('#design-')) {
      const pid = h.slice('#design-'.length);
      if (pid) open(pid);
    }
  };
  window.addEventListener('hashchange', () => {
    const h = location.hash || '';
    if (h.startsWith('#design-')) _fromHash();
  });
  if ((location.hash || '').startsWith('#design-')) setTimeout(_fromHash, 300);

  // Register with modalManager for minimize/dock support (open stays self-wired).
  import('./modalManager.js').then((Modals) => {
    if (Modals && Modals.register) {
      Modals.register('design-maker-overlay', {
        railBtnId: 'rail-design',
        sidebarBtnId: 'tool-design-maker-btn',
        closeFn: () => close(),
        restoreFn: () => { const ov = document.getElementById('design-maker-overlay'); if (ov) ov.classList.remove('hidden'); },
      });
    }
  }).catch(() => {});
}

// Self-initialize on load (matches the module-side init of other surfaces).
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

export default { open, close, toggle, isOpen, init };
