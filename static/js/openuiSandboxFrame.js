(() => {
  const PARENT_SOURCE = 'odysseus-openui-parent';
  const SANDBOX_SOURCE = 'odysseus-openui-sandbox';
  const root = document.getElementById('root');
  let currentId = null;

  function post(type, payload) {
    if (!currentId) return;
    parent.postMessage({ source: SANDBOX_SOURCE, id: currentId, type, ...payload }, '*');
  }

  function safeClone(value, maxChars = 20000) {
    try {
      const text = JSON.stringify(value ?? null);
      if (text.length > maxChars) return null;
      return JSON.parse(text);
    } catch (_e) {
      return null;
    }
  }

  function applyTheme(theme) {
    if (!theme || typeof theme !== 'object') return;
    const map = {
      bg: '--bg',
      fg: '--fg',
      accent: '--accent',
      border: '--border',
      fontFamily: '--font-family',
    };
    for (const [key, cssVar] of Object.entries(map)) {
      if (theme[key]) document.documentElement.style.setProperty(cssVar, String(theme[key]));
    }
  }

  function resize() {
    const height = Math.ceil(Math.max(
      120,
      root.scrollHeight,
      document.documentElement.scrollHeight,
      document.body.scrollHeight
    ));
    post('height', { height });
  }

  function fail(message) {
    root.innerHTML = `<div class="sandbox-error">${message}</div>`;
    post('error', { error: message });
    resize();
  }

  const observer = new ResizeObserver(resize);
  observer.observe(root);

  window.addEventListener('message', (event) => {
    if (event.source !== parent) return;
    const msg = event.data || {};
    if (!msg || msg.source !== PARENT_SOURCE || !msg.id) return;
    currentId = msg.id;

    if (msg.type === 'unmount') {
      if (window.OpenUIRenderer?.unmountOpenUI) window.OpenUIRenderer.unmountOpenUI(root);
      root.innerHTML = '';
      resize();
      return;
    }

    if (msg.type !== 'render') return;
    applyTheme(msg.theme);
    try {
      if (!window.OpenUIRenderer?.renderOpenUI) {
        fail('OpenUI renderer failed to load.');
        return;
      }
      window.OpenUIRenderer.renderOpenUI(root, String(msg.response || ''), {
        isStreaming: !!msg.isStreaming,
        initialState: safeClone(msg.initialState),
        onState: (state) => post('state', { state: safeClone(state) }),
        onAction: (action) => post('action', { action: safeClone(action) }),
      });
      requestAnimationFrame(resize);
    } catch (err) {
      fail(`OpenUI render failed: ${String(err && err.message || err)}`);
    }
  });

  resize();
})();
