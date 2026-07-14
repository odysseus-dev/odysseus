import {
  CLASS_CHOICES,
  GENDER_CHOICES,
  RACE_CHOICES,
  subclassChoicesForClass,
} from './dnd5eOptions.js';

export const WIZARD_TAB_LABELS = [
  'Campaign',
  'Genre',
  'Rules',
  'WorldDefinition',
  'Character',
  'Backstory',
  'Picture',
  'Inventory',
  'Gear',
  'Opening',
  'Summary',
];

export const PLAYSTYLE_OPTIONS = [
  { value: 'hardcore', label: 'Hardcore', framework: 'rules_based' },
  { value: 'adventure', label: 'Adventure', framework: 'rules_based' },
  { value: 'exploration', label: 'Exploration', framework: 'rules_based' },
  { value: 'survival', label: 'Survival', framework: 'rules_based' },
  { value: 'mystery', label: 'Mystery', framework: 'rules_based' },
  { value: 'slice_of_life', label: 'Slice of Life', framework: 'freeform' },
];

export const RULES_MODE_OPTIONS = [
  { value: '5e-style', label: '5e-style' },
  { value: 'homebrew', label: 'Homebrew' },
];

export const RESOLUTION_MODE_OPTIONS = [
  { value: 'dice', label: 'Dice' },
  { value: 'narrative', label: 'Narrative' },
];

export const CAMPAIGN_LENGTH_OPTIONS = ['short', 'medium', 'long', 'epic', 'sandbox'];
export const THEME_OPTIONS = ['Fantasy', 'Sci-fi', 'Modern', 'Present', 'Custom'];
export const POINT_BUY_BUDGET = 27;
export const PORTRAIT_ROW_OPTIONS = {
  height: ['—', 'Very short', 'Short', 'Average height', 'Tall', 'Very tall', 'Custom'],
  build: ['—', 'Slender', 'Average', 'Athletic', 'Stocky', 'Heavyset', 'Custom'],
  muscle: ['—', 'Low muscle tone', 'Average muscle', 'Well-defined', 'Very muscular', 'Custom'],
  hair_style: ['—', 'Straight', 'Wavy', 'Curly', 'Coily', 'Braided', 'Dreadlocks', 'Ponytail', 'Bun / updo', 'Mohawk', 'Slicked back', 'Messy / tousled', 'Shaved sides / undercut', 'Custom'],
  hair_length: ['—', 'Bald / shaved', 'Buzz cut', 'Short', 'Shoulder-length', 'Mid-back', 'Waist+', 'Custom'],
  hair_color: ['—', 'Black', 'Dark brown', 'Brown', 'Auburn / red', 'Blonde', 'Grey / white', 'Dyed / unnatural', 'Custom'],
  skin_tone: ['—', 'Very fair', 'Fair', 'Medium', 'Olive', 'Brown', 'Dark brown', 'Fantasy tint (blue/green/etc.)', 'Custom'],
  facial_hair: ['—', 'Clean-shaven', 'Light stubble', 'Heavy stubble', 'Short beard', 'Full beard', 'Goatee', 'Moustache only', 'Sideburns', 'Custom'],
  accessories: ['—', 'None notable', 'Glasses', 'Jewelry', 'Scars / tattoos', 'Hat / hood', 'Tech / cyber detail', 'Custom'],
  ethnic_appearance: ['—', 'East Asian', 'South Asian', 'African', 'Middle Eastern', 'European', 'Latin', 'Pacific / Indigenous', 'Mixed / ambiguous', 'Custom'],
};

export const PORTRAIT_ROW_LABELS = {
  height: 'Height',
  build: 'Build',
  muscle: 'Muscle',
  hair_style: 'Hair style',
  hair_length: 'Hair length',
  hair_color: 'Hair color',
  skin_tone: 'Skin tone',
  facial_hair: 'Facial hair',
  accessories: 'Accessories',
  ethnic_appearance: 'Ethnic / regional look',
};

export function themeLabel(themeMode, customTheme) {
  const mode = String(themeMode || 'Fantasy').trim();
  if (mode === 'Custom') return String(customTheme || '').trim() || 'Custom';
  return mode || 'Fantasy';
}

const THEME_IMAGE_STYLE_HINTS = {
  Fantasy: 'realistic',
  'Sci-fi': 'krea',
  Modern: 'realistic',
  Present: 'realistic',
  Custom: 'realistic',
};

