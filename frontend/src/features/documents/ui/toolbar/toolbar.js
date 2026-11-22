// frontend/src/features/documents/ui/toolbar/toolbar.js
/**
 * Document formatting toolbar and AI writing menu.
 */

import { activeDocId, docs } from '../../application/documentState.js';
import { saveDocument } from '../../application/persistence.js';
import { sessionModule, spinnerModule, uiModule } from '../host.js';
import { toggleFullscreen } from '../panel/paneLayout.js';
import { _showMdDropdown, nextMdDropdownActivationToken } from '../richEditor/formatMenus.js';
import { _saveFormatSelection, applyMdFormat } from '../richEditor/formatting.js';
import { _emailRichbodyActive } from '../richEditor/richBodySync.js';
import { _hideRichSelectionToolbar } from '../richEditor/selectionToolbar.js';

function ownerDropdownActivationToken() {
  return nextMdDropdownActivationToken();
}

const _DOCUMENT_TOOLBAR_GROUPS = [
    {
      name: 'display-size',
      selectors: ['#doc-fontsize-btn'],
    },
    {
      name: 'type',
      selectors: [
        '#doc-email-ai-reply-btn',
        '#md-toolbar-sep-after-ai-reply',
        '#doc-ai-writing-btn',
        '[data-dd="heading"]',
        '[data-dd="alert"]',
        '[data-dd="font"]',
        '#md-toolbar-sep-after-heading',
        '[data-dd="textsize"]',
      ],
    },
    {
      name: 'inline-basic',
      selectors: [
        '[data-md="bold"]',
        '[data-md="italic"]',
        '[data-md="underline"]',
        '[data-md="strike"]',
      ],
    },
    {
      name: 'inline-color',
      selectors: [
        '[data-dd="color"]',
        '[data-dd="highlight"]',
        '#md-toolbar-sep-after-highlight',
        '[data-md="removeformat"]',
        '#md-toolbar-attach-btn',
        '[data-md="link"]',
        '#md-toolbar-inline-image-btn',
      ],
    },
    {
      name: 'alignment',
      selectors: ['[data-dd="align"]'],
    },
    {
      name: 'spacing',
      selectors: ['[data-dd="spacing"]'],
    },
    {
      name: 'paragraph',
      selectors: [
        '[data-dd="list"]',
        '[data-dd="separator"]',
        '[data-md="outdent"]',
        '[data-md="indent"]',
        '[data-md="quote"]',
        '[data-dd="code"]',
      ],
    },
    {
      name: 'insert',
      selectors: [
        '[data-dd="image"]',
        '[data-dd="table"]',
        '[data-md="pagebreak"]',
      ],
    },
    {
      name: 'inline-rich',
      selectors: ['[data-md="superscript"]', '[data-md="subscript"]'],
    },
    {
      name: 'document',
      selectors: [
        '#doc-find-toolbar-btn',
        '#doc-outline-toolbar-btn',
        '#md-toolbar-emoji-slot',
      ],
    },
    {
      name: 'view',
      selectors: ['#doc-diff-toggle-btn'],
    },
  ];

const _AI_WRITING_ACTIONS = Object.freeze({
    proofread: 'Fix spelling and grammar in the open document. Apply corrections directly using targeted edits. Preserve the meaning, voice, and formatting; do not rewrite for style. Keep already-correct sentences unchanged. Use each affected paragraph as an exact unique FIND anchor and change only the spelling or grammar errors in its replacement. Check every paragraph, including repeated errors. Do not just list corrections in chat.',
    improve: 'Review the open document and create inline suggestions for clarity, wording, structure, and readability. Do not apply changes.',
    concise: 'Make the open document more concise by proposing one concrete, shorter replacement per affected paragraph. Each REPLACE must actually shorten the prose, not merely correct spelling or grammar. Each FIND must quote the entire original paragraph exactly, including any HTML tags. Keep distinct paragraphs separate and preserve their facts and meaning. Create inline suggestions only; do not apply changes.',
    style: 'Rewrite the open document to match my configured Writing Style setting. Preserve the meaning and create inline suggestions only; do not apply changes.',
    sources: 'Check factual claims in the open document using web research. For each claim, verify it, suggest a source link/citation when valid, or clearly mark it as unverified when you cannot find reliable evidence. Create inline suggestions only; do not apply changes.',
  });

