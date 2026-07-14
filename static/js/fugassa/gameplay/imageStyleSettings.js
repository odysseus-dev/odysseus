import {
  defaultImageStyleHintForTheme,
  FALLBACK_IMAGE_STYLES,
} from '../wizard/helpers.js';

export const IMAGE_STYLE_AUTO = 'auto';

export function imageStyleSettingsMarkup() {
  return `
    <h3>Image generator</h3>
    <p class="fugassa-muted">SD model for new scenes and portraits. Already generated images stay as they are.</p>
    <label class="fugassa-field"><span>Generation model</span>
      <select data-image-style><option value="">Loading…</option></select>
    </label>
    <p class="fugassa-muted" data-image-style-hint></p>
  `;
}

export function readImageStyleFromForm(wrap) {
  return String(wrap.querySelector('[data-image-style]')?.value || '').trim();
}

export function wireImageStyleSelect(wrap, {
  styles,
  currentStyle = '',
  theme = 'Fantasy',
  onChange,
} = {}) {
  const select = wrap.querySelector('[data-image-style]');
  const hint = wrap.querySelector('[data-image-style-hint]');
  if (!select) return;

  const syncHint = () => {
    if (!hint) return;
    const selected = readImageStyleFromForm(wrap);
    if (selected === IMAGE_STYLE_AUTO || !selected) {
      const autoStyle = defaultImageStyleHintForTheme(theme);
      hint.textContent = `Auto picks a generator from genre (currently ~${autoStyle}).`;
      return;
    }
    const label = select.selectedOptions[0]?.textContent || selected;
    hint.textContent = `New scenes and portraits use ${label}.`;
  };

  const available = (styles || FALLBACK_IMAGE_STYLES).filter((s) => s && s.id);
  select.replaceChildren();
  available.forEach(({ id, label }) => {
    const opt = document.createElement('option');
    opt.value = id;
    opt.textContent = label || id;
    select.appendChild(opt);
  });
  const autoOpt = document.createElement('option');
  autoOpt.value = IMAGE_STYLE_AUTO;
  autoOpt.textContent = 'Auto (match genre)';
  select.appendChild(autoOpt);

  const saved = String(currentStyle || '').trim();
  const validValues = new Set([...select.options].map((o) => o.value));
  let nextValue = IMAGE_STYLE_AUTO;
  if (saved === IMAGE_STYLE_AUTO || !saved) {
    nextValue = IMAGE_STYLE_AUTO;
  } else if (validValues.has(saved)) {
    nextValue = saved;
  } else if (available.length > 0) {
    const hintStyle = defaultImageStyleHintForTheme(theme);
    nextValue = available.find((s) => s.id === hintStyle)?.id || available[0].id;
  }
  select.value = nextValue;
  syncHint();

  select.addEventListener('change', () => {
    syncHint();
    onChange?.(readImageStyleFromForm(wrap));
  });
}
