// static/js/emailLibrary/bodyRender.js
//
// Turning a fetched message into reader HTML: the plain and threaded body
// paths, inline MIME image handling, and the quote/thread folding that sits on
// top of the detectors in ./signatureFold.js.
//
// Everything here writes into a reader element via innerHTML, so every branch
// that touches server-supplied HTML goes through `_sanitizeHtml` from
// ./utils.js first. `tests/test_security_regressions.py` counts those call
// sites against the raw per-turn HTML reads, package-wide — so do not name that
// property in a comment, or the count stops matching.

import {
  _QUOTE_ICON,
  _SIG_ICON,
  _extractQuoteMeta,
  _extractTurnMetaFromBlockquote,
  _foldSignature,
  _foldSummary,
  _harvestAttribution,
  _isBloatedSig,
  _looksLikeSignature,
} from './signatureFold.js';
import { state } from './state.js';
import {
  _SIG_BLOAT_MIN_CHARS,
  _TALON_ORIG_RE,
  _TALON_WROTE,
  _esc,
  _escLinkify,
  _formatBubbleDate,
  _formatRecipients,
  _initials,
  _parseTurnMeta,
  _sanitizeHtml,
  _senderColor,
} from './utils.js';
import { _disableInlineImages } from './settingsPage.js';

const API_BASE = window.location.origin;

/**
 * Wrap a probable signature block in a collapsed <details> so it stops
 * eating the whole reader. We try, in priority order:
 *   1. Mail-client signature wrappers — Gmail's `gmail_signature` div is
 *      explicit, no guessing required. Same for Apple Mail's data-smartmail.
 *   2. The standard "-- " RFC 3676 sig delimiter.
 *   3. A common closing phrase ("Best regards", "Cheers", etc.) on its own
 *      line — fuzzier, but catches sigs without the dash marker.
 *   4. "Sent from my iPhone/Android" / "Get Outlook for ..." mobile-client
 *      boilerplate.
 * Anything matched gets wrapped from the marker through end-of-body.
 */
/**
 * Render the email body with sig/quote folds. If the backend has cached
 * LLM-detected boundary offsets (data.boundaries), use those for an exact
 * fold based on plain-text positions. Otherwise fall back to the regex
 * detectors. The plain-body branch is always preferred when boundaries
 * exist because the offsets are computed against plain text.
 */
// Global escape hatch — when the server's thread parser misfires (it
// occasionally splits a single reply into two bogus "turns" by treating a
// signature/disclaimer as its own message), the user can flip this off to
// fall back to plain rendering. Survives reloads.
const _BUBBLES_DISABLED_KEY = 'odysseus.email.bubblesDisabled';
// Threaded chat-bubble email view is DISABLED for now — too buggy to
// ship. Force plain-text rendering everywhere by always returning true.
// Re-enable by restoring the localStorage-backed body + the toggle
// menu item in the reader's More menu.
function _bubblesDisabled() {
  return true;
}
function _setBubblesDisabled(v) {
  try { localStorage.setItem(_BUBBLES_DISABLED_KEY, v ? '1' : '0'); } catch {}
}