export const FALLBACK_IMAGE_STYLES = [
  { id: 'realistic', label: 'Realistic' },
  { id: 'anime', label: 'Anime' },
  { id: 'pixelart', label: 'Pixel art' },
  { id: 'krea', label: 'Krea' },
];

export function defaultImageStyleHintForTheme(themeMode) {
  return THEME_IMAGE_STYLE_HINTS[String(themeMode || 'Fantasy').trim()] || 'realistic';
}

export const GENERIC_FANTASY_CURRENCY = ['bronze', 'silver', 'gold'];

// Mirrors `titan/fugassa/starting_wealth.py` — preview only; applied at Create in
// `game_bootstrap.apply_wizard_draft()`.
const WEALTH_KEYWORDS = [
  [4, ['noble', 'aristocrat', 'lord', 'lady', 'baron', 'baroness', 'duke', 'duchess', 'count', 'countess', 'knight', 'royal', 'princess', 'prince', 'wealthy', 'rich', 'heir', 'dynasty', 'courtier', 'gentry']],
  [3, ['merchant', 'trader', 'shopkeeper', 'guildmaster', 'officer', 'captain', 'landowner', 'proprietor']],
  [2, ['soldier', 'guard', 'artisan', 'apprentice', 'acolyte', 'hermit', 'sailor', 'farmer', 'blacksmith', 'scholar', 'craftsman']],
  [1, ['wanderer', 'drifter', 'vagabond', 'urchin', 'peasant', 'laborer', 'dockworker', 'thief', 'orphan', 'commoner']],
  [0, ['beggar', 'destitute', 'penniless', 'broke', 'outcast', 'slave', 'fugitive']],
];

const WEALTH_TIER_LABELS = {
  0: 'destitute',
  1: 'modest wanderer',
  2: 'working / soldier',
  3: 'merchant / tradesman',
  4: 'noble / wealthy',
};

export function wealthTierForBackground(background) {
  const bg = String(background || '').toLowerCase();
  if (!bg.trim()) return 1;
  let best = 1;
  WEALTH_KEYWORDS.forEach(([tier, words]) => {
    if (words.some((word) => bg.includes(word))) best = Math.max(best, tier);
  });
  return best;
}

export function startingCurrencyGrants(background, currency, { level = 1 } = {}) {
  const tiers = (currency || []).map((c) => String(c).trim()).filter(Boolean);
  const low = tiers[0] || 'bronze';
  const mid = tiers[1] || tiers[0] || 'silver';
  const high = tiers[tiers.length - 1] || 'gold';
  const wealth = wealthTierForBackground(background);
  const lvl = Math.max(1, Number(level || 1));
  const scale = 1.0 + Math.min(lvl - 1, 9) * 0.05;
  const grants = [];
  if (wealth >= 4) {
    grants.push({ name: high, qty: Math.max(5, Math.round(15 * scale)) });
    grants.push({ name: mid, qty: Math.max(2, Math.round(8 * scale)) });
  } else if (wealth === 3) {
    grants.push({ name: mid, qty: Math.max(5, Math.round(12 * scale)) });
    grants.push({ name: low, qty: Math.max(10, Math.round(25 * scale)) });
  } else if (wealth === 2) {
    grants.push({ name: low, qty: Math.max(15, Math.round(30 * scale)) });
    grants.push({ name: mid, qty: Math.max(2, Math.round(5 * scale)) });
  } else if (wealth === 1) {
    grants.push({ name: low, qty: Math.max(8, Math.round(10 * scale)) });
  } else {
    grants.push({ name: low, qty: Math.max(2, Math.round(5 * scale)) });
  }
  return grants;
}

function inventoryHasCurrency(structured, currency) {
  const names = new Set(
    (currency || []).map((c) => String(c).trim().toLowerCase()).filter(Boolean),
  );
  if (!names.size) ['bronze', 'silver', 'gold'].forEach((c) => names.add(c));
  const items = Array.isArray(structured?.items) ? structured.items : [];
  return items
    .filter((item) => names.has(String(item?.name || '').trim().toLowerCase()))
    .map((item) => ({
      name: String(item.name).trim(),
      qty: Math.max(1, Number(item.quantity || 1)),
    }));
}

