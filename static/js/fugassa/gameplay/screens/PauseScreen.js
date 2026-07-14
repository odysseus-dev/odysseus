import uiModule from '../../../ui.js';
import * as api from '../../fugassaApi.js';
import { escapeHtml } from './InventoryScreen.js';
import {
  applyDisplaySettings,
  displaySettingsMarkup,
  readDisplaySettingsFromForm,
} from '../displaySettings.js';
import {
  imageStyleSettingsMarkup,
  readImageStyleFromForm,
  wireImageStyleSelect,
} from '../imageStyleSettings.js';
import {
  playTtsPreview,
  readTtsPrefsFromForm,
  ttsSettingsMarkup,
} from '../ttsSettings.js';

export async function mountPauseScreen(root, { saveId, onClose, onStateChange }) {
  root.className = 'fugassa-screen fugassa-screen--pause';
  root.innerHTML = `
    <header class="fugassa-screen-head">
      <h2>Pause — World & GM Guides</h2>
      <button type="button" class="fugassa-btn fugassa-btn--ghost fugassa-btn--sm" data-close>Back to game</button>
    </header>
    <div class="fugassa-inline-actions fugassa-pause-tabs" data-pause-tabs>
      <button type="button" class="fugassa-btn fugassa-btn--sm fugassa-hud-tab--active" data-tab="settings">Settings</button>
      <button type="button" class="fugassa-btn fugassa-btn--sm" data-tab="world">World</button>
      <button type="button" class="fugassa-btn fugassa-btn--sm" data-tab="guides">GM Guides</button>
      <button type="button" class="fugassa-btn fugassa-btn--sm" data-tab="audio">Audio</button>
      <button type="button" class="fugassa-btn fugassa-btn--sm" data-tab="debug">Debug</button>
    </div>
    <div class="fugassa-screen-body fugassa-pause-panel" data-panel-settings>
      <p class="fugassa-muted">UI and chat text size for this save.</p>
      <section class="fugassa-screen-card" data-display-wrap></section>
      <div class="fugassa-pause-actions">
        <button type="button" class="fugassa-btn fugassa-btn--primary fugassa-btn--sm" data-save-display>Save display settings</button>
      </div>
      <p class="fugassa-muted" data-display-feedback></p>
    </div>
    <div class="fugassa-screen-body fugassa-pause-panel" data-panel-world hidden>
      <p class="fugassa-muted">World profile and rules. Changes may affect story coherence with past play.</p>
      <section class="fugassa-screen-card" data-world-wrap><p class="fugassa-muted">Loading…</p></section>
      <section class="fugassa-screen-card" data-image-style-wrap></section>
      <section class="fugassa-screen-card" data-rules-wrap></section>
      <div class="fugassa-pause-actions">
        <button type="button" class="fugassa-btn fugassa-btn--primary fugassa-btn--sm" data-save-world>Save world settings</button>
      </div>
      <p class="fugassa-muted" data-world-feedback></p>
    </div>
    <div class="fugassa-screen-body fugassa-pause-panel" data-panel-guides hidden>
      <p class="fugassa-muted">Per-guide instructions for the GM. Changes may affect story coherence with past play.</p>
      <section class="fugassa-screen-card" data-gm-wrap></section>
      <div class="fugassa-pause-actions">
        <button type="button" class="fugassa-btn fugassa-btn--primary fugassa-btn--sm" data-save-guides>Save GM guides</button>
      </div>
      <p class="fugassa-muted" data-guides-feedback></p>
    </div>
    <div class="fugassa-screen-body fugassa-pause-panel" data-panel-audio hidden>
      <p class="fugassa-muted">Per-save GM narration settings (Supertonic-3).</p>
      <section class="fugassa-screen-card" data-audio-wrap><p class="fugassa-muted">Loading…</p></section>
      <div class="fugassa-pause-actions">
        <button type="button" class="fugassa-btn fugassa-btn--primary fugassa-btn--sm" data-save-audio>Save audio settings</button>
      </div>
      <p class="fugassa-muted" data-audio-feedback></p>
    </div>
    <div class="fugassa-screen-body fugassa-pause-panel" data-panel-debug hidden>
      <p class="fugassa-muted">Read-only engine snapshot — current location subgraph (depth 2), last turn resolution, world flags.</p>
      <section class="fugassa-screen-card" data-debug-wrap><p class="fugassa-muted">Loading…</p></section>
    </div>
  `;

  root.querySelector('[data-close]').addEventListener('click', () => onClose?.());
  const displayFeedback = root.querySelector('[data-display-feedback]');
  const worldFeedback = root.querySelector('[data-world-feedback]');
  const guidesFeedback = root.querySelector('[data-guides-feedback]');
  const displayWrap = root.querySelector('[data-display-wrap]');
  const worldWrap = root.querySelector('[data-world-wrap]');
  const imageStyleWrap = root.querySelector('[data-image-style-wrap]');
  const rulesWrap = root.querySelector('[data-rules-wrap]');
  const gmWrap = root.querySelector('[data-gm-wrap]');
  const panelSettings = root.querySelector('[data-panel-settings]');
  const panelWorld = root.querySelector('[data-panel-world]');
  const panelGuides = root.querySelector('[data-panel-guides]');
  const panelAudio = root.querySelector('[data-panel-audio]');
  const panelDebug = root.querySelector('[data-panel-debug]');
  const debugWrap = root.querySelector('[data-debug-wrap]');
  const audioWrap = root.querySelector('[data-audio-wrap]');
  const audioFeedback = root.querySelector('[data-audio-feedback]');
  let debugLoaded = false;

  async function loadVoicesForLang(lang) {
    try {
      const res = await fetch(`/api/tts/voices?engine=supertonic&lang=${encodeURIComponent(lang)}`);
      if (!res.ok) return [];
      const data = await res.json();
      return data.voices || [];
    } catch {
      return [];
    }
  }

  async function fetchTtsReady() {
    try {
      const res = await fetch('/api/tts/stats');
      if (!res.ok) return false;
      const data = await res.json();
      return Boolean(data.supertonic_ready);
    } catch {
      return false;
    }
  }

  async function wireAudioControls() {
    const speedInput = audioWrap.querySelector('[data-tts-speed]');
    const speedLabel = audioWrap.querySelector('[data-tts-speed]')?.closest('label')?.querySelector('span');
    speedInput?.addEventListener('input', () => {
      if (speedLabel) speedLabel.textContent = `Speed (${Number(speedInput.value).toFixed(2)}×)`;
    });
    audioWrap.querySelector('[data-tts-lang]')?.addEventListener('change', async (ev) => {
      const nextVoices = await loadVoicesForLang(ev.target.value);
      const speakerSel = audioWrap.querySelector('[data-tts-speaker]');
      if (!speakerSel) return;
      const current = speakerSel.value;
      speakerSel.innerHTML = nextVoices.map(
        (v) => `<option value="${v.id}">${escapeHtml(v.label)}</option>`,
      ).join('');
      if (current) speakerSel.value = current;
    });
    const previewBtn = audioWrap.querySelector('[data-tts-preview]');
    const previewStatus = audioWrap.querySelector('[data-tts-preview-status]');
    let previewBusy = false;
    previewBtn?.addEventListener('click', async () => {
      if (previewBusy) return;
      previewBusy = true;
      previewBtn.disabled = true;
      try {
        await playTtsPreview(readTtsPrefsFromForm(audioWrap), {
          onStatus: (text) => {
            if (previewStatus) previewStatus.textContent = text || '';
          },
        });
      } catch (error) {
        if (previewStatus) previewStatus.textContent = error.message || String(error);
      } finally {
        previewBusy = false;
        const ready = await fetchTtsReady();
        if (previewBtn) previewBtn.disabled = !ready;
      }
    });
  }

  async function renderAudioSettings(prefs) {
    const [voices, modelReady] = await Promise.all([
      loadVoicesForLang(prefs.lang || 'cs'),
      fetchTtsReady(),
    ]);
    audioWrap.innerHTML = ttsSettingsMarkup(prefs, { voices, modelReady });
    await wireAudioControls();
  }

  root.querySelectorAll('[data-pause-tabs] button').forEach((tabBtn) => {
    tabBtn.addEventListener('click', async () => {
      root.querySelectorAll('[data-pause-tabs] button').forEach((b) => b.classList.remove('fugassa-hud-tab--active'));
      tabBtn.classList.add('fugassa-hud-tab--active');
      const tab = tabBtn.dataset.tab;
      panelSettings.hidden = tab !== 'settings';
      panelWorld.hidden = tab !== 'world';
      panelGuides.hidden = tab !== 'guides';
      panelAudio.hidden = tab !== 'audio';
      panelDebug.hidden = tab !== 'debug';
      if (tab === 'audio') {
        await renderAudioSettings(readTtsPrefsFromForm(audioWrap) || pauseData?.tts_prefs || {});
      }
      if (tab === 'debug' && !debugLoaded) {
        debugLoaded = true;
        try {
          const snapshot = await api.getDebugSnapshot(saveId);
          debugWrap.innerHTML = `<pre class="fugassa-debug-json">${escapeHtml(JSON.stringify(snapshot, null, 2))}</pre>`;
        } catch (error) {
          debugWrap.innerHTML = `<p class="fugassa-muted">${escapeHtml(error.message || String(error))}</p>`;
          debugLoaded = false;
        }
      }
    });
  });

  let pauseData = null;
  try {
    pauseData = await api.getGamePause(saveId);
  } catch (error) {
    worldWrap.innerHTML = `<p class="fugassa-muted">${escapeHtml(error.message)}</p>`;
    return;
  }

  const wp = pauseData.world_profile || {};
  displayWrap.innerHTML = displaySettingsMarkup(pauseData.display_settings);
  await renderAudioSettings(pauseData.tts_prefs || {});
  const previewDisplay = () => {
    applyDisplaySettings(root, readDisplaySettingsFromForm(displayWrap));
  };
  displayWrap.querySelector('[data-ui-text-size]')?.addEventListener('change', previewDisplay);
  displayWrap.querySelector('[data-chat-text-size]')?.addEventListener('change', previewDisplay);
  previewDisplay();
  worldWrap.innerHTML = `
    <h3>World profile</h3>
    <label class="fugassa-field"><span>World information</span><textarea data-wi rows="5">${escapeHtml(wp.world_information || '')}</textarea></label>
    <label class="fugassa-field"><span>Opening hook</span><textarea data-oh rows="3">${escapeHtml(wp.opening_hook || '')}</textarea></label>
    <label class="fugassa-field"><span>Currency (comma-separated)</span><input data-cur type="text" value="${escapeHtml((wp.currency || []).join(', '))}" /></label>
  `;

  imageStyleWrap.innerHTML = imageStyleSettingsMarkup();
  const campaignTheme = String(wp.theme || 'Fantasy').trim() || 'Fantasy';
  const renderImageStyles = (styles) => {
    wireImageStyleSelect(imageStyleWrap, {
      styles,
      currentStyle: wp.image_style || '',
      theme: campaignTheme,
    });
  };
  try {
    const stylePayload = await api.getImageStyles();
    renderImageStyles(stylePayload?.styles);
  } catch {
    renderImageStyles();
  }

  rulesWrap.innerHTML = `
    <h3>Rules</h3>
    <label class="fugassa-field"><span>Rules mode</span>
      <select data-rules>
        <option value="5e-style">5e-style (strict)</option>
        <option value="homebrew">Homebrew</option>
      </select>
    </label>
    <label class="fugassa-field"><span>Resolution</span>
      <select data-res>
        <option value="dice">Dice</option>
        <option value="narrative">Narrative</option>
      </select>
    </label>
    <label class="fugassa-field"><span>Playstyle</span>
      <select data-ps>
        <option value="adventure">Adventure</option>
        <option value="mystery">Mystery</option>
        <option value="slice_of_life">Slice of life</option>
      </select>
    </label>
  `;
  rulesWrap.querySelector('[data-rules]').value = pauseData.rules_mode || '5e-style';
  rulesWrap.querySelector('[data-res]').value = pauseData.resolution_mode || 'dice';
  rulesWrap.querySelector('[data-ps]').value = pauseData.playstyle || 'adventure';

  const gmMap = pauseData.gm_guides_map || {};
  const guideNames = pauseData.gm_guide_names?.length ? pauseData.gm_guide_names : Object.keys(gmMap);
  let selectedGuide = guideNames[0] || '';
  gmWrap.innerHTML = `
    <h3>GM guides</h3>
    <div class="fugassa-inline-actions" data-gm-tabs></div>
    <label class="fugassa-field"><span>Guide text</span><textarea data-gm-text rows="12"></textarea></label>
  `;
  const tabs = gmWrap.querySelector('[data-gm-tabs]');
  const gmText = gmWrap.querySelector('[data-gm-text]');
  const localGm = { ...gmMap };

  const syncGmText = () => {
    gmText.value = localGm[selectedGuide] || '';
  };

  guideNames.forEach((name) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'fugassa-btn fugassa-btn--sm';
    btn.textContent = name.replace(/^gm_|\.txt$/g, '').replace(/_/g, ' ');
    btn.addEventListener('click', () => {
      localGm[selectedGuide] = gmText.value;
      selectedGuide = name;
      syncGmText();
    });
    tabs.appendChild(btn);
  });
  syncGmText();

  const readWorldProfile = () => {
    const currency = worldWrap.querySelector('[data-cur]').value.split(',').map((s) => s.trim()).filter(Boolean);
    return {
      world_information: worldWrap.querySelector('[data-wi]').value.trim(),
      opening_hook: worldWrap.querySelector('[data-oh]').value.trim(),
      currency,
      image_style: readImageStyleFromForm(imageStyleWrap),
    };
  };

  const readRulesPayload = () => ({
    rules_mode: rulesWrap.querySelector('[data-rules]').value,
    resolution_mode: rulesWrap.querySelector('[data-res]').value,
    playstyle: rulesWrap.querySelector('[data-ps]').value,
  });

  root.querySelector('[data-save-display]').addEventListener('click', async () => {
    displayFeedback.textContent = 'Saving…';
    try {
      const res = await api.patchGamePause(saveId, {
        display_settings: readDisplaySettingsFromForm(displayWrap),
      });
      onStateChange?.(res.state);
      displayFeedback.textContent = 'Saved.';
      uiModule.showToast?.('Display settings saved', { duration: 2200, leadingIcon: 'check' });
    } catch (error) {
      displayFeedback.textContent = error.message || String(error);
    }
  });

  root.querySelector('[data-save-world]').addEventListener('click', async () => {
    if (!window.confirm('Save world changes? Story coherence with past play may shift.')) return;
    worldFeedback.textContent = 'Saving…';
    try {
      const res = await api.patchGamePause(saveId, {
        world_profile: readWorldProfile(),
        ...readRulesPayload(),
      });
      onStateChange?.(res.state);
      worldFeedback.textContent = 'Saved.';
      uiModule.showToast?.('World settings saved', { duration: 2200, leadingIcon: 'check' });
    } catch (error) {
      worldFeedback.textContent = error.message || String(error);
    }
  });

  root.querySelector('[data-save-guides]').addEventListener('click', async () => {
    localGm[selectedGuide] = gmText.value;
    if (!window.confirm('Save GM guide changes? Story coherence with past play may shift.')) return;
    guidesFeedback.textContent = 'Saving…';
    try {
      const res = await api.patchGamePause(saveId, {
        gm_guides_map: localGm,
      });
      onStateChange?.(res.state);
      guidesFeedback.textContent = 'Saved.';
      uiModule.showToast?.('GM guides saved', { duration: 2200, leadingIcon: 'check' });
    } catch (error) {
      guidesFeedback.textContent = error.message || String(error);
    }
  });

  root.querySelector('[data-save-audio]').addEventListener('click', async () => {
    audioFeedback.textContent = 'Saving…';
    try {
      const res = await api.patchGamePause(saveId, {
        tts_prefs: readTtsPrefsFromForm(audioWrap),
      });
      onStateChange?.(res.state);
      audioFeedback.textContent = 'Saved.';
      uiModule.showToast?.('Audio settings saved', { duration: 2200, leadingIcon: 'check' });
    } catch (error) {
      audioFeedback.textContent = error.message || String(error);
    }
  });
}