export function _renderEmailBody(data) {
  const plain = (typeof data?.body === 'string' && data.body.length) ? data.body : '';
  const folder = String(data?.folder || '').toLowerCase();
  const isSentFolder = folder.includes('sent');
  const fromAddr = String(data?.from_address || '').toLowerCase().trim();
  const isMine = !!fromAddr && _meEmailAddrs().has(fromAddr);

  // Messages authored by the user (Sent folder or self-sent copies in INBOX)
  // are current authored text. Do not let cached boundaries or HTML
  // blockquote parsing hide the whole thing behind "Earlier reply".
  if ((isSentFolder || isMine) && plain) {
    const plainTurns = _renderPlaintextThread(plain);
    if (plainTurns && !/^\s*<details\b/i.test(plainTurns.trim())) {
      return _foldSignature(plainTurns, null);
    }
    return _foldSignature(_escLinkify(plain).replace(/\n/g, '<br>'), null);
  }

  // Prefer the normalized plain-text thread when the message contains
  // explicit reply markers. HTML mail from Outlook/Gmail commonly wraps the
  // same quoted message in several nested blockquotes; feeding that markup
  // to the HTML walker creates phantom chains such as "Earlier reply >
  // Earlier reply > sender". The plain representation has the actual quote
  // levels and gives us one stable fold per real quoted message.
  const hasPlainThreadMarkers = plain && (
    /^\s*>/m.test(plain)
    || /^\s*-{5,}\s*(?:Previous message|Original message)\s*-{5,}\s*$/im.test(plain)
    || /^\s*On\s.+?\s(?:wrote|skrev|schrieb|écrit|escribió)\s*:\s*$/im.test(plain)
  );
  if (hasPlainThreadMarkers) {
    const plainThread = _renderPlaintextThread(plain);
    if (plainThread) return _foldSignature(plainThread, data && data.sender_signature || null);
  }

  // Prefer the server-cached thread parse — that's the richest structure
  // and the one the chat-bubble layout is built around. Skip when the user
  // has manually disabled bubble rendering.
  if (!_bubblesDisabled() && Array.isArray(data && data.thread_turns) && data.thread_turns.length) {
    return _foldSignature(
      _renderTurnsAsBubbles(data.thread_turns, data),
      data && data.sender_signature || null,
    );
  }
  const b = data && data.boundaries;
  // Use cached boundaries when present AND we have plain-text body to slice
  if (b && plain && (b.sig_start >= 0 || b.quote_start >= 0)) {
    // Pick the EARLIER of the two as the cut for "everything below this is
    // foldable", but render sig and quote with their own labels.
    let sig = (typeof b.sig_start === 'number' && b.sig_start >= 0) ? b.sig_start : -1;
    let quote = (typeof b.quote_start === 'number' && b.quote_start >= 0) ? b.quote_start : -1;
    // Clamp
    if (sig >= plain.length) sig = -1;
    if (quote >= plain.length) quote = -1;
    let head = plain;
    let sigSection = '';
    let quoteSection = '';
    if (sig >= 0 && quote >= 0) {
      const earlier = Math.min(sig, quote);
      head = plain.slice(0, earlier);
      if (sig < quote) {
        sigSection = plain.slice(sig, quote);
        quoteSection = plain.slice(quote);
      } else {
        quoteSection = plain.slice(quote, sig);
        sigSection = plain.slice(sig);
      }
    } else if (sig >= 0) {
      head = plain.slice(0, sig);
      sigSection = plain.slice(sig);
    } else {
      head = plain.slice(0, quote);
      quoteSection = plain.slice(quote);
    }
    const fmt = (s) => _escLinkify(s).replace(/\n/g, '<br>');
    let out = fmt(head);
    if (quoteSection) {
      out += '<details class="email-quote-fold">'
           + _foldSummary('Earlier thread', _QUOTE_ICON, _extractQuoteMeta(quoteSection))
           + fmt(quoteSection) + '</details>';
    }
    if (sigSection) {
      const sigHtml = fmt(sigSection);
      if (_isBloatedSig(sigHtml)) {
        out += '<details class="email-sig-fold">' + _foldSummary('Signature', _SIG_ICON)
             + sigHtml + '</details>';
      } else {
        // Short closing — leave inline; folding would just add chrome.
        out += sigHtml;
      }
    }
    return out;
  }
  // Fallback: client-side parse (HTML or plaintext).
  const hintSig = (data && data.sender_signature) || null;
  const isHtml = !!data.body_html;
  let rendered;
  if (isHtml) {
    rendered = _sanitizeHtml(data.body_html);
  } else {
    const plainTurns = _renderPlaintextThread(data.body || '');
    if (plainTurns) return _foldSignature(plainTurns, hintSig);
    rendered = _escLinkify(data.body || '').replace(/\n/g, '<br>');
  }
  const threaded = _renderThreadStructure(rendered);
  if (threaded) return _prepareEmailInlineImages(_foldSignature(threaded, hintSig));
  return _prepareEmailInlineImages(_foldSignature(_foldQuotedReplies(rendered), hintSig));
}

export function _safeRenderEmailBody(data) {
  try {
    return _prepareEmailInlineImages(_renderEmailBody(data));
  } catch (e) {
    console.error('email body render failed:', e);
    const plain = (typeof data?.body === 'string') ? data.body : '';
    if (plain) return _escLinkify(plain).replace(/\n/g, '<br>');
    if (data?.body_html) return _prepareEmailInlineImages(_sanitizeHtml(data.body_html));
    return '<span style="opacity:.65">No body</span>';
  }
}