export function startingWealthPreview(draft) {
  const background = String(draft?.character_background || '').trim();
  const currency = Array.isArray(draft?.currency) && draft.currency.length
    ? draft.currency
    : [...GENERIC_FANTASY_CURRENCY];
  const level = Number(draft?.level || 1);
  const tier = wealthTierForBackground(background);
  const existingCurrency = inventoryHasCurrency(draft?.inventory_structured, currency);
  if (existingCurrency.length) {
    return { tier, skipped: true, grants: [], existingCurrency };
  }
  return {
    tier,
    skipped: false,
    grants: startingCurrencyGrants(background, currency, { level }),
    existingCurrency: [],
  };
}

export function startingWealthSummaryLines(draft) {
  const preview = startingWealthPreview(draft);
  const lines = [
    'Starting Wealth (at Create)',
    `Wealth tier: ${WEALTH_TIER_LABELS[preview.tier] || 'modest wanderer'} (from backstory)`,
  ];
  if (preview.skipped) {
    const listed = preview.existingCurrency.map((c) => `${c.qty} ${c.name}`).join(', ');
    lines.push(`Not auto-granted — inventory already lists currency: ${listed}`);
  } else if (preview.grants.length) {
    preview.grants.forEach((g) => lines.push(`${g.qty} ${g.name}`));
  } else {
    lines.push('(none)');
  }
  return lines;
}

// Mirrors `titan/fugassa/game_bootstrap.py::default_currency_for_theme` — used
// so the Inventory tab shows a setting-appropriate currency by default instead
// of always falling back to generic fantasy coins, even before the player
// ever asks the LLM to suggest/regenerate one.
export function defaultCurrencyForTheme(theme) {
  const t = String(theme || '').toLowerCase();
  if (t.includes('sci')) return ['credits', 'data chips', 'reactor cores'];
  if (t.includes('modern') || t.includes('present')) return ['coins', 'bills', 'certificates'];
  return [...GENERIC_FANTASY_CURRENCY];
}

export function rulesContext(draft) {
  return {
    playstyle_framework: playstyleFramework(draft?.playstyle || 'adventure'),
    playstyle: draft?.playstyle || 'adventure',
    rules_mode: draft?.rules_mode || '5e-style',
    resolution_mode: draft?.resolution_mode || 'dice',
    level: Number(draft?.level || 1),
    character_class: effectiveClass(draft),
    race: effectiveRace(draft),
    background: String(draft?.character_background || '').trim(),
  };
}

export function playstyleFramework(playstyle) {
  return PLAYSTYLE_OPTIONS.find((item) => item.value === playstyle)?.framework || 'rules_based';
}

export function playstyleLabel(playstyle) {
  return PLAYSTYLE_OPTIONS.find((item) => item.value === playstyle)?.label || 'Adventure';
}

export function effectiveGender(draft) {
  const idx = Number.isFinite(Number(draft?.player_gender_idx)) ? Number(draft.player_gender_idx) : 0;
  const label = GENDER_CHOICES[idx] || GENDER_CHOICES[0];
  return label === 'Custom' ? (draft?.player_gender_custom || '').trim() : label;
}

export function effectiveRace(draft) {
  const idx = Number.isFinite(Number(draft?.player_race_idx)) ? Number(draft.player_race_idx) : 0;
  const label = RACE_CHOICES[idx] || RACE_CHOICES[0];
  return label === 'Custom' ? (draft?.player_race_custom || '').trim() : label;
}

export function effectiveSubrace(draft) {
  const custom = String(draft?.player_subrace_custom || '').trim();
  if (custom) return custom;
  const options = draft?._subrace_options || [];
  const idx = Number.isFinite(Number(draft?.player_subrace_idx)) ? Number(draft.player_subrace_idx) : 0;
  if (Array.isArray(options) && options[idx]) return String(options[idx]);
  return '';
}

export function effectiveClass(draft) {
  const idx = Number.isFinite(Number(draft?.player_class_idx)) ? Number(draft.player_class_idx) : 0;
  const label = CLASS_CHOICES[idx] || CLASS_CHOICES[0];
  return label === 'Custom' ? (draft?.player_class_custom || '').trim() : label;
}

export function effectiveSubclass(draft) {
  const level = Number(draft?.level || 1);
  if (level < 3) return '';
  const className = effectiveClass(draft);
  if (!className) return '';
  if (className === (draft?.player_class_custom || '').trim()) {
    return String(draft?.player_subclass_custom || '').trim();
  }
  const subclasses = subclassChoicesForClass(className);
  const idx = Number.isFinite(Number(draft?.player_subclass_idx)) ? Number(draft.player_subclass_idx) : 0;
  return subclasses[idx] || '';
}

