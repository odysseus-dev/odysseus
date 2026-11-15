// ui/email/composerWiring.js

import { API_BASE, _closingDocs, _deletedDocIds, _documentNavigationGeneration, _inFlightDeletes, _lastSessionId, _minimizedDocId, activeDocId, beginDelete, bumpNavigationGeneration, clearDocuments, docs, endDelete, isOpen, isPanelOpen, markDeleted, registerDocument, releaseClosingDraft, removeDocument, retainClosingDraft, setActiveDocId, setApiBase, setLastSessionId, setMinimizedDocId, setPanelOpen } from '../../application/documentState.js';

import { _autoSaveDebounce, _cancelAutoSave, _emailRichbodySaveDebounce, _flushAutoSave, _markDocumentDirty, _pendingAutoSaves, _resetPersistenceHooksForTesting, _saveClosingDraft, _scheduleAutoSave, _setDocumentSaveState, awaitWrites, bindPersistenceHooks, runSerializedWrite, saveDocument } from '../../application/persistence.js';

import { AUTO_DETECT_DELAY, AUTO_DETECT_MIN_CHARS, AUTO_DETECT_MIN_RELEVANCE, AUTO_DETECT_SAMPLE_SIZE, HLJS_TO_DROPDOWN, _DOC_SAVE_STATE, _buildDiffSummary, _esc, _escHtml, _hasViewToggle, _isDocxLang, _isMarkdownImageFile, _isRenderLang, _isRichTextLang, _looksLikeMarkdown, _markdownImageAlt } from '../../domain/documentKinds.js';

import { _handleMarkdownImageUpload, _uploadMarkdownImages } from '../editor/markdownImages.js';

import { _showDocAiReplyChoice } from './aiReply.js';

import { _handleAttachUpload, _renderComposeAttachments, _showComposeAttachMenu, _stageForwardedSourceAttachments, _uploadComposeFiles } from './attachments.js';

import { _hideEmailFields, _maybeAutoCollapseEmailHeader, _setEmailHeaderCollapsed, _showEmailFields, _syncEmailHeaderSummary, _syncStreamingEmailFields } from './composerFields.js';

import { _captureEmailBodyFocusState, _emailBodyToHtml, _emailReplyOwnText, _emailStreamAnimFrame, _emailStreamRenderedBody, _emailStreamTargetBody, _focusEmailBodyEnd, _renderStreamingEmailBody, _restoreEmailBodyFocusState, _setEmailBodyText, _splitEmailReplyQuote } from './emailBody.js';

import { _clearCurrentEmailLocalDraft, _clearEmailLocalDraft, _emailFieldsWithLocalDraft, _emailLocalDraftDebounce, _persistEmailLocalDraftNow, _persistEmailLocalDraftSoon, _sanitizeOutgoingEmailBody } from './localDraft.js';

import { _wireRecipientAutocomplete } from './recipients.js';

import { _discardEmail, _markUnreadAndClose, _saveDraft, _scheduleSend, _sendEmail } from './sendWorkflow.js';

import { _showRichImageSourceMenu } from '../richEditor/imageMenus.js';

import { _emailHtmlToPlainText, _emailPlainTextToHtml, _emailRichbodyActive, _richTextContentToHtml, _sanitizedRichTextHtml, _scheduleEmailRichbodySave, _syncEmailRichbody, _syncRichEmptyImport } from '../richEditor/richBodySync.js';

import { _activeDocLanguage, _closeDocumentOutline, _documentOutlineEntries, _documentOutlineSupported, _openDocumentOutline, _refreshDocumentOutline, _renderDocumentSaveState, _renderDocumentStats, _scheduleDocumentHistoryControls, _scheduleDocumentStats, _setDocumentHistoryControlState, _syncDocumentHistoryControlsNow, _syncEditorPlaceholder } from '../status/documentStatus.js';

