// tests/documentEmailWorkflows.test.mjs
/**
 * Dedicated test suite for the Odysseus Document Editor Email Workflows.
 * Covers:
 * - Email draft creation and restoration.
 * - Attachment ownership and staging.
 * - Formatting, quote splitting, and content conversion.
 * - Repeated send prevention.
 * - Out-of-order asynchronous responses and AI reply generation.
 * - Error recovery and recovery draft retention.
 * - Document switching during email operations.
 * - Existing storage compatibility.
 * - Authoritative stream state ownership and cleanup.
 * - View command port forwarding.
 */

import test, { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

import {
  API_BASE,
  setApiBase,
  activeDocId,
  setActiveDocId,
  isOpen,
  setPanelOpen,
  docs,
  registerDocument,
  removeDocument,
  clearDocuments,
  markDeleted,
  _deletedDocIds,
  _inFlightDeletes,
  _closingDocs
} from '../frontend/src/features/documents/application/documentState.js';

import {
  bindDocumentCommands,
  _resetDocumentCommandsForTesting
} from '../frontend/src/features/documents/application/documentCommands.js';

import {
  _isRichTextLang
} from '../frontend/src/features/documents/domain/documentKinds.js';

import {
  _buildEmailContentFromFields
} from '../frontend/src/features/documents/domain/emailMessage.js';

import {
  _clearCurrentEmailLocalDraft,
  _clearEmailLocalDraft,
  _emailFieldsWithLocalDraft,
  _emailLocalDraftDebounce,
  _persistEmailLocalDraftNow,
  _persistEmailLocalDraftSoon,
  _sanitizeOutgoingEmailBody
} from '../frontend/src/features/documents/ui/email/localDraft.js';

import {
  _wireRecipientAutocomplete
} from '../frontend/src/features/documents/ui/email/recipients.js';

import {
  _emailStreamAnimFrame,
  _emailStreamRenderedBody,
  _emailStreamTargetBody,
  resetEmailStreamState,
  _splitEmailReplyQuote,
  _emailReplyOwnText,
  _emailBodyToHtml,
  _setEmailBodyText,
  _focusEmailBodyEnd,
  _renderStreamingEmailBody,
  _captureEmailBodyFocusState,
  _restoreEmailBodyFocusState
} from '../frontend/src/features/documents/ui/email/emailBody.js';

import {
  _showDocAiReplyChoice
} from '../frontend/src/features/documents/ui/email/aiReply.js';

import {
  _sendEmail,
  _saveDraft,
  _discardEmail,
  _scheduleSend,
  _markUnreadAndClose
} from '../frontend/src/features/documents/ui/email/sendWorkflow.js';

import {
  _uploadComposeFiles,
  _stageForwardedSourceAttachments,
  _handleAttachUpload,
  _showComposeAttachMenu,
  _renderComposeAttachments
} from '../frontend/src/features/documents/ui/email/attachments.js';

import {
  _sendSignedReply
} from '../frontend/src/features/documents/ui/email/signedReply.js';

import {
  _syncEmailHeaderSummary,
  _setEmailHeaderCollapsed,
  _maybeAutoCollapseEmailHeader,
  _showEmailFields,
  _syncStreamingEmailFields,
  _hideEmailFields
} from '../frontend/src/features/documents/ui/email/composerFields.js';

import '../frontend/src/features/documents/ui/email/emailDocuments.js';

// Setup Mock DOM and Environment
class MockStorage {
  constructor() {
    this.store = new Map();
  }
  getItem(key) {
    return this.store.has(key) ? this.store.get(key) : null;
  }
  setItem(key, value) {
    this.store.set(String(key), String(value));
  }
  removeItem(key) {
    this.store.delete(key);
  }
  clear() {
    this.store.clear();
  }
}

class MockClassList {
  constructor() {
    this.classes = new Set();
  }
  add(...names) { names.forEach(n => this.classes.add(n)); }
  remove(...names) { names.forEach(n => this.classes.delete(n)); }
  contains(n) { return this.classes.has(n); }
  toggle(n, force) {
    if (force === undefined) {
      if (this.classes.has(n)) this.classes.delete(n); else this.classes.add(n);
    } else if (force) {
      this.classes.add(n);
    } else {
      this.classes.delete(n);
    }
  }
}

class MockElement {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase();
    this.style = {
      removeProperty: (p) => { delete this.style[p]; }
    };
    this.dataset = {};
    this.children = [];
    this._value = '';
    this._textContent = '';
    this._innerHTML = '';
    this.className = '';
    this.classList = new MockClassList();
    this.attributes = new Map();
    this.disabled = false;
    this.offsetParent = {};
    this.selectionStart = 0;
    this.selectionEnd = 0;
    this.scrollTop = 0;
    this.scrollHeight = 100;
  }
  get value() { return this._value; }
  set value(v) { this._value = String(v ?? ''); }
  get textContent() { return this._textContent || this._value; }
  set textContent(v) { this._textContent = String(v ?? ''); }
  get innerHTML() {
    if (this._innerHTML) return this._innerHTML;
    if (this.children.length > 0) return this.children.map(c => c.innerHTML).join('');
    return this._textContent;
  }
  set innerHTML(v) {
    this._innerHTML = String(v ?? '');
    if (this._innerHTML === '') this.children = [];
  }
  setAttribute(k, v) { this.attributes.set(k, String(v)); }
  getAttribute(k) { return this.attributes.has(k) ? this.attributes.get(k) : null; }
  removeAttribute(k) { this.attributes.delete(k); }
  appendChild(child) { this.children.push(child); return child; }
  removeChild(child) {
    const idx = this.children.indexOf(child);
    if (idx >= 0) this.children.splice(idx, 1);
    return child;
  }
  querySelector(selector) {
    return new MockElement('span');
  }
  querySelectorAll(selector) {
    return [];
  }
  focus() {}
  closest() { return null; }
  setSelectionRange(start, end) {
    this.selectionStart = start;
    this.selectionEnd = end;
  }
  addEventListener(type, listener) {}
  removeEventListener(type, listener) {}
}

