/**
 * Hardware Monitor Widget
 * Injects a "Hardware" sidebar button that opens the hardware panel modal.
 *
 * Uses window.OdysseusPkg (set by pkg-api.js before any widget loads).
 */
(function () {
  'use strict';
  const api = window.OdysseusPkg;
  if (!api) {
    console.warn('[hardware-monitor] OdysseusPkg not available — is pkg-api.js loaded?');
    return;
  }

  // ── Sidebar button ──────────────────────────────────────────────────────────
  const btn = document.createElement('div');
  btn.className = 'list-item';
  btn.id = 'tool-hardware-btn';
  btn.style.cursor = 'pointer';
  btn.innerHTML = `
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
         style="flex-shrink:0;opacity:0.5">
      <rect x="2" y="3" width="20" height="14" rx="2"/>
      <line x1="8" y1="21" x2="16" y2="21"/>
      <line x1="12" y1="17" x2="12" y2="21"/>
    </svg>
    <span class="grow">Hardware</span>
  `;

  let _panel = null;

  btn.addEventListener('click', () => {
    api.openPanel('hardware-modal', 'Hardware & System', {
      width: '680px',
      sidebarBtnId: 'tool-hardware-btn',
      onInit: async (bodyEl) => {
        const { default: HardwarePanel } = await import('/static/js/hardware.js');
        _panel = HardwarePanel;
        HardwarePanel.init(bodyEl, { isAdmin: !!window._isAdmin });
      },
      onClose: () => {
        if (_panel) { _panel.destroy(); _panel = null; }
      },
    });
  });

  api.addWidget('sidebar', btn);
})();