export function cumulativeWorldContext(draft) {
  return String(draft?.world_information || '').trim();
}

export function inventoryGearWizardContext(draft) {
  const parts = [];
  const world = cumulativeWorldContext(draft);
  if (world) parts.push(`World:\n${world}`);
  const backstory = String(draft?.character_background || '').trim();
  if (backstory) {
    parts.push(
      'Character backstory (anchor inventory/gear to items and equipment explicitly mentioned here when sensible):\n'
      + backstory,
    );
  }
  return parts.join('\n\n');
}

export function characterProfile(draft) {
  const bits = [];
  const name = String(draft?.player_name || '').trim();
  const gender = effectiveGender(draft);
  const race = effectiveRace(draft);
  const subrace = effectiveSubrace(draft);
  const klass = effectiveClass(draft);
  const subclass = effectiveSubclass(draft);
  const age = String(draft?.player_age || '').trim();
  const level = Number(draft?.level || 1);
  if (name) bits.push(`Name: ${name}`);
  if (gender) bits.push(`Gender: ${gender}`);
  if (race) bits.push(`Race: ${race}`);
  if (subrace) bits.push(`Subrace: ${subrace}`);
  if (klass) bits.push(`Class: ${klass}`);
  if (subclass) bits.push(`Subclass: ${subclass}`);
  if (age) bits.push(`Age: ${age}`);
  bits.push(`Level: ${level}`);
  const abilities = draft?.abilities || {};
  const abilitySummary = ['str', 'dex', 'con', 'int', 'wis', 'cha']
    .map((key) => `${key.toUpperCase()} ${Number(abilities[key] ?? 10)}`)
    .join(', ');
  bits.push(`Abilities: ${abilitySummary}`);
  const snap = draft?.sheet_snapshot || {};
  if (Array.isArray(snap.cantrips) && snap.cantrips.length) {
    bits.push(`Cantrips: ${snap.cantrips.map((c) => c.name || c.id || c).join(', ')}`);
  } else {
    const sc = draft?.character_sheet?.stable_sheet?.spellcasting || snap.spellcasting;
    if (sc) {
      const cantrips = sc.cantrips || [];
      const spells = sc.spells_known || [];
      if (cantrips.length) bits.push(`Cantrips: ${cantrips.join(', ')}`);
      if (spells.length) bits.push(`Spells: ${spells.join(', ')}`);
      if (sc.save_dc) bits.push(`Spell DC ${sc.save_dc}`);
    }
  }
  if (Array.isArray(snap.homebrew_choices) && snap.homebrew_choices.length) {
    bits.push(
      `Choices: ${snap.homebrew_choices.map((c) => `${c.label}: ${c.value}`).join('; ')}`,
    );
  }
  const llm = draft?.character_sheet?.llm_summary || {};
  if (llm.feature_summary) bits.push(`Features: ${llm.feature_summary}`);
  return bits.join('\n');
}

export function dialogTranscript(messages) {
  return (messages || [])
    .filter((item) => item && item.content)
    .map((item) => `${item.role === 'user' ? 'YOU' : 'GAME MASTER'}: ${String(item.content).trim()}`)
    .join('\n\n');
}

export function inventoryStructuredFromDraft(draft) {
  const structured = draft?.inventory_structured || {};
  const items = Array.isArray(structured?.items) ? structured.items : [];
  const currency = Array.isArray(draft?.currency) && draft.currency.length
    ? draft.currency.slice(0, 3)
    : [...GENERIC_FANTASY_CURRENCY];
  return { items, currency };
}

export function currencyConversionHint(currency) {
  const tiers = Array.isArray(currency) ? currency : [];
  const low = tiers[0] || 'bronze';
  const mid = tiers[1] || 'silver';
  const high = tiers[2] || 'gold';
  return `100 ${low} = 1 ${mid} | 100 ${mid} = 1 ${high}`;
}

