// compare/scoreboard.js — vote history display
import Storage from '../storage.js';
import state from './state.js';
import { VOTES_STORAGE_KEY } from './icons.js';
import themeModule from '../theme.js';
import uiModule from '../ui.js';

const __t = (k, v) => (window.__t || (kk=>kk))(k, v);

const escapeHtml = uiModule.esc;

// Type icons for the mode tabs — match the Compare selector's tab icons.
const _TYPE_ICONS = {
  chat: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
  agent: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>',
  search: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
  research: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>',
};

/** Detect search provider names to fix legacy votes without mode. */
const _searchProviderNames = new Set(['brave search', 'duckduckgo', 'google', 'searxng', 'bing', 'tavily']);

/** Guess the compare mode for a vote record (legacy votes lack a mode field). */
function _guessVoteMode(v) {
  if (v.mode) return v.mode;
  // Legacy vote — check if models look like search providers
  if (v.models && v.models.some(m => _searchProviderNames.has(m.toLowerCase()))) return 'search';
  return 'chat';
}

export function showScoreboard() {
  // Remove existing overlay if present
  const existing = document.getElementById('scoreboard-overlay');
  if (existing) existing.remove();

  const votes = Storage.getJSON(VOTES_STORAGE_KEY, []);

  // Build modal
  const overlay = document.createElement('div');
  overlay.id = 'scoreboard-overlay';
  overlay.className = 'modal';
  overlay.style.zIndex = '10001';
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  // Esc handling lives in the global "close topmost popup" handler (app.js)
  // so the scoreboard closes first without also dismissing the compare
  // window beneath it.

  const content = document.createElement('div');
  content.className = 'modal-content';
  content.style.maxWidth = '520px';

  const header = document.createElement('div');
  header.className = 'modal-header';
  const title = document.createElement('h3');
  title.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px;"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>' + (window.__t || (k=>k))('compare.score');
  title.style.margin = '0';
  const closeX = document.createElement('button');
  closeX.className = 'close-btn';
  closeX.innerHTML = '&#x2716;';
  closeX.addEventListener('click', () => overlay.remove());
  header.appendChild(title);
  header.appendChild(closeX);
  content.appendChild(header);

  const body = document.createElement('div');
  body.className = 'modal-body';
  body.style.padding = '12px 16px';
  // Mobile: add bottom padding so the Clear History button isn't hidden behind
  // Firefox's bottom URL bar / the home-indicator safe area.
  if (window.innerWidth <= 768) {
    body.style.paddingBottom = 'calc(env(safe-area-inset-bottom, 0px) + 72px)';
    body.style.overflowY = 'auto';
  }

  // Mode tabs
  const modes = ['chat', 'agent', 'search', 'research'];
  const modeLabels = { chat: 'Chat', agent: 'Agent', search: 'Search', research: 'Research' };
  const tabBar = document.createElement('div');
  tabBar.className = 'compare-mode-tabs';
  tabBar.style.marginBottom = '12px';
  let activeMode = 'chat';

  function renderScoreTable() {
    // Clear previous table
    const prev = body.querySelector('.scoreboard-wrap');
    if (prev) {
      // The Clear button was moved INTO the wrap on a prior render — rescue it
      // back to the body before removing the wrap, otherwise it's destroyed
      // with the wrap and never re-found (it vanished after visiting an empty
      // mode like Images and switching back).
      const clr = prev.querySelector('.scoreboard-clear-btn');
      if (clr) body.appendChild(clr);
      prev.remove();
    }

    const wrap = document.createElement('div');
    wrap.className = 'scoreboard-wrap';

    const filtered = votes.filter(v => _guessVoteMode(v) === activeMode);

    // Aggregate
    const stats = {};
    for (const v of filtered) {
      for (let mi = 0; mi < v.models.length; mi++) {
        const m = v.models[mi];
        if (!stats[m]) stats[m] = { wins: 0, losses: 0, ties: 0, games: 0, totalCost: 0, costCount: 0 };
        stats[m].games++;
        if (v.winner === 'tie') stats[m].ties++;
        else if (v.winner === m) stats[m].wins++;
        else stats[m].losses++;
        if (v.costs && v.costs[mi] != null) {
          stats[m].totalCost += v.costs[mi];
          stats[m].costCount++;
        }
      }
    }
    const sorted = Object.entries(stats).sort((a, b) => {
      const rateA = a[1].games ? a[1].wins / a[1].games : 0;
      const rateB = b[1].games ? b[1].wins / b[1].games : 0;
      return rateB - rateA;
    });

    if (sorted.length === 0) {
      const empty = document.createElement('p');
      empty.style.cssText = 'color:color-mix(in srgb, var(--fg) 50%, transparent);text-align:center;padding:24px 0;';
      empty.textContent = 'No ' + activeMode + ' votes yet. Run a comparison and vote!';
      wrap.appendChild(empty);
    } else {
      const table = document.createElement('table');
      table.className = 'scoreboard-table';
      const thead = document.createElement('thead');
      thead.innerHTML = '<tr><th>Model</th><th>Win%</th><th>W</th><th>L</th><th>T</th><th>Games</th><th>$/1k</th></tr>';
      table.appendChild(thead);
      const tbody = document.createElement('tbody');
      for (const [name, s] of sorted) {
        const pct = s.games ? Math.round((s.wins / s.games) * 100) : 0;
        const avgCost = s.costCount ? (s.totalCost / s.costCount) * 1000 : null;
        const costStr = avgCost !== null ? ('$' + (avgCost < 1 ? avgCost.toFixed(2) : avgCost.toFixed(0))) : '—';
        const tr = document.createElement('tr');
        tr.innerHTML =
          '<td class="scoreboard-model">' + escapeHtml(name) + '</td>' +
          '<td class="scoreboard-pct"><strong>' + pct + '%</strong></td>' +
          '<td>' + s.wins + '</td><td>' + s.losses + '</td><td>' + s.ties + '</td>' +
          '<td>' + s.games + '</td>' +
          '<td style="color:var(--color-success, #4caf50);" title="Avg estimated cost per 1,000 responses">' + costStr + '</td>';
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      wrap.appendChild(table);
    }

    const total = document.createElement('div');
    total.style.cssText = 'font-size:0.8em;color:color-mix(in srgb, var(--fg) 40%, transparent);margin-top:12px;text-align:center;';
    total.textContent = filtered.length + ' vote' + (filtered.length !== 1 ? 's' : '') + ' recorded';
    wrap.appendChild(total);

    // Move clear button into wrap so it stays at bottom
    const existingClear = body.querySelector('.scoreboard-clear-btn');
    if (existingClear) wrap.appendChild(existingClear);

    body.appendChild(wrap);
  }

  modes.forEach(mode => {
    const tab = document.createElement('button');
    tab.type = 'button';
    tab.className = 'compare-mode-tab' + (mode === activeMode ? ' active' : '');
    tab.innerHTML = (_TYPE_ICONS[mode] || '') + '<span class="compare-toggle-label">' + modeLabels[mode] + '</span>';
    tab.addEventListener('click', () => {
      activeMode = mode;
      tabBar.querySelectorAll('.compare-mode-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      renderScoreTable();
    });
    tabBar.appendChild(tab);
  });
  body.appendChild(tabBar);
  renderScoreTable();

  // Clear history button
  const clearBtn = document.createElement('button');
  clearBtn.className = 'scoreboard-clear-btn';
  clearBtn.textContent = (window.__t || (k=>k))('compare.reset');
  clearBtn.style.cssText = 'display:block;margin:16px 0 4px auto;padding:4px 12px;background:none;border:1px solid var(--border);color:var(--fg);border-radius:4px;cursor:pointer;font-size:11px;opacity:0.4;transition:opacity 0.15s;';
  clearBtn.addEventListener('mouseenter', () => { clearBtn.style.opacity = '1'; });
  clearBtn.addEventListener('mouseleave', () => { clearBtn.style.opacity = '0.6'; });
  clearBtn.addEventListener('click', () => {
    // Inline confirmation
    const confirmRow = document.createElement('div');
    confirmRow.style.cssText = 'display:flex;gap:8px;justify-content:center;align-items:center;margin-top:8px;padding:8px 12px;border:1px solid color-mix(in srgb, var(--red) 40%, var(--border));border-radius:6px;background:color-mix(in srgb, var(--red) 5%, transparent);';
    const confirmLabel = document.createElement('span');
    confirmLabel.style.cssText = 'font-size:12px;opacity:0.7;';
    confirmLabel.textContent = 'Clear all vote history?';
    const yesBtn = document.createElement('button');
    yesBtn.textContent = (window.__t || (k=>k))('common.delete');
    yesBtn.style.cssText = 'padding:4px 12px;background:var(--red);color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px;font-weight:600;';
    yesBtn.addEventListener('click', () => {
      Storage.setJSON(VOTES_STORAGE_KEY, []);
      overlay.remove();
      showScoreboard();
    });
    const noBtn = document.createElement('button');
    noBtn.textContent = (window.__t || (k=>k))('common.cancel');
    noBtn.className = 'cmp-btn-secondary';
    noBtn.style.cssText = 'padding:4px 12px;border-radius:4px;font-size:12px;';
    noBtn.addEventListener('click', () => confirmRow.remove());
    confirmRow.appendChild(confirmLabel);
    confirmRow.appendChild(yesBtn);
    confirmRow.appendChild(noBtn);
    // Replace button with confirmation
    clearBtn.style.display = 'none';
    clearBtn.parentElement.appendChild(confirmRow);
  });
  body.appendChild(clearBtn);

  content.appendChild(body);
  overlay.appendChild(content);
  document.body.appendChild(overlay);

  if (themeModule && themeModule.makeDraggable) {
    themeModule.makeDraggable(content, header);
  }
}

export default { showScoreboard };
