// API integration form. Extracted from the unified integrations host.

import { esc } from '../../html.js';
import { byId as el } from '../../dom.js';
import { bindMenuDismiss } from '../../../escMenuStack.js';
import { notifyIntegrationsChanged } from '../events.js';

// ── API form ──
export async function showApiForm({ formEl, renderList, closeForm }, editId) {
  let presets = {};
  try {
    const r = await fetch('/api/auth/integrations/presets', { credentials: 'same-origin' });
    if (r.ok) { const d = await r.json(); presets = d.presets || {}; }
  } catch (_) {}
  const presetEntries = Object.entries(presets);
  // Same `?` hint helper as the email form. Native title tooltip,
  // tabbable for keyboard users. Inline-styled so it doesn't need
  // a CSS dependency.
  // Tips are plain text; the title attribute never renders markup.
  const _apiHint = (tip) =>
    `<span class="uf-hint" title="${esc(tip)}" aria-label="${esc(tip)}" tabindex="0" `
    + `style="display:inline-block;width:13px;height:13px;border-radius:50%;`
    + `border:1px solid currentColor;font-size:9px;line-height:11px;text-align:center;`
    + `opacity:0.45;margin-left:5px;cursor:help;vertical-align:1px;font-weight:600;">?</span>`;
  // Real <select> instead of <datalist>: datalists are silently
  // suppressed in Firefox when autocomplete="off" is on the input,
  // and they're patchy on mobile browsers. A native select renders
  // the same everywhere and makes the available options visible
  // without needing the user to type.
  const sortedPresets = presetEntries.sort((a, b) => (a[1].name || a[0]).localeCompare(b[1].name || b[0]));
  const selectOpts = sortedPresets
    .map(([k, p]) => `<option value="${k}">${esc(p.name || k)}</option>`)
    .join('');
  // Letter-in-brand-color logo for each API preset; outline plug icon for
  // "Custom (no preset)". Matches the email-provider dropdown pattern.
  const _apiLetter = (letter, bg) => `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" style="flex-shrink:0"><circle cx="12" cy="12" r="11" fill="${bg}"/><text x="12" y="16.5" font-size="13" font-weight="700" text-anchor="middle" fill="#fff" font-family="system-ui,sans-serif">${letter}</text></svg>`;
  const _apiCustomIco = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex-shrink:0;opacity:0.7"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>';
  const API_PRESET_LOGO = {
    miniflux:        _apiLetter('M', '#214c87'),
    gitea:           _apiLetter('G', '#609926'),
    linkding:        _apiLetter('L', '#1f2937'),
    home_assistant:  _apiLetter('H', '#41bdf5'),
    ntfy:            _apiLetter('n', '#317f43'),
    vaultwarden:     _apiLetter('V', '#175ddc'),
    freshrss:        _apiLetter('R', '#ef6c00'),
  };
  const _apiIconFor = (k) => {
    if (!k) return _apiCustomIco;
    if (API_PRESET_LOGO[k]) return API_PRESET_LOGO[k];
    const first = (presets[k]?.name || k).trim().charAt(0).toUpperCase() || '?';
    return _apiLetter(first, '#6b7280');
  };
  const _apiRows = [['', 'Custom (no preset)'], ...sortedPresets.map(([k, p]) => [k, p.name || k])]
    .map(([k, label]) => `<button type="button" class="ufapi-option" data-value="${esc(k)}" style="display:flex;align-items:center;gap:10px;width:100%;padding:8px 10px;background:transparent;border:0;color:var(--fg);font:inherit;cursor:pointer;text-align:left;">${_apiIconFor(k)}<span>${esc(label)}</span></button>`).join('');
  formEl.innerHTML = `
      <div class="admin-card" style="margin-top:8px">
        <h2 style="font-size:13px;display:flex;align-items:center;gap:6px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent, var(--red));flex-shrink:0;"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>API Integration</h2>
        <div class="settings-col">
          <div class="settings-row"><label class="settings-label" for="uf-api-preset-trigger">Preset</label>
            <div style="position:relative;flex:1;min-width:0;">
              <select id="uf-api-preset" tabindex="-1" aria-hidden="true" style="position:absolute;width:1px;height:1px;opacity:0;pointer-events:none;"><option value="">Custom (no preset)</option>${selectOpts}</select>
              <button type="button" id="uf-api-preset-trigger" class="settings-select" style="display:flex;align-items:center;gap:10px;cursor:pointer;text-align:left;width:100%;padding-right:24px;position:relative;">
                <span class="ufapi-icon" style="display:inline-flex;align-items:center;">${_apiCustomIco}</span>
                <span class="ufapi-label" style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">Custom (no preset)</span>
                <span aria-hidden="true" style="position:absolute;right:8px;top:50%;transform:translateY(-50%);opacity:0.5;font-size:10px;pointer-events:none;">▾</span>
              </button>
              <div id="uf-api-preset-menu" style="display:none;position:absolute;top:calc(100% + 2px);left:0;right:0;z-index:1000;background:var(--panel);border:1px solid var(--border);border-radius:6px;max-height:340px;overflow-y:auto;box-shadow:0 6px 18px rgba(0,0,0,0.25);">${_apiRows}</div>
            </div>
          </div>
          <div class="settings-row"><label class="settings-label" for="uf-api-name">Name</label><input id="uf-api-name" class="settings-input" placeholder="My Service" required aria-required="true"></div>
          <div class="settings-row"><label class="settings-label" for="uf-api-url">Base URL</label><input id="uf-api-url" class="settings-input" placeholder="http://localhost:8080" required aria-required="true"></div>
          <div id="uf-api-ntfy-hint" style="display:none;font-size:11px;line-height:1.35;opacity:0.68;margin:-2px 0 2px 106px;"></div>
          <div class="settings-row"><label class="settings-label" for="uf-api-auth">Auth${_apiHint('How this service expects the credential to be sent. Bearer = sends "Authorization: Bearer YOUR_KEY" (most modern APIs, ntfy, OpenAI-style). Header = sends YOUR_KEY verbatim under a header name you choose (Miniflux uses X-Auth-Token). Basic = HTTP basic auth (user:pass). None = the API is open / no auth.')}</label><select id="uf-api-auth" class="settings-input"><option value="bearer">Bearer (most common)</option><option value="header">Header</option><option value="basic">Basic</option><option value="none">None</option></select></div>
          <div class="settings-row" id="uf-api-header-row"><label class="settings-label" for="uf-api-header">Header${_apiHint('The HTTP header name the key goes under (Miniflux: X-Auth-Token; most others: Authorization). Only used when Auth = Header.')}</label><input id="uf-api-header" class="settings-input" placeholder="X-Auth-Token"></div>
          <div class="settings-row"><label class="settings-label" for="uf-api-key">API Key${_apiHint('The secret token the service issued you (generated in its admin panel / settings). Used to prove your identity on each request. Required for any Auth mode except None.')}</label><input id="uf-api-key" class="settings-input" type="password" placeholder="Token/key"></div>
          <div class="settings-row" style="margin-top:10px;align-items:center;justify-content:flex-end;gap:6px;">
            <span id="uf-api-msg" role="status" aria-live="polite" style="font-size:11px;flex:1;margin-right:8px"></span>
            <button class="admin-btn-add" id="uf-api-test" style="display:inline-flex;align-items:center;gap:5px;background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">Test</button>
            <button class="admin-btn-add" id="uf-api-save" style="display:inline-flex;align-items:center;gap:5px;background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));font-weight:600;">Save</button>
            <button class="admin-btn-add" id="uf-api-cancel" style="display:inline-flex;align-items:center;gap:5px;background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">Cancel</button>
          </div>
        </div>
      </div>`;
  // Custom preset dropdown wire-up (hidden select stays as data source).
  (() => {
    const trig = el('uf-api-preset-trigger');
    const menu = el('uf-api-preset-menu');
    const sel = el('uf-api-preset');
    if (!trig || !menu || !sel) return;
    const lbl = trig.querySelector('.ufapi-label');
    const ico = trig.querySelector('.ufapi-icon');
    const _setFromKey = (k) => {
      const row = menu.querySelector(`.ufapi-option[data-value="${k}"]`);
      const text = row?.querySelector('span')?.textContent || 'Custom (no preset)';
      if (lbl) lbl.textContent = text;
      if (ico) ico.innerHTML = _apiIconFor(k);
    };
    // Menu is reused (hidden, not recreated). close() hides it and tears down
    // its outside-click listener + Escape-stack entry; bindMenuDismiss is
    // re-registered fresh on each open (see _open).
    let _close = () => { menu.style.display = 'none'; };
    const _open = () => {
      menu.style.display = 'block';
      const tRect = trig.getBoundingClientRect();
      const mRect = menu.getBoundingClientRect();
      const below = window.innerHeight - tRect.bottom;
      const above = tRect.top;
      if (mRect.height > below && above > below) { menu.style.top = 'auto'; menu.style.bottom = 'calc(100% + 2px)'; }
      else { menu.style.top = 'calc(100% + 2px)'; menu.style.bottom = 'auto'; }
      _close = bindMenuDismiss(menu, () => { menu.style.display = 'none'; }, (ev) => !menu.contains(ev.target) && ev.target !== trig);
    };
    trig.addEventListener('click', (e) => { e.stopPropagation(); menu.style.display === 'block' ? _close() : _open(); });
    menu.querySelectorAll('.ufapi-option').forEach(btn => {
      btn.addEventListener('mouseenter', () => { btn.style.background = 'color-mix(in srgb, var(--fg) 8%, transparent)'; });
      btn.addEventListener('mouseleave', () => { btn.style.background = 'transparent'; });
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const k = btn.dataset.value || '';
        sel.value = k;
        _setFromKey(k);
        _close();
        sel.dispatchEvent(new Event('change', { bubbles: true }));
      });
    });
    _setFromKey(sel.value || '');
  })();

  const preset = el('uf-api-preset'), name = el('uf-api-name'), url = el('uf-api-url'), auth = el('uf-api-auth'), header = el('uf-api-header'), key = el('uf-api-key'), ntfyHint = el('uf-api-ntfy-hint');
  let _editId = editId && editId !== 'new' ? editId : null;
  // Load existing
  if (_editId) {
    try {
      const r = await fetch('/api/auth/integrations', { credentials: 'same-origin' });
      const d = await r.json();
      const item = (d.integrations || []).find(i => i.id === _editId);
      if (item) { name.value = item.name || ''; url.value = item.base_url || ''; auth.value = item.auth_type || 'none'; header.value = item.auth_header || ''; }
    } catch (_) {}
  }
  // Native <select>: the option `value` is the preset key directly, so
  // no typed-name → key lookup is needed (datalist-era leftover).
  const _applyPreset = () => {
    const p = presets[preset.value];
    const isNtfy = preset.value === 'ntfy' || (p && (p.name || '').toLowerCase() === 'ntfy');
    const isUrlAuth = preset.value === 'discord_webhook'; // secret embedded in URL — no key/auth fields needed
    if (ntfyHint) {
      ntfyHint.style.display = isNtfy ? 'block' : 'none';
      if (isNtfy) {
        ntfyHint.innerHTML = 'Enter the ntfy server URL Odysseus can reach. Examples: <code>http://127.0.0.1:8091</code>, <code>http://100.x.y.z:8091</code>, or <code>https://ntfy.example.com</code>.';
      }
    }
    if (url) {
      url.placeholder = isNtfy ? 'http://127.0.0.1:8091' : isUrlAuth ? 'https://discord.com/api/webhooks/...' : 'http://localhost:8080';
    }
    // For presets that embed the secret in the URL, hide auth/key/header rows
    // so users aren't confused into thinking they need to fill them in.
    const keyRow = key?.closest('.settings-row');
    const authRow = auth?.closest('.settings-row');
    const headerRow = el('uf-api-header-row');
    if (keyRow) keyRow.style.display = isUrlAuth ? 'none' : '';
    if (authRow) authRow.style.display = isUrlAuth ? 'none' : '';
    if (headerRow) headerRow.style.display = isUrlAuth ? 'none' : '';
    if (!p) return;
    name.value = p.name || '';
    auth.value = p.auth_type || 'none';
    header.value = p.auth_header || '';
  };
  preset.addEventListener('change', _applyPreset);
  _applyPreset();
  el('uf-api-cancel').addEventListener('click', () => {
    if (typeof closeForm === 'function') closeForm();
    else { formEl.style.display = 'none'; formEl.innerHTML = ''; }
  });
  el('uf-api-save').addEventListener('click', async () => {
    const presetKey = preset.value || undefined;
    const nameValue = name.value.trim();
    const urlValue = url.value.trim();
    if (!nameValue) { el('uf-api-msg').textContent = 'Name required'; el('uf-api-msg').style.color = 'var(--red)'; return; }
    if (!urlValue) { el('uf-api-msg').textContent = 'Base URL required'; el('uf-api-msg').style.color = 'var(--red)'; return; }
    const body = { name: nameValue, base_url: urlValue, auth_type: auth.value, auth_header: header.value, preset: presetKey };
    if (key.value) body.api_key = key.value;
    try {
      const u = _editId ? `/api/auth/integrations/${_editId}` : '/api/auth/integrations';
      const m = _editId ? 'PUT' : 'POST';
      const r = await fetch(u, { method: m, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (!r.ok) throw new Error();
      const saved = await r.json().catch(() => null);
      // If this was a create, capture the new ID so Test works
      // immediately without needing a form reopen. The POST response
      // shape is {ok, integration: {id, ...}} — saved.id at the top
      // level would silently miss, leaving Test perpetually stuck on
      // "Save first" until the form was reopened.
      if (!_editId && saved) _editId = saved.integration?.id || saved.id;
      el('uf-api-msg').textContent = 'Saved'; el('uf-api-msg').style.color = 'var(--green,#50fa7b)';
      await renderList();
      notifyIntegrationsChanged();
    } catch (_) { el('uf-api-msg').textContent = 'Failed'; el('uf-api-msg').style.color = 'var(--red)'; }
  });
  el('uf-api-test').addEventListener('click', async () => {
    if (!_editId) { el('uf-api-msg').textContent = 'Save first'; return; }
    try {
      const r = await fetch(`/api/auth/integrations/${_editId}/test`, { method: 'POST', credentials: 'same-origin' });
      const d = await r.json();
      // Backend returns {ok: bool, message: str}
      if (d.ok) {
        el('uf-api-msg').textContent = d.message || 'Connected';
        el('uf-api-msg').style.color = 'var(--green,#50fa7b)';
      } else {
        el('uf-api-msg').textContent = (d.message || d.error || d.detail || `HTTP ${r.status}`).slice(0, 360);
        el('uf-api-msg').style.color = 'var(--red)';
      }
    } catch (e) { el('uf-api-msg').textContent = 'Error: ' + e.message; el('uf-api-msg').style.color = 'var(--red)'; }
  });
}