export function formatInventoryOptionsForChat(text, optStart = 1) {
  const raw = String(text || '').trim();
  if (!raw.startsWith('{')) return null;
  try {
    const parsed = JSON.parse(raw);
    const options = parsed?.options;
    if (!Array.isArray(options) || !options.length) return null;
    const start = Number(optStart) || 1;
    const parts = [];
    options.slice(0, 3).forEach((option, index) => {
      parts.push(`Option ${start + index}: ${option?.title || ''}`);
      for (const item of option?.items || []) {
        parts.push(
          `- ${item?.name || ''} x${item?.quantity ?? 1} [${item?.usage || ''}] — ${item?.description || ''}`,
        );
      }
      if (Array.isArray(option?.currency) && option.currency.length) {
        parts.push(`Currency: ${option.currency.join(', ')}`);
      }
      parts.push('');
    });
    const count = Math.min(options.length, 3);
    if (count >= 3) {
      parts.push(`Choose one option (${start} / ${start + 1} / ${start + 2}), send your own inventory, or ask for new options.`);
    } else if (count === 2) {
      parts.push(`Choose option ${start} or ${start + 1}, send your own inventory, or ask for new options.`);
    } else {
      parts.push(`Choose option ${start}, send your own inventory, or ask for new options.`);
    }
    return parts.join('\n').trim();
  } catch {
    return null;
  }
}

function repairGearOptionsJsonish(text) {
  return String(text || '').replace(/(\]\s*)\},\s*\{\s*"title"/g, '$1}},{"title"');
}

function extractJsonObjectAt(raw, start = 0) {
  const s = String(raw || '');
  const brace = s.indexOf('{', start);
  if (brace < 0) return null;
  let depth = 0;
  let inString = false;
  let escapeNext = false;
  for (let idx = brace; idx < s.length; idx += 1) {
    const char = s[idx];
    if (escapeNext) {
      escapeNext = false;
      continue;
    }
    if (char === '\\') {
      escapeNext = true;
      continue;
    }
    if (char === '"') {
      inString = !inString;
      continue;
    }
    if (inString) continue;
    if (char === '{') depth += 1;
    else if (char === '}') {
      depth -= 1;
      if (depth === 0) {
        try {
          return JSON.parse(s.slice(brace, idx + 1));
        } catch {
          return null;
        }
      }
    }
  }
  return null;
}

export function salvageGearOptions(raw) {
  const src = repairGearOptionsJsonish(raw);
  const options = [];
  const seen = new Set();
  const re = /"title"\s*:\s*"/g;
  let match = re.exec(src);
  while (match) {
    const brace = src.lastIndexOf('{', match.index);
    const obj = brace >= 0 ? extractJsonObjectAt(src, brace) : null;
    const title = String(obj?.title || '').trim();
    if (obj?.weapon && obj?.armor && title && !seen.has(title.toLowerCase())) {
      seen.add(title.toLowerCase());
      options.push(obj);
    }
    match = re.exec(src);
  }
  return options;
}

export function parseGearOptionsRaw(text) {
  const raw = repairGearOptionsJsonish(String(text || '').trim());
  if (!raw) return [];
  if (raw.startsWith('{')) {
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed?.options)) {
        return parsed.options.filter((opt) => opt && typeof opt === 'object');
      }
    } catch {
      /* salvage below */
    }
  }
  return salvageGearOptions(raw);
}

export function formatGearOptionsForChat(text, optStart = 1) {
  const options = parseGearOptionsRaw(text);
  if (!options.length) return null;
  const start = Number(optStart) || 1;
  const parts = [];
  options.slice(0, 3).forEach((option, index) => {
    const weapon = option?.weapon || {};
    const armor = option?.armor || {};
    parts.push(`Option ${start + index}: ${option?.title || ''}`);
    parts.push(
      `Weapon: ${weapon.name || ''} (${weapon.damage || ''}) — ${weapon.description || ''}`.trim(),
    );
    parts.push(
      `Armor: ${armor.name || ''} (AC ${armor.ac ?? ''}) — ${armor.description || ''}`.trim(),
    );
    parts.push('');
  });
  const count = Math.min(options.length, 3);
  if (count >= 3) {
    parts.push(`Choose one option (${start} / ${start + 1} / ${start + 2}), send your own gear, or ask for new options.`);
  } else if (count === 2) {
    parts.push(`Choose option ${start} or ${start + 1}, send your own gear, or ask for new options.`);
  } else {
    parts.push(`Choose option ${start}, send your own gear, or ask for new options.`);
  }
  return parts.join('\n').trim();
}

