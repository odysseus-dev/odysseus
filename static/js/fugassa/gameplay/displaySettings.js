/** Per-save HUD text size — applied via CSS custom properties on `.fugassa-main`. */

export const TEXT_SIZE_PRESETS = {
  small: 0.85,
  normal: 1,
  large: 1.15,
  xlarge: 1.3,
};

export const TEXT_SIZE_LABELS = {
  small: 'Small',
  normal: 'Normal',
  large: 'Large',
  xlarge: 'Extra large',
};

export const DEFAULT_DISPLAY_SETTINGS = {
  ui_text_size: 'normal',
  chat_text_size: 'normal',
};

export function normalizeDisplaySettings(raw) {
  const out = { ...DEFAULT_DISPLAY_SETTINGS };
  if (!raw || typeof raw !== 'object') return out;
  if (raw.ui_text_size in TEXT_SIZE_PRESETS) out.ui_text_size = raw.ui_text_size;
  if (raw.chat_text_size in TEXT_SIZE_PRESETS) out.chat_text_size = raw.chat_text_size;
  return out;
}

export function scaleForPreset(name) {
  return TEXT_SIZE_PRESETS[name] ?? TEXT_SIZE_PRESETS.normal;
}

export function applyDisplaySettings(root, settings) {
  const host = root?.closest?.('.fugassa-main') || root;
  if (!host) return;
  const norm = normalizeDisplaySettings(settings);
  host.style.setProperty('--fugassa-ui-text-scale', String(scaleForPreset(norm.ui_text_size)));
  host.style.setProperty('--fugassa-chat-text-scale', String(scaleForPreset(norm.chat_text_size)));
}

export function displaySettingsMarkup(current) {
  const norm = normalizeDisplaySettings(current);
  const option = (key, field) => Object.entries(TEXT_SIZE_LABELS)
    .map(([value, label]) => `<option value="${value}"${norm[field] === value ? ' selected' : ''}>${label}</option>`)
    .join('');
  return `
    <h3>Text size</h3>
    <p class="fugassa-muted">UI text affects the HUD, sidebars, and pause screens. Chat text affects GM messages and your input.</p>
    <label class="fugassa-field"><span>General UI text</span>
      <select data-ui-text-size>${option('ui', 'ui_text_size')}</select>
    </label>
    <label class="fugassa-field"><span>Chat panel text</span>
      <select data-chat-text-size>${option('chat', 'chat_text_size')}</select>
    </label>
  `;
}

export function readDisplaySettingsFromForm(wrap) {
  return normalizeDisplaySettings({
    ui_text_size: wrap.querySelector('[data-ui-text-size]')?.value,
    chat_text_size: wrap.querySelector('[data-chat-text-size]')?.value,
  });
}