const _aiWritingButtonMarkup = '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 1.8 14.45 9.55 22.2 12l-7.75 2.45L12 22.2l-2.45-7.75L1.8 12l7.75-2.45L12 1.8Z"/></svg><svg width="7" height="7" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>';

function _aiWritingSelectionText() {
    const rich = _emailRichbodyActive();
    const browserSelection = window.getSelection?.();
    if (rich && browserSelection?.rangeCount && rich.contains(browserSelection.anchorNode)) {
      const text = browserSelection.toString().trim();
      if (text) return text;
    }
    const textarea = document.getElementById('doc-editor-textarea');
    if (textarea && textarea.selectionStart !== textarea.selectionEnd) {
      const text = textarea.value.slice(textarea.selectionStart, textarea.selectionEnd).trim();
      if (text) return text;
    }
    return '';
  }

let _aiWritingStreamTimer = null;
let _aiWritingStreamAbortCleanup = null;

function _clearAiWritingStreamWait() {
  if (_aiWritingStreamTimer !== null) {
    clearTimeout(_aiWritingStreamTimer);
    _aiWritingStreamTimer = null;
  }
  if (_aiWritingStreamAbortCleanup) {
    _aiWritingStreamAbortCleanup();
    _aiWritingStreamAbortCleanup = null;
  }
}

function _setAiWritingLoading(loading) {
    const button = document.getElementById('doc-ai-writing-btn');
    if (!button) return;
    if (button._writingSpinner) {
      button._writingSpinner.destroy?.();
      button._writingSpinner = null;
    }
    button.disabled = !!loading;
    button.classList.toggle('is-loading', !!loading);
    button.innerHTML = _aiWritingButtonMarkup;
    if (!loading) return;
    const whirlpool = spinnerModule.createWhirlpool(13);
    whirlpool.element.classList.add('doc-ai-writing-whirlpool');
    whirlpool.element.style.cssText = 'width:13px;height:13px;margin:0;display:inline-flex;align-items:center;justify-content:center;';
    button.replaceChildren(whirlpool.element, button.lastElementChild);
    button._writingSpinner = whirlpool;
  }

function _closeAiWritingMenu() {
    const menu = document.getElementById('doc-ai-writing-menu');
    if (menu) {
      menu._cleanup?.();
      menu.remove();
    }
    document.getElementById('doc-ai-writing-btn')?.setAttribute('aria-expanded', 'false');
  }

