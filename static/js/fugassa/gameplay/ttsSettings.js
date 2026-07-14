/** Per-save Fugassa GM TTS preferences (Supertonic-3). */

export const TTS_LANGS = [
  { value: 'cs', label: 'Čeština' },
  { value: 'en', label: 'English' },
  { value: 'uk', label: 'Українська' },
];

export const TTS_MODES = [
  { value: 'manual', label: 'Manual (▶ per message)' },
  { value: 'auto', label: 'Auto (on reading phase)' },
  { value: 'off', label: 'Off' },
];

export const TTS_PREVIEW_SAMPLES = {
  cs: 'Temná chodba se táhne před tebou. Světlo pochodně kolísá na vlhkých stěnách.',
  en: 'A dark corridor stretches before you. Torchlight flickers on the damp stone walls.',
  uk: 'Темний коридор тягнеться перед тобою. Світло смолоскипа мерехтить на вологих стінах.',
};

export const DEFAULT_TTS_PREFS = {
  enabled: true,
  mode: 'manual',
  lang: 'cs',
  speaker_id: 0,
  speed: 1.0,
};

const SUPPORTED_LANGS = new Set(TTS_LANGS.map((l) => l.value));
const SUPPORTED_MODES = new Set(TTS_MODES.map((m) => m.value));

export function normalizeTtsPrefs(raw, { defaultLang = 'cs' } = {}) {
  const out = { ...DEFAULT_TTS_PREFS };
  const baseLang = SUPPORTED_LANGS.has(defaultLang) ? defaultLang : 'cs';
  out.lang = baseLang;
  if (!raw || typeof raw !== 'object') return out;
  if ('enabled' in raw) out.enabled = Boolean(raw.enabled);
  const mode = String(raw.mode || out.mode).trim().toLowerCase();
  if (SUPPORTED_MODES.has(mode)) out.mode = mode;
  const lang = String(raw.lang || out.lang).trim().toLowerCase();
  if (SUPPORTED_LANGS.has(lang)) out.lang = lang;
  const sid = Number(raw.speaker_id);
  if (Number.isFinite(sid)) out.speaker_id = Math.max(0, Math.min(9, Math.trunc(sid)));
  const speed = Number(raw.speed);
  if (Number.isFinite(speed)) out.speed = Math.max(0.75, Math.min(1.5, speed));
  return out;
}

export function isTtsActive(prefs) {
  const p = normalizeTtsPrefs(prefs);
  return p.enabled && p.mode !== 'off';
}

export function previewSampleForLang(lang) {
  return TTS_PREVIEW_SAMPLES[lang] || TTS_PREVIEW_SAMPLES.cs;
}

export function ttsSettingsMarkup(current, { voices = [], modelReady = false } = {}) {
  const prefs = normalizeTtsPrefs(current);
  const modeOpts = TTS_MODES.map(
    (m) => `<option value="${m.value}"${prefs.mode === m.value ? ' selected' : ''}>${m.label}</option>`,
  ).join('');
  const langOpts = TTS_LANGS.map(
    (l) => `<option value="${l.value}"${prefs.lang === l.value ? ' selected' : ''}>${l.label}</option>`,
  ).join('');
  const voiceOpts = (voices.length ? voices : [{ id: prefs.speaker_id, label: `Hlas ${prefs.speaker_id + 1}` }])
    .map((v) => `<option value="${v.id}"${prefs.speaker_id === v.id ? ' selected' : ''}>${v.label}</option>`)
    .join('');
  const modelStatus = modelReady
    ? '<span class="fugassa-tts-model-ok">Supertonic-3 ✓</span>'
    : '<span class="fugassa-tts-model-miss">Model not installed — open Model Hub → TTS</span>';
  return `
    <h3>GM narration (TTS)</h3>
    <p class="fugassa-muted fugassa-tts-model-status">${modelStatus}</p>
    <label class="fugassa-field fugassa-field--checkbox">
      <input type="checkbox" data-tts-enabled${prefs.enabled ? ' checked' : ''} />
      <span>Enable GM narration</span>
    </label>
    <label class="fugassa-field"><span>Playback mode</span>
      <select data-tts-mode>${modeOpts}</select>
    </label>
    <label class="fugassa-field"><span>Language</span>
      <select data-tts-lang>${langOpts}</select>
    </label>
    <label class="fugassa-field"><span>Voice</span>
      <select data-tts-speaker>${voiceOpts}</select>
    </label>
    <label class="fugassa-field"><span>Speed (${prefs.speed.toFixed(2)}×)</span>
      <input type="range" data-tts-speed min="0.75" max="1.5" step="0.05" value="${prefs.speed}" />
    </label>
    <div class="fugassa-inline-actions" style="margin-top:8px;">
      <button type="button" class="fugassa-btn fugassa-btn--sm" data-tts-preview ${modelReady ? '' : 'disabled'}>Preview voice</button>
      <span class="fugassa-muted" data-tts-preview-status style="font-size:12px;"></span>
    </div>
  `;
}

export function readTtsPrefsFromForm(wrap) {
  const speedRaw = wrap.querySelector('[data-tts-speed]')?.value;
  return normalizeTtsPrefs({
    enabled: Boolean(wrap.querySelector('[data-tts-enabled]')?.checked),
    mode: wrap.querySelector('[data-tts-mode]')?.value,
    lang: wrap.querySelector('[data-tts-lang]')?.value,
    speaker_id: wrap.querySelector('[data-tts-speaker]')?.value,
    speed: speedRaw,
  });
}

export async function playTtsPreview(prefs, { onStatus } = {}) {
  const p = normalizeTtsPrefs(prefs);
  onStatus?.('Synthesizing…');
  const res = await fetch('/api/tts/synthesize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      text: previewSampleForLang(p.lang),
      format: 'audio',
      engine: 'supertonic',
      lang: p.lang,
      speaker_id: p.speaker_id,
      speed: p.speed,
    }),
  });
  if (!res.ok) {
    let detail = 'Preview failed';
    try {
      const err = await res.json();
      detail = err.detail?.message || err.detail || detail;
    } catch {
      // ignore
    }
    onStatus?.(detail);
    throw new Error(detail);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  onStatus?.('Playing…');
  await new Promise((resolve, reject) => {
    const audio = new Audio(url);
    if (p.speed !== 1) audio.playbackRate = p.speed;
    audio.onended = () => {
      URL.revokeObjectURL(url);
      onStatus?.('');
      resolve();
    };
    audio.onerror = () => {
      URL.revokeObjectURL(url);
      onStatus?.('Playback failed');
      reject(new Error('Playback failed'));
    };
    audio.play().catch(reject);
  });
}
