// Search-provider settings and the deep-research search settings that share
// the provider tables. Extracted verbatim from settings.js.

import { byId as el } from './dom.js';
import { postSettings as _postSettings } from './api.js';
import searchModule from '../search.js';
import { SEARCH_PROVIDER_LOGOS as _SEARCH_PROVIDER_LOGOS } from '../searchProviderIcons.js';

var _LINK = function(href, text) {
  return '<a href="' + href + '" target="_blank" rel="noopener noreferrer" style="color:var(--accent, var(--red));text-decoration:underline;">' + text + '</a>';
};

var _searchProviderHints = {
  searxng: 'Private, self-hosted instance. Leave URL empty to use the SEARXNG_INSTANCE env var.',
  duckduckgo: 'No API key needed, but rate-limited — heavy use can return empty results. Configure a fallback below.',
  brave: 'Get your API key from ' + _LINK('https://brave.com/search/api/', 'brave.com/search/api'),
  google_pse: 'Requires a Google API key and a Programmable Search Engine ID (CX). Create one at ' + _LINK('https://programmablesearchengine.google.com/', 'programmablesearchengine.google.com'),
  tavily: 'AI-optimized search. 1,000 free credits/month at ' + _LINK('https://tavily.com/', 'tavily.com'),
  serper: 'Google results via API. 2,500 free queries at ' + _LINK('https://serper.dev/', 'serper.dev'),
  you_com: 'Works with no key, but keyless requests are subject to usage limits and Deep Research can consume multiple requests. Optional API key from ' + _LINK('https://you.com/platform?utm_source=odysseus-dev-odysseus&utm_medium=oss_integration&utm_campaign=2026-10-oss-integrations&utm_content=settings', 'you.com/platform') + ' for higher limits.',
  disabled: 'Web search and deep research tools will be unavailable.',
};

var _searchNeedsKey = { brave: 1, google_pse: 1, tavily: 1, serper: 1 };

// Optional key: show the key field but do not treat a missing key as misconfigured.
var _searchOptionalKey = { you_com: 1 };

var _searchLabels = {
  searxng: 'SearXNG', duckduckgo: 'DuckDuckGo', brave: 'Brave Search',
  google_pse: 'Google PSE', tavily: 'Tavily', serper: 'Serper',
  you_com: 'You.com', disabled: 'Disabled',
};

var _searchKeyFields = {
  brave: 'brave_api_key', google_pse: 'google_pse_key',
  tavily: 'tavily_api_key', serper: 'serper_api_key',
  you_com: 'ydc_api_key',
};

