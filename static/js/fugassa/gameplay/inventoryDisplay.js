/**
 * Inventory + wallet display helpers.
 *
 * Campaign currency has two layers in game state:
 * - `world_profile.currency` — three tier *names* (low / mid / high)
 * - `inventory.shared` — actual holdings as `{ name, qty }` rows whose names
 *   match those tiers (plus gear items in the same list)
 *
 * Gameplay UI must never show tier names alone — always resolve quantities from
 * shared inventory (same source as quest rewards / travel costs).
 */

export function normalizeCurrencyTiers(state) {
  const raw = state?.world_profile?.currency;
  if (typeof raw === 'string') {
    return raw.split(/[,→]/).map((s) => s.trim()).filter(Boolean).slice(0, 3);
  }
  if (Array.isArray(raw)) {
    return raw.map((c) => String(c).trim()).filter(Boolean).slice(0, 3);
  }
  return inferCurrencyTiersFromHoldings(state);
}

function inferCurrencyTiersFromHoldings(state) {
  const generic = new Set([
    'gold', 'silver', 'bronze', 'copper', 'coin', 'coins', 'credits', 'scrip',
    'guilders', 'sovereigns', 'certificates', 'bills', 'data chips', 'reactor cores',
  ]);
  const gearWords = ['sword', 'armor', 'potion', 'dagger', 'mail', 'boot', 'cloak', 'ring', 'wand', 'scroll', 'compass'];
  const found = [];
  const seen = new Set();
  (state?.inventory?.shared || []).forEach((item) => {
    if (!item || typeof item !== 'object') return;
    const name = String(item.name || '').trim();
    const key = name.toLowerCase();
    if (!key || seen.has(key)) return;
    const hay = key;
    if (gearWords.some((w) => hay.includes(w))) return;
    if (generic.has(key)) {
      found.push(name);
      seen.add(key);
    }
  });
  return found.slice(0, 3);
}

export function currencyTierNames(state) {
  return normalizeCurrencyTiers(state);
}

function tierNameSet(tiers) {
  return new Set(tiers.map((t) => String(t).trim().toLowerCase()).filter(Boolean));
}

export function isCurrencyItemName(name, tiers) {
  const key = String(name || '').trim().toLowerCase();
  return Boolean(key && tierNameSet(tiers).has(key));
}

/** Wallet rows ordered low → high tier, qty 0 when not held. */
export function walletFromState(state) {
  const tiers = currencyTierNames(state);
  if (!tiers.length) return [];

  const qtyByKey = {};
  (state?.inventory?.shared || []).forEach((item) => {
    if (!item || typeof item !== 'object') return;
    const name = String(item.name || '').trim();
    const key = name.toLowerCase();
    if (!tierNameSet(tiers).has(key)) return;
    const canonical = tiers.find((t) => t.toLowerCase() === key) || name;
    qtyByKey[key] = {
      name: canonical,
      qty: Math.max(0, Number(item.qty) || 0),
    };
  });

  return tiers.map((tier) => {
    const key = tier.toLowerCase();
    return qtyByKey[key] || { name: tier, qty: 0 };
  });
}

/** Shared inventory rows excluding currency tiers (shown in wallet block). */
export function backpackGearFromState(state) {
  const tiers = currencyTierNames(state);
  const tierKeys = tierNameSet(tiers);
  return (state?.inventory?.shared || []).filter((item) => {
    if (!item || typeof item !== 'object') return false;
    const key = String(item.name || '').trim().toLowerCase();
    return key && !tierKeys.has(key);
  });
}

export function formatWalletText(wallet) {
  if (!wallet.length) return '';
  return wallet.map(({ name, qty }) => `${qty} ${name}`).join(' · ');
}

/** Compact wallet for HUD — non-zero tiers only. */
export function formatWalletCompact(wallet) {
  const held = wallet.filter(({ qty }) => Number(qty) > 0);
  if (!held.length) return '';
  return formatWalletText(held);
}

export function renderWalletHtml(wallet, escapeHtml) {
  if (!wallet.length) {
    return '<p class="fugassa-muted">No currency tiers configured.</p>';
  }
  const rows = wallet
    .map(
      ({ name, qty }) => `
        <div class="fugassa-wallet-row">
          <span class="fugassa-wallet-name">${escapeHtml(name)}</span>
          <span class="fugassa-wallet-qty">${escapeHtml(String(qty))}</span>
        </div>`,
    )
    .join('');
  return `<div class="fugassa-wallet">${rows}</div>`;
}

