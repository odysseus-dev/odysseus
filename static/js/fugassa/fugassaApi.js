/**
 * Fugassa API client — mirrors Fugassa-II backend/server.js endpoints.
 */

import { rulesContext } from './wizard/helpers.js';

const API = '/api/fugassa';

async function _json(method, path, body) {
  const opts = {
    method,
    credentials: 'same-origin',
    headers: body != null ? { 'Content-Type': 'application/json' } : undefined,
    body: body != null ? JSON.stringify(body) : undefined,
  };
  const res = await fetch(`${API}${path}`, opts);
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    let msg = data?.detail?.message || data?.detail || data?.error || data?.message || `HTTP ${res.status}`;
    if (typeof msg === 'object' && msg !== null) {
      msg = msg.message || JSON.stringify(msg);
    }
    const err = new Error(String(msg));
    err.status = res.status;
    err.code = data?.detail?.code;
    throw err;
  }
  return data;
}

export function listSaves() {
  return _json('GET', '/saves');
}

export function getSave(id) {
  return _json('GET', `/saves/${encodeURIComponent(id)}`);
}

export function createSave(name, theme = 'fantasy') {
  return _json('POST', '/saves', { name, theme });
}

export function createSaveFromWizard(draft) {
  return _json('POST', '/saves/from-wizard', draft);
}

export function renameSave(id, name) {
  return _json('PATCH', `/saves/${encodeURIComponent(id)}`, { name });
}

export function deleteSave(id) {
  return _json('DELETE', `/saves/${encodeURIComponent(id)}`);
}

export function checkSaveName(name) {
  return _json('GET', `/saves/check?name=${encodeURIComponent(name)}`);
}

export function loadConfig() {
  return _json('GET', '/config');
}

export function patchConfig(body) {
  return _json('PATCH', '/config', body);
}

export function getImageStyles() {
  return _json('GET', '/image-styles');
}

export function loadWizardDraft() {
  return _json('GET', '/wizard-draft');
}

export function patchWizardDraft(body) {
  return _json('PATCH', '/wizard-draft', body);
}

export function clearWizardDraft() {
  return _json('DELETE', '/wizard-draft');
}

export function syncSessionManifest(body) {
  return _json('PUT', '/session-manifest', body);
}

export function loadSessionManifest() {
  return _json('GET', '/session-manifest');
}

export function getDnd5e(resource) {
  return _json('GET', `/dnd5e/${encodeURIComponent(resource)}`);
}

export function computeCharacterSheet(draft) {
  return _json('POST', '/character-sheet/compute', draft || {});
}

export function validateCharacterSheet(draft) {
  return _json('POST', '/character-sheet/validate', draft || {});
}

export function generateHomebrewSheet(draft) {
  return _json('POST', '/wizard/character/homebrew', draft || {});
}

function _rules(draft) {
  return rulesContext(draft || {});
}

export function wizardWorldOptions({ theme, campaignLength, playerRequest, optionStart, draft }) {
  return _json('POST', '/wizard/world/options', {
    theme,
    campaignLength,
    playerRequest: playerRequest || '',
    optionStart: optionStart || 1,
    rulesContext: _rules(draft || {}),
  });
}

export function wizardWorldSummary({ theme, campaignLength, currentDraft, playerRequest, dialog, dialogTranscript, draft }) {
  return _json('POST', '/wizard/world/summary', {
    theme,
    campaignLength,
    currentDraft: currentDraft || '',
    playerRequest: playerRequest || '',
    dialog: dialog || [],
    dialogTranscript: dialogTranscript || '',
    rulesContext: _rules(draft || {}),
  });
}

export function wizardBackstoryOptions({ theme, playerName, worldInformation, playerRequest, characterProfile, optionStart, draft }) {
  return _json('POST', '/wizard/backstory/options', {
    theme,
    playerName,
    worldInformation,
    playerRequest: playerRequest || '',
    characterProfile: characterProfile || '',
    optionStart: optionStart || 1,
    rulesContext: _rules(draft || {}),
  });
}

export function wizardBackstorySummary({ theme, playerName, currentDraft, playerRequest, worldInformation, characterProfile, dialog, dialogTranscript, draft }) {
  return _json('POST', '/wizard/backstory/summary', {
    theme,
    playerName,
    currentDraft: currentDraft || '',
    playerRequest: playerRequest || '',
    worldInformation: worldInformation || '',
    characterProfile: characterProfile || '',
    dialog: dialog || [],
    dialogTranscript: dialogTranscript || '',
    rulesContext: _rules(draft || {}),
  });
}

export function wizardInventoryOptions({ theme, playerName, worldInformation, optionStart, draft }) {
  return _json('POST', '/wizard/inventory/options', {
    theme,
    playerName,
    worldInformation,
    optionStart: optionStart || 1,
    rulesContext: _rules(draft || {}),
  });
}

export function wizardInventorySummary({ theme, playerName, worldInformation, currentDraft, playerRequest, dialog, dialogTranscript, draft }) {
  return _json('POST', '/wizard/inventory/summary', {
    theme,
    playerName,
    worldInformation,
    currentDraft: currentDraft || '',
    playerRequest: playerRequest || '',
    dialog: dialog || [],
    dialogTranscript: dialogTranscript || '',
    rulesContext: _rules(draft || {}),
  });
}

