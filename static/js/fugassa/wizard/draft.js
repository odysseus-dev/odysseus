import * as api from '../fugassaApi.js';
import { emptyDraft as defaultDraft } from './defaultDraft.js';
import { clone, FALLBACK_IMAGE_STYLES, WIZARD_TAB_LABELS } from './helpers.js';

export const WIZARD_TABS = WIZARD_TAB_LABELS.map((label, index) => ({
  id: index,
  key: label,
  label,
}));

export function emptyDraft() {
  return defaultDraft();
}

function stripDraftForSave(draft) {
  const copy = clone(draft);
  delete copy._image_styles;
  delete copy.image_styles;
  return copy;
}

async function resolveImageStyles(raw) {
  if (Array.isArray(raw?.image_styles) && raw.image_styles.length) {
    return raw.image_styles;
  }
  try {
    const res = await api.getImageStyles();
    if (Array.isArray(res?.styles) && res.styles.length) return res.styles;
  } catch {
    /* fall through */
  }
  try {
    const cfg = await api.loadConfig();
    if (Array.isArray(cfg?.image_styles) && cfg.image_styles.length) return cfg.image_styles;
  } catch {
    /* fall through */
  }
  return FALLBACK_IMAGE_STYLES;
}

export function normalizeDraft(raw) {
  const base = defaultDraft();
  const merged = {
    ...base,
    ...(raw || {}),
    abilities: { ...base.abilities, ...(raw?.abilities || {}) },
    inventory_structured: { ...base.inventory_structured, ...(raw?.inventory_structured || {}) },
    gear_structured: { ...base.gear_structured, ...(raw?.gear_structured || {}) },
    opening_structured: { ...base.opening_structured, ...(raw?.opening_structured || {}) },
    gm_guides_map: { ...base.gm_guides_map, ...(raw?.gm_guides_map || {}) },
    gm_guides_builtin: { ...base.gm_guides_builtin, ...(raw?.gm_guides_builtin || {}) },
    portrait_appearance: {
      ...base.portrait_appearance,
      ...(raw?.portrait_appearance || {}),
      ...(raw?.portrait_appearance?.rows
        ? { rows: { ...(base.portrait_appearance.rows || {}), ...raw.portrait_appearance.rows } }
        : {}),
    },
    skill_proficiencies: raw?.skill_proficiencies != null
      ? { ...(raw.skill_proficiencies || {}) }
      : { ...base.skill_proficiencies },
    expertise: raw?.expertise != null
      ? { ...(raw.expertise || {}) }
      : { ...base.expertise },
    selected_spells_by_level: raw?.selected_spells_by_level != null
      ? { ...(raw.selected_spells_by_level || raw.selected_spells || {}) }
      : { ...base.selected_spells_by_level },
    selected_cantrips: raw?.selected_cantrips != null
      ? [...(raw.selected_cantrips || [])]
      : [...(base.selected_cantrips || [])],
    asi_choices: { ...base.asi_choices, ...(raw?.asi_choices || {}) },
    homebrew_choices: raw?.homebrew_choices != null
      ? { ...(raw.homebrew_choices || {}) }
      : { ...base.homebrew_choices },
    class_mechanic_choices: raw?.class_mechanic_choices != null
      ? { ...(raw.class_mechanic_choices || {}) }
      : { ...base.class_mechanic_choices },
    homebrew_details: { ...base.homebrew_details, ...(raw?.homebrew_details || {}) },
    sheet_snapshot: { ...base.sheet_snapshot, ...(raw?.sheet_snapshot || {}) },
  };
  delete merged.image_styles;
  if (Array.isArray(raw?.image_styles) && raw.image_styles.length) {
    merged._image_styles = raw.image_styles;
  }
  return clone(merged);
}

export async function loadDraft() {
  const raw = await api.loadWizardDraft();
  const normalized = normalizeDraft(raw);
  if (!Array.isArray(normalized._image_styles) || !normalized._image_styles.length) {
    normalized._image_styles = await resolveImageStyles(raw);
  }
  return normalized;
}

export async function patchDraft(draft) {
  return normalizeDraft(await api.patchWizardDraft(stripDraftForSave(draft)));
}

export async function clearDraft() {
  return normalizeDraft(await api.clearWizardDraft());
}

export function isDraftResumable(draft) {
  if (!draft) return false;
  if (Number(draft.unlocked_tab || 0) > 0) return true;
  if (String(draft.world_name || '').trim() && String(draft.world_name).trim() !== 'New Campaign') return true;
  if (String(draft.world_information || '').trim()) return true;
  if (String(draft.character_background || '').trim()) return true;
  if (String(draft.player_name || '').trim() && String(draft.player_name).trim() !== 'Hero') return true;
  return false;
}

let saveTimer = null;

export function debouncedSaveDraft(draft, delayMs = 300) {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    api.patchWizardDraft(stripDraftForSave(draft)).catch((error) => {
      console.warn('[fugassa wizard] draft autosave failed', error);
    });
  }, delayMs);
}

export function mergeServerDraftInto(state, serverDraft) {
  if (!serverDraft || typeof serverDraft !== 'object') return state;
  const imageStyles = state._image_styles;
  const preserve = {
    selected_cantrips: [...(state.selected_cantrips || [])],
    selected_spells_by_level: { ...(state.selected_spells_by_level || {}) },
    homebrew_choices: { ...(state.homebrew_choices || {}) },
    class_mechanic_choices: { ...(state.class_mechanic_choices || {}) },
    skill_proficiencies: { ...(state.skill_proficiencies || {}) },
    expertise: { ...(state.expertise || {}) },
  };
  const merged = normalizeDraft({ ...state, ...serverDraft });
  if (preserve.selected_cantrips.length && !(merged.selected_cantrips || []).length) {
    merged.selected_cantrips = preserve.selected_cantrips;
  }
  if (Object.keys(preserve.selected_spells_by_level).length
    && !Object.keys(merged.selected_spells_by_level || {}).length) {
    merged.selected_spells_by_level = preserve.selected_spells_by_level;
  }
  if (Object.keys(preserve.homebrew_choices).length
    && !Object.keys(merged.homebrew_choices || {}).length) {
    merged.homebrew_choices = preserve.homebrew_choices;
  }
  if (Object.keys(preserve.class_mechanic_choices).length
    && !Object.keys(merged.class_mechanic_choices || {}).length) {
    merged.class_mechanic_choices = preserve.class_mechanic_choices;
  }
  if (Object.keys(preserve.skill_proficiencies).length
    && !Object.keys(merged.skill_proficiencies || {}).length) {
    merged.skill_proficiencies = preserve.skill_proficiencies;
  }
  if ((!merged._image_styles || !merged._image_styles.length) && imageStyles?.length) {
    merged._image_styles = imageStyles;
  }
  Object.keys(state).forEach((key) => { delete state[key]; });
  Object.assign(state, merged);
  return state;
}

export async function flushDraft(draft) {
  clearTimeout(saveTimer);
  return normalizeDraft(await api.patchWizardDraft(stripDraftForSave(draft)));
}

export async function flushDraftInto(state) {
  return mergeServerDraftInto(state, await flushDraft(state));
}
