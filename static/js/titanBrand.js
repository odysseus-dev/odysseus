/** TITAN / Fugassa brand marks and filled sidebar nav icons — single source of truth. */

const NAV_PATHS = {
  newChat:
    '<path fill="currentColor" d="M11 4h2v16h-2V4zM4 11h16v2H4v-2z"/>',
  search:
    '<path fill="currentColor" d="M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15zm0 2a5.5 5.5 0 1 1 0 11 5.5 5.5 0 0 1 0-11z"/><path fill="currentColor" d="m19.7 19.7-4.2-4.2 1.4-1.4 4.2 4.2-1.4 1.4z"/>',
  chats:
    '<path fill="currentColor" d="M6 4h12a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9.4L5 20.6V6a2 2 0 0 1 2-2z"/>',
  email:
    '<path fill="currentColor" d="M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5zm2 1.4 7 4.9 7-4.9V5H5v1.4zM19 18V9.1l-7 4.9-7-4.9V18h14z"/>',
  documents:
    '<path fill="currentColor" d="M8 3h8l4 4v14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zm6 1.6V8h4.4L14 4.6z"/><path fill="currentColor" opacity="0.5" d="M8 11.5h8v1.6H8v-1.6zm0 3.5h5.5v1.6H8V15z"/>',
  settings:
    '<path fill="currentColor" d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z"/><path fill="currentColor" d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  libArchive:
    '<path fill="currentColor" d="M3 6h18v3H3V6zm2 4h14v11H5V10zm3 3h8v2H8v-2z"/>',
};

const TOOL_PATHS = {
  memory:
    '<path fill="currentColor" d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path fill="currentColor" d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/>',
  calendar:
    '<path fill="currentColor" d="M7 2h2v3H7V2zm8 0h2v3h-2V2zM5 5h14a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2zm1 5h2v2H6v-2zm4 0h2v2h-2v-2zm4 0h2v2h-2v-2z"/>',
  compare:
    '<circle cx="7" cy="7" r="3" fill="currentColor"/><circle cx="17" cy="17" r="3" fill="currentColor"/><path fill="currentColor" d="M13 7h3a2 2 0 0 1 2 2v5"/>',
  scheduler:
    '<path fill="currentColor" d="M6 4h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zm2 4v4h8V8H8z"/><path fill="currentColor" d="M4 9h2v2H4V9zm14 0h2v2h-2V9zM4 14h2v2H4v-2zm14 0h2v2h-2v-2z"/>',
  cookbook:
    '<path fill="currentColor" d="M6 3h8a4 4 0 0 1 4 4v14H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zm2 2v16h8V7a2 2 0 0 0-2-2H8z"/>',
  research:
    '<path fill="currentColor" d="M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15zm0 2a5.5 5.5 0 1 1 0 11 5.5 5.5 0 0 1 0-11z"/><path fill="currentColor" d="M11 8h2v3h3v2h-3v3h-2v-3H8v-2h3V8z"/>',
  gallery:
    '<path fill="currentColor" d="M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zm2 10 3-3 4 5 3-4 4 5H6v-3z"/><circle cx="9" cy="9" r="1.5" fill="currentColor"/>',
  imageStudio:
    '<path fill="currentColor" d="M17.5 3.5 14 7l-1.6-1.6a2 2 0 0 0-2.8 2.8L15 10.2 20.5 4.7 17.5 3.5z"/><path fill="currentColor" d="M4 20h16v2H4v-2z"/><path fill="currentColor" d="M5 20c0-4 3-7.5 7-8.5 1.5-.3 3 .2 4.2 1.3L13 16l-2 2H5z"/>',
  archive:
    '<path fill="currentColor" d="M6 3h12a2 2 0 0 1 2 2v16H6a2.5 2.5 0 0 1-2.5-2.5v-13A2.5 2.5 0 0 1 6 3zm0 4v11h12V7H6zm2 2h8v2H8V9zm0 4h5v2H8v-2z"/>',
  notes:
    '<path fill="currentColor" d="M8 4h9a2 2 0 0 1 2 2v14H8a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"/><path fill="currentColor" opacity="0.45" d="M6 6h2.5v14H6V6z"/><path fill="currentColor" opacity="0.5" d="M10 9h6.5v1.5H10V9zm0 3h6.5v1.5H10V12zm0 3h4.5v1.5H10V15z"/><path fill="currentColor" d="m15.2 15.8 2.8 2.8-1.2 1.2-2.8-2.8 1.2-1.2z"/>',
  tasks:
    '<path fill="currentColor" d="M7 2h2v3H7V2zm8 0h2v3h-2V2zM5 5h14a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2zm3 9.5 2 2 4.5-4.5-1.4-1.4L10 14.6l-.6-.6L8 15.5z"/>',
  theme:
    '<path fill="currentColor" d="M12 2a10 10 0 1 0 0 20 4.8 0 0 0 0-10A4.8 4.8 0 0 1 12 2z"/>',
};