export async function initSearchSettings() {
  var provSel = el('set-searchProvider');
  var countSel = el('set-searchResultCount');
  var countCustomInput = el('set-searchResultCountCustom');
  var urlInput = el('set-searchUrl');
  var urlRow = el('set-searchUrlRow');
  var keyInput = el('set-searchApiKey');
  var keyRow = el('set-searchKeyRow');
  var cxInput = el('set-searchCx');
  var cxRow = el('set-searchCxRow');
  var hint = el('set-searchHint');
  var msg = el('set-searchMsg');
  var _settings = {};

  function keyFieldFor(prov) { return _searchKeyFields[prov] || ''; }

  function loadKeyForProvider(prov) {
    var field = keyFieldFor(prov);
    // Optional-key providers ignore the legacy shared key so a stale key from
    // another provider is not silently copied over.
    var legacy = _searchOptionalKey[prov] ? '' : (_settings.search_api_key || '');
    keyInput.value = field ? (_settings[field] || legacy) : '';
  }

  function updateVisibility() {
    var prov = provSel.value;
    urlRow.style.display = prov === 'searxng' ? 'flex' : 'none';
    keyRow.style.display = (_searchNeedsKey[prov] || _searchOptionalKey[prov]) ? 'flex' : 'none';
    cxRow.style.display = prov === 'google_pse' ? 'flex' : 'none';
    hint.innerHTML = _searchProviderHints[prov] || '';
    if (prov === 'brave') keyInput.placeholder = 'Brave API key';
    else if (prov === 'google_pse') keyInput.placeholder = 'Google API key';
    else if (prov === 'tavily') keyInput.placeholder = 'Tavily API key';
    else if (prov === 'serper') keyInput.placeholder = 'Serper API key';
    else if (prov === 'you_com') keyInput.placeholder = 'YDC_API_KEY (optional)';
    else keyInput.placeholder = 'API key';
    loadKeyForProvider(prov);
  }

  function updateCountDisplay() {
    var val = _settings.search_result_count || 5;
    var presets = ['3', '5', '10', '20'];
    if (presets.includes(String(val))) {
      countSel.value = String(val);
      countCustomInput.style.display = 'none';
    } else {
      countSel.value = 'custom';
      countCustomInput.value = Math.max(1, Math.min(100, val));
      countCustomInput.style.display = 'block';
    }
  }

  try {
    var res = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    _settings = await res.json();
    if (_settings.search_provider) provSel.value = _settings.search_provider;
    updateCountDisplay();
    if (_settings.search_url) urlInput.value = _settings.search_url;
    if (_settings.google_pse_cx) cxInput.value = _settings.google_pse_cx;
  } catch (e) { console.warn('Failed to load search settings', e); }

  countSel.addEventListener('change', function() {
    if (this.value === 'custom') {
      countCustomInput.style.display = 'block';
      countCustomInput.focus();
    } else {
      countCustomInput.style.display = 'none';
    }
  });

  updateVisibility();

  async function refreshStatus() {
    try {
      var sRes = await fetch('/api/auth/settings', { credentials: 'same-origin' });
      var s = await sRes.json();
      _settings = s;
      var active = s.search_provider || 'searxng';
      var label = _searchLabels[active] || active;
      var extra = '';
      var kf = keyFieldFor(active);
      var legacyKey = _searchOptionalKey[active] ? '' : (s.search_api_key || '').trim();
      // hasKey from saved settings (what /api/auth/settings returns).
      var hasKey = kf ? ((s[kf] || '').trim() || legacyKey) : false;
      // For optional-key providers, also check runtime key presence via
      // /api/search/config so that a configured key (from settings or
      // environment) is reflected even when credentials are hidden or
      // empty in /api/auth/settings (#6809).
      if (_searchOptionalKey[active] && !hasKey) {
        try {
          var cfgRes = await fetch('/api/search/config', { credentials: 'same-origin' });
          var cfg = await cfgRes.json();
          if (cfg.active_provider === active && cfg.has_api_key) {
            hasKey = true;
          }
        } catch (_) { /* /api/search/config is best-effort */ }
      }
      if (_searchNeedsKey[active]) {
        extra = hasKey ? ' (key set)' : ' (no key)';
      } else if (_searchOptionalKey[active]) {
        extra = hasKey ? ' (key active)' : ' (keyless)';
      } else if (active === 'searxng' && (s.search_url || '').trim()) {
        extra = ' (' + s.search_url + ')';
      }
      var count = s.search_result_count || 5;
      msg.textContent = 'Active: ' + label + extra + ' \u00b7 ' + count + ' results';
      msg.style.color = active === 'disabled' ? 'var(--red)' : (_searchNeedsKey[active] && !hasKey) ? 'var(--red)' : 'var(--fg)';
    } catch (e) { /* ignore */ }
  }
  refreshStatus();

  async function saveSearch() {
    try {
      var prov = provSel.value;
      var resultCount;
      if (countSel.value === 'custom') {
        var customVal = parseInt(countCustomInput.value, 10);
        if (isNaN(customVal) || customVal < 1 || customVal > 100) {
          resultCount = _settings.search_result_count || 5;
        } else {
          resultCount = customVal;
        }
      } else {
        resultCount = parseInt(countSel.value, 10);
      }
      var payload = {
        search_provider: prov,
        search_result_count: resultCount,
        search_url: urlInput.value.trim(),
        google_pse_cx: cxInput.value.trim(),
      };
      var kf = keyFieldFor(prov);
      if (kf) {
        payload[kf] = keyInput.value.trim();
        _settings[kf] = keyInput.value.trim();
      }
      await _postSettings(payload);
      msg.textContent = 'Saved'; msg.style.color = 'var(--fg)';
      setTimeout(refreshStatus, 2000);
      if (searchModule && searchModule.refresh) searchModule.refresh();
    } catch (e) { msg.textContent = e.status === 403 ? 'Admin access is required to change these settings.' : 'Failed to save'; msg.style.color = 'var(--red)'; }
  }

  provSel.addEventListener('change', function() { updateVisibility(); saveSearch(); _syncSearchPicker(); });
  countSel.addEventListener('change', saveSearch);
  urlInput.addEventListener('change', saveSearch);
  keyInput.addEventListener('change', saveSearch);
  cxInput.addEventListener('change', saveSearch);

  // ── Provider picker with logos (mirrors the hidden <select>) ──
  var picker = el('search-provider-picker');
  var pickerBtn = el('search-provider-btn');
  var pickerMenu = el('search-provider-menu');
  var pickerCurrent = picker ? picker.querySelector('.adm-provider-current') : null;
  function _searchProviderLogoSvg(key) {
    return _SEARCH_PROVIDER_LOGOS[key] || '';
  }
  function _renderSearchPickerMenu() {
    if (!pickerMenu) return;
    pickerMenu.innerHTML = Array.from(provSel.options).map(function(o) {
      var logo = _searchProviderLogoSvg(o.dataset.searchLogo);
      var active = o.value === provSel.value ? ' active' : '';
      return '<div class="adm-provider-item' + active + '" role="option" data-value="' + o.value.replace(/"/g, '&quot;') + '">' +
        '<span class="adm-provider-logo">' + logo + '</span>' +
        '<span>' + o.textContent + '</span>' +
      '</div>';
    }).join('');
  }
  function _syncSearchPicker() {
    if (!pickerCurrent) return;
    var opt = provSel.selectedOptions[0] || provSel.options[0];
    var logo = _searchProviderLogoSvg(opt.dataset.searchLogo);
    pickerCurrent.querySelector('.adm-provider-logo').innerHTML = logo;
    pickerCurrent.querySelector('.adm-provider-name').textContent = opt.textContent;
  }
  if (picker && pickerBtn && pickerMenu && pickerCurrent) {
    _renderSearchPickerMenu();
    _syncSearchPicker();
    pickerBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      pickerMenu.classList.toggle('hidden');
    });
    pickerMenu.addEventListener('click', function(e) {
      var item = e.target.closest('.adm-provider-item');
      if (!item) return;
      provSel.value = item.dataset.value;
      provSel.dispatchEvent(new Event('change', { bubbles: true }));
      pickerMenu.classList.add('hidden');
      _renderSearchPickerMenu();
    });
    document.addEventListener('click', function(e) {
      if (!picker.contains(e.target)) pickerMenu.classList.add('hidden');
    });
  }

  // ── Fallback chain ──
  // Stored as an ordered array of provider IDs (primary not included).
  // When the primary fails or hits rate-limit, the backend walks this
  // list in order trying each one.
  var fbWrap = el('set-searchFallbackChain');
  function _availableFallbackOptions() {
    var primary = provSel.value;
    var chain = _settings.search_fallback_chain || [];
    var inChain = new Set(chain.concat([primary, 'disabled']));
    return Array.from(provSel.options)
      .map(function(o) { return { value: o.value, label: o.textContent, logo: o.dataset.searchLogo }; })
      .filter(function(o) { return !inChain.has(o.value); });
  }
  var addBtn = el('set-searchAddFallback');
  var TRASH_SVG = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>';
  function _renderFallbackChain() {
    if (!fbWrap) return;
    var chain = (_settings.search_fallback_chain || []).slice();
    fbWrap.innerHTML = '';
    chain.forEach(function(p, idx) {
      var row = document.createElement('div');
      row.className = 'settings-fallback-row';

      var num = document.createElement('span');
      num.className = 'settings-fallback-num';
      num.textContent = (idx + 1) + '.';
      row.appendChild(num);

      // Inline logo so the row identifies its provider at a glance even
      // before opening the dropdown. The <select> below still drives
      // selection; we just mirror its value into the logo span.
      var logoWrap = document.createElement('span');
      logoWrap.style.cssText = 'display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;flex-shrink:0;color:var(--fg);';
      var setLogo = function(val) {
        var srcOpt = Array.from(provSel.options).find(function(o) { return o.value === val; });
        logoWrap.innerHTML = srcOpt ? _searchProviderLogoSvg(srcOpt.dataset.searchLogo) : '';
      };
      setLogo(p);
      row.appendChild(logoWrap);

      var sel = document.createElement('select');
      sel.className = 'settings-select';
      // Options: this row's current value + every other provider not yet in the chain (and not the primary or 'disabled').
      var primary = provSel.value;
      var others = new Set(chain.filter(function(x) { return x !== p; }).concat([primary, 'disabled']));
      Array.from(provSel.options).forEach(function(o) {
        if (o.value !== p && others.has(o.value)) return;
        var opt = document.createElement('option');
        opt.value = o.value;
        opt.textContent = o.textContent;
        sel.appendChild(opt);
      });
      sel.value = p;
      sel.addEventListener('change', function() {
        setLogo(sel.value);
        var next = (_settings.search_fallback_chain || []).slice();
        next[idx] = sel.value;
        _saveFallbackChain(next);
      });
      row.appendChild(sel);

      var rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'settings-fallback-remove';
      rm.title = 'Remove fallback';
      rm.innerHTML = TRASH_SVG;
      rm.addEventListener('click', function() {
        var next = (_settings.search_fallback_chain || []).filter(function(x, i) { return i !== idx; });
        _saveFallbackChain(next);
      });
      row.appendChild(rm);

      fbWrap.appendChild(row);
    });
    // Add-fallback button: disabled when there are no remaining providers to add.
    if (addBtn) {
      var hasMore = _availableFallbackOptions().length > 0;
      addBtn.style.display = hasMore ? '' : 'none';
    }
  }
  if (addBtn && !addBtn._wired) {
    addBtn._wired = true;
    addBtn.addEventListener('click', function() {
      var avail = _availableFallbackOptions();
      if (!avail.length) return;
      var next = (_settings.search_fallback_chain || []).slice();
      next.push(avail[0].value);
      _saveFallbackChain(next);
    });
  }
  async function _saveFallbackChain(chain) {
    _settings.search_fallback_chain = chain;
    try {
      await _postSettings({ search_fallback_chain: chain });
      msg.textContent = 'Saved'; msg.style.color = 'var(--fg)';
      setTimeout(refreshStatus, 2000);
    } catch (e) { msg.textContent = e.status === 403 ? 'Admin access is required to change these settings.' : 'Failed to save'; msg.style.color = 'var(--red)'; }
    _renderFallbackChain();
  }
  _renderFallbackChain();
  // Re-render whenever the primary changes (it gets filtered out of "Add").
  provSel.addEventListener('change', _renderFallbackChain);

  // ── Test button ── runs a one-off query against the configured provider.
  var testBtn = el('set-searchTestBtn');
  if (testBtn) {
    testBtn.addEventListener('click', async function() {
      var prov = provSel.value;
      if (!prov || prov === 'disabled') {
        msg.textContent = 'Pick a provider first';
        msg.style.color = 'var(--red)';
        return;
      }
      // Persist current form values first so the test uses what's on screen.
      await saveSearch();
      testBtn.disabled = true;
      var origHtml = testBtn.innerHTML;
      var wp = null;
      try {
        var sp = window.spinnerModule || (await import('../spinner.js')).default;
        wp = sp.createWhirlpool(11);
        wp.element.style.cssText = 'display:inline-flex;width:11px;height:11px;margin:0 4px 0 0;';
        testBtn.innerHTML = '';
        testBtn.appendChild(wp.element);
        testBtn.appendChild(document.createTextNode('Testing'));
      } catch (_) {
        testBtn.innerHTML = origHtml.replace(/>Test\s*$/, '>Testing...');
      }
      msg.textContent = '';
      var t0 = performance.now();
      try {
        var fd = new FormData();
        fd.append('query', 'hello world');
        fd.append('provider', prov);
        fd.append('count', '3');
        var r = await fetch('/api/search/query', { method: 'POST', body: fd, credentials: 'same-origin' });
        var d = await r.json();
        var ms = Math.round(performance.now() - t0);
        if (d.error) {
          msg.textContent = '✗ ' + d.error + ' (' + ms + 'ms)';
          msg.style.color = 'var(--red)';
        } else if (!d.results || !d.results.length) {
          msg.textContent = '⚠ No results returned (' + ms + 'ms)';
          msg.style.color = 'var(--red)';
        } else {
          var topTitle = (d.results[0].title || d.results[0].url || '').slice(0, 60);
          msg.textContent = '✓ ' + d.results.length + ' result' + (d.results.length === 1 ? '' : 's') + ' · ' + ms + 'ms · top: ' + topTitle;
          msg.style.color = 'var(--fg)';
        }
      } catch (e) {
        msg.textContent = '✗ Test failed: ' + (e && e.message ? e.message : e);
        msg.style.color = 'var(--red)';
      } finally {
        if (wp) { try { wp.destroy(); } catch (_) {} }
        testBtn.disabled = false; testBtn.innerHTML = origHtml;
      }
    });
  }
}