export function extractGearJson(text) {
  const raw = String(text || '').trim();
  if (!raw) return null;
  if (raw.startsWith('{')) {
    try {
      const parsed = JSON.parse(raw);
      if (parsed?.weapon && parsed?.armor) return parsed;
    } catch {
      /* fall through */
    }
  }
  const obj = extractJsonObjectAt(raw, 0);
  if (obj?.weapon && obj?.armor) return obj;
  return null;
}

export function optionBatchHelpers(kind) {
  const keyMap = {
    world: {
      text: 'world_campaign_options_text',
      next: 'world_options_next_start',
      batch: 'world_campaign_batch_start',
    },
    backstory: {
      text: 'backstory_options_text',
      next: 'backstory_options_next_start',
      batch: 'backstory_options_batch_start',
    },
    inventory: {
      text: 'inventory_options_raw',
      next: 'inventory_options_next_start',
      batch: 'inventory_options_batch_start',
    },
    gear: {
      text: 'gear_options_raw',
      next: 'gear_options_next_start',
      batch: 'gear_options_batch_start',
    },
    opening: {
      text: 'opening_options_raw',
      next: 'opening_options_next_start',
      batch: 'opening_options_batch_start',
    },
  };
  return keyMap[kind];
}

export function inferOptionNumber(text, batchStart = 1) {
  const match = String(text || '').match(/\b(?:option|campaign)\s+(\d+)\b/i) || String(text || '').match(/\b(\d+)\b/);
  if (!match) return 0;
  const picked = Number(match[1]);
  if (!Number.isFinite(picked) || picked <= 0) return 0;
  return picked >= batchStart ? picked : 0;
}

export function requestAfterOptionPick(text, pickedGlobal) {
  const src = String(text || '');
  if (!pickedGlobal) return src.trim();
  return src.replace(new RegExp(`\\b(?:option|campaign)\\s+${pickedGlobal}\\b`, 'ig'), '').trim();
}

export function nextOptionStart(current) {
  const n = Number(current || 1);
  return Number.isFinite(n) && n > 0 ? n : 1;
}

export function pointBuyCost(score) {
  const table = { 8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9 };
  return table[Number(score)] ?? -1;
}

export function pointBuySpent(abilities) {
  return ['str', 'dex', 'con', 'int', 'wis', 'cha']
    .reduce((sum, key) => sum + Math.max(0, pointBuyCost(Number(abilities?.[key] ?? 8))), 0);
}

export function abilityMod(score) {
  return Math.floor((Number(score || 10) - 10) / 2);
}

export function formatModifier(mod) {
  return mod >= 0 ? `+${mod}` : String(mod);
}

export function escapeHtml(value) {
  const div = document.createElement('div');
  div.textContent = value == null ? '' : String(value);
  return div.innerHTML;
}

export function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

export function normalizeChatMessages(items) {
  return Array.isArray(items) ? items.filter((item) => item && typeof item === 'object').map((item) => ({
    role: item.role === 'user' ? 'user' : 'assistant',
    content: String(item.content || ''),
  })) : [];
}

export function builtInGuides() {
  return {
    'gm_custom_1.txt': [
      'Act as a dramatic but fair game master.',
      'Honor the chosen playstyle, rules mode, and resolution mode.',
      'Use the world information, backstory, inventory, gear, and opening as canon.',
    ].join('\n'),
    'gm_noir.txt': [
      'Lean into tension, secrets, and costly choices.',
      'Keep scenes vivid and NPC motives layered.',
      'Respect player agency over plot rails.',
    ].join('\n'),
    'gm_pulp.txt': [
      'Favor momentum, cliffhangers, and larger-than-life reveals.',
      'Escalate danger quickly, then offer bold responses.',
      'Keep exposition concise and playable.',
    ].join('\n'),
  };
}

export function combinedGuidesText(guidesMap) {
  const entries = Object.entries(guidesMap || {});
  return entries.map(([name, text]) => `${name}\n${String(text || '').trim()}`).join('\n\n');
}

