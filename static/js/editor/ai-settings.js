/**
 * AI settings dialog.
 *
 * Holds the image-edit model, the AI agent (vision) model, and the opt-in
 * "Double-check AI edits" toggle. Opened from the Quick Edit gear button and
 * from the editor setup checklist; keeps the Quick Edit box itself uncluttered.
 */
export function createAiSettings({ container }) {
  const el = document.createElement('div');
  el.className = 'ge-ai-settings';
  el.hidden = true;
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  el.setAttribute('aria-label', 'AI settings');
  el.innerHTML = `
    <div class="ge-ai-settings-card">
      <div class="ge-ai-settings-head">
        <h3>AI settings</h3>
        <button type="button" class="ge-ai-settings-close" data-ai-settings-close aria-label="Close">×</button>
      </div>
      <div class="ge-ai-settings-body">
        <label class="ge-ai-settings-label" for="ge-ai-command-model">Image edit model</label>
        <select class="ge-ai-command-model" id="ge-ai-command-model" title="Image edit model" aria-label="Image edit model"></select>
        <label class="ge-ai-settings-label" for="ge-ai-command-agent-model">AI agent (vision) model</label>
        <select class="ge-ai-command-model" id="ge-ai-command-agent-model" title="Quick Edit agent model" aria-label="Quick Edit agent model"></select>
        <p class="ge-ai-settings-note">Recommended, not required — the vision agent plans edits and can double-check results.</p>
        <label class="ge-ai-settings-label" for="ge-look-strength">Look strength</label>
        <div class="ge-ai-settings-strength">
          <input type="range" id="ge-look-strength" min="0.5" max="2" step="0.1" value="1.4" aria-label="Look grade strength" />
          <span class="ge-ai-settings-strength-val" id="ge-look-strength-val">1.4×</span>
        </div>
        <p class="ge-ai-settings-note">How strongly named looks (Kodachrome, teal and orange, …) are graded.</p>
        <label class="ge-ai-settings-opt" title="Ask the vision model to review the result and retry on a clear failure">
          <input type="checkbox" id="ge-ai-command-selfcheck" /> Double-check AI edits
        </label>
      </div>
      <div class="ge-ai-settings-foot">
        <button type="button" class="ge-ai-settings-done" data-ai-settings-close>Done</button>
      </div>
    </div>`;

  function open(focusId) {
    el.hidden = false;
    if (focusId) {
      const target = document.getElementById(focusId);
      if (target) {
        try { target.focus({ preventScroll: true }); } catch (_) { target.focus(); }
      }
    }
  }
  function close() { el.hidden = true; }

  el.addEventListener('click', (e) => {
    if (e.target === el || e.target.closest('[data-ai-settings-close]')) {
      e.preventDefault();
      close();
    }
  });
  const onKey = (e) => { if (e.key === 'Escape' && !el.hidden) close(); };
  document.addEventListener('keydown', onKey);

  container.appendChild(el);

  const strengthInput = el.querySelector('#ge-look-strength');
  const strengthVal = el.querySelector('#ge-look-strength-val');
  if (strengthInput) {
    try {
      const saved = parseFloat(localStorage.getItem('ge-look-strength'));
      if (Number.isFinite(saved) && saved > 0) strengthInput.value = String(saved);
    } catch (_) {}
    if (strengthVal) strengthVal.textContent = Number(strengthInput.value).toFixed(1) + '×';
    strengthInput.addEventListener('input', () => {
      const v = Number(strengthInput.value);
      if (strengthVal) strengthVal.textContent = v.toFixed(1) + '×';
      try { localStorage.setItem('ge-look-strength', String(v)); } catch (_) {}
    });
  }

  function destroy() {
    document.removeEventListener('keydown', onKey);
    el.remove();
  }

  return { element: el, open, close, destroy };
}