function _prepareEmailInlineImages(html) {
  const raw = String(html || '');
  if (!/<img[\s>]/i.test(raw)) return raw;
  const doc = new DOMParser().parseFromString(`<div>${raw}</div>`, 'text/html');
  const root = doc.body.firstElementChild;
  if (!root) return raw;
  root.querySelectorAll('img').forEach((img, idx) => {
    const src = (img.getAttribute('src') || '').trim();
    const alt = (img.getAttribute('alt') || img.getAttribute('title') || '').trim();
    const isHttp = /^https?:\/\//i.test(src);
    const isCid = /^cid:/i.test(src);
    const cid = isCid ? src.replace(/^cid:/i, '').replace(/^<|>$/g, '').trim() : '';
    const label = alt || (isCid ? 'Inline image' : 'Remote image');
    const ph = doc.createElement('span');
    ph.className = 'email-inline-image-placeholder';
    ph.setAttribute('role', 'group');
    ph.setAttribute('aria-label', label);
    if (src) ph.dataset.emailImgSrc = src;
    if (cid) ph.dataset.emailImgCid = cid;
    ph.innerHTML = `
      <span class="email-inline-image-skeleton">
        <span class="email-inline-image-icon">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>
        </span>
      </span>
      <span class="email-inline-image-info">
        <span class="email-inline-image-title">${_esc(label)}</span>
        <span class="email-inline-image-sub">${isHttp ? 'Remote image blocked' : isCid ? 'Inline image hidden' : 'Image unavailable'}</span>
      </span>
      <span class="email-inline-image-actions">
        ${isHttp ? `<a class="email-inline-image-btn email-inline-image-download-btn" href="${_esc(src)}" target="_blank" rel="noopener noreferrer" download title="Download" aria-label="Download"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></a>` : ''}
        ${isCid ? `<button type="button" class="email-inline-image-btn email-inline-image-download-btn" data-email-img-download="${idx}" title="Download" aria-label="Download"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button>` : ''}
      </span>
    `;
    img.replaceWith(ph);
  });
  return root.innerHTML;
}

