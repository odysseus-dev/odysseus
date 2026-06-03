// static/js/chat.js

/**
 * Main chat functionality - message handling and streaming
 */
// ES6 module — IIFE removed

import Storage from './storage.js';
import uiModule from './ui.js';
import sessionModule from './sessions.js';
import chatRenderer from './chatRenderer.js';
import chatStream from './chatStream.js';
import { addAITTSButton } from './tts-ai.js';
import markdownModule from './markdown.js';
import spinnerModule from './spinner.js';
import presetsModule from './presets.js';
import fileHandlerModule from './fileHandler.js';
import searchModule from './search.js';
import documentModule from './document.js';
import * as emailInbox from './emailInbox.js';
import codeRunnerModule from './codeRunner.js';
import slashCommands, { initSlashCommands, isCommand, handleSlashCommand, handleSetupInput, handleSetupWizard, typewriterInto } from './slashCommands.js';
import createResearchSynapse from './researchSynapse.js';
  const RESEARCH_TIMEOUT_MS = 360000;
  const DEFAULT_TIMEOUT_MS = 120000;
  const RESEARCH_SVG = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>';

  let API_BASE = '';
  let currentAbort = null;
  let isStreaming = false;
  // Continuous stall watchdog: while streaming, if the SSE stream produces
  // NOTHING for STALL_THRESHOLD_MS (no deltas, no tool heartbeat — tools beat
  // every 2s, so a full minute of silence means it's genuinely stuck or the
  // model quietly stopped), surface a non-destructive "still working?" prompt
  // instead of silently hanging. Replaces relying only on the tab-refocus
  // recovery (which fired only on visibilitychange and silently reloaded).
  let _stallWatchdog = null;
  let _stallBannerShown = false;
  const STALL_THRESHOLD_MS = 60000;
  let _sendInFlight = false;   // covers the window from click → streaming start
  let _displayOverride = null; // Override visible user bubble text (hides injected prompts)
  let _hideUserBubble = false; // Skip user bubble entirely (e.g. continue after stop)
  let _pendingContinue = null; // Stores the stopped AI element to merge with new response
  // ── Auto-recovery: when a turn's stream silently dies (connection drop) or
  // goes quiet while the connection is alive, re-engage the model with a
  // completion handshake instead of leaving it hung. Capped so it can't loop.
  let _autoNudges = 0;             // handshakes fired for the CURRENT user turn
  let _autoContinuePending = false; // marks the next submit as an auto-continue (don't reset the counter)
  const _AUTO_NUDGE_CAP = 3;

  // shortModel and modelColor are now in chatRenderer.js
  var _shortModel = chatRenderer.shortModel;
  var _applyModelColor = chatRenderer.applyModelColor;
  // Per-session research tracking (supports concurrent research across sessions)
  const _researchingStreamIds = new Set();
  let _researchTimerEl = null, _researchTimerInterval = null;
  let _researchStartTime = 0, _researchAvgDuration = null;
  let _researchSynapse = null;
  function _clearResearchTimer() {
    if (_researchTimerInterval) { clearInterval(_researchTimerInterval); _researchTimerInterval = null; }
    if (_researchTimerEl) { _researchTimerEl.remove(); _researchTimerEl = null; }
    if (_researchSynapse) {
      // Mark complete first so the user briefly sees the "done" state,
      // then tear it down on next tick.
      try { _researchSynapse.complete(); } catch {}
      const s = _researchSynapse;
      _researchSynapse = null;
      setTimeout(() => { try { s.destroy(); } catch {} }, 800);
    }
    _researchStartTime = 0;
    _researchAvgDuration = null;
  }

  /** Append a "Generate Visual Report" button — delegates to chatRenderer. */
  function _appendViewReportLink(msgEl, sessionId) {
    const body = msgEl.querySelector('.body');
    if (body) chatRenderer.appendReportButton(body, sessionId);
  }
  let currentAccumulated = ''; // Track accumulated text across function scope
  let currentHolder = null; // Track current message holder
  let currentSpinner = null; // Track current spinner for stop cleanup

  // Background streaming support
  const _backgroundStreams = new Map(); // sessionId -> { status, accumulated, sourcesHtml, abortCtrl, query, metrics }
  let _streamSessionId = null; // Session ID for the currently active reader loop
  let _lastReaderActivity = 0; // Timestamp of last reader.read() success — used to detect frozen streams
  let _webLockRelease = null;  // Function to release the Web Lock held during streaming

  /** Check if an SSE reader is still actively connected for a session. */
  function hasActiveStream(sessionId) {
    return _streamSessionId === sessionId || _backgroundStreams.has(sessionId);
  }

  // Sources box builder and toggleSources are now in chatRenderer.js
  var _buildSourcesBox = chatRenderer.buildSourcesBox;

  // Browser notifications now in chatStream.js
  var _notifyResearchComplete = chatStream.notifyResearchComplete;

  // Model/image pricing, _buildImageBubble now in chatRenderer.js
  var _buildImageBubble = chatRenderer.buildImageBubble;
  var getModelCost = chatRenderer.getModelCost;
  var getImageCost = chatRenderer.getImageCost;

  // stripToolBlocks and roleTimestamp now in chatRenderer.js
  var stripToolBlocks = chatRenderer.stripToolBlocks;

  function _normalizeEndpointForCompare(url) {
    if (!url) return '';
    try {
      const u = new URL(String(url), window.location.origin);
      let path = u.pathname.replace(/\/+$/, '');
      const suffixes = [
        '/v1/chat/completions', '/chat/completions',
        '/v1/completions', '/completions',
        '/v1/messages', '/messages',
        '/v1/models', '/models',
      ];
      for (const suffix of suffixes) {
        if (path.toLowerCase().endsWith(suffix)) {
          path = path.slice(0, -suffix.length).replace(/\/+$/, '');
          break;
        }
      }
      return (u.origin + path).toLowerCase();
    } catch (_) {
      return String(url).trim().replace(/\/+$/, '').toLowerCase();
    }
  }

  async function _probeCurrentEndpointStatus(endpointUrl, signal) {
    const target = _normalizeEndpointForCompare(endpointUrl);
    if (!target) return null;
    const modelsRes = await fetch(`${API_BASE}/api/models`, { credentials: 'same-origin', signal });
    if (!modelsRes.ok) return null;
    const modelsData = await modelsRes.json().catch(() => ({}));
    const item = (modelsData.items || []).find(ep =>
      _normalizeEndpointForCompare(ep.url || ep.endpoint_url || ep.base_url) === target
    );
    if (!item || !item.endpoint_id) return null;

    const probesRes = await fetch(`${API_BASE}/api/model-endpoints/probe-local`, {
      credentials: 'same-origin',
      signal,
    });
    if (!probesRes.ok) return null;
    const probes = await probesRes.json().catch(() => ({}));
    return probes[item.endpoint_id] || null;
  }

  /**
   * Initialize with dependencies
   */
  export function init(apiBase) {
    API_BASE = apiBase;
    initSlashCommands({ apiBase, isStreaming: () => isStreaming });
    // Initialize email inbox
    emailInbox.init(documentModule);
    // Wire the slash-command autocomplete popup on the chat composer. The
    // dispatcher already handles the typed command — this just surfaces the
    // registry as a discoverable menu when the user starts a message with /.
    import('./slashAutocomplete.js').then(mod => {
      const ta = document.getElementById('message');
      if (ta && mod.initSlashAutocomplete) mod.initSlashAutocomplete(ta);
    }).catch(() => {});
  }

  // addMessage, createMsgFooter, displayMetrics, hideWelcomeScreen, showWelcomeScreen
  // are now in chatRenderer.js — referenced via the public API delegation above.
  var addMessage = chatRenderer.addMessage;
  var createMsgFooter = chatRenderer.createMsgFooter;
  var displayMetrics = chatRenderer.displayMetrics;
  var hideWelcomeScreen = chatRenderer.hideWelcomeScreen;
  var showWelcomeScreen = chatRenderer.showWelcomeScreen;

  /**
   * Update submit button state
   */
  function updateSubmitButton(state, submitBtn) {
    if (!submitBtn) return;

    if (state === 'streaming') {
      // Clear any pending transitions from + → arrow swap
      submitBtn.classList.remove('anim-spin', 'anim-spin-swap', 'anim-land', 'mic-mode', 'newchat-mode', 'newchat-expanded', 'recording');
      // Ensure arrow icon is showing before launch
      var icons = window._odysseusBtnIcons;
      if (icons) submitBtn.innerHTML = icons.send;
      void submitBtn.offsetWidth;
      // Arrow launches up, then stop icon lands in
      submitBtn.classList.add('anim-launch');
      const _stopSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
      // Wait for the launch keyframe to finish (0.3s) before swapping the
      // arrow out for the stop icon — otherwise the swap happens mid-flight
      // and the user sees nothing fly out.
      setTimeout(() => {
        submitBtn.innerHTML = _stopSvg;
        submitBtn.classList.remove('anim-launch');
        void submitBtn.offsetWidth;
        submitBtn.classList.add('anim-land');
        submitBtn.addEventListener('animationend', () => submitBtn.classList.remove('anim-land'), { once: true });
      }, 300);
      submitBtn.title = 'Stop generation';
      submitBtn.dataset.mode = 'streaming';
      submitBtn.dataset.phase = 'processing';
      isStreaming = true;
      _startStallWatchdog();
    } else if (state === 'idle') {
      submitBtn.dataset.mode = '';
      delete submitBtn.dataset.phase;
      submitBtn.classList.remove('recording');
      isStreaming = false;
      _stopStallWatchdog();
      // Defer to global updater which handles mic/newchat/send modes
      if (window._updateSendBtnIcon) {
        setTimeout(window._updateSendBtnIcon, 50);
      } else {
        var icons = window._odysseusBtnIcons;
        submitBtn.innerHTML = icons ? icons.send : '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
        submitBtn.title = 'Send message';
        submitBtn.classList.remove('mic-mode', 'newchat-mode');
      }
    }
  }

  // -----------------------------------------------------------------------
  // Slash commands — now in slashCommands.js
  // -----------------------------------------------------------------------

  // API key pattern for the guard in handleChatSubmit
  const API_KEY_RE = /^(sk-[a-zA-Z0-9_\-]{20,}|gsk_[a-zA-Z0-9]{20,}|AIza[a-zA-Z0-9_\-]{30,}|xai-[a-zA-Z0-9]{20,})$/;


  /**
   * Handle chat form submission
   */
  export async function handleChatSubmit(e) {
    e.preventDefault();
    // Cancel research clarification timeout if active
    if (window._researchTimeoutTimer) {
      clearTimeout(window._researchTimeoutTimer);
      window._researchTimeoutTimer = null;
    }
    // Get current session
    const sessionId = sessionModule.getCurrentSessionId();
    const session = sessionModule.getSessions().find(s => s.id === sessionId);
    
    const submitBtn = document.querySelector('.send-btn');
    
    // If compare is active, stop all compare streams
    if (window.compareModule && window.compareModule.isActive()) {
      window.compareModule.handleCompareSubmit();
      return;
    }

    // If currently streaming, stop it
    if (isStreaming) {
      // Cancel server-side research if in progress
      const _cancelSid = sessionModule.getCurrentSessionId();
      if (_cancelSid && _researchingStreamIds.has(_cancelSid)) {
        fetch(`${API_BASE}/api/research/cancel/${_cancelSid}`, { method: 'POST' }).catch(e => console.warn('Research cancel failed:', e));
        _researchingStreamIds.delete(_cancelSid);
        _clearResearchTimer();
      }
      abortCurrentRequest(true);  // explicit user Stop → also cancel the detached server run

      // Clean up any running agent thread nodes (stop wave animation, remove "running" state)
      document.querySelectorAll('.agent-thread-node.running').forEach(node => {
        if (node._waveInterval) { clearInterval(node._waveInterval); node._waveInterval = null; }
        if (node._elapsedTicker) { clearInterval(node._elapsedTicker); node._elapsedTicker = null; }
        node.classList.remove('running');
        const wave = node.querySelector('.agent-thread-wave');
        if (wave) wave.textContent = '';
        const icon = node.querySelector('.agent-thread-icon');
        if (icon) icon.textContent = '\u25A0'; // stop square
        const statusEl = node.querySelector('.agent-thread-status');
        if (!statusEl) {
          const header = node.querySelector('.agent-thread-header');
          if (header) {
            const s = document.createElement('span');
            s.className = 'agent-thread-status';
            s.textContent = 'stopped';
            header.appendChild(s);
          }
        }
      });
      document.querySelectorAll('.agent-thread.streaming').forEach(t => t.classList.remove('streaming'));

      // Clean up any thinking spinners
      document.querySelectorAll('.agent-thinking-dots').forEach(el => {
        if (el._spinner) el._spinner.destroy();
        el.remove();
      });
      // No text accumulated — remove the empty holder with spinner
      if (currentHolder && !currentAccumulated) {
        if (currentSpinner) { currentSpinner.destroy(); currentSpinner = null; }
        // Empty cancel — keep the assistant bubble around with a "Cancelled
        // by user" indicator and persist a placeholder server-side so the
        // turn survives a refresh instead of vanishing without a trace.
        _renderCancelledBubble(currentHolder);
        currentHolder = null;
        updateSubmitButton('idle', submitBtn);
        const messageInput = uiModule.el('message');
        if (messageInput) messageInput.disabled = false;
        currentAccumulated = '';
        return;
      }
      // Render whatever was accumulated so far
      if (currentHolder && currentAccumulated) {
        // Store accumulated in a closure variable before it gets cleared
        const stoppedContent = currentAccumulated;
        
        // Store raw content in dataset for consistency with other messages
        currentHolder.dataset.raw = stoppedContent;
        
        currentHolder.querySelector('.body').innerHTML = markdownModule.processWithThinking(
          markdownModule.squashOutsideCode(stoppedContent)
        );
        
        // Highlight code blocks
        if (window.hljs) {
          currentHolder.querySelectorAll('pre code').forEach((block) => {
            window.hljs.highlightElement(block);
          });
        }
        
        // Add the stopped indicator with continue button
        const stoppedIndicator = document.createElement('div');
        stoppedIndicator.className = 'stopped-indicator';
        const stoppedLabel = document.createElement('span');
        stoppedLabel.textContent = '[Message interrupted]';
        stoppedIndicator.appendChild(stoppedLabel);
        const continueBtn = document.createElement('button');
        continueBtn.className = 'continue-btn';
        continueBtn.title = 'Continue';
        continueBtn.textContent = '\u25B8';
        const _stoppedHolder = currentHolder; // capture before it gets cleared
        continueBtn.addEventListener('click', () => {
          stoppedIndicator.remove();
          _hideUserBubble = true;
          _pendingContinue = _stoppedHolder;
          const cutoff = stoppedContent;
          const msgInput = uiModule.el('message');
          if (msgInput) {
            msgInput.value = 'Your previous response was interrupted. It ended with:\n\n' + cutoff.slice(-500) + '\n\nDo NOT repeat what you already said. Continue exactly from where you were cut off.';
            const sb = document.querySelector('.send-btn');
            if (sb) sb.click();
          }
        });
        stoppedIndicator.appendChild(continueBtn);
        currentHolder.querySelector('.body').appendChild(stoppedIndicator);

        // Tell server to mark this message as stopped
        const _sid = sessionModule.getCurrentSessionId();
        if (_sid) fetch(`${API_BASE}/api/session/${_sid}/mark-stopped`, { method: 'POST' }).catch(e => console.warn('mark-stopped failed:', e));

        // Add footer with copy/regen if not already present
        if (!currentHolder.querySelector('.msg-footer')) {
          currentHolder.dataset.raw = stoppedContent;
          currentHolder.appendChild(createMsgFooter(currentHolder));
        }

        uiModule.scrollHistory();
      }
      
      // Reset button state
      updateSubmitButton('idle', submitBtn);
      
      // Re-enable message input
      const messageInput = uiModule.el('message');
      if (messageInput) messageInput.disabled = false;
      
      // Clear tracking variables
      currentAccumulated = '';
      currentHolder = null;

      return;
    }

    // --- Send-path entry: block re-clicks between submit and stream start ---
    if (_sendInFlight) return;
    _sendInFlight = true;
    // Instant visual feedback so the user sees their click was accepted
    // even before the streaming button state kicks in below.
    const _earlyMessageInput = uiModule.el('message');
    if (_earlyMessageInput) _earlyMessageInput.disabled = true;
    if (submitBtn) submitBtn.classList.add('send-pending');
    const _releaseSendFlag = () => {
      _sendInFlight = false;
      if (_earlyMessageInput) _earlyMessageInput.disabled = false;
      if (submitBtn) submitBtn.classList.remove('send-pending');
    };

    // --- Setup mode: intercept next message (but let slash commands through) ---
    {
      const el = uiModule.el;
      const rawMsg = (el('message').value || '').trim();
      const currentSetupMode = slashCommands.getSetupMode();
      if (currentSetupMode && rawMsg && !isCommand(rawMsg)) {
        const mode = currentSetupMode;
        slashCommands.clearSetupMode(mode === 'endpoint-provider' || mode === 'endpoint-key-for-provider');
        el('message').value = '';
        if (window._syncModelPickerAutohide) window._syncModelPickerAutohide();
        if (uiModule.autoResize) uiModule.autoResize(el('message'));
        if (mode === true || mode === 'endpoint') {
          handleSetupInput(rawMsg);
        } else {
          handleSetupWizard(mode, rawMsg);
        }
        _releaseSendFlag();
        return;
      }
      if (currentSetupMode && rawMsg && isCommand(rawMsg)) {
        slashCommands.clearSetupMode();  // Clear setup mode, fall through to slash handler
      }
    }

    const el = uiModule.el;
    const msg = el('message').value;
    // Allow empty text when a regen carries over the original message's
    // attachment ids — a photo-only message still has something to send.
    if (!msg.trim() && !fileHandlerModule.getPendingCount() && !(_pendingRegenAttachments && _pendingRegenAttachments.length)) { _releaseSendFlag(); return; }

    // --- Slash commands: execute directly without AI (no session needed) ---
    if (isCommand(msg.trim())) {
      const handled = await handleSlashCommand(msg.trim());
      if (handled) {
        el('message').value = '';
        if (window._syncModelPickerAutohide) window._syncModelPickerAutohide();
        if (uiModule.autoResize) uiModule.autoResize(el('message'));
        _releaseSendFlag();
        return;
      }
    }

    // Materialize pending session (deferred from model click) on first message
    if (sessionModule.hasPendingChat && sessionModule.hasPendingChat()) {
      const ok = await sessionModule.materializePendingSession();
      if (!ok || !sessionModule.getCurrentSessionId()) { _releaseSendFlag(); return; }
    }

    if (!sessionModule.getCurrentSessionId()) {
      // Auto-create a session using default chat config. Always fetch fresh
      // so that a recent Settings change takes effect without a page reload.
      try {
        let dc = null;
        try {
          const dcRes = await fetch('/api/default-chat');
          dc = await dcRes.json();
          if (dc && dc.endpoint_url && dc.model) {
            try { window.__odysseusDefaultChat = dc; } catch (_) {}
          }
        } catch (_) {
          dc = (typeof window !== 'undefined' && window.__odysseusDefaultChat) || null;
        }
        if (dc.endpoint_url && dc.model) {
          await sessionModule.createDirectChat(dc.endpoint_url, dc.model, dc.endpoint_id);
          const ok = await sessionModule.materializePendingSession();
          if (!ok || !sessionModule.getCurrentSessionId()) { _releaseSendFlag(); return; }
        } else {
          el('message').value = '';
          if (uiModule.autoResize) uiModule.autoResize(el('message'));
          addMessage('assistant',
            'No chat session active. You can:\n\n' +
            '- Open the model picker in the chat box and pick a model\n' +
            '- Use the `+` button in the model picker to add a model endpoint\n' +
            '- Use `/help` to see all available commands');
          _releaseSendFlag();
          return;
        }
      } catch (e) {
        el('message').value = '';
        if (uiModule.autoResize) uiModule.autoResize(el('message'));
        addMessage('assistant',
          'No chat session active. You can:\n\n' +
          '- Open the model picker in the chat box and pick a model\n' +
          '- Use the `+` button in the model picker to add a model endpoint\n' +
          '- Use `/help` to see all available commands');
        _releaseSendFlag();
        return;
      }
    }

    // --- API key guard: warn if message looks like an API key ---
    if (API_KEY_RE.test(msg.trim())) {
      if (!await window.styledConfirm('This looks like an API key. Sending it to the AI could expose it.\n\nDid you mean to use /setup instead?', { confirmText: 'Send anyway', danger: true })) {
        _releaseSendFlag();
        return;
      }
    }


    const messageInput = el('message');
    const originalBtnText = submitBtn ? submitBtn.innerHTML : '';

    // Re-enable the textarea now that we've handed off to the stream: the
    // user wants to compose the next message while the AI is still talking.
    // The `isStreaming` flag is the re-click guard for the send button.
    if (messageInput) messageInput.disabled = false;
    updateSubmitButton('streaming', submitBtn);
    if (submitBtn) submitBtn.classList.remove('send-pending');
    _sendInFlight = false;

    // Capture session ID for background stream detection
    const streamSessionId = sessionModule.getCurrentSessionId();
    _streamSessionId = streamSessionId;
    const streamQuery = msg;
    _lastReaderActivity = Date.now();

    // Acquire Web Lock to hint browser not to discard this tab while streaming
    if (navigator.locks) {
      navigator.locks.request('odysseus-stream-' + streamSessionId, { mode: 'exclusive', ifAvailable: true }, lock => {
        if (!lock) return; // Another stream already holds a lock — fine
        return new Promise(resolve => { _webLockRelease = resolve; });
      }).catch(e => console.warn('web lock acquire failed:', e)); // Ignore lock errors — best-effort
    }

    // Declare accumulated outside try block so it's accessible in catch
    let accumulated = '';
    // Are we currently inside an unclosed <think> block? Toggled per think/answer
    // cycle so a multi-round agent response (one reasoning phase PER round) wraps each
    // round's reasoning in its own <think>…</think> instead of leaking rounds 2+ as text.
    let _thinkOpen = false;
    let holder = null;
    let finalMeta = null;
    let finalModelName = null;
    let spinner = null;
    let timedOut = false;
    let processingProbeTimer = null;
    let processingProbeAbort = null;
    let _renderStream = () => {};
    let _cancelThinkingTimer = () => {};
    let _removeThinkingSpinner = () => {};
    const clearProcessingProbe = () => {
      if (processingProbeTimer) {
        clearTimeout(processingProbeTimer);
        processingProbeTimer = null;
      }
      if (processingProbeAbort) {
        try { processingProbeAbort.abort(); } catch (_) {}
        processingProbeAbort = null;
      }
    };

    // Reset tracking variables at start
    currentAccumulated = '';
    currentHolder = null;
    
    try {
      // Re-enable auto-scroll when user sends a message
      uiModule.setAutoScroll(true);
      uiModule.scrollHistoryInstant();
      // Clear completed dot now that user is interacting
      if (sessionModule.clearStreamComplete) sessionModule.clearStreamComplete(sessionModule.getCurrentSessionId());

      // Check for document selection context before consuming display override
      const docSel = documentModule && documentModule.getSelectionContext();
      if (docSel) {
        const sels = Array.isArray(docSel) ? docSel : [docSel];
        const lineRefs = sels.map(s =>
          s.startLine === s.endLine ? `L${s.startLine}` : `L${s.startLine}-${s.endLine}`
        );
        _displayOverride = `[Doc edit: ${lineRefs.join(', ')}] ${msg}`;
      }

      const userDisplay = _displayOverride || msg;
      _displayOverride = null;
      const skipBubble = _hideUserBubble;
      _hideUserBubble = false;
      // Auto-recovery counter: carries across a turn's auto-continues, but resets
      // when the user genuinely sends a new message (so each task gets a fresh cap).
      // A real user turn (visible bubble) ALWAYS resets the budget — even if a
      // prior auto-continue's deferred click never cleared the pending flag — so a
      // stuck flag can't silently eat the next turn's recovery budget.
      if (!skipBubble) { _autoNudges = 0; _autoContinuePending = false; }
      else if (_autoContinuePending) { _autoContinuePending = false; }
      const _pendingAttachInfo = fileHandlerModule.getPendingCount() ? fileHandlerModule.getPendingInfo() : null;
      // Pre-read importable file contents before upload clears pending files
      const IMPORTABLE_EXT = /\.(txt|py|js|ts|html|htm|css|md|json|csv|yml|yaml|sh|sql|rs|go|java|c|cpp|h|rb|php|xml|jsx|tsx|log|toml|ini|conf|env|vue|svelte|scss|sass|less)$/i;
      const _importableFiles = [];
      if (_pendingAttachInfo && documentModule) {
        const rawFiles = fileHandlerModule.getPendingRaw ? fileHandlerModule.getPendingRaw() : [];
        for (let i = 0; i < _pendingAttachInfo.length; i++) {
          const att = _pendingAttachInfo[i];
          if (IMPORTABLE_EXT.test(att.name) && rawFiles[i]) {
            _importableFiles.push({ info: att, file: rawFiles[i] });
          }
        }
      }
      let _userMsgEl = null;
      if (!skipBubble) {
        _userMsgEl = addMessage('user', userDisplay, null, _pendingAttachInfo ? { attachments: _pendingAttachInfo } : null);
      }
      messageInput.value = '';
      messageInput.style.height = '';
      messageInput.dispatchEvent(new Event('input'));
      // Mobile: dismiss the on-screen keyboard after sending. iOS in
      // particular ignores a bare blur() in some cases (or some other
      // listener refocuses straight after), so we temporarily mark the
      // input readonly which forces the keyboard to retract, then blur,
      // then drop the readonly attribute after the keyboard is gone so
      // typing still works for the next message.
      if (window.innerWidth <= 768) {
        try {
          messageInput.setAttribute('readonly', 'readonly');
          messageInput.blur();
          const _dropReadonly = () => { try { messageInput.removeAttribute('readonly'); } catch {} };
          setTimeout(() => {
            // If the blur stuck, the input is no longer the active element —
            // safe to drop readonly now so the next message can be typed.
            // If it did NOT stick (some mobile browsers keep the textarea
            // focused after a programmatic blur), removing readonly here would
            // re-summon the keyboard mid-stream — the "bounce up" that then
            // lingers until the end-of-stream blur. In that case keep readonly
            // on (keyboard stays down) and drop it the moment the user taps to
            // type again, so typing still works without the bounce.
            if (document.activeElement === messageInput) {
              messageInput.addEventListener('pointerdown', _dropReadonly, { once: true });
              messageInput.addEventListener('focus', _dropReadonly, { once: true });
            } else {
              _dropReadonly();
            }
          }, 120);
        } catch {}
      }

      let ids = [];
      try {
        ids = await fileHandlerModule.uploadPending();
      } catch(e) {
        console.error('upload failed', e);
      }

      // Carry over the original message's file-ids on a regenerate so the new
      // send still references the same photos / docs (and picks up the user's
      // edited OCR text via the server-side .vision cache). Always CONSUME the
      // slot — even when empty / errored — so the regen ids can't bleed into
      // an unrelated next message if uploadPending() above had thrown.
      if (_pendingRegenAttachments && _pendingRegenAttachments.length) {
        ids = ids.concat(_pendingRegenAttachments);
      }
      _pendingRegenAttachments = null;

      // The optimistic user bubble was rendered before the upload assigned ids,
      // so image previews couldn't show (the renderer needs att.id). Now that
      // the upload resolved, stamp the ids — plus width/height for images so
      // the skeleton can size itself to the photo's aspect ratio — and
      // re-render so the thumbnail appears live, no refresh needed.
      if (_userMsgEl && _pendingAttachInfo && ids.length) {
        const _meta = fileHandlerModule.getLastUploadedMeta?.() || [];
        for (let i = 0; i < _pendingAttachInfo.length && i < ids.length; i++) {
          _pendingAttachInfo[i].id = ids[i];
          const _m = _meta[i];
          if (_m) {
            if (_m.width)  _pendingAttachInfo[i].width  = _m.width;
            if (_m.height) _pendingAttachInfo[i].height = _m.height;
          }
        }
        chatRenderer.updateMessageAttachments(_userMsgEl, _pendingAttachInfo);
      }

      // Offer to import text files to document library
      if (_importableFiles.length > 0) {
        const existing = document.getElementById('import-prompt-banner');
        if (existing) existing.remove();
        const banner = document.createElement('div');
        banner.id = 'import-prompt-banner';
        banner.className = 'import-prompt-banner';
        const label = _importableFiles.length === 1
          ? `Import "${_importableFiles[0].info.name}" to document library?`
          : `Import ${_importableFiles.length} files to document library?`;
        const textEl = document.createElement('span');
        textEl.textContent = label;
        banner.appendChild(textEl);
        const importBtn = document.createElement('button');
        importBtn.textContent = 'Import';
        importBtn.addEventListener('click', async () => {
          importBtn.disabled = true;
          importBtn.textContent = 'Importing…';
          const EXT_LANG = {'.py':'python','.js':'javascript','.ts':'typescript','.html':'html','.css':'css','.md':'markdown','.json':'json','.yml':'yaml','.yaml':'yaml','.sh':'bash','.sql':'sql','.rs':'rust','.go':'go','.java':'java','.c':'c','.cpp':'cpp','.rb':'ruby','.php':'php','.xml':'xml','.jsx':'javascript','.tsx':'typescript'};
          let imported = 0;
          for (const { info, file } of _importableFiles) {
            try {
              const content = await file.text();
              const dotIdx = info.name.lastIndexOf('.');
              const title = dotIdx > 0 ? info.name.slice(0, dotIdx) : info.name;
              const ext = dotIdx >= 0 ? info.name.slice(dotIdx).toLowerCase() : '';
              await fetch(`${API_BASE}/api/document`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, language: EXT_LANG[ext] || '', content }),
              });
              imported++;
            } catch (e) { console.error('Import failed:', info.name, e); }
          }
          banner.textContent = `Imported ${imported} file${imported !== 1 ? 's' : ''}`;
          setTimeout(() => banner.remove(), 2000);
        });
        banner.appendChild(importBtn);
        const dismissBtn = document.createElement('button');
        dismissBtn.textContent = '\u00d7';
        dismissBtn.className = 'import-prompt-dismiss';
        dismissBtn.addEventListener('click', () => banner.remove());
        banner.appendChild(dismissBtn);
        const chatBar = document.getElementById('chat-bar');
        if (chatBar) chatBar.parentNode.insertBefore(banner, chatBar);
        // Auto-dismiss after 15 seconds
        setTimeout(() => { if (banner.parentNode) banner.remove(); }, 15000);
      }

      // Auto-save document editor content before sending so the AI sees latest text
      if (documentModule && documentModule.isPanelOpen() && documentModule.getCurrentDocId()) {
        try { await documentModule.saveDocument(); } catch(e) { console.warn('doc auto-save failed', e); }
      }

      // Inject document selection context if present
      let finalMsg = msg;
      if (docSel) {
        const sels = Array.isArray(docSel) ? docSel : [docSel];
        if (sels.length === 1) {
          const s = sels[0];
          const lineRef = s.startLine === s.endLine ? `line ${s.startLine}` : `lines ${s.startLine}-${s.endLine}`;
          finalMsg = `In the document, edit this specific text (${lineRef}):\n\`\`\`\n${s.text}\n\`\`\`\n\nInstruction: ${msg}`;
        } else {
          const parts = sels.map((s, i) => {
            const lineRef = s.startLine === s.endLine ? `line ${s.startLine}` : `lines ${s.startLine}-${s.endLine}`;
            return `Selection ${i + 1} (${lineRef}):\n\`\`\`\n${s.text}\n\`\`\``;
          });
          finalMsg = `In the document, edit these specific sections:\n\n${parts.join('\n\n')}\n\nInstruction: ${msg}`;
        }
      }

      // Apply inject prefix/suffix
      const _inject = presetsModule.getInject ? presetsModule.getInject() : { prefix: '', suffix: '' };
      let _finalMsgWithInject = finalMsg;
      if (_inject.prefix) _finalMsgWithInject = _inject.prefix + ' ' + _finalMsgWithInject;
      if (_inject.suffix) _finalMsgWithInject = _finalMsgWithInject + ' ' + _inject.suffix;

      const fd = new FormData();
      fd.append('message', _finalMsgWithInject);
      fd.append('session', streamSessionId);
      if (ids.length) fd.append('attachments', JSON.stringify(ids));
      // Auto-save & send active doc ID so the backend sees latest content
      if (documentModule && documentModule.isPanelOpen() && documentModule.getCurrentDocId()) {
        try { await documentModule.saveDocument({ silent: true }); } catch (_e) { /* best-effort */ }
        fd.append('active_doc_id', documentModule.getCurrentDocId());
      }
      // Web toggle: pre-search in Chat mode, tool permission in Agent mode
      const toggleState = Storage.loadToggleState();
      let isAgentMode = (toggleState.mode || 'chat') === 'agent';
      // Auto-escalate to agent mode when a document is open — the user expects
      // the AI to see the document and have tools to edit it
      if (!isAgentMode && documentModule && documentModule.isPanelOpen() && documentModule.getCurrentDocId()) {
        isAgentMode = true;
      }
      fd.append('mode', isAgentMode ? 'agent' : 'chat');
      if (el('web-toggle').checked) {
        if (isAgentMode) {
          fd.append('allow_web_search', 'true');
        } else {
          fd.append('use_web', 'true');
        }
      }
      if (el('research-toggle').checked) {
        fd.append('use_research', 'true');
        // Research always runs in chat mode — override agent if set
        fd.set('mode', 'chat');
      }
      if (el('bash-toggle').checked) {
        fd.append('allow_bash', 'true');
      }
      const ragChk = el('rag-toggle');
      if (ragChk && !ragChk.checked) {
        fd.append('use_rag', 'false');
      }
      const incognitoChk = el('incognito-toggle');
      if (incognitoChk && incognitoChk.checked) {
        fd.append('incognito', 'true');
      }
      if (presetsModule.getSelectedPreset()) {
        fd.append('preset_id', presetsModule.getSelectedPreset());
      }


      const abortCtrl = new AbortController();
      abortCtrl._reason = '';
      currentAbort = abortCtrl;

      const _tState = Storage.loadToggleState();
      const _isAgent = (_tState.mode || 'chat') === 'agent';

      // Timeout: 6 min for research and agent mode, 3 min otherwise
      const timeoutMs = el('research-toggle').checked || _isAgent ? RESEARCH_TIMEOUT_MS : DEFAULT_TIMEOUT_MS;
      const timeoutId = setTimeout(() => {
        if (!abortCtrl.signal.aborted) {
          timedOut = true;
          abortCtrl._reason = 'timeout';
          abortCtrl.abort();
        }
      }, timeoutMs);
      
      const box = el('chat-history');
      holder = document.createElement('div');
      holder.className = 'msg msg-ai streaming';

      // Track holder globally so stop button can access it
      currentHolder = holder;
      holder._researchQuery = msg; // Store query for notification text
      
      const modelName = sessionModule.getCurrentModel() || null;

      let loadingText = 'Initializing...';

      if (el('web-toggle').checked && !_isAgent) {
        const _searchLabel = searchModule ? searchModule.getProviderLabel() : 'web';
        loadingText = `Searching via ${_searchLabel}...<br>
                       <span style="font-size: 0.9em; opacity: 0.8;">
                       Query: "${msg.substring(0, 50)}${msg.length > 50 ? '...' : ''}"<br>
                       Fetching top results...</span>`;
      } else if (el('research-toggle').checked) {
        loadingText = 'Deep research mode active...';
      } else {
        loadingText = 'Processing request...';
      }

      var roleLabel = _shortModel(modelName);
      var _charNameInit = presetsModule.getCharacterName ? presetsModule.getCharacterName() : '';
      if (_charNameInit) roleLabel = _charNameInit;
      const roleTs = new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
      holder.innerHTML = `<div class="role">${roleLabel} <span class="role-timestamp">${roleTs}</span></div><div class="body"></div>`;
      _applyModelColor(holder.querySelector('.role'), modelName);
      holder.style.position = 'relative';
      
      // Create spinner
      spinner = spinnerModule.create('Initializing', 'right', 'wave');
      currentSpinner = spinner;
      const bodyDiv = holder.querySelector('.body');
      bodyDiv.appendChild(spinner.createElement());
      spinner.start();
      
      // Update spinner message based on mode
      if (el('web-toggle').checked && !_isAgent) {
        spinner.updateMessage('Searching web with ' + (searchModule ? searchModule.getProviderLabel() : 'SearXNG'));
        setTimeout(() => spinner.updateMessage('Processing results'), 1500);
      } else if (el('research-toggle').checked) {
        spinner.updateMessage('Researching');
        setTimeout(() => spinner.updateMessage('Analyzing sources'), 1500);
      } else {
        spinner.updateMessage('Processing request');
        const endpointUrlForProbe = sessionModule.getCurrentEndpointUrl ? sessionModule.getCurrentEndpointUrl() : null;
        if (endpointUrlForProbe && modelName) {
          processingProbeTimer = setTimeout(async () => {
            processingProbeTimer = null;
            if (accumulated || !spinner || !spinner.element || (currentAbort && currentAbort.signal.aborted)) return;
            processingProbeAbort = new AbortController();
            try {
              spinner.updateMessage('Checking model endpoint');
              const status = await _probeCurrentEndpointStatus(endpointUrlForProbe, processingProbeAbort.signal);
              if (accumulated || !spinner || !spinner.element || (currentAbort && currentAbort.signal.aborted)) return;
              if (!status) {
                spinner.updateMessage('Still waiting for model');
              } else if (status.alive) {
                const latency = status.latency_ms ? ` (${status.latency_ms}ms)` : '';
                spinner.updateMessage(`Endpoint online${latency}; waiting for first token`);
              } else {
                // Probe confirms the endpoint isn't responding. Don't
                // sit on a hung fetch — give the user 5s to read the
                // status, then auto-abort with reason='offline' so the
                // catch handler shows a clean "switch model" message
                // instead of leaving the spinner spinning forever.
                if (status.error) console.warn('Model endpoint probe failed:', status.error);
                let _countdown = 5;
                spinner.updateMessage(`Endpoint offline — cancelling in ${_countdown}s`);
                const _tick = setInterval(() => {
                  _countdown--;
                  if (!spinner || !spinner.element || (currentAbort && currentAbort.signal.aborted) || accumulated) {
                    clearInterval(_tick);
                    return;
                  }
                  if (_countdown > 0) {
                    spinner.updateMessage(`Endpoint offline — cancelling in ${_countdown}s`);
                  } else {
                    clearInterval(_tick);
                    if (currentAbort && !currentAbort.signal.aborted) {
                      currentAbort._reason = 'offline';
                      currentAbort.abort();
                    }
                  }
                }, 1000);
              }
            } catch (e) {
              if (e && e.name !== 'AbortError' && spinner && spinner.element && !accumulated) {
                spinner.updateMessage('Still waiting for model');
              }
            } finally {
              processingProbeAbort = null;
            }
          }, 10000);
        }
      }
      
      const researchBtn = el('research-toggle-btn');
      if (el('research-toggle').checked && researchBtn) {
        researchBtn.disabled = true;
        researchBtn.classList.remove('active');
      }
      box.appendChild(holder);
      uiModule.scrollHistory();

      const enableResearchBtn = () => {
        if (!researchBtn) return;
        researchBtn.disabled = false;
        researchBtn.classList.toggle('active', el('research-toggle').checked);
      };

      if (el('research-toggle').checked && researchBtn) {
        researchBtn.style.display = 'none';
        // Uncheck research toggle so follow-up messages don't trigger another research
        el('research-toggle').checked = false;
      }

      // User's current UTC offset in minutes (east of UTC). Threaded into
      // the agent so natural-language times like "today at 9pm" are
      // interpreted in YOUR timezone, not the server's.
      const _tzOffsetMin = -new Date().getTimezoneOffset();
      const res = await fetch(`${API_BASE}/api/chat_stream`, {
        method: 'POST',
        body: fd,
        headers: { 'X-Tz-Offset': String(_tzOffsetMin) },
        signal: abortCtrl.signal
      });
      
      clearTimeout(timeoutId);
      
      if (!res.ok) {
        if (res.status === 404) {
          // Session was deleted (e.g. by AI) — reload and go to welcome
          holder.remove();
          if (sessionModule) await sessionModule.loadSessions();
          return;
        }
        let errText = `Error ${res.status}`;
        try {
          const errBody = await res.text();
          // Parse nested JSON error if present
          const m = errBody.match(/"message"\s*:\s*"([^"]+)"/);
          if (m) errText = m[1].replace(/\\"/g, '"');
          else if (errBody.length < 200) errText = errBody;
        } catch {}
        // Auto-switch to chat mode for tool-related errors
        if (errText.includes('tool') || errText.includes('auto')) {
          errText = 'This model doesn\'t support agent tools — switched to Chat mode. Try again.';
          const _ab = document.getElementById('mode-agent-btn');
          const _cb = document.getElementById('mode-chat-btn');
          if (_ab && _cb) {
            _ab.classList.remove('active');
            _cb.classList.add('active');
            const _toggle = _ab.closest('.mode-toggle');
            if (_toggle) _toggle.classList.add('mode-chat');
          }
          if (typeof Storage !== 'undefined' && Storage.KEYS) {
            const _st = Storage.getJSON(Storage.KEYS.TOGGLES, {});
            _st.mode = 'chat';
            Storage.setJSON(Storage.KEYS.TOGGLES, _st);
          }
        }
        typewriterInto(holder.querySelector('.body'), errText);
        enableResearchBtn();
        return;
      }

      // Mark the chat log busy while streaming so screen readers wait for the
      // settled response instead of announcing every token. Cleared in finally.
      const _chatLog = document.getElementById('chat-history');
      if (_chatLog) _chatLog.setAttribute('aria-busy', 'true');

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let metrics = null;
      let isThinking = false;
      let thinkingStartTime = null;
      // Streaming TTS: synthesize sentence-by-sentence during streaming
      const streamingTTS = !!(window.aiTTSManager && window.aiTTSManager.autoPlay && window.aiTTSManager.available);
      if (streamingTTS) window.aiTTSManager.streamingStart();
      // Multi-bubble agent tracking
      let roundHolder = holder;       // Current AI text bubble (changes per round)
      let roundText = '';             // Text accumulated for current round
      let currentToolBubble = null;   // Current tool execution bubble
      let roundFinalized = false;     // Whether current round's text is finalized
      let _sourcesHtml = '';          // Sources box HTML to prepend to body
      let _sourcesExpanded = false;   // Track if user expanded sources during stream
      let _sourcesData = null;        // Raw sources data for rebuilding
      let _sourcesType = '';          // 'web' or 'research'
      let _findingsData = null;      // Raw findings data for collapsible box
      // _keepResearchOn removed — clarification state now persisted server-side via DB mode
      // Insert sources box as a stable DOM node that won't be replaced during streaming.
      // Returns the content container to use for innerHTML updates.
      function _ensureStreamLayout(body) {
        if (!body) return body;
        // Sources are deferred to final render — don't insert during streaming
        // Ensure a stable content div exists for text content
        var contentDiv = body.querySelector('.stream-content');
        if (!contentDiv) {
          contentDiv = document.createElement('div');
          contentDiv.className = 'stream-content';
          body.appendChild(contentDiv);
        }
        return contentDiv;
      }
      const esc = uiModule.esc;
      // Remove thinking spinner helper
      _removeThinkingSpinner = () => {
        const el = document.querySelector('.agent-thinking-dots');
        if (el) {
          if (el._spinner) el._spinner.destroy();
          el.remove();
        }
      };

      // Tool-aware thinking spinner
      let _lastToolName = '';
      const _searchIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" style="vertical-align:-2px;margin-right:4px"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>';
      const _toolLabels = {
        'web_search': _searchIcon + 'Searching',
        'bash': 'Running',
        'python': 'Running',
        'create_document': 'Writing',
        'update_document': 'Writing',
        'read_document': 'Reading',
        'edit_file': 'Editing',
        'read_file': 'Reading',
        'write_file': 'Writing',
        'list_files': 'Browsing',
        'image_gen': 'Generating',
        'generate_image': 'Generating',
        'manage_memory': 'Remembering',
        'save_memory': 'Remembering',
        'search_memory': 'Recalling',
        'manage_session': 'Organizing',
        'deep_research': 'Researching',
        'list_models': 'Browsing',
        'ui_control': 'Adjusting',
      };
      function _thinkingLabel() {
        if (!_lastToolName) {
          return 'Thinking';
        }
        // Check exact match first, then prefix match
        const lower = _lastToolName.toLowerCase();
        if (_toolLabels[lower]) return _toolLabels[lower];
        for (const [key, label] of Object.entries(_toolLabels)) {
          if (lower.includes(key) || key.includes(lower)) return label;
        }
        return 'Thinking';
      }

      function _showThinkingSpinner(label) {
        if (document.querySelector('.agent-thinking-dots')) return;
        const _thinkMsg = document.createElement('div');
        _thinkMsg.className = 'msg msg-ai agent-thinking-dots';
        const _thinkBody = document.createElement('div');
        _thinkBody.className = 'body';
        const _ts = spinnerModule.create(label || 'Thinking', 'right', 'wave');
        _thinkBody.appendChild(_ts.createElement());
        _ts.start(120);
        _thinkMsg._spinner = _ts;
        _thinkMsg.appendChild(_thinkBody);
        document.getElementById('chat-history').appendChild(_thinkMsg);
        uiModule.scrollHistory();
      }

      // Auto-show thinking spinner after text stops streaming
      let _textPauseTimer = null;
      function _scheduleThinkingSpinner() {
        if (_textPauseTimer) clearTimeout(_textPauseTimer);
        _textPauseTimer = setTimeout(() => {
          if (!document.querySelector('.agent-thinking-dots') && isStreaming) {
            _showThinkingSpinner(_thinkingLabel());
          }
        }, 400);
      }
      _cancelThinkingTimer = () => {
        if (_textPauseTimer) { clearTimeout(_textPauseTimer); _textPauseTimer = null; }
      };

      // Document streaming state (text-fence detection)
      let _docFenceOpened = false;
      let _docFenceContentStart = -1;
      let _liveThinkSection = null;
      let _liveThinkContent = null;
      let _liveThinkInner = null;
      let _liveThinkHeader = null;
      let _liveThinkSpinnerSlot = null;
      let _liveThinkTimerEl = null;
      let _liveThinkToggle = null;
      let _liveThinkDomId = null;

      // Offscreen measurement div — reused across renders
      let _measureDiv = null;

      function _replyAfterClosedThinking(text) {
        const closeRe = /<\/think(?:ing)?>/gi;
        let match = null;
        let last = null;
        while ((match = closeRe.exec(text || '')) !== null) last = match;
        if (!last) return '';
        return (text || '').slice(last.index + last[0].length).trimStart();
      }

      // Direct render helper for streaming text
      _renderStream = () => {
        let dt = stripToolBlocks(roundText);
        const bodyEl = roundHolder.querySelector('.body');
        const contentEl = _ensureStreamLayout(bodyEl);

        // If thinking was already collapsed in-place, only render the reply portion
        let liveReply = contentEl.querySelector('.live-reply-content');
        if (liveReply) {
          // Extract reply text — handle native <think> tags and non-tag patterns
          const closedThinkReply = _replyAfterClosedThinking(dt);
          const { thinkingBlocks, content: replyText } = closedThinkReply
            ? { thinkingBlocks: [''], content: closedThinkReply }
            : markdownModule.extractThinkingBlocks(dt);
          let replyTrimmed = '';
          if (thinkingBlocks.length) {
            replyTrimmed = (replyText || '').trim();
          } else {
            // Non-tag: check for garbled <think> (reasoning\n<think>reply)
            const _gm = dt.match(/^[\s\S]+?<think(?:ing)?>\s*([\s\S]*?)(?:<\/think(?:ing)?>)?\s*$/i);
            if (_gm && _gm[1].trim()) {
              replyTrimmed = _gm[1].trim();
            } else {
              // Pure non-tag: find reply boundary
              const _rPrefixes = markdownModule.startsWithReasoningPrefix;
              const _rpStarts = ['Hey', 'Hi ', 'Hi!', 'Hello', 'Sure', 'Yes', 'No ', 'No,', 'Yo', 'OK', 'Here', 'Absolutely', 'Of course', 'Great', 'Alright', 'Thanks', 'Welcome', 'Good ', "I'm happy", "I'd be"];
              const _rt = (replyText || '').trimStart();
              if (_rPrefixes(_rt)) {
                const _rLines = _rt.split('\n');
                for (let _ri = 1; _ri < _rLines.length; _ri++) {
                  const _rl = _rLines[_ri].trim();
                  if (!_rl) continue;
                  if (_rpStarts.some(rp => _rl.startsWith(rp))) { replyTrimmed = _rLines.slice(_ri).join('\n'); break; }
                }
                if (!replyTrimmed) {
                  for (const rp of _rpStarts) {
                    const rx = new RegExp('[.!?]\\s*(' + rp.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')');
                    const m = rx.exec(_rt);
                    if (m && m.index > 20) { replyTrimmed = _rt.slice(m.index + 1).trim(); break; }
                  }
                }
              }
            }
          }
          if (replyTrimmed) {
            const replyHtml = markdownModule.mdToHtml(markdownModule.squashOutsideCode(replyTrimmed));
            const prevLen = liveReply._prevTextLen || 0;
            liveReply.innerHTML = replyHtml;
            _fadeNewTokens(liveReply, prevLen);
            liveReply._prevTextLen = liveReply.textContent.length;
            if (window.hljs) liveReply.querySelectorAll('pre code').forEach((b) => window.hljs.highlightElement(b));
          }
          // Reply empty or not — preserve thinking bar, don't fall through to full re-render
          uiModule.scrollHistory();
          return;
        }

        const prevLen = contentEl._prevTextLen || 0;
        // If thinking is still streaming (unclosed <think>), show indicator instead of raw text
        if (markdownModule.hasUnclosedThinkTag && markdownModule.hasUnclosedThinkTag(dt)) {
          const thinkStart = dt.search(/<think(?:ing)?>/i);
          const thinkContent = dt.substring(thinkStart).replace(/<think(?:ing)?>/i, '').trim();
          const lines = thinkContent.split('\n').length;
          // Don't show beforeThink text during streaming — it'll appear in the final render
          // This prevents the "split into two" duplication
          contentEl.innerHTML =
            '<div class="thinking-section"><div class="thinking-header"><div class="thinking-header-left">Thinking' +
            (lines > 1 ? ` (${lines} lines)` : '') + '</div></div></div>';
          contentEl._prevTextLen = 0;
          uiModule.scrollHistory();
          return;
        }
        const html = markdownModule.processWithThinking(markdownModule.squashOutsideCode(dt));

        // Smooth expand only for regular chat text (not thinking/agent blocks)
        const _hasThinking = html.includes('thinking-section');
        const _isAgentRound = roundHolder !== holder;
        if (!_hasThinking && !_isAgentRound) {
          // Render into offscreen clone to measure new height before swapping
          if (!_measureDiv) {
            _measureDiv = document.createElement('div');
            _measureDiv.style.cssText = 'position:absolute;visibility:hidden;pointer-events:none;z-index:-1;';
          }
          _measureDiv.style.width = contentEl.offsetWidth + 'px';
          _measureDiv.className = contentEl.className;
          _measureDiv.innerHTML = html;
          contentEl.parentNode.appendChild(_measureDiv);
          const measuredH = _measureDiv.offsetHeight;
          _measureDiv.remove();
          const curMin = parseFloat(contentEl.style.minHeight) || 0;
          contentEl.style.minHeight = Math.max(curMin, measuredH) + 'px';
        } else {
          contentEl.style.minHeight = '';
        }

        contentEl.innerHTML = html;
        _fadeNewTokens(contentEl, prevLen);
        contentEl._prevTextLen = contentEl.textContent.length;
        if (window.hljs) contentEl.querySelectorAll('pre code').forEach((b) => window.hljs.highlightElement(b));
        uiModule.scrollHistory();
      };

      // Walk text nodes, skip past `prevLen` characters of old text,
      // wrap everything after that in <span class="token-new"> for fade-in
      function _fadeNewTokens(container, prevLen) {
        if (!prevLen) return; // First chunk — skip, whole msg already has entrance anim
        const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
        let charCount = 0;
        const toWrap = [];
        while (walker.nextNode()) {
          const node = walker.currentNode;
          const len = node.textContent.length;
          if (charCount + len <= prevLen) { charCount += len; continue; }
          const splitAt = charCount < prevLen ? prevLen - charCount : 0;
          toWrap.push({ node, splitAt });
          charCount += len;
        }
        for (const { node, splitAt } of toWrap) {
          const parent = node.parentNode;
          if (!parent || parent.closest('pre, .think-content')) continue;
          const target = splitAt > 0 ? node.splitText(splitAt) : node;
          const span = document.createElement('span');
          span.className = 'token-new';
          parent.replaceChild(span, target);
          span.appendChild(target);
        }
      }

      let _nextIsError = false;
      let _streamSawDone = false;

      while (true) {
        const { done, value } = await reader.read();
        _lastReaderActivity = Date.now();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          // Log SSE event types (e.g. "event: error") for debugging
          if (line.startsWith('event: ')) {
            const evtType = line.slice(7).trim();
            if (evtType === 'error') _nextIsError = true;
            continue;
          }
          if (line.startsWith('data: ')) {
            const data = line.slice(6);

            // (thinking spinner removal is handled in agent_step / tool_start / content handlers)

            // Background detection: are we on a different session?
            const _isBg = (sessionModule.getCurrentSessionId() !== streamSessionId);

            // On first transition to background, store state in map
            if (_isBg && !_backgroundStreams.has(streamSessionId)) {
              _backgroundStreams.set(streamSessionId, {
                status: 'running',
                accumulated: accumulated,
                sourcesHtml: _sourcesHtml,
                findingsData: null,
                abortCtrl: currentAbort,
                query: streamQuery,
                metrics: null,
              });
              if (sessionModule && sessionModule.markStreaming) {
                sessionModule.markStreaming(streamSessionId);
              }
            }

            if (data === '[DONE]') {
              _streamSawDone = true;
              // Always update background map if entry exists (even if user switched back)
              var bgDone = _backgroundStreams.get(streamSessionId);
              if (bgDone) {
                bgDone.status = 'completed';
                bgDone.accumulated = accumulated;
                if (_isBg) {
                  try {
                    _notifyStreamComplete(streamSessionId, streamQuery);
                    _insertStreamDoneToast(streamSessionId, streamQuery);
                  } catch (toastErr) {
                    console.warn('[bg-stream] Toast/notification error:', toastErr);
                  }
                }
                // CRITICAL: always mark stream complete for the sidebar dot
                try {
                  if (sessionModule && sessionModule.markStreamComplete) {
                    sessionModule.markStreamComplete(streamSessionId);
                  }
                } catch (dotErr) {
                  console.warn('[bg-stream] markStreamComplete error:', dotErr);
                }
                // Don't do foreground final render — the checkBackgroundStream poll
                // will detect 'completed' and reload history cleanly
                break;
              }
              // Force-close thinking if still open (model never output boundary)
              if (isThinking) {
                isThinking = false;
                cancelAnimationFrame(_thinkTimerRAF);
                var _elapsedDone = thinkingStartTime ? ((Date.now() - thinkingStartTime) / 1000).toFixed(1) : null;
                if (_elapsedDone) {
                  accumulated = accumulated.replace(/<think>/i, '<think time="' + _elapsedDone + '">');
                  roundText = roundText.replace(/<think>/i, '<think time="' + _elapsedDone + '">');
                }
                if (_liveThinkHeader) _liveThinkHeader.textContent = 'View thinking process';
                if (_liveThinkSpinnerSlot) _liveThinkSpinnerSlot.remove();
                if (_liveThinkTimerEl && _elapsedDone) {
                  _liveThinkTimerEl.textContent = _elapsedDone + 's';
                  _liveThinkTimerEl.style.marginLeft = 'auto';
                  _liveThinkTimerEl.style.marginRight = '5px';
                  var _hdrDone = _liveThinkTimerEl.closest('.thinking-header');
                  // Keep the chevron furthest right with the timer to its left
                  // (match the live + final-render layout) — insert before the
                  // toggle rather than appending (which would land after it).
                  if (_hdrDone) {
                    if (_liveThinkToggle && _liveThinkToggle.parentElement === _hdrDone)
                      _hdrDone.insertBefore(_liveThinkTimerEl, _liveThinkToggle);
                    else _hdrDone.appendChild(_liveThinkTimerEl);
                  }
                }
                // Assign stable IDs
                var _thinkIdDone = 'think-' + Date.now();
                var _liveHdrDone = _liveThinkSection && _liveThinkSection.querySelector('.thinking-header');
                if (_liveHdrDone) _liveHdrDone.dataset.thinkingId = _thinkIdDone;
                if (_liveThinkContent) _liveThinkContent.id = _thinkIdDone;
                if (_liveThinkToggle) _liveThinkToggle.id = _thinkIdDone + '-toggle';
                // Create live-reply container so final render preserves thinking bar
                var _streamElDone = _liveThinkSection ? _liveThinkSection.parentElement : roundHolder.querySelector('.stream-content');
                if (!_streamElDone) _streamElDone = roundHolder.querySelector('.body');
                if (_streamElDone && !_streamElDone.querySelector('.live-reply-content')) {
                  var _replyElDone = document.createElement('div');
                  _replyElDone.className = 'live-reply-content';
                  _streamElDone.appendChild(_replyElDone);
                }
              }
              // Normal foreground completion — metrics will be displayed in the final render block below
              break;
            }
            try {
              const json = JSON.parse(data);
              // Handle SSE error events (e.g. HTTP 404 from provider)
              if (_nextIsError || json.status >= 400) {
                _nextIsError = false;
                const errMsg = json.text || json.error?.message || `Error ${json.status || 'unknown'}`;
                console.error('Stream error:', errMsg);
                if (spinner && spinner.element) spinner.destroy();
                typewriterInto(roundHolder.querySelector('.body'), errMsg);
                break;
              }
              if (json.delta || json.type === 'tool_start' || json.type === 'agent_step' || json.type === 'doc_stream_delta') {
                clearProcessingProbe();
              }
              if (json.delta) {
                _cancelThinkingTimer();
                _removeThinkingSpinner();
                // Text arrived after tools — connect thread line to this bubble
                const _threadAbove = roundHolder?.previousElementSibling;
                if (_threadAbove && _threadAbove.classList.contains('agent-thread') && !_threadAbove.classList.contains('has-bottom')) {
                  _threadAbove.classList.add('has-bottom');
                }
                // VLLM reasoning tokens: wrap in <think> tags for the thinking UI.
                // Stateful open/close (not a whole-message substring check) so each round
                // of a multi-round agent response gets its own <think>…</think> — otherwise
                // only round 1 is wrapped and rounds 2+ reasoning leaks into the answer.
                let _delta = json.delta;
                if (json.thinking) {
                  if (!_thinkOpen) { _delta = '<think>' + _delta; _thinkOpen = true; }
                } else if (_thinkOpen) {
                  _delta = '</think>' + _delta; _thinkOpen = false;
                }
                const wasEmpty = !accumulated;
                accumulated += _delta;
                roundText += _delta;
                currentAccumulated = accumulated; // Update global tracker
                // First token arrived — switch stop button from processing to streaming
                if (wasEmpty && submitBtn && !_isBg) {
                  submitBtn.dataset.phase = 'receiving';
                }

                // Update background map if running in background
                if (_isBg) {
                  var bgEntry = _backgroundStreams.get(streamSessionId);
                  if (bgEntry) bgEntry.accumulated = accumulated;
                  continue; // Skip all DOM writes
                }

                // --- Text-fence doc streaming (for models that don't use native tool calls) ---
                if (!_docFenceOpened && documentModule && roundText.includes('```create_document\n')) {
                  const fenceIdx = roundText.indexOf('```create_document\n');
                  const afterFence = roundText.slice(fenceIdx + '```create_document\n'.length);
                  const fenceLines = afterFence.split('\n');
                  if (fenceLines.length >= 1 && fenceLines[0].trim()) {
                    _docFenceOpened = true;
                    const title = fenceLines[0].trim();
                    // Keep in sync with backend _KNOWN_LANGS in src/tool_implementations.py
                    const knownLangs = ['python','py','javascript','js','typescript','ts','html','css','json','yaml','bash','sql','rust','go','java','c','cpp','markdown','text','plain','ruby','swift','kotlin','php','email','csv','xml','toml','ini'];
                    const isLang = fenceLines.length >= 2 && knownLangs.includes(fenceLines[1].trim().toLowerCase());
                    const lang = isLang ? fenceLines[1].trim() : '';
                    _docFenceContentStart = fenceIdx + '```create_document\n'.length + title.length + 1 + (isLang ? fenceLines[1].length + 1 : 0);
                    documentModule.streamDocOpen(title, lang);
                  }
                }
                if (_docFenceOpened && _docFenceContentStart > 0 && documentModule) {
                  let raw = roundText.slice(_docFenceContentStart);
                  const closeIdx = raw.indexOf('\n```');
                  if (closeIdx >= 0) raw = raw.slice(0, closeIdx);
                  documentModule.streamDocDelta(raw);
                }

                // Detect thinking-in-progress:
                // 1. Normal: <think>...no closing tag yet
                // 2. Malformed: <think></think>\n...text but no second </think> yet
                // 3. Qwen3.5: "Thinking Process:" without <think> tags
                let hasUnclosedThink = markdownModule.hasUnclosedThinkTag(roundText);
                // Detect non-tag thinking patterns: "Thinking:", "Thinking Process:", Gemma-style reasoning
                // These patterns don't use <think> tags, so we simulate unclosed thinking during streaming
                const _replyPrefixes = ['Hey', 'Hi ', 'Hi!', 'Hello', 'Sure', 'Yes', 'No ', 'No,', 'Yo', 'OK', 'Here', 'Absolutely', 'Of course', 'Great', 'Alright', 'Thanks', 'Welcome', 'Good ', "I'm happy", "I'd be"];
                if (!hasUnclosedThink && !roundText.includes('<think')) {
                  const _trimmedRT = roundText.trimStart();
                  const _isReasoning = markdownModule.startsWithReasoningPrefix(_trimmedRT);
                  if (_isReasoning) {
                    // Check if we can see a reply boundary yet (newline then reply pattern)
                    const _lines = _trimmedRT.split('\n');
                    let _replyFound = false;
                    for (let li = 1; li < _lines.length; li++) {
                      const _l = _lines[li].trim();
                      if (!_l) continue;
                      if (_replyPrefixes.some(rp => _l.startsWith(rp))) {
                        _replyFound = true;
                        break;
                      }
                    }
                    if (!_replyFound) {
                      // Also check within-line: "reasoning text.Reply text"
                      const _inlineReply = _replyPrefixes.some(rp => {
                        const rx = new RegExp('[.!?]\\s*' + rp.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
                        const m = rx.exec(_trimmedRT);
                        return m && m.index > 20;
                      });
                      if (!_inlineReply) hasUnclosedThink = true;
                    }
                  }
                }
                if (!hasUnclosedThink && /^<think(?:ing)?>\s*<\/think(?:ing)?>/i.test(roundText)) {
                  // Empty <think></think> — the model likely put thinking outside the tags
                  const afterEmpty = roundText.replace(/^<think(?:ing)?>\s*<\/think(?:ing)?>/i, '').trim();
                  const closeTags = (afterEmpty.match(/<\/think(?:ing)?>/gi) || []).length;
                  if (closeTags === 0 && afterEmpty.length > 0) {
                    hasUnclosedThink = true; // still waiting for real closing tag
                  }
                }
                // Detect false close: <think>short</think> where real thinking follows untagged
                // Only applies when there's a second </think> later (model leaked thinking outside tags)
                // Do NOT trigger if the text after </think> contains tool calls (that's real content)
                if (!hasUnclosedThink && isThinking) {
                  const _thinkMatch = roundText.match(/<think(?:ing)?>([\s\S]*?)<\/think(?:ing)?>/i);
                  const _thinkLen = _thinkMatch ? _thinkMatch[1].trim().length : 0;
                  if (_thinkLen < 20) {
                    const _afterClose = roundText.replace(/<think(?:ing)?>([\s\S]*?)<\/think(?:ing)?>/i, '').trim();
                    // Only keep waiting if there's trailing text that looks like thinking (not tool calls)
                    const _hasToolCall = /```(?:bash|python|web_search|read_file|write_file|create_document|edit_document|manage_|generate_image)/i.test(_afterClose);
                    const _hasOrphanClose = /<\/think(?:ing)?>/i.test(_afterClose);
                    if (!_hasToolCall && (_hasOrphanClose || (Date.now() - thinkingStartTime) < 500)) {
                      hasUnclosedThink = true; // keep waiting for real </think>
                    }
                  }
                }

                if (hasUnclosedThink && !isThinking) {
                  isThinking = true;
                  thinkingStartTime = Date.now();
                  if (spinner && spinner.element) spinner.destroy();

                  // Create a live thinking box — starts expanded so content streams visibly
                  var thinkBody = roundHolder.querySelector('.body');
                  var thinkContent = _ensureStreamLayout(thinkBody);
                  thinkContent.style.minHeight = '';
                  _liveThinkDomId = 'live-think-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
                  thinkContent.innerHTML = `
                    <div class="thinking-section">
                      <div class="thinking-header" data-thinking-id="${_liveThinkDomId}">
                        <div class="thinking-header-left"><span class="live-think-header-text">Thinking\u2026</span></div>
                        <span class="live-think-spinner-slot" style="flex-shrink:0;margin-left:auto;"></span>
                        <span class="live-think-timer" style="font-size:11px;opacity:0.4;font-variant-numeric:tabular-nums;margin-left:6px;margin-right:5px;"></span>
                        <span class="thinking-toggle live-think-toggle" id="${_liveThinkDomId}-toggle"></span>
                      </div>
                      <div class="thinking-content" id="${_liveThinkDomId}">
                        <div class="thinking-content-inner live-think-inner"></div>
                      </div>
                    </div>`;
                  _liveThinkSection = thinkContent.querySelector('.thinking-section');
                  _liveThinkContent = thinkContent.querySelector('.thinking-content');
                  _liveThinkInner = thinkContent.querySelector('.live-think-inner');
                  _liveThinkHeader = thinkContent.querySelector('.live-think-header-text');
                  _liveThinkSpinnerSlot = thinkContent.querySelector('.live-think-spinner-slot');
                  _liveThinkTimerEl = thinkContent.querySelector('.live-think-timer');
                  _liveThinkToggle = thinkContent.querySelector('.live-think-toggle');
                  // Live timer
                  var _thinkTimerStart = Date.now();
                  var _thinkTimerRAF = 0;
                  function _tickThinkTimer() {
                    if (!_liveThinkTimerEl || !_liveThinkTimerEl.isConnected) return;
                    var s = ((Date.now() - _thinkTimerStart) / 1000).toFixed(1);
                    _liveThinkTimerEl.textContent = s + 's';
                    _thinkTimerRAF = requestAnimationFrame(_tickThinkTimer);
                  }
                  _thinkTimerRAF = requestAnimationFrame(_tickThinkTimer);
                  // Whirlpool spinner
                  if (_liveThinkSpinnerSlot) {
                    var _wp = spinnerModule.createWhirlpool(12);
                    _wp.element.style.margin = '0';
                    _wp.element.style.width = '12px';
                    _wp.element.style.height = '12px';
                    _wp.element.style.transform = 'translateY(-1px)'; // align the whirlpool with the header text
                    _liveThinkSpinnerSlot.appendChild(_wp.element);
                  }
                } else if (hasUnclosedThink && isThinking) {
                  if (_liveThinkInner) {
                    // Extract raw thinking text (strip all <think>/<thinking> open/close tags and prefixes)
                    var thinkText = roundText.replace(/<\/?think(?:ing)?>/gi, '');
                    thinkText = thinkText.replace(/^\s*Thinking(?:\s+Process)?:\s*/i, '');
                    _liveThinkInner.innerHTML = markdownModule.mdToHtml(thinkText);
                    // Keep thinking box scrolled to bottom
                    var thinkBox = _liveThinkInner.closest('.thinking-content');
                    if (thinkBox) thinkBox.scrollTop = thinkBox.scrollHeight;
                  }
                  uiModule.scrollHistory();
                  continue;
                } else if (!hasUnclosedThink && isThinking) {
                  isThinking = false;
                  var _thinkTextLen = _liveThinkInner ? _liveThinkInner.textContent.trim().length : 0;

                  // If thinking was trivially short (< 20 chars), remove the section entirely
                  // Models sometimes emit <think>The</think> or similar noise
                  if (_thinkTextLen < 20 && _liveThinkSection) {
                    _liveThinkSection.remove();
                    _liveThinkSection = null;
                    _liveThinkContent = null;
                    _liveThinkInner = null;
                    _liveThinkHeader = null;
                    _liveThinkSpinnerSlot = null;
                    _liveThinkTimerEl = null;
                    _liveThinkToggle = null;
                    _liveThinkDomId = null;
                    // Fall through to normal streaming
                    if (spinner && spinner.element) spinner.destroy();
                    _renderStream();
                    _scheduleThinkingSpinner();
                    continue;
                  }

                  // Thinking ended — smooth transition: update header, pause, then collapse
                  // Stop live timer and spinner
                  cancelAnimationFrame(_thinkTimerRAF);
                  var elapsed = thinkingStartTime ? ((Date.now() - thinkingStartTime) / 1000).toFixed(1) : null;
                  // Embed thinking time in the <think> tag for persistence on reload
                  if (elapsed) {
                    accumulated = accumulated.replace(/<think>/i, '<think time="' + elapsed + '">');
                    roundText = roundText.replace(/<think>/i, '<think time="' + elapsed + '">');
                  }
                  if (_liveThinkHeader) _liveThinkHeader.textContent = 'View thinking process';
                  if (_liveThinkSpinnerSlot) _liveThinkSpinnerSlot.remove();
                  // Move timer to right side of header
                  if (_liveThinkTimerEl && elapsed) {
                    _liveThinkTimerEl.textContent = elapsed + 's';
                    _liveThinkTimerEl.style.marginLeft = 'auto';
                    _liveThinkTimerEl.style.marginRight = '5px';
                    var _hdrRow = _liveThinkTimerEl.closest('.thinking-header');
                    // Chevron furthest right, timer to its left — insert before
                    // the toggle (appending would put the timer after it).
                    if (_hdrRow) {
                      if (_liveThinkToggle && _liveThinkToggle.parentElement === _hdrRow)
                        _hdrRow.insertBefore(_liveThinkTimerEl, _liveThinkToggle);
                      else _hdrRow.appendChild(_liveThinkTimerEl);
                    }
                  }

                  // Assign stable IDs (for click-toggle handler in markdown.js)
                  var _thinkId = 'think-' + Date.now();
                  var _liveHdr = _liveThinkSection && _liveThinkSection.querySelector('.thinking-header');
                  if (_liveHdr) _liveHdr.dataset.thinkingId = _thinkId;
                  if (_liveThinkContent) _liveThinkContent.id = _thinkId;
                  if (_liveThinkToggle) _liveThinkToggle.id = _thinkId + '-toggle';

                  // Append a container for the reply text that follows thinking
                  var _streamEl = _liveThinkSection ? _liveThinkSection.parentElement : roundHolder.querySelector('.stream-content');
                  if (!_streamEl) _streamEl = roundHolder.querySelector('.body');
                  if (_streamEl) {
                    var _replyEl = document.createElement('div');
                    _replyEl.className = 'live-reply-content';
                    _streamEl.appendChild(_replyEl);
                  }

                  // Render any reply text that arrived with the closing </think> token
                  _renderStream();
                } else {
                  // Normal streaming
                  if (spinner && spinner.element) spinner.destroy();
                  _renderStream();
                  _scheduleThinkingSpinner();
                  // Feed streaming TTS with accumulated text
                  if (streamingTTS) window.aiTTSManager.streamingUpdate(roundText);
                }
              } else if (json.type === 'research_progress') {
                if (_isBg) continue; // Skip DOM updates in background
                _researchingStreamIds.add(streamSessionId);
                // Highlight research button while running
                var _rToggle = document.getElementById('research-toggle-btn');
                if (_rToggle) _rToggle.classList.add('research-running');
                // Request notification permission on first research event
                if ('Notification' in window && Notification.permission === 'default') {
                  Notification.requestPermission();
                }
                // Mark session as researching in sidebar
                var _rSid = sessionModule && sessionModule.getCurrentSessionId();
                if (_rSid && sessionModule.markResearching) sessionModule.markResearching(_rSid);
                const rp = json.data;
                // Start research timer + synapse on first progress event
                if (!_researchTimerEl && spinner && spinner.element) {
                  _researchStartTime = rp.started_at ? rp.started_at * 1000 : Date.now();
                  _researchAvgDuration = rp.avg_duration || null;
                  _researchTimerEl = document.createElement('div');
                  _researchTimerEl.className = 'research-timer';
                  // Styles in .research-timer CSS class
                  spinner.element.parentNode.insertBefore(_researchTimerEl, spinner.element.nextSibling);
                  _researchTimerInterval = setInterval(() => {
                    if (!_researchTimerEl) return;
                    var elapsed = Math.floor((Date.now() - _researchStartTime) / 1000);
                    var mm = String(Math.floor(elapsed / 60)).padStart(2, '0');
                    var ss = String(elapsed % 60).padStart(2, '0');
                    var txt = mm + ':' + ss;
                    if (_researchAvgDuration) {
                      var avgM = String(Math.floor(_researchAvgDuration / 60)).padStart(2, '0');
                      var avgS = String(Math.round(_researchAvgDuration % 60)).padStart(2, '0');
                      txt += ' / avg ' + avgM + ':' + avgS;
                    }
                    _researchTimerEl.textContent = txt;
                  }, 1000);
                  // Synapse visualization — insert right above the timer so
                  // it sits between the spinner message and the timer line.
                  try {
                    _researchSynapse = createResearchSynapse(spinner.element.parentNode, {
                      query: holder._researchQuery || rp.query || '',
                      startedAt: _researchStartTime,
                    });
                    // Move it to live between spinner and timer
                    if (_researchSynapse.element && _researchTimerEl) {
                      spinner.element.parentNode.insertBefore(_researchSynapse.element, _researchTimerEl);
                    }
                  } catch (e) { console.warn('synapse init failed', e); }
                }
                if (_researchSynapse) {
                  _researchSynapse.setPhase(rp.phase, rp);
                  if (typeof rp.round === 'number') _researchSynapse.setRound(rp.round);
                  if (typeof rp.total_sources === 'number') _researchSynapse.setSourceCount(rp.total_sources);
                  if (rp.phase === 'error') _researchSynapse.complete();
                }
                if (spinner && spinner.element) {
                  if (rp.phase === 'probing') {
                    spinner.updateMessage(`Verifying model: ${rp.model || '?'}`);
                  } else if (rp.phase === 'planning') {
                    spinner.updateMessage('Analyzing question & planning research strategy');
                  } else if (rp.phase === 'searching') {
                    const q = rp.queries ? `${rp.queries} queries` : '';
                    const s = rp.total_sources ? ` · ${rp.total_sources} sources` : '';
                    spinner.updateMessage(`Round ${rp.round || '?'}: Searching${q ? ' (' + q + ')' : ''}${s}`);
                  } else if (rp.phase === 'reading') {
                    spinner.updateMessage(rp.title ? `Reading: ${rp.title}` : `Round ${rp.round || '?'}: Reading ${rp.new_sources || ''} pages · ${rp.total_sources || 0} sources total`);
                  } else if (rp.phase === 'analyzing') {
                    spinner.updateMessage(`Round ${rp.round || '?'}: Analyzing ${rp.total_findings || 0} findings`);
                  } else if (rp.phase === 'writing') {
                    spinner.updateMessage(`Writing report · ${rp.total_sources || 0} sources`);
                  } else if (rp.phase === 'error') {
                    spinner.updateMessage(rp.message || 'Search error');
                  }
                }
              } else if (json.type === 'research_sources') {
                if (_isBg) {
                  // Store sources HTML in background map
                  if (json.data && json.data.length > 0) {
                    _sourcesHtml = _buildSourcesBox(json.data, 'research');
                    var bgE = _backgroundStreams.get(streamSessionId);
                    if (bgE) bgE.sourcesHtml = _sourcesHtml;
                  }
                  // Clear researching indicator for this background session
                  if (sessionModule && sessionModule.clearResearching) sessionModule.clearResearching(streamSessionId);
                  continue;
                }
                // Research done — clean up timer, show sources box, then spinner for LLM response
                _clearResearchTimer();
                holder._researchSources = json.data;
                var _rSid2 = sessionModule && sessionModule.getCurrentSessionId();
                if (_rSid2 && sessionModule.clearResearching) sessionModule.clearResearching(_rSid2);
                if (json.data && json.data.length > 0) {
                  _sourcesData = json.data; _sourcesType = 'research';
                  _sourcesHtml = _buildSourcesBox(json.data, 'research');
                }
                if (document.hidden) {
                  _notifyResearchComplete(_rSid2 || '', holder._researchQuery || '');
                }
              } else if (json.type === 'research_findings') {
                if (_isBg) {
                  var bgEf = _backgroundStreams.get(streamSessionId);
                  if (bgEf) bgEf.findingsData = json.data;
                  continue;
                }
                if (json.data && json.data.length > 0) {
                  _findingsData = json.data;
                }
              } else if (json.type === 'research_done') {
                // Research complete — reload session to show the persisted report
                _clearResearchTimer();
                if (sessionModule && sessionModule.clearResearching) {
                  sessionModule.clearResearching(streamSessionId);
                }
                _researchingStreamIds.delete(streamSessionId);
                // Small delay then reload session history which includes the full report
                setTimeout(async () => {
                  // Don't yank the user back to this chat if they've navigated
                  // away (e.g. started a new chat) while research finished —
                  // just refresh the sidebar so the report shows when they return.
                  if (sessionModule.getCurrentSessionId && sessionModule.getCurrentSessionId() === streamSessionId) {
                    await sessionModule.selectSession(streamSessionId);
                  } else {
                    await sessionModule.loadSessions();
                  }
                }, 500);
                continue;
              } else if (json.type === 'web_sources') {
                if (_isBg) {
                  if (json.data && json.data.length > 0) {
                    _sourcesHtml = _buildSourcesBox(json.data, 'web');
                    var bgE2 = _backgroundStreams.get(streamSessionId);
                    if (bgE2) bgE2.sourcesHtml = _sourcesHtml;
                  }
                  continue;
                }
                // Web search done — store sources for final render (don't render mid-stream)
                holder._webSources = json.data;
                if (json.data && json.data.length > 0) {
                  _sourcesData = json.data; _sourcesType = 'web';
                  _sourcesHtml = _buildSourcesBox(json.data, 'web');
                }
              } else if (json.type === 'model_fallback') {
                // Model went offline — switched to fallback
                var _fbData = json.data || {};
                uiModule.showToast(
                  `Model ${_fbData.old_model || '?'} offline — switched to ${_fbData.new_model || '?'}`,
                  5000
                );
                // Update the model picker to reflect the new model
                if (sessionModule && sessionModule.updateModelPicker) {
                  sessionModule.updateModelPicker();
                }
                continue;
              } else if (json.type === 'model_info') {
                // Update role label with model name as soon as we know it
                if (!_isBg && holder) {
                  const roleEl = holder.querySelector('.role');
                  if (roleEl) {
                    const tsSpan = roleEl.querySelector('.role-timestamp');
                    var _modelLabel = _shortModel(json.model);
                    if (json.suffix) {
                      _modelLabel += ' (' + json.suffix + ')';
                      holder._roleSuffix = json.suffix;
                    }
                    // Prepend character name if sent by server or set locally
                    var _charName = json.character_name || (presetsModule.getCharacterName ? presetsModule.getCharacterName() : '');
                    if (_charName) {
                      _modelLabel = _charName;
                      holder._characterName = _charName;
                    }
                    roleEl.textContent = _modelLabel + ' ';
                    _applyModelColor(roleEl, json.model);
                    if (tsSpan) roleEl.appendChild(tsSpan);
                  }
                }
              } else if (json.type === 'fallback') {
                // The selected model failed and another provider answered. Make
                // it visible so a misconfigured provider is never silently
                // masked under the selected model's name.
                if (!_isBg) {
                  var _selM = _shortModel(json.selected_model || '');
                  var _ansM = _shortModel(json.answered_by || '');
                  uiModule.showToast('⚠ ' + _selM + ' failed — answered by ' + _ansM, 6000);
                  if (holder) {
                    var _rEl = holder.querySelector('.role');
                    if (_rEl) {
                      var _tsS = _rEl.querySelector('.role-timestamp');
                      _rEl.textContent = _ansM + ' (fallback) ';
                      _rEl.title = (json.selected_model || '') + ' failed' +
                        (json.reason ? ': ' + json.reason : '') + ' — answered by ' + (json.answered_by || '');
                      _applyModelColor(_rEl, json.answered_by);
                      if (_tsS) _rEl.appendChild(_tsS);
                    }
                  }
                }
              } else if (json.type === 'attachments') {
                if (_isBg) continue;
                // Update user bubble — replace file chips with image previews
                const _ub = document.querySelector('#chat-history .msg-user:last-of-type');
                if (_ub) {
                  const _aw = _ub.querySelector('.attach-cards');
                  if (_aw) {
                    for (const _att of json.data) {
                      const _isImg = (_att.mime || '').startsWith('image/') || /\.(png|jpg|jpeg|gif|webp|svg|bmp)$/i.test(_att.name || '');
                      if (_isImg && _att.id) {
                        // Skip if we already have a preview for this file id —
                        // on a regenerate the original user bubble keeps its
                        // photo and the backend re-emits the attachment event
                        // for the same id; without this guard we'd append a
                        // duplicate (which visually pushes the real photo off).
                        const _existingPreview = _aw.querySelector('[data-file-id="' + _att.id + '"]');
                        if (_existingPreview) {
                          if (_att.vision_model && !_existingPreview.querySelector('.attach-vision-model')) {
                            const _vl = document.createElement('div');
                            _vl.className = 'attach-vision-model';
                            _vl.textContent = 'Vision: ' + String(_att.vision_model).split('/').pop();
                            const _name = _existingPreview.querySelector('.attach-image-name');
                            if (_name) _existingPreview.insertBefore(_vl, _name);
                            else _existingPreview.appendChild(_vl);
                          }
                          continue;
                        }
                        const _card = _aw.querySelector('.attach-card[data-name="' + (_att.name || '').replace(/"/g, '\\"') + '"]');
                        const _iw = document.createElement('div');
                        _iw.className = 'attach-image-preview';
                        _iw.dataset.fileId = _att.id;
                        _iw.style.cursor = 'pointer';
                        _iw.onclick = () => window.open(API_BASE + '/api/upload/' + _att.id, '_blank');
                        const _im = document.createElement('img');
                        _im.src = API_BASE + '/api/upload/' + _att.id;
                        _im.alt = _att.name || 'Image';
                        _im.style.cssText = 'max-width:300px;max-height:200px;border-radius:6px;display:block;';
                        _iw.appendChild(_im);
                        if (_att.vision_model) {
                          const _vl = document.createElement('div');
                          _vl.className = 'attach-vision-model';
                          _vl.textContent = 'Vision: ' + String(_att.vision_model).split('/').pop();
                          _iw.appendChild(_vl);
                        }
                        if (_att.name) {
                          const _nm = document.createElement('div');
                          _nm.className = 'attach-image-name';
                          _nm.textContent = _att.name;
                          _iw.appendChild(_nm);
                        }
                        if (_card) _card.replaceWith(_iw); else _aw.appendChild(_iw);
                      } else {
                        const _card = _aw.querySelector('.attach-card[data-name="' + (_att.name || '').replace(/"/g, '\\"') + '"]');
                        if (_card && _att.id) {
                          _card.dataset.fileId = _att.id;
                          _card.style.cursor = 'pointer';
                          _card.onclick = () => window.open(API_BASE + '/api/upload/' + _att.id, '_blank');
                        }
                      }
                    }
                  }
                  // Caption / OCR text is no longer rendered as an inline
                  // collapsible on the user bubble — the user can view/edit
                  // it via the "Caption" button on the photo thumbnail.
                }
              } else if (json.type === 'rag_sources') {
                if (_isBg) continue;
                holder._ragSources = json.data;
              } else if (json.type === 'memories_used') {
                if (_isBg) continue;
                holder._memoriesUsed = json.data;
              } else if (json.type === 'compacted') {
                if (!_isBg) {
                  uiModule.showToast('Context compacted — older messages summarized');
                }
              } else if (json.type === 'metrics') {
                metrics = json.data;
                if (_isBg) {
                  var bgM = _backgroundStreams.get(streamSessionId);
                  if (bgM) bgM.metrics = json.data;
                  continue;
                }

              } else if (json.type === 'message_saved') {
                // Wire the persisted DB id onto the just-streamed bubble so it
                // can be edited/deleted immediately, without reloading the chat.
                if (_isBg) continue;
                if (currentHolder && json.id) currentHolder.dataset.dbId = json.id;

              } else if (json.type === 'tool_start') {
                if (_isBg) continue;
                _cancelThinkingTimer();
                _removeThinkingSpinner();
                // Force-close thinking if still open — tools are real content, not thinking
                if (isThinking) {
                  isThinking = false;
                  cancelAnimationFrame(_thinkTimerRAF);
                  var _elapsed2 = thinkingStartTime ? ((Date.now() - thinkingStartTime) / 1000).toFixed(1) : null;
                  if (_liveThinkHeader) _liveThinkHeader.textContent = 'View thinking process';
                  if (_liveThinkTimerEl) _liveThinkTimerEl.textContent = _elapsed2 ? _elapsed2 + 's' : '';
                  if (_liveThinkSpinnerSlot) _liveThinkSpinnerSlot.remove();
                  // Assign stable IDs
                  var _thinkId2 = 'think-' + Date.now();
                  var _liveHdr2 = _liveThinkSection && _liveThinkSection.querySelector('.thinking-header');
                  if (_liveHdr2) _liveHdr2.dataset.thinkingId = _thinkId2;
                  if (_liveThinkContent) _liveThinkContent.id = _thinkId2;
                  if (_liveThinkToggle) _liveThinkToggle.id = _thinkId2 + '-toggle';
                }
                _renderStream();
                // --- Finalize current text bubble (only once per round) ---
                if (!roundFinalized) {
                  roundFinalized = true;
                  if (spinner && spinner.element) spinner.destroy();
                  const dt = stripToolBlocks(roundText);
                  if (dt.trim()) {
                    var _body3 = roundHolder.querySelector('.body');
                    var _contentEl3 = _ensureStreamLayout(_body3);
                    _contentEl3.style.minHeight = '';  // clear streaming inflate
                    _contentEl3.innerHTML = markdownModule.processWithThinking(markdownModule.squashOutsideCode(dt));
                    if (window.hljs) roundHolder.querySelectorAll('pre code').forEach((b) => window.hljs.highlightElement(b));
                  } else {
                    roundHolder.style.display = 'none';
                  }
                }

                // Track tool name for contextual spinner labels
                _lastToolName = json.tool || '';

                // --- Thread timeline: group tools in a thread container ---
                const cmd = json.command || '';
                const chatBox = document.getElementById('chat-history');
                // Find existing thread to append to — check last few children
                // (agent_step may insert an empty msg-ai between tool rounds)
                let threadWrap = null;
                for (let ci = chatBox.children.length - 1; ci >= Math.max(0, chatBox.children.length - 5); ci--) {
                  const child = chatBox.children[ci];
                  if (child.classList.contains('agent-thread')) {
                    threadWrap = child;
                    break;
                  }
                  // Skip hidden (empty) bubbles and thinking spinners
                  if (child.style.display === 'none' || child.classList.contains('agent-thinking-dots')) continue;
                  // Stop if we hit a visible message bubble (has real content between tools)
                  if (child.classList.contains('msg')) break;
                }
                if (threadWrap) {
                  // Continuing an existing thread — remove has-bottom (agent_step may have set it
                  // expecting text, but we got more tools instead)
                  threadWrap.classList.remove('has-bottom');
                } else {
                  threadWrap = document.createElement('div');
                  threadWrap.className = 'agent-thread';
                  // Extend line up to connect to chat bubble above (if there is one)
                  const _prevSib = chatBox.lastElementChild;
                  const _hasBubbleAbove = _prevSib && (_prevSib.classList.contains('msg') && _prevSib.style.display !== 'none');
                  const _hasThreadAbove = _prevSib && _prevSib.classList.contains('agent-thread');
                  if (_hasBubbleAbove || _hasThreadAbove || (roundText.trim() && roundHolder && roundHolder.style.display !== 'none')) {
                    threadWrap.classList.add('has-top');
                  }
                  chatBox.appendChild(threadWrap);
                }
                threadWrap.classList.add('streaming');
                const toolLabel = _toolLabels[json.tool.toLowerCase()] || json.tool;
                const node = document.createElement('div')
                node.className = 'agent-thread-node running';
                const cmdHtml = cmd ? `<pre class="agent-thread-cmd">${esc(cmd)}</pre>` : '';
                node.innerHTML = `<div class="agent-thread-dot"></div><div class="agent-thread-header"><span class="agent-thread-icon">\u25B6</span><span class="agent-thread-tool">${toolLabel}</span><span class="agent-thread-wave">▁▂▃</span></div><div class="agent-thread-content">${cmdHtml}</div>`;
                // Expand/collapse via delegated click handler (init at module bottom).
                threadWrap.appendChild(node);
                currentToolBubble = node;
                // Animate the wave
                const waveEl = node.querySelector('.agent-thread-wave');
                if (waveEl) {
                  const waveFrames = ['▁▂▃', '▂▃▄', '▃▄▅', '▄▅▆', '▅▆▇', '▆▅▄', '▅▄▃', '▄▃▂'];
                  let waveIdx = 0;
                  node._waveInterval = setInterval(() => {
                    waveIdx = (waveIdx + 1) % waveFrames.length;
                    waveEl.textContent = waveFrames[waveIdx];
                  }, 100);
                }
                // Smooth per-second "cooking" timer — ticks every second (not
                // just on the 2s backend heartbeat) so a long-running tool
                // always shows visible motion and never reads as frozen.
                node._startTime = Date.now();
                node._elapsedTicker = setInterval(() => {
                  const hdr2 = node.querySelector('.agent-thread-header');
                  if (!hdr2) return;
                  let el2 = hdr2.querySelector('.agent-thread-elapsed');
                  if (!el2) {
                    el2 = document.createElement('span');
                    el2.className = 'agent-thread-elapsed';
                    // Sits on the LEFT, right after the icon.
                    const icon = hdr2.querySelector('.agent-thread-icon');
                    if (icon && icon.nextSibling) hdr2.insertBefore(el2, icon.nextSibling);
                    else hdr2.appendChild(el2);
                  }
                  const s = (Date.now() - node._startTime) / 1000;
                  // Hundredths so it visibly counts sub-second (1.00, 1.05, …).
                  el2.textContent = s < 60 ? `${s.toFixed(2)}s` : `${Math.floor(s / 60)}m ${(s % 60).toFixed(2).padStart(5, '0')}s`;
                }, 50);
                uiModule.scrollHistory();

              } else if (json.type === 'tool_progress') {
                // Long-running subprocess (bash, python) is still in
                // flight — refresh the running tool card with the
                // elapsed-time + tail of its stdout/stderr so the
                // user doesn't stare at a blind "Running…" spinner.
                if (_isBg) continue;
                if (!currentToolBubble) continue;
                // The per-second ticker (started in tool_start) owns the
                // elapsed display; here we just surface the live output tail.
                const tailStr = (json.tail || '').trim();
                if (tailStr) {
                  let tailEl = currentToolBubble.querySelector('.agent-thread-tail');
                  if (!tailEl) {
                    tailEl = document.createElement('pre');
                    tailEl.className = 'agent-thread-tail';
                    tailEl.style.cssText = 'margin:4px 0 0;padding:6px 8px;font-size:11px;background:rgba(0,0,0,0.18);border-radius:4px;max-height:140px;overflow:auto;white-space:pre-wrap;opacity:0.85;';
                    const content = currentToolBubble.querySelector('.agent-thread-content');
                    if (content) content.appendChild(tailEl);
                  }
                  tailEl.textContent = tailStr;
                  tailEl.scrollTop = tailEl.scrollHeight;
                }
                uiModule.scrollHistory();

              } else if (json.type === 'tool_output') {
                if (_isBg) continue;
                // --- Update the current thread node ---
                if (currentToolBubble) {
                  // Stop wave animation + the per-second cooking ticker
                  if (currentToolBubble._waveInterval) {
                    clearInterval(currentToolBubble._waveInterval);
                    currentToolBubble._waveInterval = null;
                  }
                  if (currentToolBubble._elapsedTicker) {
                    clearInterval(currentToolBubble._elapsedTicker);
                    currentToolBubble._elapsedTicker = null;
                  }
                  const ok = (json.exit_code === 0 || json.exit_code == null);
                  const cmd = json.command || '';
                  let outHtml = '';
                  if (json.output && json.output.trim()) {
                    outHtml = `<details class="agent-tool-output"><summary>Output</summary><pre>${esc(json.output)}</pre></details>`;
                  }
                  const cmdHtml2 = cmd ? `<pre class="agent-thread-cmd">${esc(cmd)}</pre>` : '';
                  // Preserve the user's .open choice across the innerHTML
                  // rewrite \u2014 otherwise expanding a running tool collapses
                  // it as soon as the result lands, forcing the user to
                  // click again. Click handling is delegated (see init at
                  // bottom of file) so no per-node listener needed.
                  const _wasOpen = currentToolBubble.classList.contains('open');
                  currentToolBubble.className = 'agent-thread-node' + (ok ? '' : ' error') + (_wasOpen ? ' open' : '');
                  currentToolBubble.innerHTML = `<div class="agent-thread-dot"></div><div class="agent-thread-header"><span class="agent-thread-icon">${ok ? '\u2713' : '\u2717'}</span><span class="agent-thread-tool">${esc(json.tool)}</span><span class="agent-thread-status">${ok ? 'done' : 'failed'}</span><span class="agent-thread-chevron">\u25B6</span></div><div class="agent-thread-content">${cmdHtml2}${outHtml}</div>`;
                  // Reset so thinking spinner between tools says "Thinking" not the old tool's label
                  _lastToolName = '';
                  uiModule.scrollHistory();
                }
                // --- Render generated images inline ---
                if (json.image_url) {
                  const chatBox = document.getElementById('chat-history');
                  chatBox.appendChild(_buildImageBubble(json.image_url, json.image_prompt, json.image_model, json.image_size, json.image_quality, json.image_id));
                  uiModule.scrollHistory();
                  // Notify gallery to refresh if open
                  window.dispatchEvent(new CustomEvent('gallery-refresh'));
                }
                // --- Render browser screenshots in tool output ---
                if (json.screenshot && currentToolBubble) {
                  const contentEl = currentToolBubble.querySelector('.agent-thread-content');
                  if (contentEl) {
                    const details = document.createElement('details');
                    details.className = 'agent-tool-output';
                    details.innerHTML = `<summary>Screenshot</summary><img src="${json.screenshot}" style="max-width:100%;border-radius:6px;margin-top:6px;border:1px solid var(--border)" />`;
                    contentEl.appendChild(details);
                  }
                }
                // --- Reload sessions after manage_session tool (delete, rename, etc.) ---
                // Debounce so bulk deletes don't fire loadSessions per call
                if (json.tool === 'manage_session' && sessionModule) {
                  if (window._manageSessionTimer) clearTimeout(window._manageSessionTimer);
                  window._manageSessionTimer = setTimeout(() => sessionModule.loadSessions(), 1000);
                }
                // --- Live-refresh the calendar after manage_calendar (add/edit/delete) ---
                // so a new event shows without the user hard-refreshing. Debounced
                // so a batch of event creates only triggers one refetch.
                if (json.tool === 'manage_calendar') {
                  if (window._manageCalTimer) clearTimeout(window._manageCalTimer);
                  window._manageCalTimer = setTimeout(
                    () => window.dispatchEvent(new CustomEvent('calendar-refresh')), 600);
                }
                // --- Live-refresh Memories after manage_memory changes ---
                if (json.tool === 'manage_memory') {
                  if (window._manageMemoryTimer) clearTimeout(window._manageMemoryTimer);
                  window._manageMemoryTimer = setTimeout(
                    () => window.dispatchEvent(new CustomEvent('memory-refresh')), 600);
                }
                // --- Apply UI control actions embedded in tool_output ---
                if (json.ui_event) {
                  chatStream.handleUIControl(json);
                }

                // Schedule a thinking spinner between tool rounds (short delay so
                // agent_step in the same SSE chunk can cancel it before it shows)
                _scheduleThinkingSpinner();
                uiModule.scrollHistory();

              } else if (json.type === 'doc_stream_open') {
                if (_isBg) {
                  // Store for replay when user returns to this session
                  var bgDocOpen = _backgroundStreams.get(streamSessionId);
                  if (bgDocOpen) {
                    bgDocOpen._docTitle = json.title || '';
                    bgDocOpen._docLang = json.language || '';
                    bgDocOpen._docContent = '';
                  }
                  continue;
                }
                if (documentModule) {
                  documentModule.streamDocOpen(json.title || '', json.language || '');
                }

              } else if (json.type === 'doc_stream_delta') {
                if (_isBg) {
                  var bgDocDelta = _backgroundStreams.get(streamSessionId);
                  if (bgDocDelta) bgDocDelta._docContent = json.content || '';
                  continue;
                }
                if (documentModule) {
                  documentModule.streamDocDelta(json.content || '');
                }

              } else if (json.type === 'doc_update') {
                // doc_update means the server already saved the doc to DB.
                if (_isBg) continue;
                if (documentModule) {
                  documentModule.handleDocUpdate(json);
                }

              } else if (json.type === 'doc_suggestions') {
                if (_isBg) continue;
                if (documentModule && documentModule.handleDocSuggestions) {
                  documentModule.handleDocSuggestions(json);
                }

              } else if (json.type === 'ui_control') {
                if (_isBg) continue;
                chatStream.handleUIControl(json.data || {});

              } else if (json.type === 'agent_step') {
                if (_isBg) continue;
                _cancelThinkingTimer();
                _removeThinkingSpinner();
                _renderStream();
                // Mark thread as connected to bubble below
                const _activeThread = document.querySelector('.agent-thread.streaming');
                if (_activeThread) {
                  _activeThread.classList.add('has-bottom');
                }
                // --- New round: create fresh AI bubble with spinner ---
                currentToolBubble = null;
                roundFinalized = false;
                isThinking = false;
                _docFenceOpened = false;
                _docFenceContentStart = -1;
                const box = document.getElementById('chat-history');
                const newWrap = document.createElement('div');
                newWrap.className = 'msg msg-ai msg-continuation streaming';
                // Add model name label
                const newRole = document.createElement('div');
                newRole.className = 'role';
                const metaS = sessionModule.getSessions().find(s => s.id === streamSessionId);
                newRole.textContent = _shortModel(metaS?.model) || '';
                _applyModelColor(newRole, metaS?.model);
                newWrap.appendChild(newRole);
                const newBody = document.createElement('div');
                newBody.className = 'body';
                newWrap.appendChild(newBody);
                box.appendChild(newWrap);
                roundHolder = newWrap;
                roundText = '';
                // Destroy any previous spinner before creating new one
                if (spinner && spinner.element) spinner.destroy();
                // Show spinner while waiting for text (skip for research — has its own progress)
                if (!_researchingStreamIds.has(streamSessionId)) {
                  spinner = spinnerModule.create('Generating response', 'right', 'wave');
                  newBody.appendChild(spinner.createElement());
                  spinner.start();
                }
                if (streamingTTS) window.aiTTSManager._streamSentencesSent = 0;
                uiModule.scrollHistory();
              } else if (json.type === 'budget_exceeded') {
                if (_isBg) continue;
                _cancelThinkingTimer();
                _removeThinkingSpinner();
                const budgetDiv = document.createElement('div');
                budgetDiv.style.cssText = 'font-size:11px;opacity:0.6;font-style:italic;padding:4px 8px;margin:4px 0;';
                budgetDiv.textContent = `Tool budget reached (${json.used}/${json.limit} calls). Agent stopped.`;
                const chatBox = document.getElementById('chat-history');
                chatBox.appendChild(budgetDiv);

              } else if (json.type === 'teacher_takeover') {
                if (_isBg) continue;
                _cancelThinkingTimer();
                _removeThinkingSpinner();
                // Finalize any in-flight bubble so the takeover banner
                // separates student attempt from teacher attempt.
                if (spinner && spinner.element) { try { spinner.destroy(); } catch(_){} spinner = null; }
                const chatBox = document.getElementById('chat-history');
                const banner = document.createElement('div');
                banner.className = 'teacher-takeover-banner';
                banner.style.cssText = 'margin:10px 0;padding:8px 12px;border-left:3px solid #c08a3e;background:rgba(192,138,62,0.08);font-size:12px;color:var(--fg);border-radius:4px;';
                const teacherName = json.teacher_model || 'teacher';
                const why = json.student_failure ? ` &mdash; <span style="opacity:0.7">${esc(json.student_failure)}</span>` : '';
                banner.innerHTML = `<strong>Teacher takeover:</strong> escalating to <code>${esc(teacherName)}</code>${why}`;
                chatBox.appendChild(banner);
                // Reset round bubble state so the teacher's first text starts a new bubble
                roundHolder = null;
                roundText = '';
                roundFinalized = false;
                currentToolBubble = null;
                uiModule.scrollHistory();

              } else if (json.type === 'skill_saved') {
                if (_isBg) continue;
                const chatBox = document.getElementById('chat-history');
                const note = document.createElement('div');
                note.className = 'skill-saved-note';
                note.style.cssText = 'margin:6px 0;padding:6px 10px;border-left:3px solid #4a8a4a;background:rgba(74,138,74,0.07);font-size:12px;color:var(--fg);border-radius:4px;';
                note.innerHTML = `<strong>Skill learned:</strong> <code>${esc(json.name || '')}</code>${json.category ? ` <span style="opacity:0.6">[${esc(json.category)}]</span>` : ''}`;
                chatBox.appendChild(note);
                uiModule.scrollHistory();

              } else if (json.type === 'escalation_failed' || json.type === 'skill_save_failed') {
                if (_isBg) continue;
                const chatBox = document.getElementById('chat-history');
                const note = document.createElement('div');
                note.className = 'escalation-failed-note';
                note.style.cssText = 'margin:6px 0;padding:6px 10px;border-left:3px solid #8a4a4a;background:rgba(138,74,74,0.07);font-size:12px;color:var(--fg);border-radius:4px;';
                const label = json.type === 'escalation_failed' ? 'Teacher could not solve it' : 'Skill not saved';
                note.innerHTML = `<strong>${label}:</strong> <span style="opacity:0.75">${esc(json.reason || '')}</span>`;
                chatBox.appendChild(note);
                uiModule.scrollHistory();

              } else if (json.error) {
                // --- Backend error (timeout, connection issue, etc.) ---
                console.error('Stream error from backend:', json.error);
                if (_isBg) continue;
                if (spinner && spinner.element) spinner.destroy();
                const errDiv = document.createElement('div');
                errDiv.style.cssText = 'color: var(--color-error); font-style: italic; padding: 4px 0;';
                errDiv.textContent = `[Error: ${json.error}]`;
                roundHolder.querySelector('.body').appendChild(errDiv);
                uiModule.scrollHistory();
              }
            } catch (e) {
              console.error('Error parsing SSE data:', e);
            }
          }
        }
      }

      if (!_streamSawDone) {
        throw new Error('Stream closed before completion');
      }

      _renderStream();
      _cancelThinkingTimer();
      _removeThinkingSpinner();
      // Stop any thread pulse animations
      document.querySelectorAll('.agent-thread.streaming').forEach(t => t.classList.remove('streaming'));
      // --- Final render (skip if stream was ever backgrounded or currently in background) ---
      // Remove streaming class from all round bubbles
      holder.classList.remove('streaming');
      if (roundHolder && roundHolder !== holder) roundHolder.classList.remove('streaming');

      const _isBgFinal = (sessionModule.getCurrentSessionId() !== streamSessionId) || _backgroundStreams.has(streamSessionId);
      if (!_isBgFinal) {
        finalMeta = sessionModule.getSessions().find(s => s.id === sessionModule.getCurrentSessionId());
        finalModelName = _shortModel(metrics?.model || finalMeta?.model);
        // Preserve suffix (e.g. "Research") if set by model_info event
        if (holder._roleSuffix) finalModelName += ' (' + holder._roleSuffix + ')';
        // Prepend character name if set
        var _charNameFinal = presetsModule.getCharacterName ? presetsModule.getCharacterName() : '';
        if (_charNameFinal) finalModelName = _charNameFinal;
        const roleEl = holder.querySelector('.role');
        if (roleEl) {
          const tsSpan = roleEl.querySelector('.role-timestamp');
          roleEl.textContent = finalModelName + ' ';
          _applyModelColor(roleEl, metrics?.model || finalMeta?.model);
          if (tsSpan) roleEl.appendChild(tsSpan);
        }
        holder.dataset.raw = accumulated;

        // Anti-stall: a turn that ran tools but ended with essentially no
        // final prose usually means the model stopped mid-task (the case
        // where you had to type "did you finish?"). Offer a one-click
        // Continue that resumes exactly where it left off — reuses the same
        // resume mechanism as the user-stop "[Message interrupted]" button.
        try {
          const _usedTools = holder.querySelector('.agent-thread-node');
          const _proseLen = (accumulated || '').replace(/<[^>]*>/g, '').trim().length;
          if (_usedTools && _proseLen < 24 && !holder.querySelector('.agent-continue-btn')) {
            const _stall = document.createElement('div');
            _stall.className = 'stopped-indicator';
            const _lbl = document.createElement('span');
            _lbl.style.cssText = 'font-style:italic;opacity:0.7;';
            _lbl.textContent = 'Paused mid-task';
            _stall.appendChild(_lbl);
            const _cont = document.createElement('button');
            _cont.className = 'continue-btn agent-continue-btn';
            _cont.title = 'Continue — pick up where it left off';
            _cont.textContent = '▸';
            _cont.addEventListener('click', () => {
              _stall.remove();
              const mi = uiModule.el('message');
              if (mi) {
                mi.value = 'Continue — you stopped before finishing. Pick up exactly where you left off and complete the task.';
                const sb = document.querySelector('.send-btn');
                if (sb) sb.click();
              }
            });
            _stall.appendChild(_cont);
            (holder.querySelector('.body') || holder).appendChild(_stall);
          }
        } catch (_) {}

        // Clear streaming minHeight lock
        const _streamContent = roundHolder.querySelector('.stream-content');
        if (_streamContent) _streamContent.style.minHeight = '';

        // Finalize the last round's bubble — flatten stream-content wrapper for clean DOM
        const finalDisplay = stripToolBlocks(roundText);
        if (finalDisplay.trim()) {
          var _body4 = roundHolder.querySelector('.body');
          // Preserve sources expanded state before final render
          var _wasExpanded = _sourcesExpanded || !!(_body4 && _body4.querySelector('.sources-content.expanded'));

          // If thinking was collapsed in-place during streaming, preserve it
          var _liveReplyEl = _body4 && _body4.querySelector('.live-reply-content');
          var _extracted = _liveReplyEl ? markdownModule.extractThinkingBlocks(finalDisplay) : null;
          var _finalReply = '';
          if (_liveReplyEl) {
            // Try standard extraction first (for native <think> tags)
            if (_extracted?.thinkingBlocks?.length) {
              _finalReply = (_extracted.content || '').trim();
            } else {
              // Non-tag thinking: extract reply from raw text
              // Handle garbled <think> tag: "Thinking: reasoning\n<think>reply"
              const _garbledMatch = finalDisplay.match(/^[\s\S]+?<think(?:ing)?>\s*([\s\S]*?)(?:<\/think(?:ing)?>)?\s*$/i);
              if (_garbledMatch && _garbledMatch[1].trim()) {
                _finalReply = _garbledMatch[1].trim();
              } else {
                // Pure non-tag: find reply boundary by prefix patterns
                const _rs2 = ['Hey', 'Hi ', 'Hi!', 'Hello', 'Sure', 'Yes', 'No ', 'No,', 'Yo', 'OK', 'Here', 'Absolutely', 'Of course', 'Great', 'Alright', 'Thanks', 'Welcome', 'Good ', "I'm happy", "I'd be"];
                const _fr = (finalDisplay || '').trimStart();
                if (markdownModule.startsWithReasoningPrefix(_fr)) {
                  const _fLines = _fr.split('\n');
                  for (let _fi = 1; _fi < _fLines.length; _fi++) {
                    const _fl = _fLines[_fi].trim();
                    if (!_fl) continue;
                    if (_rs2.some(rp => _fl.startsWith(rp))) { _finalReply = _fLines.slice(_fi).join('\n'); break; }
                  }
                  // Within-line check
                  if (!_finalReply) {
                    for (const rp of _rs2) {
                      const rx = new RegExp('[.!?]\\s*(' + rp.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')');
                      const m = rx.exec(_fr);
                      if (m && m.index > 20) { _finalReply = _fr.slice(m.index + 1).trim(); break; }
                    }
                  }
                }
              }
            }
          }
          if (_liveReplyEl && _finalReply) {
            // Render reply into the live-reply container (thinking bar already showing)
            var _replyHtml = markdownModule.mdToHtml(markdownModule.squashOutsideCode(_finalReply));
            _liveReplyEl.innerHTML = _replyHtml;
            _liveReplyEl.classList.remove('live-reply-content');
            if (_sourcesData) {
              var _srcEl = document.createElement('div');
              _srcEl.innerHTML = _buildSourcesBox(_sourcesData, _sourcesType, _wasExpanded);
              _body4.insertBefore(_srcEl.firstChild || _srcEl, _body4.firstChild);
            }
            if (_findingsData) _body4.insertAdjacentHTML('beforeend', chatRenderer.buildFindingsBox(_findingsData));
          } else {
            // Full re-render (reply empty or no live-reply container)
            _body4.innerHTML = (_sourcesData ? _buildSourcesBox(_sourcesData, _sourcesType, _wasExpanded) : '')
              + markdownModule.processWithThinking(markdownModule.squashOutsideCode(finalDisplay))
              + (_findingsData ? chatRenderer.buildFindingsBox(_findingsData) : '');
          }
        } else if (_sourcesHtml) {
          var _body4b = roundHolder.querySelector('.body');
          var _wasExpanded2 = _sourcesExpanded || !!(_body4b && _body4b.querySelector('.sources-content.expanded'));
          _body4b.innerHTML = _sourcesData ? _buildSourcesBox(_sourcesData, _sourcesType, _wasExpanded2) : _sourcesHtml;
        } else if (roundHolder !== holder) {
          // Check if there's thinking content worth showing
          const _thinkMatch = roundText.match(/<think(?:ing)?>([\s\S]*?)<\/think(?:ing)?>/i);
          if (_thinkMatch && _thinkMatch[1].trim()) {
            // Show thinking in a collapsed section even if no visible reply text
            const _body4c = roundHolder.querySelector('.body');
            if (_body4c) _body4c.innerHTML = markdownModule.processWithThinking(roundText);
          } else {
            roundHolder.style.display = 'none';
            // Thread above expected a bubble below — remove has-bottom since bubble is hidden
            const _lastThread = roundHolder.previousElementSibling;
            if (_lastThread && _lastThread.classList.contains('agent-thread')) {
              _lastThread.classList.remove('has-bottom');
            }
          }
        }


        if (window.hljs) {
          roundHolder.querySelectorAll('pre code').forEach((block) => {
            window.hljs.highlightElement(block);
          });
        }
        if (markdownModule.renderMermaid) markdownModule.renderMermaid(roundHolder);

        uiModule.scrollHistory();
        // Render RAG sources if present
        if (holder._ragSources && holder._ragSources.length) {
          const details = document.createElement('details');
          details.className = 'rag-sources';
          const summary = document.createElement('summary');
          summary.textContent = `Sources (${holder._ragSources.length} documents)`;
          details.appendChild(summary);
          holder._ragSources.forEach(src => {
            const item = document.createElement('div');
            item.className = 'rag-source-item';
            const _esc = uiModule.esc;
            item.innerHTML = `<strong>${_esc(src.filename)}</strong> <span class="rag-similarity">${(src.similarity * 100).toFixed(1)}%</span><div class="rag-snippet">${_esc(src.snippet)}</div>`;
            details.appendChild(item);
          });
          holder.querySelector('.body').appendChild(details);
        }

        // Hide first bubble if it has no visible text content (e.g. agent went straight to tools)
        if (holder !== roundHolder && holder.style.display !== 'none') {
          const _hBody = holder.querySelector('.body');
          const _hText = _hBody ? _hBody.textContent.trim() : '';
          if (!_hText) holder.style.display = 'none';
        }

        // Attach footer to the last visible bubble (roundHolder for multi-round agent, holder for single)
        const footerTarget = (roundHolder && roundHolder !== holder && roundHolder.style.display !== 'none') ? roundHolder : holder;
        footerTarget.appendChild(createMsgFooter(footerTarget));
        // Add "View Report" link for completed research
        if (_researchingStreamIds.has(streamSessionId)) {
          _appendViewReportLink(footerTarget, streamSessionId);
        }
        // Also store raw on the footer target so copy/TTS work
        if (footerTarget !== holder) footerTarget.dataset.raw = accumulated;
        if (addAITTSButton && accumulated && window.aiTTSManager?._provider !== 'disabled' && window.aiTTSManager?.available) {
          addAITTSButton(footerTarget, accumulated);
        }
        // TTS auto-play: streaming mode flushes remaining text, non-streaming enqueues full message
        if (accumulated && window.aiTTSManager && window.aiTTSManager.autoPlay) {
          const ttsBtn = holder.querySelector('.ai-tts-button');
          if (ttsBtn) {
            var ICON_PLAY_TTS = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="6 3 20 12 6 21 6 3"/></svg>';
            var ICON_STOP_TTS = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>';
            const resetFn = () => {
              ttsBtn.innerHTML = ICON_PLAY_TTS;
              ttsBtn.classList.remove('playing', 'loading');
              ttsBtn.style.color = '#6b7280';
              ttsBtn.title = 'Read aloud';
            };
            if (streamingTTS) {
              // Flush remaining partial sentence and attach the real button
              window.aiTTSManager.streamingEnd(accumulated);
              window.aiTTSManager.streamingAttachButton(ttsBtn, resetFn);
              // If still playing sentences from the stream, show stop icon
              if (window.aiTTSManager.isPlaying || window.aiTTSManager._processing) {
                ttsBtn.innerHTML = ICON_STOP_TTS;
                ttsBtn.classList.add('playing');
                ttsBtn.style.color = '#ccc';
                ttsBtn.title = 'Stop';
              }
            } else {
              // Non-streaming fallback (autoPlay toggled mid-stream, etc.)
              window.aiTTSManager.enqueue(accumulated, ttsBtn, resetFn);
            }
          }
        }
        if (metrics) {
          displayMetrics(footerTarget, metrics);
        }
        // Attach variant navigation if this was a regeneration
        _attachVariantNav(footerTarget);

        // Merge with previous stopped message if this was a continue
        if (_pendingContinue) {
          const prevEl = _pendingContinue;
          _pendingContinue = null;
          const prevBody = prevEl.querySelector('.body');
          const newBody = footerTarget.querySelector('.body');
          if (prevBody && newBody && prevEl.parentNode) {
            // Merge: combine raw text with *(continued)* marker
            const oldRaw = prevEl.dataset.raw || '';
            const newRaw = footerTarget.dataset.raw || '';
            const mergedRaw = oldRaw + '\n\n*(continued)*\n\n' + newRaw;
            prevEl.dataset.raw = mergedRaw;
            // Re-render merged content
            prevBody.innerHTML = markdownModule.processWithThinking(
              markdownModule.squashOutsideCode(mergedRaw)
            );
            // Remove the new bubble and re-add footer to the merged one
            footerTarget.remove();
            const oldFooter = prevEl.querySelector('.msg-footer');
            if (oldFooter) oldFooter.remove();
            prevEl.appendChild(createMsgFooter(prevEl));
            if (window.hljs) {
              prevEl.querySelectorAll('pre code').forEach(block => window.hljs.highlightElement(block));
            }

            // Persist merge to server
            const sid = sessionModule.getCurrentSessionId();
            if (sid) {
              fetch(`${API_BASE}/api/session/${sid}/merge-last-assistant`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ separator: '\n\n*(continued)*\n\n' })
              }).catch(e => console.warn('merge-last-assistant failed:', e));
            }
          }
        }
      } // end if (!_isBgFinal)

    } catch (err) {
      _renderStream();
      // Clean up any active spinner (e.g. "Generating response" during tool calls)
      if (spinner && spinner.element) spinner.destroy();
      _cancelThinkingTimer();
      _removeThinkingSpinner();
      document.querySelectorAll('.agent-thread.streaming').forEach(t => t.classList.remove('streaming'));
      // Check if this stream was running in background
      const _isBgCatch = (sessionModule.getCurrentSessionId() !== streamSessionId) || _backgroundStreams.has(streamSessionId);

      if (_isBgCatch) {
        // Error happened while backgrounded — update map, don't touch DOM
        console.error('Background stream error:', err);
        var bgErr = _backgroundStreams.get(streamSessionId);
        if (bgErr && bgErr.status === 'completed') {
          // [DONE] was already processed — this error is benign (e.g. reader.read() after close)
          // Don't override the completed status; just ensure the completed dot stays
          if (sessionModule && sessionModule.clearStreaming) {
            sessionModule.clearStreaming(streamSessionId);
          }
        } else if (bgErr) {
          bgErr.status = 'error';
          if (sessionModule && sessionModule.clearStreaming) {
            sessionModule.clearStreaming(streamSessionId);
          }
        }
      } else {
        // Stop streaming TTS on any error/abort
        if (streamingTTS && window.aiTTSManager) window.aiTTSManager.stop();

        if (currentAbort && currentAbort.signal.aborted) {
          const abortReason = currentAbort._reason || '';
          // Timeout-triggered aborts should remain visible instead of disappearing.
          if (timedOut || abortReason === 'timeout') {
            const timeoutMsg = _isAgent
              ? 'Agent response timed out. Try again, switch to a faster model, or reduce tool usage.'
              : 'Response timed out. Try again.';

            if (holder && !accumulated) {
              holder.querySelector('.body').innerHTML =
                `<div style="color: var(--color-error); font-style: italic; padding: 4px 0;">[${timeoutMsg}]</div>`;
            } else if (holder && accumulated) {
              const timeoutNote = document.createElement('div');
              timeoutNote.className = 'stopped-indicator';
              timeoutNote.innerHTML =
                `<span style="color: var(--color-error);">[${timeoutMsg}]</span>`;
              holder.querySelector('.body').appendChild(timeoutNote);
            }
            currentAbort = null;
            return;
          }

          if (abortReason === 'offline') {
            const offlineMsg = 'Endpoint offline — switch model or try again.';
            if (holder && !accumulated) {
              holder.querySelector('.body').innerHTML =
                `<div style="color: var(--color-error); font-style: italic; padding: 4px 0;">[${offlineMsg}]</div>`;
            } else if (holder && accumulated) {
              const offlineNote = document.createElement('div');
              offlineNote.className = 'stopped-indicator';
              offlineNote.innerHTML =
                `<span style="color: var(--color-error);">[${offlineMsg}]</span>`;
              holder.querySelector('.body').appendChild(offlineNote);
            }
            currentAbort = null;
            return;
          }

          if (abortReason === 'recovery') {
            const recoveryMsg = 'Streaming was interrupted after the tab went inactive. Partial output was preserved.';
            if (holder && !accumulated) {
              holder.querySelector('.body').innerHTML =
                `<div style="color: var(--color-error); font-style: italic; padding: 4px 0;">[${recoveryMsg}]</div>`;
            } else if (holder && accumulated) {
              const recoveryNote = document.createElement('div');
              recoveryNote.className = 'stopped-indicator';
              recoveryNote.innerHTML =
                `<span style="color: var(--color-error);">[${recoveryMsg}]</span>`;
              holder.querySelector('.body').appendChild(recoveryNote);
            }
            currentAbort = null;
            return;
          }

          // User-initiated stop (or browser navigation abort).
          // Stopped before any text arrived — keep the bubble as a
          // "Cancelled by user" record (so it survives a refresh).
          if (holder && !accumulated) {
            _renderCancelledBubble(holder);
          }

          // But just in case the stop button didn't render it, render it here
          if (holder && accumulated && !currentHolder) {
            holder.dataset.raw = accumulated;
            holder.querySelector('.body').innerHTML = markdownModule.processWithThinking(
              markdownModule.squashOutsideCode(accumulated)
            );

            if (window.hljs) {
              holder.querySelectorAll('pre code').forEach((block) => {
                window.hljs.highlightElement(block);
              });
            }

            const stoppedIndicator = document.createElement('div');
            stoppedIndicator.className = 'stopped-indicator';
            const stoppedLabel = document.createElement('span');
            stoppedLabel.textContent = '[Message interrupted]';
            stoppedIndicator.appendChild(stoppedLabel);
            const continueBtn = document.createElement('button');
            continueBtn.className = 'continue-btn';
            continueBtn.title = 'Continue';
            continueBtn.textContent = '\u25B8';
            continueBtn.addEventListener('click', () => {
              stoppedIndicator.remove();
              _hideUserBubble = true;
              _pendingContinue = holder;
              const cutoff = accumulated;
              const msgInput = uiModule.el('message');
              if (msgInput) {
                msgInput.value = 'Your previous response was interrupted. It ended with:\n\n' + cutoff.slice(-500) + '\n\nDo NOT repeat what you already said. Continue exactly from where you were cut off.';
                const sb = document.querySelector('.send-btn');
                if (sb) sb.click();
              }
            });
            stoppedIndicator.appendChild(continueBtn);
            holder.querySelector('.body').appendChild(stoppedIndicator);

            // Tell server to mark this message as stopped
            const _sid2 = sessionModule.getCurrentSessionId();
            if (_sid2) fetch(`${API_BASE}/api/session/${_sid2}/mark-stopped`, { method: 'POST' }).catch(e => console.warn('mark-stopped failed:', e));

            if (!holder.querySelector('.msg-footer')) {
              holder.appendChild(createMsgFooter(holder));
            }

            uiModule.scrollHistory();
          }

          // Now clear the abort controller
          currentAbort = null;
        } else {
          console.error(err);
          // Stream died with a tool node still spinning. Its per-node tickers
          // (_elapsedTicker 50ms / _waveInterval 100ms) are normally cleared in
          // `tool_output`, which will never arrive now — without this sweep they
          // fire forever on the orphaned node (and auto-recover compounds it per
          // nudge). Safe here: auto-recover's new send is deferred 200ms, so no
          // fresh running nodes exist yet.
          document.querySelectorAll('.agent-thread-node.running').forEach(node => {
            if (node._waveInterval) { clearInterval(node._waveInterval); node._waveInterval = null; }
            if (node._elapsedTicker) { clearInterval(node._elapsedTicker); node._elapsedTicker = null; }
            node.classList.remove('running');
          });
          // Stream died unexpectedly — the "silently died" case. Re-engage the
          // model immediately (no wait) with a completion handshake, up to the
          // cap. Only auto-recover from connection-class failures; deterministic
          // errors (unsupported tools, 4xx/5xx, parse failures) surface right away
          // instead of burning the nudge budget on a guaranteed-to-fail retry.
          if (!(_isRecoverableStreamErr(err) && _tryAutoRecover(holder, accumulated, streamSessionId))) {
            const errorHolder = document.querySelector('.msg-ai:last-of-type .body');
            if (errorHolder) {
              let errMsg = `Error: ${err.message}`;
              // Add hint for tool-call errors
              if (err.message && (err.message.includes('tool') || err.message.includes('auto'))) {
                errMsg += '\n\nThis model may not support tools — try switching to Chat mode.';
              }
              typewriterInto(errorHolder, errMsg);
            }
          }
        }
      }
    } finally {
      clearProcessingProbe();
      // Streaming done — let screen readers announce the settled response.
      const _chatLogDone = document.getElementById('chat-history');
      if (_chatLogDone) _chatLogDone.setAttribute('aria-busy', 'false');
      // Always clean up research tracking regardless of background state
      _researchingStreamIds.delete(streamSessionId);
      if (_researchingStreamIds.size === 0) {
        var _rToggleCleanup = document.getElementById('research-toggle-btn');
        if (_rToggleCleanup) _rToggleCleanup.classList.remove('research-running');
      }

      // Only reset UI state if still on the stream's session and was never backgrounded
      const _isBgFinally = (sessionModule.getCurrentSessionId() !== streamSessionId) || _backgroundStreams.has(streamSessionId);

      if (!_isBgFinally) {
        // Reset button to idle state
        updateSubmitButton('idle', submitBtn);

        // Re-enable message input; on mobile blur to dismiss keyboard
        if (messageInput) {
          messageInput.disabled = false;
          if (window.innerWidth <= 768) {
            messageInput.blur();
          } else {
            messageInput.focus();
          }
        }

        // Clear tracking variables
        currentAccumulated = '';
        currentHolder = null;
        currentSpinner = null;
        _researchingStreamIds.delete(streamSessionId);
        // Clear research-running highlight if no more active research
        if (_researchingStreamIds.size === 0) {
          var _rToggle2 = document.getElementById('research-toggle-btn');
          if (_rToggle2) _rToggle2.classList.remove('research-running');
        }
        _clearResearchTimer();

        // Re-enable research button and auto-untoggle after use
        // (skip if clarification round — keep toggle on for follow-up)
        const _el = uiModule.el;
        const _researchBtn = _el('research-toggle-btn');
        const _researchToggle = _el('research-toggle');
        if (_researchToggle && _researchToggle.checked) {
          _researchToggle.checked = false;
          Storage.setToggle('research', false);
        }
        if (_researchBtn) {
          _researchBtn.disabled = false;
          _researchBtn.classList.remove('active');
          _researchBtn.style.display = 'none';
        }
        // Also sync overflow and tool sidebar buttons
        const _overflowRes = _el('overflow-research-btn');
        if (_overflowRes) _overflowRes.classList.remove('active');
        const _toolRes = _el('tool-research-btn');
        if (_toolRes) _toolRes.classList.remove('active');

      }

      // Research clarification timeout — if user doesn't reply within 5 min, show timeout
      if (holder && holder._roleSuffix === 'Research' && !_researchingStreamIds.has(streamSessionId)) {
        var _timeoutSessionId = streamSessionId;
        var _timeoutTimer = setTimeout(async function() {
          // Check if research_pending is still active (user hasn't replied)
          try {
            var _box = document.getElementById('chat-history');
            if (_box && sessionModule.getCurrentSessionId() === _timeoutSessionId) {
              var _timeoutMsg = document.createElement('div');
              _timeoutMsg.className = 'msg msg-ai';
              _timeoutMsg.innerHTML = '<div class="role">Odysseus</div><div class="body" style="opacity:0.6;font-style:italic;">Research clarification timed out. Toggle research again to start over.</div>';
              _box.appendChild(_timeoutMsg);
              uiModule.scrollHistory();
            }
          } catch(_te) {}
        }, 5 * 60 * 1000);
        // Cancel timeout if user sends a message
        var _origSubmit = window._researchTimeoutTimer;
        if (_origSubmit) clearTimeout(_origSubmit);
        window._researchTimeoutTimer = _timeoutTimer;
      }

      // Release Web Lock
      if (_webLockRelease) {
        _webLockRelease();
        _webLockRelease = null;
      }

      // Refresh session list after a delay (picks up auto-generated names)
      setTimeout(() => {
        if (sessionModule && sessionModule.loadSessions) {
          sessionModule.loadSessions();
        }
      }, 3000);
    }
  }

  /**
   * Abort current chat request
   */
  // stopServer=true ONLY for an explicit user Stop. The run is now DETACHED
  // (survives tab close / navigation), so the generic abort used by cleanup
  // paths (session switch, delete, reader teardown on tab close) must NOT stop
  // the server run — otherwise closing the tab would kill the background task,
  // defeating the whole point. Only the Stop button cancels the server run.
  export function abortCurrentRequest(stopServer = false) {
    if (currentAbort) {
      currentAbort.abort();
      // Don't set to null here - let catch block handle it
    }
    if (stopServer) {
      try {
        const _sid = _streamSessionId
          || (window.sessionModule && window.sessionModule.getCurrentSessionId && window.sessionModule.getCurrentSessionId());
        if (_sid) {
          fetch(`/api/chat/stop/${encodeURIComponent(_sid)}`, { method: 'POST', credentials: 'same-origin' }).catch(() => {});
        }
      } catch (_) {}
    }
  }

  // ── Stall watchdog ──────────────────────────────────────────────
  // Auto-recover a turn whose stream died (connection drop) or went silent:
  // preserve the partial, then re-submit a completion handshake by reusing the
  // existing continue/resume path. Returns false at the cap so the caller can
  // surface the failure instead of nudging forever.
  // Only auto-recover from connection-class failures (the genuine "silently
  // died" case). Deterministic errors — unsupported tools, HTTP 4xx/5xx, JSON
  // parse failures — will fail identically on retry, so surfacing them
  // immediately is both more honest and avoids wasting the nudge budget.
  function _isRecoverableStreamErr(err) {
    if (!err) return false;
    if (err.name === 'TypeError') return true;   // fetch/reader network failure
    const m = (err.message || '').toLowerCase();
    if (/\btool\b|unsupported|json|parse|\b4\d\d\b|\b5\d\d\b/.test(m)) return false;
    return /network|fetch|connection|reset|closed|aborted|stream|tim(?:e|ed)\s?out|econn|eof/.test(m);
  }

  function _tryAutoRecover(holder, accumulated, sessionId) {
    if (_autoNudges >= _AUTO_NUDGE_CAP) return false;
    _autoNudges++;
    if (holder && accumulated) {
      holder.dataset.raw = accumulated;
      try {
        holder.querySelector('.body').innerHTML =
          markdownModule.processWithThinking(markdownModule.squashOutsideCode(accumulated));
      } catch (_) {}
    }
    _pendingContinue = holder || null;   // merge the continuation into the same bubble
    _hideUserBubble = true;              // no user bubble for the handshake
    _autoContinuePending = true;         // don't reset the counter on this submit
    const _abandon = () => {             // clear the pending flags so they can't
      _pendingContinue = null;           // leak into whatever chat is now open
      _hideUserBubble = false;
      _autoContinuePending = false;
    };
    // Defer so the stream's finally resets state first — otherwise the send
    // button is still in "stop" mode and clicking it would toggle, not send.
    setTimeout(() => {
      // The stream that died may not be the chat the user is now looking at —
      // never inject the recovery handshake into the wrong conversation.
      if (sessionId && sessionModule.getCurrentSessionId() !== sessionId) { _abandon(); return; }
      const msgInput = uiModule.el('message');
      const sb = document.querySelector('.send-btn');
      if (!msgInput || !sb) { _abandon(); return; }
      const tail = (accumulated || '').slice(-400);
      msgInput.value = tail
        ? `The stream dropped before you finished. It ended with:\n\n${tail}\n\nIf the task is fully complete, reply with just: DONE. Otherwise continue exactly where you left off and finish it — do not repeat what you already wrote.`
        : `The stream dropped before you produced anything. If the task is already done, reply with just: DONE. Otherwise complete it now.`;
      sb.click();
    }, 200);
    return true;
  }

  function _removeStallBanner() {
    const b = document.getElementById('stall-banner');
    if (b) b.remove();
    _stallBannerShown = false;
  }
  function _showStallBanner(secs) {
    if (document.getElementById('stall-banner')) return;
    _stallBannerShown = true;
    const box = document.getElementById('chat-history');
    if (!box) return;
    const bar = document.createElement('div');
    bar.id = 'stall-banner';
    bar.className = 'stall-banner';
    const mins = Math.floor(secs / 60);
    const label = mins >= 1 ? `${mins}m` : `${secs}s`;
    bar.innerHTML = `<span class="stall-banner-txt">Quiet for ${label} — still working?</span>`;
    const cont = document.createElement('button');
    cont.className = 'stall-banner-btn';
    cont.textContent = 'Nudge it';
    cont.title = 'Stop the stalled stream and ask it to continue';
    cont.addEventListener('click', () => {
      _removeStallBanner();
      const mi = uiModule.el('message');
      if (mi) {
        mi.value = 'Are you still working? If you stopped, continue exactly where you left off and finish the task.';
        const sb = document.querySelector('.send-btn');
        if (sb) sb.click();
      }
    });
    const stop = document.createElement('button');
    stop.className = 'stall-banner-btn stall-banner-stop';
    stop.textContent = 'Stop';
    stop.addEventListener('click', () => { _removeStallBanner(); abortCurrentRequest(true); });
    bar.appendChild(cont);
    bar.appendChild(stop);
    box.appendChild(bar);
    if (uiModule.scrollHistory) uiModule.scrollHistory();
  }
  function _startStallWatchdog() {
    // Disabled: the server-side stall detector / auto-continue (agent
    // loop-breaker) handles quiet/stalled streams now, so the manual
    // "Quiet for Nm — still working?" banner is redundant (and annoying).
    if (_stallWatchdog) { clearInterval(_stallWatchdog); _stallWatchdog = null; }
    _removeStallBanner();
  }
  function _stopStallWatchdog() {
    if (_stallWatchdog) { clearInterval(_stallWatchdog); _stallWatchdog = null; }
    _removeStallBanner();
  }

  /** Show a "Cancelled by user" record in `holder` and persist an empty
   *  assistant placeholder server-side so the turn survives a refresh.
   *  Called from both abort paths when no tokens had streamed yet. */
  function _renderCancelledBubble(holder) {
    if (!holder) return;
    holder.dataset.raw = '';
    const body = holder.querySelector('.body');
    if (body) {
      body.innerHTML = '';
      const indicator = document.createElement('div');
      indicator.className = 'stopped-indicator';
      const label = document.createElement('span');
      label.style.fontStyle = 'italic';
      label.style.opacity = '0.7';
      label.textContent = '[Cancelled by user]';
      indicator.appendChild(label);
      body.appendChild(indicator);
    }
    if (typeof createMsgFooter === 'function' && !holder.querySelector('.msg-footer')) {
      holder.appendChild(createMsgFooter(holder));
    }
    // Persist as an assistant message with stopped+cancelled metadata so the
    // chat-history loader renders the same indicator after a refresh.
    // Include the model name so the bubble header still shows which model
    // was running when the user hit Stop.
    const sid = sessionModule.getCurrentSessionId();
    if (sid) {
      let modelName = '';
      try { modelName = sessionModule.getCurrentModel?.() || ''; } catch {}
      // Fallback: pull from the holder's existing meta (the streaming
      // placeholder usually has the model set in the header already).
      if (!modelName) {
        modelName = holder.dataset.model
          || holder.querySelector('.msg-header .msg-model')?.textContent
          || '';
      }
      fetch(`${API_BASE}/api/session/${sid}/inject_messages`, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: [{
            role: 'assistant',
            content: '',
            metadata: { stopped: true, cancelled: true, model: modelName },
          }],
        }),
      }).catch(() => {});
    }
  }

  /**
   * Detach current stream to run in background instead of aborting.
   * Called when user switches sessions mid-stream.
   */
  export function detachCurrentStream(sessionId) {
    if (!isStreaming || !currentAbort) {
      // Not streaming — fall through to abort
      abortCurrentRequest();
      return;
    }
    // Store background stream state
    _backgroundStreams.set(sessionId, {
      status: 'running',
      accumulated: currentAccumulated,
      sourcesHtml: '',
      findingsData: null,
      abortCtrl: currentAbort,
      query: currentHolder ? (currentHolder._researchQuery || '') : '',
      metrics: null,
    });
    // Mark session with pulsing dot in sidebar
    if (sessionModule && sessionModule.markStreaming) {
      sessionModule.markStreaming(sessionId);
    }
    // Clear local state WITHOUT aborting the fetch
    currentAbort = null;
    isStreaming = false;
    currentHolder = null;
    currentAccumulated = '';
    // Reset submit button so the new chat is ready to send
    const submitBtn = document.querySelector('.send-btn');
    if (submitBtn) updateSubmitButton('idle', submitBtn);
  }

  // _notifyStreamComplete and _insertStreamDoneToast now in chatStream.js
  var _notifyStreamComplete = chatStream.notifyStreamComplete;
  var _insertStreamDoneToast = chatStream.insertStreamDoneToast;

  /**
   * Check for background streams when switching to a session.
   * Called after history loads on session switch.
   */
  export function checkBackgroundStream(sessionId) {
    if (!sessionId || !_backgroundStreams.has(sessionId)) return;
    var entry = _backgroundStreams.get(sessionId);

    if (entry.status === 'completed') {
      // Response is already saved to DB and will appear in history — just clean up
      _backgroundStreams.delete(sessionId);
      return;
    }

    if (entry.status === 'error') {
      _backgroundStreams.delete(sessionId);
      var box = document.getElementById('chat-history');
      if (box) {
        var errHolder = document.createElement('div');
        errHolder.className = 'msg msg-ai';
        errHolder.innerHTML = '<div class="body"><i style="color: var(--color-error);">[Background stream encountered an error]</i></div>';
        box.appendChild(errHolder);
      }
      return;
    }

    if (entry.status === 'running') {
      // Stream is still active — show a clean spinner, poll until done,
      // then reload history to show the final saved response.
      var box = document.getElementById('chat-history');
      if (!box) return;

      // Replay any doc content that was streamed in the background
      if (entry._docTitle != null && documentModule) {
        documentModule.streamDocOpen(entry._docTitle, entry._docLang || '');
        if (entry._docContent) {
          documentModule.streamDocDelta(entry._docContent);
        }
      }

      var holder = document.createElement('div');
      holder.className = 'msg msg-ai';
      var meta = sessionModule.getSessions().find(function(s) { return s.id === sessionId; });
      var roleLabel = _shortModel(meta && meta.model);
      var roleTs = new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
      holder.innerHTML = '<div class="role">' + roleLabel + ' <span class="role-timestamp">' + roleTs + '</span></div><div class="body"></div>';
      _applyModelColor(holder.querySelector('.role'), meta && meta.model);

      var bodyDiv = holder.querySelector('.body');
      var spinner = spinnerModule.create('Response streaming in background', 'right');
      bodyDiv.appendChild(spinner.createElement());
      spinner.start();

      box.appendChild(holder);
      uiModule.scrollHistory();

      // Poll map until stream finishes, then reload history
      var pollId = setInterval(function() {
        if (sessionModule.getCurrentSessionId() !== sessionId) {
          clearInterval(pollId);
          spinner.destroy();
          if (holder.parentNode) holder.remove();
          return;
        }
        // Update doc content while polling
        var curPoll = _backgroundStreams.get(sessionId);
        if (curPoll && curPoll._docContent && documentModule) {
          documentModule.streamDocDelta(curPoll._docContent);
        }
        if (!curPoll || curPoll.status !== 'running') {
          clearInterval(pollId);
          spinner.destroy();
          if (holder.parentNode) holder.remove(); // Remove entire holder, not just spinner
          _backgroundStreams.delete(sessionId);
          // Reload session to show the completed response — but only if the user
          // is still on it; don't yank them back from a new chat they opened.
          if (sessionModule.getCurrentSessionId && sessionModule.getCurrentSessionId() === sessionId) {
            sessionModule.selectSession(sessionId);
          } else {
            sessionModule.loadSessions();
          }
        }
      }, 500);
    }
  }

  // Tag short single-line code blocks with .pre-compact so the CSS can
  // render the Run/Edit/Copy buttons as a slim row that doesn't make a
  // 1-line bash block taller than its own contents.
  function _markCompactPre(pre) {
    const code = pre.querySelector('code');
    if (!code) return;
    const txt = code.textContent || '';
    // Count visible lines — ignore trailing newline (common with fenced
    // blocks) and treat any empty extra line as not a real second line.
    const lines = txt.replace(/\n+$/, '').split('\n');
    const compact = lines.length <= 1 && txt.length < 200;
    pre.classList.toggle('pre-compact', compact);
  }
  function _scanCompactPres(root) {
    if (!root || !root.querySelectorAll) return;
    root.querySelectorAll('pre').forEach(_markCompactPre);
  }
  // Global observer so any <pre> added anywhere in the app (chat stream,
  // chat re-renders, document library chat previews, slash commands,
  // research previews, etc.) gets tagged without each call site needing
  // to remember.
  (function _initCompactPreObserver() {
    if (window._cmpPreObserverWired) return;
    window._cmpPreObserverWired = true;
    _scanCompactPres(document.body);
    const obs = new MutationObserver((muts) => {
      for (const m of muts) {
        for (const n of m.addedNodes) {
          if (n.nodeType !== 1) continue;
          if (n.tagName === 'PRE') _markCompactPre(n);
          if (n.querySelectorAll) _scanCompactPres(n);
        }
      }
    });
    obs.observe(document.body, { childList: true, subtree: true });
  })();

  /**
   * Initialize event listeners
   */
  export function initListeners() {
    // Global event delegation for copy-code buttons
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('.copy-code');
      if (!btn) return;
      e.stopPropagation();
      const code = btn.getAttribute('data-code');
      if (code && uiModule) {
        uiModule.copyToClipboard(code);
        // Visual feedback: swap the icon to a checkmark (regular size)
        // and add .copied which the CSS uses to flash green + pulse.
        // For slim/.pre-compact buttons the label text comes from a
        // CSS ::before — swap it via data-state so we don't break the
        // text-button layout.
        const origHTML = btn.innerHTML;
        const isCompact = !!btn.closest('pre.pre-compact');
        if (!isCompact) {
          btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
        }
        btn.classList.add('copied');
        btn.dataset.state = 'copied';
        setTimeout(() => {
          if (!isCompact) btn.innerHTML = origHTML;
          btn.classList.remove('copied');
          delete btn.dataset.state;
        }, 1500);
      }
    });

    // Run code button delegation
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('.run-code');
      if (!btn) return;
      e.stopPropagation();
      if (codeRunnerModule) codeRunnerModule.run(btn);
    });

    // Edit code button delegation — toggle contentEditable on the code element
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('.edit-code');
      if (!btn) return;
      e.stopPropagation();
      const pre = btn.closest('pre');
      if (!pre) return;
      const codeEl = pre.querySelector('code');
      if (!codeEl) return;
      const isEditing = codeEl.contentEditable !== 'false' && codeEl.contentEditable !== 'inherit';
      if (isEditing) {
        // Save: exit edit mode, update data-code on copy/run buttons
        codeEl.contentEditable = 'false';
        codeEl.classList.remove('editing');
        pre.classList.remove('editing');
        const newCode = codeEl.textContent;
        const copyBtn = pre.querySelector('.copy-code');
        if (copyBtn) copyBtn.setAttribute('data-code', newCode);
        const runBtn = pre.querySelector('.run-code');
        if (runBtn) runBtn.setAttribute('data-code', newCode);
        // Swap icon back to pencil
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
        btn.title = 'Edit';
        btn.classList.remove('active');
      } else {
        // Enter edit mode. Firefox (especially on mobile) historically lacks
        // contentEditable="plaintext-only" — setting it there leaves the block
        // non-editable, so the tap "just gets a checkmark" with no way to type.
        // Fall back to "true" when plaintext-only didn't take.
        try { codeEl.contentEditable = 'plaintext-only'; } catch (_) { /* unsupported value */ }
        if (codeEl.contentEditable !== 'plaintext-only') codeEl.contentEditable = 'true';
        codeEl.classList.add('editing');
        pre.classList.add('editing');
        // preventScroll keeps the page from jumping to the codeblock when
        // focusing the editable on mobile — the browser would otherwise
        // scroll it into view above the keyboard, which reads as "auto-
        // scroll triggered by clicking Edit".
        try { codeEl.focus({ preventScroll: true }); } catch (_) { codeEl.focus(); }
        // Swap icon to checkmark
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
        btn.title = 'Done editing';
        btn.classList.add('active');
      }
    });

    // Tapping a code block body (not its buttons) toggles the overlay
    // copy/edit/run buttons, which otherwise cover the text on mobile.
    document.addEventListener('click', (e) => {
      if (e.target.closest('.copy-code, .edit-code, .run-code')) return;
      const pre = e.target.closest('pre');
      if (!pre || !pre.querySelector('.copy-code')) return;
      // Don't hide while editing — the buttons (incl. the Done checkmark) matter.
      if (pre.classList.contains('editing')) return;
      pre.classList.toggle('buttons-hidden');
    });

    // Position copy/run buttons top or bottom based on viewport position
    // — DESKTOP ONLY. On mobile this was constantly retriggering on tap
    // (synthetic mouseenter) and made the buttons jump, so the user's
    // finger landed on the moved target. Keep them pinned at the top on
    // touch — no auto-repositioning.
    document.addEventListener('mouseenter', (e) => {
      if (window.matchMedia('(max-width: 768px)').matches) return;
      const pre = e.target.closest ? e.target.closest('pre') : null;
      if (!pre || pre.dataset.btnPosComputed) return;
      const rect = pre.getBoundingClientRect();
      const threshold = window.innerHeight * 0.35;
      const isBottom = rect.top < threshold;
      const copyBtn = pre.querySelector('.copy-code');
      if (copyBtn) copyBtn.classList.toggle('bottom', isBottom);
      const editBtn = pre.querySelector('.edit-code');
      if (editBtn) editBtn.classList.toggle('bottom', isBottom);
      const runBtn = pre.querySelector('.run-code');
      if (runBtn) runBtn.classList.toggle('bottom', isBottom);
      pre.dataset.btnPosComputed = '1';
    }, true);

    // Tab suspension recovery: when user tabs back in, check if stream froze
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'visible') return;
      if (!isStreaming) return;

      // Stream claims to be running — check if reader is actually alive
      const staleSince = Date.now() - _lastReaderActivity;
      if (staleSince < 20000) return; // Active recently, probably fine

      // Reader hasn't produced data in 5+ seconds after tab resume.
      // Give it a short grace period then recover.
      console.warn('[tab-recovery] Stream appears frozen (no activity for ' + Math.round(staleSince/1000) + 's). Recovering...');

      setTimeout(() => {
        // Re-check — maybe the reader woke up during the grace period
        if (!isStreaming) return;
        const stillStale = Date.now() - _lastReaderActivity;
        if (stillStale < 5000) return; // Came back to life

        console.warn('[tab-recovery] Stream confirmed dead. Aborting and reloading session.');

        // Abort the frozen stream, but preserve the visible bubble.
        if (currentAbort) {
          currentAbort._reason = 'recovery';
          currentAbort.abort();
        }
        isStreaming = false;

        // Release Web Lock
        if (_webLockRelease) {
          _webLockRelease();
          _webLockRelease = null;
        }

        // Reset UI state
        var _submitBtn = document.getElementById('submit');
        updateSubmitButton('idle', _submitBtn);
        var _msgInput = document.getElementById('message');
        if (_msgInput) _msgInput.disabled = false;
      }, 2000); // 2 second grace period
    });

    // On mobile, fade out welcome text when keyboard opens to prevent overlap
    if (window.innerWidth <= 768) {
      const msgInput = document.getElementById('message');
      if (msgInput) {
        msgInput.addEventListener('focus', () => {
          const ws = document.getElementById('welcome-screen');
          if (ws && !ws.classList.contains('hidden')) {
            ws.classList.add('kb-hidden');
          }
        });
        msgInput.addEventListener('blur', () => {
          const ws = document.getElementById('welcome-screen');
          if (ws && !ws.classList.contains('hidden')) {
            // Delay re-show so tapping within chatbox doesn't flash
            setTimeout(() => {
              if (document.activeElement !== msgInput) {
                ws.classList.remove('kb-hidden');
              }
            }, 200);
          }
        });
      }
      // Smooth viewport resize when keyboard opens/closes
      if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', () => {
          document.documentElement.style.setProperty('--vh', window.visualViewport.height + 'px');
        });
        document.documentElement.style.setProperty('--vh', window.visualViewport.height + 'px');
      }
    }

    // If the browser discarded and restored this tab, reload the current session
    // so the user sees the server-saved partial response instead of a blank page
    if (document.wasDiscarded) {
      console.warn('[tab-recovery] Tab was discarded by browser — reloading session');
      setTimeout(() => {
        var _sid = sessionModule && sessionModule.getCurrentSessionId();
        if (_sid) sessionModule.selectSession(_sid);
      }, 500);
    }
  }

  /**
   * Regenerate response: truncate history to the user message before this AI message,
   * then re-submit that user message.
   */
  /**
   * Edit a user message: show an input, truncate to before it, resubmit the edited text.
   */
  export async function editUserMessage(userMsgElement) {
    const box = document.getElementById('chat-history');
    const allMsgs = Array.from(box.querySelectorAll('.msg'));
    const msgIndex = allMsgs.indexOf(userMsgElement);
    if (msgIndex < 0) return;

    const bodyEl = userMsgElement.querySelector('.body');
    const currentText = bodyEl ? bodyEl.textContent.trim().replace(/\s*\[\d+ attachment\(s\)\]$/, '') : '';

    // Replace body with an editable textarea
    const editor = document.createElement('textarea');
    editor.className = 'edit-textarea';
    editor.value = currentText;
    editor.rows = Math.max(2, currentText.split('\n').length);

    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex; gap:6px; margin-top:4px;';

    const saveBtn = document.createElement('button');
    saveBtn.className = 'edit-save-btn';
    saveBtn.textContent = 'Send';
    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'edit-cancel-btn';
    cancelBtn.textContent = 'Cancel';
    btnRow.appendChild(saveBtn);
    btnRow.appendChild(cancelBtn);

    const originalHTML = bodyEl.innerHTML;
    bodyEl.innerHTML = '';
    bodyEl.appendChild(editor);
    bodyEl.appendChild(btnRow);
    editor.focus();

    cancelBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      bodyEl.innerHTML = originalHTML;
    });

    saveBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const newText = editor.value.trim();
      if (!newText) return;

      const sessionId = sessionModule.getCurrentSessionId();
      if (!sessionId) return;

      const keepCount = msgIndex;
      try {
        await fetch(`${API_BASE}/api/session/${sessionId}/truncate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ keep_count: keepCount })
        });

        // Remove DOM elements from msgIndex onward
        for (let i = allMsgs.length - 1; i >= msgIndex; i--) {
          allMsgs[i].remove();
        }

        // Submit the edited text
        const messageInput = uiModule.el('message');
        messageInput.value = newText;
        const submitBtn = document.querySelector('.send-btn');
        if (submitBtn) submitBtn.click();
      } catch (err) {
        console.error('Edit failed:', err);
        if (uiModule) uiModule.showError('Edit failed: ' + err.message);
        bodyEl.innerHTML = originalHTML;
      }
    });

    // Also submit on Enter (without shift)
    editor.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        saveBtn.click();
      }
    });
  }

  /**
   * Resend a user message — truncates history to that point and resubmits.
   */
  export async function resendUserMessage(userMsgElement) {
    const box = document.getElementById('chat-history');
    const allMsgs = Array.from(box.querySelectorAll('.msg'));
    const msgIndex = allMsgs.indexOf(userMsgElement);
    if (msgIndex < 0) return;

    // Prefer dataset.raw (stripped original user text) over .body.textContent
    // — the latter slurps the rendered "View image description" collapsible
    // content too, which would then be sent back as the user's question and
    // the AI would reply to that gibberish instead of the actual prompt.
    const bodyEl = userMsgElement.querySelector('.body');
    let text = (userMsgElement.dataset.raw || (bodyEl ? bodyEl.textContent : '') || '').trim();
    text = text.replace(/\s*\[\d+ attachment\(s\)\]$/, '');

    // Collect file_ids attached to this user message so the resend re-carries
    // the photos / docs (and the chat handler picks up the user-edited OCR
    // text cached server-side under those file ids).
    const _attachEls = userMsgElement.querySelectorAll('[data-file-id]');
    let _ids = Array.from(_attachEls).map(el => el.dataset.fileId).filter(Boolean);
    if (!_ids.length) {
      const _imgs = userMsgElement.querySelectorAll('.attach-image-preview img, .attach-card img');
      for (const _im of _imgs) {
        const _m = (_im.getAttribute('src') || '').match(/\/api\/upload\/([A-Za-z0-9_\-]+)/);
        if (_m && _m[1] && !_ids.includes(_m[1])) _ids.push(_m[1]);
      }
    }

    // Rescue: legacy bubbles may have stored the filename as the message
    // content (artifact of earlier broken resends). Don't re-send that as
    // the user prompt if we still have the file attached. Loosen the regex
    // to cover real-world camera/screenshot names with spaces, parens,
    // multi-dots: "Screen Shot 2026-05-28 at 4.05.32 PM.png", "IMG (1).JPG".
    if (text && _ids.length && /^[^\n\r]{1,200}\.(png|jpe?g|gif|webp|svg|bmp|heic|heif)$/i.test(text)) {
      text = '';
    }
    // Empty text + no attachments → tell the user instead of silently bailing.
    // The common case is a regen during a pre-upload race where the bubble
    // never had an `[data-file-id]` to scrape.
    if (!text && !_ids.length) {
      if (uiModule?.showError) uiModule.showError('Nothing to resend — message has no text and no attachments yet (try again after the upload finishes).');
      return;
    }

    const sessionId = sessionModule.getCurrentSessionId();
    if (!sessionId) return;

    // Truncate backend to keep everything before this user message
    const keepCount = msgIndex;
    try {
      await fetch(`${API_BASE}/api/session/${sessionId}/truncate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keep_count: keepCount })
      });

      // Drop the AI replies after the user message but KEEP the user bubble
      // itself (so its photo stays visible). Then suppress the new user
      // bubble that send would otherwise add — same pattern as regenerate.
      let sibling = userMsgElement.nextSibling;
      while (sibling) {
        const next = sibling.nextSibling;
        sibling.remove();
        sibling = next;
      }
      _hideUserBubble = true;
      _pendingRegenAttachments = _ids;

      // Resubmit
      const messageInput = uiModule.el('message');
      messageInput.value = text;
      const submitBtn = document.querySelector('.send-btn');
      if (submitBtn) submitBtn.click();
    } catch (err) {
      console.error('Resend failed:', err);
      if (uiModule) uiModule.showError('Resend failed: ' + err.message);
    }
  }

  export async function regenerateFrom(aiMsgElement) {
    const box = document.getElementById('chat-history');
    const allMsgs = Array.from(box.querySelectorAll('.msg'));
    const aiIndex = allMsgs.indexOf(aiMsgElement);
    if (aiIndex < 0) return;

    // Find the preceding user message
    let userIndex = -1;
    let userText = '';
    let userMsgEl = null;
    for (let i = aiIndex - 1; i >= 0; i--) {
      if (allMsgs[i].classList.contains('msg-user')) {
        userIndex = i;
        userMsgEl = allMsgs[i];
        // Prefer dataset.raw (set by addMessage with the stripped, original
        // user text) over the rendered body's textContent — the latter
        // pulls in the "View image description" collapsible content too,
        // duplicating the OCR text on regen.
        const bodyEl = userMsgEl.querySelector('.body');
        userText = (userMsgEl.dataset.raw || (bodyEl ? bodyEl.textContent : '') || '').trim();
        userText = userText.replace(/\s*\[\d+ attachment\(s\)\]$/, '');
        break;
      }
    }

    if (userIndex < 0) {
      if (uiModule) uiModule.showError('Could not find the user message to regenerate');
      return;
    }

    // Collect any file_ids attached to the original user message so the
    // regenerated send re-uses them. Without this the AI is regenerated on
    // text alone — photos (and the user-edited OCR text cached server-side
    // under that file_id) would be silently dropped.
    const _attachEls = userMsgEl ? userMsgEl.querySelectorAll('[data-file-id]') : [];
    let _regenIds = Array.from(_attachEls).map(el => el.dataset.fileId).filter(Boolean);
    // Fallback for bubbles rendered before the data-file-id stamp landed:
    // sniff the file id straight out of any `.attach-image-preview img`
    // src URLs (matches /api/upload/<id>). Otherwise an older bubble would
    // regen with zero attachments and the photo would be lost from the
    // resulting message even though the file still exists on disk.
    if (!_regenIds.length && userMsgEl) {
      const _imgs = userMsgEl.querySelectorAll('.attach-image-preview img, .attach-card img');
      for (const _im of _imgs) {
        const _m = (_im.getAttribute('src') || '').match(/\/api\/upload\/([A-Za-z0-9_\-]+)/);
        if (_m && _m[1] && !_regenIds.includes(_m[1])) _regenIds.push(_m[1]);
      }
    }
    _pendingRegenAttachments = _regenIds;

    // Rescue: earlier-version regens (before the dataset.raw fix) stored the
    // photo's filename as the user-message content. On a follow-up regen,
    // that filename would be sent back as the literal user prompt, so the
    // AI thinks the question is "blue_night_preview.jpg" and replies "that's
    // an image file". If userText is just a bare image filename and we have
    // attachments, drop it so the OCR text (or the image bytes for vision
    // models) is what the model actually sees.
    if (userText && _pendingRegenAttachments.length &&
        /^[^\n\r]{1,200}\.(png|jpe?g|gif|webp|svg|bmp|heic|heif)$/i.test(userText.trim())) {
      userText = '';
    }

    // A photo-only message has empty user text — regen must still proceed,
    // because the attachments themselves are the message. Bail only if there
    // is no text AND no attachments to send.
    if (!userText && !_pendingRegenAttachments.length) {
      if (uiModule) uiModule.showError('Nothing to regenerate — the user message has no text and no attachments');
      return;
    }

    const sessionId = sessionModule.getCurrentSessionId();
    if (!sessionId) return;

    // Save current response as a variant
    const oldRaw = aiMsgElement.dataset.raw || aiMsgElement.querySelector('.body')?.textContent || '';
    const oldHtml = aiMsgElement.querySelector('.body')?.innerHTML || '';
    let variants = [];
    try { variants = JSON.parse(aiMsgElement.dataset.variants || '[]'); } catch(_) {}
    if (variants.length === 0) {
      // First regen — save the original as variant 0
      variants.push({ raw: oldRaw, html: oldHtml, label: 'original' });
    }

    const keepCount = userIndex;

    try {
      await fetch(`${API_BASE}/api/session/${sessionId}/truncate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keep_count: keepCount })
      });

      for (let i = allMsgs.length - 1; i > aiIndex; i--) {
        allMsgs[i].remove();
      }

      // Remove the AI message from DOM — it will be replaced by the new streaming response
      // But first, stash the variants data so we can transfer it to the new element
      _pendingVariants = variants;
      _pendingVariantLabel = 'regen';
      aiMsgElement.remove();

      _hideUserBubble = true;
      const messageInput = uiModule.el('message');
      messageInput.value = userText;
      const submitBtn = document.querySelector('.send-btn');
      if (submitBtn) submitBtn.click();

    } catch (err) {
      console.error('Regenerate failed:', err);
      if (uiModule) uiModule.showError('Regenerate failed: ' + err.message);
    }
  }

  // Pending variants from a regeneration — transferred to new streaming element
  let _pendingVariants = null;
  let _pendingVariantLabel = null;
  // File-ids carried over from the original user message during a regen, so
  // photos / OCR overrides survive into the new send. Consumed once.
  let _pendingRegenAttachments = null;

  /**
   * Called after streaming completes to attach variant navigation if this was a regen.
   */
  function _attachVariantNav(msgElement) {
    if (!_pendingVariants) return;
    const variants = _pendingVariants;
    _pendingVariants = null;

    // Add the new response as the latest variant
    const newRaw = msgElement.dataset.raw || msgElement.querySelector('.body')?.textContent || '';
    const newHtml = msgElement.querySelector('.body')?.innerHTML || '';
    const varLabel = _pendingVariantLabel || 'regen';
    _pendingVariantLabel = null;
    variants.push({ raw: newRaw, html: newHtml, label: varLabel });

    msgElement.dataset.variants = JSON.stringify(variants);
    msgElement.dataset.variantIndex = String(variants.length - 1);

    _renderVariantNav(msgElement, variants, variants.length - 1);

    // Persist variants to server
    const sid = sessionModule.getCurrentSessionId();
    if (sid) {
      fetch(`${API_BASE}/api/session/${sid}/update-last-meta`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ metadata: { variants: variants, variantIndex: variants.length - 1 } })
      }).catch(e => console.warn('update-last-meta (variants) failed:', e));
    }
  }

  const _VARIANT_ICONS = { regen: '\u21BB', shorter: '\u2702', simpler: '?', original: '\u25CB' };
  function _variantTagText(label) {
    return _VARIANT_ICONS[label] || _VARIANT_ICONS['original'];
  }

  function _renderVariantNav(msgElement, variants, currentIdx) {
    // Remove existing nav if any
    const old = msgElement.querySelector('.variant-nav');
    if (old) old.remove();

    if (variants.length < 2) return;

    const nav = document.createElement('span');
    nav.className = 'variant-nav';
    nav.addEventListener('click', (e) => e.stopPropagation());

    // Label showing what this variant is
    // Divider
    const divider = document.createElement('span');
    divider.className = 'variant-divider';
    divider.textContent = '|';
    nav.appendChild(divider);

    // Label
    const curVariant = variants[currentIdx];
    const tagLabel = document.createElement('span');
    tagLabel.className = 'variant-tag' + (curVariant?.label === 'shorter' ? ' variant-tag-scissors' : '');
    tagLabel.textContent = _variantTagText(curVariant?.label);
    nav.appendChild(tagLabel);

    // < button
    const prevBtn = document.createElement('button');
    prevBtn.className = 'variant-btn';
    prevBtn.textContent = '<';
    prevBtn.disabled = currentIdx === 0;
    prevBtn.addEventListener('click', (e) => { e.stopPropagation(); _switchVariant(msgElement, variants, currentIdx - 1); });
    nav.appendChild(prevBtn);

    // Clickable number for current index (click left number = go left, right = go right)
    const numLeft = document.createElement('button');
    numLeft.className = 'variant-num';
    numLeft.textContent = String(currentIdx + 1);
    numLeft.disabled = currentIdx === 0;
    numLeft.addEventListener('click', (e) => { e.stopPropagation(); _switchVariant(msgElement, variants, currentIdx - 1); });
    nav.appendChild(numLeft);

    const slash = document.createElement('span');
    slash.className = 'variant-slash';
    slash.textContent = '/';
    nav.appendChild(slash);

    const numRight = document.createElement('button');
    numRight.className = 'variant-num';
    numRight.textContent = String(variants.length);
    numRight.disabled = currentIdx === variants.length - 1;
    numRight.addEventListener('click', (e) => { e.stopPropagation(); _switchVariant(msgElement, variants, currentIdx + 1); });
    nav.appendChild(numRight);

    // > button
    const nextBtn = document.createElement('button');
    nextBtn.className = 'variant-btn';
    nextBtn.textContent = '>';
    nextBtn.disabled = currentIdx === variants.length - 1;
    nextBtn.addEventListener('click', (e) => { e.stopPropagation(); _switchVariant(msgElement, variants, currentIdx + 1); });
    nav.appendChild(nextBtn);

    // Insert into the .role header
    const roleEl = msgElement.querySelector('.role');
    if (roleEl) {
      roleEl.appendChild(nav);
    } else {
      msgElement.appendChild(nav);
    }
  }

  function _switchVariant(msgElement, variants, newIdx) {
    if (newIdx < 0 || newIdx >= variants.length) return;
    const v = variants[newIdx];
    const body = msgElement.querySelector('.body');
    if (body) body.innerHTML = v.html;
    msgElement.dataset.raw = v.raw;
    msgElement.dataset.variantIndex = String(newIdx);
    if (window.hljs) {
      msgElement.querySelectorAll('pre code').forEach(block => window.hljs.highlightElement(block));
    }
    _renderVariantNav(msgElement, variants, newIdx);

    // Persist selected variant to server
    const sid = sessionModule.getCurrentSessionId();
    if (sid) {
      fetch(`${API_BASE}/api/session/${sid}/update-last-meta`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ metadata: { variantIndex: newIdx } })
      }).catch(e => console.warn('update-last-meta (variantIndex) failed:', e));
    }
  }

  export async function forkFrom(aiMsgElement) {
    const box = document.getElementById('chat-history');
    const allMsgs = Array.from(box.querySelectorAll('.msg'));
    const aiIndex = allMsgs.indexOf(aiMsgElement);
    if (aiIndex < 0) return;

    const sessionId = sessionModule.getCurrentSessionId();
    if (!sessionId) return;

    const keepCount = aiIndex + 1;

    try {
      const res = await fetch(`${API_BASE}/api/session/${sessionId}/fork`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keep_count: keepCount }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();

      await sessionModule.loadSessions();
      await sessionModule.selectSession(data.id);
      if (uiModule) uiModule.showToast(`Forked → ${data.name}`);
    } catch (err) {
      console.error('Fork failed:', err);
      if (uiModule) uiModule.showError('Fork failed: ' + err.message);
    }
  }

  /**
   * Check for pending/completed research after page refresh or session switch.
   * If research is still running, show a spinner and poll until done.
   * If research is done, fetch result and render it.
   */
  export async function checkPendingResearch(sessionId) {
    if (!sessionId) return;
    try {
      const res = await fetch(`${API_BASE}/api/research/status/${sessionId}`);
      if (!res.ok) return; // 404 = no research for this session
      const data = await res.json();

      if (data.status === 'done') {
        // Fetch and render the completed result
        _notifyResearchComplete(sessionId, data.query || '');
        if (sessionModule && sessionModule.clearResearching) sessionModule.clearResearching(sessionId);
        const resultRes = await fetch(`${API_BASE}/api/research/result/${sessionId}`, { method: 'POST' });
        if (resultRes.ok) {
          const resultData = await resultRes.json();
          if (resultData.result) {
            // Skip if history already has a research message for this session
            if (document.querySelector(`#chat-history .msg-ai[data-research-session="${sessionId}"]`)) return;

            var srcBox = '';
            if (resultData.sources && resultData.sources.length > 0) {
              srcBox = _buildSourcesBox(resultData.sources, 'research');
            }
            var findingsBox = chatRenderer.buildFindingsBox(resultData.raw_findings);
            var cleanResult = resultData.result;
            // Build DOM directly to avoid double-processing through addMessage
            chatRenderer.hideWelcomeScreen();
            var _box = document.getElementById('chat-history');
            if (_box) {
              var _wrap = document.createElement('div');
              _wrap.className = 'msg msg-ai';
              _wrap.dataset.researchSession = sessionId;
              var _role = document.createElement('div');
              _role.className = 'role';
              var _meta = sessionModule.getSessions().find(function(s) { return s.id === sessionId; });
              _role.textContent = _shortModel(_meta?.model);
              _applyModelColor(_role, _meta?.model);
              _role.appendChild(chatRenderer.roleTimestamp());
              var _body = document.createElement('div');
              _body.className = 'body';
              _body.innerHTML = srcBox + markdownModule.processWithThinking(
                markdownModule.squashOutsideCode(cleanResult)
              ) + findingsBox;
              _wrap.dataset.raw = cleanResult;
              _wrap.appendChild(_role);
              _wrap.appendChild(_body);
              _wrap.appendChild(chatRenderer.createMsgFooter(_wrap));
              _appendViewReportLink(_wrap, sessionId);
              _box.appendChild(_wrap);
              if (window.hljs) _wrap.querySelectorAll('pre code').forEach(function(b) { window.hljs.highlightElement(b); });
              uiModule.scrollHistory();
            }
          }
        }
        return;
      }

      if (data.status !== 'running') return;

      // Don't show reconnect UI if we've already switched away
      if (sessionModule.getCurrentSessionId() !== sessionId) return;

      // Research is still running — show reconnect UI with spinner
      const box = document.getElementById('chat-history');
      if (!box) return;

      const holder = document.createElement('div');
      holder.className = 'msg msg-ai research-reconnect';
      holder.dataset.researchSession = sessionId;
      const roleTs = new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
      const agentMeta = sessionModule.getSessions().find(s => s.id === sessionModule.getCurrentSessionId());
      const agentModelLabel = _shortModel(agentMeta?.model);
      holder.innerHTML = `<div class="role">${agentModelLabel} <span class="role-timestamp">${roleTs}</span></div><div class="body"></div>`;
      _applyModelColor(holder.querySelector('.role'), agentMeta?.model);
      box.appendChild(holder);

      const bodyDiv = holder.querySelector('.body');
      const spinner = spinnerModule.create('Reconnecting to research...', 'right');
      bodyDiv.appendChild(spinner.createElement());
      spinner.start();

      // Update spinner with current progress if available
      function updateSpinnerFromProgress(progress) {
        if (!progress || !progress.phase) return;
        const rp = progress;
        if (rp.phase === 'probing') {
          spinner.updateMessage(`Verifying model: ${rp.model || '?'}`);
        } else if (rp.phase === 'planning') {
          spinner.updateMessage('Analyzing question & planning research strategy');
        } else if (rp.phase === 'searching') {
          const q = rp.queries ? `${rp.queries} queries` : '';
          const s = rp.total_sources ? ` · ${rp.total_sources} sources` : '';
          spinner.updateMessage(`Round ${rp.round || '?'}: Searching${q ? ' (' + q + ')' : ''}${s}`);
        } else if (rp.phase === 'reading') {
          spinner.updateMessage(rp.title ? `Reading: ${rp.title}` : `Round ${rp.round || '?'}: Reading ${rp.new_sources || ''} pages · ${rp.total_sources || 0} sources total`);
        } else if (rp.phase === 'analyzing') {
          spinner.updateMessage(`Round ${rp.round || '?'}: Analyzing ${rp.total_findings || 0} findings`);
        } else if (rp.phase === 'writing') {
          spinner.updateMessage(`Writing report · ${rp.total_sources || 0} sources`);
        }
      }

      updateSpinnerFromProgress(data.progress);
      _researchingStreamIds.add(sessionId);
      if (sessionModule && sessionModule.markResearching) sessionModule.markResearching(sessionId);

      // Restore research timer from started_at
      if (data.started_at && spinner && spinner.element) {
        _researchStartTime = data.started_at * 1000;
        _researchAvgDuration = data.avg_duration || null;
        _researchTimerEl = document.createElement('div');
        _researchTimerEl.className = 'research-timer';
        _researchTimerEl.style.cssText = 'font-size:0.8em; opacity:0.6; margin-top:4px; font-family:monospace;';
        spinner.element.parentNode.insertBefore(_researchTimerEl, spinner.element.nextSibling);
        _researchTimerInterval = setInterval(() => {
          if (!_researchTimerEl) return;
          var elapsed = Math.floor((Date.now() - _researchStartTime) / 1000);
          var mm = String(Math.floor(elapsed / 60)).padStart(2, '0');
          var ss = String(elapsed % 60).padStart(2, '0');
          var txt = mm + ':' + ss;
          if (_researchAvgDuration) {
            var avgM = String(Math.floor(_researchAvgDuration / 60)).padStart(2, '0');
            var avgS = String(Math.round(_researchAvgDuration % 60)).padStart(2, '0');
            txt += ' / avg ' + avgM + ':' + avgS;
          }
          _researchTimerEl.textContent = txt;
        }, 1000);
        // Reconnect synapse — seed it with whatever progress is already known
        try {
          _researchSynapse = createResearchSynapse(spinner.element.parentNode, {
            query: data.query || '',
            startedAt: _researchStartTime,
          });
          if (_researchSynapse.element && _researchTimerEl) {
            spinner.element.parentNode.insertBefore(_researchSynapse.element, _researchTimerEl);
          }
          if (data.progress) {
            _researchSynapse.setPhase(data.progress.phase, data.progress);
            if (typeof data.progress.round === 'number') _researchSynapse.setRound(data.progress.round);
            if (typeof data.progress.total_sources === 'number') _researchSynapse.setSourceCount(data.progress.total_sources);
          }
        } catch (e) { console.warn('synapse reconnect failed', e); }
      }

      // Poll for completion
      const pollInterval = setInterval(async () => {
        // Stop polling if user switched to a different session
        if (sessionModule.getCurrentSessionId() !== sessionId) {
          clearInterval(pollInterval);
          spinner.destroy();
          _clearResearchTimer();
          if (holder.parentNode) holder.remove();
          _researchingStreamIds.delete(sessionId);
          if (_researchingStreamIds.size === 0) {
            var _rToggleP = document.getElementById('research-toggle-btn');
            if (_rToggleP) _rToggleP.classList.remove('research-running');
          }
          return;
        }
        try {
          const pollRes = await fetch(`${API_BASE}/api/research/status/${sessionId}`);
          if (!pollRes.ok) {
            clearInterval(pollInterval);
            spinner.destroy();
            _clearResearchTimer();
            _researchingStreamIds.delete(sessionId);
            if (sessionModule && sessionModule.clearResearching) sessionModule.clearResearching(sessionId);
            return;
          }
          const pollData = await pollRes.json();
          updateSpinnerFromProgress(pollData.progress);
          if (_researchSynapse && pollData.progress) {
            _researchSynapse.setPhase(pollData.progress.phase, pollData.progress);
            if (typeof pollData.progress.round === 'number') _researchSynapse.setRound(pollData.progress.round);
            if (typeof pollData.progress.total_sources === 'number') _researchSynapse.setSourceCount(pollData.progress.total_sources);
          }

          if (pollData.status !== 'running') {
            clearInterval(pollInterval);
            spinner.destroy();
            _clearResearchTimer();
            _researchingStreamIds.delete(sessionId);
            if (sessionModule && sessionModule.clearResearching) sessionModule.clearResearching(sessionId);

            if (pollData.status === 'done') {
              _notifyResearchComplete(sessionId, data.query || '');
              const rRes = await fetch(`${API_BASE}/api/research/result/${sessionId}`, { method: 'POST' });
              if (rRes.ok) {
                const rData = await rRes.json();
                if (rData.result) {
                  var srcHtml = '';
                  if (rData.sources && rData.sources.length > 0) {
                    srcHtml = _buildSourcesBox(rData.sources, 'research');
                  }
                  var findingsHtml = chatRenderer.buildFindingsBox(rData.raw_findings);
                  bodyDiv.innerHTML = srcHtml + markdownModule.processWithThinking(
                    markdownModule.squashOutsideCode(rData.result)
                  ) + findingsHtml;
                  holder.dataset.raw = rData.result;
                  _appendViewReportLink(holder, sessionId);
                  if (window.hljs) {
                    holder.querySelectorAll('pre code').forEach(b => window.hljs.highlightElement(b));
                  }
                }
              }
            } else {
              bodyDiv.innerHTML = '<i style="color: var(--color-error);">[Research ' + pollData.status + ']</i>';
            }
          }
        } catch (e) {
          console.error('Research poll error:', e);
        }
      }, 2000);
    } catch (e) {
      // No research pending, that's fine
    }
  }

  /** Set a display override for the next user message bubble */
  export function setDisplayOverride(text) {
    _displayOverride = text;
  }

  /** Hide the user bubble for the next submit (e.g. continue after stop) */
  export function setHideUserBubble() {
    _hideUserBubble = true;
  }

  /** Set the AI element to merge with the next streamed response (continue after stop) */
  export function setPendingContinue(el) {
    _pendingContinue = el;
  }

  /**
   * Delete an AI message and its preceding user message from the conversation.
   */
  export async function deleteMessage(msgElement) {
    const box = document.getElementById('chat-history');
    const allMsgs = Array.from(box.querySelectorAll('.msg'));
    const clickedIndex = allMsgs.indexOf(msgElement);
    if (clickedIndex < 0) return;

    // No early-out on a missing session: an output shown before any model was
    // selected (issue #1428) has no session/persisted rows, but its "x" must
    // still remove it. We only need the session id for the server-side delete
    // below; without one we fall back to removing the DOM.
    const sessionId = sessionModule.getCurrentSessionId();

    const clickedIsUser = msgElement.classList.contains('msg-user');

    // Find the user+AI pair
    let userIndex = -1;
    let aiIndex = -1;
    if (clickedIsUser) {
      userIndex = clickedIndex;
      // Find the following AI message
      for (let i = clickedIndex + 1; i < allMsgs.length; i++) {
        if (allMsgs[i].classList.contains('msg-ai') && !allMsgs[i].classList.contains('msg-continuation')) {
          aiIndex = i;
          break;
        }
        if (allMsgs[i].classList.contains('msg-user')) break; // next user msg, no AI response
      }
    } else {
      // If clicked on a continuation, walk back to the main AI message
      let mainAiIndex = clickedIndex;
      if (allMsgs[mainAiIndex].classList.contains('msg-continuation')) {
        for (let i = mainAiIndex - 1; i >= 0; i--) {
          if (allMsgs[i].classList.contains('msg-ai') && !allMsgs[i].classList.contains('msg-continuation')) {
            mainAiIndex = i;
            break;
          }
        }
      }
      aiIndex = mainAiIndex;
      // Find the preceding user message
      for (let i = aiIndex - 1; i >= 0; i--) {
        if (allMsgs[i].classList.contains('msg-user')) {
          userIndex = i;
          break;
        }
      }
    }

    // Collect DB message IDs and DOM elements to remove
    const msgIds = [];
    const domToRemove = [];

    // Add the user message if found
    if (userIndex >= 0) {
      domToRemove.push(allMsgs[userIndex]);
      const uid = allMsgs[userIndex].dataset.dbId;
      if (uid) msgIds.push(uid);
    }

    // Add the AI message if found
    if (aiIndex >= 0) {
      domToRemove.push(allMsgs[aiIndex]);
      const aid = allMsgs[aiIndex].dataset.dbId;
      if (aid) msgIds.push(aid);

      const aiEl = allMsgs[aiIndex];
      // Also remove agent-thread elements BETWEEN user and AI
      if (userIndex >= 0) {
        let between = allMsgs[userIndex].nextElementSibling;
        while (between && between !== aiEl) {
          domToRemove.push(between);
          between = between.nextElementSibling;
        }
      }
      // Walk forward from the AI element to remove continuations and tool bubbles
      let sibling = aiEl.nextElementSibling;
      while (sibling) {
        if (sibling.classList.contains('msg-user') ||
            (sibling.classList.contains('msg-ai') && !sibling.classList.contains('msg-continuation'))) {
          break;
        }
        domToRemove.push(sibling);
        sibling = sibling.nextElementSibling;
      }
    }

    if (!msgIds.length || !sessionId) {
      // No persisted rows to delete (no DB IDs, or no session at all — e.g. an
      // error output shown before a model was selected, #1428). Just remove the
      // DOM so the "x" works regardless.
      domToRemove.forEach(el => el.remove());
      if (uiModule) uiModule.showToast('Message deleted');
      return;
    }

    try {
      const res = await fetch(`${API_BASE}/api/session/${sessionId}/delete-messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ msg_ids: msgIds })
      });
      if (!res.ok) throw new Error('Server error ' + res.status);
      domToRemove.forEach(el => el.remove());
      if (uiModule) uiModule.showToast('Message deleted');
    } catch (err) {
      console.error('Delete failed:', err);
      if (uiModule) uiModule.showError('Delete failed: ' + err.message);
    }
  }

  /**
   * Edit an AI message inline. Makes the body contentEditable, saves to DB on confirm.
   */
  export async function editAIMessage(msgElement) {
    const body = msgElement.querySelector('.body');
    if (!body) return;

    const isEditing = body.contentEditable === 'true' || body.contentEditable === 'plaintext-only';
    if (isEditing) return; // already editing

    const originalRaw = msgElement.dataset.raw || body.textContent || '';

    // Create editable textarea overlay
    const textarea = document.createElement('textarea');
    textarea.className = 'msg-edit-textarea';
    textarea.value = originalRaw;
    textarea.style.width = '100%';
    textarea.style.minHeight = Math.max(100, body.offsetHeight) + 'px';
    body.style.display = 'none';
    body.parentNode.insertBefore(textarea, body.nextSibling);
    textarea.focus();

    // Add save/cancel bar
    const bar = document.createElement('div');
    bar.className = 'msg-edit-bar';
    const saveBtn = document.createElement('button');
    saveBtn.className = 'msg-edit-save';
    saveBtn.textContent = 'Save';
    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'msg-edit-cancel';
    cancelBtn.textContent = 'Cancel';
    bar.appendChild(saveBtn);
    bar.appendChild(cancelBtn);
    textarea.parentNode.insertBefore(bar, textarea.nextSibling);

    function cleanup() {
      textarea.remove();
      bar.remove();
      body.style.display = '';
    }

    cancelBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      cleanup();
    });

    saveBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const newContent = textarea.value;
      if (newContent === originalRaw) { cleanup(); return; }

      const msgId = msgElement.dataset.dbId;
      if (!msgId) { if (uiModule) uiModule.showError('Cannot edit: message ID not found'); cleanup(); return; }

      const sessionId = sessionModule.getCurrentSessionId();
      if (!sessionId) { cleanup(); return; }

      try {
        const res = await fetch(`${API_BASE}/api/session/${sessionId}/edit-message`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ msg_id: msgId, content: newContent }),
        });
        if (!res.ok) throw new Error('Server error ' + res.status);

        // Re-render body with markdown
        body.innerHTML = markdownModule.processWithThinking(markdownModule.squashOutsideCode(newContent));
        msgElement.dataset.raw = newContent;

        // Add edited indicator if not already present
        if (!msgElement.querySelector('.edited-indicator')) {
          const indicator = document.createElement('div');
          indicator.className = 'edited-indicator';
          indicator.textContent = '[Message edited]';
          body.parentNode.insertBefore(indicator, body.nextSibling);
        }

        cleanup();
        if (uiModule) uiModule.showToast('Message edited');
      } catch (err) {
        console.error('Edit failed:', err);
        if (uiModule) uiModule.showError('Edit failed: ' + err.message);
      }
    });
  }

  /**
   * Rewrite the AI's last response with a specific instruction.
   * Uses the lightweight /api/rewrite endpoint — no tools, no agent loop.
   * Just rewrites the text of the last AI bubble.
   */
  export async function rewriteWith(aiMsgElement, instruction) {
    const sessionId = sessionModule.getCurrentSessionId();
    if (!sessionId) return;

    // Get the original text from the AI bubble
    const oldRaw = aiMsgElement.dataset.raw || aiMsgElement.querySelector('.body')?.textContent || '';
    const oldHtml = aiMsgElement.querySelector('.body')?.innerHTML || '';

    if (!oldRaw.trim()) {
      if (uiModule) uiModule.showError('No text to rewrite');
      return;
    }

    // Save current response as a variant
    let variants = [];
    try { variants = JSON.parse(aiMsgElement.dataset.variants || '[]'); } catch(_) {}
    if (variants.length === 0) {
      variants.push({ raw: oldRaw, html: oldHtml, label: 'original' });
    }

    // Determine label from instruction
    let varLabel = 'rewrite';
    if (instruction.includes('shorter')) varLabel = 'shorter';
    else if (instruction.includes('simpler')) varLabel = 'simpler';

    // Clear the bubble and show a whirlpool spinner while we wait for the
    // rewrite (replaces the old "Rewriting..." text).
    const bodyEl = aiMsgElement.querySelector('.body');
    let _rwSpin = null;
    if (bodyEl) {
      bodyEl.innerHTML = '';
      _rwSpin = spinnerModule.createWhirlpool(18);
      _rwSpin.element.style.margin = '4px 0';
      bodyEl.appendChild(_rwSpin.element);
    }
    // Stop + detach the spinner (called once real content starts rendering, and
    // on the failure path so it never spins forever).
    const _killRwSpin = () => { if (_rwSpin) { try { _rwSpin.destroy(); } catch (_) {} _rwSpin = null; } };

    try {
      const res = await fetch(`${API_BASE}/api/rewrite`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          original_text: oldRaw,
          instruction: instruction,
        }),
      });

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let newText = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const payload = line.slice(6).trim();
          if (payload === '[DONE]') continue;
          try {
            const data = JSON.parse(payload);
            // The endpoint streams `event: error\ndata: {error,status}` on
            // failure — surface it instead of silently hanging on "Rewriting…".
            if (data.error) {
              throw new Error(data.error || ('HTTP ' + (data.status || 500)));
            }
            // Reasoning tokens (vLLM --reasoning-parser: Qwen3 / DeepSeek-R1)
            // arrive as separate {delta, thinking:true} chunks. They are NOT
            // the rewrite — fold them away so they don't pollute the result.
            if (data.thinking) continue;
            if (data.delta) {
              newText += data.delta;
              _killRwSpin();
              if (bodyEl) {
                bodyEl.innerHTML = markdownModule.processWithThinking(
                  markdownModule.squashOutsideCode(newText)
                );
              }
            }
          } catch (e) {
            if (e instanceof Error && e.message) throw e;  // re-throw real errors
            /* ignore JSON parse noise */
          }
        }
      }

      // Strip any thinking markup from the answer. A reasoning model may emit
      // an inline <think>…</think> block, a bare </think> (no opener), or — when
      // its reasoning came via reasoning_content — a stray leading <think> that
      // never closes (so it would otherwise hide the whole answer). Peel all of
      // those off so what's left is just the rewritten text.
      const _stripThink = (t) => {
        t = t.replace(/<think>[\s\S]*?<\/think>/gi, '');   // complete blocks
        if (/<\/think>/i.test(t)) t = t.replace(/^[\s\S]*?<\/think>/i, '');  // reasoning w/o opener
        return t.replace(/<\/?think>/gi, '').trim();        // any orphan tag
      };
      newText = _stripThink(newText);

      // Nothing left after stripping (or an empty stream) → real failure, not a
      // blank bubble.
      if (!newText.trim()) {
        throw new Error('model returned no rewritten text');
      }

      // Update the element's raw text
      if (newText) {
        aiMsgElement.dataset.raw = newText;
        // Final render with proper markdown
        if (bodyEl) {
          bodyEl.innerHTML = markdownModule.processWithThinking(
            markdownModule.squashOutsideCode(newText)
          );
        }

        // Save the new response as a variant
        variants.push({ raw: newText, html: bodyEl ? bodyEl.innerHTML : '', label: varLabel });
        aiMsgElement.dataset.variants = JSON.stringify(variants);
        aiMsgElement.dataset.variantIndex = String(variants.length - 1);

        // Persist variant metadata to server
        try {
          await fetch(`${API_BASE}/api/session/${sessionId}/update-last-meta`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ metadata: { variants: variants, variantIndex: variants.length - 1 } }),
          });
        } catch (_) {}

        // Re-render variant navigation
        _renderVariantNav(aiMsgElement, variants, variants.length - 1);
      }

      if (uiModule) uiModule.scrollHistory();

    } catch (err) {
      console.error('Rewrite failed:', err);
      _killRwSpin();
      // Restore original content on failure
      if (bodyEl) bodyEl.innerHTML = oldHtml;
      if (uiModule) uiModule.showError('Rewrite failed: ' + err.message);
    }
  }

  /**
   * Continue the AI's response from where it left off.
   */
  export async function continueFrom(aiMsgElement) {
    const sessionId = sessionModule.getCurrentSessionId();
    if (!sessionId) return;

    const messageInput = uiModule.el('message');
    if (messageInput) {
      messageInput.value = 'Continue from where you left off.';
      const submitBtn = document.querySelector('.send-btn');
      if (submitBtn) submitBtn.click();
    }
  }

  // Open a chat attachment in the right place: images → Gallery editor; PDFs &
  // text/code/markdown → Documents viewer; anything else → raw file. A given
  // upload's imported document is reused (cached by upload id) so clicking it
  // again re-opens the same doc instead of making duplicates.
  const _attachDocCache = new Map();  // upload id -> doc id
  function _attachLang(name) {
    const m = (name || '').toLowerCase().match(/\.([a-z0-9]+)$/);
    const ext = m ? m[1] : '';
    const map = { md:'markdown', markdown:'markdown', js:'javascript', ts:'typescript',
      jsx:'javascript', tsx:'typescript', py:'python', rb:'ruby', go:'go', rs:'rust',
      java:'java', c:'c', cpp:'cpp', h:'c', hpp:'cpp', cs:'csharp', php:'php', html:'html',
      htm:'html', css:'css', scss:'scss', json:'json', yaml:'yaml', yml:'yaml', sh:'bash',
      bash:'bash', sql:'sql', csv:'csv', xml:'xml' };
    return map[ext] || '';
  }
  async function openAttachment(att, isImage) {
    if (!att || !att.id) return;
    const id = att.id, name = att.name || '', mime = att.mime || '';
    const url = `${API_BASE}/api/upload/${id}`;

    // Images → Gallery editor.
    if (isImage) {
      try {
        const gx = await import('./galleryEditor.js');
        if (gx.openEditor) { gx.openEditor(url, id, null, name); return; }
      } catch (e) { console.warn('gallery open failed', e); }
      window.open(url, '_blank');
      return;
    }

    const isPdf = mime === 'application/pdf' || /\.pdf$/i.test(name);
    const TEXT_EXT = /\.(txt|md|markdown|js|ts|jsx|tsx|py|rb|go|rs|java|c|cpp|h|hpp|cs|php|html?|css|scss|sass|less|json|ya?ml|toml|ini|conf|env|sh|bash|sql|csv|tsv|xml|log|vue|svelte)$/i;
    const isTextDoc = TEXT_EXT.test(name) || /^text\//.test(mime);
    if (!isPdf && !isTextDoc) { window.open(url, '_blank'); return; }  // binary/unknown → raw

    // Reuse the doc we already imported for this upload, if it still loads.
    const cached = _attachDocCache.get(id);
    if (cached) {
      try {
        documentModule.openPanel && documentModule.openPanel();
        await documentModule.loadDocument(cached);
        return;
      } catch (_) { _attachDocCache.delete(id); }
    }

    // Need a session to attach the doc to (bare-session fallback, same as compose).
    let sid = '';
    try { sid = sessionModule.getCurrentSessionId() || ''; } catch (_) {}
    if (!sid) {
      try {
        const _fd = new FormData();
        _fd.append('name', name || 'Attachment');
        _fd.append('skip_validation', 'true');
        const r = await fetch(`${API_BASE}/api/session`, { method: 'POST', body: _fd, credentials: 'same-origin' });
        if (r.ok) { const d = await r.json(); if (d && d.id) { sid = d.id; if (sessionModule.loadSessions) await sessionModule.loadSessions(); } }
      } catch (_) {}
    }

    try {
      let doc;
      if (isPdf) {
        // import-pdf wants a fresh file upload — re-fetch the stored blob and post it.
        const blob = await (await fetch(url)).blob();
        const fd = new FormData();
        fd.append('file', blob, name || 'document.pdf');
        if (sid) fd.append('session_id', sid);
        const res = await fetch(`${API_BASE}/api/documents/import-pdf`, { method: 'POST', body: fd, credentials: 'same-origin' });
        if (!res.ok) throw new Error('import-pdf ' + res.status);
        doc = await res.json();
      } else {
        const text = await (await fetch(url)).text();
        const res = await fetch(`${API_BASE}/api/document`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: sid || null, title: name.replace(/\.[^.]+$/, '') || 'Document', content: text, language: _attachLang(name) }),
        });
        if (!res.ok) throw new Error('document ' + res.status);
        doc = await res.json();
      }
      if (doc && doc.id) {
        _attachDocCache.set(id, doc.id);
        documentModule.openPanel && documentModule.openPanel();
        if (documentModule.injectFreshDoc) documentModule.injectFreshDoc(doc);
        else await documentModule.loadDocument(doc.id);
      }
    } catch (e) {
      console.error('open attachment as document failed', e);
      import('./ui.js').then(m => m.showError && m.showError('Could not open attachment')).catch(() => {});
      window.open(url, '_blank');  // fallback so the file is still reachable
    }
  }

  // Public API
  const chatModule = {
    init,
    initListeners,
    openAttachment,
    addMessage: chatRenderer.addMessage,
    displayMetrics: chatRenderer.displayMetrics,
    handleChatSubmit,
    abortCurrentRequest,
    detachCurrentStream,
    checkBackgroundStream,
    hideWelcomeScreen: chatRenderer.hideWelcomeScreen,
    showWelcomeScreen: chatRenderer.showWelcomeScreen,
    checkPendingResearch,
    getImageCost: chatRenderer.getImageCost,
    setDisplayOverride,
    setHideUserBubble,
    setPendingContinue,
    regenerateFrom,
    forkFrom,
    editUserMessage,
    editAIMessage,
    resendUserMessage,
    deleteMessage,
    rewriteWith,
    continueFrom,
    _appendViewReportLink,
    hasActiveStream,
  };

  // Single delegated handler for tool-call fold/expand. One listener on
  // document.body covers every .agent-thread-node — running, completed,
  // streaming, history-rendered, compare-mode, all of them. Re-attaching
  // per-node listeners on every innerHTML rewrite was the source of the
  // "needs many clicks" bug.
  if (!window.__odysseus_thread_click_bound) {
    document.body.addEventListener('click', (e) => {
      const header = e.target.closest('.agent-thread-header');
      if (!header) return;
      const node = header.closest('.agent-thread-node');
      if (!node) return;
      node.classList.toggle('open');
    });
    window.__odysseus_thread_click_bound = true;
  }

  export default chatModule;
  window.chatModule = chatModule;
