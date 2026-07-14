import * as api from '../fugassaApi.js';
import {
  classChoices,
  defaultClassIndex,
  defaultRaceIndex,
  genderChoices,
  raceChoices,
  subclassChoicesForClass,
} from './dnd5eOptions.js';
import {
  POINT_BUY_BUDGET,
  abilityMod,
  buildWizardSheetSnapshot,
  effectiveGender,
  formatModifier,
  labelize,
  playstyleFramework,
  pointBuyCost,
  pointBuySpent,
  wizardCharacterSummaryLines,
} from './helpers.js';

const ABILITY_KEYS = ['str', 'dex', 'con', 'int', 'wis', 'cha'];
const COMPUTE_DEBOUNCE_MS = 320;
const SRD_CANTRIP_FALLBACK_BY_CLASS = {
  artificer: 'wizard',
};

const SKILLS = [
  ['acrobatics', 'Acrobatics'],
  ['animal-handling', 'Animal Handling'],
  ['arcana', 'Arcana'],
  ['athletics', 'Athletics'],
  ['deception', 'Deception'],
  ['history', 'History'],
  ['insight', 'Insight'],
  ['intimidation', 'Intimidation'],
  ['investigation', 'Investigation'],
  ['medicine', 'Medicine'],
  ['nature', 'Nature'],
  ['perception', 'Perception'],
  ['performance', 'Performance'],
  ['persuasion', 'Persuasion'],
  ['religion', 'Religion'],
  ['sleight-of-hand', 'Sleight of Hand'],
  ['stealth', 'Stealth'],
  ['survival', 'Survival'],
];

