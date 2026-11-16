import { dismissOrRemove, bindMenuDismiss } from '../host.js';
import { API_BASE, activeDocId, docs } from "../../application/documentState.js";
import { _hasRichHtmlElement, _htmlToPlainText } from "../../domain/documentKinds.js";

import { _setEmailBodyText, _splitEmailReplyQuote } from "./emailBody.js";
import { _emailRichbodyActive } from "../richEditor/richBodySync.js";
import { readEmailReplyResponse, sessionModule, uiModule } from "../host.js";

const _AI_REPLY_CONTEXT_STORE_PREFIX = 'odysseus:email-ai-reply-context:v1:';

async function _aiReply(opts = {}) {
    const { mode = 'auto', noteHint = '', contextKey = '' } = (opts || {});
    const to = document.getElementById('doc-email-to')?.value?.trim() || '';
    const subject = document.getElementById('doc-email-subject')?.value?.trim() || '';
    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea) return;
    const currentBody = textarea.value || '';
    const inReplyTo = document.getElementById('doc-email-in-reply-to')?.value?.trim() || '';
    const sourceUid = document.getElementById('doc-email-source-uid')?.value?.trim() || '';
    const sourceFolder = document.getElementById('doc-email-source-folder')?.value?.trim() || 'INBOX';
    const sourceAccountId = docs.get(activeDocId)?.sourceEmailAccountId || window.__odysseusActiveEmailAccount || '';
    const cleanAiReplyText = (text) => {
      if (!text) return '';
      let t = String(text);
      const open = /<<<\s*(?:REPLY|SUMMARY|OUTPUT)\s*>>+/i;
      const close = /<<<\s*END\s*>>+/i;
      const m = open.exec(t);
      if (m) {
        const rest = t.slice(m.index + m[0].length);
        const c = close.exec(rest);
        t = c ? rest.slice(0, c.index) : rest;
      }
      return t
        .replace(/<<<\s*(?:REPLY|SUMMARY|OUTPUT)\s*>>+/gi, '')
        .replace(/<<<\s*END\s*>>+/gi, '')
        .replace(/<\/?\|(?:assistant|assistan|user|system|tool)\|>?|<\/\|end\|>?/gi, '')
        .trim();
    };
    const splitCurrent = _splitEmailReplyQuote(currentBody);
    const ownBodyHtml = String(splitCurrent.body || '');
    const ownText = _htmlToPlainText(ownBodyHtml).trim();
    const hasRichElement = _hasRichHtmlElement(ownBodyHtml);
    const isReplaceableDraft = (!ownText && !hasRichElement) || /^(\[AI reply draft will appear here\]|Drafting AI reply)/i.test(ownText);
    if (!isReplaceableDraft) {
      if (uiModule) uiModule.showToast('Reply already has text');
      return;
    }

    // Keep the request tied to the exact draft state that the user approved.
    // AI generation is asynchronous; a late response must never replace text
    // the user typed while it was in flight.
    const generationId = ++_emailAiReplyGeneration;
    const generationDocId = activeDocId;
    let generationBody = currentBody;
    const generationRich = _emailRichbodyActive();
    const richDraftSnapshot = () => {
      if (!generationRich) return '';
      const clone = generationRich.cloneNode(true);
      // Focusing an empty reply inserts a caret slot, not a user edit.
      clone.querySelectorAll('.email-reply-edit-slot').forEach(slot => {
        if (!slot.textContent.trim() && !slot.querySelector('img,video,audio,iframe,table')) slot.remove();
      });
      return clone.innerHTML;
    };
    let generationRichHtml = richDraftSnapshot();
    let manuallyEdited = false;
    const markEdited = () => { manuallyEdited = true; };
    textarea.addEventListener('input', markEdited);
    generationRich?.addEventListener('input', markEdited);
    const draftStillUnchanged = () => (
      generationId === _emailAiReplyGeneration &&
      !manuallyEdited &&
      activeDocId === generationDocId &&
      textarea.value === generationBody &&
      (!generationRich || richDraftSnapshot() === generationRichHtml)
    );

    // Use the current chat model
    let currentModel = '';
    let currentSessionId = '';
    try {
      currentModel = sessionModule?.getCurrentModel() || '';
      currentSessionId = sessionModule?.getCurrentSessionId() || '';
    } catch (_) {}

    const btn = document.getElementById('doc-email-ai-reply-btn');
    if (btn) { btn.disabled = true; btn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" style="vertical-align:-1px;margin-right:3px"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg>Drafting...'; }
    if (uiModule) uiModule.showToast('Writing AI reply', {
      duration: 8000,
      leadingIcon: 'spinner',
      aiReplyProgress: true,
    });

    try {
      // Empty-compose path: if there's no original body, send a placeholder
      // so the backend's "no body" guard doesn't fail. The user_hint carries
      // the user's compose intent; the model uses To/Subject + that hint.
      const bodyForApi = opts.originalBody || splitCurrent.quote || currentBody || (noteHint ? '(no prior email -- compose from the user instructions)' : '');
      const res = await fetch(`${API_BASE}/api/email/ai-reply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          to: to,
          subject: subject,
          original_body: bodyForApi,
          stream: true,
          model: currentModel,
          session_id: currentSessionId,
          message_id: inReplyTo,
          uid: sourceUid,
          folder: sourceFolder,
          account_id: sourceAccountId,
          fast: mode !== 'ai-reply-full',
          user_hint: noteHint || '',
        }),
      });
      const data = await readEmailReplyResponse(res, text => {
        if (!draftStillUnchanged()) return false;
        const quote = splitCurrent.quote || '';
        _setEmailBodyText(textarea, text + (quote ? `\n\n${quote}` : ''));
        generationBody = textarea.value;
        generationRichHtml = richDraftSnapshot();
        return true;
      });
      if (!res.ok) {
        throw new Error(data.error || `AI reply service returned HTTP ${res.status}`);
      }
      if (data.success && data.reply) {
        if (!draftStillUnchanged()) {
          if (uiModule) uiModule.showToast('AI reply ready, but draft was edited', { aiReplyResult: true });
          return;
        }
        let cleanReply = cleanAiReplyText(data.reply);
        // Strip any "On <date>, <name> wrote:" attribution + everything
        // after it from the AI's output — the model sometimes re-quotes
        // the original thread, and we already have the real quote in
        // currentBody. Without this, AI's invented quote stacked on top
        // of the real one and looked like the history had been "edited".
        cleanReply = cleanReply.replace(/\n*On\b[\s\S]*?\bwrote:[\s\S]*$/m, '').trim();
        const quote = splitCurrent.quote || '';
        const newBody = cleanReply + (quote ? `\n\n${quote}` : '');
        // Reconcile the final body only while this generation still owns the draft.
        if (!draftStillUnchanged()) {
          if (uiModule) uiModule.showToast('AI reply ready, but draft was edited', { aiReplyResult: true });
          return;
        }
        _setEmailBodyText(textarea, newBody);
        _clearDocAiReplyContext(contextKey || _docAiReplyContextKey());
        if (uiModule) uiModule.showToast(`AI draft inserted (${data.model_used || 'AI'})`, { aiReplyResult: true });
        return true;
      } else {
        if (draftStillUnchanged()) _setEmailBodyText(textarea, currentBody);
        const rawMsg = data.error || 'Failed to generate reply';
        const msg = /empty response/i.test(rawMsg)
          ? 'AI reply failed: AI returned empty response.'
          : rawMsg;
        if (uiModule) uiModule.showError(msg);
      }
    } catch (e) {
      if (draftStillUnchanged()) _setEmailBodyText(textarea, currentBody);
      if (uiModule) uiModule.showError(`AI reply failed: ${e?.message || 'Unable to reach the AI reply service'}`);
    } finally {
      textarea.removeEventListener('input', markEdited);
      generationRich?.removeEventListener('input', markEdited);
      if (btn) { btn.disabled = false; btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style="color:var(--accent, var(--red));flex-shrink:0;position:relative;top:-1px;"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg><span style="font-size:11px;margin-left:4px;">Reply</span>'; }
    }
  }

function _clearDocAiReplyContext(key) {
    if (!key) return;
    try { localStorage.removeItem(key); } catch (_) {}
  }

function _closeDocAiReplyChoice() {
    if (_docAiReplyChoiceMenu) {
      // Tear down through the menu's registered dismiss (drops its outside-click
      // listener + Escape-stack entry) rather than orphaning them with a raw
      // remove(); the onClose below nulls the ref.
      try { dismissOrRemove(_docAiReplyChoiceMenu); } catch (_) {}
      _docAiReplyChoiceMenu = null;
    }
  }

let _docAiReplyChoiceMenu = null;

function _docAiReplyContextKey() {
    try {
      const sourceUid = document.getElementById('doc-email-source-uid')?.value?.trim() || '';
      const sourceFolder = document.getElementById('doc-email-source-folder')?.value?.trim() || 'INBOX';
      const inReplyTo = document.getElementById('doc-email-in-reply-to')?.value?.trim() || '';
      const to = document.getElementById('doc-email-to')?.value?.trim() || '';
      const subject = document.getElementById('doc-email-subject')?.value?.trim() || '';
      const stable = sourceUid
        ? `uid:${sourceFolder}:${sourceUid}`
        : inReplyTo
          ? `msg:${inReplyTo}`
          : activeDocId
            ? `doc:${activeDocId}`
            : `compose:${to}:${subject}`;
      return _AI_REPLY_CONTEXT_STORE_PREFIX + stable;
    } catch (_) {
      return '';
    }
  }

let _emailAiReplyGeneration = 0;

function _loadDocAiReplyContext(key) {
    if (!key) return '';
    try { return localStorage.getItem(key) || ''; } catch (_) { return ''; }
  }

function _saveDocAiReplyContext(key, value) {
    if (!key) return;
    try {
      const text = String(value || '');
      if (text.trim()) localStorage.setItem(key, text);
      else localStorage.removeItem(key);
    } catch (_) {}
  }

export function _showDocAiReplyChoice(btn) {
    _closeDocAiReplyChoice();
    if (!btn) return;
    const rect = btn.getBoundingClientRect();
    const menu = document.createElement('div');
    menu.className = 'doc-ai-reply-choice';
    const menuMaxW = Math.min(240, window.innerWidth - 16);
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - menuMaxW - 8));
    const estHeight = 150;
    const spaceBelow = window.innerHeight - rect.bottom - 8;
    const spaceAbove = rect.top - 8;
    const top = (spaceBelow >= estHeight || spaceBelow >= spaceAbove)
      ? Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - estHeight - 8))
      : Math.max(8, rect.top - estHeight - 6);
    menu.style.cssText = [
      'position:fixed',
      `left:${left}px`,
      `top:${top}px`,
      `max-width:${menuMaxW}px`,
      'box-sizing:border-box',
      'z-index:10060',
      'display:flex',
      'gap:6px',
      'padding:6px',
      'background:var(--bg,#111)',
      'border:1px solid var(--border,#333)',
      'border-radius:7px',
      'box-shadow:0 8px 24px rgba(0,0,0,.28)',
    ].join(';');
    menu.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:6px;min-width:200px;">
        <textarea data-note-input rows="2" placeholder="Context (optional)" style="width:100%;box-sizing:border-box;resize:vertical;min-height:42px;font-family:inherit;font-size:11px;padding:5px 6px;border-radius:5px;border:1px solid var(--border,#333);background:var(--bg-elev,#1a1a1a);color:var(--fg);"></textarea>
        <div style="display:flex;align-items:center;gap:4px;">
          <button class="memory-toolbar-btn" data-mode="ai-reply-fast" title="Draft reply" style="display:inline-flex;align-items:center;justify-content:center;gap:5px;flex:1;">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" style="color:var(--accent, var(--red));"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg>
            Submit
          </button>
        </div>
      </div>
    `;
    const noteInput = menu.querySelector('[data-note-input]');
    const contextKey = _docAiReplyContextKey();
    if (noteInput) {
      noteInput.value = _loadDocAiReplyContext(contextKey);
      noteInput.addEventListener('input', () => {
        _saveDocAiReplyContext(contextKey, noteInput.value || '');
      });
    }
    setTimeout(() => noteInput?.focus(), 0);
    menu.addEventListener('mousedown', (ev) => ev.stopPropagation());
    document.body.appendChild(menu);
    _docAiReplyChoiceMenu = menu;
    // Outside-click AND Escape both route through the central esc-stack via
    // bindMenuDismiss; onClose owns the actual teardown (node removal + state).
    const close = bindMenuDismiss(menu, () => {
      try { menu.remove(); } catch (_) {}
      if (_docAiReplyChoiceMenu === menu) _docAiReplyChoiceMenu = null;
    });
    menu.addEventListener('click', async (ev) => {
      const choice = ev.target.closest('[data-mode]');
      if (!choice) return;
      ev.preventDefault();
      ev.stopPropagation();
      const mode = choice.getAttribute('data-mode') || 'ai-reply-fast';
      const noteHint = (noteInput?.value || '').trim();
      _saveDocAiReplyContext(contextKey, noteInput?.value || '');
      close();
      await _aiReply({ mode, noteHint, contextKey });
    });
  }

export async function generateEmailReply(opts = {}) {
    return _aiReply(opts);
  }
