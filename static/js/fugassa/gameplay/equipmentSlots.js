/**
 * Client-side mirror of titan/fugassa/equipment_slots.py — used only for
 * instant drag-and-drop feedback (valid/invalid drop highlighting). The
 * server (`POST .../game/equip`) re-validates authoritatively; this must
 * never be the only gate against putting a potion in the armor slot.
 */

export const SLOTS = [
  'head', 'body', 'clothes', 'feet', 'hands', 'backpack', 'weapon_main', 'weapon_off', 'belt',
];

export const SLOT_LABELS = {
  head: 'Head',
  body: 'Body armor',
  clothes: 'Clothes',
  feet: 'Feet',
  hands: 'Hands',
  backpack: 'Backpack',
  weapon_main: 'Weapon (main hand)',
  weapon_off: 'Weapon (off hand)',
  belt: 'Belt',
};

const SLOT_CATEGORY = {
  head: 'head',
  body: 'body',
  clothes: 'clothes',
  feet: 'feet',
  hands: 'hands',
  backpack: 'backpack',
  belt: 'belt',
  weapon_main: 'weapon',
  weapon_off: 'weapon',
};

const KEYWORDS = {
  head: ['helmet', 'helm', 'hood', 'cap', 'hat', 'mask', 'circlet', 'crown', 'headgear'],
  body: ['armor', 'armour', 'vest', 'mail', 'plate', 'cuirass', 'chestplate', 'breastplate', 'jack'],
  clothes: ['shirt', 'tunic', 'robe', 'cloak', 'coat', 'dress', 'cape', 'garment', 'jacket'],
  feet: ['boots', 'boot', 'shoes', 'sandals', 'greaves', 'footwear'],
  hands: ['gloves', 'glove', 'gauntlets', 'gauntlet', 'bracers', 'mitts', 'handwear'],
  backpack: ['backpack', 'satchel', 'rucksack', 'pack'],
  belt: ['belt', 'sash', 'girdle', 'bandolier'],
  weapon: [
    'sword', 'blade', 'axe', 'mace', 'bow', 'gun', 'pistol', 'rifle', 'dagger',
    'staff', 'wand', 'spear', 'hammer', 'knife', 'rapier', 'crossbow', 'cannon', 'club',
  ],
};

export function classifyItemCategory(item) {
  if (!item || typeof item !== 'object') return null;
  const explicit = String(item.slot || '').toLowerCase();
  if (SLOT_CATEGORY[explicit]) return SLOT_CATEGORY[explicit];
  if (KEYWORDS[explicit]) return explicit;
  if (item.weapon_type || item.damage || item.attack_bonus) return 'weapon';
  if (item.armor_type || item.ac || item.defense) return 'body';
  const haystack = [item.name, item.description, item.usage, ...(item.tags || [])]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  for (const [category, words] of Object.entries(KEYWORDS)) {
    if (words.some((w) => haystack.includes(w))) return category;
  }
  return null;
}

export function slotAccepts(slot, item) {
  const category = SLOT_CATEGORY[slot];
  if (!category) return false;
  return classifyItemCategory(item) === category;
}

export function slotCategory(slot) {
  return SLOT_CATEGORY[slot] || null;
}

export function slotsForItem(item) {
  const category = classifyItemCategory(item);
  if (!category) return [];
  return SLOTS.filter((slot) => SLOT_CATEGORY[slot] === category);
}