function _showAiWritingMenu(button) {
    _closeAiWritingMenu();
    // Capture before the menu can take focus or alter the browser selection.
    // This matters for both textarea ranges and contenteditable selections.
    const selectedTextAtOpen = _aiWritingSelectionText();
    const menu = document.createElement('div');
    menu.id = 'doc-ai-writing-menu';
    menu.className = 'doc-overflow-menu doc-ai-writing-menu open';
    menu.setAttribute('role', 'menu');
    menu.innerHTML = `
      <div class="doc-ai-writing-menu-title"><svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 1.8 14.45 9.55 22.2 12l-7.75 2.45L12 22.2l-2.45-7.75L1.8 12l7.75-2.45L12 1.8Z"/></svg><span>Writing tools</span></div>
      <button type="button" class="doc-overflow-item" data-ai-writing-action="proofread" role="menuitem"><span class="doc-ai-writing-item-icon">Aa</span><span>Spelling &amp; grammar</span></button>
      <button type="button" class="doc-overflow-item" data-ai-writing-action="improve" role="menuitem"><span class="doc-ai-writing-item-icon">✦</span><span>Suggest improvements</span></button>
      <button type="button" class="doc-overflow-item" data-ai-writing-action="concise" role="menuitem"><span class="doc-ai-writing-item-icon">≡</span><span>Make more concise</span></button>
      <button type="button" class="doc-overflow-item" data-ai-writing-action="style" role="menuitem"><span class="doc-ai-writing-item-icon">✎</span><span>Match my writing style</span></button>
      <button type="button" class="doc-overflow-item" data-ai-writing-action="sources" role="menuitem"><span class="doc-ai-writing-item-icon">↗</span><span>Check sources</span></button>`;
    document.body.appendChild(menu);
    const rect = button.getBoundingClientRect();
    const menuWidth = menu.offsetWidth || 210;
    const menuHeight = menu.offsetHeight || 132;
    menu.style.left = `${Math.max(8, Math.min(window.innerWidth - menuWidth - 8, rect.left))}px`;
    menu.style.top = `${Math.min(window.innerHeight - menuHeight - 8, rect.bottom + 5)}px`;
    button.setAttribute('aria-expanded', 'true');

    const lifetimeSignal = button.closest('#doc-editor-pane')?._lifetime?.signal || null;
    let listenerTimerId = null;
    const onPaneAbort = () => _closeAiWritingMenu();
    const dismiss = (event) => {
      if (event && (menu.contains(event.target) || event.target === button)) return;
      _closeAiWritingMenu();
    };
    const onKeydown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        _closeAiWritingMenu();
        button.focus({ preventScroll: true });
      }
    };
    menu._cleanup = () => {
      if (listenerTimerId !== null) {
        clearTimeout(listenerTimerId);
        listenerTimerId = null;
      }
      lifetimeSignal?.removeEventListener('abort', onPaneAbort);
      document.removeEventListener('pointerdown', dismiss, true);
      document.removeEventListener('keydown', onKeydown, true);
    };
    lifetimeSignal?.addEventListener('abort', onPaneAbort, { once: true });
    menu.querySelectorAll('[data-ai-writing-action]').forEach(item => {
      item.addEventListener('mousedown', event => event.preventDefault());
      item.addEventListener('click', async () => {
        const action = item.dataset.aiWritingAction;
        _closeAiWritingMenu();
        const prompt = _AI_WRITING_ACTIONS[action];
        if (!prompt) return;
        let configuredStyle = '';
        if (action === 'style') {
          const activeDocument = activeDocId ? docs.get(activeDocId) : null;
          const isEmailDocument = activeDocument?.language === 'email';
          try {
            const accountId = String(window.__odysseusActiveEmailAccount || '').trim();
            const suffix = accountId ? `?account_id=${encodeURIComponent(accountId)}` : '';
            const generalResponse = await fetch('/api/auth/settings', { credentials: 'same-origin' });
            const generalData = await generalResponse.json().catch(() => ({}));
            const generalStyle = String(generalData.document_writing_style || '').trim();
            let emailStyle = '';
            if (isEmailDocument) {
              const emailResponse = await fetch(`/api/email/style${suffix}`, { credentials: 'same-origin' });
              const emailData = await emailResponse.json().catch(() => ({}));
              if (emailResponse.ok) emailStyle = String(emailData.style || '').trim();
            }
            configuredStyle = isEmailDocument
              ? [
                  generalStyle && `GENERAL WRITING STYLE:\n${generalStyle}`,
                  emailStyle && `EMAIL CONVENTIONS:\n${emailStyle}`,
                ].filter(Boolean).join('\n\n')
              : generalStyle;
            if (!generalResponse.ok || !configuredStyle) {
              uiModule?.showToast?.('You haven\'t set up a writing style yet.', {
                duration: 7000,
                action: 'Set up in Settings',
                actionHint: isEmailDocument ? 'Email → Writing Style' : 'AI Defaults → Writing Style',
                onAction: () => window.adminModule?.open?.(isEmailDocument ? 'email' : 'ai'),
              });
              return;
            }
          } catch (_) {
            uiModule?.showToast?.('You haven\'t set up a writing style yet.', {
              duration: 7000,
              action: 'Set up in Settings',
              actionHint: isEmailDocument ? 'Email → Writing Style' : 'AI Defaults → Writing Style',
              onAction: () => window.adminModule?.open?.(isEmailDocument ? 'email' : 'ai'),
            });
            return;
          }
        }
        const selectedText = selectedTextAtOpen || _aiWritingSelectionText();
        const styleContext = configuredStyle
          ? `\n\nUse this configured writing style as the source of truth:\n---\n${configuredStyle.slice(0, 8000)}\n---`
          : '';
        const scopedPrompt = selectedText
          ? `${prompt}${styleContext}\n\nImportant scope: work only on this selected passage and do not change or suggest changes elsewhere in the document. Use the exact matching text from the active document as the FIND target even if the editor stores formatting markup around it. Selected passage:\n---\n${selectedText.slice(0, 12000)}\n---`
          : `${prompt}${styleContext}\n\nThere is no text selection, so work on the whole open document.`;
        // Desktop reveals the chat beside the document before dispatching the
        // writing request. On mobile the document is already a fixed sheet;
        // toggling its fullscreen class here reflows the sheet as the prompt is
        // sent and makes the whole screen visibly bounce.
        if (window.innerWidth > 768 && document.querySelector('.doc-editor-pane.doc-fullscreen')) {
          toggleFullscreen();
        }
        try { await saveDocument({ silent: true }); } catch (_) {}
        const input = document.getElementById('message');
        const send = document.querySelector('.send-btn');
        if (!input || !send || !window.chatModule?.handleChatSubmit) {
          uiModule?.showError?.('Chat is not ready for writing tools.');
          return;
        }
        input.value = scopedPrompt;
        window.chatModule.setHideUserBubble?.();
        _clearAiWritingStreamWait();
        _setAiWritingLoading(true);
        send.click();
        const streamSessionId = sessionModule.getCurrentSessionId?.();
        const loadingStartedAt = Date.now();
        const streamSignal = button.closest('#doc-editor-pane')?._lifetime?.signal || lifetimeSignal;
        if (streamSignal && !streamSignal.aborted) {
          const onStreamAbort = () => {
            _clearAiWritingStreamWait();
            _setAiWritingLoading(false);
          };
          streamSignal.addEventListener('abort', onStreamAbort, { once: true });
          _aiWritingStreamAbortCleanup = () => streamSignal.removeEventListener('abort', onStreamAbort);
        }
        const waitForWritingStream = () => {
          _aiWritingStreamTimer = null;
          if (streamSignal?.aborted) {
            _clearAiWritingStreamWait();
            _setAiWritingLoading(false);
            return;
          }
          const active = !!window.chatModule?.hasActiveStream?.(streamSessionId);
          const tooLong = Date.now() - loadingStartedAt > 10 * 60 * 1000;
          if ((!active && Date.now() - loadingStartedAt > 500) || tooLong) {
            _clearAiWritingStreamWait();
            _setAiWritingLoading(false);
          } else {
            _aiWritingStreamTimer = setTimeout(waitForWritingStream, 250);
          }
        };
        _aiWritingStreamTimer = setTimeout(waitForWritingStream, 250);
      });
    });
    listenerTimerId = setTimeout(() => {
      listenerTimerId = null;
      if (!menu.isConnected || lifetimeSignal?.aborted) return;
      document.addEventListener('pointerdown', dismiss, true);
      document.addEventListener('keydown', onKeydown, true);
    }, 0);
  }

