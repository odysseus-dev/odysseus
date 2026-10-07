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
 *   onClearSelection: (which: 'marquee'|'lasso'|'wand') => void,
 * }} ctx
 * @returns {{ toolbar: HTMLDivElement, toolKeyMap: Record<string,string> }}
 */
import { TOOL_SHORTCUTS } from '../tool-shortcuts.js';

export function buildToolbar({ currentTool, onSelectTool, onClearSelection }) {
  const toolbar = document.createElement('div');
  toolbar.className = 'ge-toolbar';
  const tools = [
    { id: 'move', label: 'Move', icon: '✥' },
    { id: 'hand', label: 'Hand', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 11V6a2 2 0 0 0-4 0v4"/><path d="M14 10V4a2 2 0 0 0-4 0v6"/><path d="M10 10V5a2 2 0 0 0-4 0v9"/><path d="M6 13.5 4.5 12A2.1 2.1 0 0 0 2 15l4.5 5A6 6 0 0 0 11 22h2a7 7 0 0 0 7-7v-4a2 2 0 0 0-4 0v1"/></svg>' },
    { id: 'crop', label: 'Crop', icon: '✂' },
    { id: 'transform', label: 'Transform', icon: '⤢' },
    { id: 'text', label: 'Text', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7V4h16v3"/><path d="M9 20h6"/><path d="M12 4v16"/></svg>' },
    { id: 'shape', label: 'Shape', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="12" height="12" rx="1"/><circle cx="17" cy="15" r="4"/></svg>' },
    { sep: true },
    { id: 'brush', label: 'Brush', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.06 11.9l8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/><path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04a3.01 3.01 0 0 0-3-3.02z"/></svg>' },
    { id: 'gradient', label: 'Gradient', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20 20 4"/><path d="M6 18 18 6" opacity=".45"/><path d="M8 16 16 8" opacity=".2"/></svg>' },
    { id: 'eraser', label: 'Eraser', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19.4 14.6 14.6 19.4a2 2 0 0 1-2.83 0L4.6 12.23a2 2 0 0 1 0-2.83l7.17-7.17a2 2 0 0 1 2.83 0l4.8 4.8a2 2 0 0 1 0 2.83Z"/><line x1="22" y1="21" x2="7" y2="21"/><line x1="14" y1="3" x2="9" y2="8"/></svg>' },
    { id: 'eyedropper', label: 'Eyedropper', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m19 3 2 2-9.5 9.5-3-3Z"/><path d="m8.5 11.5-5 5V21h4.5l5-5"/></svg>' },
    { id: 'smudge', label: 'Smudge', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 19c2-3 4-5 6-6 2-1 4-3 4-6 0-2-1-4-3-4s-3 2-3 4v5"/><path d="M9 13c-2 0-4 1-5 3-.7 1.2-.1 3 1.4 3H18c1.7 0 3-1.3 3-3 0-1.1-.9-2-2-2h-4"/></svg>' },
    { sep: true },
    { id: 'clone', label: 'Clone', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="9" r="3"/><path d="M9 12l-3 4h12l-3-4"/><path d="M4 20h16"/></svg>' },
    { id: 'heal', label: 'Healing', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="m5 19 14-14"/><path d="M7 5h4M9 3v4M13 17h4M15 15v4"/></svg>' },
    { id: 'dodge', label: 'Dodge', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="10" cy="10" r="6"/><path d="m14.5 14.5 6 6"/></svg>' },
    { id: 'burn', label: 'Burn', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 22c4 0 7-3 7-7 0-5-4-8-7-13-3 5-7 8-7 13 0 4 3 7 7 7Z"/><path d="M9 16c1.5 1 4.5 1 6 0"/></svg>' },
    { id: 'marquee', label: 'Marquee', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="1" stroke-dasharray="3 3"/></svg>' },
    { id: 'lasso', label: 'Lasso', icon: '⟡' },
    { id: 'pen', label: 'Pen selection', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 19 7-7 3 3-7 7-3-3zM18 13l-1.5-7.5L2 2l3.5 14.5L13 18M2 2l7.5 7.5"/><circle cx="10" cy="10" r="2"/></svg>' },
    { id: 'wand', label: 'Wand', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 4V2"/><path d="M15 16v-2"/><path d="M8 9h2"/><path d="M20 9h2"/><path d="M17.8 11.8L19 13"/><path d="M15 9h0"/><path d="M17.8 6.2L19 5"/><path d="M3 21l9-9"/><path d="M12.2 6.2L11 5"/></svg>' },
    { id: 'sam', label: 'SAM', ai: true, icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7c3-3 13-3 16 0"/><path d="M4 17c3 3 13 3 16 0"/><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3"/></svg>' },
    { sep: true },
    { id: 'inpaint', label: 'Inpaint', ai: true, icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.06 11.9l8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/><path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04a3.01 3.01 0 0 0-3-3.02z"/></svg>' },
    { id: 'rembg', ai: true, label: 'Bg Remove', icon: '✄' },
    { id: 'sharpen', ai: true, label: 'Sharpen', icon: '◈' },
  ];
  const toolKeyMap = {};
  for (const t of tools) {
    t.key = TOOL_SHORTCUTS[t.id];
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
    if (t.id === 'pen') btn.title += ' - Click anchors; drag for curves; close path or press Enter';
    // Heavy 4-point AI star marker for AI-backed tools — sits just to
    // the left of the icon so the user can spot AI vs local tools at a
    // glance now that the "AI Tools" separator is gone.
    const aiStar = t.ai ? '<span class="ge-tool-ai" title="AI">✦</span>' : '';
    btn.classList.toggle('is-ai', !!t.ai);
    // Selection-clear badge — rendered only for tools that can hold a
    // selection (lasso, wand). Inpaint masks are first-class sub-layers
    // now so they get their own delete-X in the layer panel.
    const clearTitle = t.id === 'sam' ? 'Open SAM prompt' : 'Clear selection';
    const clearBadge = (t.id === 'marquee' || t.id === 'lasso' || t.id === 'wand' || t.id === 'sam')
      ? '<span class="ge-tool-clear" title="' + clearTitle + '" data-clear-tool="' + t.id + '">' +
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