export function portraitAppearanceToText(draft) {
  const root = draft?.portrait_appearance || {};
  const rows = root.rows || {};
  const lines = [];
  Object.keys(PORTRAIT_ROW_OPTIONS).forEach((key) => {
    const row = rows[key] || {};
    const options = PORTRAIT_ROW_OPTIONS[key];
    const idx = Math.max(0, Math.min(options.length - 1, Number(row.i || 0)));
    const label = PORTRAIT_ROW_LABELS[key] || key;
    if (idx === 0) return;
    if (idx === options.length - 1) {
      const custom = String(row.t || '').trim();
      if (custom) lines.push(`${label}: ${custom}`);
      return;
    }
    lines.push(`${label}: ${options[idx]}`);
  });
  const notes = String(root.notes || '').trim();
  if (notes) lines.push(`Player notes: ${notes}`);
  return lines.join('\n');
}

export function inventoryNotesFromStructured(structured) {
  const items = Array.isArray(structured?.items) ? structured.items : [];
  return items.map((item) => inventoryLine(item)).filter(Boolean).join('\n');
}

export function inventoryLine(item) {
  if (!item || typeof item !== 'object') return '';
  const qty = Number(item.quantity || 1);
  const name = String(item.name || item.item || '').trim();
  const notes = String(item.notes || item.description || '').trim();
  if (!name) return '';
  return `${qty > 1 ? `${qty}x ` : ''}${name}${notes ? ` — ${notes}` : ''}`;
}

export function gearSummaryText(structured) {
  const weapon = structured?.weapon || {};
  const armor = structured?.armor || {};
  return {
    weapon: Object.entries(weapon).map(([k, v]) => `${labelize(k)}: ${v}`).join('\n'),
    armor: Object.entries(armor).map(([k, v]) => `${labelize(k)}: ${v}`).join('\n'),
  };
}