/** Filled nav icon for sidebar tiles and icon rail. */
export function navIcon(name, { size = 14, className = '' } = {}) {
  const paths = NAV_PATHS[name] || TOOL_PATHS[name];
  if (!paths) return '';
  const cls = className ? ` class="${className}"` : '';
  return `<svg${cls} width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
}

/** Filled tool icon for sidebar tiles and icon rail. */
export function toolIcon(name, { size = 14, className = 'sidebar-action-icon' } = {}) {
  const paths = TOOL_PATHS[name];
  if (!paths) return '';
  const cls = className ? ` class="${className}"` : '';
  return `<svg${cls} width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
}

/** Modal / window header tile — same filled icon as sidebar. */
export function modalHeaderTileHtml(iconName, { size = 14 } = {}) {
  const svg = navIcon(iconName, { size }) || toolIcon(iconName, { size, className: '' });
  if (!svg) return '';
  return `<span class="modal-header-icon-tile">${svg}</span>`;
}

/** Doclib sub-tab icons (Library modal header). */
export const DOCLIB_TAB_ICONS = {
  chats: 'chats',
  documents: 'documents',
  research: 'research',
  archive: 'libArchive',
};

/** TITAN mark — horizontal + vertical bar with constellation (no crossbar). */
export function titanMarkSvg({ size = 26, accent = 'currentColor', cutout = 'var(--panel)' } = {}) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 100 100" aria-hidden="true">
  <rect x="18" y="14" width="64" height="19" rx="4" fill="${accent}"/>
  <rect x="40.5" y="14" width="19" height="72" rx="4" fill="${accent}"/>
  <g fill="${cutout}">
    <circle cx="50" cy="45" r="3"/>
    <circle cx="46" cy="60" r="2.6"/>
    <circle cx="54" cy="73" r="2.3"/>
  </g>
  <g stroke="${cutout}" stroke-width="1.4" opacity="0.8">
    <line x1="50" y1="45" x2="46" y2="60"/>
    <line x1="46" y1="60" x2="54" y2="73"/>
  </g>
</svg>`;
}

/** Fugassa mark — F-bar with crossbar and constellation. */
export function fugassaMarkSvg({ size = 26, accent = 'currentColor', cutout = 'var(--panel)' } = {}) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 100 100" aria-hidden="true">
  <rect x="18" y="14" width="64" height="19" rx="4" fill="${accent}"/>
  <rect x="18" y="14" width="19" height="72" rx="4" fill="${accent}"/>
  <rect x="18" y="42" width="48" height="17" rx="4" fill="${accent}"/>
  <g fill="${cutout}">
    <circle cx="60" cy="66" r="2.8"/>
    <circle cx="70" cy="78" r="2.3"/>
    <circle cx="52" cy="80" r="2"/>
  </g>
  <g stroke="${cutout}" stroke-width="1.3" opacity="0.8">
    <line x1="60" y1="66" x2="70" y2="78"/>
    <line x1="60" y1="66" x2="52" y2="80"/>
  </g>
</svg>`;
}

export function titanSidebarBrandHtml() {
  return `<span class="sidebar-brand-mark" aria-hidden="true">${titanMarkSvg({ size: 34, accent: 'var(--brand-color, var(--red))' })}</span>
<span class="sidebar-brand-text">
  <span class="sidebar-brand-title">TITAN</span>
  <span class="sidebar-brand-sub">by Fugarius</span>
</span>`;
}

/** Sidebar toggle — Titan mark instead of hamburger lines. */
export function titanToggleMarkHtml({ size = 18 } = {}) {
  return titanMarkSvg({
    size,
    accent: 'var(--brand-color, var(--red))',
    cutout: 'var(--panel, var(--bg))',
  });
}

/** Welcome / home screen — same lockup as sidebar, larger. */
export function titanWelcomeBrandHtml() {
  return `<span class="welcome-brand-mark" aria-hidden="true">${titanMarkSvg({ size: 56, accent: 'var(--brand-color, var(--red))', cutout: 'var(--bg)' })}</span>
<span class="welcome-brand-text">
  <span class="welcome-brand-title">TITAN</span>
  <span class="welcome-brand-sub">by Fugarius</span>
</span>`;
}

export function fugassaBrandLockupHtml() {
  return `<div class="fugassa-brand-lockup">
  <span class="fugassa-brand-mark" aria-hidden="true">${fugassaMarkSvg({ size: 34, accent: 'var(--fugassa-accent, var(--red, #e85d5d))' })}</span>
  <div class="fugassa-brand-text">
    <h1 class="fugassa-brand-title">FUGASSA</h1>
    <p class="fugassa-brand-sub">by Fugarius</p>
    <p class="fugassa-brand-powered">powered by <span class="fugassa-brand-titan">TITAN</span></p>
  </div>
</div>`;
}