/** Human-readable labels for inventory item / slot color coding. */
export const ITEM_KIND_LABELS = {
  head: 'Head',
  body: 'Armor',
  clothes: 'Clothes',
  feet: 'Boots',
  hands: 'Hands',
  backpack: 'Backpack',
  belt: 'Belt',
  weapon: 'Weapon',
  utility: 'Utility',
  exploration: 'Exploration',
  consumable: 'Consumable',
  tech: 'Tech',
  quest: 'Quest',
  lore: 'Lore',
  deed: 'Deed',
  misc: 'Misc',
};

const USAGE_KINDS = new Set([
  'utility',
  'exploration',
  'consumable',
  'tech',
  'quest',
  'lore',
  'deed',
  'misc',
]);

function normalizeUsageKind(raw) {
  const key = String(raw || '').trim().toLowerCase();
  if (!key) return null;
  if (USAGE_KINDS.has(key)) return key;
  if (key.includes('consum')) return 'consumable';
  if (key.includes('explor') || key.includes('travel')) return 'exploration';
  if (key.includes('util') || key.includes('tool')) return 'utility';
  if (key.includes('tech') || key.includes('gadget')) return 'tech';
  if (key.includes('quest') || key.includes('key')) return 'quest';
  if (key.includes('lore') || key.includes('book') || key.includes('doc')) return 'lore';
  if (key.includes('deed') || key.includes('title')) return 'deed';
  return null;
}

function inferNonEquipKind(item) {
  const haystack = [item?.name, item?.description, item?.usage, ...(item?.tags || [])]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();

  if (/\b(potions?|elixirs?|vials?|rations?|food|drink|consumables?|antidotes?|salves?)\b/.test(haystack)) {
    return 'consumable';
  }
  if (/\b(quest|artifacts?|relics?|key items?)\b/.test(haystack)) {
    return 'quest';
  }
  if (/\b(ledgers?|tomes?|books?|scrolls?|journals?|maps?|codex|manuals?|records?)\b/.test(haystack)) {
    return 'lore';
  }
  if (/\b(compass|tools?|kits?|gadgets?|devices?|multi-tools?|lockpicks?|wire-cutters?|pry-bars?|pouches?)\b/.test(haystack)) {
    return 'utility';
  }
  if (/\b(explor|travel|survival|camp|torch|rope|grappling)\b/.test(haystack)) {
    return 'exploration';
  }
  if (/\b(circuit|chip|reactor|cyber|tech|scanner|module)\b/.test(haystack)) {
    return 'tech';
  }
  return 'misc';
}

/** Resolve property code from deed / title inventory items. */
export function resolveDeedPropertyCode(item) {
  if (!item || typeof item !== 'object') return null;
  const meta = item.metadata && typeof item.metadata === 'object' ? item.metadata : {};
  const direct = String(item.property_code || meta.property_code || '').trim();
  if (direct) return direct;
  const itemType = String(item.item_type || item.usage || '').trim().toLowerCase();
  if (itemType !== 'deed') return null;
  const hay = [item.name, item.description].filter(Boolean).join(' ').toLowerCase();
  if (hay.includes('driscoll')) return 'house_driscoll_city';
  return null;
}

export function isDeedItem(item) {
  return Boolean(resolveDeedPropertyCode(item));
}

/**
 * Display kind for coloring + badge. Equippable gear uses slot category;
 * backpack items use usage/tags/heuristics (utility, consumable, …).
 */
export function resolveItemDisplayKind(item, classifyItemCategory) {
  const equipCategory = classifyItemCategory?.(item) || null;
  if (equipCategory) {
    return {
      kind: equipCategory,
      label: ITEM_KIND_LABELS[equipCategory] || equipCategory,
      equippable: true,
    };
  }

  for (const source of [item?.usage, item?.item_type, ...(item?.tags || [])]) {
    const usageKind = normalizeUsageKind(source);
    if (usageKind) {
      return {
        kind: usageKind,
        label: ITEM_KIND_LABELS[usageKind] || usageKind,
        equippable: false,
      };
    }
  }

  const kind = inferNonEquipKind(item);
  return {
    kind,
    label: ITEM_KIND_LABELS[kind] || kind,
    equippable: false,
  };
}

export function itemKindClass(kind) {
  const safe = String(kind || 'misc').trim().toLowerCase().replace(/[^a-z0-9_-]/g, '') || 'misc';
  return `fugassa-inv-item--kind-${safe}`;
}

export function kindBadgeModifier(kind) {
  const safe = String(kind || 'misc').trim().toLowerCase().replace(/[^a-z0-9_-]/g, '') || 'misc';
  return `fugassa-inv-item-kind--${safe}`;
}

export function slotCategoryClass(slot, slotCategoryFn) {
  const cat = slotCategoryFn?.(slot) || 'misc';
  const safe = String(cat).trim().toLowerCase().replace(/[^a-z0-9_-]/g, '') || 'misc';
  return `fugassa-slot--cat-${safe}`;
}