/* ── Deep Research Search (Search tab) ── */
export async function initResearchSearchSettings() {
  var searchSel = el('set-researchSearch');
  var msg = el('set-researchSearchMsg');
  var logoEl = el('set-researchSearch-logo');

  function updateSearchLogo() {
    if (!logoEl) return;
    var opt = searchSel.selectedOptions[0];
    var key = opt && opt.dataset ? opt.dataset.searchLogo : '';
    logoEl.innerHTML = key ? (_SEARCH_PROVIDER_LOGOS[key] || '') : '';
  }

  function updateSearchOptions(settings) {
    var options = searchSel.querySelectorAll('option');
    options.forEach(function(opt) {
      var prov = opt.value;
      if (!prov) return;
      var kf = _searchKeyFields[prov];
      if (!kf) return;
      var hasKey = ((settings[kf] || '').trim() || (settings.search_api_key || '').trim());
      // Optional-key providers work without a key; do not flag them as
      // misconfigured when no key is saved in settings (#6809).
      if (!hasKey && !_searchOptionalKey[prov]) {
        opt.textContent = (_searchLabels[prov] || prov) + ' (no key)';
        opt.style.color = 'var(--red)';
      } else {
        opt.textContent = _searchLabels[prov] || prov;
        opt.style.color = '';
      }
    });
  }

  try {
    var res = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    var settings = await res.json();
    if (settings.research_search_provider) searchSel.value = settings.research_search_provider;
    updateSearchOptions(settings);
    updateSearchLogo();
  } catch (e) { console.warn('Failed to load research search settings', e); }

  async function saveResearchSearch() {
    try {
      await _postSettings({ research_search_provider: searchSel.value });
      msg.textContent = 'Saved'; msg.style.color = 'var(--fg)';
      setTimeout(function() { msg.textContent = ''; }, 2000);
    } catch (e) { msg.textContent = e.status === 403 ? 'Admin access is required to change these settings.' : 'Failed to save'; msg.style.color = 'var(--red)'; }
  }

  searchSel.addEventListener('change', function() { updateSearchLogo(); saveResearchSearch(); });
}
