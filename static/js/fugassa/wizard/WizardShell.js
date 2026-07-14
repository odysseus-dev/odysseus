import uiModule from '../../ui.js';
import * as api from '../fugassaApi.js';
import { createWizardChat } from './WizardChat.js';
import { createDnd5eCharacterBuilder } from './dnd5eCharacterBuilder.js';
import { classChoices, genderChoices, raceChoices } from './dnd5eOptions.js';
import { emptyDraft } from './defaultDraft.js';
import {
  builtInGuides,
  CAMPAIGN_LENGTH_OPTIONS,
  characterProfile,
  clone,
  combinedGuidesText,
  cumulativeWorldContext,
  inventoryGearWizardContext,
  defaultCurrencyForTheme,
  defaultImageStyleHintForTheme,
  currencyConversionHint,
  dialogTranscript,
  FALLBACK_IMAGE_STYLES,
  formatInventoryOptionsForChat,
  formatGearOptionsForChat,
  extractGearJson,
  parseGearOptionsRaw,
  effectiveClass,
  effectiveGender,
  effectiveRace,
  effectiveSubclass,
  escapeHtml,
  GENERIC_FANTASY_CURRENCY,
  gearSummaryText,
  inferOptionNumber,
  inventoryNotesFromStructured,
  inventoryStructuredFromDraft,
  openingStructuredFromDraft,
  optionBatchHelpers,
  PLAYSTYLE_OPTIONS,
  playstyleFramework,
  playstyleLabel,
  POINT_BUY_BUDGET,
  startingWealthPreview,
  startingWealthSummaryLines,
  PORTRAIT_ROW_LABELS,
  PORTRAIT_ROW_OPTIONS,
  requestAfterOptionPick,
  RESOLUTION_MODE_OPTIONS,
  RULES_MODE_OPTIONS,
  rulesContext,
  themeLabel,
  THEME_OPTIONS,
  WIZARD_TAB_LABELS,
  wizardCharacterSummaryLines,
} from './helpers.js';
import {
  debouncedSaveDraft,
  flushDraftInto,
  normalizeDraft,
  clearDraft as clearWizardDraft,
} from './draft.js';

const HELP_TEXT = {
  WorldDefinition: 'Send updates the canonical world brief. Suggest 3 generates proposals in chat only until you choose and refine one.',
  Backstory: 'Send writes or refines the canonical backstory. Suggest 3 generates alternatives without overwriting the saved backstory.',
  Inventory: 'Inventory covers starter items, tools, consumables, and campaign currency tiers. Weapon and armor belong on the Gear tab.',
  Gear: 'Gear tracks your primary weapon and worn armor. Suggest 3 stores option bundles without applying them until refined.',
  Opening: 'Opening controls the first scene and time hint. Suggest 3 returns structured opening bundles.',
  Picture: 'Generate Prompt calls the portrait prompt API. Stable Diffusion generation is still routed through the Titan scheduler stub.',
};

