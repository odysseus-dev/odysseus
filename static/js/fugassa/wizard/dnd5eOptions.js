export const GENDER_CHOICES = ['Man', 'Woman', 'Custom'];

export const RACE_CHOICES = [
  'Aarakocra', 'Aasimar', 'Bugbear', 'Dragonborn', 'Dwarf', 'Elf', 'Firbolg',
  'Genasi', 'Gnome', 'Goblin', 'Goliath', 'Halfling', 'Half-Elf', 'Half-Orc',
  'Harengon', 'Hobgoblin', 'Human', 'Kenku', 'Kobold', 'Lizardfolk', 'Orc',
  'Tabaxi', 'Tiefling', 'Tortle', 'Triton', 'Yuan-ti Pureblood',
  'Changeling', 'Kalashtar', 'Shifter', 'Warforged',
  'Custom',
];

export const CLASS_CHOICES = [
  'Artificer', 'Barbarian', 'Bard', 'Cleric', 'Druid', 'Fighter',
  'Monk', 'Paladin', 'Ranger', 'Rogue', 'Sorcerer', 'Warlock', 'Wizard',
  'Custom',
];

const SUBCLASS_BY_CLASS = {
  Artificer: ['Alchemist', 'Armorer', 'Artillerist', 'Battle Smith'],
  Barbarian: [
    'Path of the Berserker', 'Path of the Totem Warrior', 'Path of the Ancestral Guardian',
    'Path of the Storm Herald', 'Path of the Zealot', 'Path of Wild Magic', 'Path of the Beast',
    'Path of the Battlerager', 'Path of the Giant',
  ],
  Bard: [
    'College of Lore', 'College of Valor', 'College of Glamour', 'College of Swords',
    'College of Whispers', 'College of Eloquence', 'College of Spirits',
  ],
  Cleric: [
    'Knowledge Domain', 'Life Domain', 'Light Domain', 'Nature Domain', 'Tempest Domain',
    'Trickery Domain', 'War Domain', 'Death Domain', 'Arcana Domain', 'Forge Domain',
    'Grave Domain', 'Order Domain', 'Peace Domain', 'Twilight Domain',
  ],
  Druid: [
    'Circle of the Land', 'Circle of the Moon', 'Circle of Dreams', 'Circle of the Shepherd',
    'Circle of Spores', 'Circle of Stars', 'Circle of Wildfire',
  ],
  Fighter: [
    'Champion', 'Battle Master', 'Eldritch Knight', 'Purple Dragon Knight (Banneret)',
    'Arcane Archer', 'Cavalier', 'Samurai', 'Psi Warrior', 'Rune Knight', 'Echo Knight',
  ],
  Monk: [
    'Way of the Open Hand', 'Way of Shadow', 'Way of the Four Elements', 'Way of the Drunken Master',
    'Way of the Kensei', 'Way of the Sun Soul', 'Way of the Astral Self', 'Way of Mercy',
  ],
  Paladin: [
    'Oath of Devotion', 'Oath of the Ancients', 'Oath of Vengeance', 'Oath of Conquest',
    'Oath of Redemption', 'Oath of Glory', 'Oath of the Watchers', 'Oathbreaker',
  ],
  Ranger: [
    'Hunter', 'Beast Master', 'Gloom Stalker', 'Horizon Walker', 'Monster Slayer',
    'Fey Wanderer', 'Swarmkeeper', 'Drakewarden',
  ],
  Rogue: [
    'Thief', 'Assassin', 'Arcane Trickster', 'Inquisitive', 'Mastermind', 'Scout',
    'Swashbuckler', 'Phantom', 'Soulknife',
  ],
  Sorcerer: [
    'Draconic Bloodline', 'Wild Magic', 'Divine Soul', 'Shadow Magic', 'Storm Sorcery',
    'Aberrant Mind', 'Clockwork Soul',
  ],
  Warlock: [
    'The Fiend', 'The Great Old One', 'The Archfey', 'The Celestial', 'The Hexblade',
    'The Fathomless', 'The Genie', 'The Undead', 'The Undying',
  ],
  Wizard: [
    'School of Abjuration', 'School of Conjuration', 'School of Divination', 'School of Enchantment',
    'School of Evocation', 'School of Illusion', 'School of Necromancy', 'School of Transmutation',
    'Bladesinging', 'War Magic', 'Chronurgy Magic', 'Graviturgy Magic',
  ],
};

export function genderChoices() {
  return GENDER_CHOICES.slice();
}

export function raceChoices() {
  return RACE_CHOICES.slice();
}

export function classChoices() {
  return CLASS_CHOICES.slice();
}

export function defaultRaceIndex() {
  return RACE_CHOICES.indexOf('Human');
}

export function defaultClassIndex() {
  return CLASS_CHOICES.indexOf('Fighter');
}

export function subclassChoicesForClass(dndClass) {
  return (SUBCLASS_BY_CLASS[dndClass] || []).slice();
}
