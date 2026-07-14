/**
 * Modal for per-turn chat scene images (📷 → preview inline, no second lightbox).
 * Owned by GameplayHub — one instance per gameplay session.
 */

export function createSceneReadyPopup(root) {
  const backdrop = document.createElement('div');
  backdrop.className = 'fugassa-popup-backdrop fugassa-scene-ready-backdrop';
  backdrop.hidden = true;
  backdrop.setAttribute('aria-hidden', 'true');
  backdrop.innerHTML = `
    <div class="fugassa-popup fugassa-scene-ready-popup" role="dialog" aria-modal="true" aria-labelledby="fugassa-scene-ready-title">
      <h4 id="fugassa-scene-ready-title">Scene ready</h4>
      <p class="fugassa-muted" data-scene-ready-label>A new scene image was generated.</p>
      <div class="fugassa-scene-ready-preview" data-scene-preview hidden>
        <img data-scene-img alt="Generated scene" />
      </div>
      <div class="fugassa-popup-actions">
        <button type="button" class="fugassa-btn fugassa-btn--ghost fugassa-btn--sm" data-scene-regen>Regenerate</button>
        <button type="button" class="fugassa-btn fugassa-btn--primary fugassa-btn--sm" data-scene-close>Close</button>
      </div>
    </div>
  `;
  root.appendChild(backdrop);

  const popup = backdrop.querySelector('.fugassa-scene-ready-popup');
  const labelEl = backdrop.querySelector('[data-scene-ready-label]');
  const previewWrap = backdrop.querySelector('[data-scene-preview]');
  const imgEl = backdrop.querySelector('[data-scene-img]');
  const regenBtn = backdrop.querySelector('[data-scene-regen]');
  const closeBtn = backdrop.querySelector('[data-scene-close]');

  let onRegen = null;

  const close = () => {
    backdrop.hidden = true;
    backdrop.setAttribute('aria-hidden', 'true');
  };

  popup.addEventListener('click', (ev) => ev.stopPropagation());
  regenBtn.addEventListener('click', () => {
    close();
    onRegen?.();
  });
  closeBtn.addEventListener('click', close);
  backdrop.addEventListener('click', (ev) => {
    if (ev.target === backdrop) close();
  });

  return {
    el: backdrop,
    show({ label, imageUrl, onRegen: regenHandler }) {
      labelEl.textContent = label || 'Scene image is ready.';
      onRegen = regenHandler;
      if (imageUrl) {
        imgEl.src = imageUrl;
        previewWrap.hidden = false;
      } else {
        imgEl.removeAttribute('src');
        previewWrap.hidden = true;
      }
      backdrop.hidden = false;
      backdrop.setAttribute('aria-hidden', 'false');
    },
    hide: close,
    destroy() {
      backdrop.remove();
    },
  };
}

/** Remove orphaned popups left on document.body from older builds. */
export function cleanupOrphanScenePopups() {
  document.querySelectorAll('body > .fugassa-scene-ready-backdrop').forEach((el) => el.remove());
}
