import uiModule from '../../ui.js';
import * as api from '../fugassaApi.js';
import { mountWizardShell } from '../wizard/WizardShell.js';
import { isDraftResumable, loadDraft, clearDraft } from '../wizard/draft.js';
import { fugassaMenuBrandHtml } from '../../titanBrand.js';

export async function mountMenuCenterStage(root, { session, onHide, onSessionChange }) {
  root.innerHTML = '';
  root.className = 'fugassa-main fugassa-menu-root';

  const shell = document.createElement('div');
  shell.className = 'fugassa-menu-stage';
  root.appendChild(shell);

  const body = document.createElement('div');
  body.className = 'fugassa-menu-body';
  shell.appendChild(body);

  const persistSession = (partial) => {
    onSessionChange?.(partial);
  };

  const goHome = () => {
    persistSession({ mode: 'menu', menuScreen: 'home', lastTool: 'fugassa' });
    showHome();
  };

  const showHome = async () => {
    body.className = 'fugassa-menu-body fugassa-menu-body--home';
    body.innerHTML = '';
    const home = document.createElement('div');
    home.className = 'fugassa-menu-home';
    const brand = document.createElement('div');
    brand.className = 'fugassa-menu-brand';
    brand.innerHTML = fugassaMenuBrandHtml();
    const actions = document.createElement('div');
    actions.className = 'fugassa-menu-actions';
    const newBtn = menuBtn('New Game');
    const continueBtn = menuBtn('Continue');
    const settingsBtn = menuBtn('Settings');
    const exitBtn = menuBtn('Exit');
    actions.append(newBtn, continueBtn, settingsBtn, exitBtn);
    home.append(brand, actions);
    body.appendChild(home);

    newBtn.addEventListener('click', async () => {
      const draft = await loadDraft().catch(() => null);
      if (draft && isDraftResumable(draft)) {
        showResumeDraftDialog(draft);
        return;
      }
      showWizard(draft);
    });
    continueBtn.addEventListener('click', () => {
      persistSession({ mode: 'menu', menuScreen: 'continue', lastTool: 'fugassa' });
      openContinue();
    });
    settingsBtn.addEventListener('click', () => {
      persistSession({ mode: 'menu', menuScreen: 'settings', lastTool: 'fugassa' });
      openSettings();
    });
    exitBtn.addEventListener('click', () => onHide?.());
  };

  const showWizard = (draft) => {
    persistSession({ mode: 'menu', menuScreen: 'wizard', wizardStep: session?.wizardStep || 0, lastTool: 'fugassa' });
    body.className = 'fugassa-menu-body fugassa-menu-body--wizard';
    body.innerHTML = '';
    const frame = document.createElement('div');
    frame.className = 'fugassa-wizard-frame';
    body.appendChild(frame);
    mountWizardShell(frame, {
      draft,
      initialTab: session?.wizardStep || 0,
      onBack: goHome,
      onCreated: () => {
        uiModule.showToast?.('Campaign created', { duration: 2500, leadingIcon: 'check' });
      },
      onSessionChange,
    });
  };

  const showResumeDraftDialog = (draft) => {
    body.className = 'fugassa-menu-body';
    body.innerHTML = '';
    const card = document.createElement('div');
    card.className = 'fugassa-dialog-card fugassa-panel';
    card.innerHTML = `
      <h3>Resume wizard draft?</h3>
      <p>A resumable Fugassa II draft already exists for <strong>${escapeHtml(draft.world_name || 'New Campaign')}</strong>.</p>
    `;
    const actions = document.createElement('div');
    actions.className = 'fugassa-inline-actions';
    const resumeBtn = menuBtn('Resume Draft', 'primary');
    const restartBtn = menuBtn('Start Over');
    const cancelBtn = menuBtn('Cancel');
    actions.append(resumeBtn, restartBtn, cancelBtn);
    card.appendChild(actions);
    const panel = document.createElement('div');
    panel.className = 'fugassa-submenu';
    panel.appendChild(card);
    body.appendChild(panel);
    resumeBtn.addEventListener('click', () => showWizard(draft));
    restartBtn.addEventListener('click', async () => {
      await clearDraft().catch(() => {});
      showWizard(null);
    });
    cancelBtn.addEventListener('click', goHome);
  };

  const renderSaveList = async (list) => {
    list.innerHTML = '<p class="fugassa-muted">Loading saves…</p>';
    try {
      const response = await api.listSaves();
      const saves = response?.saves || [];
      if (!saves.length) {
        list.innerHTML = '<p class="fugassa-muted">No saves found yet.</p>';
        return;
      }
      list.innerHTML = '';
      saves.forEach((save) => {
        const card = document.createElement('div');
        card.className = 'fugassa-save-card';
        const id = save.id || save.name || save.world_name || 'Campaign';
        const meta = [];
        if (save.updated_at) meta.push(formatSaveDate(save.updated_at));
        if (save.theme) meta.push(save.theme);
        if (save.turn_number) meta.push(`Turn ${save.turn_number}`);
        card.innerHTML = `
          <div class="fugassa-save-card-info">
            <strong>${escapeHtml(save.name || id)}</strong>
            ${meta.length ? `<span class="fugassa-muted">${escapeHtml(meta.join(' · '))}</span>` : ''}
          </div>
        `;
        const actions = document.createElement('div');
        actions.className = 'fugassa-save-card-actions';
        const playBtn = menuBtn('Play', 'primary');
        playBtn.classList.add('fugassa-btn--sm');
        playBtn.addEventListener('click', () => {
          onSessionChange?.({
            mode: 'play',
            menuScreen: 'home',
            wizardStep: session?.wizardStep || 0,
            activeSaveId: id,
            lastTool: 'fugassa',
          });
        });
        const renameBtn = menuBtn('Rename');
        renameBtn.classList.add('fugassa-btn--sm');
        renameBtn.addEventListener('click', async () => {
          const nextName = window.prompt('New campaign name:', save.name || id);
          if (nextName == null) return;
          const trimmed = nextName.trim();
          if (!trimmed || trimmed === id) return;
          try {
            await api.renameSave(id, trimmed);
            uiModule.showToast?.('Save renamed', { duration: 2200, leadingIcon: 'check' });
            await renderSaveList(list);
          } catch (error) {
            uiModule.showToast?.(error.message || String(error), { duration: 3500 });
          }
        });
        const deleteBtn = menuBtn('Delete');
        deleteBtn.classList.add('fugassa-btn--sm');
        deleteBtn.addEventListener('click', async () => {
          if (!window.confirm(`Delete save "${save.name || id}"? This cannot be undone.`)) return;
          try {
            await api.deleteSave(id);
            uiModule.showToast?.('Save deleted', { duration: 2200, leadingIcon: 'check' });
            await renderSaveList(list);
          } catch (error) {
            uiModule.showToast?.(error.message || String(error), { duration: 3500 });
          }
        });
        actions.append(playBtn, renameBtn, deleteBtn);
        card.appendChild(actions);
        list.appendChild(card);
      });
    } catch (error) {
      list.innerHTML = `<p class="fugassa-muted">${escapeHtml(error.message || String(error))}</p>`;
    }
  };

  const openContinue = async () => {
    body.className = 'fugassa-menu-body fugassa-menu-body--submenu';
    body.innerHTML = '';
    const panel = document.createElement('div');
    panel.className = 'fugassa-submenu';
    const backBtn = menuBtn('Back');
    backBtn.classList.add('fugassa-submenu-back');
    backBtn.addEventListener('click', goHome);
    const header = document.createElement('div');
    header.className = 'fugassa-save-list-header';
    header.innerHTML = '<h3>Continue</h3><p>Select an existing save and switch Titan into play mode.</p>';
    const list = document.createElement('div');
    list.className = 'fugassa-save-list';
    const content = document.createElement('div');
    content.className = 'fugassa-panel fugassa-continue-panel';
    content.append(header, list);
    panel.append(backBtn, content);
    body.appendChild(panel);
    await renderSaveList(list);
  };

  const openSettings = async () => {
    body.className = 'fugassa-menu-body fugassa-menu-body--submenu';
    body.innerHTML = '';
    const panel = document.createElement('div');
    panel.className = 'fugassa-submenu';
    const backBtn = menuBtn('Back');
    backBtn.classList.add('fugassa-submenu-back');
    backBtn.addEventListener('click', goHome);
    const wrap = document.createElement('div');
    wrap.className = 'fugassa-settings-card fugassa-panel';
    panel.append(backBtn, wrap);
    body.appendChild(panel);
    try {
      const config = await api.loadConfig();
      const llm = checkboxField('LLM enabled', Boolean(config.llm_enabled ?? true));
      const images = checkboxField('Images enabled', Boolean(config.images_enabled ?? true));
      const style = inputField('Default image style', config.image_style_default || '');
      const debug = checkboxField('Debug AI logging', Boolean(config.debug_ai_logging ?? false));
      const language = inputField('Language', config.language || '');
      const hudTheme = inputField('HUD theme', config.hud_theme || '');
      const save = menuBtn('Save settings', 'primary');
      wrap.append(llm.el, images.el, style.el, debug.el, language.el, hudTheme.el, save);
      save.addEventListener('click', async () => {
        await api.patchConfig({
          llm_enabled: llm.input.checked,
          images_enabled: images.input.checked,
          image_style_default: style.input.value.trim(),
          debug_ai_logging: debug.input.checked,
          language: language.input.value.trim(),
          hud_theme: hudTheme.input.value.trim(),
        });
        uiModule.showToast?.('Fugassa settings saved', { duration: 2200, leadingIcon: 'check' });
      });
    } catch (error) {
      wrap.innerHTML = `<p class="fugassa-muted">${escapeHtml(error.message || String(error))}</p>`;
    }
  };

  if (session?.menuScreen === 'wizard') {
    const draft = await loadDraft().catch(() => null);
    showWizard(draft);
  } else if (session?.menuScreen === 'continue') {
    await openContinue();
  } else if (session?.menuScreen === 'settings') {
    await openSettings();
  } else {
    await showHome();
  }
}

function menuBtn(label, variant = 'ghost') {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `fugassa-btn fugassa-btn--${variant}`;
  button.textContent = label;
  return button;
}

function inputField(label, value) {
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

function checkboxField(label, checked) {
  const el = document.createElement('label');
  el.className = 'fugassa-checkbox';
  const input = document.createElement('input');
  input.type = 'checkbox';
  input.checked = checked;
  const text = document.createElement('span');
  text.textContent = label;
  el.append(input, text);
  return { el, input };
}

function escapeHtml(value) {
  const div = document.createElement('div');
  div.textContent = value == null ? '' : String(value);
  return div.innerHTML;
}

function formatSaveDate(iso) {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleString();
  } catch {
    return String(iso);
  }
}