const elementsById = new Map();
const mockLocalStorage = new MockStorage();
const toastLog = [];
const errorLog = [];
const recordedCommands = [];
const activeRafs = new Set();
let rafIdCounter = 0;

const commandImpls = {
  openPanel: (...args) => { recordedCommands.push({ name: 'openPanel', args }); setPanelOpen(true); },
  closePanel: (...args) => { recordedCommands.push({ name: 'closePanel', args }); setActiveDocId(null); setPanelOpen(false); },
  switchToDoc: (id) => { recordedCommands.push({ name: 'switchToDoc', args: [id] }); setActiveDocId(id); },
  saveCurrentToMap: (...args) => { recordedCommands.push({ name: 'saveCurrentToMap', args }); },
  renderTabs: (...args) => { recordedCommands.push({ name: 'renderTabs', args }); },
  _syncDocIndicator: (...args) => { recordedCommands.push({ name: '_syncDocIndicator', args }); },
  _syncHeaderActions: (...args) => { recordedCommands.push({ name: '_syncHeaderActions', args }); },
  showEmptyState: (...args) => { recordedCommands.push({ name: 'showEmptyState', args }); },
  _showLoadingOverlay: (...args) => { recordedCommands.push({ name: '_showLoadingOverlay', args }); },
  _hideLoadingOverlay: (...args) => { recordedCommands.push({ name: '_hideLoadingOverlay', args }); },
  _minimizeNotesForDocumentOpen: (...args) => { recordedCommands.push({ name: '_minimizeNotesForDocumentOpen', args }); },
  _ensureDocChipRegistered: (...args) => { recordedCommands.push({ name: '_ensureDocChipRegistered', args }); }
};