export function wizardGearOptions({ theme, playerName, worldInformation, optionStart, draft }) {
  return _json('POST', '/wizard/gear/options', {
    theme,
    playerName,
    worldInformation,
    optionStart: optionStart || 1,
    rulesContext: _rules(draft || {}),
  });
}

export function wizardGearSummary({ theme, playerName, worldInformation, currentDraft, playerRequest, dialog, dialogTranscript, draft }) {
  return _json('POST', '/wizard/gear/summary', {
    theme,
    playerName,
    worldInformation,
    currentDraft: currentDraft || '',
    playerRequest: playerRequest || '',
    dialog: dialog || [],
    dialogTranscript: dialogTranscript || '',
    rulesContext: _rules(draft || {}),
  });
}

export function wizardOpeningOptions({ theme, playerName, worldInformation, optionStart, draft }) {
  return _json('POST', '/wizard/opening/options', {
    theme,
    playerName,
    worldInformation,
    optionStart: optionStart || 1,
    rulesContext: _rules(draft || {}),
  });
}

export function wizardOpeningSummary({ theme, playerName, worldInformation, currentDraft, playerRequest, dialog, dialogTranscript, draft }) {
  return _json('POST', '/wizard/opening/summary', {
    theme,
    playerName,
    worldInformation,
    currentDraft: currentDraft || '',
    playerRequest: playerRequest || '',
    dialog: dialog || [],
    dialogTranscript: dialogTranscript || '',
    rulesContext: _rules(draft || {}),
  });
}

export function wizardPortraitPrompts({ theme, playerName, backstory, worldInformation, styleOverride, characterProfile, appearanceVisual }) {
  return _json('POST', '/wizard/portrait/prompts', {
    theme,
    playerName,
    backstory,
    worldInformation,
    styleOverride: styleOverride || '',
    characterProfile: characterProfile || '',
    appearanceVisual: appearanceVisual || '',
  });
}

export function wizardPortraitGenerate({ positive_prompt, negative_prompt, theme, style_override }) {
  return _json('POST', '/wizard/portrait/generate', {
    positive_prompt: positive_prompt || '',
    negative_prompt: negative_prompt || '',
    theme: theme || 'Fantasy',
    style_override: style_override || '',
  });
}

export function wizardPortraitStagingUrl(cacheBust) {
  return `${API}/wizard/portrait/staging?v=${encodeURIComponent(cacheBust || Date.now())}`;
}

export function getGameState(saveId) {
  return _json('GET', `/saves/${encodeURIComponent(saveId)}/game`);
}

export function getGameJobs(saveId, optionsOrBatchId = null) {
  const opts =
    typeof optionsOrBatchId === 'string' || optionsOrBatchId == null
      ? { batchId: optionsOrBatchId || null }
      : optionsOrBatchId;
  const { batchId = null, status = null, jobType = null, turnNumber = null, limit = 30 } = opts;
  const params = new URLSearchParams();
  if (batchId) params.set('batch_id', batchId);
  if (status) params.set('status', status);
  if (jobType) params.set('job_type', jobType);
  if (turnNumber != null) params.set('turn_number', String(turnNumber));
  if (limit != null) params.set('limit', String(limit));
  const q = params.toString();
  return _json('GET', `/saves/${encodeURIComponent(saveId)}/game/jobs${q ? `?${q}` : ''}`);
}

export function getGameJobDetail(saveId, jobId) {
  return _json('GET', `/saves/${encodeURIComponent(saveId)}/game/jobs/${encodeURIComponent(jobId)}`);
}

export function retryGameJob(saveId, jobId) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/game/jobs/${encodeURIComponent(jobId)}/retry`);
}

export function bootstrapGame(saveId) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/game/bootstrap`);
}

export function submitGameAction(saveId, text) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/game/submit`, { text });
}

export function undoGameTurn(saveId) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/game/undo`);
}

export function getGameMap(saveId) {
  return _json('GET', `/saves/${encodeURIComponent(saveId)}/game/map`);
}

export function travelGame(saveId, { x, y, z, mode }) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/game/travel`, { x, y, z, mode: mode || 'walk' });
}

export function moveGame(saveId, direction) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/game/move`, { direction });
}

export function startCombat(saveId) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/game/combat/start`);
}

export function endCombat(saveId) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/game/combat/end`);
}

export function getInvestigateOptions(saveId) {
  return _json('GET', `/saves/${encodeURIComponent(saveId)}/game/investigate/options`);
}

export function investigateGame(saveId, { searchTypes = [], durationMinutes = 30 } = {}) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/game/investigate`, {
    search_types: searchTypes,
    duration_minutes: durationMinutes,
  });
}

export function pickupLoot(saveId, { items = [] } = {}) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/game/loot/pickup`, { items });
}

export function getGamePause(saveId) {
  return _json('GET', `/saves/${encodeURIComponent(saveId)}/game/pause`);
}