/** Fugassa menu home — vertical lockup above menu buttons (no top bar). */
export function fugassaMenuBrandHtml() {
  return `<div class="fugassa-menu-brand-lockup">
  <span class="fugassa-menu-brand-mark" aria-hidden="true">${fugassaMarkSvg({ size: 56, accent: 'var(--fugassa-accent, var(--red, #e85d5d))', cutout: 'var(--bg, #0e0e10)' })}</span>
  <div class="fugassa-menu-brand-text">
    <h1 class="fugassa-menu-brand-title">FUGASSA</h1>
    <p class="fugassa-menu-brand-sub">by Fugarius</p>
    <p class="fugassa-menu-brand-powered">powered by <span class="fugassa-menu-brand-titan">TITAN</span></p>
  </div>
</div>`;
}

/** Core sidebar nav targets (icon rail copies these after mount). */
export const NAV_ICON_TARGETS = {
  'sidebar-new-chat-btn': 'newChat',
  'sidebar-search-btn': 'search',
  'chats-section-title': 'chats',
  'sidebar-documents-btn': 'documents',
  'email-section-title': 'email',
};

/** Tool sidebar rows — filled icons with brand color on tiles. */
export const TOOL_ICON_TARGETS = {
  'tool-memory-btn': 'memory',
  'tool-calendar-btn': 'calendar',
  'tool-compare-btn': 'compare',
  'tool-scheduler-btn': 'scheduler',
  'tool-cookbook-btn': 'cookbook',
  'tool-research-btn': 'research',
  'tool-gallery-btn': 'gallery',
  'tool-image-studio-btn': 'imageStudio',
  'tool-library-btn': 'archive',
  'tool-notes-btn': 'notes',
  'tool-tasks-btn': 'tasks',
  'tool-theme-btn': 'theme',
};

/** Map icon-rail row → sidebar anchor element (DOM order matches sidebar). */
export const RAIL_ROW_ANCHORS = [
  { rail: 'rail-new-session', anchor: 'sidebar-new-chat-btn' },
  { rail: 'rail-search-btn', anchor: 'sidebar-search-btn' },
  { rail: 'rail-chats', anchor: 'sessions-section', anchorSelector: '#sessions-section > .section-header-flex' },
  { rail: 'rail-documents', anchor: 'sidebar-documents-btn', optional: true },
  { rail: 'rail-email', anchor: 'email-section', anchorSelector: '#email-section > .section-header-flex' },
];

/** Tool rail buttons — order matches #tools-section .list-item rows. */
export const RAIL_TOOL_ANCHORS = [
  ['rail-memory', 'tool-memory-btn'],
  ['rail-calendar', 'tool-calendar-btn'],
  ['rail-compare', 'tool-compare-btn'],
  ['rail-scheduler', 'tool-scheduler-btn'],
  ['rail-cookbook', 'tool-cookbook-btn'],
  ['rail-research', 'tool-research-btn'],
  ['rail-fugassa', 'tool-fugassa-btn'],
  ['rail-gallery', 'tool-gallery-btn'],
  ['rail-image-studio', 'tool-image-studio-btn'],
  ['rail-archive', 'tool-library-btn'],
  ['rail-notes', 'tool-notes-btn'],
  ['rail-tasks', 'tool-tasks-btn'],
  ['rail-theme', 'tool-theme-btn'],
];

/** Map icon-rail buttons → sidebar tile SVG source (DOM order matches sidebar). */
export const RAIL_ICON_SOURCES = [
  ['rail-new-session', '#sidebar-new-chat-btn .sidebar-icon-tile svg'],
  ['rail-search-btn', '#sidebar-search-btn .sidebar-icon-tile svg'],
  ['rail-chats', '#chats-section-title .sidebar-icon-tile svg'],
  ['rail-documents', '#sidebar-documents-btn .sidebar-icon-tile svg'],
  ['rail-email', '#email-section-title .sidebar-icon-tile svg'],
  ['rail-memory', '#tool-memory-btn .sidebar-icon-tile svg'],
  ['rail-calendar', '#tool-calendar-btn .sidebar-icon-tile svg'],
  ['rail-compare', '#tool-compare-btn .sidebar-icon-tile svg'],
  ['rail-scheduler', '#tool-scheduler-btn .sidebar-icon-tile svg'],
  ['rail-cookbook', '#tool-cookbook-btn .sidebar-icon-tile svg'],
  ['rail-research', '#tool-research-btn .sidebar-icon-tile svg'],
  ['rail-fugassa', '#tool-fugassa-btn .sidebar-icon-tile svg'],
  ['rail-gallery', '#tool-gallery-btn .sidebar-icon-tile svg'],
  ['rail-image-studio', '#tool-image-studio-btn .sidebar-icon-tile svg'],
  ['rail-archive', '#tool-library-btn .sidebar-icon-tile svg'],
  ['rail-notes', '#tool-notes-btn .sidebar-icon-tile svg'],
  ['rail-tasks', '#tool-tasks-btn .sidebar-icon-tile svg'],
  ['rail-theme', '#tool-theme-btn .sidebar-icon-tile svg'],
  ['rail-settings', '#user-bar-settings svg'],
];