import { Modals, attachColorPicker, bindMenuDismiss, closeLibrary, codeRunnerModule, dismissOrRemove, dismissTopMenu, documentLibrary, emojiPicker, getDocumentStats, initLibrary, isLibraryOpen, langIcon, markdownModule, openLibrary, parseMarkdownOutline, readEmailReplyResponse, sessionModule, signatureModule, spinnerModule, topPortalZ, uiModule } from '../host.js';



export function bindComposerWiring({ pane, paneLifetime }) {
const emojiSlot = document.getElementById('md-toolbar-emoji-slot');
if (emojiSlot && !emojiSlot.querySelector('.emoji-picker-btn')) {
      // Resolve the live target on click: the WYSIWYG email contenteditable
      // when active, otherwise the plain markdown textarea.
      emojiSlot.appendChild(emojiPicker.createEmojiButton(
        () => _emailRichbodyActive() || document.getElementById('doc-editor-textarea')
      ));
    }
const handleSendIntent = (e) => {
      if (e && e.__odysseusEmailSendHandled) return;
      const rawTarget = e && e.target;
      const target = rawTarget && rawTarget.nodeType === Node.TEXT_NODE ? rawTarget.parentElement : rawTarget;
      const targetBtn = target && target.closest ? target.closest('#doc-email-send-btn') : null;
      const btn = targetBtn || null;
      if (!btn || btn.disabled) return;
      if (e) {
        e.preventDefault();
        e.stopPropagation();
        e.__odysseusEmailSendHandled = true;
      }
      const _m = document.getElementById('doc-email-more-menu');
      if (_m) _m.style.display = 'none';
      document.getElementById('doc-email-send-caret')?.setAttribute('aria-expanded', 'false');
      _sendEmail();
    };
window.odysseusEmailSendIntent = handleSendIntent;
if (!window._emailSendDelegatedBoundV3) {
      window._emailSendDelegatedBoundV3 = true;
      ['pointerdown', 'mousedown', 'pointerup', 'click'].forEach((type) => {
        window.addEventListener(type, handleSendIntent, true);
        document.addEventListener(type, handleSendIntent, true);
      });
    }
let lastCaretToggleAt = 0;
const toggleSendMenu = (caret) => {
      const menu = document.getElementById('doc-email-more-menu');
      if (!menu) return;
      const opening = menu.style.display === 'none';
      menu.style.display = opening ? '' : 'none';
      if (caret) caret.setAttribute('aria-expanded', String(opening));
    };
const handleCaretIntent = (e) => {
      if (e && e.__odysseusEmailCaretHandled) return;
      const now = Date.now();
      if (e && e.type === 'click' && now - lastCaretToggleAt < 350) {
        e.preventDefault();
        e.stopPropagation();
        e.__odysseusEmailCaretHandled = true;
        return;
      }
      const rawTarget = e && e.target;
      const target = rawTarget && rawTarget.nodeType === Node.TEXT_NODE ? rawTarget.parentElement : rawTarget;
      const targetCaret = target && target.closest ? target.closest('#doc-email-send-caret') : null;
      const caret = targetCaret || null;
      if (!caret) return;
      if (e) {
        e.preventDefault();
        e.stopPropagation();
        e.__odysseusEmailCaretHandled = true;
      }
      lastCaretToggleAt = now;
      toggleSendMenu(caret);
    };
window.odysseusEmailCaretIntent = handleCaretIntent;
if (!window._emailCaretDelegatedBoundV1) {
      window._emailCaretDelegatedBoundV1 = true;
      ['pointerdown', 'mousedown', 'click'].forEach((type) => {
        window.addEventListener(type, handleCaretIntent, true);
        document.addEventListener(type, handleCaretIntent, true);
      });
    }
if (!window._emailCtrlEnterBound) {
      window._emailCtrlEnterBound = true;
      document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
          const doc = activeDocId && docs.get(activeDocId);
          if (doc && doc.language === 'email' && isOpen) {
            e.preventDefault();
            _sendEmail();
          }
        }
      });
    }