export function patchGamePause(saveId, body) {
  return _json('PATCH', `/saves/${encodeURIComponent(saveId)}/game/pause`, body);
}

export function patchGameInventory(saveId, inventory) {
  return _json('PATCH', `/saves/${encodeURIComponent(saveId)}/game/inventory`, { inventory });
}

export function getEquipmentSlots(saveId) {
  return _json('GET', `/saves/${encodeURIComponent(saveId)}/game/equipment-slots`);
}

export function equipItem(saveId, { heroName, itemName, slot }) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/game/equip`, {
    hero_name: heroName,
    item_name: itemName,
    slot,
  });
}

export function unequipItem(saveId, { heroName, slot }) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/game/unequip`, {
    hero_name: heroName,
    slot,
  });
}

export function getCraftingProfessions(saveId, heroName) {
  const qs = new URLSearchParams({ hero_name: heroName }).toString();
  return _json('GET', `/saves/${encodeURIComponent(saveId)}/game/crafting/professions?${qs}`);
}

export function getCraftingBlueprints(saveId, heroName) {
  const qs = new URLSearchParams({ hero_name: heroName }).toString();
  return _json('GET', `/saves/${encodeURIComponent(saveId)}/game/crafting/blueprints?${qs}`);
}

export function craftItem(saveId, { heroName, recipeCode }) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/game/crafting/craft`, {
    hero_name: heroName,
    recipe_code: recipeCode,
  });
}

export function inventBlueprint(saveId, { heroName, profession, tier, description }) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/game/crafting/invent`, {
    hero_name: heroName,
    profession,
    tier,
    description,
  });
}

export function reverseEngineerItem(saveId, { heroName, profession, itemName }) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/game/crafting/reverse-engineer`, {
    hero_name: heroName,
    profession,
    item_name: itemName,
  });
}

export function getNpcDetail(saveId, npcId) {
  return _json('GET', `/saves/${encodeURIComponent(saveId)}/npcs/${encodeURIComponent(npcId)}`);
}

export function getDebugSnapshot(saveId) {
  return _json('GET', `/saves/${encodeURIComponent(saveId)}/debug`);
}

export function getGameProperties(saveId) {
  return _json('GET', `/saves/${encodeURIComponent(saveId)}/game/properties`);
}

export function visitGameProperty(saveId, propertyCode) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/game/properties/visit`, {
    property_code: propertyCode,
  });
}

export function visitGamePropertyRoom(saveId, propertyCode, roomLocationId) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/game/properties/visit-room`, {
    property_code: propertyCode,
    room_location_id: roomLocationId,
  });
}

export function setActiveResidence(saveId, propertyCode) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/game/properties/active-residence`, {
    property_code: propertyCode,
  });
}

export function getAssetsMeta(saveId, params = {}) {
  const qs = new URLSearchParams(params).toString();
  return _json('GET', `/saves/${encodeURIComponent(saveId)}/assets-meta${qs ? `?${qs}` : ''}`);
}

export function patchAssetPrompt(saveId, assetId, { positive_prompt, negative_prompt } = {}) {
  return _json('PATCH', `/saves/${encodeURIComponent(saveId)}/assets/${encodeURIComponent(assetId)}/prompt`, {
    positive_prompt,
    negative_prompt,
  });
}

export function patchNpcPortraitPrompt(saveId, npcId, { positive_prompt, negative_prompt } = {}) {
  return _json('PATCH', `/saves/${encodeURIComponent(saveId)}/npcs/${encodeURIComponent(npcId)}/portrait-prompt`, {
    positive_prompt,
    negative_prompt,
  });
}

export function patchPlayerPortraitPrompt(saveId, { positive_prompt, negative_prompt } = {}) {
  return _json('PATCH', `/saves/${encodeURIComponent(saveId)}/player-character/portrait-prompt`, {
    positive_prompt,
    negative_prompt,
  });
}

export function regenerateAsset(saveId, assetId, { positive_prompt, negative_prompt, use_auto_prompt = false } = {}) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/assets/${encodeURIComponent(assetId)}/regenerate`, {
    positive_prompt,
    negative_prompt,
    use_auto_prompt,
  });
}

export function generateAsset(saveId, { entityType, entityId, assetType = 'scene', positivePrompt, negativePrompt, useAutoPrompt = true } = {}) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/assets/generate`, {
    entity_type: entityType,
    entity_id: entityId,
    asset_type: assetType,
    positive_prompt: positivePrompt,
    negative_prompt: negativePrompt,
    use_auto_prompt: useAutoPrompt,
  });
}

export function getChatSceneAssets(saveId) {
  return _json('GET', `/saves/${encodeURIComponent(saveId)}/game/chat-scene-assets`);
}

export function getGameSummary(saveId) {
  return _json('GET', `/saves/${encodeURIComponent(saveId)}/game/summary`);
}

export function previewLevelUp(saveId, targetLevel) {
  return _json('GET', `/saves/${encodeURIComponent(saveId)}/game/level-up/preview?level=${encodeURIComponent(targetLevel)}`);
}

export function applyLevelUp(saveId, body) {
  return _json('POST', `/saves/${encodeURIComponent(saveId)}/game/level-up/apply`, body || {});
}
