/**
 * Docker Workspaces Widget
 * Injects a "Workspaces" sidebar button that opens the AI workspaces panel.
 *
 * Uses window.OdysseusPkg (set by pkg-api.js before any widget loads).
 */
(function () {
  'use strict';
  const api = window.OdysseusPkg;
  if (!api) {
    console.warn('[docker-workspaces] OdysseusPkg not available — is pkg-api.js loaded?');
    return;
  }

  // ── Sidebar button ──────────────────────────────────────────────────────────
  const btn = document.createElement('div');
  btn.className = 'list-item';
  btn.id = 'tool-docker-workspaces-btn';
  btn.style.cursor = 'pointer';
  btn.innerHTML = `
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
         style="flex-shrink:0;opacity:0.5">
      <path d="M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
      <path d="M8 7v10M16 7v10M3 12h18"/>
    </svg>
    <span class="grow">Workspaces</span>
  `;

  btn.addEventListener('click', () => {
    api.openPanel('docker-workspaces-modal', 'AI Workspaces', {
      width: '720px',
      sidebarBtnId: 'tool-docker-workspaces-btn',
      onInit: async (bodyEl) => {
        const { default: WorkspaceManager } = await import('/static/js/workspaces.js');
        WorkspaceManager.init(bodyEl);
      },
      onClose: () => {},
    });
  });

  api.addWidget('sidebar', btn);
})();