export function _wireEmailInlineImages(reader) {
  if (!reader) return;
  const placeholders = Array.from(reader.querySelectorAll('.email-inline-image-placeholder'));
  const visiblePlaceholders = placeholders.filter(ph => (
    ph.isConnected &&
    (ph.dataset.emailImgSrc || ph.dataset.emailImgCid) &&
    getComputedStyle(ph).display !== 'none'
  ));
  const autoLoadInlineImages = state._libViewInlineImages !== false;
  const manualPlaceholders = autoLoadInlineImages
    ? visiblePlaceholders.filter(ph => !ph.dataset.emailImgCid)
    : visiblePlaceholders;
  const existingBulkBar = reader.querySelector('.email-inline-image-load-all');
  if (!manualPlaceholders.length) existingBulkBar?.remove();
  const loadPlaceholder = (ph) => {
    if (!ph || !ph.isConnected) return;
    const src = (ph.dataset.emailImgSrc || '').trim();
    const cid = (ph.dataset.emailImgCid || '').trim();
    const ctxRoot = reader.dataset?.emailUid
      ? reader
      : ph.closest('[data-email-uid]') || reader.closest?.('[data-email-uid]');
    const folder = ctxRoot?.dataset?.emailFolder || reader.dataset?.emailFolder || state._libFolder || 'INBOX';
    const account = ctxRoot?.dataset?.emailAccount || reader.dataset?.emailAccount || state._libAccountId || '';
    const uid = ctxRoot?.dataset?.emailUid || reader.dataset?.emailUid || '';
    const inlineUrl = () => {
      if (!uid || !cid) return '';
      const params = new URLSearchParams({ cid, folder });
      if (account) params.set('account_id', account);
      return `${API_BASE}/api/email/inline-image/${encodeURIComponent(uid)}?${params.toString()}`;
    };
    const isRemoteImage = /^https?:\/\//i.test(src);
    const isCidImage = !!cid && !isRemoteImage;
    const loadSrc = isRemoteImage ? src : inlineUrl();
    if (!loadSrc || ph.classList.contains('is-loading')) return;
    ph.classList.remove('is-error');
    ph.classList.add('is-loading');
    const sub = ph.querySelector('.email-inline-image-sub');
    if (sub) sub.textContent = 'Loading image...';
    const actions = ph.querySelector('.email-inline-image-actions');
    if (actions) actions.style.display = 'none';
    const img = document.createElement('img');
    img.className = 'email-inline-image-loaded';
    img.alt = ph.querySelector('.email-inline-image-title')?.textContent || 'Loaded email image';
    img.loading = 'eager';
    img.referrerPolicy = 'no-referrer';
    let settled = false;
    let objectUrl = '';
    let frame = null;
    let directFallbackUsed = false;
    const ensureFrame = () => {
      if (frame) return frame;
      frame = document.createElement('span');
      frame.className = 'email-inline-image-frame is-loading';
      frame.appendChild(img);
      if (ph.isConnected) ph.replaceWith(frame);
      return frame;
    };
    const showLoaded = () => {
      if (settled) return;
      settled = true;
      if (frame) frame.classList.remove('is-loading');
      img.classList.add('is-visible');
    };
    const showError = (err) => {
      if (settled) return;
      settled = true;
      if (frame?.isConnected) frame.replaceWith(ph);
      ph.classList.remove('is-loading');
      ph.classList.add('is-error');
      if (sub) sub.textContent = err?.message ? `Image failed: ${err.message}` : 'Image failed to load';
      if (actions) actions.style.display = '';
      if (objectUrl) {
        try { URL.revokeObjectURL(objectUrl); } catch {}
      }
    };
    const loadDirect = () => {
      directFallbackUsed = true;
      ensureFrame();
      img.src = loadSrc;
      if (img.complete && img.naturalWidth > 0) showLoaded();
    };
    img.addEventListener('load', showLoaded, { once: true });
    img.addEventListener('error', () => {
      if (!directFallbackUsed && objectUrl && isCidImage) {
        try { URL.revokeObjectURL(objectUrl); } catch {}
        objectUrl = '';
        loadDirect();
        return;
      }
      showError();
    }, { once: true });
    if (isCidImage || isRemoteImage) {
      loadDirect();
      setTimeout(() => {
        if (!settled && frame) {
          frame.classList.add('is-loading');
        }
      }, 12000);
      return;
    }
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 20000);
    fetch(loadSrc, { credentials: 'same-origin', signal: controller.signal })
      .then(async res => {
        clearTimeout(timeoutId);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        if (!blob || !blob.size) throw new Error('Empty image');
        const type = blob.type && blob.type.startsWith('image/') ? blob.type : 'image/png';
        objectUrl = URL.createObjectURL(blob.type === type ? blob : new Blob([blob], { type }));
        ensureFrame();
        img.src = objectUrl;
        if (img.complete && img.naturalWidth > 0) showLoaded();
        if (!settled && typeof img.decode === 'function') {
          img.decode().then(showLoaded).catch(() => {
            if (img.complete && img.naturalWidth === 0) showError();
          });
        }
      })
      .catch(err => {
        clearTimeout(timeoutId);
        showError(err?.name === 'AbortError' ? new Error('request timed out') : err);
      });
    setTimeout(() => {
      if (!settled && !frame) {
        ph.classList.remove('is-loading');
        if (sub) sub.textContent = 'Still loading...';
        if (actions) actions.style.display = '';
      }
    }, 12000);
  };
  if (manualPlaceholders.length && !reader.querySelector('.email-inline-image-load-all')) {
    const first = manualPlaceholders[0];
    const bar = document.createElement('div');
    bar.className = 'email-inline-image-load-all';
    const label = autoLoadInlineImages
      ? `${manualPlaceholders.length} external image${manualPlaceholders.length === 1 ? '' : 's'} blocked`
      : `${manualPlaceholders.length} hidden inline image${manualPlaceholders.length === 1 ? '' : 's'}`;
    bar.innerHTML = `
      <span>${label}</span>
      <button type="button" data-email-img-dismiss-all>Dismiss</button>
      <button type="button" data-email-img-load-all>Load all</button>
    `;
    bar.querySelector('[data-email-img-load-all]')?.addEventListener('click', ev => {
      ev.preventDefault();
      ev.stopPropagation();
      bar.remove();
      manualPlaceholders.forEach(loadPlaceholder);
    });
    bar.querySelector('[data-email-img-dismiss-all]')?.addEventListener('click', ev => {
      ev.preventDefault();
      ev.stopPropagation();
      bar.remove();
      manualPlaceholders.forEach(ph => ph.remove());
      _disableInlineImages().catch(() => {});
    });
    first.parentNode?.insertBefore(bar, first);
  }
  placeholders.forEach(ph => {
    if (ph.dataset.wired !== '1') {
      ph.dataset.wired = '1';
      const src = (ph.dataset.emailImgSrc || '').trim();
      const cid = (ph.dataset.emailImgCid || '').trim();
      const ctxRoot = reader.dataset?.emailUid
        ? reader
        : ph.closest('[data-email-uid]') || reader.closest?.('[data-email-uid]');
      const folder = ctxRoot?.dataset?.emailFolder || reader.dataset?.emailFolder || state._libFolder || 'INBOX';
      const account = ctxRoot?.dataset?.emailAccount || reader.dataset?.emailAccount || state._libAccountId || '';
      const uid = ctxRoot?.dataset?.emailUid || reader.dataset?.emailUid || '';
      const inlineUrl = () => {
        if (!uid || !cid) return '';
        const params = new URLSearchParams({ cid, folder });
        if (account) params.set('account_id', account);
        return `${API_BASE}/api/email/inline-image/${encodeURIComponent(uid)}?${params.toString()}`;
      };
      ph.querySelector('[data-email-img-download]')?.addEventListener('click', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        const url = inlineUrl();
        if (!url) return;
        const a = document.createElement('a');
        a.href = url;
        a.download = 'inline-image';
        document.body.appendChild(a);
        a.click();
        a.remove();
      });
    }
    const cid = (ph.dataset.emailImgCid || '').trim();
    if (autoLoadInlineImages && cid && ph.isConnected && ph.dataset.emailImgAutoRequested !== '1') {
      ph.dataset.emailImgAutoRequested = '1';
      loadPlaceholder(ph);
    }
  });
}

// ── Chat-bubble rendering for email threads ──
// Each parsed turn renders as a chat bubble. Bubbles for the active
// account's outgoing replies align right; everyone else aligns left.
// Order is reversed so the oldest message sits at the top of the
// conversation and the newest (the message currently being read) sits
// at the bottom — matches the mental model people have from chat.

