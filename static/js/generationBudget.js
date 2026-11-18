// Presentation of the backend's generation_budget report (native chat JSON/history and the SSE generation_budget event).
// The backend owns every number: this module only validates shape and displays it. It never recomputes a budget.

const _isCount = v => typeof v === 'number' && Number.isFinite(v) && Number.isInteger(v);

/** Return the report when it has the established shape, else null (absent/older/malformed metadata is simply not shown). */
export function normalizeGenerationBudget(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  if (!_isCount(raw.requested_max_tokens) || !_isCount(raw.effective_max_tokens)) return null;
  return raw;
}

/** Pure description of what to tell the user, or null when the backend reports a fit with nothing to warn about. */
export function describeGenerationBudget(raw) {
  const b = normalizeGenerationBudget(raw);
  if (!b) return null;
  const blocked = b.safe_to_generate === false;
  const reduced = b.effective_max_tokens < b.requested_max_tokens;
  const warning = typeof b.warning === 'string' && b.warning.trim() ? b.warning.trim() : '';
  if (!blocked && !reduced && !warning) return null;
  const lines = [];
  lines.push(blocked
    ? `No output space is available (requested ${b.requested_max_tokens} tokens, effective ${b.effective_max_tokens}).`
    : `Output limit reduced: requested ${b.requested_max_tokens} tokens, effective ${b.effective_max_tokens}.`);
  if (warning) lines.push(warning);
  if (_isCount(b.context_length) && _isCount(b.estimated_input_tokens)) {
    lines.push(`Context ${b.context_length} tokens; prompt and reserves use about ${b.estimated_input_tokens}.`);
  }
  lines.push('To resolve: choose a larger context length for this model, or shorten the prompt or conversation.');
  return { level: blocked ? 'blocked' : 'reduced', lines };
}

/** Build the notice element (textContent only). It is a sibling of the message body, never part of the reply text. */
export function buildGenerationBudgetNotice(raw, doc = document) {
  const info = describeGenerationBudget(raw);
  if (!info) return null;
  const el = doc.createElement('div');
  el.className = `generation-budget-notice generation-budget-${info.level}`;
  el.setAttribute('role', 'note');
  el.dataset.generationBudget = info.level;
  info.lines.forEach((text, i) => {
    const line = doc.createElement(i === 0 ? 'strong' : 'div');
    line.textContent = text;
    el.appendChild(line);
  });
  return el;
}

/** Idempotently show (or clear) the notice on a message wrapper; the latest report wins (e.g. a fallback candidate). */
export function showGenerationBudgetNotice(host, raw, doc = document) {
  if (!host) return null;
  host.querySelectorAll(':scope > .generation-budget-notice').forEach(n => n.remove());
  const el = buildGenerationBudgetNotice(raw, doc);
  if (el) host.appendChild(el);
  return el;
}
