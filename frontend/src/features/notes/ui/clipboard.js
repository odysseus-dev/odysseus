/** Note clipboard feedback and the Copy/Agent corner menu. */
import { _serializeNoteForCopy } from '../domain/noteRules.js';
export function createClipboard({
  _agentSolveNote,
  bindMenuDismiss,
  dismissOrRemove,
  state,
  topPortalZ,
  uiModule
}) {
  function _openNoteCornerMenu(btn) {
    document.querySelectorAll('.note-corner-menu-dropdown').forEach(dismissOrRemove);
    const id = btn.dataset.noteId;
    const note = state.notes.find(n => n.id === id);
    if (!note) return;
    const menu = document.createElement('div');
    menu.className = 'note-corner-menu-dropdown';
    menu.innerHTML = `
    <button type="button" class="ncm-item" data-act="copy">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
      <span>Copy</span>
    </button>
    <button type="button" class="ncm-item" data-act="agent">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8V4H8"/><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/></svg>
      <span>${note.agent_session_id ? 'Re-run agent' : 'Agent: solve this'}</span>
    </button>`;
    document.body.appendChild(menu);
    const r = btn.getBoundingClientRect();
    // Right-align to the ⋯ button, clamped to the viewport.
    const mw = 168;
    let left = Math.min(r.right - mw, window.innerWidth - mw - 8);
    left = Math.max(8, left);
    // Drop down by default; flip up if there isn't room below (the button
    // sits at the card's bottom edge now).
    const mh = menu.offsetHeight || 96;
    const below = window.innerHeight - r.bottom;
    const top = (below < mh + 8 && r.top > mh + 8) ? (r.top - mh - 4) : (r.bottom + 4);
    menu.style.cssText += `position:fixed;z-index:${topPortalZ()};top:${Math.round(top)}px;left:${Math.round(left)}px;`;
    const close = bindMenuDismiss(menu, () => { menu.remove(); });
    menu.querySelector('[data-act="copy"]').addEventListener('click', () => { close(); _copyNote(id, btn); });
    menu.querySelector('[data-act="agent"]').addEventListener('click', () => { close(); _agentSolveNote(id); });
  }

  async function _copyNote(noteId, btnEl) {
    const note = state.notes.find(n => n.id === noteId);
    if (!note) return false;
    const text = _serializeNoteForCopy(note);
    if (!text) return false;
    let ok = false;
    try {
      await navigator.clipboard.writeText(text);
      ok = true;
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed'; ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      try { ok = document.execCommand('copy'); } catch { ok = false; }
      ta.remove();
    }
    if (ok) {
      if (btnEl && !btnEl._copyFlashing) {
        const original = btnEl.innerHTML;
        btnEl._copyFlashing = true;
        btnEl.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
        btnEl.classList.add('copied');
        setTimeout(() => {
          btnEl.innerHTML = original;
          btnEl.classList.remove('copied');
          btnEl._copyFlashing = false;
        }, 1200);
      }
      uiModule.showToast?.('Copied');
    } else {
      uiModule.showError?.('Copy failed');
    }
    return ok;
  }

  return { _openNoteCornerMenu, _copyNote };
}
