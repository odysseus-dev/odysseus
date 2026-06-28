// compare/vote.js — voting, revealing, confetti
import Storage from '../storage.js';
import state from './state.js';
import { _modelDisplayNames } from './models.js';
import { getModelCost } from '../chatRenderer.js';
import uiModule from '../ui.js';
import { VOTES_STORAGE_KEY, VOTES_MAX } from './icons.js';
import { showScoreboard } from './scoreboard.js';

var escapeHtml = uiModule.esc;

// ── Helpers imported lazily to avoid circular deps ──
// stopAll and resetCompare live in compare.js; caller must register them.
let _stopAll = null;
let _resetCompare = null;

/** Register external functions that live in compare.js (avoids circular imports). */
function registerCompareActions({ stopAll, resetCompare }) {
  _stopAll = stopAll;
  _resetCompare = resetCompare;
}

function _slotChar(i) { return state._parallel ? String.fromCharCode(65 + i) : String(i + 1); }

function addFinishBadge(paneIdx) {
  const hist = document.getElementById('cmp-history-' + paneIdx);
  if (!hist) return;
  // Find the last AI message's footer
  const lastAi = hist.querySelector('.msg-ai:last-of-type');
  const footer = lastAi && lastAi.querySelector('.msg-footer');
  if (footer) {
    const badge = document.createElement('span');
    badge.className = 'pane-finish-badge';
    badge.textContent = ' · Fastest';
    footer.querySelector('.response-metrics')?.appendChild(badge);
  }
}

/** Build vote/action bar. The per-model "vote for this" buttons live
 *  inside each pane's footer now — this bar carries only the shared
 *  actions (Tie, Reveal, Reset). */
function buildVoteBar(n) {
  const bar = document.getElementById('compare-vote-bar');
  if (!bar) return;
  bar.classList.remove('hidden');

  bar.innerHTML = '';
  // Vote buttons are disabled until a prompt has been sent.
  const noPrompt = !state._lastPrompt;

  // Sync per-pane vote button state to match the prompt-sent / blind-mode
  // state — these elements were created when the panes were built, but
  // their enabled/labelled state needs to refresh whenever this bar is
  // (re)built (e.g. after sending the first prompt or revealing models).
  for (let i = 0; i < n; i++) {
    const paneBtn = document.querySelector('.compare-pane[data-pane="' + i + '"] .pane-vote-btn');
    if (!paneBtn) continue;
    paneBtn.disabled = noPrompt;
    paneBtn.style.opacity = noPrompt ? '0.4' : '';
    const label = state._blindMode
      ? 'Vote ' + _slotChar(i)
      : 'Vote ' + state._selectedModels[i].name;
    paneBtn.querySelector('.pane-vote-label').textContent = label;
  }

  const tieBtn = document.createElement('button');
  tieBtn.className = 'compare-vote-btn compare-vote-tie';
  tieBtn.textContent = 'Tie';
  if (noPrompt) { tieBtn.disabled = true; tieBtn.style.opacity = '0.25'; }
  tieBtn.addEventListener('click', () => handleVote(-1));
  bar.appendChild(tieBtn);

  // Scoreboard button — sits next to Tie. Stays enabled even after a vote (and
  // before a prompt) since viewing the scoreboard is always allowed.
  const scoreBtn = document.createElement('button');
  scoreBtn.className = 'compare-vote-btn compare-score-btn';
  scoreBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:3px;"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>Score';
  scoreBtn.title = 'Scoreboard';
  scoreBtn.addEventListener('click', () => showScoreboard());
  bar.insertBefore(scoreBtn, tieBtn); // furthest left, before Tie

  if (state._blindMode) {
    const revealBtn = document.createElement('button');
    revealBtn.className = 'compare-vote-btn';
    revealBtn.style.opacity = noPrompt ? '0.25' : '0.5';
    revealBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:3px;"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>Reveal';
    if (noPrompt) revealBtn.disabled = true;
    revealBtn.addEventListener('click', () => handleVote(-2));
    bar.appendChild(revealBtn);
  }

  // Add Model button

  // Reset button (always)
  const resetBtn = document.createElement('button');
  resetBtn.className = 'compare-vote-btn compare-rematch-btn';
  resetBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px;margin-right:3px;"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>Reset';
  resetBtn.addEventListener('click', () => { if (_resetCompare) _resetCompare(); });
  bar.appendChild(resetBtn);
}

/** Persist a vote record to localStorage and fire-and-forget to backend. */
function _saveVote(winnerIdx) {
  const modelNames = _modelDisplayNames(state._selectedModels);
  const winner = winnerIdx === -1 ? 'tie' : modelNames[winnerIdx];
  // Calculate per-model costs
  const costs = state._selectedModels.map((m, i) => {
    const pm = state._paneMetrics[i];
    if (!pm) return null;
    return getModelCost(pm.model || m.model, pm.input_tokens || 0, pm.output_tokens || 0);
  });
  const record = {
    models: modelNames,
    winner: winner,
    prompt: state._lastPrompt,
    blind: state._blindMode,
    mode: state._compareMode || 'chat',
    timestamp: Date.now(),
    costs: costs,
  };

  // localStorage persistence
  const votes = Storage.getJSON(VOTES_STORAGE_KEY, []);
  votes.push(record);
  if (votes.length > VOTES_MAX) votes.splice(0, votes.length - VOTES_MAX);
  Storage.setJSON(VOTES_STORAGE_KEY, votes);

  // Fire-and-forget POST to backend
  try {
    fetch(`${state.API_BASE}/api/compare/record`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt: state._lastPrompt,
        models: modelNames,
        winner: winner,
        is_blind: state._blindMode,
      }),
    }).catch(() => {});   // silently ignore errors
  } catch (_) {}
}

