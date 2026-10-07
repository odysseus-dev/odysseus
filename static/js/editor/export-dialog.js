/** Controlled image export dialog and canvas encoding helpers. */

const FORMAT_META = {
  png: { mime: 'image/png', extension: 'png', quality: undefined },
  jpeg: { mime: 'image/jpeg', extension: 'jpg', quality: 0.9 },
  webp: { mime: 'image/webp', extension: 'webp', quality: 0.9 },
};

function clampNumber(value, min, max, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.min(max, Math.max(min, parsed)) : fallback;
}

export function normalizeExportSettings(settings, sourceWidth, sourceHeight) {
  const format = FORMAT_META[settings?.format] ? settings.format : 'png';
  return {
    format,
    width: Math.round(clampNumber(settings?.width, 1, 16384, sourceWidth)),
    height: Math.round(clampNumber(settings?.height, 1, 16384, sourceHeight)),
    quality: clampNumber(settings?.quality, 0.01, 1, FORMAT_META[format].quality ?? 1),
    transparency: format !== 'jpeg' && settings?.transparency !== false,
    matte: /^#[0-9a-f]{6}$/i.test(settings?.matte || '') ? settings.matte : '#ffffff',
    filename: String(settings?.filename || 'edited-image').trim() || 'edited-image',
  };
}

export function prepareExportCanvas(sourceCanvas, settings) {
  const normalized = normalizeExportSettings(settings, sourceCanvas.width, sourceCanvas.height);
  const canvas = document.createElement('canvas');
  canvas.width = normalized.width;
  canvas.height = normalized.height;
  const ctx = canvas.getContext('2d');
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  if (!normalized.transparency) {
    ctx.fillStyle = normalized.matte;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }
  ctx.drawImage(sourceCanvas, 0, 0, canvas.width, canvas.height);
  return canvas;
}