export function mountWizardShell(root, { draft, initialTab = 0, onBack, onCreated, onSessionChange } = {}) {
  const state = normalizeDraft(draft || emptyDraft());
  const guides = { ...builtInGuides(), ...(state.gm_guides_map || {}) };
  let currentTab = clamp(initialTab, 0, WIZARD_TAB_LABELS.length - 1);
  if (currentTab > state.unlocked_tab) currentTab = state.unlocked_tab;

  root.innerHTML = '';
  root.className = 'fugassa-wizard-shell';

  const header = document.createElement('div');
  header.className = 'fugassa-wizard-header';
  const title = document.createElement('div');
  title.className = 'fugassa-wizard-title';
  title.innerHTML = '<h2>Fugassa II Wizard</h2><p>Godot-style tab flow for Titan</p>';
  const progress = document.createElement('div');
  progress.className = 'fugassa-wizard-progress';
  header.append(title, progress);

  const tabsBar = document.createElement('div');
  tabsBar.className = 'fugassa-wizard-tabs';

  const content = document.createElement('div');
  content.className = 'fugassa-wizard-content';

  const footer = document.createElement('div');
  footer.className = 'fugassa-wizard-footer';
  const feedback = document.createElement('div');
  feedback.className = 'fugassa-wizard-feedback';
  const actions = document.createElement('div');
  actions.className = 'fugassa-wizard-footer-actions';
  const saveBtn = footerButton('Save tab', 'primary');
  const createBtn = footerButton('Create', 'primary');
  const backBtn = footerButton('Back', 'ghost');
  actions.append(saveBtn, createBtn, backBtn);
  footer.append(feedback, actions);

  root.append(header, tabsBar, content, footer);

  const tabSections = [];
  const tabButtons = [];
  const trackChange = () => {
    debouncedSaveDraft(state);
    onSessionChange?.({
      mode: 'menu',
      menuScreen: 'wizard',
      wizardStep: currentTab,
      lastTool: 'fugassa',
    });
  };

  /** Persist assistant reply in draft chat history (survives page refresh). */
  function persistWizardAssistantTurn(historyKey, messages, assistantContent) {
    const reply = String(assistantContent || '').trim();
    state[historyKey] = reply
      ? [...clone(messages), { role: 'assistant', content: reply }]
      : clone(messages);
  }

  async function saveWizardDraftNow() {
    await flushDraftInto(state);
    onSessionChange?.({
      mode: 'menu',
      menuScreen: 'wizard',
      wizardStep: currentTab,
      lastTool: 'fugassa',
    });
  }

  const showFeedback = (text, error = false) => {
    feedback.textContent = text || '';
    feedback.dataset.error = error ? '1' : '0';
  };

  const syncGuides = () => {
    state.gm_guides_map = clone(guides);
    state.gm_guides_notes = combinedGuidesText(guides);
  };

  function refreshTabs() {
    tabButtons.forEach((button, index) => {
      button.disabled = index > state.unlocked_tab;
      button.classList.toggle('is-active', index === currentTab);
      button.classList.toggle('is-locked', index > state.unlocked_tab);
      button.querySelector('.fugassa-tab-lock').textContent = index > state.unlocked_tab ? 'Locked' : '';
    });
    tabSections.forEach((section, index) => {
      const active = index === currentTab;
      section.hidden = !active;
      section.classList.toggle('is-active', active);
    });
    progress.textContent = `Progress ${Math.min(state.unlocked_tab + 1, WIZARD_TAB_LABELS.length)} / ${WIZARD_TAB_LABELS.length}`;
    createBtn.disabled = !(currentTab === WIZARD_TAB_LABELS.length - 1 && state.unlocked_tab >= WIZARD_TAB_LABELS.length - 1);
    onSessionChange?.({
      mode: 'menu',
      menuScreen: 'wizard',
      wizardStep: currentTab,
      lastTool: 'fugassa',
    });
  }

  function goToTab(index) {
    if (index > state.unlocked_tab) return;
    currentTab = clamp(index, 0, WIZARD_TAB_LABELS.length - 1);
    refreshTabs();
    if (currentTab === WIZARD_TAB_LABELS.length - 1) {
      void renderSummary();
    }
  }

  function addTab(label, section) {
    const idx = tabSections.length;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'fugassa-wizard-tab';
    button.innerHTML = `<span class="fugassa-tab-label">${escapeHtml(label)}</span><span class="fugassa-tab-lock"></span>`;
    button.addEventListener('click', () => goToTab(idx));
    tabsBar.appendChild(button);
    section.classList.add('fugassa-wizard-panel');
    section.hidden = true;
    tabButtons.push(button);
    tabSections.push(section);
    content.appendChild(section);
  }

  const campaignTab = document.createElement('section');
  const worldName = inputField('World name', state.world_name, 'Used for the save name and campaign title.');
  const campaignLength = selectField('Campaign length', CAMPAIGN_LENGTH_OPTIONS, state.campaign_length);
  campaignTab.append(
    tabIntro('Campaign', 'Set the save name and pacing for the campaign.'),
    worldName.el,
    campaignLength.el,
  );
  worldName.input.addEventListener('input', () => { state.world_name = worldName.input.value; trackChange(); });
  campaignLength.select.addEventListener('change', () => { state.campaign_length = campaignLength.select.value; trackChange(); });
  addTab('Campaign', campaignTab);

  const genreTab = document.createElement('section');
  const themeMode = selectField('Genre', THEME_OPTIONS, state.theme_mode);
  const themeCustom = inputField('Custom theme', state.theme_custom, 'Only used when Genre is Custom.');
  const imageStyleEl = document.createElement('label');
  imageStyleEl.className = 'fugassa-field';
  const imageStyleTitle = document.createElement('span');
  imageStyleTitle.textContent = 'Image generator';
  const imageStyleSelect = document.createElement('select');
  const imageStyleHint = document.createElement('div');
  imageStyleHint.className = 'fugassa-muted';
  imageStyleEl.append(imageStyleTitle, imageStyleSelect, imageStyleHint);

  const IMAGE_STYLE_AUTO = 'auto';

  const syncImageStyleHint = () => {
    const selected = String(state.image_style || '').trim();
    if (selected === IMAGE_STYLE_AUTO || !selected) {
      const autoStyle = defaultImageStyleHintForTheme(themeMode.select.value);
      imageStyleHint.textContent = `Auto picks a generator from genre (currently ~${autoStyle}).`;
      return;
    }
    const label = imageStyleSelect.selectedOptions[0]?.textContent || selected;
    imageStyleHint.textContent = `Campaign scenes and portraits use ${label}.`;
  };

  const populateImageStyles = (styles) => {
    imageStyleSelect.replaceChildren();
    const available = (styles || []).filter((s) => s && s.id);
    available.forEach(({ id, label }) => {
      const opt = document.createElement('option');
      opt.value = id;
      opt.textContent = label || id;
      imageStyleSelect.appendChild(opt);
    });
    const autoOpt = document.createElement('option');
    autoOpt.value = IMAGE_STYLE_AUTO;
    autoOpt.textContent = 'Auto (match genre)';
    imageStyleSelect.appendChild(autoOpt);

    const saved = String(state.image_style || '').trim();
    const validValues = new Set([...imageStyleSelect.options].map((o) => o.value));
    let nextValue = IMAGE_STYLE_AUTO;
    if (saved === IMAGE_STYLE_AUTO || !saved) {
      nextValue = IMAGE_STYLE_AUTO;
    } else if (validValues.has(saved)) {
      nextValue = saved;
    } else if (available.length > 0) {
      const hint = defaultImageStyleHintForTheme(themeMode.select.value);
      nextValue = available.find((s) => s.id === hint)?.id || available[0].id;
      if (nextValue !== saved) {
        state.image_style = nextValue;
        trackChange();
      }
    } else {
      nextValue = IMAGE_STYLE_AUTO;
      if (saved !== IMAGE_STYLE_AUTO) {
        state.image_style = IMAGE_STYLE_AUTO;
        trackChange();
      }
    }
    imageStyleSelect.value = nextValue;
    syncImageStyleHint();
  };

  populateImageStyles(state._image_styles || FALLBACK_IMAGE_STYLES);

  const syncThemeCustom = () => { themeCustom.el.style.display = themeMode.select.value === 'Custom' ? '' : 'none'; };
  genreTab.append(
    tabIntro('Genre', 'Choose the genre tag and which installed SD generator this campaign uses for scenes and portraits.'),
    themeMode.el,
    themeCustom.el,
    imageStyleEl,
  );
  themeMode.select.addEventListener('change', () => {
    state.theme_mode = themeMode.select.value;
    syncThemeCustom();
    syncImageStyleHint();
    trackChange();
  });
  themeCustom.input.addEventListener('input', () => { state.theme_custom = themeCustom.input.value; trackChange(); });
  imageStyleSelect.addEventListener('change', () => {
    state.image_style = imageStyleSelect.value;
    syncImageStyleHint();
    trackChange();
  });
  api.getImageStyles()
    .then((payload) => populateImageStyles(payload?.styles || state._image_styles || FALLBACK_IMAGE_STYLES))
    .catch(() => populateImageStyles(state._image_styles || FALLBACK_IMAGE_STYLES));
  syncThemeCustom();
  addTab('Genre', genreTab);

  const rulesTab = document.createElement('section');
  const playstyle = selectField('Playstyle', PLAYSTYLE_OPTIONS.map((item) => item.label), playstyleLabel(state.playstyle));
  const rulesMode = selectField('Rules mode', RULES_MODE_OPTIONS.map((item) => item.label), labelForValue(RULES_MODE_OPTIONS, state.rules_mode));
  const resolutionMode = selectField('Resolution', RESOLUTION_MODE_OPTIONS.map((item) => item.label), labelForValue(RESOLUTION_MODE_OPTIONS, state.resolution_mode));
  const gmGuideName = selectField('GM guide', Object.keys(guides), state.gm_selected_guide);
  const gmGuideBody = textAreaField('Guide text', guides[gmGuideName.select.value] || '', 14);
  const rulesNote = document.createElement('div');
  rulesNote.className = 'fugassa-muted';
  const syncRulesUi = () => {
    state.playstyle = PLAYSTYLE_OPTIONS[playstyle.select.selectedIndex]?.value || 'adventure';
    state.playstyle_framework = playstyleFramework(state.playstyle);
    const freeform = state.playstyle_framework === 'freeform';
    rulesMode.el.style.display = freeform ? 'none' : '';
    resolutionMode.el.style.display = freeform ? 'none' : '';
    rulesNote.textContent = freeform
      ? 'Freeform playstyle selected: the character sheet becomes narrative context rather than hard dice automation.'
      : 'Rules-based playstyle selected: character sheet, gear, and opening context stay mechanical.';
  };
  rulesTab.append(
    tabIntro('Rules', 'Configure playstyle, mechanical framing, and GM guide text.'),
    playstyle.el,
    rulesMode.el,
    resolutionMode.el,
    rulesNote,
    gmGuideName.el,
    gmGuideBody.el,
  );
  playstyle.select.addEventListener('change', () => { syncRulesUi(); trackChange(); });
  rulesMode.select.addEventListener('change', () => {
    state.rules_mode = RULES_MODE_OPTIONS[rulesMode.select.selectedIndex]?.value || '5e-style';
    trackChange();
  });
  resolutionMode.select.addEventListener('change', () => {
    state.resolution_mode = RESOLUTION_MODE_OPTIONS[resolutionMode.select.selectedIndex]?.value || 'dice';
    trackChange();
  });
  gmGuideName.select.addEventListener('change', () => {
    state.gm_selected_guide = gmGuideName.select.value;
    gmGuideBody.textarea.value = guides[state.gm_selected_guide] || '';
    syncGuides();
    trackChange();
  });
  gmGuideBody.textarea.addEventListener('input', () => {
    guides[gmGuideName.select.value] = gmGuideBody.textarea.value;
    syncGuides();
    trackChange();
  });
  syncRulesUi();
  addTab('Rules', rulesTab);

  const worldTab = createWorldLikeTab({
    title: 'WorldDefinition',
    kind: 'world',
    intro: 'Talk with the assistant about the campaign world and keep one canonical world brief.',
    placeholder: 'Describe the world, tone, factions, or must-have lore…',
    draft: state,
    initialMessages: state.world_info_chat_history,
    definitionText: () => state.world_information || '',
    setDefinitionText: (value) => { state.world_information = value; trackChange(); },
    suggestKey: 'world_campaign_options_text',
    onSend: async ({ text, messages }) => {
      state.world_info_chat_history = clone(messages);
      trackChange();
      const response = await api.wizardWorldSummary({
        theme: themeLabel(state.theme_mode, state.theme_custom),
        campaignLength: state.campaign_length,
        currentDraft: pickWorldCurrentDraft(text),
        playerRequest: text,
        dialog: messages,
        dialogTranscript: dialogTranscript(messages),
        draft: state,
      });
      const payload = response?.data || response || {};
      const content = String(payload.text || payload.content || payload.world_definition || '').trim();
      if (looksLikeOptionBatch(content)) {
        state.world_campaign_options_text = content;
      } else if (content) {
        state.world_information = content;
      }
      persistWizardAssistantTurn('world_info_chat_history', messages, content);
      await saveWizardDraftNow();
      const status = looksLikeOptionBatch(content)
        ? 'Campaign options in chat — pick one and refine to save world brief.'
        : (content ? 'World updated.' : 'No response content.');
      return { content, status };
    },
    onSuggest: async ({ messages }) => {
      const response = await api.wizardWorldOptions({
        theme: themeLabel(state.theme_mode, state.theme_custom),
        campaignLength: state.campaign_length,
        playerRequest: '',
        optionStart: state.world_options_next_start || 1,
        draft: state,
      });
      const payload = response?.data || response || {};
      const content = String(payload.text || payload.content || '').trim();
      state.world_campaign_options_text = content;
      state.world_campaign_batch_start = payload.optionStartUsed || state.world_options_next_start || 1;
      state.world_options_next_start = (payload.optionStartUsed || state.world_options_next_start || 1) + 3;
      state.world_info_chat_history = [...clone(messages), { role: 'assistant', content }];
      await saveWizardDraftNow();
      return { content, status: 'World proposals generated.' };
    },
  });
  addTab('WorldDefinition', worldTab.el);

  const characterTab = document.createElement('section');
  characterTab.appendChild(tabIntro('Character', 'Set the player identity and mechanical sheet in one merged Fugassa II character tab.'));
  const characterBuilder = createDnd5eCharacterBuilder({
    draft: state,
    onChange: () => trackChange(),
  });
  characterTab.appendChild(characterBuilder.el);
  addTab('Character', characterTab);

  const backstoryTab = createWorldLikeTab({
    title: 'Backstory',
    kind: 'backstory',
    intro: 'Keep one canonical backstory while using the assistant for alternatives and revisions.',
    placeholder: 'Write your backstory or request targeted edits…',
    draft: state,
    initialMessages: state.backstory_chat_history,
    definitionText: () => state.character_background || '',
    setDefinitionText: (value) => { state.character_background = value; trackChange(); },
    suggestKey: 'backstory_options_text',
    onSend: async ({ text, messages }) => {
      state.backstory_chat_history = clone(messages);
      trackChange();
      const response = await api.wizardBackstorySummary({
        theme: themeLabel(state.theme_mode, state.theme_custom),
        playerName: state.player_name,
        currentDraft: pickBackstoryCurrentDraft(text),
        playerRequest: text,
        worldInformation: cumulativeWorldContext(state),
        characterProfile: characterProfile(state),
        dialog: messages,
        dialogTranscript: dialogTranscript(messages),
        draft: state,
      });
      const payload = response?.data || response || {};
      const content = String(payload.text || payload.content || '').trim();
      if (looksLikeOptionBatch(content)) {
        state.backstory_options_text = content;
      } else if (content) {
        state.character_background = content;
      }
      persistWizardAssistantTurn('backstory_chat_history', messages, content);
      await saveWizardDraftNow();
      return { content, status: content ? 'Backstory updated.' : 'No response content.' };
    },
    onSuggest: async ({ messages }) => {
      const response = await api.wizardBackstoryOptions({
        theme: themeLabel(state.theme_mode, state.theme_custom),
        playerName: state.player_name,
        worldInformation: cumulativeWorldContext(state),
        characterProfile: characterProfile(state),
        optionStart: state.backstory_options_next_start || 1,
        draft: state,
      });
      const payload = response?.data || response || {};
      const content = String(payload.text || payload.content || '').trim();
      state.backstory_options_text = content;
      state.backstory_options_batch_start = payload.optionStartUsed || state.backstory_options_next_start || 1;
      state.backstory_options_next_start = (payload.optionStartUsed || state.backstory_options_next_start || 1) + 3;
      state.backstory_chat_history = [...clone(messages), { role: 'assistant', content }];
      await saveWizardDraftNow();
      return { content, status: 'Backstory proposals generated.' };
    },
  });
  addTab('Backstory', backstoryTab.el);

  const pictureTab = document.createElement('section');
  pictureTab.appendChild(tabIntro('Picture', 'Build the portrait appearance prompt and keep the future Titan SD action in place.'));
  const appearanceNotes = textAreaField('Portrait notes', state.portrait_appearance?.notes || '', 4);
  const appearanceRowsWrap = document.createElement('div');
  appearanceRowsWrap.className = 'fugassa-portrait-grid';
  const portraitControls = {};
  Object.entries(PORTRAIT_ROW_OPTIONS).forEach(([key, options]) => {
    const row = document.createElement('div');
    row.className = 'fugassa-portrait-row';
    const label = document.createElement('label');
    label.textContent = PORTRAIT_ROW_LABELS[key];
    const select = document.createElement('select');
    options.forEach((option) => {
      const el = document.createElement('option');
      el.value = option;
      el.textContent = option;
      select.appendChild(el);
    });
    const custom = document.createElement('input');
    custom.type = 'text';
    const saved = state.portrait_appearance?.rows?.[key] || {};
    select.selectedIndex = Math.max(0, Math.min(options.length - 1, Number(saved.i || 0)));
    custom.value = String(saved.t || '');
    const syncCustom = () => { custom.style.display = select.selectedIndex === options.length - 1 ? '' : 'none'; };
    select.addEventListener('change', () => { syncCustom(); savePortraitDraft(); });
    custom.addEventListener('input', savePortraitDraft);
    syncCustom();
    row.append(label, select, custom);
    portraitControls[key] = { select, custom, options };
    appearanceRowsWrap.appendChild(row);
  });
  const portraitPrompt = textAreaField('Generated portrait prompt', state.portrait_sd_prompt_text || '', 10);
  portraitPrompt.textarea.readOnly = true;
  const portraitStatus = document.createElement('div');
  portraitStatus.className = 'fugassa-muted';
  portraitStatus.textContent = state.portrait_sd_status || '';
  const portraitPreview = document.createElement('img');
  portraitPreview.className = 'fugassa-portrait-preview';
  portraitPreview.alt = 'Generated portrait preview';
  portraitPreview.style.display = 'none';
  portraitPreview.style.maxWidth = '320px';
  portraitPreview.style.marginTop = '12px';
  portraitPreview.style.borderRadius = '8px';
  portraitPreview.style.border = '1px solid rgba(255,255,255,0.12)';
  portraitPreview.addEventListener('error', () => { portraitPreview.style.display = 'none'; });
  const portraitPromptBtn = footerButton(state.portrait_sd_prompt_text ? 'Regenerate Prompt' : 'Generate Prompt', 'ghost');
  const portraitSdBtn = footerButton(state.character_portrait_path ? 'Regenerate via Titan SD' : 'Generate via Titan SD', 'ghost');

  function setPortraitStatus(text) {
    portraitStatus.textContent = text || '';
    state.portrait_sd_status = portraitStatus.textContent;
    trackChange();
  }
  function setPortraitPromptText(text) {
    portraitPrompt.textarea.value = text || '';
    state.portrait_sd_prompt_text = portraitPrompt.textarea.value;
    trackChange();
  }
  function showPortraitPreview() {
    portraitPreview.src = api.wizardPortraitStagingUrl(Date.now());
    portraitPreview.style.display = '';
  }
  if (state.character_portrait_path) showPortraitPreview();

  portraitPromptBtn.addEventListener('click', async () => {
    if (portraitPromptBtn.disabled) return;
    const label = portraitPromptBtn.textContent;
    portraitPromptBtn.disabled = true;
    portraitPromptBtn.textContent = 'Thinking…';
    try {
      setPortraitStatus('Generating portrait prompt… this can take up to a minute.');
      const response = await api.wizardPortraitPrompts({
        theme: themeLabel(state.theme_mode, state.theme_custom),
        playerName: state.player_name,
        backstory: state.character_background,
        worldInformation: cumulativeWorldContext(state),
        styleOverride: '',
        characterProfile: characterProfile(state),
        appearanceVisual: portraitAppearanceText(),
      });
      const payload = response?.data || response || {};
      const positive = String(payload.positive_prompt || payload.prompt || '').trim();
      const negative = String(payload.negative_prompt || '').trim();
      if (positive) {
        setPortraitPromptText([`Positive\n${positive}`, negative && `Negative\n${negative}`].filter(Boolean).join('\n\n'));
        setPortraitStatus('Portrait prompt generated.');
        portraitPromptBtn.textContent = 'Regenerate Prompt';
        await flushDraftInto(state);
      } else {
        // Keep whatever prompt was already generated — a failed regeneration
        // should not wipe out a previously working one.
        const message = `Prompt generation failed: ${payload.error || 'model did not return usable JSON'}. Try again.`;
        setPortraitStatus(message);
        uiModule.showToast?.(message, { duration: 4000, leadingIcon: 'alert-triangle' });
        portraitPromptBtn.textContent = label;
      }
    } catch (error) {
      const message = error?.message || String(error);
      setPortraitStatus(message);
      uiModule.showToast?.(`Portrait prompt generation failed: ${message}`, { duration: 4000, leadingIcon: 'alert-triangle' });
      portraitPromptBtn.textContent = label;
    } finally {
      portraitPromptBtn.disabled = false;
    }
  });
  portraitSdBtn.addEventListener('click', async () => {
    const { positive, negative } = parsePortraitPrompts(portraitPrompt.textarea.value);
    if (!positive) {
      setPortraitStatus('Generate a portrait prompt first (or paste a positive prompt).');
      return;
    }
    try {
      portraitSdBtn.disabled = true;
      setPortraitStatus('Generating portrait via Titan SD… this can take up to a minute.');
      const response = await api.wizardPortraitGenerate({
        positive_prompt: positive,
        negative_prompt: negative,
        theme: themeLabel(state.theme_mode, state.theme_custom),
        style_override: state.image_style === 'auto' ? '' : (state.image_style || ''),
      });
      const payload = response?.data || response || {};
      state.character_portrait_path = payload.path || payload.relative_path || state.character_portrait_path;
      setPortraitPromptText([
        `Positive\n${positive}`,
        negative && `Negative\n${negative}`,
      ].filter(Boolean).join('\n\n'));
      setPortraitStatus('Portrait generated and staged for Create.');
      showPortraitPreview();
      portraitSdBtn.textContent = 'Regenerate via Titan SD';
      trackChange();
      await flushDraftInto(state);
      uiModule.showToast?.('Portrait generated via Titan SD', { duration: 2500, leadingIcon: 'check' });
    } catch (error) {
      setPortraitStatus(error.message || String(error));
    } finally {
      portraitSdBtn.disabled = false;
    }
  });
  appearanceNotes.textarea.addEventListener('input', savePortraitDraft);
  pictureTab.append(
    appearanceNotes.el,
    appearanceRowsWrap,
    actionRow([portraitPromptBtn, portraitSdBtn]),
    portraitStatus,
    portraitPrompt.el,
    portraitPreview,
  );
  addTab('Picture', pictureTab);

  const isUntouchedCurrency = !Array.isArray(state.currency)
    || state.currency.every((v, i) => String(v || '').toLowerCase() === GENERIC_FANTASY_CURRENCY[i]);
  if (isUntouchedCurrency) {
    const themed = defaultCurrencyForTheme(themeLabel(state.theme_mode, state.theme_custom));
    if (themed.join('|') !== GENERIC_FANTASY_CURRENCY.join('|')) {
      state.currency = themed;
      trackChange();
    }
  }
  const currencyLow = inputField('Low currency', state.currency?.[0] || 'bronze');
  const currencyMid = inputField('Mid currency', state.currency?.[1] || 'silver');
  const currencyHigh = inputField('High currency', state.currency?.[2] || 'gold');
  const currencyHint = document.createElement('div');
  currencyHint.className = 'fugassa-muted';
  const inventoryWealth = preBlock('Starting wealth (at Create)');
  const syncCurrencyHint = () => {
    state.currency = [currencyLow.input.value.trim(), currencyMid.input.value.trim(), currencyHigh.input.value.trim()];
    currencyHint.textContent = currencyConversionHint(state.currency);
    inventoryWealth.pre.textContent = startingWealthSummaryLines(state).join('\n');
  };
  const syncCurrencyInputs = () => {
    currencyLow.input.value = state.currency?.[0] || '';
    currencyMid.input.value = state.currency?.[1] || '';
    currencyHigh.input.value = state.currency?.[2] || '';
    syncCurrencyHint();
  };
  [currencyLow.input, currencyMid.input, currencyHigh.input].forEach((input) => {
    input.addEventListener('input', () => { syncCurrencyHint(); trackChange(); });
  });
  syncCurrencyHint();

  const inventoryRight = document.createElement('div');
  inventoryRight.className = 'fugassa-inventory-summary';
  const inventoryNotesField = textAreaField('Inventory notes', state.inventory_notes || '', 12);
  inventoryNotesField.textarea.addEventListener('input', () => {
    state.inventory_notes = inventoryNotesField.textarea.value;
    trackChange();
  });
  inventoryRight.append(
    inventoryNotesField.el,
    currencyLow.el,
    currencyMid.el,
    currencyHigh.el,
    currencyHint,
    inventoryWealth.wrap,
  );

  const inventoryTab = createStructuredTab({
    kind: 'inventory',
    title: 'Inventory',
    intro: 'Chat about starter items and campaign currency tiers. Items, currency names, and starting wealth preview stay on the right.',
    draft: state,
    initialMessages: state.inventory_chat_history,
    placeholder: 'Adjust inventory or currency names, or ask for different starter packs…',
    help: HELP_TEXT.Inventory,
    customRight: inventoryRight,
    onSend: async ({ text, messages }) => {
      state.inventory_chat_history = clone(messages);
      trackChange();
      const response = await api.wizardInventorySummary({
        theme: themeLabel(state.theme_mode, state.theme_custom),
        playerName: state.player_name,
        worldInformation: inventoryGearWizardContext(state),
        currentDraft: JSON.stringify(selectInventoryCurrentDraft(text)),
        playerRequest: requestAfterOptionPick(text, inferOptionNumber(text, state.inventory_options_batch_start || 1)),
        dialog: messages,
        dialogTranscript: dialogTranscript(messages),
        draft: state,
      });
      const payload = response?.data || response || {};
      const rawContent = String(payload.text || payload.content || '').trim();
      const optionText = formatInventoryOptionsForChat(rawContent, state.inventory_options_batch_start || 1)
        || formatInventoryOptionsForChat(String(payload.raw || ''), state.inventory_options_batch_start || 1);
      if (optionText) {
        state.inventory_options_raw = String(payload.raw || rawContent).trim();
        persistWizardAssistantTurn('inventory_chat_history', messages, optionText);
        await saveWizardDraftNow();
        return { content: optionText, status: 'Inventory proposals generated.' };
      }
      const applied = applyInventoryJson(rawContent);
      if (applied) {
        inventoryNotesField.textarea.value = state.inventory_notes || '';
        syncCurrencyInputs();
      }
      trackChange();
      const content = applied
        ? [
            state.inventory_notes && `Inventory:\n${state.inventory_notes}`,
            Array.isArray(state.currency) && state.currency.length
              ? `Currency: ${state.currency.filter(Boolean).join(' → ')}\n${currencyConversionHint(state.currency)}`
              : '',
            inventoryWealth.pre.textContent,
          ].filter(Boolean).join('\n\n') || 'Inventory updated.'
        : rawContent;
      persistWizardAssistantTurn('inventory_chat_history', messages, content);
      await saveWizardDraftNow();
      return { content, status: applied ? 'Inventory updated.' : 'Inventory reply was not valid JSON.' };
    },
    onSuggest: async ({ messages }) => {
      const response = await api.wizardInventoryOptions({
        theme: themeLabel(state.theme_mode, state.theme_custom),
        playerName: state.player_name,
        worldInformation: inventoryGearWizardContext(state),
        optionStart: state.inventory_options_next_start || 1,
        draft: state,
      });
      const payload = response?.data || response || {};
      const batchStart = payload.optionStartUsed || state.inventory_options_next_start || 1;
      const rawContent = String(payload.text || payload.content || '').trim();
      const content = formatInventoryOptionsForChat(rawContent, batchStart)
        || formatInventoryOptionsForChat(String(payload.raw || ''), batchStart)
        || rawContent;
      state.inventory_options_raw = String(payload.raw || rawContent).trim();
      state.inventory_options_batch_start = batchStart;
      state.inventory_options_next_start = batchStart + 3;
      state.inventory_chat_history = [...clone(messages), { role: 'assistant', content }];
      await saveWizardDraftNow();
      return { content, status: 'Inventory proposals generated.' };
    },
  });
  addTab('Inventory', inventoryTab.el);

  const gearRight = document.createElement('div');
  gearRight.className = 'fugassa-gear-summary';
  const gearWeapon = preBlock('Weapon');
  const gearArmor = preBlock('Armor');
  gearRight.append(gearWeapon.wrap, gearArmor.wrap);
  const gearTab = createStructuredTab({
    kind: 'gear',
    title: 'Gear',
    intro: 'Chat about primary weapon and armor, then keep the structured weapon and armor blocks on the right.',
    draft: state,
    initialMessages: state.gear_chat_history,
    placeholder: 'Refine weapon or armor, or ask for 3 new loadouts…',
    help: HELP_TEXT.Gear,
    customRight: gearRight,
    onSend: async ({ text, messages }) => {
      state.gear_chat_history = clone(messages);
      trackChange();
      const batchStart = state.gear_options_batch_start || 1;
      const picked = inferOptionNumber(text, batchStart);
      if (picked && state.gear_options_raw) {
        const options = parseGearOptionsRaw(state.gear_options_raw);
        const idx = picked - batchStart;
        const pack = options[idx];
        if (pack?.weapon && pack?.armor) {
          state.gear_structured = { weapon: pack.weapon, armor: pack.armor };
          state.start_weapon = pack.weapon.name || '';
          state.start_armor = pack.armor.name || '';
          renderGearViews();
          trackChange();
          const summary = gearSummaryText(state.gear_structured);
          const content = [
            summary.weapon && `Weapon:\n${summary.weapon}`,
            summary.armor && `Armor:\n${summary.armor}`,
          ].filter(Boolean).join('\n\n') || 'Gear updated.';
          persistWizardAssistantTurn('gear_chat_history', messages, content);
          await saveWizardDraftNow();
          return { content, status: 'Gear loadout selected.' };
        }
      }
      const response = await api.wizardGearSummary({
        theme: themeLabel(state.theme_mode, state.theme_custom),
        playerName: state.player_name,
        worldInformation: inventoryGearWizardContext(state),
        currentDraft: JSON.stringify(selectGearCurrentDraft(text)),
        playerRequest: requestAfterOptionPick(text, inferOptionNumber(text, state.gear_options_batch_start || 1)),
        dialog: messages,
        dialogTranscript: dialogTranscript(messages),
        draft: state,
      });
      const payload = response?.data || response || {};
      const rawContent = String(payload.text || payload.content || '').trim();
      const optionText = formatGearOptionsForChat(rawContent, batchStart)
        || formatGearOptionsForChat(String(payload.raw || ''), batchStart);
      if (optionText) {
        state.gear_options_raw = String(payload.raw || rawContent).trim();
        state.gear_options_batch_start = batchStart;
        persistWizardAssistantTurn('gear_chat_history', messages, optionText);
        renderGearViews();
        trackChange();
        await saveWizardDraftNow();
        return { content: optionText, status: 'Gear proposals generated.' };
      }
      const applied = applyGearJson(rawContent);
      renderGearViews();
      trackChange();
      let content = rawContent;
      if (applied) {
        const summary = gearSummaryText(state.gear_structured);
        content = [
          summary.weapon && `Weapon:\n${summary.weapon}`,
          summary.armor && `Armor:\n${summary.armor}`,
        ].filter(Boolean).join('\n\n') || 'Gear updated.';
      }
      persistWizardAssistantTurn('gear_chat_history', messages, content);
      await saveWizardDraftNow();
      return { content, status: applied ? 'Gear updated.' : 'Gear reply was not valid JSON.' };
    },
    onSuggest: async ({ messages }) => {
      const response = await api.wizardGearOptions({
        theme: themeLabel(state.theme_mode, state.theme_custom),
        playerName: state.player_name,
        worldInformation: inventoryGearWizardContext(state),
        optionStart: state.gear_options_next_start || 1,
        draft: state,
      });
      const payload = response?.data || response || {};
      const batchStart = payload.optionStartUsed || state.gear_options_next_start || 1;
      const rawContent = String(payload.text || payload.content || '').trim();
      const content = formatGearOptionsForChat(rawContent, batchStart)
        || formatGearOptionsForChat(String(payload.raw || ''), batchStart)
        || rawContent;
      state.gear_options_raw = String(payload.raw || rawContent).trim();
      state.gear_options_batch_start = batchStart;
      state.gear_options_next_start = batchStart + 3;
      state.gear_chat_history = [...clone(messages), { role: 'assistant', content }];
      await saveWizardDraftNow();
      const status = payload.valid === false && payload.error
        ? `Gear proposals generated (${payload.error})`
        : 'Gear proposals generated.';
      return { content, status };
    },
  });
  addTab('Gear', gearTab.el);

  const openingRight = document.createElement('div');
  openingRight.className = 'fugassa-opening-summary';
  const openingHook = textAreaField('Opening text', state.opening_hook || '', 8);
  const openingTime = textAreaField('Time hint', state.opening_time_hint || '', 5);
  const syncOpeningFromFields = () => {
    state.opening_structured = {
      opening_text: state.opening_hook || '',
      time_hint: state.opening_time_hint || '',
    };
  };
  openingHook.textarea.addEventListener('input', () => {
    state.opening_hook = openingHook.textarea.value;
    syncOpeningFromFields();
    trackChange();
  });
  openingTime.textarea.addEventListener('input', () => {
    state.opening_time_hint = openingTime.textarea.value;
    syncOpeningFromFields();
    trackChange();
  });
  openingRight.append(openingHook.el, openingTime.el);
  const openingTab = createStructuredTab({
    kind: 'opening',
    title: 'Opening',
    intro: 'Chat about the opening scene and time hint, then keep the structured opening bundle on the right.',
    draft: state,
    initialMessages: state.opening_chat_history,
    placeholder: 'Refine the opening scene or time table…',
    help: HELP_TEXT.Opening,
    customRight: openingRight,
    onSend: async ({ text, messages }) => {
      state.opening_chat_history = clone(messages);
      trackChange();
      const response = await api.wizardOpeningSummary({
        theme: themeLabel(state.theme_mode, state.theme_custom),
        playerName: state.player_name,
        worldInformation: cumulativeWorldContext(state),
        currentDraft: JSON.stringify(selectOpeningCurrentDraft(text)),
        playerRequest: requestAfterOptionPick(text, inferOptionNumber(text, state.opening_options_batch_start || 1)),
        dialog: messages,
        dialogTranscript: dialogTranscript(messages),
        draft: state,
      });
      const payload = response?.data || response || {};
      const rawContent = String(payload.text || payload.content || '').trim();
      const applied = applyOpeningJson(rawContent);
      trackChange();
      const content = applied
        ? [
            state.opening_hook && `Opening:\n${state.opening_hook}`,
            state.opening_time_hint && `Time hint: ${state.opening_time_hint}`,
          ].filter(Boolean).join('\n\n') || 'Opening updated.'
        : rawContent;
      persistWizardAssistantTurn('opening_chat_history', messages, content);
      await saveWizardDraftNow();
      return { content, status: applied ? 'Opening updated.' : 'Opening reply was not valid JSON.' };
    },
    onSuggest: async ({ messages }) => {
      const response = await api.wizardOpeningOptions({
        theme: themeLabel(state.theme_mode, state.theme_custom),
        playerName: state.player_name,
        worldInformation: cumulativeWorldContext(state),
        optionStart: state.opening_options_next_start || 1,
        draft: state,
      });
      const payload = response?.data || response || {};
      const content = String(payload.text || payload.content || '').trim();
      state.opening_options_raw = String(payload.raw || '').trim();
      state.opening_options_batch_start = payload.optionStartUsed || state.opening_options_next_start || 1;
      state.opening_options_next_start = (payload.optionStartUsed || state.opening_options_next_start || 1) + 3;
      state.opening_chat_history = [...clone(messages), { role: 'assistant', content }];
      await saveWizardDraftNow();
      return { content, status: 'Opening proposals generated.' };
    },
  });
  addTab('Opening', openingTab.el);

  const summaryTab = document.createElement('section');
  summaryTab.appendChild(tabIntro('Summary', 'Review the full Fugassa draft before creating the save and switching Titan into play mode.'));
  const summaryPre = document.createElement('pre');
  summaryPre.className = 'fugassa-summary-view';
  summaryTab.appendChild(summaryPre);
  addTab('Summary', summaryTab);

  function portraitAppearanceText() {
    const rows = {};
    Object.entries(portraitControls).forEach(([key, control]) => {
      rows[key] = { i: control.select.selectedIndex, t: control.custom.value.trim() };
    });
    const lines = Object.entries(rows)
      .map(([key, row]) => {
        const options = PORTRAIT_ROW_OPTIONS[key];
        if (!options) return '';
        if (row.i === 0) return '';
        if (row.i === options.length - 1) return row.t ? `${PORTRAIT_ROW_LABELS[key]}: ${row.t}` : '';
        return `${PORTRAIT_ROW_LABELS[key]}: ${options[row.i]}`;
      })
      .filter(Boolean);
    const notes = appearanceNotes.textarea.value.trim();
    if (notes) lines.push(`Player notes: ${notes}`);
    return lines.join('\n');
  }

  function savePortraitDraft() {
    const rows = {};
    Object.entries(portraitControls).forEach(([key, control]) => {
      rows[key] = { i: control.select.selectedIndex, t: control.custom.value.trim() };
    });
    state.portrait_appearance = {
      notes: appearanceNotes.textarea.value,
      rows,
    };
    trackChange();
  }

  function renderGearViews() {
    const text = gearSummaryText(state.gear_structured);
    const hasGear = Boolean(text.weapon || text.armor);
    if (hasGear) {
      gearWeapon.pre.textContent = text.weapon || '(empty)';
      gearArmor.pre.textContent = text.armor || '(empty)';
      return;
    }
    const pending = parseGearOptionsRaw(state.gear_options_raw || '');
    if (pending.length) {
      const start = state.gear_options_batch_start || 1;
      gearWeapon.pre.textContent = `(not selected — reply ${start}, ${start + 1}, or ${start + 2} in chat)`;
      gearArmor.pre.textContent = `${pending.length} loadout proposal(s) ready. Pick one to fill weapon and armor.`;
      return;
    }
    gearWeapon.pre.textContent = '(empty)';
    gearArmor.pre.textContent = '(empty)';
  }

  async function renderSummary() {
    characterBuilder.collect();
    await characterBuilder.syncSnapshot();
    syncCurrencyHint();
    renderGearViews();
    const sheet = state.sheet_snapshot || {};
    const characterDetail = wizardCharacterSummaryLines(state, sheet, { includeSpellcastingHeader: false });
    const gearText = gearSummaryText(state.gear_structured);
    const wealthPreview = startingWealthPreview(state);
    const inventoryNotes = String(state.inventory_notes || '').trim();
    const gearInInventory = inventoryNotes && (
      (gearText.weapon && inventoryNotes.toLowerCase().includes(String(state.gear_structured?.weapon?.name || '').toLowerCase()))
      || (gearText.armor && inventoryNotes.toLowerCase().includes(String(state.gear_structured?.armor?.name || '').toLowerCase()))
    );
    const spellDcSuffix = sheet.spellcasting?.spell_save_dc
      ? ` · Spell DC ${sheet.spellcasting.spell_save_dc}`
      : '';
    summaryPre.textContent = [
      'World Information',
      state.world_information || '(empty)',
      '',
      'Backstory',
      state.character_background || '(empty)',
      '',
      'Playstyle',
      `${playstyleLabel(state.playstyle)} — framework: ${playstyleFramework(state.playstyle)}`,
      '',
      'Portrait',
      state.character_portrait_path ? `Saved: ${state.character_portrait_path}` : 'Saved: not yet',
      state.portrait_sd_prompt_text ? `Prompt: ${state.portrait_sd_prompt_text}` : '',
      portraitAppearanceText() || '(no appearance constraints yet)',
      '',
      'Character Sheet',
      `Name: ${state.player_name || 'Hero'}`,
      `Gender: ${effectiveGender(state) || '—'}`,
      `Race: ${effectiveRace(state) || '—'}`,
      `Class: ${effectiveClass(state) || '—'}`,
      effectiveSubclass(state) ? `Subclass: ${effectiveSubclass(state)}` : '',
      `Age: ${state.player_age || '—'}`,
      `Level: ${state.level || 1} | Proficiency: +${sheet.proficiency_bonus || 2}${spellDcSuffix}`,
      `HP ${sheet.hp ?? '—'} | AC ${sheet.ac_base ?? '—'} | Speed ${sheet.speed ?? '—'} ft`,
      ...(sheet.abilities ? Object.entries(sheet.abilities).map(([key, value]) => `${key.toUpperCase()} ${value}`) : []),
      ...(sheet.saves?.length ? ['', 'Saving Throws', ...sheet.saves.map((s) => `  ${s}`)] : []),
      ...(sheet.skills?.length ? ['', 'Skills', ...sheet.skills.map((s) => `  ${s}`)] : []),
      ...(characterDetail.length ? ['', ...characterDetail] : []),
      '',
      'Inventory / Gear',
      inventoryNotes || '(empty)',
      `Currency: ${(state.currency || []).join(', ')}`,
      currencyConversionHint(state.currency || []),
      ...(wealthPreview.skipped ? [] : ['', ...startingWealthSummaryLines(state)]),
      ...(!gearInInventory && gearText.weapon ? ['', 'Weapon', ...gearText.weapon.split('\n')] : []),
      ...(!gearInInventory && gearText.armor ? ['', 'Armor', ...gearText.armor.split('\n')] : []),
      '',
      'Opening',
      state.opening_hook || '(empty)',
      '',
      'Time hint',
      state.opening_time_hint || '(time hint not set)',
    ].filter(Boolean).join('\n');
  }

  function pickWorldCurrentDraft(text) {
    const picked = inferOptionNumber(text, state.world_campaign_batch_start || 1);
    if (picked && state.world_campaign_options_text) return state.world_campaign_options_text;
    return state.world_information || '';
  }

  function pickBackstoryCurrentDraft(text) {
    const picked = inferOptionNumber(text, state.backstory_options_batch_start || 1);
    if (picked && state.backstory_options_text) return state.backstory_options_text;
    return state.character_background || '';
  }

  function selectInventoryCurrentDraft(text) {
    const picked = inferOptionNumber(text, state.inventory_options_batch_start || 1);
    if (!picked || !state.inventory_options_raw) return inventoryStructuredFromDraft(state);
    try {
      const raw = JSON.parse(state.inventory_options_raw);
      const idx = picked - (state.inventory_options_batch_start || 1);
      const pack = Array.isArray(raw.options) ? raw.options[idx] : null;
      if (!pack?.items) return inventoryStructuredFromDraft(state);
      const result = { items: pack.items, currency: inventoryStructuredFromDraft(state).currency };
      if (Array.isArray(pack.currency) && pack.currency.length === 3) {
        result.currency = pack.currency.slice(0, 3);
      }
      return result;
    } catch {
      return inventoryStructuredFromDraft(state);
    }
  }

  function selectGearCurrentDraft(text) {
    const picked = inferOptionNumber(text, state.gear_options_batch_start || 1);
    if (!picked || !state.gear_options_raw) return state.gear_structured || { weapon: {}, armor: {} };
    const options = parseGearOptionsRaw(state.gear_options_raw);
    const idx = picked - (state.gear_options_batch_start || 1);
    const pack = options[idx];
    return pack?.weapon && pack?.armor
      ? { weapon: pack.weapon, armor: pack.armor }
      : (state.gear_structured || { weapon: {}, armor: {} });
  }

  function selectOpeningCurrentDraft(text) {
    const picked = inferOptionNumber(text, state.opening_options_batch_start || 1);
    if (!picked || !state.opening_options_raw) return openingStructuredFromDraft(state);
    try {
      const raw = JSON.parse(state.opening_options_raw);
      const idx = picked - (state.opening_options_batch_start || 1);
      return Array.isArray(raw.options) ? raw.options[idx] || openingStructuredFromDraft(state) : openingStructuredFromDraft(state);
    } catch {
      return openingStructuredFromDraft(state);
    }
  }

  function applyInventoryJson(text) {
    try {
      const parsed = JSON.parse(text);
      if (!Array.isArray(parsed.items)) return false;
      state.inventory_structured = { items: parsed.items };
      if (Array.isArray(parsed.currency) && parsed.currency.length === 3) {
        state.currency = parsed.currency.slice(0, 3);
      }
      state.inventory_notes = inventoryNotesFromStructured(state.inventory_structured);
      return true;
    } catch {
      return false;
    }
  }

  function applyGearJson(text) {
    const parsed = extractGearJson(text);
    if (!parsed?.weapon || !parsed?.armor) return false;
    state.gear_structured = parsed;
    state.start_weapon = parsed.weapon.name || parsed.weapon.weapon || '';
    state.start_armor = parsed.armor.name || parsed.armor.armor || '';
    return true;
  }

  function applyOpeningJson(text) {
    try {
      const parsed = JSON.parse(text);
      if (!parsed.opening_text) return false;
      state.opening_structured = {
        opening_text: parsed.opening_text || '',
        time_hint: parsed.time_hint || '',
      };
      state.opening_hook = parsed.opening_text || '';
      state.opening_time_hint = parsed.time_hint || '';
      openingHook.textarea.value = state.opening_hook;
      openingTime.textarea.value = state.opening_time_hint;
      return true;
    } catch {
      return false;
    }
  }

  function looksLikeOptionBatch(content) {
    return /(Option|Campaign)\s+1/i.test(content) && /(Option|Campaign)\s+2/i.test(content) && /(Option|Campaign)\s+3/i.test(content);
  }

  async function validateTab(index) {
    characterBuilder.collect();
    syncGuides();
    switch (index) {
      case 0: {
        const name = String(state.world_name || '').trim();
        if (!name) return 'World name is required.';
        try {
          const res = await api.checkSaveName(name);
          const sameExisting = String(state.activeSaveId || '').trim() === name;
          if (res && res.available === false && !sameExisting) return 'World name already exists.';
        } catch {
          return 'Unable to validate world name right now.';
        }
        return '';
      }
      case 1:
        if (state.theme_mode === 'Custom' && !String(state.theme_custom || '').trim()) return 'Custom genre requires a custom theme label.';
        return '';
      case 4: {
        if (!String(state.player_name || '').trim()) return 'Player name is required.';
        if (!String(state.player_age || '').trim()) return 'Age is required.';
        if (effectiveGender(state) === '' && genderNeedsCustom()) return 'Custom gender requires text.';
        if (effectiveRace(state) === '' && raceNeedsCustom()) return 'Custom race requires text.';
        if (effectiveClass(state) === '' && classNeedsCustom()) return 'Custom class requires text.';
        if (Number(state.level || 1) >= 3 && classNeedsCustom() && !String(state.player_subclass_custom || '').trim()) return 'Custom subclass is required at level 3+.';
        const sheetErr = await characterBuilder.validate();
        if (sheetErr) return sheetErr;
        return '';
      }
      case 7:
        if (!(state.currency || []).every((value) => String(value || '').trim())) return 'All three currency names are required.';
        return '';
      case 9:
        return '';
      default:
        return '';
    }
  }

  function genderNeedsCustom() {
    return Number(state.player_gender_idx || 0) === genderChoices().length - 1;
  }

  function raceNeedsCustom() {
    return Number(state.player_race_idx || 0) === raceChoices().length - 1;
  }

  function classNeedsCustom() {
    return Number(state.player_class_idx || 0) === classChoices().length - 1;
  }

  async function saveCurrentTab() {
    showFeedback('');
    characterBuilder.collect();
    syncGuides();
    const err = await validateTab(currentTab);
    if (err) {
      showFeedback(err, true);
      return;
    }
    if (currentTab < WIZARD_TAB_LABELS.length - 1) {
      const previous = state.unlocked_tab;
      state.unlocked_tab = Math.max(previous, currentTab + 1);
      await flushDraftInto(state);
      if (state.unlocked_tab > previous) {
        goToTab(currentTab + 1);
        showFeedback(`Saved ${WIZARD_TAB_LABELS[currentTab]} and unlocked ${WIZARD_TAB_LABELS[currentTab + 1]}.`);
      } else {
        showFeedback(`Saved ${WIZARD_TAB_LABELS[currentTab]}.`);
      }
    } else {
      await flushDraftInto(state);
      await renderSummary();
      showFeedback('Summary saved.');
    }
    refreshTabs();
  }

  async function createCampaign() {
    showFeedback('');
    await renderSummary();
    for (let i = 0; i < WIZARD_TAB_LABELS.length; i += 1) {
      const err = await validateTab(i);
      if (err) {
        goToTab(i);
        showFeedback(err, true);
        return;
      }
    }
    try {
      savePortraitDraft();
      state.portrait_sd_prompt_text = String(portraitPrompt?.textarea?.value || state.portrait_sd_prompt_text || '').trim();
      await flushDraftInto(state);
      characterBuilder.collect();
      syncGuides();
      const created = await api.createSaveFromWizard(state);
      const save = created?.save || created || {};
      await clearWizardDraft();
      onSessionChange?.({
        mode: 'play',
        menuScreen: 'home',
        wizardStep: WIZARD_TAB_LABELS.length - 1,
        activeSaveId: save.id || save.name || state.world_name,
        lastTool: 'fugassa',
      });
      onCreated?.(save);
    } catch (error) {
      showFeedback(error.message || String(error), true);
    }
  }

  saveBtn.addEventListener('click', saveCurrentTab);
  createBtn.addEventListener('click', createCampaign);
  backBtn.addEventListener('click', () => onBack?.());

  renderGearViews();
  refreshTabs();
  if (currentTab === WIZARD_TAB_LABELS.length - 1) void renderSummary();

  return {
    draft: state,
    refresh: refreshTabs,
  };
}

