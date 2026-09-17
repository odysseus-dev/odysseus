// static/js/sttTranscriptDoc.js
//
// Raw-transcript document helpers for uploaded-audio STT (#6319 follow-up).
//
// The raw document is `<stem>_raw.md` created through the existing
// POST /api/document route — no new backend, no LLM step, no cleanup.
// This module is DOM-free at import time (safe for node:test); document.js
// is loaded lazily only when opening a viewer in the browser.

/** `meeting.m4a` -> `meeting_raw.md`. Pure string transform. */
export function rawDocTitle(name) {
  const base = (name || 'transcript').split('/').pop().split('\\').pop();
  const stem = base.replace(/\.[^.]+$/, '') || 'transcript';
  return stem + '_raw.md';
}

/**
 * Build the combined transcript markdown from per-recording entries in
 * queue order. Pure string transform (DOM-free, unit-testable).
 * Entries: [{name, text}]. Empty/failed recordings are skipped so a single
 * failure never blocks the batch; transcript text is preserved verbatim —
 * no cleanup, summary, translation or rewrite.
 */
export function buildCombinedMarkdown(entries) {
  const ok = (entries || []).filter((e) => e && typeof e.text === 'string' && e.text);
  const parts = ok.map((e) => {
    const base = String(e.name || 'recording').split('/').pop().split('\\').pop();
    const stem = base.replace(/\.[^.]+$/, '') || base;
    return '## ' + stem + '\n\n' + e.text;
  });
  return '# Audio Transcripts\n\n' + parts.join('\n\n---\n\n');
}



/** Current chat session id when available; "" means a library document. */
export function currentSessionId() {
  try {
    if (window.sessionModule && window.sessionModule.getCurrentSessionId) {
      return window.sessionModule.getCurrentSessionId() || '';
    }
  } catch (_) { /* non-browser (tests) or unavailable */ }
  return '';
}

/**
 * Persist transcript text verbatim as a markdown document.
 * Returns {id, title}; throws Error with a user-facing message on failure.
 * Never re-runs transcription and never transforms the text.
 */
export async function saveRawTranscriptDoc({ name, transcript, sessionId }) {
  if (!transcript) throw new Error('Empty transcript');
  const body = {
    title: rawDocTitle(name),
    content: transcript,
    language: 'markdown',
  };
  const sid = sessionId !== undefined ? sessionId : currentSessionId();
  if (sid) body.session_id = sid;
  const res = await fetch('/api/document', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let msg = 'Could not save document';
    try { const e = await res.json(); msg = e.detail || msg; } catch (_) {}
    throw new Error(typeof msg === 'string' ? msg : 'Could not save document');
  }
  const doc = await res.json();
  if (!doc || !doc.id) throw new Error('Could not save document');
  return { id: doc.id, title: doc.title || body.title };
}

/** Open a document in the existing viewer; toast fallback if unavailable. */
export function openRawDocument(docId) {
  if (!docId) return;
  import('./document.js?v=20260815approvalsave1').then((mod) => {
    const open = mod.loadDocument
      || mod.openDocument
      || (mod.default && (mod.default.loadDocument || mod.default.openDocument));
    if (open) open(docId);
    else if (window.showToast) window.showToast('Document saved — open it from Documents');
  }).catch(() => {
    if (window.showToast) window.showToast('Document saved — open it from Documents');
  });
}
