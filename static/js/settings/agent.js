// Agent settings panel.
// Extracted verbatim from static/js/settings.js. Behaviour is unchanged: the
// functions moved, their bodies did not. settings.js imports them and calls
// them from the same places, so load order and init sequence are untouched.

import { byId as el } from './dom.js';
import { postSettings as _postSettings } from './api.js';

export async function initAgentSettings() {
  var toolsInput = el('set-agentMaxTools');
  var roundsInput = el('set-agentMaxRounds');
  var supInput = el('set-agentSupervisorLadder');
  var msg = el('set-agentMsg');
  if (!toolsInput) return;

  try {
    var res = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    var settings = await res.json();
    if (settings.agent_max_tool_calls) toolsInput.value = settings.agent_max_tool_calls;
    if (roundsInput && settings.agent_max_rounds) roundsInput.value = settings.agent_max_rounds;
    if (supInput) supInput.checked = !!settings.agent_supervisor_ladder;
  } catch (e) {}

  // Clamp + coerce a raw input to an int in [lo, hi]; falls back to `dflt`
  // when blank/non-numeric. Mirrors the server-side validation.
  function clampInt(raw, lo, hi, dflt) {
    var n = parseInt(raw, 10);
    if (isNaN(n)) return dflt;
    return Math.max(lo, Math.min(n, hi));
  }

  async function save() {
    var tools = clampInt(toolsInput.value, 0, 1000, 0);
    var rounds = roundsInput ? clampInt(roundsInput.value, 1, 200, 20) : null;
    toolsInput.value = tools;                       // reflect the clamped value
    if (roundsInput) roundsInput.value = rounds;
    var payload = { agent_max_tool_calls: tools };
    if (rounds != null) payload.agent_max_rounds = rounds;
    if (supInput) payload.agent_supervisor_ladder = !!supInput.checked;
    try {
      await _postSettings(payload);
      msg.textContent = (tools > 0 ? 'Limit: ' + tools + ' tool calls' : 'Unlimited tool calls') +
        (rounds != null ? ' · ' + rounds + ' steps/message' : '') +
        (supInput && supInput.checked ? ' · supervisor on' : '');
      msg.style.color = 'var(--fg)';
    } catch (e) { msg.textContent = e.status === 403 ? 'Admin access is required to change these settings.' : 'Failed to save'; msg.style.color = 'var(--red)'; }
  }

  toolsInput.addEventListener('change', save);
  if (roundsInput) roundsInput.addEventListener('change', save);
  if (supInput) supInput.addEventListener('change', save);
  var cur = parseInt(toolsInput.value, 10) || 0;
  var curR = roundsInput ? (parseInt(roundsInput.value, 10) || 20) : null;
  msg.textContent = (cur > 0 ? 'Limit: ' + cur + ' tool calls' : 'Unlimited tool calls') +
    (curR != null ? ' · ' + curR + ' steps/message' : '') +
    (supInput && supInput.checked ? ' · supervisor on' : '');

}
