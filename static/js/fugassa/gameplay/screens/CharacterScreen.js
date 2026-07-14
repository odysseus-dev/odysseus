import { escapeHtml } from './InventoryScreen.js';
import { mountAssetEditor } from '../hud/AssetEditor.js';
import * as api from '../../fugassaApi.js';
import {
  buildDnd5eCatalogLookups,
  formatBonus,
  renderDescribedEntry,
  resolveFeatEntry,
  resolveFeatureEntry,
  resolveSpellEntry,
  resolveTraitEntry,
  skillModifierDisplay,
  spellSlotsDisplay,
} from '../characterSheetDisplay.js';

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'abilities', label: 'Abilities & Skills' },
  { id: 'spells', label: 'Spells' },
  { id: 'features', label: 'Features' },
  { id: 'traits', label: 'Traits & Feats' },
];

function modStr(score) {
  const mod = Math.floor((Number(score || 10) - 10) / 2);
  return formatBonus(mod);
}

function renderOverview({
  identity,
  hero,
  derived,
  volatile,
  spellcasting,
  equippedRows,
  classResources,
  classResourceSummary,
  playerTitles,
}) {
  const resourceLines = classResourceSummary?.length
    ? classResourceSummary
    : Object.entries(classResources || {})
      .filter(([, v]) => v != null && v !== '' && v !== 0 && !(Array.isArray(v) && !v.length))
      .slice(0, 8)
      .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${Array.isArray(v) ? v.join(', ') : v}`);

  const xp = Number(hero.xp ?? 0);
  const xpToNext = hero.xp_to_next != null ? Number(hero.xp_to_next) : null;
  const xpLine = xpToNext != null && Number.isFinite(xpToNext)
    ? `${xp.toLocaleString()} / ${xpToNext.toLocaleString()} XP toward next level`
    : `${xp.toLocaleString()} XP`;
  const slotLine = spellcasting?.slots
    ? spellSlotsDisplay(spellcasting.slots, volatile.spell_slots_remaining)
    : '';

  const titleLine = playerTitles?.active_display
    ? `<p class="fugassa-muted"><strong>Title</strong> ${escapeHtml(playerTitles.active_display)}</p>`
    : `<p class="fugassa-muted"><strong>Title</strong> <em>None yet</em> — earned through quests and story.</p>`;
  const otherTitles = (playerTitles?.titles || []).filter(
    (t) => t?.display && t.display !== playerTitles?.active_display,
  );
  const titleListLine = otherTitles.length
    ? `<p class="fugassa-muted">Also held: ${otherTitles.map((t) => escapeHtml(t.display)).join(', ')}</p>`
    : '';
  const titleBonusLine = playerTitles?.bonuses && (playerTitles.bonuses.social_bonus || playerTitles.bonuses.persuasion_bonus)
    ? `<p class="fugassa-muted">Title bonus: ${[
      playerTitles.bonuses.social_bonus ? `social +${playerTitles.bonuses.social_bonus}` : '',
      playerTitles.bonuses.persuasion_bonus ? `persuasion +${playerTitles.bonuses.persuasion_bonus}` : '',
    ].filter(Boolean).join(' · ')}</p>`
    : '';

  return `
    <section class="fugassa-screen-card">
      <h3>${escapeHtml(identity.name || hero.name || 'Hero')}</h3>
      <p class="fugassa-muted">${escapeHtml(identity.race || hero.race || '')} ${escapeHtml(identity.subrace || '')} ${escapeHtml(identity.character_class || hero.character_class || '')}${identity.subclass ? ` (${escapeHtml(identity.subclass)})` : ''} · Level ${Number(identity.level || hero.level || 1)}</p>
      <p>${escapeHtml(identity.background || hero.background || '')}</p>
      ${titleLine}
      ${titleListLine}
      ${titleBonusLine}
      <p class="fugassa-muted">HP ${Number(volatile.hp_current ?? hero.hp ?? 0)}/${hero.max_hp != null ? Number(hero.max_hp) : '—'} · AC ${Number(hero.ac ?? derived.ac_base ?? 12)} · Speed ${Number(derived.speed || 30)} ft · PB +${Number(derived.proficiency_bonus || 2)}</p>
      <p class="fugassa-muted"><strong>Experience</strong> ${escapeHtml(xpLine)}</p>
      ${slotLine ? `<p class="fugassa-muted"><strong>Spell slots</strong> ${escapeHtml(slotLine)}</p>` : ''}
      <p class="fugassa-muted">Passive Perception ${Number(derived.passive_perception || 10)} · Location ${escapeHtml(volatile.location || '—')}</p>
      ${resourceLines.length ? `<ul class="fugassa-char-list">${resourceLines.map((line) => `<li>${escapeHtml(String(line))}</li>`).join('')}</ul>` : ''}
    </section>
    <section class="fugassa-screen-card">
      <h3>Equipment</h3>
      <div class="fugassa-inv-equip">${equippedRows || '<p class="fugassa-muted">Nothing equipped yet.</p>'}</div>
    </section>
  `;
}

function renderAbilities({ abilities, skills, saves, derived }) {
  const abilityRows = Object.entries(abilities)
    .map(([k, v]) => `<tr><td>${escapeHtml(k)}</td><td>${Number(v)}</td><td>${modStr(v)}</td></tr>`)
    .join('');
  const skillRows = (skills || [])
    .map((s) => `<tr><td>${s.proficient ? '●' : '○'}${s.expertise ? '◆' : ''} ${escapeHtml(s.name || s.id || '')}</td><td>${escapeHtml(skillModifierDisplay(s))}</td></tr>`)
    .join('');
  const saveRows = (saves || [])
    .map((s) => `<tr><td>${s.proficient ? '●' : '○'} ${escapeHtml(String(s.ability || '').toUpperCase())}</td><td>${escapeHtml(s.modifier_str || formatBonus(s.modifier))}</td></tr>`)
    .join('');
  return `
    <section class="fugassa-screen-card">
      <h3>Abilities</h3>
      <table class="fugassa-simple-table"><thead><tr><th>Ability</th><th>Score</th><th>Mod</th></tr></thead><tbody>${abilityRows || '<tr><td colspan="3" class="fugassa-muted">No ability data</td></tr>'}</tbody></table>
    </section>
    <section class="fugassa-screen-card">
      <h3>Saving Throws</h3>
      <table class="fugassa-simple-table"><tbody>${saveRows || '<tr><td colspan="2" class="fugassa-muted">No saving throw data</td></tr>'}</tbody></table>
    </section>
    <section class="fugassa-screen-card">
      <h3>Skills</h3>
      <table class="fugassa-simple-table"><tbody>${skillRows || '<tr><td colspan="2" class="fugassa-muted">No skill data</td></tr>'}</tbody></table>
      <p class="fugassa-muted">Passive Perception ${Number(derived.passive_perception || 10)}</p>
    </section>
  `;
}

function renderSpells({ spellcasting, derived, volatile, lookups }) {
  if (!spellcasting) {
    return '<section class="fugassa-screen-card"><p class="fugassa-muted">This character has no spellcasting.</p></section>';
  }
  const cantrips = (spellcasting.cantrips || [])
    .map((id) => {
      const entry = resolveSpellEntry(id, lookups);
      return renderDescribedEntry({ name: entry.name, description: entry.description, meta: 'cantrip' });
    })
    .join('');
  const spells = (spellcasting.spells_known || [])
    .map((id) => {
      const entry = resolveSpellEntry(id, lookups);
      return renderDescribedEntry({ name: entry.name, description: entry.description });
    })
    .join('');
  const slotLine = spellSlotsDisplay(spellcasting.slots, volatile.spell_slots_remaining);
  return `
    <section class="fugassa-screen-card">
      <h3>Spellcasting</h3>
      <p class="fugassa-muted">${escapeHtml(spellcasting.model || 'prepared')} · ${escapeHtml(spellcasting.ability || '').toUpperCase()} · DC ${Number(spellcasting.save_dc || derived.spell_save_dc || 0)} · Attack +${Number(spellcasting.attack_bonus || derived.spell_attack_bonus || 0)}</p>
      ${slotLine ? `<p class="fugassa-muted"><strong>Slots</strong> ${escapeHtml(slotLine)}</p>` : ''}
      <h4>Cantrips</h4>
      <ul class="fugassa-char-list">${cantrips || '<li class="fugassa-muted">None</li>'}</ul>
      <h4>Spells</h4>
      <ul class="fugassa-char-list">${spells || '<li class="fugassa-muted">None selected</li>'}</ul>
    </section>
  `;
}

function renderFeatures({ features, classMechanics, classResources, lookups }) {
  const mechRows = (classMechanics || [])
    .map((m) => {
      const names = (m.values || []).map((v) => v.name).join(', ');
      return `<li><strong>${escapeHtml(m.label || m.id || '')}</strong>: ${escapeHtml(names)}</li>`;
    })
    .join('');
  const resourceRows = Object.entries(classResources || {})
    .filter(([, v]) => v != null && v !== '' && v !== 0 && !(Array.isArray(v) && !v.length))
    .map(([k, v]) => {
      const label = k.replace(/_/g, ' ');
      const val = Array.isArray(v) ? v.join(', ') : String(v);
      return `<li><span class="fugassa-muted">${escapeHtml(label)}:</span> ${escapeHtml(val)}</li>`;
    })
    .join('');
  const rows = (features || [])
    .map((f) => {
      const entry = resolveFeatureEntry(f, lookups);
      const meta = `${entry.source || 'class'}${entry.level ? ` L${entry.level}` : ''}`;
      return renderDescribedEntry({ name: entry.name, description: entry.description, meta });
    })
    .join('');
  return `
    <section class="fugassa-screen-card"><h3>Class & Subclass Features</h3><ul class="fugassa-char-list">${rows || '<li class="fugassa-muted">None</li>'}</ul></section>
    ${mechRows ? `<section class="fugassa-screen-card"><h3>Class mechanics</h3><ul class="fugassa-char-list">${mechRows}</ul></section>` : ''}
    ${resourceRows ? `<section class="fugassa-screen-card"><h3>Class resources</h3><ul class="fugassa-char-list">${resourceRows}</ul></section>` : ''}
  `;
}

function renderTraits({ traits, feats, lookups }) {
  const traitRows = (traits || [])
    .map((t) => {
      const entry = resolveTraitEntry(t, lookups);
      return renderDescribedEntry({ name: entry.name, description: entry.description, meta: entry.source || 'race' });
    })
    .join('');
  const featRows = (feats || [])
    .map((f) => {
      const entry = resolveFeatEntry(f, lookups);
      const meta = entry.level ? `L${entry.level}` : '';
      return renderDescribedEntry({ name: entry.name, description: entry.description, meta });
    })
    .join('');
  return `
    <section class="fugassa-screen-card"><h3>Racial Traits</h3><ul class="fugassa-char-list">${traitRows || '<li class="fugassa-muted">None</li>'}</ul></section>
    <section class="fugassa-screen-card"><h3>Feats</h3><ul class="fugassa-char-list">${featRows || '<li class="fugassa-muted">None</li>'}</ul></section>
  `;
}

function renderCompanionOverview(member, npcDetail, saveId) {
  const rel = member?.relationship || {};
  const backstory = member?.backstory_summary || npcDetail?.backstory_summary || npcDetail?.npc?.backstory_summary || '';
  const race = member?.race || npcDetail?.race || npcDetail?.npc?.race || '';
  const role = member?.character_class || npcDetail?.class_role || npcDetail?.npc?.class_role || 'companion';
  const relLine = rel.summary || rel.attitude || '';
  const portraitFile = member?.portrait_file || npcDetail?.portrait_path || npcDetail?.npc?.portrait_path;
  const portraitUrl = portraitFile && saveId
    ? `/api/fugassa/saves/${encodeURIComponent(saveId)}/assets/${encodeURIComponent(portraitFile)}`
    : null;
  const portraitBlock = portraitUrl
    ? `<img class="fugassa-char-companion-portrait" src="${portraitUrl}" alt="" />`
    : `<div class="fugassa-char-companion-portrait fugassa-char-companion-portrait--placeholder">${escapeHtml((member?.name || 'C').charAt(0).toUpperCase())}</div>`;
  return `
    <section class="fugassa-screen-card fugassa-char-companion-overview">
      <div class="fugassa-char-companion-head">
        ${portraitBlock}
        <div>
          <h3>${escapeHtml(member?.name || 'Companion')}</h3>
          <p class="fugassa-muted">${escapeHtml(race)} ${escapeHtml(role)} · HP ${Number(member?.hp ?? 0)}/${Number(member?.max_hp ?? 0)} · AC ${Number(member?.ac ?? 12)}</p>
        </div>
      </div>
      ${relLine ? `<p><strong>Relationship</strong> ${escapeHtml(relLine)}${rel.trust != null ? ` (trust ${rel.trust})` : ''}</p>` : ''}
      ${backstory ? `<p>${escapeHtml(backstory)}</p>` : '<p class="fugassa-muted">No backstory on file yet.</p>'}
    </section>
  `;
}

export async function mountCharacterScreen(root, {
  state,
  saveId,
  onClose,
  onOpenInventory,
  onOpenLevelUp,
  initialMemberIndex = 0,
}) {
  root.className = 'fugassa-screen fugassa-screen--character';
  const party = state?.party || [];
  let memberIndex = Math.max(0, Math.min(initialMemberIndex, Math.max(0, party.length - 1)));
  let npcDetailCache = null;

  async function loadNpcDetail(member) {
    const npcId = member?.npc_id;
    if (!npcId || !saveId) return null;
    try {
      return await api.getNpcDetail(saveId, npcId);
    } catch {
      return null;
    }
  }

  const hero = party[0] || {};
  const sheet = state?.character_sheet || {};
  const stable = sheet.stable_sheet || {};
  const identity = stable.identity || {};
  const abilities = stable.abilities || {};
  const derived = sheet.derived || {};
  const volatile = sheet.volatile_state || {};
  const computed = sheet.computed || {};
  const equipped = (state?.inventory?.equipped || {})[hero.name || identity.name] || {};
  const equippedRows = Object.entries(equipped)
    .map(([slot, item]) => `<div><span class="fugassa-muted">${escapeHtml(slot.replace('_', ' '))}</span><strong>${escapeHtml(item?.name || '—')}</strong></div>`)
    .join('');

  let lookups = buildDnd5eCatalogLookups();
  try {
    const [spells, features, traits, feats] = await Promise.all([
      api.getDnd5e('spells'),
      api.getDnd5e('features'),
      api.getDnd5e('traits'),
      api.getDnd5e('feats'),
    ]);
    lookups = buildDnd5eCatalogLookups({
      spells: Array.isArray(spells) ? spells : [],
      features: Array.isArray(features) ? features : [],
      traits: Array.isArray(traits) ? traits : [],
      feats: Array.isArray(feats) ? feats : [],
    });
  } catch {
    // SRD lookup is best-effort — names still fall back to id normalization.
  }

  const ctx = {
    identity,
    hero,
    derived,
    volatile,
    abilities,
    skills: stable.skills || [],
    saves: stable.saving_throws || [],
    spellcasting: stable.spellcasting,
    features: stable.features || [],
    classMechanics: stable.class_mechanics || [],
    classResources: stable.class_resources || computed.class_resources || {},
    classResourceSummary: computed.class_resource_summary || [],
    traits: stable.traits || [],
    feats: stable.feats || [],
    equippedRows,
    lookups,
    playerTitles: state?.player_titles || null,
  };

  const currentLevel = Number(identity.level || hero.level || 1);
  const xpToNextLevel = hero.xp_to_next != null ? Number(hero.xp_to_next) : null;
  const canLevelUp = currentLevel < 20
    && xpToNextLevel != null
    && Number.isFinite(xpToNextLevel)
    && Number(hero.xp ?? 0) >= xpToNextLevel;

  function portraitPromptFromState(gameState) {
    const fromSql = String(gameState?.portrait_prompt || '').trim();
    if (fromSql) return fromSql;
    const snap = gameState?.wizard_draft_snapshot;
    const appearance = snap?.portrait_appearance;
    if (appearance && typeof appearance === 'object') {
      const fromAppearance = String(appearance.positive_prompt || appearance.prompt || '').trim();
      if (fromAppearance) return fromAppearance;
    }
    const raw = String(gameState?.portrait_sd_prompt_text || snap?.portrait_sd_prompt_text || '').trim();
    if (!raw) return '';
    const negIdx = raw.search(/\bNegative\b/i);
    const positiveBlock = negIdx >= 0 ? raw.slice(0, negIdx) : raw;
    return positiveBlock.replace(/^Positive\s*\n?/i, '').trim();
  }

  let activeTab = 'overview';

  function renderTab() {
    const panel = root.querySelector('[data-char-panel]');
    if (!panel) return;
    const member = party[memberIndex] || hero;
    const isCompanion = memberIndex > 0;
    if (isCompanion) {
      panel.innerHTML = renderCompanionOverview(member, npcDetailCache, saveId);
      root.querySelector('.fugassa-char-tabs')?.classList.add('fugassa-hidden');
      root.querySelector('[data-level-up]')?.classList.add('fugassa-hidden');
      return;
    }
    root.querySelector('.fugassa-char-tabs')?.classList.remove('fugassa-hidden');
    root.querySelector('[data-level-up]')?.classList.remove('fugassa-hidden');
    switch (activeTab) {
      case 'abilities':
        panel.innerHTML = renderAbilities(ctx);
        break;
      case 'spells':
        panel.innerHTML = renderSpells(ctx);
        break;
      case 'features':
        panel.innerHTML = renderFeatures(ctx);
        break;
      case 'traits':
        panel.innerHTML = renderTraits(ctx);
        break;
      default:
        panel.innerHTML = renderOverview(ctx);
    }
    root.querySelectorAll('[data-char-tab]').forEach((btn) => {
      btn.classList.toggle('is-active', btn.dataset.charTab === activeTab);
    });
  }

  root.innerHTML = `
    <header class="fugassa-screen-head">
      <h2>Character</h2>
      <button type="button" class="fugassa-btn fugassa-btn--ghost fugassa-btn--sm" data-close>Back to game</button>
    </header>
    ${party.length > 1 ? `<nav class="fugassa-char-party-tabs" data-party-tabs></nav>` : ''}
    <nav class="fugassa-char-tabs">
      ${TABS.map((t) => `<button type="button" class="fugassa-btn fugassa-btn--ghost fugassa-btn--sm" data-char-tab="${t.id}">${t.label}</button>`).join('')}
    </nav>
    <div class="fugassa-screen-body fugassa-char-layout">
      <div data-char-panel></div>
      <section class="fugassa-screen-card" data-asset-wrap></section>
    </div>
    <footer class="fugassa-screen-foot">
      <button type="button" class="fugassa-btn fugassa-btn--ghost fugassa-btn--sm" data-open-inventory>Open Inventory</button>
      ${currentLevel < 20 ? `<button type="button" class="fugassa-btn fugassa-btn--ghost fugassa-btn--sm" data-level-up${canLevelUp ? '' : ' disabled'} title="${canLevelUp ? 'Choose level-up options' : 'Earn more XP before leveling up'}">Level up</button>` : ''}
    </footer>
  `;

  root.querySelector('[data-close]').addEventListener('click', () => onClose?.());
  root.querySelector('[data-open-inventory]').addEventListener('click', () => onOpenInventory?.());
  const levelBtn = root.querySelector('[data-level-up]');
  if (levelBtn) {
    levelBtn.addEventListener('click', () => {
      if (levelBtn.disabled) return;
      onOpenLevelUp?.();
    });
  }
  root.querySelectorAll('[data-char-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      activeTab = btn.dataset.charTab || 'overview';
      renderTab();
    });
  });

  const partyTabs = root.querySelector('[data-party-tabs]');
  if (partyTabs && party.length > 1) {
    party.forEach((member, idx) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = `fugassa-btn fugassa-btn--sm ${idx === memberIndex ? 'fugassa-btn--primary' : 'fugassa-btn--ghost'}`;
      b.textContent = member.name || (idx === 0 ? 'Hero' : `Companion ${idx}`);
      b.addEventListener('click', async () => {
        memberIndex = idx;
        partyTabs.querySelectorAll('button').forEach((btn, i) => {
          btn.className = `fugassa-btn fugassa-btn--sm ${i === memberIndex ? 'fugassa-btn--primary' : 'fugassa-btn--ghost'}`;
        });
        npcDetailCache = memberIndex > 0 ? await loadNpcDetail(party[memberIndex]) : null;
        activeTab = 'overview';
        renderTab();
        const assetWrap = root.querySelector('[data-asset-wrap]');
        if (assetWrap) assetWrap.innerHTML = '';
        if (memberIndex === 0 && state?.player_character_id) {
          await mountAssetEditor(assetWrap, {
            saveId,
            entityType: 'player_character',
            entityId: state.player_character_id,
            title: 'Portrait',
            defaultPositivePrompt: portraitPromptFromState(state),
          });
        }
      });
      partyTabs.appendChild(b);
    });
  }

  if (memberIndex > 0) {
    npcDetailCache = await loadNpcDetail(party[memberIndex]);
  }

  renderTab();

  if (memberIndex === 0 && state?.player_character_id) {
    await mountAssetEditor(root.querySelector('[data-asset-wrap]'), {
      saveId,
      entityType: 'player_character',
      entityId: state.player_character_id,
      title: 'Portrait',
      defaultPositivePrompt: portraitPromptFromState(state),
    });
  }
}
