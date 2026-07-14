import { escapeHtml } from './screens/InventoryScreen.js';
import {
  formatModifier,
  labelize,
  resolveSpellDisplayName,
} from '../wizard/helpers.js';

/** Format a skill/save total bonus (+3, -1, …). */
export function formatBonus(bonus) {
  const n = Number(bonus);
  if (!Number.isFinite(n)) return '—';
  return formatModifier(n);
}

/** Skill row display — never treat bonus as an ability score. */
export function skillModifierDisplay(skill) {
  if (!skill || typeof skill !== 'object') return '—';
  const str = String(skill.modifier_str || '').trim();
  if (str && str !== '-') return str;
  if (skill.bonus != null && skill.bonus !== '') return formatBonus(skill.bonus);
  if (skill.modifier != null && skill.modifier !== '') return formatBonus(skill.modifier);
  return '—';
}

/** Normalize wizard / SQL / homebrew spell id strings to SRD index (e.g. mage-hand). */
export function normalizeSpellIndex(spellId) {
  const raw = String(spellId || '').trim();
  if (!raw) return '';
  const idMatch = raw.match(/^id:spell:(?:cantrip|level\d+):(.+)$/i);
  if (idMatch) return idMatch[1];
  return raw
    .replace(/^hbspell:srdfallback:/i, '')
    .replace(/^hbspell:/i, '')
    .replace(/^id:spell:/i, '');
}

export function formatDescription(desc) {
  if (Array.isArray(desc)) {
    return desc.map((part) => String(part || '').trim()).filter(Boolean).join('\n\n');
  }
  return String(desc || '').trim();
}

export function buildDnd5eCatalogLookups({
  spells = [],
  features = [],
  traits = [],
  feats = [],
} = {}) {
  const spellByIndex = new Map();
  spells.forEach((row) => {
    if (!row?.index) return;
    spellByIndex.set(String(row.index).toLowerCase(), row);
  });

  const featureByIndex = new Map();
  features.forEach((row) => {
    if (!row?.index) return;
    featureByIndex.set(String(row.index).toLowerCase(), row);
  });

  const traitByIndex = new Map();
  traits.forEach((row) => {
    if (!row?.index) return;
    traitByIndex.set(String(row.index).toLowerCase(), row);
  });

  const featByKey = new Map();
  feats.forEach((row) => {
    if (row?.index) featByKey.set(String(row.index).toLowerCase(), row);
    if (row?.name) featByKey.set(String(row.name).toLowerCase(), row);
  });

  return { spellByIndex, featureByIndex, traitByIndex, featByKey };
}

export function resolveSpellEntry(spellId, lookups, spellNames = {}) {
  const index = normalizeSpellIndex(spellId);
  const row = lookups?.spellByIndex?.get(index.toLowerCase()) || null;
  const name = row?.name || resolveSpellDisplayName(spellId, spellNames);
  const description = formatDescription(row?.desc);
  return { id: spellId, index, name, description };
}

export function resolveFeatureEntry(entry, lookups) {
  const index = String(entry?.index || entry?.name || '').trim().toLowerCase();
  const row = lookups?.featureByIndex?.get(index) || null;
  const name = entry?.name || row?.name || labelize(index);
  const description = formatDescription(row?.desc);
  return { ...entry, name, description };
}

export function resolveTraitEntry(entry, lookups) {
  const index = String(entry?.index || entry?.name || '').trim().toLowerCase();
  const row = lookups?.traitByIndex?.get(index) || null;
  const name = entry?.name || row?.name || labelize(index);
  const description = formatDescription(row?.desc);
  return { ...entry, name, description };
}

export function resolveFeatEntry(entry, lookups) {
  const key = String(entry?.index || entry?.name || '').trim().toLowerCase();
  const row = lookups?.featByKey?.get(key) || null;
  const name = entry?.name || row?.name || labelize(key);
  const description = formatDescription(row?.desc);
  return { ...entry, name, description };
}

/** Spell slots as remaining/max per level, e.g. "L1: 2/2 · L2: 1/2". */
export function spellSlotsDisplay(slots = {}, remaining = {}) {
  return Object.entries(slots || {})
    .filter(([, total]) => Number(total) > 0)
    .sort(([a], [b]) => Number(a) - Number(b))
    .map(([lvl, total]) => {
      const max = Number(total) || 0;
      const remRaw = remaining?.[lvl] ?? remaining?.[String(lvl)];
      const rem = remRaw != null ? Number(remRaw) : max;
      return `L${lvl}: ${rem}/${max}`;
    })
    .join(' · ');
}

/**
 * List item with optional expandable description (details/summary).
 * Falls back to plain text when there is no description body.
 */
export function renderDescribedEntry({
  name,
  description = '',
  meta = '',
  className = '',
} = {}) {
  const safeName = escapeHtml(name || '—');
  const safeMeta = meta ? ` <span class="fugassa-muted">${escapeHtml(meta)}</span>` : '';
  const body = formatDescription(description);
  if (!body) {
    return `<li class="fugassa-char-entry${className ? ` ${className}` : ''}"><strong>${safeName}</strong>${safeMeta}</li>`;
  }
  return `
    <li class="fugassa-char-entry fugassa-char-entry--described${className ? ` ${className}` : ''}">
      <details>
        <summary><strong>${safeName}</strong>${safeMeta}</summary>
        <p class="fugassa-char-entry-desc">${escapeHtml(body).replace(/\n/g, '<br />')}</p>
      </details>
    </li>
  `;
}