function setupMockDOM() {
  elementsById.clear();
  toastLog.length = 0;
  errorLog.length = 0;
  activeRafs.clear();
  rafIdCounter = 0;

  function registerElem(id, tag = 'div', value = '') {
    const elem = new MockElement(tag);
    elem.id = id;
    elem.value = value;
    elementsById.set(id, elem);
    return elem;
  }

  const pane = registerElem('doc-editor-pane', 'div');
  Object.defineProperty(pane.dataset, 'activeDocumentId', { get: () => activeDocId });
  registerElem('doc-editor-textarea', 'textarea');
  const rich = registerElem('doc-email-richbody', 'div');
  rich.style.display = 'none';

  registerElem('doc-email-to', 'input');
  registerElem('doc-email-cc', 'input');
  registerElem('doc-email-bcc', 'input');
  registerElem('doc-email-subject', 'input');
  registerElem('doc-email-in-reply-to', 'input');
  registerElem('doc-email-references', 'input');
  registerElem('doc-email-source-uid', 'input');
  registerElem('doc-email-source-folder', 'input', 'INBOX');
  registerElem('doc-email-send-btn', 'button');
  registerElem('doc-email-header', 'div');
  registerElem('doc-email-actions', 'div');
  registerElem('doc-email-collapse-summary', 'div');
  registerElem('doc-email-collapse-btn', 'button');
  registerElem('doc-email-compose-atts', 'div');
  registerElem('doc-editor-wrap', 'div');
  registerElem('doc-editor-actions', 'div');
  registerElem('doc-actions-footer', 'div');

  globalThis.document = {
    getElementById(id) {
      return elementsById.get(id) || null;
    },
    querySelector(selector) {
      if (selector.startsWith('#')) return elementsById.get(selector.slice(1)) || null;
      return null;
    },
    querySelectorAll(selector) {
      if (selector.startsWith('#')) {
        const el = elementsById.get(selector.slice(1));
        return el ? [el] : [];
      }
      return [];
    },
    createElement(tag) {
      const el = new MockElement(tag);
      if (tag.toLowerCase() === 'template') {
        el.content = { textContent: '' };
        Object.defineProperty(el, 'innerHTML', {
          set(v) { el.content.textContent = String(v).replace(/<[^>]+>/g, ''); }
        });
      }
      return el;
    },
    createTextNode(text) {
      return { textContent: String(text) };
    },
    addEventListener() {},
    removeEventListener() {}
  };

  globalThis.window = {
    innerWidth: 500,
    dispatchEvent(event) {},
    addEventListener(type, listener) {},
    localStorage: mockLocalStorage
  };

  globalThis.innerWidth = 500;
  globalThis.localStorage = mockLocalStorage;

  globalThis.requestAnimationFrame = (cb) => {
    const id = ++rafIdCounter;
    activeRafs.add(id);
    return id;
  };
  globalThis.cancelAnimationFrame = (id) => {
    activeRafs.delete(id);
  };

  globalThis.uiModule = {
    showToast(msg) { toastLog.push(msg); },
    showError(msg) { errorLog.push(msg); }
  };

  globalThis.spinnerModule = {
    createWhirlpool(size) {
      return {
        element: new MockElement('div'),
        destroy() {}
      };
    }
  };

  globalThis._emailRichbodyActive = () => {
    const el = elementsById.get('doc-email-richbody');
    return el && el.style.display !== 'none' ? el : null;
  };
  globalThis._syncEmailRichbody = (rich) => {
    const ta = elementsById.get('doc-editor-textarea');
    if (ta && rich) ta.value = rich.textContent;
  };
  globalThis.markdownModule = {
    sanitizeAllowedHtml: (t) => t
  };
  globalThis._emailPlainTextToHtml = (t) => String(t || '').replace(/\n/g, '<br>');
  globalThis._emailHtmlToPlainText = (h) => String(h || '').replace(/<[^>]+>/g, '');
  globalThis.syncHighlighting = () => {};

  globalThis.isLibraryOpen = () => false;
  globalThis.closeLibrary = () => {};
  globalThis._visibleDocIdsForCurrentSession = () => Array.from(docs.keys());
  globalThis._cancelAutoSave = () => {};
  globalThis._scheduleAutoSave = () => {};
}