function createWorldLikeTab({ title, kind, intro, placeholder, draft, initialMessages, definitionText, setDefinitionText, suggestKey, onSend, onSuggest }) {
  const el = document.createElement('section');
  el.appendChild(tabIntro(title, intro));
  const split = document.createElement('div');
  split.className = 'fugassa-tab-split';
  const left = document.createElement('div');
  const right = document.createElement('div');
  const status = document.createElement('div');
  status.className = 'fugassa-muted';
  const helpBtn = footerButton('Help', 'ghost');
  const suggestBtn = footerButton('Suggest 3', 'ghost');
  const definition = textAreaField(title === 'Backstory' ? 'Backstory definition' : 'World information', definitionText(), 16);
  definition.textarea.addEventListener('input', () => setDefinitionText(definition.textarea.value));
  const chat = createWizardChat({
    placeholder,
    disabled: false,
    onSend: async (text, messages) => {
      try {
        const result = await onSend({ text, messages });
        if (result?.status) status.textContent = result.status;
        if (result?.content && !looksLikeOptionsForTab(result.content)) {
          definition.textarea.value = definitionText() || result.content;
        }
        return result?.content || '';
      } catch (error) {
        status.textContent = error.message || String(error);
        throw error;
      }
    },
  });
  chat.setMessages(clone(initialMessages || seedChat(kind)));
  suggestBtn.addEventListener('click', async () => {
    const label = suggestBtn.textContent;
    suggestBtn.disabled = true;
    suggestBtn.textContent = 'Thinking…';
    chat.setBusy(true);
    status.textContent = 'Waiting for the model — this can take up to a minute…';
    try {
      const messages = chat.getMessages();
      const result = await onSuggest({ messages });
      if (result?.content) {
        chat.appendAssistant(result.content);
      }
      status.textContent = result?.status || '';
    } catch (error) {
      status.textContent = error.message || String(error);
    } finally {
      suggestBtn.disabled = false;
      suggestBtn.textContent = label;
      chat.setBusy(false);
    }
  });
  helpBtn.addEventListener('click', () => uiModule.showToast?.(HELP_TEXT[title] || HELP_TEXT.WorldDefinition, 4000));
  left.append(chat.el, actionRow([suggestBtn, helpBtn]), status);
  right.append(definition.el);
  split.append(left, right);
  el.appendChild(split);
  return { el };
}