export function labelize(value) {
  return String(value || '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (m) => m.toUpperCase());
}

const SKILL_LABELS = {
  acrobatics: 'Acrobatics',
  'animal-handling': 'Animal Handling',
  arcana: 'Arcana',
  athletics: 'Athletics',
  deception: 'Deception',
  history: 'History',
  insight: 'Insight',
  intimidation: 'Intimidation',
  investigation: 'Investigation',
  medicine: 'Medicine',
  nature: 'Nature',
  perception: 'Perception',
  performance: 'Performance',
  persuasion: 'Persuasion',
  religion: 'Religion',
  'sleight-of-hand': 'Sleight of Hand',
  stealth: 'Stealth',
  survival: 'Survival',
};

export function resolveSkillLabel(skillId) {
  const key = String(skillId || '').trim();
  return SKILL_LABELS[key] || labelize(key.replace(/-/g, ' '));
}

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

export function spellIdToFallbackLabel(spellId) {
  return labelize(normalizeSpellIndex(spellId).replace(/-/g, ' '));
}

export function resolveSpellDisplayName(spellId, spellNames = {}) {
  const id = String(spellId || '').trim();
  if (!id) return '';
  if (spellNames[id]) return spellNames[id];
  const core = normalizeSpellIndex(id);
  if (spellNames[core]) return spellNames[core];
  return spellIdToFallbackLabel(id);
}

export function buildWizardSheetSnapshot(draft, sheet, spellNames = {}) {
  const labels = sheet?.labels || {};
  const abilities = sheet?.abilities || {};
  const cantrips = (draft?.selected_cantrips || []).map((id) => ({
    id,
    name: resolveSpellDisplayName(id, spellNames),
  }));
  const spellsByLevel = {};
  Object.entries(draft?.selected_spells_by_level || {}).forEach(([lvl, bucket]) => {
    const ids = Array.isArray(bucket)
      ? bucket
      : Object.entries(bucket || {}).filter(([, on]) => on).map(([k]) => k);
    if (!ids.length) return;
    spellsByLevel[lvl] = ids.map((id) => ({
      id,
      name: resolveSpellDisplayName(id, spellNames),
    }));
  });
  const pending = sheet?.homebrew_pending_choices || [];
  const homebrewChoices = pending
    .map((choice) => {
      const raw = String((draft?.homebrew_choices || {})[choice.id] || '').trim();
      if (!raw) return null;
      const value = choice.type === 'skill_any' ? resolveSkillLabel(raw) : raw;
      return { id: choice.id, label: choice.label || choice.id, value };
    })
    .filter(Boolean);
  const proficientSkills = (sheet?.skills || [])
    .filter((row) => row?.proficient)
    .map((row) => `${row.name} ${row.modifier_str || ''}`.trim());
  const expertiseSkills = (sheet?.skills || [])
    .filter((row) => row?.expertise)
    .map((row) => `${row.name} ${row.modifier_str || ''}`.trim());
  const sc = sheet?.spellcasting || null;
  return {
    level: sheet?.level ?? draft?.level ?? 1,
    class_name: labels.class || '',
    subclass_name: labels.subclass || '',
    race_name: labels.race || '',
    subrace_name: labels.subrace || '',
    proficiency_bonus: sheet?.proficiency_bonus ?? 2,
    hit_die: sheet?.hit_die ?? 8,
    hp: sheet?.hp ?? 0,
    ac_base: sheet?.ac_base ?? 10,
    speed: sheet?.speed ?? 30,
    passive_perception: sheet?.passive_perception ?? 10,
    abilities: { ...abilities },
    saves: (sheet?.saving_throws || []).map(
      (s) => `${s.proficient ? '●' : '○'} ${String(s.ability).toUpperCase()} ${s.modifier_str}`,
    ),
    skills: (sheet?.skills || []).map(
      (s) => `${s.proficient ? '●' : '○'}${s.expertise ? '◆' : ''} ${s.name} ${s.modifier_str}`,
    ),
    proficient_skills: proficientSkills,
    expertise_skills: expertiseSkills,
    cantrips,
    spells_by_level: spellsByLevel,
    homebrew_choices: homebrewChoices,
    spellcasting: sc?.has
      ? {
          model: sc.model,
          ability: sc.ability,
          cantrips_known: sc.cantrips_known,
          spells_known: sc.spells_known,
          spells_prepared_estimate: sc.spells_prepared_estimate,
          spell_save_dc: sc.spell_save_dc,
          spell_attack_mod: sc.spell_attack_mod,
        }
      : null,
    class_resources: sheet?.class_resources || {},
    class_mechanic_selections: sheet?.class_mechanic_selections || [],
    class_resource_summary: sheet?.class_resource_summary || [],
  };
}

export function wizardCharacterSummaryLines(draft, snap, { includeSpellcastingHeader = true } = {}) {
  const sheet = snap || draft?.sheet_snapshot || {};
  const lines = [];
  if (sheet.homebrew_choices?.length) {
    lines.push('Required choices', ...sheet.homebrew_choices.map((c) => `  ${c.label}: ${c.value}`));
  }
  const hasCantrips = Array.isArray(sheet.cantrips) && sheet.cantrips.length;
  const spellLevels = Object.keys(sheet.spells_by_level || {}).sort(
    (a, b) => Number(a) - Number(b),
  );
  const hasSpells = spellLevels.some((lvl) => (sheet.spells_by_level[lvl] || []).length);
  if (includeSpellcastingHeader && sheet.spellcasting?.spell_save_dc && (hasCantrips || hasSpells)) {
    lines.push(
      `Spellcasting: ${sheet.spellcasting.model || '—'} (${String(sheet.spellcasting.ability || '').toUpperCase()}) · DC ${sheet.spellcasting.spell_save_dc}`,
    );
  }
  if (hasCantrips) {
    lines.push('Cantrips', ...sheet.cantrips.map((c) => `  ${c.name || c.id || c}`));
  }
  spellLevels.forEach((lvl) => {
    const picks = sheet.spells_by_level[lvl] || [];
    if (!picks.length) return;
    lines.push(`Spells level ${lvl}`, ...picks.map((s) => `  ${s.name || s.id || s}`));
  });
  const mechanicLabels = new Set();
  if (sheet.class_mechanic_selections?.length) {
    sheet.class_mechanic_selections.forEach((block) => {
      const names = (block.values || []).map((v) => v.name || v.id).filter(Boolean);
      if (!names.length) return;
      const label = String(block.label || block.id || '').trim();
      if (label) mechanicLabels.add(label.toLowerCase());
      lines.push(`${label || block.id}: ${names.join(', ')}`);
    });
  }
  if (sheet.class_resource_summary?.length) {
    const extra = sheet.class_resource_summary
      .map((line) => String(line || '').trim())
      .filter((line) => {
        if (!line) return false;
        const key = line.split(':')[0].trim().toLowerCase();
        return !mechanicLabels.has(key);
      });
    if (extra.length) {
      lines.push('Class resources', ...extra.map((line) => `  ${line}`));
    }
  }
  return lines;
}

export function openingStructuredFromDraft(draft) {
  return {
    opening_text: String(draft?.opening_hook || '').trim(),
    time_hint: String(draft?.opening_time_hint || '').trim(),
  };
}