if (!window._docMdToggleBound) {
      window._docMdToggleBound = true;
      document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.altKey && !e.shiftKey && (e.key === 'm' || e.key === 'M' || e.code === 'KeyM')) {
          if (!isOpen) return;
          const doc = activeDocId && docs.get(activeDocId);
          const lang = (doc?.language || 'markdown').toLowerCase();
          if (lang !== 'markdown') return;
          e.preventDefault();
          toggleMarkdownPreview();
          _syncHeaderActions();
        }
      });
    }
if (!window._docStickyTypingBound) {
      window._docStickyTypingBound = true;
      window.addEventListener('keydown', (e) => {
        if (!isOpen || e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
        const textarea = document.getElementById('doc-editor-textarea');
        if (!textarea || textarea.disabled || textarea.readOnly || textarea.getClientRects().length === 0) return;

        const target = e.target instanceof Element ? e.target : null;
        if (
          target?.matches('input, textarea, select, button, a, [contenteditable="true"]')
          || target?.closest('input, textarea, select, button, a, [contenteditable="true"]')
          || target?.isContentEditable
        ) return;

        let replacement = null;
        if (e.key.length === 1) replacement = e.key;
        else if (e.key === 'Enter') replacement = '\n';
        else if (e.key === 'Backspace' || e.key === 'Delete') {
          const start = textarea.selectionStart;
          const end = textarea.selectionEnd;
          if (start === end) {
            if (e.key === 'Backspace' && start > 0) textarea.setSelectionRange(start - 1, start);
            else if (e.key === 'Delete' && end < textarea.value.length) textarea.setSelectionRange(end, end + 1);
          }
          replacement = '';
        }
        if (replacement === null) return;

        textarea.focus({ preventScroll: true });
        textarea.setRangeText(replacement, textarea.selectionStart, textarea.selectionEnd, 'end');
        textarea.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: replacement ? 'insertText' : 'deleteContentBackward', data: replacement || null }));
        e.preventDefault();
        e.stopPropagation();
      }, true);
    }
document.getElementById('doc-email-draft-btn')?.addEventListener('click', () => {
      document.getElementById('doc-email-more-menu').style.display = 'none';
      _saveDraft();
    });
document.getElementById('doc-email-discard-btn')?.addEventListener('click', _discardEmail);
document.getElementById('doc-email-unread-btn')?.addEventListener('click', () => {
      document.getElementById('doc-email-more-menu').style.display = 'none';
      _markUnreadAndClose();
    });
document.getElementById('doc-email-schedule-btn')?.addEventListener('click', (e) => {
      const anchor = document.getElementById('doc-email-send-caret') || e.currentTarget;
      document.getElementById('doc-email-more-menu').style.display = 'none';
      _scheduleSend(anchor);
    });
document.getElementById('doc-email-ai-reply-btn')?.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      _showDocAiReplyChoice(ev.currentTarget);
    });
const collapseBtn = document.getElementById('doc-email-collapse-btn');
if (collapseBtn && !collapseBtn._emailCollapseWired) {
      collapseBtn._emailCollapseWired = true;
      collapseBtn.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const focusState = _captureEmailBodyFocusState();
        const header = document.getElementById('doc-email-header');
        const nextCollapsed = !header?.classList.contains('doc-email-header-collapsed');
        _setEmailHeaderCollapsed(nextCollapsed);
        if (!nextCollapsed) _restoreEmailBodyFocusState(focusState);
      });
      collapseBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
      });
    }
['doc-email-to', 'doc-email-cc', 'doc-email-bcc', 'doc-email-subject'].forEach(id => {
      document.getElementById(id)?.addEventListener('input', () => {
        _syncEmailHeaderSummary();
        saveCurrentToMap();
        _persistEmailLocalDraftSoon();
        _scheduleAutoSave(activeDocId, 800);
      });
      document.getElementById(id)?.addEventListener('focus', () => _setEmailHeaderCollapsed(false, { manual: false }));
    });