function _meEmailAddrs() {
  const set = new Set();
  for (const a of (state._libAccounts || [])) {
    if (a && a.from_address) set.add(String(a.from_address).toLowerCase().trim());
    if (a && a.imap_user) set.add(String(a.imap_user).toLowerCase().trim());
  }
  return set;
}

// _parseTurnMeta / _formatBubbleDate / _formatRecipients / _senderColor /
// _initials live in ./emailLibrary/utils.js

function _renderTurnsAsBubbles(turns, data) {
  if (!Array.isArray(turns) || !turns.length) return '';
  const mineSet = _meEmailAddrs();
  const lvl0Email = String(data && data.from_address || '').toLowerCase().trim();
  const lvl0Mine = !!lvl0Email && mineSet.has(lvl0Email);
  const lvl0Author = (data && (data.from_name || data.from_address)) || '';
  const lvl0Date = _formatBubbleDate(data && data.date);

  // Newest reply on top, older history below. Turns come ordered shallow→deep
  // (level 0 = current reply, deeper levels = older quoted material) so we
  // render in source order without reversing.
  const ordered = turns.slice();

  // Gather per-turn sender identity + frequency for the no-self case below.
  const turnIdentity = ordered.map((t) => {
    if (t.level === 0) {
      return { email: lvl0Email, author: lvl0Author };
    }
    const p = _parseTurnMeta(t.meta || '');
    return { email: p.email, author: p.author };
  });
  const anyMine = turnIdentity.some(x => x.email && mineSet.has(x.email));
  // When the user isn't a participant in this thread (forwarded chains,
  // historical archives, etc.), assign the two most frequent senders to
  // opposite sides so the conversation still reads side-to-side. Third+
  // parties fall back to hash mod 2.
  const sideForKey = (() => {
    if (anyMine) return null;
    const freq = new Map();
    const firstSeen = new Map();
    turnIdentity.forEach((x, i) => {
      const key = (x.email || x.author || '').toLowerCase();
      if (!key) return;
      freq.set(key, (freq.get(key) || 0) + 1);
      if (!firstSeen.has(key)) firstSeen.set(key, i);
    });
    const sorted = [...freq.entries()]
      .sort((a, b) => (b[1] - a[1]) || (firstSeen.get(a[0]) - firstSeen.get(b[0])));
    const leftKey  = sorted[0] && sorted[0][0];
    const rightKey = sorted[1] && sorted[1][0];
    return (key) => {
      if (!key) return 'theirs';
      if (key === leftKey)  return 'theirs';
      if (key === rightKey) return 'mine';
      // Stable hash for 3rd+ parties.
      let h = 0;
      for (let i = 0; i < key.length; i++) h = ((h << 5) - h + key.charCodeAt(i)) | 0;
      return (h & 1) ? 'mine' : 'theirs';
    };
  })();

  const rows = ordered.map((t, i) => {
    let isMine, author, date;
    if (t.level === 0) {
      isMine = lvl0Mine;
      author = lvl0Author || 'Me';
      date = lvl0Date;
    } else {
      const p = _parseTurnMeta(t.meta || '');
      isMine = !!p.email && mineSet.has(p.email);
      author = p.author || (t.meta || 'Earlier reply');
      date = p.date;
    }
    // No-self fallback: route by per-sender side mapping.
    if (sideForKey) {
      const id = turnIdentity[i];
      const key = (id.email || id.author || '').toLowerCase();
      isMine = sideForKey(key) === 'mine';
    }
    const side = isMine ? 'mine' : 'theirs';
    const initials = _initials(author);
    const color = _senderColor(author || (t.level === 0 ? lvl0Email : ''));
    const head =
      `<div class="email-bubble-head">`
      + `<span class="email-bubble-author" style="color:${color}">${_esc(author)}</span>`
      + (date ? `<span class="email-bubble-date">${_esc(date)}</span>` : '')
      + `</div>`;
    const avatar = `<div class="email-bubble-avatar" aria-hidden="true" style="background:${color}">${_esc(initials)}</div>`;
    return (
      `<div class="email-bubble-row email-bubble-${side}" style="--bubble-accent:${color}">`
      + (isMine ? '' : avatar)
      + `<div class="email-bubble">`
      +   head
      +   `<div class="email-bubble-body">${_sanitizeHtml(t.body_html || '')}</div>`
      + `</div>`
      + (isMine ? avatar : '')
      + `</div>`
    );
  });
  return `<div class="email-bubbles">${rows.join('')}</div>`;
}

/**
 * Render server-cached thread turns (list of {level, body_html, meta})
 * into the same nested-card structure the client-side parser produces.
 */