function createStructuredTab({ kind, title, intro, draft, initialMessages, placeholder, definitionLabel, definitionValue, setDefinitionValue, help, customRight, onSend, onSuggest }) {
  const el = document.createElement('section');
  el.appendChild(tabIntro(title, intro));
  const split = document.createElement('div');
  split.className = 'fugassa-tab-split';
  const left = document.createElement('div');
  const right = document.createElement('div');
  const status = document.createElement('div');
  status.className = 'fugassa-muted';
  const helpBtn = footerButton('Help', 'ghost');
  const suggestBtn = footerButton('Suggest 3', 'ghost');
  const chat = createWizardChat({
    placeholder,
    disabled: false,
    onSend: async (text, messages) => {
      try {
        const result = await onSend({ text, messages });
        if (result?.status) status.textContent = result.status;
        if (definitionValue && setDefinitionValue) {
          // keep right-side textarea in sync if present
          // eslint-disable-next-line no-use-before-define
          if (definitionField) definitionField.textarea.value = definitionValue();
        }
        return result?.content || '';
      } catch (error) {
        status.textContent = error.message || String(error);
        throw error;
      }
    },
  });
  chat.setMessages(clone(initialMessages || seedChat(kind)));
  suggestBtn.addEventListener('click', async () => {
    const label = suggestBtn.textContent;
    suggestBtn.disabled = true;
    suggestBtn.textContent = 'Thinking…';
    chat.setBusy(true);
    status.textContent = 'Waiting for the model — this can take up to a minute…';
    try {
      const result = await onSuggest({ messages: chat.getMessages() });
      if (result?.content) chat.appendAssistant(result.content);
      status.textContent = result?.status || '';
    } catch (error) {
      status.textContent = error.message || String(error);
    } finally {
      suggestBtn.disabled = false;
      suggestBtn.textContent = label;
      chat.setBusy(false);
    }
  });
  helpBtn.addEventListener('click', () => uiModule.showToast?.(help, 4000));
  left.append(chat.el, actionRow([suggestBtn, helpBtn]), status);
  let definitionField = null;
  if (definitionLabel && definitionValue && setDefinitionValue) {
    definitionField = textAreaField(definitionLabel, definitionValue(), 16);
    definitionField.textarea.addEventListener('input', () => setDefinitionValue(definitionField.textarea.value));
    right.append(definitionField.el);
  }
  if (customRight) right.appendChild(customRight);
  split.append(left, right);
  el.appendChild(split);
  return { el };
}