export function encodeExportCanvas(sourceCanvas, settings) {
  const normalized = normalizeExportSettings(settings, sourceCanvas.width, sourceCanvas.height);
  const output = prepareExportCanvas(sourceCanvas, normalized);
  const meta = FORMAT_META[normalized.format];
  return new Promise((resolve, reject) => {
    output.toBlob(
      blob => blob ? resolve({ blob, settings: normalized, extension: meta.extension }) : reject(new Error('Image encoding failed')),
      meta.mime,
      meta.quality == null ? undefined : normalized.quality,
    );
  });
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return 'Estimating...';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function openExportDialog({
  sourceCanvas,
  defaultName = 'edited-image',
  title = 'Export Image',
  submitLabel = 'Export',
  attachColorPicker = null,
  returnFocus = null,
}) {
  return new Promise(resolve => {
    const previouslyFocused = returnFocus || document.activeElement;
    const overlay = document.createElement('div');
    overlay.className = 'ge-export-overlay';
    overlay.innerHTML = `
      <form class="ge-export-dialog" role="dialog" aria-modal="true" aria-labelledby="ge-export-title">
        <header class="ge-export-head">
          <h2 id="ge-export-title">${String(title).replace(/[<>&]/g, '')}</h2>
          <button type="button" class="ge-export-close" aria-label="Close">&times;</button>
        </header>
        <div class="ge-export-body">
          <div class="ge-export-preview-wrap"><canvas class="ge-export-preview"></canvas></div>
          <div class="ge-export-fields">
            <label class="ge-export-field"><span>Filename</span><input id="ge-export-filename" type="text" value="${String(defaultName).replace(/[<>&"']/g, '')}" /></label>
            <div class="ge-export-field"><span>Format</span><div class="ge-export-format" role="group" aria-label="Export format">
              <button type="button" class="active" data-format="png">PNG</button>
              <button type="button" data-format="jpeg">JPEG</button>
              <button type="button" data-format="webp">WebP</button>
            </div></div>
            <div class="ge-export-dimensions">
              <label class="ge-export-field"><span>Width</span><input id="ge-export-width" type="number" min="1" max="16384" value="${sourceCanvas.width}" /></label>
              <button type="button" class="ge-export-link active" aria-label="Lock aspect ratio" title="Lock aspect ratio" aria-pressed="true">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
              </button>
              <label class="ge-export-field"><span>Height</span><input id="ge-export-height" type="number" min="1" max="16384" value="${sourceCanvas.height}" /></label>
            </div>
            <label class="ge-export-field ge-export-quality"><span>Quality <output>90%</output></span><input id="ge-export-quality" type="range" min="1" max="100" value="90" /></label>
            <div class="ge-export-surface-row">
              <label class="ge-export-toggle"><input id="ge-export-transparency" type="checkbox" checked /><span>Transparency</span></label>
              <label class="ge-export-matte"><span>Matte</span><input id="ge-export-matte" class="ge-color-picker" type="color" value="#ffffff" /></label>
            </div>
          </div>
        </div>
        <footer class="ge-export-footer"><span class="ge-export-estimate">Estimating...</span><div><button type="button" class="ge-btn ge-btn-sm ge-export-cancel">Cancel</button><button type="submit" class="ge-btn ge-btn-primary">${String(submitLabel).replace(/[<>&]/g, '')}</button></div></footer>
      </form>`;
    document.body.appendChild(overlay);

    const dialog = overlay.querySelector('.ge-export-dialog');
    const preview = overlay.querySelector('.ge-export-preview');
    const filename = overlay.querySelector('#ge-export-filename');
    const widthInput = overlay.querySelector('#ge-export-width');
    const heightInput = overlay.querySelector('#ge-export-height');
    const qualityInput = overlay.querySelector('#ge-export-quality');
    const qualityField = overlay.querySelector('.ge-export-quality');
    const qualityOutput = qualityField.querySelector('output');
    const transparency = overlay.querySelector('#ge-export-transparency');
    const matte = overlay.querySelector('#ge-export-matte');
    const matteField = overlay.querySelector('.ge-export-matte');
    const estimate = overlay.querySelector('.ge-export-estimate');
    const link = overlay.querySelector('.ge-export-link');
    const ratio = sourceCanvas.width / sourceCanvas.height;
    let format = 'png';
    let ratioLocked = true;
    let estimateTimer = null;
    let estimateVersion = 0;

    try { attachColorPicker?.(matte); } catch {}

    const settings = () => normalizeExportSettings({
      format,
      width: widthInput.value,
      height: heightInput.value,
      quality: Number(qualityInput.value) / 100,
      transparency: transparency.checked,
      matte: matte.value,
      filename: filename.value,
    }, sourceCanvas.width, sourceCanvas.height);

    const renderPreview = () => {
      const current = settings();
      const max = 420;
      const scale = Math.min(max / current.width, max / current.height, 1);
      preview.width = Math.max(1, Math.round(current.width * scale));
      preview.height = Math.max(1, Math.round(current.height * scale));
      const ctx = preview.getContext('2d');
      // Settings can change without changing dimensions, so do not let the
      // previous matte or alpha state bleed into the next preview.
      ctx.clearRect(0, 0, preview.width, preview.height);
      if (!current.transparency) {
        ctx.fillStyle = current.matte;
        ctx.fillRect(0, 0, preview.width, preview.height);
      }
      ctx.drawImage(sourceCanvas, 0, 0, preview.width, preview.height);
    };

    const update = () => {
      const lossy = format !== 'png';
      qualityField.hidden = !lossy;
      transparency.disabled = format === 'jpeg';
      if (format === 'jpeg') transparency.checked = false;
      matteField.classList.toggle('disabled', transparency.checked && format !== 'jpeg');
      qualityOutput.textContent = `${qualityInput.value}%`;
      renderPreview();
      estimate.textContent = 'Estimating...';
      clearTimeout(estimateTimer);
      const version = ++estimateVersion;
      estimateTimer = setTimeout(async () => {
        try {
          const encoded = await encodeExportCanvas(sourceCanvas, settings());
          if (version === estimateVersion) estimate.textContent = formatBytes(encoded.blob.size);
        } catch {
          if (version === estimateVersion) estimate.textContent = 'Estimate unavailable';
        }
      }, 180);
    };

    overlay.querySelectorAll('[data-format]').forEach(button => {
      button.addEventListener('click', () => {
        format = button.dataset.format;
        overlay.querySelectorAll('[data-format]').forEach(candidate => {
          candidate.classList.toggle('active', candidate.dataset.format === format);
        });
        update();
      });
    });
    link.addEventListener('click', () => {
      ratioLocked = !ratioLocked;
      link.classList.toggle('active', ratioLocked);
      link.setAttribute('aria-pressed', ratioLocked ? 'true' : 'false');
    });
    widthInput.addEventListener('input', () => {
      if (ratioLocked) heightInput.value = String(Math.max(1, Math.round(Number(widthInput.value || 1) / ratio)));
      update();
    });
    heightInput.addEventListener('input', () => {
      if (ratioLocked) widthInput.value = String(Math.max(1, Math.round(Number(heightInput.value || 1) * ratio)));
      update();
    });
    [qualityInput, transparency, matte].forEach(input => input.addEventListener('input', update));

    const close = result => {
      clearTimeout(estimateTimer);
      document.removeEventListener('keydown', onKey, true);
      overlay.remove();
      if (previouslyFocused && previouslyFocused.isConnected && typeof previouslyFocused.focus === 'function') {
        previouslyFocused.focus({ preventScroll: true });
      }
      resolve(result);
    };
    const onKey = event => {
      if (event.key === 'Escape') { event.preventDefault(); close(null); }
    };
    document.addEventListener('keydown', onKey, true);
    overlay.querySelector('.ge-export-close').addEventListener('click', () => close(null));
    overlay.querySelector('.ge-export-cancel').addEventListener('click', () => close(null));
    overlay.addEventListener('click', event => { if (event.target === overlay) close(null); });
    dialog.addEventListener('submit', event => { event.preventDefault(); close(settings()); });
    update();
    filename.select();
  });
}