function _renderTurnsFromServer(turns) {
  if (!Array.isArray(turns) || !turns.length) return '';
  let out = '';
  const stack = []; // [{ level, html }]
  const wrap = (t) =>
    `<details class="email-thread-turn email-quote-fold">`
    + _foldSummary('Earlier reply', _QUOTE_ICON, t.meta || '')
    + `<div class="email-thread-turn-body">${t.html}</div>`
    + '</details>';

  for (const t of turns) {
    if (t.level === 0) {
      while (stack.length) {
        const top = stack.pop();
        const w = wrap(top);
        if (stack.length) stack[stack.length - 1].html += w; else out += w;
      }
      out += _sanitizeHtml(t.body_html || '');
    } else {
      while (stack.length && stack[stack.length - 1].level > t.level) {
        const top = stack.pop();
        const w = wrap(top);
        if (stack.length) stack[stack.length - 1].html += w; else out += w;
      }
      if (!stack.length || stack[stack.length - 1].level < t.level) {
        stack.push({ level: t.level, meta: t.meta, html: _sanitizeHtml(t.body_html || '') });
      } else {
        stack[stack.length - 1].html += _sanitizeHtml(t.body_html || '');
        if (t.meta && !stack[stack.length - 1].meta) {
          stack[stack.length - 1].meta = t.meta;
        }
      }
    }
  }
  while (stack.length) {
    const top = stack.pop();
    const w = wrap(top);
    if (stack.length) stack[stack.length - 1].html += w; else out += w;
  }
  // Mark the bottom-most fold for rounded corners.
  const lastIdx = out.lastIndexOf('<details class="email-thread-turn email-quote-fold"');
  if (lastIdx >= 0) {
    out = out.slice(0, lastIdx)
        + out.slice(lastIdx).replace(
            'email-thread-turn email-quote-fold"',
            'email-thread-turn email-quote-fold last-fold"'
          );
  }
  return out;
}

/**
 * Parse an email body's reply chain into a stack of turn-cards.
 * Each turn = { author, date, bodyHtml, nested[] } where the body is
 * everything UP TO the next quote boundary, and `nested` is the sub-thread
 * inside (recursively parsed). Returns null if the email has no quoted
 * thread to parse (single message, no folds needed).
 */
// ── Talon-inspired multilingual quote-detection patterns ──
// Sources:
//   github.com/mailgun/talon (HTML/text quote detection)
//   github.com/crisp-oss/email-reply-parser (locale list)
//
// _TALON_* / _SIG_BLOAT_MIN_CHARS live in ./emailLibrary/utils.js
// _SIG_ICON / _QUOTE_ICON live in ./emailLibrary/signatureFold.js

function _renderThreadStructure(html) {
  if (!html || typeof html !== 'string' || html.length > 200000) return null;
  let doc;
  try { doc = new DOMParser().parseFromString(`<div id="__t">${html}</div>`, 'text/html'); }
  catch { return null; }
  const root = doc.getElementById('__t');
  if (!root) return null;

  // Find top-level blockquotes (not nested inside another blockquote).
  const tops = Array.from(root.querySelectorAll('blockquote')).filter(b =>
    !b.parentElement.closest('blockquote')
  );
  if (!tops.length) return null;

  // Build the current-message body: everything in root up to the first
  // top-level blockquote, minus the "On <date>, <author> wrote:" attribution
  // line that introduces it.
  const head = doc.createElement('div');
  let cursor = root.firstChild;
  while (cursor && cursor !== tops[0]) {
    const next = cursor.nextSibling;
    head.appendChild(cursor);
    cursor = next;
  }
  // Strip trailing "On <date>, <name> wrote:" / Outlook-style attribution
  // from `head` since the same info will appear in the turn header.
  let attribution = _harvestAttribution(head);

  // Recursively parse each top-level blockquote into a turn (and its nested chain).
  const turnsHtml = [];
  for (let i = 0; i < tops.length; i++) {
    const bq = tops[i];
    // The blockquote may have an Outlook-style "From: / Sent: / Subject:"
    // header inside as the first text. Extract that as the turn meta.
    const meta = _extractTurnMetaFromBlockquote(bq) || attribution || _extractQuoteMeta(bq.innerHTML);
    const innerHtml = bq.innerHTML;

    // Heuristic: if a blockquote has no detectable attribution (no "From:",
    // no "On <date>... wrote:") AND its content matches signature-style
    // patterns (corporate disclaimer, "registered in", legal notices, just
    // a name + title), treat it as a Signature fold instead of an Earlier
    // Reply. This stops mail clients that wrap signatures in <blockquote>
    // from making the signature appear as a phantom prior email.
    if (!meta && _looksLikeSignature(innerHtml)) {
      turnsHtml.push(
        '<details class="email-sig-fold">'
        + _foldSummary('Signature', _SIG_ICON)
        + `<div class="email-sig-body">${innerHtml}</div>`
        + '</details>'
      );
      attribution = null;
      continue;
    }

    // Recursively render the inside of this blockquote (which may contain
    // its own nested blockquotes representing earlier replies).
    const nested = _renderThreadStructure(innerHtml);
    const bodyHtml = nested || innerHtml;
    const isLast = i === tops.length - 1;
    turnsHtml.push(
      `<details class="email-thread-turn email-quote-fold${isLast ? ' last-fold' : ''}">`
        + _foldSummary('Earlier reply', _QUOTE_ICON, meta || '')
        + `<div class="email-thread-turn-body">${bodyHtml}</div>`
      + '</details>'
    );
    // Only the first turn uses the harvested attribution; deeper turns
    // get their own from inside the blockquote.
    attribution = null;
  }

  return head.innerHTML + turnsHtml.join('');
}

