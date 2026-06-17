/**
 * Prompt Enhancer Widget
 * Injects a ✨ Enhance button into the chat input area.
 * Clicking it sends the current draft to the AI with a rewrite instruction
 * and replaces the input with the improved version.
 *
 * Uses window.OdysseusPkg (set by pkg-api.js before any widget loads).
 */
(function () {
  const api = window.OdysseusPkg;
  if (!api) {
    console.warn('[prompt-enhancer] OdysseusPkg not available — is pkg-api.js loaded?');
    return;
  }

  // ── Build the button ──────────────────────────────────────────────────────
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'input-icon-btn pkg-enhance-btn';
  btn.title = 'Enhance prompt with AI';
  btn.setAttribute('aria-label', 'Enhance prompt');
  btn.innerHTML = `
    <svg class="pkg-enhance-icon" width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 2l2.4 7.4H22l-6.2 4.5 2.4 7.4L12 17l-6.2 4.3 2.4-7.4L2 9.4h7.6z"/>
    </svg>
    <span class="pkg-enhance-label">Enhance</span>
  `;

  // ── Spinner SVG (shown while processing) ──────────────────────────────────
  const SPINNER = `
    <svg class="pkg-enhance-spinner" width="14" height="14" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
      <path d="M12 2a10 10 0 0 1 10 10"/>
    </svg>
    <span class="pkg-enhance-label">Thinking…</span>
  `;

  // ── Click handler ─────────────────────────────────────────────────────────
  let _busy = false;
  btn.addEventListener('click', async () => {
    if (_busy) return;

    const draft = api.getChatInput().trim();
    if (!draft) {
      // Pulse the button to hint the user should type something first
      btn.classList.add('pkg-enhance-empty');
      setTimeout(() => btn.classList.remove('pkg-enhance-empty'), 600);
      return;
    }

    _busy = true;
    const saved = btn.innerHTML;
    btn.innerHTML = SPINNER;
    btn.disabled = true;

    try {
      const enhanced = await api.callLLM(
        // System prompt
        "You are an expert prompt engineer. " +
        "Rewrite the user's draft message to be clearer, more specific, and better structured for an AI assistant. " +
        "Preserve the user's intent exactly — do not add unasked-for topics. " +
        "Return ONLY the improved prompt text. No explanation, no quotes, no preamble.",
        // User message
        draft
      );
      api.setChatInput(enhanced.trim());
    } catch (err) {
      console.error('[prompt-enhancer] LLM call failed:', err);
      // Show a brief error state on the button
      btn.innerHTML = `<span style="color:var(--red,#e06c75);font-size:11px;">✕ ${err.message}</span>`;
      setTimeout(() => { btn.innerHTML = saved; }, 3000);
    } finally {
      if (!btn.innerHTML.includes('✕')) btn.innerHTML = saved;
      btn.disabled = false;
      _busy = false;
    }
  });

  // ── Inject into the chatInput slot ────────────────────────────────────────
  api.addWidget('chatInput', btn);
})();
