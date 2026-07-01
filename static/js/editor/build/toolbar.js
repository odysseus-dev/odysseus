/**
 * Build the editor's left-side tool palette.
 *
 * Pure DOM construction — no module state. The big tool-switch logic
 * (cursor swap, control-section toggle, transform entry, inpaint
 * mask plumbing, etc.) stays in the caller and arrives here as the
 * `onSelectTool` callback.
 *
 * @param {{
 *   currentTool: string,
 *   onSelectTool: (toolId: string, btn: HTMLButtonElement, toolbar: HTMLDivElement) => void,
 *   onClearSelection: (which: 'lasso'|'wand') => void,
 * }} ctx
 * @returns {{ toolbar: HTMLDivElement, toolKeyMap: Record<string,string> }}
 */
export function buildToolbar({ currentTool, onSelectTool, onClearSelection }) {
  const toolbar = document.createElement('div');
  toolbar.className = 'ge-toolbar';
  const tools = [
    { id: 'move', label: window.t('Move'), icon: '✥', key: 'V' },
    { id: 'crop', label: window.t('Crop'), icon: '✂', key: 'C' },
    { id: 'transform', label: window.t('Transform'), icon: '⤢', key: 'T' },
    { sep: true },
    { id: 'brush', label: window.t('Brush'), icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.06 11.9l8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/><path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04a3.01 3.01 0 0 0-3-3.02z"/></svg>', key: 'B' },
    { id: 'eraser', label: window.t('Eraser'), icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19.4 14.6 14.6 19.4a2 2 0 0 1-2.83 0L4.6 12.23a2 2 0 0 1 0-2.83l7.17-7.17a2 2 0 0 1 2.83 0l4.8 4.8a2 2 0 0 1 0 2.83Z"/><line x1="22" y1="21" x2="7" y2="21"/><line x1="14" y1="3" x2="9" y2="8"/></svg>', key: 'E' },
    { sep: true },
    { id: 'clone', label: window.t('Clone'), icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="9" r="3"/><path d="M9 12l-3 4h12l-3-4"/><path d="M4 20h16"/></svg>', key: 'K' },
    { id: 'lasso', label: window.t('Lasso'), icon: '⟡', key: 'L' },
    { id: 'wand', label: window.t('Wand'), icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 4V2"/><path d="M15 16v-2"/><path d="M8 9h2"/><path d="M20 9h2"/><path d="M17.8 11.8L19 13"/><path d="M15 9h0"/><path d="M17.8 6.2L19 5"/><path d="M3 21l9-9"/><path d="M12.2 6.2L11 5"/></svg>', key: 'W' },
    { sep: true },
    { id: 'inpaint', label: window.t('Inpaint'), ai: true, icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.06 11.9l8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/><path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04a3.01 3.01 0 0 0-3-3.02z"/></svg>', key: 'M' },
    { id: 'rembg', ai: true, label: window.t('Bg Remove'), icon: '✄' },
    { id: 'sharpen', ai: true, label: window.t('Sharpen'), icon: '◈', key: 'S' },
  ];
  const toolKeyMap = {};
  for (const t of tools) {
    if (t.sep) {
      const sep = document.createElement('div');
      sep.className = 'ge-tool-sep';
      sep.textContent = t.label;
      toolbar.appendChild(sep);
      continue;
    }
    if (t.key) toolKeyMap[t.key.toLowerCase()] = t.id;
    const btn = document.createElement('button');
    btn.className = 'ge-tool-btn' + (t.id === currentTool ? ' active' : '');
    btn.dataset.tool = t.id;
    btn.title = t.label + (t.key ? ` (${t.key})` : '');
    // Heavy 4-point AI star marker for AI-backed tools — sits just to
    // the left of the icon so the user can spot AI vs local tools at a
    // glance now that the "AI Tools" separator is gone.
    const aiStar = t.ai ? '<span class="ge-tool-ai" title="AI">✦</span>' : '';
    btn.classList.toggle('is-ai', !!t.ai);
    // Selection-clear badge — rendered only for tools that can hold a
    // selection (lasso, wand). Inpaint masks are first-class sub-layers
    // now so they get their own delete-X in the layer panel.
    const clearBadge = (t.id === 'lasso' || t.id === 'wand')
      ? '<span class="ge-tool-clear" title="' + window.t('Clear selection') + '" data-clear-tool="' + t.id + '">' +
          '<svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>' +
        '</span>'
      : '';
    btn.innerHTML = `${aiStar}<span class="ge-tool-icon"${t.small ? ' style="font-size:14px"' : ''}>${t.icon}</span><span class="ge-tool-label">${t.label}</span>${clearBadge}`;
    // Clear-badge click stops propagation so the tool itself doesn't
    // toggle; the actual clear is handled by the caller.
    btn.querySelector('.ge-tool-clear')?.addEventListener('click', (ev) => {
      ev.stopPropagation();
      onClearSelection(ev.currentTarget.dataset.clearTool);
    });
    btn.addEventListener('click', () => onSelectTool(t.id, btn, toolbar));
    toolbar.appendChild(btn);
  }
  return { toolbar, toolKeyMap };
}