// Looks like a signature / corporate disclaimer rather than a quoted email.
// Used to demote attribution-less blockquotes that some senders wrap their
// sig+disclaimer in (Outlook, EY, big firms) from "Earlier reply" to a
// proper Signature fold. Conservative — only fires when there's no quoted
// reply markers AND it matches strong corporate-noise phrases.
// _looksLikeSignature / _harvestAttribution / _extractTurnMetaFromBlockquote
// live in ./emailLibrary/signatureFold.js

/**
 * Wrap any quoted reply chain in a collapsed <details> so deep email threads
 * don't dominate the reader. Detects:
 *   - <blockquote> tags (Gmail / native quoted replies)
 *   - Outlook-style "From: ... Sent: ... To: ... Subject: ..." headers
 * Each gets its own "Earlier thread" toggle.
 */
/**
 * Parse a plaintext email body into stacked turn-cards by walking
 * `> ` quote-prefix levels and Outlook-style "On X wrote:" / Original-Message
 * boundaries. Returns rendered HTML, or null when there's no quoted content
 * (caller falls back to flat rendering).
 *
 * Mirrors talon's `extract_from_plain` and email-reply-parser fragments:
 *   1. Lines starting with one or more `>` chars are quoted (level = count of >).
 *   2. Increasing the level opens a deeper turn (nested reply).
 *   3. `-----Original Message-----` and `On <date>, <name> wrote:` start a
 *      new turn even without `>`.
 *   4. The leading non-quoted segment is the current message.
 */
function _renderPlaintextThread(text) {
  if (!text || typeof text !== 'string' || text.length > 200000) return null;
  const lines = text.split(/\r?\n/);
  const levels = lines.map(l => {
    const m = l.match(/^((?:>\s?)+)/);
    return m ? (m[1].match(/>/g) || []).length : 0;
  });
  const hasQuotes = levels.some(l => l > 0);
  const attribLineRe = new RegExp(`(?:^|\\n)\\s*On\\s.+?\\s${_TALON_WROTE}\\s*:\\s*$`, 'im');
  const hasAttrib = attribLineRe.test(text) || _TALON_ORIG_RE.test(text);
  if (!hasQuotes && !hasAttrib) return null;

  const turns = [];
  let buf = [];
  let curLevel = 0;
  let pendingMeta = null;
  const flush = () => {
    if (!buf.length) return;
    const t = buf.join('\n').trimEnd();
    if (t || curLevel > 0) turns.push({ level: curLevel, text: t, meta: pendingMeta });
    buf = [];
    pendingMeta = null;
  };
  for (let i = 0; i < lines.length; i++) {
    const lvl = levels[i];
    const raw = lines[i];
    const stripped = lvl > 0 ? raw.replace(/^(?:>\s?)+/, '') : raw;
    const isSeparatorLine = lvl === 0 && /^-{5,}\s*Previous message\s*-{5,}$/i.test(raw.trim());
    const isAttribLine = lvl === 0
      && (new RegExp(`^\\s*On\\s.+?\\s${_TALON_WROTE}\\s*:\\s*$`, 'i').test(raw)
          || _TALON_ORIG_RE.test('\n' + raw));
    if (isSeparatorLine || isAttribLine) {
      flush();
      pendingMeta = isSeparatorLine ? null : (_extractQuoteMeta(raw) || raw.trim());
      curLevel = 1;
      continue;
    }
    if (lvl !== curLevel) {
      flush();
      curLevel = lvl;
    }
    buf.push(stripped);
  }
  flush();

  if (!turns.length || (turns.length === 1 && turns[0].level === 0)) return null;

  const fmt = s => _escLinkify(s).replace(/\n/g, '<br>');
  let out = '';
  const stack = [];
  const wrapTurn = (t) =>
    `<details class="email-thread-turn email-quote-fold">`
    + _foldSummary('Earlier reply', _QUOTE_ICON, t.meta || '')
    + `<div class="email-thread-turn-body">${t.html}</div>`
    + '</details>';

  for (const t of turns) {
    if (t.level === 0) {
      while (stack.length) {
        const top = stack.pop();
        const wrapped = wrapTurn(top);
        if (stack.length) stack[stack.length - 1].html += wrapped; else out += wrapped;
      }
      out += fmt(t.text);
    } else {
      while (stack.length && stack[stack.length - 1].level > t.level) {
        const top = stack.pop();
        const wrapped = wrapTurn(top);
        if (stack.length) stack[stack.length - 1].html += wrapped; else out += wrapped;
      }
      if (!stack.length || stack[stack.length - 1].level < t.level) {
        stack.push({ level: t.level, meta: t.meta, html: fmt(t.text) });
      } else {
        stack[stack.length - 1].html += '<br>' + fmt(t.text);
        if (t.meta && !stack[stack.length - 1].meta) stack[stack.length - 1].meta = t.meta;
      }
    }
  }
  while (stack.length) {
    const top = stack.pop();
    const wrapped = wrapTurn(top);
    if (stack.length) stack[stack.length - 1].html += wrapped; else out += wrapped;
  }
  const lastIdx = out.lastIndexOf('<details class="email-thread-turn email-quote-fold"');
  if (lastIdx >= 0) {
    out = out.slice(0, lastIdx)
        + out.slice(lastIdx).replace(
            'email-thread-turn email-quote-fold"',
            'email-thread-turn email-quote-fold last-fold"'
          );
  }
  return out;
}

