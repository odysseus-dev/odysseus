/**
 * Build the editor's left-side tool palette.
 *
 * Pure DOM construction — no module state. The big tool-switch logic
 * (cursor swap, control-section toggle, transform entry, inpaint
 * mask plumbing, etc.) stays in the caller and arrives here as the
 * `onSelectTool` callback.
 *
 * Layout mirrors PhotoCraft's rail: ordered sections, with related tools
 * collapsed into a single flyout slot (face = current member, caret opens
 * the family). Selecting a flyout member promotes it to the slot face.
 *
 * @param {{
 *   currentTool: string,
 *   onSelectTool: (toolId: string, btn: HTMLButtonElement, toolbar: HTMLDivElement) => void,
 *   onClearSelection: (which: 'marquee'|'lasso'|'wand'|'sam') => void,
 * }} ctx
 * @returns {{
 *   toolbar: HTMLDivElement,
 *   toolKeyMap: Record<string,string>,
 *   syncActive: (toolId: string) => void,
 * }}
 */
import { TOOL_SHORTCUTS } from '../tool-shortcuts.js';
import { loadBrushPresets } from '../brush-presets.js';

const CLEAR_X = '<svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>';
const CARET = '<svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>';

export const TOOLS = {
  move: { label: 'Move', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="5 9 2 12 5 15"/><polyline points="9 5 12 2 15 5"/><polyline points="15 19 12 22 9 19"/><polyline points="19 9 22 12 19 15"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="12" y1="2" x2="12" y2="22"/></svg>' },
  hand: { label: 'Hand', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 11V6a2 2 0 0 0-4 0v4"/><path d="M14 10V4a2 2 0 0 0-4 0v6"/><path d="M10 10V5a2 2 0 0 0-4 0v9"/><path d="M6 13.5 4.5 12A2.1 2.1 0 0 0 2 15l4.5 5A6 6 0 0 0 11 22h2a7 7 0 0 0 7-7v-4a2 2 0 0 0-4 0v1"/></svg>' },
  zoom: { label: 'Zoom', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>' },
  crop: { label: 'Crop', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/></svg>' },
  transform: { label: 'Transform', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>' },
  text: { label: 'Text', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7V4h16v3"/><path d="M9 20h6"/><path d="M12 4v16"/></svg>' },
  shape: { label: 'Shape', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="12" height="12" rx="1"/><circle cx="17" cy="15" r="4"/></svg>' },
  brush: { label: 'Brush', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.06 11.9l8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/><path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04a3.01 3.01 0 0 0-3-3.02z"/></svg>' },
  gradient: { label: 'Gradient', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M6 18 18 6" opacity=".2"/><path d="M9 18 18 9" opacity=".5"/><path d="M12 18 18 12"/></svg>' },
  bucket: { label: 'Paint Bucket', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m19 11-8-8-8.6 8.6a2 2 0 0 0 0 2.8l5.2 5.2c.8.8 2 .8 2.8 0L19 11Z"/><path d="m5 2 5 5"/><path d="M2 13h15"/><path d="M22 20a2 2 0 1 1-4 0c0-1.6 1.7-2.4 2-4 .3 1.6 2 2.4 2 4Z"/></svg>' },
  eraser: { label: 'Eraser', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19.4 14.6 14.6 19.4a2 2 0 0 1-2.83 0L4.6 12.23a2 2 0 0 1 0-2.83l7.17-7.17a2 2 0 0 1 2.83 0l4.8 4.8a2 2 0 0 1 0 2.83Z"/><line x1="22" y1="21" x2="7" y2="21"/><line x1="14" y1="3" x2="9" y2="8"/></svg>' },
  pixel: { label: 'Pixel', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" stroke="none" aria-hidden="true"><rect x="4" y="4" width="7" height="7"/><rect x="13" y="13" width="7" height="7"/><rect x="13" y="4" width="7" height="7" opacity="0.35"/><rect x="4" y="13" width="7" height="7" opacity="0.35"/></svg>' },
  eyedropper: { label: 'Eyedropper', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m2 22 1-1h3l9-9"/><path d="M3 21v-3l9-9"/><path d="m15 6 3.4-3.4a2.1 2.1 0 1 1 3 3L18 9l.9.9a1 1 0 0 1 0 1.4l-1.6 1.6a1 1 0 0 1-1.4 0L12 8.4a1 1 0 0 1 0-1.4l1.6-1.6a1 1 0 0 1 1.4 0l.9.9Z"/></svg>' },
  smudge: { label: 'Smudge', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.5 12V5.5a1.75 1.75 0 1 1 3.5 0V12"/><path d="M16 12v-1.5a1.5 1.5 0 0 1 3 0V16a5 5 0 0 1-5 5h-2.2a4.5 4.5 0 0 1-3.5-1.7l-3.2-4a1.6 1.6 0 0 1 2.4-2.1l1.8 1.9"/></svg>' },
  clone: { label: 'Clone', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="9" r="3"/><path d="M9 12l-3 4h12l-3-4"/><path d="M4 20h16"/></svg>' },
  heal: { label: 'Healing', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="m5 19 14-14"/><path d="M7 5h4M9 3v4M13 17h4M15 15v4"/></svg>' },
  patch: { label: 'Patch', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="10" height="10" rx="1" stroke-dasharray="3 2"/><path d="M13 13h8v8h-8z"/></svg>' },
  dodge: { label: 'Dodge', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="m9.5 14.5-6 6"/><circle cx="14" cy="10" r="6" fill="currentColor"/></svg>' },
  burn: { label: 'Burn', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 22c4 0 7-3 7-7 0-5-4-8-7-13-3 5-7 8-7 13 0 4 3 7 7 7Z"/><path d="M9 16c1.5 1 4.5 1 6 0"/></svg>' },
  liquify: { label: 'Liquify', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8c2.5-3 5-3 7.5 0s5 3 7.5 0"/><path d="M3 14c2.5-3 5-3 7.5 0s5 3 7.5 0"/><circle cx="17.5" cy="19" r="2"/></svg>' },
  marquee: { label: 'Marquee', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="1" stroke-dasharray="3 3"/></svg>' },
  lasso: { label: 'Lasso', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 22a5 5 0 0 1-2-4"/><path d="M3.3 13.9A6.9 6.9 0 0 1 2 9c0-4 4-7 10-7s10 3 10 7-4 7-10 7"/><path d="M5 18a4.6 4.6 0 0 1-1.5-3.5"/><circle cx="5" cy="19" r="2"/></svg>' },
  pen: { label: 'Pen selection', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 19 7-7 3 3-7 7-3-3zM18 13l-1.5-7.5L2 2l3.5 14.5L13 18M2 2l7.5 7.5"/><circle cx="10" cy="10" r="2"/></svg>' },
  path: { label: 'Pen Path', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20 20 4"/><path d="M4 20h4M20 4v4"/><circle cx="4" cy="20" r="2"/><circle cx="20" cy="4" r="2"/></svg>' },
  wand: { label: 'Wand', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 4V2"/><path d="M15 16v-2"/><path d="M8 9h2"/><path d="M20 9h2"/><path d="M17.8 11.8L19 13"/><path d="M15 9h0"/><path d="M17.8 6.2L19 5"/><path d="M3 21l9-9"/><path d="M12.2 6.2L11 5"/></svg>' },
  quickselect: { label: 'Quick Select', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v4M12 17v4M3 12h4M17 12h4"/><circle cx="12" cy="12" r="4"/><path d="M12 8a4 4 0 0 1 0 8"/></svg>' },
  objectselect: { label: 'Object Select', icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2" stroke-dasharray="3 3"/><path d="M9 9h6v6H9z"/></svg>' },
  sam: { label: 'SAM', ai: true, icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7c3-3 13-3 16 0"/><path d="M4 17c3 3 13 3 16 0"/><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3"/></svg>' },
  inpaint: { label: 'Inpaint', ai: true, icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.06 11.9l8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/><path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04a3.01 3.01 0 0 0-3-3.02z"/></svg>' },
  rembg: { label: 'Bg Remove', ai: true, icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><line x1="20" y1="4" x2="8.12" y2="15.88"/><line x1="14.47" y1="14.48" x2="20" y2="20"/><line x1="8.12" y1="8.12" x2="12" y2="12"/></svg>' },
  sharpen: { label: 'Sharpen', ai: true, icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4z"/></svg>' },
};

// Ordered sections. A string is a single-tool slot; an array is a flyout
// slot whose first member is the default face.
const SECTIONS = [
  ['move'],
  [['marquee', 'lasso'], ['wand', 'quickselect', 'objectselect', 'sam']],
  [['crop', 'transform'], ['brush', 'eraser', 'pixel'], ['clone', 'heal', 'patch'], ['smudge', 'dodge', 'burn', 'liquify'], ['gradient', 'bucket'], 'eyedropper'],
  [['pen', 'path'], ['text', 'shape'], ['inpaint', 'rembg', 'sharpen']],
  [['hand', 'zoom']],
];

export function buildToolbar({ currentTool, onSelectTool, onClearSelection }) {
  const toolbar = document.createElement('div');
  toolbar.className = 'ge-toolbar';
  // The tool slots live in their own scroller so the colour swatches can sit
  // in a fixed footer below it. With the swatches inside the scroller they
  // were sticky over the last slots and swallowed their clicks.
  const scroller = document.createElement('div');
  scroller.className = 'ge-toolbar-scroll';
  toolbar.appendChild(scroller);
  const toolKeyMap = {};
  const slots = [];

  for (const id of Object.keys(TOOLS)) {
    const key = TOOL_SHORTCUTS[id];
    if (key) toolKeyMap[key.toLowerCase()] = id;
  }

  const setInner = (btn, toolId) => {
    const t = TOOLS[toolId];
    btn.dataset.tool = toolId;
    btn.title = t.label + (TOOL_SHORTCUTS[toolId] ? ` (${TOOL_SHORTCUTS[toolId]})` : '');
    if (toolId === 'pen') btn.title += ' - Click anchors; drag for curves; close path or press Enter';
    if (toolId === 'path') btn.title += ' - Click to add anchors; double-click or press Enter to finish';
    btn.classList.toggle('is-ai', !!t.ai);
    const aiStar = t.ai ? '<span class="ge-tool-ai" title="AI">✦</span>' : '';
    const clearTitle = toolId === 'sam' ? 'Open SAM prompt' : 'Clear selection';
    const clearBadge = ['marquee', 'lasso', 'quickselect', 'objectselect', 'wand', 'sam'].includes(toolId)
      ? '<span class="ge-tool-clear" title="' + clearTitle + '" data-clear-tool="' + toolId + '">' + CLEAR_X + '</span>'
      : '';
    btn.innerHTML = `${aiStar}<span class="ge-tool-icon"${t.small ? ' style="font-size:14px"' : ''}>${t.icon}</span>${clearBadge}`;
    btn.querySelector('.ge-tool-clear')?.addEventListener('click', (ev) => {
      ev.stopPropagation();
      onClearSelection(ev.currentTarget.dataset.clearTool);
    });
  };

  const closeAllFlyouts = () => {
    toolbar.querySelectorAll('.ge-tool-slot.open').forEach(s => s.classList.remove('open'));
    toolbar.classList.remove('ge-flyout-open');
    toolbar.closest('.ge-toolbar-wrap')?.classList.remove('ge-flyout-open');
  };

  const openFlyout = (slotEl, face) => {
    const isOpen = slotEl.classList.contains('open');
    closeAllFlyouts();
    if (isOpen) return;
    slotEl.classList.add('open');
    toolbar.classList.add('ge-flyout-open');
    toolbar.closest('.ge-toolbar-wrap')?.classList.add('ge-flyout-open');
    const flyout = slotEl.querySelector('.ge-tool-flyout');
    if (!flyout) return;
    const r = face.getBoundingClientRect();
    // The flyout is position:fixed, but an ancestor with a transform (the
    // gallery modal sets an identity matrix) becomes its containing block, so
    // viewport coords must be offset by that block's origin or the popup lands
    // far from the button.
    const cb = flyout.offsetParent;
    const cbRect = cb ? cb.getBoundingClientRect() : { left: 0, top: 0 };
    const cbX = cbRect.left + (cb ? cb.clientLeft : 0);
    const cbY = cbRect.top + (cb ? cb.clientTop : 0);
    if (window.innerWidth <= 820) {
      flyout.style.left = (Math.max(r.left, 4) - cbX) + 'px';
      flyout.style.top = (r.bottom + 4 - cbY) + 'px';
    } else {
      flyout.style.left = (r.right + 4 - cbX) + 'px';
      flyout.style.top = (Math.max(r.top, 4) - cbY) + 'px';
    }
    // Keep the list inside the window. The rail's lower slots (brush with its
    // presets, clone, smudge) open a tall menu that would otherwise spill past
    // the bottom edge, leaving the lower rows impossible to click.
    const fw = flyout.offsetWidth;
    const fh = flyout.offsetHeight;
    const viewLeft = (parseFloat(flyout.style.left) || 0) + cbX;
    const viewTop = (parseFloat(flyout.style.top) || 0) + cbY;
    const maxLeft = Math.max(4, window.innerWidth - fw - 4);
    const maxTop = Math.max(4, window.innerHeight - fh - 4);
    if (viewLeft > maxLeft) flyout.style.left = (maxLeft - cbX) + 'px';
    if (viewTop > maxTop) flyout.style.top = (maxTop - cbY) + 'px';
  };

  document.addEventListener('click', (ev) => {
    if (!toolbar.contains(ev.target)) closeAllFlyouts();
  });

  let first = true;
  for (const section of SECTIONS) {
    if (!first) {
      const sep = document.createElement('div');
      sep.className = 'ge-tool-sep';
      scroller.appendChild(sep);
    }
    first = false;
    for (const entry of section) {
      const members = Array.isArray(entry) ? entry : [entry];
      const slotEl = document.createElement('div');
      slotEl.className = 'ge-tool-slot';
      const face = document.createElement('button');
      face.type = 'button';
      face.className = 'ge-tool-btn ge-tool-slot-face';
      const faceId = members.includes(currentTool) ? currentTool : members[0];
      setInner(face, faceId);
      face.addEventListener('click', () => {
        closeAllFlyouts();
        onSelectTool(face.dataset.tool, face, toolbar);
      });
      slotEl.appendChild(face);

      const slot = { el: slotEl, face, members, current: faceId, items: [] };
      if (members.length > 1) {
        // Photoshop-style corner triangle marks a slot whose family has
        // more tools behind it. It lives on the slot (not on the face) so
        // re-rendering the face icon when a flyout member is picked never
        // wipes the hint away.
        slotEl.insertAdjacentHTML('beforeend', '<span class="ge-tool-flyout-tri" aria-hidden="true"></span>');
        const tri = slotEl.querySelector('.ge-tool-flyout-tri');
        tri?.addEventListener('click', (ev) => {
          ev.stopPropagation();
          openFlyout(slotEl, face);
        });

        const flyout = document.createElement('div');
        flyout.className = 'ge-tool-flyout';
        for (const id of members) {
          const item = document.createElement('button');
          item.type = 'button';
          item.className = 'ge-tool-btn ge-tool-flyout-item';
          setInner(item, id);
          // Keep `data-tool` unique to the slot face so `[data-tool="X"]`
          // selectors are unambiguous; flyout members expose the option id.
          item.dataset.toolOption = id;
          delete item.dataset.tool;
          item.addEventListener('click', (ev) => {
            ev.stopPropagation();
            setInner(face, id);
            slot.current = id;
            closeAllFlyouts();
            onSelectTool(id, face, toolbar);
          });
          item.insertAdjacentHTML('beforeend', `<span class="ge-tool-flyout-label">${TOOLS[id].label}</span>`);
          flyout.appendChild(item);
          slot.items.push(item);
        }
        // Photoshop-style: the Brush slot's flyout also lists the brush
        // presets, so any saved presets are reachable straight from the tool
        // button. Picking one switches to the Brush and applies the preset
        // through the options-bar picker so the size and stroke controls stay
        // in sync. Pixel is its own tool (above), so it is NOT listed here as a
        // brush type.
        if (members.includes('brush')) {
          const brushPresets = loadBrushPresets().filter((preset) => !preset.pixel);
          if (brushPresets.length) {
            const sep = document.createElement('div');
            sep.className = 'ge-tool-flyout-sep';
            flyout.appendChild(sep);
            const heading = document.createElement('div');
            heading.className = 'ge-tool-flyout-heading';
            heading.textContent = 'Brushes';
            flyout.appendChild(heading);
            for (const preset of brushPresets) {
              const presetItem = document.createElement('button');
              presetItem.type = 'button';
              presetItem.className = 'ge-tool-btn ge-tool-flyout-item ge-tool-flyout-preset';
              presetItem.dataset.brushPreset = preset.id;
              // Each preset row shows a glyph that reflects the preset's tip:
              // a 1px checker square for the pixel brush, a solid disc for a
              // hard round, a soft-edged disc for a soft round, and a mid disc
              // in between — so a row is recognisable at a glance.
              const soft = typeof preset.softness === 'number' ? preset.softness : 100;
              const presetIcon = preset.pixel
                ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none" aria-hidden="true"><rect x="6" y="6" width="5" height="5"/><rect x="13" y="13" width="5" height="5"/><rect x="13" y="6" width="5" height="5" opacity="0.35"/><rect x="6" y="13" width="5" height="5" opacity="0.35"/></svg>'
                : soft <= 20
                  ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none" aria-hidden="true"><circle cx="12" cy="12" r="6.5"/></svg>'
                  : soft >= 70
                    ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="6.5" fill="currentColor" opacity="0.3"/><circle cx="12" cy="12" r="6.5"/></svg>'
                    : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="12" cy="12" r="6.5" fill="currentColor" opacity="0.65"/><circle cx="12" cy="12" r="6.5"/></svg>';
              const icon = document.createElement('span');
              icon.className = 'ge-tool-icon';
              icon.innerHTML = presetIcon;
              const label = document.createElement('span');
              label.className = 'ge-tool-flyout-label';
              label.textContent = preset.name;
              presetItem.append(icon, label);
              presetItem.addEventListener('click', (ev) => {
                ev.stopPropagation();
                setInner(face, 'brush');
                slot.current = 'brush';
                onSelectTool('brush', face, toolbar);
                const picker = document.getElementById('ge-brush-preset');
                if (picker) {
                  picker.value = preset.id;
                  picker.dispatchEvent(new Event('change', { bubbles: true }));
                }
                closeAllFlyouts();
              });
              flyout.appendChild(presetItem);
            }
          }
        }
        slotEl.appendChild(flyout);
        face.addEventListener('contextmenu', (ev) => {
          ev.preventDefault();
          openFlyout(slotEl, face);
        });
      }
      slots.push(slot);
      scroller.appendChild(slotEl);
    }
  }

  // Foreground / background color swatches (Photoshop-style) pinned to
  // the bottom of the tool rail, with a swap control.
  const swatches = document.createElement('div');
  swatches.className = 'ge-tool-swatches';
  swatches.innerHTML = `
    <label class="ge-swatch ge-swatch-bg" title="Background color">
      <input type="color" id="ge-bg-color" value="#ffffff" aria-label="Background color" />
    </label>
    <label class="ge-swatch ge-swatch-fg" title="Foreground color">
      <input type="color" id="ge-fg-color" value="#000000" aria-label="Foreground color" />
    </label>
    <button type="button" class="ge-swatch-swap" id="ge-swap-colors" title="Swap foreground and background colors" aria-label="Swap colors">
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 4 3 8l4 4"/><path d="M3 8h13a4 4 0 0 1 0 8h-2"/><path d="M17 20l4-4-4-4"/></svg>
    </button>`;
  toolbar.appendChild(swatches);

  const syncActive = (toolId) => {
    for (const slot of slots) {
      if (!slot.members.includes(toolId)) continue;
      if (slot.current !== toolId) setInner(slot.face, toolId);
      slot.current = toolId;
      slot.face.classList.add('active');
      slot.items.forEach(it => it.classList.toggle('active', (it.dataset.toolOption || it.dataset.tool) === toolId));
    }
  };

  // Initial active state.
  syncActive(currentTool);

  return { toolbar, toolKeyMap, syncActive };
}