function _orderDocumentToolbar(itemsWrap) {
    if (!itemsWrap) return;
    const pdfItems = Array.from(itemsWrap.querySelectorAll(':scope > .md-toolbar-pdf-only'));
    itemsWrap.querySelectorAll(':scope > .md-toolbar-sep:not(.md-toolbar-pdf-only):not(.md-toolbar-manual-sep)').forEach(separator => separator.remove());

    _DOCUMENT_TOOLBAR_GROUPS.forEach((group, index) => {
      group.selectors.forEach(selector => {
        const item = itemsWrap.querySelector(`:scope > ${selector}`);
        if (!item) return;
        item.dataset.toolbarGroup = group.name;
        itemsWrap.appendChild(item);
      });
      if (index === _DOCUMENT_TOOLBAR_GROUPS.length - 1) return;
      const separator = document.createElement('span');
      separator.className = 'md-toolbar-sep md-toolbar-edit-only';
      separator.dataset.toolbarSeparator = `${group.name}-${_DOCUMENT_TOOLBAR_GROUPS[index + 1].name}`;
      separator.setAttribute('aria-hidden', 'true');
      itemsWrap.appendChild(separator);
    });

    pdfItems.forEach(item => itemsWrap.appendChild(item));
  }

export function _syncDocumentToolbarSeparators(itemsWrap) {
    if (!itemsWrap) return;
    const isVisible = (el) => el && !el.hidden && el.style.display !== 'none';
    itemsWrap.querySelectorAll(':scope > .md-toolbar-sep:not(.md-toolbar-pdf-only)').forEach((separator) => {
      if (separator.classList.contains('md-toolbar-manual-sep')) {
        let following = separator.nextElementSibling;
        while (following && !isVisible(following)) following = following.nextElementSibling;
        separator.style.display = isVisible(separator.previousElementSibling) && following ? '' : 'none';
        return;
      }
      separator.style.display = isVisible(separator.previousElementSibling) &&
        isVisible(separator.nextElementSibling) ? '' : 'none';
    });
  }

