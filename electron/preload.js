const { contextBridge, ipcRenderer } = require('electron');

// Expose safe window-control API to the injected title bar
contextBridge.exposeInMainWorld('electronAPI', {
  minimize: () => ipcRenderer.send('window-minimize'),
  maximize: () => ipcRenderer.send('window-maximize'),
  close:    () => ipcRenderer.send('window-close'),
});

// ── Inject custom title bar ──
function injectTitleBar() {
  if (document.getElementById('electron-custom-titlebar')) return;

  const bar = document.createElement('div');
  bar.id = 'electron-custom-titlebar';
  bar.innerHTML = `
    <div class="etb-drag">
      <span class="etb-icon">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
      </span>
      <span class="etb-title">Odysseus</span>
    </div>
    <div class="etb-controls">
      <button class="etb-btn etb-minimize" title="Minimize" aria-label="Minimize">
        <span style="display:inline-block;width:10px;height:2px;background:currentColor;border-radius:0.5px;"></span>
      </button>
      <button class="etb-btn etb-maximize" title="Maximize" aria-label="Maximize">
        <svg width="10" height="10" viewBox="0 0 10 10"><rect x="0.5" y="0.5" width="9" height="9" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>
      </button>
      <button class="etb-btn etb-close" title="Close" aria-label="Close">
        <svg width="10" height="10" viewBox="0 0 10 10"><path d="M1 1l8 8M9 1L1 9" stroke="currentColor" stroke-width="1.5" fill="none"/></svg>
      </button>
    </div>
  `;

  // Styles using Odysseus CSS custom properties (reactive to theme changes)
  const style = document.createElement('style');
  style.textContent = `
    #electron-custom-titlebar {
      position: fixed;
      top: 0; left: 0; right: 0;
      height: 32px;
      background: var(--panel, var(--bg, #111));
      display: flex;
      align-items: center;
      justify-content: space-between;
      z-index: 5000;
      user-select: none;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      -webkit-app-region: drag;
    }
    #electron-custom-titlebar .etb-drag {
      display: flex;
      align-items: center;
      gap: 8px;
      padding-left: 12px;
      flex: 1;
      height: 100%;
      -webkit-app-region: drag;
    }
    #electron-custom-titlebar .etb-icon {
      color: var(--fg, #9cdef2);
      display: flex;
      align-items: center;
      opacity: 0.7;
    }
    #electron-custom-titlebar .etb-title {
      color: var(--fg, #9cdef2);
      font-size: 12.5px;
      font-weight: 500;
      letter-spacing: 0.3px;
      opacity: 0.8;
    }
    #electron-custom-titlebar .etb-controls {
      display: flex;
      align-items: center;
      height: 100%;
      padding: 0 8px;
      gap: 4px;
      -webkit-app-region: no-drag;
    }
    /* Match existing .minimize-btn / .close-btn styles from the app */
    #electron-custom-titlebar .etb-btn {
      background: var(--bg, #282c34);
      color: var(--fg, #9cdef2);
      border: 1px solid var(--fg, #9cdef2);
      width: 24px;
      height: 24px;
      padding: 0;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      line-height: 1;
      cursor: pointer;
      border-radius: 4px;
      flex-shrink: 0;
      transition: background 0.12s ease, color 0.12s ease;
      font-size: 14px;
      font-weight: 700;
    }
    #electron-custom-titlebar .etb-btn.etb-minimize {
      padding: 0 0 4px 0;
      align-items: flex-end;
    }
    #electron-custom-titlebar .etb-btn:hover {
      background: var(--fg, #9cdef2);
      color: var(--bg, #282c34);
    }
    /* Push content below title bar */
    body { padding-top: 32px; }
    /* Push fixed-position hamburger button down so it stays visible */
    #hamburger-btn, .hamburger-btn { top: 40px !important; }
  `;

  document.head.appendChild(style);
  document.body.appendChild(bar);

  // Wire buttons — use ipcRenderer directly (preload context, not renderer)
  bar.querySelector('.etb-minimize').addEventListener('click', () => ipcRenderer.send('window-minimize'));
  bar.querySelector('.etb-maximize').addEventListener('click', () => ipcRenderer.send('window-maximize'));
  bar.querySelector('.etb-close')   .addEventListener('click', () => ipcRenderer.send('window-close'));
}

// Inject as soon as body is available
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', injectTitleBar);
} else {
  injectTitleBar();
}
