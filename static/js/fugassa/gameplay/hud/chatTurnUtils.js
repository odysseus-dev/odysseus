/**
 * Canonical turn-number parsing for Fugassa HUD chat (messages, scene assets, TTS).
 * Keep in one module — ChatPanel and tests must not duplicate this logic.
 */
export function normalizeTurnNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.trunc(n) : null;
}

/** Hidden cast metadata in GM message headers (primary NPCs, secondary player). */
export function sceneCastHeaderMarkup(msg, escapeHtml) {
  if (!msg || msg.role !== 'assistant') return '';
  const cast = msg.scene_cast;
  if (!cast || typeof cast !== 'object') return '';
  const primary = (cast.primary || []).map((n) => String(n).trim()).filter(Boolean);
  const secondary = (cast.secondary || []).map((n) => String(n).trim()).filter(Boolean);
  if (!primary.length && !secondary.length) return '';
  const parts = [];
  if (primary.length) parts.push(`Hlavní: ${primary.join(', ')}`);
  if (secondary.length) parts.push(`Vedlejší: ${secondary.join(', ')}`);
  const label = parts.join(' · ');
  return (
    `<span class="a11y-visually-hidden fugassa-chat-scene-cast" `
    + `data-scene-cast-primary="${escapeHtml(primary.join('|'))}" `
    + `data-scene-cast-secondary="${escapeHtml(secondary.join('|'))}">`
    + `${escapeHtml(label)}</span>`
  );
}