export function initMdToolbar() {
    const toolbar = document.getElementById('doc-md-toolbar');
    if (!toolbar) return;

    const itemsWrap = document.getElementById('md-toolbar-items');
    const overflowWrapper = document.getElementById('md-toolbar-overflow-wrapper');
    const overflowToggle = document.getElementById('md-toolbar-overflow-toggle');
    const overflowMenu = document.getElementById('md-toolbar-overflow-menu');
    const undoBtn = document.getElementById('md-toolbar-undo');

    _orderDocumentToolbar(itemsWrap);

    toolbar.querySelectorAll('.md-dd-toggle').forEach(button => {
      button.setAttribute('aria-haspopup', 'menu');
      button.setAttribute('aria-expanded', 'false');
    });

    toolbar.addEventListener('pointerdown', (e) => {
      const dd = e.target.closest('.md-dd-toggle');
      if (dd) dd._mdDdActivationToken = ownerDropdownActivationToken();
      _saveFormatSelection(e);
    });

    // Click handler for format buttons + the grouped dropdown toggles. The menu
    // is appended to <body> (not nested in the toolbar) so the draggable panel's
    // CSS transform doesn't reparent its fixed positioning or clip it.
    // Keep the editor's focus + selection when a format button / dropdown
    // toggle is pressed. Without this the button steals focus on press, which
    // collapses the textarea selection (so B/I/S apply to nothing) and, on
    // mobile, drops the keyboard — whose viewport resize then instantly closes
    // any dropdown that just opened. Preventing the default mousedown keeps the
    // textarea focused, so formatting hits the live selection and menus stay up.
    toolbar.addEventListener('mousedown', (e) => {
      if (_saveFormatSelection(e)) e.preventDefault();
    });

    toolbar.addEventListener('click', (e) => {
      const aiWriting = e.target.closest('.doc-ai-writing-btn');
      if (aiWriting) { e.preventDefault(); _showAiWritingMenu(aiWriting); return; }
      const dd = e.target.closest('.md-dd-toggle');
      if (dd) {
        e.preventDefault();
        // The contextual selection toolbar is useful until the main toolbar
        // is used. Hide it here so the dropdown opened by the user's latest
        // click cannot render underneath that older floating toolbar.
        _hideRichSelectionToolbar();
        _showMdDropdown(dd, null, dd._mdDdActivationToken);
        return;
      }
      const btn = e.target.closest('[data-md]');
      if (!btn) return;
      e.preventDefault();
      applyMdFormat(btn.dataset.md);
    });
    toolbar.addEventListener('keydown', (e) => {
      const dd = e.target.closest?.('.md-dd-toggle');
      if (!dd) return;
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'Enter' && e.key !== ' ') return;
      e.preventDefault();
      e.stopPropagation();
      _showMdDropdown(dd, e.key === 'ArrowUp' ? -1 : 0, ownerDropdownActivationToken());
    });

    // Undo button
    if (undoBtn) {
      undoBtn.addEventListener('click', (e) => {
        e.preventDefault();
        const ta = document.getElementById('doc-editor-textarea');
        if (ta) { ta.focus(); document.execCommand('undo'); }
      });
    }

    // Overflow collapse logic
    let _mdMenuOpen = false;
    // Horizontal-scroll affordance: the toolbar scrolls its icons; edge arrows
    // appear when there's more off either side and smoothly scroll to that edge.
    const scrollLeftBtn = document.getElementById('md-scroll-left');
    const scrollRightBtn = document.getElementById('md-scroll-right');
    function updateScrollArrows() {
      if (!itemsWrap || !scrollLeftBtn || !scrollRightBtn) return;
      const maxScroll = itemsWrap.scrollWidth - itemsWrap.clientWidth;
      const overflowing = maxScroll > 2;
      const showLeft = overflowing && itemsWrap.scrollLeft > 1;
      const showRight = overflowing && itemsWrap.scrollLeft < maxScroll - 1;
      toolbar.classList.toggle('has-left-scroll-arrow', showLeft);
      toolbar.classList.toggle('has-right-scroll-arrow', showRight);
      scrollLeftBtn.style.display = showLeft ? 'flex' : 'none';
      scrollRightBtn.style.display = showRight ? 'flex' : 'none';
    }
    scrollLeftBtn?.addEventListener('click', () => itemsWrap.scrollTo({ left: 0, behavior: 'smooth' }));
    scrollRightBtn?.addEventListener('click', () => itemsWrap.scrollTo({ left: itemsWrap.scrollWidth, behavior: 'smooth' }));
    itemsWrap?.addEventListener('scroll', updateScrollArrows, { passive: true });
    if (window.ResizeObserver && itemsWrap) {
      toolbar._resizeObserver = new ResizeObserver(updateScrollArrows);
      toolbar._resizeObserver.observe(itemsWrap);
    }

    function syncMdOverflow() {
      if (overflowWrapper) overflowWrapper.style.display = 'none';
      updateScrollArrows();
    }

    function closeMdMenu() {
      _mdMenuOpen = false;
      if (overflowMenu) overflowMenu.classList.remove('open');
    }

    if (overflowToggle) {
      overflowToggle.addEventListener('click', (e) => {
        e.stopPropagation();
        _mdMenuOpen = !_mdMenuOpen;
        if (_mdMenuOpen) {
          document.body.appendChild(overflowMenu);
          const rect = overflowToggle.getBoundingClientRect();
          overflowMenu.style.position = 'fixed';
          overflowMenu.style.top = (rect.bottom + 2) + 'px';
          overflowMenu.style.right = (window.innerWidth - rect.right) + 'px';
          overflowMenu.style.left = 'auto';
        } else {
          overflowWrapper.appendChild(overflowMenu);
        }
        overflowMenu.classList.toggle('open', _mdMenuOpen);
      });
    }
    const signal = toolbar.closest('#doc-editor-pane')?._lifetime?.signal;
    document.addEventListener('click', () => {
      if (_mdMenuOpen) { closeMdMenu(); overflowWrapper.appendChild(overflowMenu); }
    }, { signal });

    // Re-check overflow on resize
    let _mdResizeTimer;
    window.addEventListener('resize', () => {
      clearTimeout(_mdResizeTimer);
      _mdResizeTimer = setTimeout(syncMdOverflow, 100);
    }, { signal });
    signal?.addEventListener('abort', () => {
      clearTimeout(_mdResizeTimer);
      if (overflowMenu && !toolbar.contains(overflowMenu)) overflowMenu.remove();
    }, { once: true });

    // Show toolbar if language is already markdown
    const lang = document.getElementById('doc-language-select')?.value;
    if (lang === 'markdown') toolbar.style.display = '';

    // Initial sync after layout
    requestAnimationFrame(syncMdOverflow);
    // Expose for external calls (e.g. after fullscreen toggle)
    toolbar._syncOverflow = syncMdOverflow;
  }