document.getElementById('doc-email-richbody')?.addEventListener('focus', _maybeAutoCollapseEmailHeader);
if (window.visualViewport && !window._docEmailViewportCollapseBound) {
      window._docEmailViewportCollapseBound = true;
      window.visualViewport.addEventListener('resize', _maybeAutoCollapseEmailHeader);
    }
document.getElementById('doc-email-send-caret')?.addEventListener('click', handleCaretIntent);
document.addEventListener('click', (e) => {
      const menu = document.getElementById('doc-email-more-menu');
      // Keep the menu open ONLY while interacting with the caret itself or the
      // menu. Any other click — including the Send button (which sits in the
      // same .email-send-split) — closes it, so the popup is tied to the arrow.
      if (menu && !e.target.closest('#doc-email-send-caret, #doc-email-more-menu')) {
        menu.style.display = 'none';
        document.getElementById('doc-email-send-caret')?.setAttribute('aria-expanded', 'false');
      }
    }, { signal: paneLifetime.signal });
document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      const menu = document.getElementById('doc-email-more-menu');
      if (!menu || menu.style.display === 'none') return;
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation?.();
      menu.style.display = 'none';
      document.getElementById('doc-email-send-caret')?.setAttribute('aria-expanded', 'false');
    }, { capture: true, signal: paneLifetime.signal });
document.getElementById('doc-email-attach-btn')?.addEventListener('click', (e) => {
      _showComposeAttachMenu(e.currentTarget);
    });
document.getElementById('md-toolbar-attach-btn')?.addEventListener('click', (e) => {
      if (_activeDocLanguage() === 'email') {
        _showComposeAttachMenu(e.currentTarget);
      } else if (_isRichTextLang(_activeDocLanguage())) {
        _showRichImageSourceMenu(e.currentTarget);
      } else {
        document.getElementById('doc-md-image-input')?.click();
      }
    });
document.getElementById('md-toolbar-inline-image-btn')?.addEventListener('click', (e) => {
      if (_activeDocLanguage() === 'email' || _isRichTextLang(_activeDocLanguage())) {
        _showRichImageSourceMenu(e.currentTarget);
      }
    });
document.getElementById('doc-email-file-input')?.addEventListener('change', _handleAttachUpload);
document.getElementById('doc-md-image-input')?.addEventListener('change', _handleMarkdownImageUpload);
document.getElementById('doc-email-show-cc')?.addEventListener('click', () => {
      _setEmailHeaderCollapsed(false, { manual: false });
      const ccRow = document.getElementById('doc-email-cc-row');
      const bccRow = document.getElementById('doc-email-bcc-row');
      if (ccRow) ccRow.style.display = '';
      if (bccRow) bccRow.style.display = '';
      document.getElementById('doc-email-show-cc').style.display = 'none';
      _syncEmailHeaderSummary();
    });
document.querySelectorAll('[data-cc-close]').forEach(closeBtn => {
      closeBtn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const ccRow = document.getElementById('doc-email-cc-row');
        const bccRow = document.getElementById('doc-email-bcc-row');
        const ccInput = document.getElementById('doc-email-cc');
        const bccInput = document.getElementById('doc-email-bcc');
        if (ccRow) ccRow.style.display = 'none';
        if (bccRow) bccRow.style.display = 'none';
        if (ccInput) ccInput.value = '';
        if (bccInput) bccInput.value = '';
        const ccToggle = document.getElementById('doc-email-show-cc');
        if (ccToggle) ccToggle.style.display = '';
        _syncEmailHeaderSummary();
        saveCurrentToMap();
        _scheduleAutoSave(activeDocId, 800);
      });
    });
_wireRecipientAutocomplete('doc-email-to',  'doc-email-to-suggestions');
_wireRecipientAutocomplete('doc-email-cc',  'doc-email-cc-suggestions');
_wireRecipientAutocomplete('doc-email-bcc', 'doc-email-bcc-suggestions');
}