function seedChat(kind) {
  const copy = {
    world: 'Tell me what kind of world you want. Use Send for direct refinement and Suggest 3 for campaign proposal batches.',
    backstory: 'Describe your character background, motivations, and key history. Suggest 3 generates alternatives.',
    inventory: 'Describe starter items and currency tier names for this campaign. Primary weapon and worn armor belong in Gear.',
    gear: 'Describe the weapon and armor style you want. Suggest 3 generates gear bundles.',
    opening: 'Describe how the adventure should begin, including tone, location, and pacing.',
  };
  return [{ role: 'assistant', content: copy[kind] || '' }];
}

function looksLikeOptionsForTab(content) {
  return /(Option|Campaign)\s+1/i.test(content) && /(Option|Campaign)\s+2/i.test(content);
}

function tabIntro(title, text) {
  const wrap = document.createElement('div');
  wrap.className = 'fugassa-tab-intro';
  wrap.innerHTML = `<h3>${escapeHtml(title)}</h3><p>${escapeHtml(text)}</p>`;
  return wrap;
}

function inputField(label, value, hint = '') {
  const el = document.createElement('label');
  el.className = 'fugassa-field';
  const title = document.createElement('span');
  title.textContent = label;
  const input = document.createElement('input');
  input.type = 'text';
  input.value = value || '';
  el.append(title, input);
  if (hint) {
    const note = document.createElement('small');
    note.textContent = hint;
    el.appendChild(note);
  }
  return { el, input };
}

