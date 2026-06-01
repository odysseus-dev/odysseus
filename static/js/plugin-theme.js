/* Plugin theme bridge — applies the user's active Odysseus theme to plugin pages.
 *
 * Plugin pages are same-origin, so this reads the SAME localStorage the main
 * app's theme.js writes ('odysseus-theme'), and falls back to /api/prefs/theme.
 * Loaded as a blocking <script> in <head> → vars are set before first paint
 * (no flash). Mirrors core theme.js applyColors()/computeAdvancedDefaults() for
 * the variables the plugin components use.
 */
(function () {
  var FONT_MAP = {
    mono: "'Fira Code', monospace",
    sans: "system-ui, -apple-system, 'Segoe UI', sans-serif",
    serif: "Georgia, 'Times New Roman', serif",
  };
  var DARK = { bg: '#282c34', fg: '#9cdef2', panel: '#111111', border: '#355a66', red: '#e06c75' };
  // advanced override key (camelCase, as stored) → CSS var
  var ADV = {
    userBubbleBg: '--user-bubble-bg', aiBubbleBg: '--ai-bubble-bg', bubbleBorder: '--bubble-border',
    sidebarBg: '--sidebar-bg', brandColor: '--brand-color', hamburgerColor: '--hamburger-color',
    inputBg: '--input-bg', inputBorder: '--input-border', sendBtnBg: '--send-btn-bg',
    sendBtnHover: '--send-btn-hover', codeBg: '--code-bg', codeFg: '--code-fg', toggleActive: '--toggle-active',
  };
  function defaults(c) {
    var red = c.red || '#e06c75';
    return {
      '--user-bubble-bg': c.bg, '--ai-bubble-bg': c.panel, '--bubble-border': c.border,
      '--sidebar-bg': c.panel, '--brand-color': red, '--hamburger-color': c.fg,
      '--input-bg': c.panel, '--input-border': c.border, '--send-btn-bg': red, '--send-btn-hover': red,
      '--code-bg': c.panel, '--code-fg': c.fg, '--toggle-active': red,
    };
  }
  function apply(c, font, density) {
    if (!c || !c.bg) return;
    var s = document.documentElement.style;
    s.setProperty('--bg', c.bg); s.setProperty('--fg', c.fg);
    s.setProperty('--panel', c.panel); s.setProperty('--border', c.border);
    if (c.red) s.setProperty('--red', c.red);
    var def = defaults(c);
    for (var v in def) s.setProperty(v, def[v]);
    var o = c.advanced || {};
    for (var k in o) if (ADV[k] && o[k]) s.setProperty(ADV[k], o[k]);
    s.setProperty('--font-family', FONT_MAP[font] || FONT_MAP.mono);
    var de = document.documentElement;
    de.classList.remove('density-compact', 'density-spacious');
    if (density === 'compact') de.classList.add('density-compact');
    else if (density === 'spacious') de.classList.add('density-spacious');
  }
  window.__applyOdysseusTheme = apply;

  var saved = null;
  try {
    var raw = localStorage.getItem('odysseus-theme');
    if (raw) { var o = JSON.parse(raw); if (o && o.colors) saved = o; }
  } catch (e) {}
  if (saved) {
    apply(saved.colors, saved.font, saved.density);
  } else {
    apply(DARK, 'mono', 'comfortable');
    try {
      fetch('/api/prefs/theme', { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (d) { if (d && d.value && d.value.colors) apply(d.value.colors, d.value.font, d.value.density); })
        .catch(function () {});
    } catch (e) {}
  }

  // Live-follow theme/customization changes made in other tabs (e.g. the main
  // app's theme picker) — the storage event fires across same-origin tabs.
  window.addEventListener('storage', function (e) {
    if (e.key === 'odysseus-theme' && e.newValue) {
      try { var o = JSON.parse(e.newValue); if (o && o.colors) apply(o.colors, o.font, o.density); } catch (_) {}
    }
  });
})();