export function createDnd5eCharacterBuilder({ draft, onChange }) {
  ensureDraftShape(draft);

  let currentSheet = null;
  let srdRaces = [];
  let srdSpells = [];
  let computeTimer = null;
  let computeSeq = 0;
  let homebrewBusy = false;

  const root = document.createElement('div');
  root.className = 'fugassa-character-builder';

  const validationBox = document.createElement('div');
  validationBox.className = 'fugassa-builder-validation fugassa-muted';
  validationBox.hidden = true;

  const builderMain = document.createElement('div');
  builderMain.className = 'fugassa-character-builder-main';

  const form = document.createElement('div');
  form.className = 'fugassa-character-builder-form';

  const summary = document.createElement('pre');
  summary.className = 'fugassa-character-sheet';

  builderMain.append(form, summary);
  root.append(validationBox, builderMain);

  const playerName = textField('Player name', draft.player_name || 'Hero');
  const level = numberField('Level', draft.level || 1, 1, 20);
  const gender = selectField('Gender', genderChoices(), draft.player_gender_idx || 0);
  const genderCustom = textField('Custom gender', draft.player_gender_custom || '');
  const race = selectField('Race', raceChoices(), draft.player_race_idx ?? defaultRaceIndex());
  const raceCustom = textField('Custom race', draft.player_race_custom || '');
  const subrace = selectField('Subrace', [], draft.player_subrace_idx || 0);
  const subraceCustom = textField('Custom subrace', draft.player_subrace_custom || '');
  const klass = selectField('Class', classChoices(), draft.player_class_idx ?? defaultClassIndex());
  const classCustom = textField('Custom class', draft.player_class_custom || '');
  const subclass = selectField('Subclass', [], draft.player_subclass_idx || 0);
  const subclassCustom = textField('Custom subclass', draft.player_subclass_custom || '');
  const age = textField('Age', draft.player_age || '');
  const template = selectField(
    'Mechanical template',
    ['Auto', ...classChoices().map((name) => `Template ${name}`)],
    draft.homebrew_llm_template_pick || 0,
  );
  const method = selectField('Ability method', ['Point buy', 'Roll once'], draft.abilities_method === 'roll' ? 1 : 0);
  const methodStatus = document.createElement('div');
  methodStatus.className = 'fugassa-muted';
  const rollBtn = button('Roll stats');
  const resetBtn = button('Reset point buy');

  const identityGrid = document.createElement('div');
  identityGrid.className = 'fugassa-builder-identity-grid';
  identityGrid.append(
    fieldWrap(playerName),
    fieldWrap(level),
    fieldWrap(gender),
    fieldWrap(genderCustom),
    fieldWrap(race),
    fieldWrap(raceCustom),
    fieldWrap(subrace),
    fieldWrap(subraceCustom),
    fieldWrap(klass),
    fieldWrap(classCustom),
    fieldWrap(subclass),
    fieldWrap(subclassCustom),
    fieldWrap(age),
    fieldWrap(template),
    fieldWrap(method),
  );
  form.appendChild(identityGrid);

  const abilitySection = document.createElement('section');
  abilitySection.className = 'fugassa-builder-section';
  abilitySection.innerHTML = '<h4>Abilities</h4>';
  const abilityControls = {};
  const abilityHint = document.createElement('div');
  abilityHint.className = 'fugassa-muted';
  abilitySection.append(methodStatus, row([rollBtn, resetBtn]), abilityHint);
  ABILITY_KEYS.forEach((key) => {
    const input = numberField(key.toUpperCase(), draft.abilities[key] ?? 10, 3, 18);
    const meta = document.createElement('span');
    meta.className = 'fugassa-builder-meta';
    abilityControls[key] = { input, meta };
    const line = document.createElement('div');
    line.className = 'fugassa-builder-ability-row';
    line.appendChild(labelEl(key.toUpperCase()));
    line.appendChild(input.el);
    line.appendChild(meta);
    abilitySection.appendChild(line);
  });

  const skillsSection = document.createElement('section');
  skillsSection.className = 'fugassa-builder-section';
  skillsSection.innerHTML = '<h4>Skills</h4>';
  const skillHint = document.createElement('div');
  skillHint.className = 'fugassa-muted';
  const expertiseHint = document.createElement('div');
  expertiseHint.className = 'fugassa-muted';
  const skillsGrid = document.createElement('div');
  skillsGrid.className = 'fugassa-skills-grid';
  const skillChecks = {};
  const expertiseChecks = {};
  const expertiseGrid = document.createElement('div');
  expertiseGrid.className = 'fugassa-skills-grid';
  expertiseGrid.hidden = true;
  skillsSection.append(skillHint, skillsGrid, expertiseHint, expertiseGrid);

  const choicesSection = document.createElement('section');
  choicesSection.className = 'fugassa-builder-section fugassa-required-choices-section';
  choicesSection.id = 'fugassa-required-choices';
  choicesSection.innerHTML = '<h4>Required choices</h4>';
  const homebrewChoicesHint = document.createElement('div');
  homebrewChoicesHint.className = 'fugassa-muted';
  const homebrewChoicesContainer = document.createElement('div');
  homebrewChoicesContainer.className = 'fugassa-homebrew-choices';
  choicesSection.append(homebrewChoicesHint, homebrewChoicesContainer);
  choicesSection.hidden = true;

  const asiSection = document.createElement('section');
  asiSection.className = 'fugassa-builder-section';
  asiSection.innerHTML = '<h4>ASI / Feats</h4>';
  const asiHint = document.createElement('div');
  asiHint.className = 'fugassa-muted';
  const asiContainer = document.createElement('div');
  asiContainer.className = 'fugassa-builder-asi';
  asiSection.append(asiHint, asiContainer);

  const spellsSection = document.createElement('section');
  spellsSection.className = 'fugassa-builder-section';
  spellsSection.innerHTML = '<h4>Spells</h4>';
  const spellHint = document.createElement('div');
  spellHint.className = 'fugassa-muted';
  const cantripGrid = document.createElement('div');
  cantripGrid.className = 'fugassa-spell-grid';
  const spellLevelContainer = document.createElement('div');
  spellLevelContainer.className = 'fugassa-spell-levels';
  spellsSection.append(spellHint, cantripGrid, spellLevelContainer);
  spellsSection.hidden = true;

  const mechanicsSection = document.createElement('section');
  mechanicsSection.className = 'fugassa-builder-section';
  mechanicsSection.innerHTML = '<h4>Class mechanics</h4>';
  const mechanicsHint = document.createElement('div');
  mechanicsHint.className = 'fugassa-muted';
  const mechanicsContainer = document.createElement('div');
  mechanicsContainer.className = 'fugassa-class-mechanics';
  mechanicsSection.append(mechanicsHint, mechanicsContainer);
  mechanicsSection.hidden = true;

  const homebrewSection = document.createElement('section');
  homebrewSection.className = 'fugassa-builder-section';
  homebrewSection.innerHTML = '<h4>Homebrew mechanics (LLM)</h4>';
  const homebrewInfo = document.createElement('div');
  homebrewInfo.className = 'fugassa-muted';
  homebrewInfo.textContent = 'For custom race/class outside the bundled SRD, generate structured mechanics before Create.';
  const homebrewStatus = document.createElement('div');
  homebrewStatus.className = 'fugassa-muted';
  const homebrewPreview = document.createElement('div');
  homebrewPreview.className = 'fugassa-homebrew-preview';
  const homebrewGenerate = button('Generate mechanics');
  const homebrewClear = button('Clear homebrew');
  homebrewSection.append(homebrewInfo, homebrewStatus, homebrewPreview, row([homebrewGenerate, homebrewClear]));
  homebrewSection.hidden = true;

  const featuresSection = document.createElement('section');
  featuresSection.className = 'fugassa-builder-section';
  featuresSection.innerHTML = '<h4>Features & Traits (reference)</h4>';
  const featuresBody = document.createElement('div');
  featuresBody.className = 'fugassa-feature-cards';
  const featuresRefHint = document.createElement('div');
  featuresRefHint.className = 'fugassa-muted';
  featuresRefHint.textContent = 'Read-only summary of class features and racial traits. Active picks live in Required choices above.';
  featuresSection.append(featuresRefHint, featuresBody);
  featuresSection.hidden = true;

  form.append(abilitySection, skillsSection, choicesSection, asiSection, spellsSection, mechanicsSection, homebrewSection, featuresSection);

  function syncDraftFromFields() {
    draft.player_name = playerName.input.value.trim();
    draft.level = clamp(Number(level.input.value || 1), 1, 20);
    draft.player_gender_idx = gender.select.selectedIndex;
    draft.player_gender_custom = genderCustom.input.value.trim();
    draft.player_race_idx = race.select.selectedIndex;
    draft.player_race_custom = raceCustom.input.value.trim();
    draft.player_subrace_idx = subrace.select.selectedIndex;
    draft.player_subrace_custom = subraceCustom.input.value.trim();
    draft.player_class_idx = klass.select.selectedIndex;
    draft.player_class_custom = classCustom.input.value.trim();
    draft.player_subclass_idx = subclass.select.selectedIndex;
    draft.player_subclass_custom = subclassCustom.input.value.trim();
    draft.player_age = age.input.value.trim();
    draft.homebrew_llm_template_pick = template.select.selectedIndex;
    draft.abilities_method = method.select.selectedIndex === 1 ? 'roll' : 'point_buy';
    ABILITY_KEYS.forEach((key) => {
      draft.abilities[key] = Number(abilityControls[key].input.input.value || 10);
    });
    readSkillSelectionsFromUi();
    readExpertiseFromUi();
    draft.playstyle_framework = playstyleFramework(draft.playstyle);
    if (template.select.selectedIndex > 0) {
      const tmplClass = classChoices()[template.select.selectedIndex - 1];
      draft.spell_list_class_id = tmplClass ? raceSlug(tmplClass) : '';
    } else {
      draft.spell_list_class_id = '';
    }
  }

  function raceSlug(label) {
    return String(label || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  }

  function syncSubraceOptions() {
    const raceName = raceChoices()[race.select.selectedIndex] || '';
    const customRace = raceName === 'Custom';
    subrace.el.style.display = customRace ? 'none' : '';
    subraceCustom.el.style.display = customRace ? '' : 'none';
    if (customRace) {
      draft._subrace_options = [];
      return;
    }
    const slug = raceSlug(raceName);
    const row = srdRaces.find((item) => item.index === slug);
    const names = (row?.subraces_detail || []).map((sr) => sr.name).filter(Boolean);
    draft._subrace_options = names;
    replaceOptions(subrace.select, names.length ? names : ['—']);
    if (!names.length) {
      subrace.el.style.display = 'none';
    } else {
      subrace.select.selectedIndex = Math.min(draft.player_subrace_idx || 0, names.length - 1);
    }
  }

  function syncVisibility() {
    genderCustom.el.style.display = gender.select.selectedIndex === gender.select.options.length - 1 ? '' : 'none';
    raceCustom.el.style.display = race.select.selectedIndex === race.select.options.length - 1 ? '' : 'none';
    classCustom.el.style.display = klass.select.selectedIndex === klass.select.options.length - 1 ? '' : 'none';
    syncSubraceOptions();

    const className = classChoices()[klass.select.selectedIndex] || '';
    const showSubclass = Number(level.input.value || 1) >= 3;
    const customClass = className === 'Custom';
    const subclasses = customClass ? [] : subclassChoicesForClass(className);
    replaceOptions(subclass.select, subclasses);
    subclass.select.selectedIndex = Math.min(draft.player_subclass_idx || 0, Math.max(0, subclasses.length - 1));
    subclass.el.style.display = showSubclass && !customClass ? '' : 'none';
    subclassCustom.el.style.display = showSubclass && customClass ? '' : 'none';
  }

  function skillCap() {
    const fromSheet = Number(currentSheet?.skill_proficiency_cap);
    if (fromSheet > 0) return fromSheet;
    const hb = draft.homebrew_details || {};
    const raw = hb.skill_proficiency_choose ?? hb.optional_skill_proficiency_choose;
    if (raw != null && Number(raw) > 0) return Number(raw);
    return 2;
  }

  function skillIdFromOptionName(name) {
    const target = String(name || '').toLowerCase().trim();
    const row = SKILLS.find(([id, label]) => (
      label.toLowerCase() === target
      || id === target
      || id.replace(/-/g, ' ') === target
    ));
    return row?.[0] || null;
  }

  function allowedSkillIds() {
    const fromHint = currentSheet?.skill_proficiency_choice_hint?.option_skill_ids;
    if (Array.isArray(fromHint) && fromHint.length) {
      return new Set(fromHint);
    }
    const opts = draft.homebrew_details?.skill_proficiency_options;
    if (Array.isArray(opts) && opts.length) {
      const ids = opts.map((name) => skillIdFromOptionName(name)).filter(Boolean);
      return ids.length ? new Set(ids) : null;
    }
    return null;
  }

  function readSkillSelectionsFromUi() {
    const allowed = allowedSkillIds();
    const next = {};
    Object.entries(skillChecks).forEach(([id, input]) => {
      if (input.checked && !input.disabled && (!allowed || allowed.has(id))) {
        next[id] = true;
      }
    });
    draft.skill_proficiencies = next;
  }

  function readExpertiseFromUi() {
    if (!Object.keys(expertiseChecks).length) return;
    const next = {};
    Object.entries(expertiseChecks).forEach(([id, input]) => {
      if (input.checked) next[id] = true;
    });
    draft.expertise = next;
    draft.class_mechanic_choices ||= {};
    draft.class_mechanic_choices.expertise = Object.keys(next);
  }

  function buildSkillsGridOnce() {
    if (skillsGrid.dataset.built === '1') return;
    skillsGrid.dataset.built = '1';
    const allowedIds = allowedSkillIds();
    SKILLS.forEach(([id, label]) => {
      const selectable = !allowedIds || allowedIds.has(id);
      const wrap = document.createElement('label');
      wrap.className = `fugassa-skill-check${selectable ? '' : ' is-disabled'}`;
      wrap.dataset.skillId = id;
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.disabled = !selectable;
      input.checked = selectable && Boolean(draft.skill_proficiencies[id]);
      const span = document.createElement('span');
      span.textContent = label;
      wrap.append(input, span);
      skillsGrid.appendChild(wrap);
      skillChecks[id] = input;
      input.addEventListener('change', () => {
        const allowed = allowedSkillIds();
        const canSelect = !allowed || allowed.has(id);
        if (!canSelect || input.disabled) {
          input.checked = false;
          return;
        }
        readSkillSelectionsFromUi();
        enforceSkillCap(id);
        scheduleCompute();
      });
    });
  }

  function syncSkillCheckboxesFromDraft() {
    buildSkillsGridOnce();
    const allowedIds = allowedSkillIds();
    SKILLS.forEach(([id]) => {
      const input = skillChecks[id];
      if (!input) return;
      const selectable = !allowedIds || allowedIds.has(id);
      const wrap = input.closest('.fugassa-skill-check');
      input.disabled = !selectable;
      wrap?.classList.toggle('is-disabled', !selectable);
      if (!selectable) {
        input.checked = false;
      } else {
        input.checked = Boolean(draft.skill_proficiencies[id]);
      }
    });
    enforceSkillCap();
    syncExpertiseGridFromDraft();
  }

  function syncExpertiseGridFromDraft() {
    const classId = currentSheet?.resolved?.class_id || '';
    const lvl = Number(draft.level || 1);
    const hasMechanicExpertise = (currentSheet?.class_mechanic_pickers || []).some((p) => p.id === 'expertise');
    const show = !hasMechanicExpertise && ((classId === 'bard' && lvl >= 3) || (classId === 'rogue' && lvl >= 1));
    expertiseGrid.hidden = !show;
    expertiseHint.hidden = !show;
    if (!show) {
      expertiseGrid.innerHTML = '';
      Object.keys(expertiseChecks).forEach((id) => delete expertiseChecks[id]);
      draft.expertise = {};
      return;
    }
    expertiseHint.textContent = classId === 'bard'
      ? 'Expertise: pick two proficient skills (Bard).'
      : 'Expertise: pick two proficient skills (Rogue).';
    const proficient = SKILLS.map(([id]) => id).filter((id) => draft.skill_proficiencies?.[id]);
    expertiseGrid.innerHTML = '';
    Object.keys(expertiseChecks).forEach((id) => delete expertiseChecks[id]);
    proficient.forEach((id) => {
      const label = SKILLS.find(([sid]) => sid === id)?.[1] || id;
      const wrap = document.createElement('label');
      wrap.className = 'fugassa-skill-check';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = Boolean(draft.expertise?.[id]);
      const span = document.createElement('span');
      span.textContent = label;
      wrap.append(input, span);
      expertiseGrid.appendChild(wrap);
      expertiseChecks[id] = input;
      input.addEventListener('change', () => {
        readExpertiseFromUi();
        scheduleCompute();
      });
    });
  }

  function enforceSkillCap(changedId) {
    const cap = skillCap();
    const allowed = allowedSkillIds();
    const picked = Object.entries(skillChecks).filter(
      ([id, input]) => input.checked && !input.disabled && (!allowed || allowed.has(id)),
    ).length;
    if (picked > cap && changedId && skillChecks[changedId] && !skillChecks[changedId].disabled) {
      skillChecks[changedId].checked = false;
    }
    const nextPicked = Object.entries(skillChecks).filter(
      ([id, input]) => input.checked && !input.disabled && (!allowed || allowed.has(id)),
    ).length;
    const parts = currentSheet?.skill_proficiency_cap_parts || {};
    let hint = `Proficient skills: ${nextPicked}/${cap} (class ${parts.class_choose ?? '?'} + race ${parts.race_choose ?? '?'}).`;
    if (allowed && allowed.size) {
      const opts = (currentSheet?.skill_proficiency_choice_hint?.options
        || draft.homebrew_details?.skill_proficiency_options
        || []).join(', ');
      if (opts) hint += ` Select ${cap} from: ${opts}.`;
    }
    skillHint.textContent = hint;
    if (expertiseGrid && !expertiseGrid.hidden) {
      syncExpertiseGridFromDraft();
    }
  }

  function showExpertiseIfNeeded() {
    syncExpertiseGridFromDraft();
  }

  function renderAsiPickers() {
    asiContainer.innerHTML = '';
    const levels = currentSheet?.asi_feat_levels_reached || [];
    if (!levels.length) {
      const lvl = Number(draft.level || 1);
      if (lvl < 4) {
        asiSection.hidden = false;
        asiHint.textContent = 'Optional D&D feats and ability-score improvements are chosen here from level 4 onward. At level 1 you do not pick feats in this block.';
      } else {
        asiSection.hidden = true;
        asiHint.textContent = '';
      }
      return;
    }
    asiSection.hidden = false;
    asiHint.textContent = 'Choose an ability increase or optional feat at each level shown.';
    draft.asi_choices ||= {};
    levels.forEach((lvl) => {
      const key = String(lvl);
      const entry = draft.asi_choices[key] || draft.asi_choices[lvl] || { kind: 'none' };
      const block = document.createElement('div');
      block.className = 'fugassa-builder-asi-row';
      const kind = selectField('Kind', ['—', '+2 one ability', '+1 two abilities', 'Feat'], asiKindIndex(entry.kind));
      const ability = selectField('Ability', ABILITY_KEYS.map((k) => k.toUpperCase()), ABILITY_KEYS.indexOf(String(entry.ability || 'str').toLowerCase()));
      const ability2 = selectField('Second ability', ABILITY_KEYS.map((k) => k.toUpperCase()), ABILITY_KEYS.indexOf(String((entry.abilities || [])[1] || 'dex').toLowerCase()));
      const feat = textField('Feat name', entry.feat || entry.feat_name || '');
      block.append(
        labelEl(`Level ${lvl}`),
        kind.el,
        ability.el,
        ability2.el,
        feat.el,
      );
      const syncAsi = () => {
        const k = asiKindValue(kind.select.selectedIndex);
        const row = { kind: k };
        if (k === 'plus2') row.ability = ABILITY_KEYS[ability.select.selectedIndex] || 'str';
        if (k === 'plus1plus1') {
          row.abilities = [
            ABILITY_KEYS[ability.select.selectedIndex] || 'str',
            ABILITY_KEYS[ability2.select.selectedIndex] || 'dex',
          ];
        }
        if (k === 'feat') row.feat = feat.input.value.trim();
        draft.asi_choices[key] = row;
        scheduleCompute();
      };
      [kind.select, ability.select, ability2.select, feat.input].forEach((el) => {
        el.addEventListener('change', syncAsi);
        el.addEventListener('input', syncAsi);
      });
      ability.el.style.display = kind.select.selectedIndex === 1 || kind.select.selectedIndex === 2 ? '' : 'none';
      ability2.el.style.display = kind.select.selectedIndex === 2 ? '' : 'none';
      feat.el.style.display = kind.select.selectedIndex === 3 ? '' : 'none';
      kind.select.addEventListener('change', () => {
        ability.el.style.display = kind.select.selectedIndex === 1 || kind.select.selectedIndex === 2 ? '' : 'none';
        ability2.el.style.display = kind.select.selectedIndex === 2 ? '' : 'none';
        feat.el.style.display = kind.select.selectedIndex === 3 ? '' : 'none';
      });
      asiContainer.appendChild(block);
    });
  }

  function spellListClassId() {
    const raw = currentSheet?.spell_list_class_id || currentSheet?.resolved?.class_id || '';
    return String(raw || '').toLowerCase();
  }

  function cantripsForTemplateClass(classId) {
    const target = String(classId || '').toLowerCase();
    if (!target) return [];
    return srdSpells.filter(
      (sp) => Number(sp.level ?? 0) === 0
        && (sp.classes || []).some((c) => String(c.index || '').toLowerCase() === target),
    );
  }

  function srdCantripFallbackPool() {
    const templateId = spellListClassId() || raceSlug(draft.spell_list_class_id || '');
    if (!templateId) return { pool: [], source: '' };
    const direct = cantripsForTemplateClass(templateId);
    if (direct.length) return { pool: direct, source: templateId };
    const fallback = SRD_CANTRIP_FALLBACK_BY_CLASS[templateId];
    if (fallback) {
      const fbPool = cantripsForTemplateClass(fallback);
      if (fbPool.length) return { pool: fbPool, source: fallback };
    }
    return { pool: [], source: templateId };
  }

  function spellsForClass(levelFilter) {
    const classId = spellListClassId();
    const catalog = currentSheet?.homebrew_spell_catalog || [];
    const cantCap = Number(currentSheet?.spellcasting?.cantrips_known || 0);

    const filterLevel = (pool) => pool.filter((sp) => {
      const lvl = Number(sp.level ?? 0);
      if (levelFilter >= 0 && lvl !== levelFilter) return false;
      return true;
    });

    if (catalog.length) {
      const fromCatalog = filterLevel(catalog);
      if (levelFilter === 0 && !fromCatalog.length && cantCap > 0) {
        const { pool, source } = srdCantripFallbackPool();
        return pool.map((sp) => ({
          ...sp,
          index: `hbspell:srdfallback:${sp.index}`,
          srd_fallback: true,
          cantrip_source_class: source,
        }));
      }
      if (levelFilter < 0) return fromCatalog;
      return fromCatalog;
    }

    return srdSpells.filter((sp) => {
      const lvl = Number(sp.level ?? 0);
      if (levelFilter >= 0 && lvl !== levelFilter) return false;
      return (sp.classes || []).some((c) => String(c.index || '').toLowerCase() === classId);
    });
  }

  function cantripAliases(id) {
    const raw = String(id || '').trim();
    const aliases = new Set([raw]);
    if (raw.startsWith('hbspell:srdfallback:')) {
      aliases.add(raw.slice('hbspell:srdfallback:'.length));
    }
    if (!raw.startsWith('hbspell:')) {
      aliases.add(`hbspell:srdfallback:${raw}`);
      aliases.add(`hbspell:${raw}`);
    }
    return aliases;
  }

  function cantripSelected(id) {
    const picked = new Set((draft.selected_cantrips || []).map((x) => String(x)));
    for (const alias of cantripAliases(id)) {
      if (picked.has(alias)) return true;
    }
    return false;
  }

  function leveledSelected(level, id) {
    const bucket = draft.selected_spells_by_level?.[String(level)] || draft.selected_spells_by_level?.[level] || [];
    if (Array.isArray(bucket)) return bucket.includes(id);
    return Boolean(bucket?.[id]);
  }

  function toggleCantrip(id) {
    const cantCap = Number(currentSheet?.spellcasting?.cantrips_known || 0);
    const list = [...(draft.selected_cantrips || [])];
    const aliases = cantripAliases(id);
    const idx = list.findIndex((entry) => cantripAliases(entry).has(id) || aliases.has(entry));
    if (idx >= 0) list.splice(idx, 1);
    else {
      if (cantCap > 0 && list.length >= cantCap) return;
      list.push(id);
    }
    draft.selected_cantrips = list;
    renderSpellPickers();
    onChange?.(draft);
    scheduleCompute();
  }

  function toggleLeveledSpell(spellLevel, id) {
    draft.selected_spells_by_level ||= {};
    const key = String(spellLevel);
    let bucket = draft.selected_spells_by_level[key];
    if (!Array.isArray(bucket)) bucket = bucket ? Object.keys(bucket).filter((k) => bucket[k]) : [];
    const idx = bucket.indexOf(id);
    if (idx >= 0) bucket.splice(idx, 1);
    else bucket.push(id);
    draft.selected_spells_by_level[key] = bucket;
    renderSpellPickers();
    onChange?.(draft);
    scheduleCompute();
  }

  function renderSpellPickers() {
    const sc = currentSheet?.spellcasting || {};
    if (!sc.has) {
      spellsSection.hidden = false;
      const tmpl = template.select.selectedIndex > 0
        ? classChoices()[template.select.selectedIndex - 1]
        : '';
      spellHint.textContent = [
        'This class has no spellcasting at the current level.',
        tmpl
          ? `Mechanical template is ${tmpl} — regenerate homebrew with spellcasting enabled to add a custom spell list, or pick a standard SRD class.`
          : 'To add spells: set Mechanical template to a caster (e.g. Wizard), click Generate mechanics, then pick cantrips/spells here.',
      ].join(' ');
      cantripGrid.innerHTML = '';
      spellLevelContainer.innerHTML = '';
      return;
    }
    spellsSection.hidden = false;
    const cantCap = Number(sc.cantrips_known || 0);
    const model = String(sc.model || 'prepared');
    const leveledCap = model === 'known'
      ? Number(sc.spells_known || 0)
      : Number(sc.spells_prepared_estimate || 0);
    const cantPicked = (draft.selected_cantrips || []).length;
    const leveledPicked = Object.values(draft.selected_spells_by_level || {}).reduce(
      (sum, bucket) => sum + (Array.isArray(bucket) ? bucket.length : Object.values(bucket || {}).filter(Boolean).length),
      0,
    );
    const cantripSource = currentSheet?.cantrip_pool_source || spellListClassId();
    const cantripSourceLabel = cantripSource ? labelize(cantripSource) : '';
    spellHint.textContent = [
      sc.caster_progression_note || '',
      currentSheet?.homebrew_cantrips_supplemented && cantripSourceLabel
        ? `Spell options include ${cantripSourceLabel} list (mechanical template) plus homebrew catalog.`
        : (cantripSourceLabel ? `Spell list: ${cantripSourceLabel}.` : ''),
      `Cantrips: ${cantPicked}/${cantCap}`,
      `${model === 'known' ? 'Spells known' : 'Spells prepared'}: ${leveledPicked}/${leveledCap}`,
      sc.spell_save_dc ? `Save DC ${sc.spell_save_dc} · attack ${formatModifier(sc.spell_attack_mod)}` : '',
    ].filter(Boolean).join(' · ');

    cantripGrid.innerHTML = '';
    const cantripPool = spellsForClass(0);
    cantripPool.slice(0, 80).forEach((sp) => {
      const id = sp.index;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'fugassa-btn fugassa-btn--ghost fugassa-btn--sm';
      if (cantripSelected(id)) btn.classList.add('is-active');
      btn.textContent = sp.name || id;
      btn.addEventListener('click', () => toggleCantrip(id));
      cantripGrid.appendChild(btn);
    });
    if (cantCap > 0 && !cantripPool.length) {
      const empty = document.createElement('p');
      empty.className = 'fugassa-muted';
      empty.textContent = 'No cantrips available — set Mechanical template and regenerate homebrew, or reload after server update.';
      cantripGrid.appendChild(empty);
    }

    spellLevelContainer.innerHTML = '';
    const maxLvl = Number(sc.max_castable_spell_level || 0);
    for (let lvl = 1; lvl <= maxLvl; lvl += 1) {
      const block = document.createElement('div');
      block.className = 'fugassa-spell-level-block';
      const title = document.createElement('strong');
      title.textContent = `Level ${lvl}`;
      const grid = document.createElement('div');
      grid.className = 'fugassa-spell-grid';
      spellsForClass(lvl).slice(0, 60).forEach((sp) => {
        const id = sp.index;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'fugassa-btn fugassa-btn--ghost fugassa-btn--sm';
        if (leveledSelected(lvl, id)) btn.classList.add('is-active');
        btn.textContent = sp.name || id;
        btn.addEventListener('click', () => toggleLeveledSpell(lvl, id));
        grid.appendChild(btn);
      });
      block.append(title, grid);
      spellLevelContainer.appendChild(block);
    }
  }

  function mechanicPicked(pickerId, optionId) {
    draft.class_mechanic_choices ||= {};
    const picker = (currentSheet?.class_mechanic_pickers || []).find((p) => p.id === pickerId);
    const cap = Number(picker?.cap || 0);
    const list = [...(draft.class_mechanic_choices[pickerId] || [])];
    const idx = list.indexOf(optionId);
    if (idx >= 0) list.splice(idx, 1);
    else if (cap <= 0 || list.length < cap) list.push(optionId);
    draft.class_mechanic_choices[pickerId] = list;
    if (pickerId === 'expertise') {
      draft.expertise = {};
      list.forEach((id) => { draft.expertise[id] = true; });
    }
    renderClassMechanicPickers();
    onChange?.(draft);
    scheduleCompute();
  }

  function renderClassMechanicPickers() {
    const pickers = currentSheet?.class_mechanic_pickers || [];
    mechanicsContainer.innerHTML = '';
    if (!pickers.length) {
      mechanicsSection.hidden = true;
      mechanicsHint.textContent = '';
      return;
    }
    mechanicsSection.hidden = false;
    draft.class_mechanic_choices ||= {};
    const summaryBits = (currentSheet?.class_resource_summary || []).slice(0, 4);
    mechanicsHint.textContent = [
      'Class-specific picks (infusions, fighting style, favored enemy, invocations, …).',
      summaryBits.length ? summaryBits.join(' · ') : '',
    ].filter(Boolean).join(' ');

    pickers.forEach((picker) => {
      const pid = String(picker.id || '');
      if (!pid) return;
      const block = document.createElement('div');
      block.className = 'fugassa-class-mechanic-block';
      const title = document.createElement('strong');
      const cap = Number(picker.cap || 0);
      const picked = (draft.class_mechanic_choices[pid] || []);
      title.textContent = `${picker.label || pid} (${picked.length}/${cap})`;
      block.appendChild(title);
      if (picker.hint) {
        const hint = document.createElement('p');
        hint.className = 'fugassa-muted';
        hint.textContent = picker.hint;
        block.appendChild(hint);
      }
      const ptype = String(picker.type || 'enum');
      if (ptype === 'enum') {
        const select = document.createElement('select');
        select.className = 'fugassa-input';
        const blank = document.createElement('option');
        blank.value = '';
        blank.textContent = '— pick one —';
        select.appendChild(blank);
        (picker.options || []).forEach((opt) => {
          const o = document.createElement('option');
          o.value = opt.id;
          o.textContent = opt.name || opt.id;
          select.appendChild(o);
        });
        const saved = (draft.class_mechanic_choices[pid] || [])[0] || '';
        select.value = saved;
        select.addEventListener('change', () => {
          draft.class_mechanic_choices[pid] = select.value ? [select.value] : [];
          renderClassMechanicPickers();
          onChange?.(draft);
          scheduleCompute();
        });
        block.appendChild(select);
      } else {
        const grid = document.createElement('div');
        grid.className = 'fugassa-spell-grid';
        (picker.options || []).forEach((opt) => {
          const id = opt.id;
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'fugassa-btn fugassa-btn--ghost fugassa-btn--sm';
          if (picked.includes(id)) btn.classList.add('is-active');
          btn.textContent = opt.name || id;
          btn.addEventListener('click', () => {
            if (!picked.includes(id) && cap > 0 && picked.length >= cap) return;
            mechanicPicked(pid, id);
          });
          grid.appendChild(btn);
        });
        block.appendChild(grid);
      }
      mechanicsContainer.appendChild(block);
    });
  }

  function renderFeatureCard(title, item) {
    const card = document.createElement('article');
    card.className = 'fugassa-feature-card';
    const name = item?.name || item?.index || 'Feature';
    const level = item?.level ? ` · L${item.level}` : '';
    const source = item?.source ? ` (${item.source})` : '';
    const desc = Array.isArray(item?.desc)
      ? item.desc.join('\n\n')
      : String(item?.desc || item?.description || '').trim();
    card.innerHTML = `
      <h5>${escapeHtml(String(name))}${escapeHtml(String(level))}${escapeHtml(String(source))}</h5>
      <p class="fugassa-muted">${escapeHtml(desc || 'No description provided.')}</p>
    `;
    return card;
  }

  function renderHomebrewPreview() {
    const hb = normalizeHomebrewDetails(draft.homebrew_details || {});
    const keys = Object.keys(hb).filter((k) => !String(k).startsWith('_'));
    if (!keys.length) {
      homebrewPreview.innerHTML = '';
      return;
    }
    const parts = [];
    if (hb.hit_die) parts.push(`<p><strong>Hit die:</strong> d${Number(hb.hit_die)}</p>`);
    if (Array.isArray(hb.saving_throw_profs) && hb.saving_throw_profs.length) {
      parts.push(`<p><strong>Saving throws:</strong> ${escapeHtml(hb.saving_throw_profs.join(', ').toUpperCase())}</p>`);
    }
    const skillOpts = hb.skill_proficiency_options;
    const skillChoose = hb.skill_proficiency_choose ?? hb.optional_skill_proficiency_choose;
    if (Array.isArray(skillOpts) && skillOpts.length) {
      parts.push(
        `<p><strong>Skill choices:</strong> pick ${Number(skillChoose || 0)} from ${escapeHtml(skillOpts.join(', '))}</p>`,
      );
    }
    if (Array.isArray(hb.class_features) && hb.class_features.length) {
      const rows = hb.class_features.map((f) => (
        `<li><strong>${escapeHtml(f?.name || 'Feature')}</strong>${f?.level ? ` (L${f.level})` : ''}</li>`
      )).join('');
      parts.push(`<div><strong>Class features</strong><ul class="fugassa-char-list">${rows}</ul></div>`);
    }
    const raceTraits = hb.racial_traits
      || hb.racial_traits_applied?.racial_traits;
    if (Array.isArray(raceTraits) && raceTraits.length) {
      const rows = raceTraits.map((t) => (
        `<li><strong>${escapeHtml(t?.name || 'Trait')}</strong></li>`
      )).join('');
      parts.push(`<div><strong>Racial traits</strong><ul class="fugassa-char-list">${rows}</ul></div>`);
    }
    if (hb.class_resources && typeof hb.class_resources === 'object') {
      const rows = Object.entries(hb.class_resources).map(([k, v]) => {
        const val = typeof v === 'object' ? JSON.stringify(v) : String(v);
        return `<li><strong>${escapeHtml(k)}:</strong> ${escapeHtml(val)}</li>`;
      }).join('');
      if (rows) parts.push(`<div><strong>Class resources</strong><ul class="fugassa-char-list">${rows}</ul></div>`);
    }
    const sc = hb.spellcasting;
    if (sc && typeof sc === 'object') {
      const bits = [
        sc.has ? `${sc.model || 'prepared'} (${String(sc.ability || '').toUpperCase()})` : 'none at this level',
      ];
      if (sc.has) {
        if (sc.cantrips_known != null) bits.push(`cantrips ${sc.cantrips_known}`);
        if (sc.spells_known != null) bits.push(`spells known ${sc.spells_known}`);
        if (sc.spells_prepared_estimate != null) bits.push(`spells prepared ${sc.spells_prepared_estimate}`);
      }
      parts.push(`<p><strong>Spellcasting:</strong> ${escapeHtml(bits.join(' · '))}</p>`);
    }
    if (Array.isArray(hb.spell_catalog) && hb.spell_catalog.length) {
      parts.push(`<p><strong>Spell catalog:</strong> ${hb.spell_catalog.length} homebrew spell(s)</p>`);
    }
    homebrewPreview.innerHTML = parts.join('');
  }

  function renderHomebrewChoicePickers() {
    const pending = currentSheet?.homebrew_pending_choices || [];
    const active = document.activeElement;
    const focusId = active?.dataset?.homebrewChoiceId || '';
    const focusStart = focusId && typeof active.selectionStart === 'number'
      ? active.selectionStart
      : null;
    const focusEnd = focusId && typeof active.selectionEnd === 'number'
      ? active.selectionEnd
      : null;
    homebrewChoicesContainer.innerHTML = '';
    choicesSection.classList.remove('is-attention');
    if (!pending.length) {
      choicesSection.hidden = true;
      homebrewChoicesHint.textContent = '';
      return;
    }
    draft.homebrew_choices ||= {};
    choicesSection.hidden = false;
    homebrewChoicesHint.textContent = 'These are mandatory sheet choices (tool, bonus skill, etc.) — not optional level-4 feats. Use the dropdowns below.';
    let incomplete = 0;
    pending.forEach((choice) => {
      const id = String(choice.id || '');
      if (!id) return;
      const row = document.createElement('div');
      row.className = 'fugassa-homebrew-choice-row';
      const title = document.createElement('strong');
      title.textContent = choice.label || id;
      row.appendChild(title);
      if (choice.hint) {
        const hint = document.createElement('p');
        hint.className = 'fugassa-muted';
        hint.textContent = choice.hint;
        row.appendChild(hint);
      }
      const syncChoice = (value, { recompute = true } = {}) => {
        draft.homebrew_choices[id] = value;
        onChange?.(draft);
        if (recompute) scheduleCompute();
      };
      const saved = String(draft.homebrew_choices[id] || '');
      if (!saved.trim()) incomplete += 1;
      if (choice.type === 'skill_any') {
        const select = document.createElement('select');
        select.className = 'fugassa-input';
        select.dataset.homebrewChoiceId = id;
        const blank = document.createElement('option');
        blank.value = '';
        blank.textContent = '— pick a skill —';
        select.appendChild(blank);
        SKILLS.forEach(([skillId, label]) => {
          const opt = document.createElement('option');
          opt.value = skillId;
          opt.textContent = label;
          select.appendChild(opt);
        });
        const savedSkill = String(draft.homebrew_choices[id] || '');
        select.value = SKILLS.some(([skillId]) => skillId === savedSkill) ? savedSkill : '';
        if (!select.value) row.classList.add('is-incomplete');
        select.addEventListener('change', () => syncChoice(select.value));
        row.appendChild(select);
      } else if (choice.type === 'text') {
        const input = document.createElement('input');
        input.className = 'fugassa-input';
        input.type = 'text';
        input.placeholder = 'Your choice';
        input.dataset.homebrewChoiceId = id;
        input.value = String(draft.homebrew_choices[id] || '');
        if (!input.value.trim()) row.classList.add('is-incomplete');
        input.addEventListener('input', () => {
          draft.homebrew_choices[id] = input.value;
          row.classList.toggle('is-incomplete', !input.value.trim());
          onChange?.(draft);
        });
        input.addEventListener('blur', () => syncChoice(input.value.trim()));
        row.appendChild(input);
      } else {
        const select = document.createElement('select');
        select.className = 'fugassa-input';
        select.dataset.homebrewChoiceId = id;
        const blank = document.createElement('option');
        blank.value = '';
        blank.textContent = '— pick one —';
        select.appendChild(blank);
        (choice.options || []).forEach((optLabel) => {
          const opt = document.createElement('option');
          opt.value = optLabel;
          opt.textContent = optLabel;
          select.appendChild(opt);
        });
        const savedEnum = String(draft.homebrew_choices[id] || '');
        select.value = (choice.options || []).includes(savedEnum) ? savedEnum : '';
        if (!select.value) row.classList.add('is-incomplete');
        select.addEventListener('change', () => syncChoice(select.value));
        row.appendChild(select);
      }
      homebrewChoicesContainer.appendChild(row);
    });
    if (incomplete > 0) {
      choicesSection.classList.add('is-attention');
    }
    if (focusId) {
      const restore = homebrewChoicesContainer.querySelector(`[data-homebrew-choice-id="${focusId}"]`);
      if (restore) {
        restore.focus();
        if (focusStart != null && focusEnd != null && typeof restore.setSelectionRange === 'function') {
          try {
            restore.setSelectionRange(focusStart, focusEnd);
          } catch (_) {
            /* ignore for non-text controls */
          }
        }
      }
    }
  }

  function renderFeaturesPanel() {
    if (!currentSheet) {
      featuresSection.hidden = true;
      return;
    }
    featuresBody.innerHTML = '';
    const items = [
      ...(currentSheet.racial_traits || []).map((t) => ({ ...t, source: 'race' })),
      ...(currentSheet.class_features || []).map((f) => ({ ...f, source: 'class' })),
      ...(currentSheet.subclass_features || []).map((f) => ({ ...f, source: 'subclass' })),
      ...(currentSheet.feats_picked || []).map((f) => ({ name: f.name, level: f.level, desc: 'Feat', source: 'feat' })),
    ];
    if (!items.length) {
      featuresSection.hidden = true;
      return;
    }
    items.forEach((item) => featuresBody.appendChild(renderFeatureCard('', item)));
    featuresSection.hidden = false;
  }

  function buildSpellNameMap(sheet) {
    const map = {};
    (sheet?.homebrew_spell_catalog || []).forEach((sp) => {
      if (sp?.index) map[sp.index] = sp.name || sp.index;
    });
    srdSpells.forEach((sp) => {
      if (!sp?.index) return;
      map[sp.index] = sp.name || sp.index;
      map[`hbspell:srdfallback:${sp.index}`] = sp.name || sp.index;
    });
    return map;
  }

  function renderSummaryFromSheet(sheet) {
    if (!sheet) {
      summary.textContent = 'Computing character sheet…';
      return;
    }
    const labels = sheet.labels || {};
    const abilities = sheet.abilities || {};
    draft.sheet_snapshot = buildWizardSheetSnapshot(draft, sheet, buildSpellNameMap(sheet));
    const choiceLines = wizardCharacterSummaryLines(draft, draft.sheet_snapshot);
    summary.textContent = [
      `${draft.player_name || 'Hero'} | ${labels.race || '—'} ${labels.class || '—'}${labels.subclass ? ` (${labels.subclass})` : ''}`,
      labels.subrace ? `Subrace: ${labels.subrace}` : '',
      `Gender: ${effectiveGender(draft) || '—'} | Age: ${draft.player_age || '—'} | Level ${sheet.level}`,
      `PB ${formatModifier(sheet.proficiency_bonus)} | HP ${sheet.hp} | AC ${sheet.ac_base} | Speed ${sheet.speed} ft | d${sheet.hit_die}`,
      `Passive Perception ${sheet.passive_perception}`,
      '',
      'Abilities',
      ...ABILITY_KEYS.map((key) => {
        const val = abilities[key] ?? abilities[key === 'str' ? 'strength' : key];
        const mod = abilityMod(val);
        const pre = sheet.abilities_pre_race?.[key];
        const bonus = sheet.ability_bonuses_race?.[key] || 0;
        return `${key.toUpperCase()} ${val} (${formatModifier(mod)})  [base ${pre ?? '?'} + race ${bonus}]`;
      }),
      '',
      'Saving Throws',
      ...(draft.sheet_snapshot.saves || []),
      '',
      'Skills',
      ...(draft.sheet_snapshot.skills || []),
      ...(choiceLines.length ? ['', ...choiceLines] : []),
    ].filter(Boolean).join('\n');
  }

  function syncHomebrewPanel() {
    const needs = Boolean(currentSheet?.is_homebrew_class || currentSheet?.is_homebrew_race
      || raceChoices()[race.select.selectedIndex] === 'Custom'
      || classChoices()[klass.select.selectedIndex] === 'Custom');
    homebrewSection.hidden = !needs;
    if (draft.homebrew_details && Object.keys(draft.homebrew_details).length) {
      homebrewStatus.textContent = 'Homebrew mechanics saved — review generated rules below.';
      renderHomebrewPreview();
    } else {
      homebrewStatus.textContent = '';
      homebrewPreview.innerHTML = '';
    }
  }

  function focusBuilderIssue(errors, { scroll = false } = {}) {
    const text = (errors || []).join(' ').toLowerCase();
    if (text.includes('required choices') || text.includes('pick an option') || text.includes('tool proficiency')) {
      choicesSection.classList.add('is-attention');
      if (scroll) {
        choicesSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
      return;
    }
    if (text.includes('cantrip') && scroll) {
      spellsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  async function refreshValidation() {
    if (playstyleFramework(draft.playstyle) === 'freeform') {
      validationBox.hidden = true;
      return;
    }
    syncDraftFromFields();
    try {
      const res = await api.validateCharacterSheet(draft);
      const errors = res?.errors || [];
      if (!errors.length) {
        validationBox.hidden = true;
        validationBox.textContent = '';
        choicesSection.classList.remove('is-attention');
        return;
      }
      validationBox.hidden = false;
      validationBox.className = 'fugassa-builder-validation fugassa-field-error';
      validationBox.textContent = errors.join(' ');
      // Highlight sections with open issues but do not scroll — that ran every compute cycle.
      focusBuilderIssue(errors, { scroll: false });
    } catch {
      validationBox.hidden = false;
      validationBox.className = 'fugassa-builder-validation fugassa-muted';
      validationBox.textContent = 'Validation unavailable — check connection.';
    }
  }

  function alignDraftSkillsWithSheet() {
    const allowed = allowedSkillIds();
    const cap = skillCap();
    const ordered = SKILLS.map(([id]) => id).filter(
      (id) => draft.skill_proficiencies?.[id] && (!allowed || allowed.has(id)),
    );
    draft.skill_proficiencies = Object.fromEntries(
      ordered.slice(0, cap).map((id) => [id, true]),
    );
    const classId = currentSheet?.resolved?.class_id || '';
    const lvl = Number(draft.level || 1);
    const expertiseOk = (classId === 'rogue' && lvl >= 1) || (classId === 'bard' && lvl >= 3);
    if (!expertiseOk) {
      draft.expertise = {};
    } else {
      const prof = new Set(Object.keys(draft.skill_proficiencies || {}));
      const next = {};
      Object.entries(draft.expertise || {}).forEach(([id, on]) => {
        if (on && prof.has(id)) next[id] = true;
      });
      draft.expertise = next;
    }
  }

  async function runCompute() {
    buildSkillsGridOnce();
    syncDraftFromFields();
    const seq = ++computeSeq;
    try {
      const res = await api.computeCharacterSheet(draft);
      if (seq !== computeSeq) return;
      currentSheet = res?.sheet || null;
      alignDraftSkillsWithSheet();
      ABILITY_KEYS.forEach((key) => {
        const pre = currentSheet?.abilities_pre_race?.[key] ?? draft.abilities[key];
        const isPointBuy = draft.abilities_method !== 'roll';
        abilityControls[key].meta.textContent = isPointBuy
          ? `cost ${pointBuyCost(draft.abilities[key])}`
          : `mod ${formatModifier(abilityMod(pre))}`;
      });
      enforceSkillCap();
      syncSkillCheckboxesFromDraft();
      showExpertiseIfNeeded();
      renderAsiPickers();
      renderSpellPickers();
      renderClassMechanicPickers();
      renderHomebrewChoicePickers();
      renderFeaturesPanel();
      renderSummaryFromSheet(currentSheet);
      syncHomebrewPanel();
      onChange?.(draft);
      await refreshValidation();
    } catch (error) {
      if (seq !== computeSeq) return;
      summary.textContent = `Sheet compute failed: ${error.message || error}`;
    }
  }

  function scheduleCompute() {
    if (computeTimer) clearTimeout(computeTimer);
    computeTimer = setTimeout(() => {
      computeTimer = null;
      runCompute();
    }, COMPUTE_DEBOUNCE_MS);
  }

  function enforceAbilityMode() {
    const isPointBuy = method.select.selectedIndex === 0;
    ABILITY_KEYS.forEach((key) => {
      const input = abilityControls[key].input.input;
      input.min = isPointBuy ? '8' : '3';
      input.max = isPointBuy ? '15' : '18';
      if (isPointBuy) input.value = String(clamp(Number(input.value || 8), 8, 15));
    });
    rollBtn.disabled = draft.abilities_roll_used || !method.select.selectedIndex;
    methodStatus.textContent = isPointBuy
      ? `Point-buy budget: ${pointBuySpent(draft.abilities)}/${POINT_BUY_BUDGET}`
      : (draft.abilities_roll_used ? 'Rolled stats are locked for this draft.' : 'Roll once for all six scores.');
    abilityHint.textContent = isPointBuy
      ? 'Point-buy uses 8-15 before racial bonuses (server applies race from SRD).'
      : 'Roll mode uses 4d6-drop-lowest per ability.';
  }

  homebrewGenerate.addEventListener('click', async () => {
    if (homebrewBusy) return;
    homebrewBusy = true;
    homebrewGenerate.disabled = true;
    homebrewStatus.textContent = 'Generating homebrew mechanics…';
    syncDraftFromFields();
    try {
      const res = await api.generateHomebrewSheet(draft);
      draft.homebrew_details = normalizeHomebrewDetails(
        res?.data?.homebrew_details || res?.homebrew_details || {},
      );
      homebrewStatus.textContent = 'Homebrew mechanics generated.';
      onChange?.(draft);
      await runCompute();
    } catch (error) {
      homebrewStatus.textContent = error.message || String(error);
    } finally {
      homebrewBusy = false;
      homebrewGenerate.disabled = false;
    }
  });

  homebrewClear.addEventListener('click', () => {
    draft.homebrew_details = {};
    homebrewStatus.textContent = 'Homebrew cleared.';
    homebrewPreview.innerHTML = '';
    onChange?.(draft);
    scheduleCompute();
  });

  rollBtn.addEventListener('click', () => {
    draft.abilities_roll_used = true;
    method.select.selectedIndex = 1;
    ABILITY_KEYS.forEach((key) => {
      abilityControls[key].input.input.value = String(rollAbility());
    });
    enforceAbilityMode();
    scheduleCompute();
  });

  resetBtn.addEventListener('click', () => {
    draft.abilities_roll_used = false;
    method.select.selectedIndex = 0;
    ABILITY_KEYS.forEach((key) => {
      abilityControls[key].input.input.value = '8';
    });
    enforceAbilityMode();
    scheduleCompute();
  });

  [
    playerName.input, level.input, gender.select, genderCustom.input,
    race.select, raceCustom.input, subrace.select, subraceCustom.input,
    klass.select, classCustom.input, subclass.select, subclassCustom.input,
    age.input, template.select, method.select,
  ].forEach((el) => {
    el.addEventListener('input', () => {
      syncVisibility();
      enforceAbilityMode();
      scheduleCompute();
    });
    el.addEventListener('change', () => {
      syncVisibility();
      enforceAbilityMode();
      scheduleCompute();
    });
  });

  ABILITY_KEYS.forEach((key) => {
    const input = abilityControls[key].input.input;
    input.addEventListener('input', () => {
      if (method.select.selectedIndex === 0) {
        input.value = String(clamp(Number(input.value || 8), 8, 15));
      }
      enforceAbilityMode();
      scheduleCompute();
    });
  });

  buildSkillsGridOnce();
  syncSkillCheckboxesFromDraft();

  Promise.all([
    api.getDnd5e('races').then((data) => { srdRaces = Array.isArray(data) ? data : []; }),
    api.getDnd5e('spells').then((data) => { srdSpells = Array.isArray(data) ? data : []; }),
  ]).finally(() => {
    syncVisibility();
    enforceAbilityMode();
    runCompute();
  });

  syncVisibility();
  enforceAbilityMode();

  return {
    el: root,
    refresh() {
      syncVisibility();
      enforceAbilityMode();
      runCompute();
    },
    async syncSnapshot() {
      await runCompute();
    },
    collect() {
      syncDraftFromFields();
      return draft;
    },
    async validate() {
      syncDraftFromFields();
      if (playstyleFramework(draft.playstyle) === 'freeform') return '';
      try {
        const res = await api.validateCharacterSheet(draft);
        if (res?.ok) {
          validationBox.hidden = true;
          validationBox.textContent = '';
          choicesSection.classList.remove('is-attention');
          return '';
        }
        const errors = res?.errors || [];
        validationBox.hidden = false;
        validationBox.className = 'fugassa-builder-validation fugassa-field-error';
        validationBox.textContent = errors.join(' ');
        focusBuilderIssue(errors, { scroll: true });
        return errors.join(' ') || 'Character sheet incomplete.';
      } catch {
        return 'Unable to validate character sheet right now.';
      }
    },
  };
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function asiKindIndex(kind) {
  if (kind === 'plus2') return 1;
  if (kind === 'plus1plus1') return 2;
  if (kind === 'feat') return 3;
  return 0;
}

function asiKindValue(index) {
  if (index === 1) return 'plus2';
  if (index === 2) return 'plus1plus1';
  if (index === 3) return 'feat';
  return 'none';
}

function normalizeHomebrewDetails(hb) {
  if (!hb || typeof hb !== 'object') return {};
  const out = { ...hb };
  const classBlob = out.class;
  if (classBlob && typeof classBlob === 'object') {
    const classKeys = [
      'hit_die', 'saving_throw_profs', 'skill_proficiency_options',
      'skill_proficiency_choose', 'optional_skill_proficiency_choose',
      'class_features', 'subclass_features', 'spellcasting', 'class_resources', 'class_name',
    ];
    classKeys.forEach((key) => {
      const val = classBlob[key];
      if (val != null && val !== '' && !(Array.isArray(val) && !val.length) && !(typeof val === 'object' && !Array.isArray(val) && !Object.keys(val).length)) {
        out[key] = val;
      }
    });
    if (classBlob.name && !out.class_name) out.class_name = classBlob.name;
    delete out.class;
  }
  const raceBlob = out.race;
  if (raceBlob && typeof raceBlob === 'object') {
    const raceKeys = [
      'racial_traits', 'ability_bonuses_race', 'speed', 'size',
      'languages', 'skill_proficiency_bonus_race', 'race_name',
    ];
    raceKeys.forEach((key) => {
      const val = raceBlob[key];
      if (val != null && val !== '' && !(Array.isArray(val) && !val.length) && !(typeof val === 'object' && !Array.isArray(val) && !Object.keys(val).length)) {
        out[key] = val;
      }
    });
    if (raceBlob.name && !out.race_name) out.race_name = raceBlob.name;
    delete out.race;
  }
  if (out.skill_proficiency_choose == null && out.optional_skill_proficiency_choose != null) {
    out.skill_proficiency_choose = out.optional_skill_proficiency_choose;
  }
  return out;
}

function ensureDraftShape(draft) {
  draft.abilities ||= {};
  ABILITY_KEYS.forEach((key) => {
    if (!Number.isFinite(Number(draft.abilities[key]))) draft.abilities[key] = 10;
  });
  draft.skill_proficiencies ||= {};
  draft.expertise ||= {};
  draft.selected_cantrips ||= [];
  draft.selected_spells_by_level ||= {};
  draft.asi_choices ||= {};
  draft.homebrew_choices ||= {};
  draft.class_mechanic_choices ||= {};
  draft.homebrew_details = normalizeHomebrewDetails(draft.homebrew_details || {});
}

function rollAbility() {
  const dice = [d6(), d6(), d6(), d6()].sort((a, b) => a - b);
  return dice[1] + dice[2] + dice[3];
}

function d6() {
  return 1 + Math.floor(Math.random() * 6);
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, Number.isFinite(Number(value)) ? Number(value) : min));
}

function button(label) {
  const el = document.createElement('button');
  el.type = 'button';
  el.className = 'fugassa-btn fugassa-btn--ghost fugassa-btn--sm';
  el.textContent = label;
  return el;
}

function row(children) {
  const el = document.createElement('div');
  el.className = 'fugassa-builder-actions';
  children.forEach((child) => el.appendChild(child));
  return el;
}

function labelEl(text) {
  const el = document.createElement('span');
  el.className = 'fugassa-builder-label';
  el.textContent = text;
  return el;
}

function fieldWrap(field) {
  return field.el;
}

function textField(label, value) {
  const el = document.createElement('label');
  el.className = 'fugassa-field';
  const title = document.createElement('span');
  title.textContent = label;
  const input = document.createElement('input');
  input.type = 'text';
  input.value = value || '';
  el.append(title, input);
  return { el, input };
}

function numberField(label, value, min, max) {
  const el = document.createElement('label');
  el.className = 'fugassa-field';
  const title = document.createElement('span');
  title.textContent = label;
  const input = document.createElement('input');
  input.type = 'number';
  input.min = String(min);
  input.max = String(max);
  input.step = '1';
  input.value = String(value);
  el.append(title, input);
  return { el, input };
}

function selectField(label, options, selectedIndex = 0) {
  const el = document.createElement('label');
  el.className = 'fugassa-field';
  const title = document.createElement('span');
  title.textContent = label;
  const select = document.createElement('select');
  replaceOptions(select, options);
  select.selectedIndex = Math.max(0, Math.min(select.options.length - 1, Number(selectedIndex) || 0));
  el.append(title, select);
  return { el, select };
}

function replaceOptions(select, options) {
  select.innerHTML = '';
  (options.length ? options : ['—']).forEach((label) => {
    const option = document.createElement('option');
    option.textContent = label;
    option.value = label;
    select.appendChild(option);
  });
}