// _foldSummary / _extractQuoteMeta / _SIG_ICON / _QUOTE_ICON
// live in ./emailLibrary/signatureFold.js

function _foldQuotedReplies(html) {
  if (!html || typeof html !== 'string') return html;
  if (html.length > 200000) return html;
  const before = html;
  // Use DOMParser for proper nested-blockquote handling. Regex against HTML
  // mishandles nesting and leaves orphan close tags that the browser
  // re-balances, producing two visually inconsistent fold styles.
  try {
    const doc = new DOMParser().parseFromString(`<div id="__r">${html}</div>`, 'text/html');
    const root = doc.getElementById('__r');
    if (root) {
      // Only fold TOP-LEVEL blockquotes (children of the root that are not
      // already inside another blockquote). The inner blockquote chain stays
      // intact inside the fold and renders with the existing
      // .email-quote-fold blockquote styles, so everything matches.
      const tops = Array.from(root.querySelectorAll('blockquote')).filter(b =>
        !b.parentElement.closest('blockquote')
      );
      if (tops.length) {
        for (const bq of tops) {
          const det = doc.createElement('details');
          det.className = 'email-quote-fold';
          // Build the summary as raw HTML — easier than building DOM by hand.
          const summary = _foldSummary('Earlier thread', _QUOTE_ICON, _extractQuoteMeta(bq.innerHTML));
          det.innerHTML = summary;
          bq.parentNode.insertBefore(det, bq);
          det.appendChild(bq); // move the original blockquote (and any nested ones) into the details
        }
        // Tag only the last fold so CSS can give it rounded bottom corners.
        const allFolds = root.querySelectorAll('.email-quote-fold');
        if (allFolds.length) allFolds[allFolds.length - 1].classList.add('last-fold');
        return root.innerHTML;
      }
    }
  } catch (e) {
    // Fall through to the legacy regex path below if DOMParser fails
  }
  // If DOM-pass already wrapped something, we returned above. Otherwise no
  // blockquotes were found — try the Outlook-header heuristic.
  if (html !== before) return html;
  // Outlook-style quoted-reply header — multilingual. Fold from the first
  // "From: ... Sent: ... Subject: ..." block through end-of-body so all
  // prior thread levels collapse together.
  const FROM = '(?:From|Från|Von|De|De\\s|Da|От|Od|Van)';
  const SENT = '(?:Sent|Skickat|Gesendet|Envoyé|Inviato|Enviado|Verzonden|Отправлено|Wysłane)';
  const SUBJ = '(?:Subject|Ämne|Betreff|Objet|Oggetto|Asunto|Onderwerp|Тема|Temat)';
  const outlookRe = new RegExp(
    `(<br\\s*/?>|</p>|</div>|<p[^>]*>|<div[^>]*>|\\n)\\s*((?:<[^>]+>\\s*)*${FROM}\\s*:\\s*[^<\\n]+(?:<[^>]+>\\s*|\\s)*${SENT}\\s*:[\\s\\S]+?${SUBJ}\\s*:[\\s\\S]+)$`,
    'i'
  );
  const m = html.match(outlookRe);
  if (m) {
    const idx = html.lastIndexOf(m[0]);
    // Outlook fallback only ever produces ONE fold, so tag it as last.
    html = html.slice(0, idx) + m[1]
      + '<details class="email-quote-fold last-fold">'
      + _foldSummary('Earlier thread', _QUOTE_ICON, _extractQuoteMeta(m[2]))
      + m[2] + '</details>';
  }
  return html;
}