/** Reveal model names in pane headers. Highlights winner if one was picked. */
function handleVote(winnerIdx) {
  const displayNames = _modelDisplayNames(state._selectedModels);

  // Reveal only — just show names, keep vote buttons active
  if (winnerIdx === -2) {
    for (let i = 0; i < state._selectedModels.length; i++) {
      const el = document.getElementById('cmp-title-' + i);
      if (el) el.innerHTML = '<strong>' + escapeHtml(displayNames[i]) + '</strong> <span class="pane-title-caret">&#x25BE;</span>';
      const hist = document.getElementById('cmp-history-' + i);
      if (hist) hist.querySelectorAll('.msg-ai .role').forEach(roleEl => {
        if (roleEl.textContent.trim() === 'AI') roleEl.textContent = displayNames[i];
      });
    }
    return;
  }

  // Guard against double-voting — the per-pane vote buttons (.pane-vote-btn)
  // aren't covered by the .compare-vote-btn disable below, so without this a
  // user could spam a pane's vote button and record a score on every click.
  if (state._voted) return;
  state._voted = true;

  // Persist vote
  _saveVote(winnerIdx);

  // Stop any still-streaming panes (user voted early)
  if (state._streaming && _stopAll) _stopAll();

  const panes = document.querySelectorAll('.compare-pane');

  for (let i = 0; i < state._selectedModels.length; i++) {
    const el = document.getElementById('cmp-title-' + i);
    const pane = panes[i];
    if (!el) continue;
    const name = displayNames[i];
    const isWinner = winnerIdx === i;
    const isTie = winnerIdx === -1;

    let html = '';
    const caret = ' <span class="pane-title-caret">&#x25BE;</span>';
    if (isWinner) html = '<span style="color:var(--green, #50fa7b);margin-right:4px;">&#x2605;</span><strong>' + escapeHtml(name) + '</strong> <span style="color:var(--green, #50fa7b);font-size:0.82em;font-weight:800;text-transform:uppercase;letter-spacing:1px;position:relative;top:0;">Winner!</span>' + caret;
    else if (isTie) html = '<span style="opacity:0.5;margin-right:4px;">=</span><strong>' + escapeHtml(name) + '</strong>' + caret;
    else html = '<strong>' + escapeHtml(name) + '</strong>' + caret;
    el.innerHTML = html;

    if (pane) {
      if (isWinner) { pane.classList.add('winner'); }
      else if (winnerIdx >= 0) pane.classList.add('loser'); }
  }

  // Swap "AI" role labels to real model names in each pane's messages
  for (let i = 0; i < state._selectedModels.length; i++) {
    const hist = document.getElementById('cmp-history-' + i);
    if (!hist) continue;
    hist.querySelectorAll('.msg-ai .role').forEach(roleEl => {
      if (roleEl.textContent.trim() === 'AI') {
        roleEl.textContent = displayNames[i];
      }
    });
  }

  // Disable vote buttons but keep reset active — include the per-pane vote
  // buttons (.pane-vote-btn) so they can't be spammed after a vote.
  document.querySelectorAll('.compare-vote-btn:not(.compare-rematch-btn):not(.compare-score-btn), .pane-vote-btn').forEach(b => {
    b.disabled = true; b.style.opacity = '0.4';
  });

  // Confetti burst at the winner's pane header
  if (winnerIdx >= 0) {
    const titleEl = document.getElementById('cmp-title-' + winnerIdx);
    if (titleEl) {
      const rect = titleEl.getBoundingClientRect();
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      spawnConfetti(cx, cy, 50);
      setTimeout(() => spawnConfetti(cx - 30, cy, 25), 150);
      setTimeout(() => spawnConfetti(cx + 30, cy, 25), 300);
    }
  }
}

/** Spawn confetti particles from a point. */
function spawnConfetti(cx, cy, count) {
  const colors = ['#ffd700', '#ff6b6b', '#5b8def', '#51cf66', '#ff922b', '#cc5de8', '#22b8cf', '#fff'];
  for (let i = 0; i < count; i++) {
    const el = document.createElement('div');
    el.className = 'confetti-piece';
    const color = colors[Math.floor(Math.random() * colors.length)];
    const size = 5 + Math.random() * 8;
    const isCircle = Math.random() > 0.5;
    el.style.width = size + 'px';
    el.style.height = (isCircle ? size : size * 0.6) + 'px';
    el.style.background = color;
    el.style.borderRadius = isCircle ? '50%' : '2px';
    el.style.left = cx + 'px';
    el.style.top = cy + 'px';
    const angle = Math.random() * Math.PI * 2;
    const speed = 60 + Math.random() * 160;
    const dx = Math.cos(angle) * speed;
    const dy = Math.sin(angle) * speed - 100;
    const duration = 1.0 + Math.random() * 1.0;
    el.animate([
      { transform: 'translate(0, 0) rotate(0deg) scale(1)', opacity: 1 },
      { transform: `translate(${dx}px, ${dy + 200}px) rotate(${400 + Math.random() * 400}deg) scale(0)`, opacity: 0 }
    ], { duration: duration * 1000, easing: 'cubic-bezier(0.15, 0.6, 0.35, 1)', fill: 'forwards' });
    document.body.appendChild(el);
    setTimeout(() => el.remove(), duration * 1000 + 50);
  }
}

export { _saveVote, handleVote, buildVoteBar, addFinishBadge, spawnConfetti, registerCompareActions };