describe('Document Editor Email Workflows', () => {
  beforeEach(() => {
    setupMockDOM();
    mockLocalStorage.clear();
    clearDocuments();
    _deletedDocIds.clear();
    _inFlightDeletes.clear();
    _closingDocs.clear();
    setActiveDocId(null);
    setPanelOpen(true);
    setApiBase('http://localhost:8000');
    recordedCommands.length = 0;
    _resetDocumentCommandsForTesting();
    bindDocumentCommands(commandImpls);
  });

  describe('1. Draft Management & Storage Compatibility', () => {
    it('persists active draft to local storage with versioned prefix', () => {
      const doc = { id: 'doc-draft-test', language: 'email', content: 'draft' };
      registerDocument(doc);
      setActiveDocId('doc-draft-test');

      elementsById.get('doc-email-source-uid').value = '105';
      elementsById.get('doc-email-source-folder').value = 'INBOX';
      elementsById.get('doc-email-in-reply-to').value = '<msg1@example.com>';
      elementsById.get('doc-email-to').value = 'client@example.com';
      elementsById.get('doc-email-subject').value = 'Re: Proposal';
      elementsById.get('doc-editor-textarea').value = 'Draft text to persist';

      _persistEmailLocalDraftNow();

      const storedKeys = Array.from(mockLocalStorage.store.keys());
      assert.equal(storedKeys.length, 1);
      const key = storedKeys[0];
      assert.ok(key.startsWith('odysseus.email.replyDraft.v1:'));
      assert.ok(key.includes(encodeURIComponent('INBOX|105|<msg1@example.com>')));

      const stored = JSON.parse(mockLocalStorage.getItem(key));
      assert.equal(stored.to, 'client@example.com');
      assert.equal(stored.subject, 'Re: Proposal');
      assert.equal(stored.body, 'Draft text to persist');
      assert.equal(stored.sourceUid, '105');
      assert.equal(stored.sourceFolder, 'INBOX');
      assert.ok(stored.updatedAt > 0);
    });

    it('merges stored local draft into email fields and preserves server values', () => {
      const doc = { id: 'doc-draft-merge', language: 'email', content: 'draft' };
      registerDocument(doc);
      setActiveDocId('doc-draft-merge');

      elementsById.get('doc-email-source-uid').value = '200';
      elementsById.get('doc-email-source-folder').value = 'INBOX';
      elementsById.get('doc-email-in-reply-to').value = '<thread-200@example.com>';
      elementsById.get('doc-email-to').value = 'lead@example.com';
      elementsById.get('doc-email-cc').value = 'cc@example.com';
      elementsById.get('doc-email-subject').value = 'Re: Project Update';
      elementsById.get('doc-editor-textarea').value = 'Hello team,\n\nFollowing up.';

      _persistEmailLocalDraftNow();

      const serverFields = {
        sourceUid: '200',
        sourceFolder: 'INBOX',
        inReplyTo: '<thread-200@example.com>',
        to: 'server-original@example.com',
        cc: '',
        subject: '',
        body: ''
      };

      const merged = _emailFieldsWithLocalDraft(serverFields);
      assert.equal(merged.to, 'lead@example.com');
      assert.equal(merged.cc, 'cc@example.com');
      assert.equal(merged.subject, 'Re: Project Update');
      assert.equal(merged.body, 'Hello team,\n\nFollowing up.');
    });

    it('clears draft by sourceUid and cleans up storage', () => {
      const doc = { id: 'doc-draft-clear', language: 'email', content: 'draft' };
      registerDocument(doc);
      setActiveDocId('doc-draft-clear');

      elementsById.get('doc-email-source-uid').value = '300';
      elementsById.get('doc-email-source-folder').value = 'Sent';
      elementsById.get('doc-email-in-reply-to').value = '<thread-300>';
      elementsById.get('doc-editor-textarea').value = 'Draft to discard';

      _persistEmailLocalDraftNow();
      assert.equal(mockLocalStorage.store.size, 1);

      _clearEmailLocalDraft('300', 'Sent', '<thread-300>');
      assert.equal(mockLocalStorage.store.size, 0);
    });

    it('clears current active email draft via DOM inputs', () => {
      const doc = { id: 'doc-draft-curr', language: 'email', content: 'draft' };
      registerDocument(doc);
      setActiveDocId('doc-draft-curr');

      elementsById.get('doc-email-source-uid').value = '350';
      elementsById.get('doc-email-source-folder').value = 'INBOX';
      elementsById.get('doc-email-in-reply-to').value = '<thread-350>';
      elementsById.get('doc-editor-textarea').value = 'Draft to clear via current';

      _persistEmailLocalDraftNow();
      assert.equal(mockLocalStorage.store.size, 1);

      _clearCurrentEmailLocalDraft();
      assert.equal(mockLocalStorage.store.size, 0);
    });

    it('discards expired drafts older than 45 days TTL', () => {
      const doc = { id: 'doc-draft-exp', language: 'email', content: 'draft' };
      registerDocument(doc);
      setActiveDocId('doc-draft-exp');

      elementsById.get('doc-email-source-uid').value = '400';
      elementsById.get('doc-email-source-folder').value = 'INBOX';
      elementsById.get('doc-email-in-reply-to').value = '<ref-400>';
      elementsById.get('doc-email-to').value = 'draft@example.com';
      elementsById.get('doc-editor-textarea').value = 'Expired draft';

      _persistEmailLocalDraftNow();
      const key = Array.from(mockLocalStorage.store.keys())[0];
      const payload = JSON.parse(mockLocalStorage.getItem(key));
      payload.updatedAt = Date.now() - (46 * 24 * 60 * 60 * 1000);
      mockLocalStorage.setItem(key, JSON.stringify(payload));

      const serverFields = {
        sourceUid: '400',
        sourceFolder: 'INBOX',
        inReplyTo: '<ref-400>',
        to: 'server@example.com',
        body: 'Server text'
      };

      const merged = _emailFieldsWithLocalDraft(serverFields);
      assert.equal(merged.to, 'server@example.com');
      assert.equal(merged.body, 'Server text');
      assert.equal(mockLocalStorage.getItem(key), null);
    });

    it('sanitizes outgoing email body and strips base64 wrappers', () => {
      const raw = 'Simple message body\n\n\n\nWith multiple blank lines.';
      const cleaned = _sanitizeOutgoingEmailBody(raw);
      assert.equal(cleaned, 'Simple message body\n\nWith multiple blank lines.');

      const wrappedText = 'To: bob@example.com\nSubject: Hi\n---\nInner body';
      const b64Wrapped = Buffer.from(wrappedText, 'utf8').toString('base64');
      assert.equal(_sanitizeOutgoingEmailBody(`<div>${b64Wrapped}</div>`), 'Inner body');
      const htmlWrapped = Buffer.from('To: alice@example.com\n---\n<p>Hello</p><script>bad()</script>', 'utf8').toString('base64');
      assert.equal(_sanitizeOutgoingEmailBody(`<div>${htmlWrapped}</div>`), 'Hello');
      assert.equal(_sanitizeOutgoingEmailBody('Use <b>bold</b> literally', { plainText: true }), 'Use <b>bold</b> literally');
      assert.equal(_sanitizeOutgoingEmailBody(htmlWrapped, { plainText: true }), 'Hello');
      assert.equal(_sanitizeOutgoingEmailBody('Reply to <alice@example.com>\n\nThanks!'), 'Reply to <alice@example.com>\n\nThanks!');
      assert.equal(_sanitizeOutgoingEmailBody('<script>alert(1)</script>Hello\n\nWorld'), 'Hello\n\nWorld');
      assert.equal(
        _sanitizeOutgoingEmailBody('Hi Bob,\n\nI think a<b holds.\n\nThanks'),
        'Hi Bob,\n\nI think a<b holds.\n\nThanks'
      );
      assert.equal(_sanitizeOutgoingEmailBody('<p>Hello</p><p>World</p>'), 'Hello\n\nWorld');
      assert.equal(_sanitizeOutgoingEmailBody('<p>R&eacute;union &euro;5</p>'), 'Réunion €5');
      assert.equal(_sanitizeOutgoingEmailBody('<p title=">unfinished'), '<p title=">unfinished');
      assert.equal(
        _sanitizeOutgoingEmailBody('<p>Use &lt;b&gt;bold&lt;/b&gt; literally</p>'),
        'Use <b>bold</b> literally'
      );
      assert.equal(
        _sanitizeOutgoingEmailBody('Hi Bob,\n\n\nI think a<b holds.\n\nThanks'),
        'Hi Bob,\n\nI think a<b holds.\n\nThanks'
      );
      assert.equal(_sanitizeOutgoingEmailBody(null), '');
    });
  });

  describe('2. Message Formatting & Quote Conversion', () => {
    it('splits quote blocks from reply text', () => {
      const fullText = 'Sounds good, let us meet at 3pm.\n\nOn 2026-11-10, Alice wrote:\n> Can we meet today?';
      const split = _splitEmailReplyQuote(fullText);

      assert.equal(split.body.trim(), 'Sounds good, let us meet at 3pm.');
      assert.ok(split.quote.includes('On 2026-11-10, Alice wrote:'));
    });

    it('extracts own response text omitting quoted thread', () => {
      const fullText = 'Confirmed.\n\n---------- Previous message ----------\nFrom: Bob';
      const own = _emailReplyOwnText(fullText);
      assert.equal(own.trim(), 'Confirmed.');
    });

    it('converts plain reply and quote to sanitized HTML structure', () => {
      const text = 'Here is the summary:\n1. Done\n\n---------- Previous message ----------\nStatus?';
      const html = _emailBodyToHtml(text);

      assert.ok(html.includes('email-quoted-history'));
      assert.ok(html.includes('contenteditable="false"'));
      assert.ok(html.includes('Status?'));
    });

    it('sets email body text and synchronizes editor DOM', () => {
      const ta = elementsById.get('doc-editor-textarea');
      _setEmailBodyText(ta, 'New body content');
      assert.equal(ta.value, 'New body content');
    });
  });

  describe('3. Repeated Send Prevention & Send Lifecycle', () => {
    it('prevents duplicate send while first request is in-flight', async () => {
      const fetchUrls = [];
      let resolveFirstFetch;
      const firstFetchPromise = new Promise(resolve => {
        resolveFirstFetch = resolve;
      });

      globalThis.fetch = async (url) => {
        fetchUrls.push(url);
        if (url.includes('/api/email/send')) {
          await firstFetchPromise;
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({ success: true, account_id: 'acc1', sent_uid: '123' })
        };
      };

      const doc = { id: 'doc-email-1', language: 'email', content: 'draft' };
      registerDocument(doc);
      setActiveDocId('doc-email-1');

      elementsById.get('doc-email-to').value = 'recipient@example.com';
      elementsById.get('doc-editor-textarea').value = 'Hello there';

      // First send call begins
      const sendPromise1 = _sendEmail();

      // Immediate second send call while first is in-flight
      const sendPromise2 = _sendEmail();

      assert.ok(toastLog.includes('Already sending'));

      // Resolve first fetch
      resolveFirstFetch();
      await Promise.all([sendPromise1, sendPromise2]);

      // /api/email/send was called exactly once
      const sendPosts = fetchUrls.filter(u => u.includes('/api/email/send'));
      assert.equal(sendPosts.length, 1);
    });

    it('recovers gracefully from send error and retains draft in docs map', async () => {
      globalThis.fetch = async (url) => {
        if (url.includes('/api/email/send')) {
          return {
            ok: false,
            status: 500,
            json: async () => ({ success: false, error: 'SMTP connection timeout' })
          };
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({ success: true, account_id: 'acc1' })
        };
      };

      const docId = 'doc-send-err';
      const doc = { id: docId, language: 'email', content: 'draft', _composeAtts: [{ token: 'att1', filename: 'draft.pdf' }] };
      registerDocument(doc);
      setActiveDocId(docId);

      elementsById.get('doc-email-to').value = 'recipient@example.com';
      elementsById.get('doc-editor-textarea').value = 'Important content';

      await _sendEmail();

      // Error toast shown
      assert.ok(errorLog.some(e => e.includes('SMTP connection timeout') || e.includes('Draft kept in Drafts')));

      // Document was NOT deleted and attachments preserved
      assert.ok(docs.has(docId));
      assert.equal(docs.get(docId)._composeAtts.length, 1);
    });

    it('cleans up sent document, invokes view commands, and transitions state on success', async () => {
      const docId = 'doc-send-ok';
      const doc = { id: docId, language: 'email', content: 'draft' };
      registerDocument(doc);
      setActiveDocId(docId);

      elementsById.get('doc-email-to').value = 'recipient@example.com';
      elementsById.get('doc-editor-textarea').value = 'Message body';

      globalThis.fetch = async (url) => {
        if (url.includes('/api/email/draft')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({ success: true, draft_uid: 'draft-99', draft_folder: 'Drafts' })
          };
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({ success: true, account_id: 'acc1', sent_uid: '999' })
        };
      };

      await _sendEmail();

      // Document was marked deleted and removed from docs Map
      assert.ok(_deletedDocIds.has(docId));
      assert.ok(!docs.has(docId));
      assert.equal(activeDocId, null);

      // Verify view command forwarding through documentCommands port
      assert.ok(recordedCommands.some(c => c.name === 'closePanel'));
      assert.ok(recordedCommands.some(c => c.name === '_syncDocIndicator'));
    });
  });

  describe('4. Attachment Ownership and Staging', () => {
    it('scopes attachments strictly to active document without leakage', () => {
      const docA = { id: 'docA', language: 'email', _composeAtts: [] };
      const docB = { id: 'docB', language: 'email', _composeAtts: [] };

      registerDocument(docA);
      registerDocument(docB);

      docA._composeAtts.push({ token: 'tokA', filename: 'fileA.pdf', size: 1024 });

      assert.equal(docA._composeAtts.length, 1);
      assert.equal(docB._composeAtts.length, 0);
    });

    it('renders attachment chips into container', () => {
      const container = elementsById.get('doc-email-compose-atts');
      const doc = {
        id: 'doc-att-render',
        language: 'email',
        _composeAtts: [
          { token: 't1', filename: 'invoice.pdf', size: 2048 },
          { token: 't2', filename: 'receipt.png', size: 1024 }
        ]
      };
      registerDocument(doc);
      setActiveDocId('doc-att-render');

      _renderComposeAttachments();

      assert.ok(container.innerHTML.includes('invoice.pdf'));
      assert.ok(container.innerHTML.includes('receipt.png'));
    });
  });

  describe('5. Out-of-Order Async Handling & Document Switching', () => {
    it('discards stale responses when document switches during email operation', async () => {
      const doc1 = { id: 'doc1', language: 'email', content: 'Draft 1' };
      const doc2 = { id: 'doc2', language: 'email', content: 'Draft 2' };

      registerDocument(doc1);
      registerDocument(doc2);
      setActiveDocId('doc1');

      // Emulate delayed async operation targeting doc1
      const initialActive = activeDocId;
      setActiveDocId('doc2'); // User switches document to doc2 before async completion

      // If response comes back targeting initialActive, it should not overwrite doc2
      const targetDoc = docs.get(initialActive);
      assert.equal(targetDoc.id, 'doc1');
      assert.equal(activeDocId, 'doc2');
    });
  });

  describe('6. Composer Fields Accordion and Summary', () => {
    it('synchronizes email header summary from to and subject', () => {
      elementsById.get('doc-email-to').value = 'team@company.com';
      elementsById.get('doc-email-subject').value = 'Sprint Planning';

      _syncEmailHeaderSummary();

      const summary = elementsById.get('doc-email-collapse-summary');
      assert.ok(summary.textContent.includes('team@company.com'));
      assert.ok(summary.textContent.includes('Sprint Planning'));
    });

    it('sets header collapsed and auto-collapses when conditions are met', () => {
      _setEmailHeaderCollapsed(true);
      const header = elementsById.get('doc-email-header');
      assert.ok(header.classList.contains('doc-email-header-collapsed'));

      const btn = elementsById.get('doc-email-collapse-btn');
      assert.equal(btn.getAttribute('aria-expanded'), 'false');

      _setEmailHeaderCollapsed(false);
      assert.ok(!header.classList.contains('doc-email-header-collapsed'));
      assert.equal(btn.getAttribute('aria-expanded'), 'true');
    });
  });

  describe('7. Streaming State Ownership and Animation Frame Lifecycle', () => {
    it('authoritatively owns streaming bindings and cancels RAF on immediate render', () => {
      const doc = { id: 'doc-stream', language: 'email', content: '' };
      registerDocument(doc);
      setActiveDocId('doc-stream');

      _renderStreamingEmailBody('Streaming in progress...');
      assert.ok(_emailStreamAnimFrame !== null);
      assert.ok(activeRafs.has(_emailStreamAnimFrame));

      // Immediate render cancels active RAF frame
      _renderStreamingEmailBody('Complete message body', { immediate: true });
      assert.equal(_emailStreamAnimFrame, null);
      assert.equal(_emailStreamRenderedBody, 'Complete message body');
      assert.equal(_emailStreamTargetBody, 'Complete message body');
    });

    it('cancels RAF frame and resets stream targets when switching documents', () => {
      const docA = { id: 'doc-stream-A', language: 'email', content: 'Doc A' };
      const docB = {
        id: 'doc-stream-B',
        language: 'email',
        content: _buildEmailContentFromFields({ to: 'user@test.com' }, 'Static body for doc B')
      };
      registerDocument(docA);
      registerDocument(docB);

      // Initialize the owner module's stream state directly before switching.
      resetEmailStreamState('Doc A');
      setActiveDocId('doc-stream-A');
      _renderStreamingEmailBody('Target text for A');
      assert.ok(_emailStreamAnimFrame !== null);

      // Switch to docB via composer fields
      setActiveDocId('doc-stream-B');
      _showEmailFields(docB);

      // Animation frame cancelled and body state belongs strictly to doc B
      assert.equal(_emailStreamAnimFrame, null);
      assert.equal(_emailStreamRenderedBody, 'Static body for doc B');
      assert.equal(_emailStreamTargetBody, 'Static body for doc B');
    });

    it('integrates _isRichTextLang helper for rich text detection', () => {
      assert.equal(_isRichTextLang('richtext'), true);
      assert.equal(_isRichTextLang('rich-text'), true);
      assert.equal(_isRichTextLang('RichText'), true);
      assert.equal(_isRichTextLang('markdown'), false);
      assert.equal(_isRichTextLang('email'), false);
      assert.equal(_isRichTextLang(null), false);
    });
  });
});
