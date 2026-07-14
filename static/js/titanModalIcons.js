/**
 * Propagate TITAN filled nav icons into tool window / modal headers.
 */
import { modalHeaderTileHtml, DOCLIB_TAB_ICONS } from './titanBrand.js';

/** Modal root id → icon name (matches sidebar / rail). */
export const MODAL_ICON_MAP = {
  'memory-modal': 'memory',
  'theme-modal': 'theme',
  'notes-pane': 'notes',
  'settings-modal': 'settings',
  'calendar-modal': 'calendar',
  'gallery-modal': 'gallery',
  'image-studio-modal': 'imageStudio',
  'tasks-modal': 'tasks',
  'cookbook-modal': 'cookbook',
  'model-hub-modal': 'cookbook',
  'scheduler-panel-modal': 'scheduler',
  'research-overlay': 'research',
  'doclib-modal': 'archive',
  'compare-model-overlay': 'compare',
  'email-lib-modal': 'email',
};

function extractTitle(heading) {
  const textEl = heading.querySelector('.modal-title-text');
  if (textEl) return textEl.textContent.trim();
  const clone = heading.cloneNode(true);
  clone.querySelectorAll('svg, .modal-header-icon-tile, .sidebar-icon-tile').forEach((n) => n.remove());
  return clone.textContent.replace(/\s+/g, ' ').trim();
}

function patchModalHeading(heading, iconName) {
  if (!heading || heading.dataset.titanIcon === iconName) return;
  const title = extractTitle(heading);
  if (!title) return;
  heading.classList.add('modal-title-lockup');
  heading.dataset.titanIcon = iconName;
  heading.innerHTML = `${modalHeaderTileHtml(iconName)}<span class="modal-title-text">${title}</span>`;
}

function patchDoclibHeaderIcon() {
  const ico = document.getElementById('doclib-header-icon');
  if (!ico) return;
  const activeTab = document.querySelector('#doclib-lib-tabs .lib-tab.active')?.dataset.doclibTab;
  const iconName = DOCLIB_TAB_ICONS[activeTab] || 'archive';
  if (ico.dataset.titanIcon === iconName) return;
  ico.dataset.titanIcon = iconName;
  ico.innerHTML = modalHeaderTileHtml(iconName);
}

export function applyModalHeaderIcons() {
  for (const [id, iconName] of Object.entries(MODAL_ICON_MAP)) {
    const root = document.getElementById(id);
    if (!root) continue;
    const header = root.querySelector('.modal-header, .notes-pane-header, .research-pane-header');
    const heading = header?.querySelector('h4, .notes-pane-title');
    patchModalHeading(heading, iconName);
  }
  patchDoclibHeaderIcon();
}

export function mountTitanModalIcons() {
  applyModalHeaderIcons();
  const observer = new MutationObserver(() => {
    applyModalHeaderIcons();
  });
  observer.observe(document.body, { childList: true, subtree: true });
  document.addEventListener('click', (e) => {
    if (e.target.closest('#doclib-lib-tabs .lib-tab')) {
      requestAnimationFrame(applyModalHeaderIcons);
    }
  });
}