function selectField(label, options, valueOrLabel) {
  const el = document.createElement('label');
  el.className = 'fugassa-field';
  const title = document.createElement('span');
  title.textContent = label;
  const select = document.createElement('select');
  options.forEach((option) => {
    const opt = document.createElement('option');
    opt.value = option;
    opt.textContent = option;
    select.appendChild(opt);
  });
  const targetIndex = Math.max(0, options.findIndex((option) => option === valueOrLabel));
  select.selectedIndex = targetIndex >= 0 ? targetIndex : 0;
  el.append(title, select);
  return { el, select };
}

function textAreaField(label, value, rows = 8) {
  const el = document.createElement('label');
  el.className = 'fugassa-field';
  const title = document.createElement('span');
  title.textContent = label;
  const textarea = document.createElement('textarea');
  textarea.rows = rows;
  textarea.value = value || '';
  el.append(title, textarea);
  return { el, textarea };
}

function preBlock(label) {
  const wrap = document.createElement('div');
  wrap.className = 'fugassa-pre-block';
  const title = document.createElement('h4');
  title.textContent = label;
  const pre = document.createElement('pre');
  wrap.append(title, pre);
  return { wrap, pre };
}

function footerButton(label, variant) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `fugassa-btn fugassa-btn--${variant}`;
  button.textContent = label;
  return button;
}

function actionRow(children) {
  const row = document.createElement('div');
  row.className = 'fugassa-inline-actions';
  children.forEach((child) => row.appendChild(child));
  return row;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, Number(value || 0)));
}

function labelForValue(options, value) {
  return options.find((item) => item.value === value)?.label || options[0]?.label;
}

function parsePortraitPrompts(text) {
  const src = String(text || '').trim();
  if (!src) return { positive: '', negative: '' };
  const negIdx = src.search(/\bNegative\b/i);
  if (negIdx === -1) {
    const positiveOnly = src.replace(/^Positive\s*\n?/i, '').trim();
    return { positive: positiveOnly, negative: '' };
  }
  const positiveBlock = src.slice(0, negIdx).replace(/^Positive\s*\n?/i, '').trim();
  const negativeBlock = src.slice(negIdx).replace(/^Negative\s*\n?/i, '').trim();
  return { positive: positiveBlock, negative: negativeBlock };
}
