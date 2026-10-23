// static/js/chatRenderer.js
// Extracted from chat.js — message rendering, sources, images, metrics

import uiModule from './ui.js?v=20260916largetoolscroll1';
import markdownModule from './markdown.js';
import { generatedImageResult } from './generatedImageResult.js';
import { svgifyEmoji } from './markdown.js';
import { addAITTSButton } from './tts-ai.js';
import { providerLogo, providerLabel } from './providers.js';
import settingsModule from './settings.js?v=20260912writingstyle3';
import spinnerModule from './spinner.js';
import { bindMenuDismiss } from './escMenuStack.js';
import { loadPanel } from './panels.js?v=20260909movepicklayer1';
import { matchModelKey } from './model/matchKey.js';
import { getTools } from './appConfig.js';
import { getShowPersonaName } from './presets.js?v=20260908personaname1';

const SEARCH_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>';
const REPORT_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><line x1="10" y1="9" x2="8" y2="9"/></svg>';
const CHAT_ABOUT_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
const COPY_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
const CHECK_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
const TRASH_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>';
const PAPERCLIP_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>';
const CALENDAR_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4"/><path d="M8 2v4"/><path d="M3 10h18"/></svg>';
const EMAIL_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/></svg>';
const TOOL_ICONS = {
  manage_calendar: CALENDAR_ICON,
  manage_memory: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/><path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4"/></svg>',
  save_memory: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 4h16v16H4z"/><path d="M8 4v5h8V4M8 20v-6h8v6"/></svg>',
  search_memory: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg>',
  manage_skills: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
  manage_notes: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 3h10l4 4v14H5z"/><path d="M15 3v5h5"/><path d="M8 17.5 15.5 10l2.5 2.5L10.5 20H8z"/></svg>',
  manage_tasks: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/><path d="M9 16l2 2 4-4"/></svg>',
  trigger_research: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>',
  manage_research: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>',
  manage_documents: REPORT_ICON,
  create_document: REPORT_ICON,
  update_document: REPORT_ICON,
  edit_document: REPORT_ICON,
  read_file: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v5h5"/></svg>',
  write_file: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16v4Z"/><path d="m13.5 6.5 4 4"/></svg>',
  edit_file: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16v4Z"/><path d="m13.5 6.5 4 4"/></svg>',
  private_browser: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>',
  manage_session: CHAT_ABOUT_ICON,
  manage_contact: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>',
  manage_settings: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/><circle cx="12" cy="12" r="4"/></svg>',
  bash: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m4 17 6-5-6-5M12 19h8"/></svg>',
  host_shell: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m4 17 6-5-6-5M12 19h8"/></svg>',
  python: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3c-4 0-4 2-4 4v2h8v2H8c-4 0-4 2-4 5s2 5 5 5h2v-3H9v-2h6c4 0 4-2 4-5V8c0-3-2-5-7-5Z"/><circle cx="10" cy="6" r=".7" fill="currentColor"/><circle cx="14" cy="18" r=".7" fill="currentColor"/></svg>',
  generate_image: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m12 3 1.5 5.5L19 10l-5.5 1.5L12 17l-1.5-5.5L5 10l5.5-1.5L12 3Z"/><path d="m19 16 .7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7L19 16Z"/></svg>',
  image_gen: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m12 3 1.5 5.5L19 10l-5.5 1.5L12 17l-1.5-5.5L5 10l5.5-1.5L12 3Z"/><path d="m19 16 .7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7L19 16Z"/></svg>',
  ask_user: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 4.1 1.9c-1.1.8-1.6 1.2-1.6 2.6M12 17h.01"/></svg>',
  update_plan: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 5h16M4 12h16M4 19h10"/><circle cx="19" cy="19" r="2"/></svg>',
  list_models: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="5" rx="2"/><rect x="3" y="15" width="18" height="5" rx="2"/></svg>',
  youtube_tool: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="4"/><path d="m10 9 5 3-5 3V9Z"/></svg>',
  list_emails: EMAIL_ICON,
  read_email: EMAIL_ICON,
  search_emails: EMAIL_ICON,
  send_email: EMAIL_ICON,
  mcp__email__list_emails: EMAIL_ICON,
  mcp__email__read_email: EMAIL_ICON,
  mcp__email__search_emails: EMAIL_ICON,
  web_search: SEARCH_ICON,
  web_fetch: SEARCH_ICON,
};

export function getToolIcon(tool) {
  const name = String(tool || '').toLowerCase();
  if (TOOL_ICONS[name]) return TOOL_ICONS[name];
  if (name.endsWith('manage_calendar')) return TOOL_ICONS.manage_calendar;
  if (name.endsWith('manage_memory')) return TOOL_ICONS.manage_memory;
  if (name.endsWith('manage_skills')) return TOOL_ICONS.manage_skills;
  if (name.endsWith('manage_notes')) return TOOL_ICONS.manage_notes;
  if (name.endsWith('manage_tasks')) return TOOL_ICONS.manage_tasks;
  if (name.includes('email')) return EMAIL_ICON;
  if (name.includes('browser')) return TOOL_ICONS.private_browser;
  return '';
}
const RESEND_ICON = '<svg class="resend-message-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.15" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 0 1-15.3 6.4L3 16"/><path d="M3 21v-5h5"/><path d="M3 12a9 9 0 0 1 15.3-6.4L21 8"/><path d="M21 3v5h-5"/></svg>';
const USER_MODE_AGENT_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';
const USER_MODE_CHAT_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';

/** Sanitize a URL for use in href — only allow http(s) and protocol-relative. */
function _safeHref(url) {
  if (!url) return '#';
  try {
    var parsed = new URL(url, window.location.origin);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return uiModule.esc(url);
  } catch(e) { /* invalid URL */ }
  return '#';
}

export function safeToolScreenshotSrc(raw) {
  const src = String(raw || '').trim();
  if (/^data:image\/(?:png|jpe?g|gif|webp);base64,[a-z0-9+/=\s]+$/i.test(src)) {
    return src;
  }
  return '';
}

export function safeDisplayImageSrc(raw) {
  const src = String(raw || '').trim();
  if (!src) return '';
  if (/^data:image\/(?:png|jpe?g|gif|webp);base64,[a-z0-9+/=\s]+$/i.test(src)) {
    return src;
  }
  try {
    const parsed = new URL(src, window.location.origin);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
      return parsed.href;
    }
  } catch (_) {}
  return '';
}

function _isPrivateBrowserTool(tool) {
  const name = String(tool || '').toLowerCase();
  return name === 'private_browser'
    || name.includes('private_browser')
    || name.includes('builtin_browser')
    || name.startsWith('browser_')
    || name.includes('__browser_');
}

function _privateBrowserActionLabel(command) {
  const raw = String(command || '').trim();
  if (!raw) return 'screenshot';
  try {
    const parsed = JSON.parse(raw);
    const action = String(parsed && parsed.action || '').trim();
    if (action) {
      const target = parsed.url || parsed.selector || parsed.target || parsed.key || '';
      return target ? `${action}: ${String(target).slice(0, 90)}` : action;
    }
  } catch (_) {}
  return raw.slice(0, 110);
}

function _privateBrowserUrlFromCommand(command) {
  const raw = String(command || '').trim();
  if (!raw) return '';
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.url === 'string' && parsed.url) return parsed.url;
    const commands = Array.isArray(parsed && parsed.commands) ? parsed.commands : [];
    for (const item of commands) {
      if (Array.isArray(item) && String(item[0] || '').toLowerCase() === 'open' && item[1]) return String(item[1]);
      if (item && typeof item === 'object' && String(item.action || '').toLowerCase() === 'open' && item.url) return String(item.url);
    }
  } catch (_) {}
  return '';
}

function _safeExternalToolUrl(raw) {
  const value = String(raw || '').trim();
  if (!value) return '';
  try {
    const parsed = new URL(value.includes('://') ? value : `https://${value}`);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return parsed.href;
  } catch (_) {}
  return '';
}

function _webFetchUrlFromCommand(command) {
  const raw = String(command || '').trim();
  if (!raw) return '';
  try {
    const args = JSON.parse(raw);
    return _safeExternalToolUrl(args && args.url);
  } catch (_) {
    return _safeExternalToolUrl(raw.split(/\s+/)[0]);
  }
}

function _webSearchQueryFromCommand(command) {
  const raw = String(command || '').trim();
  if (!raw) return '';
  try {
    const args = JSON.parse(raw);
    if (args && typeof args.query === 'string') return args.query.trim();
    if (args && Array.isArray(args.queries) && args.queries.length) return String(args.queries[0] || '').trim();
  } catch (_) {}
  return raw;
}

function _searxngSearchUrl(query) {
  const q = String(query || '').trim();
  if (!q) return '';
  try {
    const url = new URL('/search/web', window.location.origin);
    url.search = '';
    url.hash = '';
    url.searchParams.set('q', q);
    return url.href;
  } catch (_) {
    return '';
  }
}

function _toolHeaderLinkHtml(url, title = 'Open link') {
  const href = _safeExternalToolUrl(url);
  if (!href) return '';
  return `<a class="agent-thread-header-link" href="${uiModule.esc(href)}" target="_blank" rel="noopener noreferrer" title="${uiModule.esc(title)}" aria-label="${uiModule.esc(title)}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg></a>`;
}

function _toolHeaderHashLinkHtml(hash, title = 'Open item', iconHtml = '', className = '') {
  const href = String(hash || '').trim();
  if (!/^#(?:event|note|email|document|task|research|skill)-[A-Za-z0-9_.:-]+$/.test(href)) return '';
  const cls = `agent-thread-header-link${className ? ' ' + className : ''}`;
  return `<a class="${uiModule.esc(cls)}" href="${uiModule.esc(href)}" title="${uiModule.esc(title)}" aria-label="${uiModule.esc(title)}">${iconHtml || '<span aria-hidden="true">↗</span>'}</a>`;
}

function _calendarEventUidFromToolData(command, data) {
  const direct = String((data && data.uid) || '').trim();
  if (direct) return direct;
  const events = data && Array.isArray(data.events) ? data.events : [];
  if (events.length === 1 && events[0] && events[0].uid) return String(events[0].uid).trim();
  const text = `${command || ''}\n${(data && data.output) || ''}\n${(data && data.anchor) || ''}`;
  const match = text.match(/#event-([A-Za-z0-9_.:-]+)/);
  return match ? match[1] : '';
}

function _safeToolTargetId(value) {
  const id = String(value || '').trim();
  return /^[A-Za-z0-9_.:@-]{2,160}$/.test(id) ? id : '';
}

function _hashTargetFromText(text, kind) {
  const match = String(text || '').match(new RegExp(`#${kind}-([A-Za-z0-9_.:@-]+)`));
  return match ? _safeToolTargetId(match[1]) : '';
}

function _toolOutputText(data) {
  if (!data) return '';
  return [data.output, data.response, data.results, data.content, data.anchor]
    .filter(value => value !== undefined && value !== null)
    .map(String)
    .join('\n');
}

function _target(kind, id, title) {
  const safeId = _safeToolTargetId(id);
  return safeId ? { href: `#${kind}-${safeId}`, title } : null;
}

function _emailTarget(args = {}, data = null) {
  const uid = String((data && data.uid) || args.uid || '').trim();
  if (!/^\d+$/.test(uid)) return null;
  const folder = String((data && data.folder) || args.folder || 'INBOX').trim() || 'INBOX';
  const account = String(
    (data && (data.account_id || data.account)) || args.account_id || args.account || '',
  ).trim();
  const clean = value => String(value || '').replace(/[^A-Za-z0-9_.@-]/g, '_');
  return _target('email', [clean(folder), uid, clean(account)].filter(Boolean).join(':'), 'Open email');
}

/** Resolve the in-app item a tool icon should open, when the tool exposed one. */
export function getToolActionTarget(tool, command, data = null) {
  const lower = String(tool || '').toLowerCase();
  let args = null;
  try { args = JSON.parse(command || '{}'); } catch (_) {}
  const output = _toolOutputText(data);

  if (lower === 'update_plan' || lower.endsWith('update_plan')) {
    return { href: '#plan', title: 'Review plan' };
  }
  if (lower === 'web_search' || lower.endsWith('web_search') || lower.includes('__web_search')) {
    return null;
  }
  if (lower === 'web_fetch' || lower.endsWith('web_fetch') || lower.includes('__web_fetch')) {
    const href = _safeExternalToolUrl(_webFetchUrlFromCommand(command));
    return href ? { href, title: 'Open fetched page', external: true } : null;
  }
  if (lower === 'manage_calendar' || lower.endsWith('manage_calendar')) {
    const uid = _calendarEventUidFromToolData(command, data || {});
    if (uid) return _target('event', uid, 'Open calendar event');
    const start = args && args.start ? String(args.start).slice(0, 10) : '';
    return /^\d{4}-\d{2}(?:-\d{2})?$/.test(start)
      ? { href: `#event-${start}`, title: 'Open calendar range' }
      : { href: '#calendar', title: 'Open calendar' };
  }

  const targetDefinitions = [
    { test: /(?:^|__)manage_memory$|^(?:save_memory|search_memory)$/, kind: 'memory', title: 'Open memory', fields: ['memory_id', 'id'], fallback: 'memory' },
    { test: /(?:^|__)manage_notes$/, kind: 'note', title: 'Open note', fields: ['note_id', 'id'], fallback: 'notes' },
    { test: /(?:^|__)manage_tasks$/, kind: 'task', title: 'Open task', fields: ['task_id', 'id'], fallback: 'tasks' },
    { test: /(?:^|__)manage_documents$|^(?:create|update|edit)_document$/, kind: 'document', title: 'Open document', fields: ['doc_id', 'document_id', 'id'], fallback: 'documents' },
    { test: /(?:^|__)manage_skills$/, kind: 'skill', title: 'Open skill', fields: ['skill_name', 'name', 'skill_id'], fallback: 'skills' },
    { test: /(?:^|__)manage_research$|^trigger_research$/, kind: 'research', title: 'Open research', fields: ['research_session_id', 'session_id', 'research_id', 'id'], fallback: 'research' },
  ];
  const definition = targetDefinitions.find(item => item.test.test(lower));
  if (definition) {
    for (const field of definition.fields) {
      const candidate = data && data[field] !== undefined ? data[field] : args && args[field];
      const result = _target(definition.kind, candidate, definition.title);
      if (result) return result;
    }
    const fromAnchor = _hashTargetFromText(output, definition.kind);
    if (fromAnchor) return _target(definition.kind, fromAnchor, definition.title);
    if (definition.kind === 'task') {
      const taskMatch = output.match(/\(([0-9a-f]{8}-[0-9a-f-]{27,})\)/i);
      if (taskMatch) return _target('task', taskMatch[1], definition.title);
    }
    if (definition.kind === 'note') {
      const noteMatch = output.match(/[-*]\s*\[([A-Za-z0-9_.:@-]{2,160})\]\s+\*\*/);
      if (noteMatch) return _target('note', noteMatch[1], definition.title);
    }
    if (definition.kind === 'memory') {
      const memoryMatch = output.match(/`([A-Za-z0-9_-]{4,160})`/);
      if (memoryMatch) return _target('memory', memoryMatch[1], definition.title);
    }
    return { href: `#${definition.fallback}`, title: `Open ${definition.fallback}` };
  }

  if (lower.includes('read_email') || lower.endsWith('email')) {
    return _emailTarget(args || {}, data) || _target('email', _hashTargetFromText(output, 'email'), 'Open email');
  }
  if (lower === 'generate_image' || lower.endsWith('image_gen')) {
    return _target('image', data?.image_id || args?.image_id, 'Open image');
  }
  if (lower === 'manage_session' || lower.endsWith('manage_session')) {
    return _target('session', data?.session_id || args?.session_id || _hashTargetFromText(output, 'session'), 'Open session');
  }
  return null;
}

export function renderToolIcon(tool, command, data = null) {
  const icon = getToolIcon(tool);
  if (!icon) return '';
  const target = getToolActionTarget(tool, command, data);
  if (!target) return `<span class="agent-thread-tool-icon">${icon}</span>`;
  const href = target.external ? _safeHref(target.href) : uiModule.esc(target.href);
  const externalAttrs = target.external ? ' target="_blank" rel="noopener noreferrer"' : '';
  return `<a class="agent-thread-tool-icon agent-thread-tool-icon-link" href="${href}" title="${uiModule.esc(target.title)}" aria-label="${uiModule.esc(target.title)}"${externalAttrs}>${icon}</a>`;
}

function _browserPreviewHtml(command, screenshotSrc, fallbackUrl = '') {
  const url = _privateBrowserUrlFromCommand(command) || String(fallbackUrl || '');
  const urlAttr = url ? ` data-browser-url="${uiModule.esc(url)}" title="Open ${uiModule.esc(url)}"` : '';
  const imgHtml = screenshotSrc
    ? `<img class="private-browser-preview-img" src="${uiModule.esc(screenshotSrc)}" alt="Private browser screenshot" />`
    : '<div class="private-browser-preview-empty"><span class="private-browser-preview-spinner" aria-hidden="true"></span><span>Browsing…</span></div><img class="private-browser-preview-img" alt="Private browser screenshot" />';
  return `<div class="private-browser-preview"${urlAttr}><div class="private-browser-preview-header"><span class="private-browser-preview-title">Private Browser</span><span class="private-browser-preview-status">${uiModule.esc(_privateBrowserActionLabel(command))}</span><button type="button" class="private-browser-preview-fold" title="Fold browser preview" aria-label="Fold browser preview">×</button></div><div class="private-browser-preview-frame">${imgHtml}</div></div>`;
}

function _renderAssistantBody(text, metadata) {
  const suppressThinking = !!(metadata && metadata.character_name);
  if (suppressThinking) {
    const extracted = markdownModule.extractThinkingBlocks(text || '');
    const visible = (extracted && typeof extracted.content === 'string') ? extracted.content : String(text || '');
    return markdownModule.mdToHtml(markdownModule.squashOutsideCode(visible));
  }
  return markdownModule.processWithThinking(text);
}

function _toolDisplayInfo(tool, command, data = null) {
  const rawTool = String(tool || '');
  const lower = rawTool.toLowerCase();
  let args = null;
  try { args = JSON.parse(command || '{}'); } catch (_) {}
  if (lower === 'web_search' || lower.endsWith('web_search') || lower.includes('__web_search')) {
    const q = _webSearchQueryFromCommand(command);
    return {
      label: '',
      commandHtml: q
        ? `<span class="agent-thread-summary">${uiModule.esc(q)}</span>`
        : '',
    };
  }
  if (lower === 'web_fetch' || lower.endsWith('web_fetch') || lower.includes('__web_fetch')) {
    const url = _webFetchUrlFromCommand(command);
    return {
      label: '',
      headerActionHtml: _toolHeaderLinkHtml(url, 'Open fetched page'),
      commandHtml: url
        ? `<a class="agent-thread-summary agent-thread-summary-link" href="${uiModule.esc(url)}" target="_blank" rel="noopener noreferrer" title="Open ${uiModule.esc(url)}">${uiModule.esc(url)}</a>`
        : '',
    };
  }
  if (lower === 'manage_calendar' || lower.endsWith('manage_calendar')) {
    const action = args && args.action ? String(args.action).toLowerCase() : '';
    const summary = args && args.summary ? String(args.summary) : '';
    const start = args && args.start ? String(args.start).slice(0, 10) : '';
    const end = args && args.end ? String(args.end).slice(0, 10) : '';
    const uid = _calendarEventUidFromToolData(command, data || {});
    const actionLabel = action.includes('create') ? 'Create event'
      : action.includes('update') ? 'Update event'
      : action.includes('delete') ? 'Delete event'
      : 'Check calendar';
    const rangeSummary = start && end ? `${start} to ${end}` : '';
    return {
      label: actionLabel,
      // The calendar glyph itself is the navigation target. Keeping the old
      // trailing action link here produced two calendar icons in one row.
      headerActionHtml: '',
      commandHtml: summary
        ? `<div class="agent-thread-summary">${uiModule.esc(summary)}</div>`
        : rangeSummary
          ? `<div class="agent-thread-summary">${uiModule.esc(rangeSummary)}</div>`
        : '',
    };
  }
  const emailTool = lower.includes('email');
  if (emailTool && (lower.includes('read_email') || lower.endsWith('read_email'))) {
    const uid = args && args.uid ? String(args.uid) : '';
    const folder = args && args.folder ? String(args.folder) : 'INBOX';
    const target = _emailTarget(args || {}, data);
    return {
      label: uid ? `Read email UID ${uid}` : 'Read email',
      headerActionHtml: target
        ? _toolHeaderHashLinkHtml(target.href, 'Open this email')
        : '',
      commandHtml: uid
        ? `<a class="agent-thread-summary agent-thread-summary-link" href="${uiModule.esc(target ? target.href : `#email-${uid}`)}" title="Open this email">UID ${uiModule.esc(uid)} · ${uiModule.esc(folder)}</a>`
        : '',
    };
  }
  if (emailTool && (lower.includes('list_emails') || lower.endsWith('list_emails'))) {
    const max = args && args.max_results ? String(args.max_results) : '';
    const folder = args && args.folder ? String(args.folder) : 'INBOX';
    const bits = [folder, max ? `${max} latest` : 'latest'].filter(Boolean).join(' · ');
    return {
      label: 'Check email',
      commandHtml: `<div class="agent-thread-summary">${uiModule.esc(bits)}</div>`,
    };
  }
  if (emailTool && (lower.includes('search_emails') || lower.endsWith('search_emails'))) {
    const q = args && args.query ? String(args.query) : '';
    return {
      label: 'Search email',
      commandHtml: q ? `<div class="agent-thread-summary">${uiModule.esc(q)}</div>` : '',
    };
  }
  return { label: '', commandHtml: '' };
}

function _suppressRawToolOutput(tool, ok) {
  if (!ok) return false;
  const lower = String(tool || '').toLowerCase();
  if (lower === 'manage_calendar' || lower.endsWith('manage_calendar')) return true;
  return (
    lower.includes('email')
    && (
      lower.includes('list_emails')
      || lower.includes('search_emails')
      || lower.includes('read_email')
      || lower.includes('download_attachment')
      || lower.includes('scan_spam')
      || lower.includes('scan_email_unsubscribes')
    )
  );
}

function _makeActionBtn(className, title, text, handler) {
  const btn = document.createElement('button');
  btn.className = className;
  btn.type = 'button';
  btn.title = title;
  btn.textContent = text;
  btn.addEventListener('click', handler);
  return btn;
}

// Attachment card helpers
function _attachIcon(mimeOrName) {
  const s = (mimeOrName || '').toLowerCase();
  if (s.startsWith('image/') || /\.(png|jpg|jpeg|gif|webp|svg)$/i.test(s))
    return '<svg class="attach-card-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>';
  if (s.startsWith('audio/') || /\.(mp3|wav|ogg|m4a|webm)$/i.test(s))
    return '<svg class="attach-card-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>';
  if (s === 'application/pdf' || /\.pdf$/i.test(s))
    return '<svg class="attach-card-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>';
  // Default: generic document
  return '<svg class="attach-card-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
}
function _formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}

// Build the `.attach-cards` element for a message's attachment list. Shared by
// addMessage and updateMessageAttachments so a live (optimistic) user bubble
// can be re-rendered with real upload ids once the upload resolves.
export function buildAttachCards(attachments) {
  const attachWrap = document.createElement('div');
  attachWrap.className = 'attach-cards';
  for (const att of attachments) {
    const isImage = (att.mime || '').startsWith('image/') || /\.(png|jpg|jpeg|gif|webp|svg|bmp)$/i.test(att.name || '');
    if (isImage) {
      // Image preview. Shown for both uploaded (att.id present) and still-
      // uploading attachments. A shimmering skeleton + whirlpool fills the
      // space until either the upload resolves (no id yet) or the thumbnail
      // image finishes loading, so the photo doesn't pop in abruptly.
      const imgWrap = document.createElement('div');
      imgWrap.className = 'attach-image-preview';
      imgWrap.style.cursor = att.id ? 'zoom-in' : 'default';
      if (att.id) imgWrap.dataset.fileId = att.id;
      if (att.id) {
        imgWrap.addEventListener('click', (e) => {
          // Tapping the corner OCR button shouldn't also open the lightbox.
          if (e.target.closest('.attach-ocr-btn')) return;
          _openImageLightbox(att);
        });
      }

      let skel = null;
      let sp = null;
      if (!att.previewUrl) {
        // Skeleton placeholder with a centered whirlpool. Self-stops when removed.
        skel = document.createElement('div');
        skel.className = 'attach-image-skeleton';
        // Match the photo's aspect ratio when the backend knew it at upload
        // time, so the skeleton doesn't sit at a 4:3 default and then snap to
        // a portrait shape when the image arrives.
        if (att.width && att.height) {
          skel.style.aspectRatio = att.width + ' / ' + att.height;
          skel.style.width = 'auto';
          skel.style.height = 'auto';
          skel.style.maxWidth = '300px';
          skel.style.maxHeight = '200px';
          skel.style.minWidth = '80px';
        }
        sp = spinnerModule.createWhirlpool(20);
        skel.appendChild(sp.element);
        imgWrap.appendChild(skel);
      }

      if (att.id || att.previewUrl) {
        const img = document.createElement('img');
        // Small cached thumbnail — the preview is tiny, no need to pull the
        // full-resolution photo. Click still opens the full image.
        img.alt = att.name || 'Image';
        img.loading = 'lazy';
        img.style.cssText = 'max-width:300px;max-height:200px;border-radius:6px;display:' + (att.previewUrl ? 'block' : 'none') + ';';
        let _revealed = false;
        let _revealTimer = null;
        const _reveal = () => {
          if (_revealed) return;
          _revealed = true;
          if (_revealTimer) { clearTimeout(_revealTimer); _revealTimer = null; }
          img.style.display = 'block';
          try { sp && sp.stop(); } catch {}
          if (skel) skel.remove();
        };
        img.addEventListener('load', _reveal);
        img.addEventListener('error', _reveal);
        img.src = att.previewUrl || `/api/upload/${att.id}?thumb=1`;
        // Cached images can be complete before the load listener attaches.
        if (img.complete && img.naturalWidth) _reveal();
        // Failsafe: if neither load nor error fires within 8s, reveal anyway.
        // The timer is cleared on reveal AND when updateMessageAttachments
        // replaces the card (which scrubs the img / skel from the DOM), so
        // repeated re-renders don't accumulate stranded timers.
        if (!att.previewUrl) _revealTimer = setTimeout(_reveal, 8000);
        imgWrap.appendChild(img);

        if (att.id) {
          // Small corner button → opens the vision/OCR editor so the user can
          // correct what the vision model extracted. The edit is cached on the
          // server keyed by file id, so any later message referencing this same
          // image picks up the corrected text instead of re-running the model.
          const ocrBtn = document.createElement('button');
          ocrBtn.type = 'button';
          ocrBtn.className = 'attach-ocr-btn';
          ocrBtn.title = 'View / edit OCR text';
          ocrBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg><span class="attach-ocr-label">Caption</span>';
          ocrBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            _openVisionEditor(att, ocrBtn.closest('.msg'));
          });
          imgWrap.appendChild(ocrBtn);
        }
      }

      if (att.vision_model) {
        const visionLabel = document.createElement('div');
        visionLabel.className = 'attach-vision-model';
        visionLabel.textContent = 'Vision: ' + String(att.vision_model).split('/').pop();
        imgWrap.appendChild(visionLabel);
      }
      if (att.name) {
        const label = document.createElement('div');
        label.className = 'attach-image-name';
        label.textContent = att.name;
        imgWrap.appendChild(label);
      }
      attachWrap.appendChild(imgWrap);
    } else {
      // Non-image file card
      const card = document.createElement('div');
      card.className = 'attach-card';
      card.dataset.name = att.name;
      if (att.id) {
        card.dataset.fileId = att.id;
        card.style.cursor = 'pointer';
        card.addEventListener('click', () => {
          // PDFs & text/code/markdown → open in the Documents viewer
          // (others fall back to the raw file).
          if (window.chatModule?.openAttachment) window.chatModule.openAttachment(att, false);
          else window.open(`/api/upload/${att.id}`, '_blank');
        });
      }
      const icon = _attachIcon(att.mime || att.name);
      const nameSpan = document.createElement('span');
      nameSpan.className = 'attach-card-name';
      nameSpan.textContent = att.name;
      card.innerHTML = icon;
      card.appendChild(nameSpan);
      if (att.size) {
        const sizeSpan = document.createElement('span');
        sizeSpan.className = 'attach-card-size';
        sizeSpan.textContent = _formatSize(att.size);
        card.appendChild(sizeSpan);
      }
      attachWrap.appendChild(card);
    }
  }
  return attachWrap;
}

// Re-render the attachment cards of an already-rendered message. Used to swap
// in real upload ids (and image thumbnails) on the optimistic user bubble once
// uploadPending() resolves — otherwise image previews only appear after a
// refresh, because the bubble is rendered before the upload assigns ids.
export function updateMessageAttachments(msgWrap, attachments) {
  if (!msgWrap || !attachments?.length) return;
  const body = msgWrap.querySelector('.body') || msgWrap;
  const existing = body.querySelector('.attach-cards');
  const fresh = buildAttachCards(attachments);
  if (existing) existing.replaceWith(fresh);
  else body.appendChild(fresh);
}

// Quick full-size preview when the user taps a chat photo thumbnail. Just an
// overlay with the original image centered — no Gallery panel, no editor.
function _openImageLightbox(att) {
  if (!att?.id) return;
  const overlay = document.createElement('div');
  overlay.className = 'attach-lightbox';
  // Show the cached thumb immediately so the overlay doesn't sit blank
  // while a 25MB original streams in. The full image swaps in once loaded;
  // if the full load fails (404 / network), we keep the thumb + show an
  // error label rather than a blank overlay forever.
  const img = document.createElement('img');
  img.alt = att.name || '';
  img.src = `/api/upload/${att.id}?thumb=1`;
  overlay.appendChild(img);
  const full = new Image();
  full.addEventListener('load', () => { img.src = full.src; });
  full.addEventListener('error', () => {
    const err = document.createElement('div');
    err.className = 'attach-lightbox-err';
    err.textContent = 'Failed to load full-resolution image.';
    overlay.appendChild(err);
  });
  full.src = `/api/upload/${att.id}`;

  const _onKey = (e) => { if (e.key === 'Escape') _close(); };
  const _close = () => {
    document.removeEventListener('keydown', _onKey);
    if (_overlayObs) { try { _overlayObs.disconnect(); } catch {} }
    overlay.remove();
  };
  // If the overlay is removed via any path other than our close handler
  // (session switch, parent re-render, external cleanup), still drop the
  // document-level keydown listener so it doesn't leak.
  let _overlayObs = null;
  try {
    _overlayObs = new MutationObserver(() => {
      if (!document.body.contains(overlay)) {
        document.removeEventListener('keydown', _onKey);
        _overlayObs.disconnect();
      }
    });
    _overlayObs.observe(document.body, { childList: true, subtree: false });
  } catch {}
  overlay.addEventListener('click', _close);
  document.addEventListener('keydown', _onKey);
  document.body.appendChild(overlay);
}

// Vision/OCR editor modal — opened from the corner "Aa" button on a chat photo
// thumbnail. Lets the user view and correct the text the vision model fed to
// the LLM (e.g. when OCR misreads a word). Persists to the server's vision
// cache (PUT /api/upload/{id}/vision), so any subsequent message that
// references the same file picks up the corrected text.
let _visionEditorEl = null;
let _visionEditorEsc = null;
function _closeVisionEditor() {
  if (_visionEditorEsc) { document.removeEventListener('keydown', _visionEditorEsc); _visionEditorEsc = null; }
  if (_visionEditorEl) { _visionEditorEl.remove(); _visionEditorEl = null; }
}
function _openVisionEditor(att, userMsgEl) {
  if (!att?.id) return;
  _closeVisionEditor();
  const overlay = document.createElement('div');
  overlay.className = 'vision-editor-overlay';
  overlay.addEventListener('click', (e) => { if (e.target === overlay) _closeVisionEditor(); });
  const panel = document.createElement('div');
  panel.className = 'vision-editor-panel';
  const title = document.createElement('div');
  title.className = 'vision-editor-title';
  // Eye icon matches the one in Settings → Vision so users recognise where
  // this text originates.
  title.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.7;flex-shrink:0"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg><span>Vision text</span>';
  panel.appendChild(title);
  const desc = document.createElement('div');
  desc.className = 'vision-editor-desc';
  desc.textContent = 'Edit text and save, new chats will have the new context. Regenerate or continue from there.';
  panel.appendChild(desc);
  const ta = document.createElement('textarea');
  ta.className = 'vision-editor-text';
  ta.rows = 10;
  ta.placeholder = 'Loading…';
  ta.disabled = true;
  panel.appendChild(ta);
  const actions = document.createElement('div');
  actions.className = 'vision-editor-actions';
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'vision-editor-btn';
  closeBtn.innerHTML = '<span class="vision-btn-label">Close</span>';
  closeBtn.addEventListener('click', _closeVisionEditor);
  const _saveVisionText = async () => {
    const res = await fetch(`/api/upload/${att.id}/vision`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ text: ta.value }),
    });
    if (!res.ok) throw new Error('save failed');
  };
  const saveBtn = document.createElement('button');
  saveBtn.type = 'button';
  saveBtn.className = 'vision-editor-btn vision-editor-btn-primary';
  saveBtn.innerHTML = '<span class="vision-btn-label">Save</span>';
  saveBtn.disabled = true;
  saveBtn.addEventListener('click', async () => {
    saveBtn.disabled = true;
    saveBtn.innerHTML = '<span class="vision-btn-label">Saving…</span>';
    try {
      await _saveVisionText();
      if (uiModule?.showToast) uiModule.showToast('Saved');
      _closeVisionEditor();
    } catch (e) {
      saveBtn.disabled = false;
      saveBtn.innerHTML = '<span class="vision-btn-label">Save</span>';
      if (uiModule?.showError) uiModule.showError('Failed to save OCR text');
    }
  });
  // Regenerate-message: save the edited text, close, then trigger a resend of
  // the user message so the new AI reply uses the edit immediately.
  const regenBtn = document.createElement('button');
  regenBtn.type = 'button';
  regenBtn.className = 'vision-editor-btn vision-editor-btn-primary';
  regenBtn.title = 'Save and regenerate the message';
  regenBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.74 9.74 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg><span class="vision-btn-label">Regenerate message</span>';
  regenBtn.disabled = true;
  regenBtn.addEventListener('click', async () => {
    regenBtn.disabled = true;
    saveBtn.disabled = true;
    try {
      await _saveVisionText();
      _closeVisionEditor();
      if (userMsgEl && window.chatModule?.resendUserMessage) {
        window.chatModule.resendUserMessage(userMsgEl, { replaceFromHere: true });
      } else if (uiModule?.showToast) {
        uiModule.showToast('Saved');
      }
    } catch (e) {
      regenBtn.disabled = false;
      saveBtn.disabled = false;
      if (uiModule?.showError) uiModule.showError('Failed to save OCR text');
    }
  });
  // Re-run OCR: ask the server to re-analyse the image with the currently
  // configured Vision model (force=1 bypasses the cached description) and
  // drop the result into the textarea. The endpoint persists the new text
  // server-side, so this does not need a separate Save; chat history is
  // untouched either way.
  const rerunBtn = document.createElement('button');
  rerunBtn.type = 'button';
  rerunBtn.className = 'vision-editor-btn';
  rerunBtn.title = 'Re-run the vision model on this image';
  rerunBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg><span class="vision-btn-label">Re-run</span>';
  rerunBtn.disabled = true;
  rerunBtn.addEventListener('click', async () => {
    const prevLabel = rerunBtn.querySelector('.vision-btn-label').textContent;
    rerunBtn.disabled = true;
    saveBtn.disabled = true;
    regenBtn.disabled = true;
    ta.disabled = true;
    rerunBtn.querySelector('.vision-btn-label').textContent = 'Running…';
    try {
      const r = await fetch(`/api/upload/${att.id}/vision?force=1`, { credentials: 'same-origin' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      ta.value = data.text || '';
      if (uiModule?.showToast) uiModule.showToast('Re-ran vision model');
    } catch (e) {
      if (uiModule?.showError) uiModule.showError('Re-run failed: ' + e.message);
    } finally {
      rerunBtn.querySelector('.vision-btn-label').textContent = prevLabel;
      rerunBtn.disabled = false;
      saveBtn.disabled = false;
      regenBtn.disabled = !userMsgEl;
      ta.disabled = false;
      ta.focus();
    }
  });
  actions.appendChild(closeBtn);
  actions.appendChild(rerunBtn);
  actions.appendChild(saveBtn);
  actions.appendChild(regenBtn);
  panel.appendChild(actions);
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  _visionEditorEl = overlay;

  // ESC closes the popup. Registered on document so it works regardless of
  // focus (the textarea swallows the event otherwise).
  _visionEditorEsc = (e) => { if (e.key === 'Escape') _closeVisionEditor(); };
  document.addEventListener('keydown', _visionEditorEsc);

  fetch(`/api/upload/${att.id}/vision`, { credentials: 'same-origin' })
    .then(r => r.ok ? r.json() : Promise.reject(r))
    .then(data => {
      ta.value = data.text || '';
      ta.placeholder = '';
      ta.disabled = false;
      saveBtn.disabled = false;
      regenBtn.disabled = !userMsgEl;
      rerunBtn.disabled = false;
      ta.focus();
    })
    .catch(() => {
      ta.value = '';
      ta.placeholder = 'Could not load OCR text — type your correction and save.';
      ta.disabled = false;
      saveBtn.disabled = false;
      regenBtn.disabled = !userMsgEl;
      rerunBtn.disabled = false;
    });
}

// Tool call syntax patterns to strip from displayed text
const TOOL_CALL_RE = /\[TOOL_CALL\][\s\S]*?\[\/TOOL_CALL\]/gi;
// Strip fenced tool-call blocks that look like structured invocations, not
// regular code examples. The tool tags are NOT hard-coded here — they are the
// backend's authoritative TOOL_TAGS set, fetched once from GET /api/tools and
// built into EXEC_FENCE_RE at load. TOOL_TAGS (src/agent_tools/__init__.py) is
// thus the single source: the live-strip list can never drift from the backend
// or miss a future tool (#3993). bash/python are carved out on purpose — they
// are languages a user may legitimately have asked the model to show, not tool
// invocations.
//
// Until the fetch resolves, EXEC_FENCE_RE stays null and exec fences aren't
// stripped — normally a sub-second window before the first stream. If the fetch
// fails it stays null for the rest of the session (logged below), so live exec
// fences won't be stripped until reload. Either way the backend already strips
// persisted history (src/tool_parsing.py builds the same regex from TOOL_TAGS),
// so a reload always renders clean.
let EXEC_FENCE_RE = null;
const EXEC_FENCE_NON_TOOL = new Set(['bash', 'python']);

function escapeRegex(source) {
  return String(source).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function stripExecutedFence(match, tag, inline, body) {
  const inlineArgs = (inline || '').trim();
  if (!inlineArgs) return '';
  const bodyText = (body || '').trim();
  const content = bodyText ? `${inlineArgs}\n${bodyText}` : inlineArgs;
  try {
    JSON.parse(content);
  } catch {
    return match;
  }
  return '';
}

async function loadExecFenceRegex() {
  try {
    // Shared with admin.js, and — more to the point — with the other copies of
    // this module: chatRenderer.js is imported under three different ?v= query
    // strings, so it is instantiated three times per load and used to issue
    // three identical /api/tools requests. appConfig.js is imported by one
    // specifier from all of them, so they now share a single fetch.
    const data = await getTools();
    const tags = (data.tools || [])
      .map((t) => t.id)
      .filter((id) => id && !EXEC_FENCE_NON_TOOL.has(id));
    if (tags.length) {
      EXEC_FENCE_RE = new RegExp(
        '```(' + tags.map(escapeRegex).join('|') + ')(?![\\w-])' +
        '[ \\t]*([\\[{][^\\n]*?)?[ \\t]*(?=\\r?\\n|```)' +
        '\\r?\\n?([\\s\\S]*?)```',
        'gi'
      );
    }
  } catch (err) {
    // Surface the failure rather than swallowing it: EXEC_FENCE_RE stays null,
    // so this session won't strip live exec fences until reload (persisted path
    // stays clean regardless).
    console.warn('chatRenderer: /api/tools fetch failed; live exec-fence stripping disabled until reload', err);
  }
}
loadExecFenceRegex();
// XML-style tool calls: <minimax:tool_call>, <tool_call>, <function_call>, bare <invoke>
const XML_TOOL_CALL_RE = /<(?:[\w]+:)?(?:tool_call|function_call)>[\s\S]*?<\/(?:[\w]+:)?(?:tool_call|function_call)>/gi;
const XML_INVOKE_RE = /<invoke\s+name=['"][^'"]*['"]>[\s\S]*?<\/invoke>/gi;
// DeepSeek "DSML" tool-call markup (fullwidth-pipe ｜ or ascii | delimited) that
// leaks into content when the model emits a text tool call instead of a native
// one. Strip the whole block; the second pattern catches stray/partial tags
// (e.g. mid-stream before the closing tag arrives).
const DSML_TOOL_RE = /<\s*[｜|]+\s*DSML\s*[｜|]+\s*tool_calls\s*>[\s\S]*?(?:<\s*\/\s*[｜|]+\s*DSML\s*[｜|]+\s*tool_calls\s*>|$)/gi;
const DSML_STRAY_RE = /<\s*\/?\s*[｜|]+\s*DSML\s*[｜|]+[^>]*>/gi;
const DSML_INVOKE_RE = /<\s*[｜|]+\s*DSML\s*[｜|]+\s*invoke\b[^>]*>[\s\S]*?(?:<\s*\/\s*[｜|]+\s*DSML\s*[｜|]+\s*invoke\s*>|$)/gi;
const RAW_OPENAI_TOOL_JSON_RE = /(?:\[\s*)?\{\s*"function"\s*:\s*\{[\s\S]*?\}\s*,\s*"id"\s*:\s*"[^"]*"\s*,\s*"type"\s*:\s*"function"\s*\}\s*\]?/gi;
const QWEN_ROLE_MARKER_RE = /<\/?\|(?:assistant|assistan|user|system|tool)\|>?|<\/\|end\|>?/gi;
// Keep in sync with _QWEN_BARE_MARKER_RE in src/tool_parsing.py. At least one
// pipe is required around `end`: with both optional (`\|?end\|?`) this also ate
// a bare `end` on its own line, breaking Ruby/Lua/shell snippets (#5547).
const QWEN_BARE_MARKER_RE = /(?:^|[\t\r\n ])(?:\/?\|end\||\|end|end\|)(?=[\t\r\n ]|$)|(?:^|[\r\n])[ \t]*assistan(?:t)?[ \t]*(?=[\r\n]|$)/gi;
// Self-narration about tool results (model echoing stdout/exit_code)
const TOOL_NARRATION_RE = /(?:The (?:result|output) shows?:?\s*)?-?\s*(?:stdout|stderr|exit_code):\s*.+/gi;


// Model pricing table — per million tokens
// Model info: pricing (per 1M tokens) + context window length
const MODEL_INFO = {
  // --- Anthropic ---
  'claude-sonnet-4-5':    { input: 3.00,  output: 15.00, ctx: 200000 },
  'claude-sonnet-4-6':    { input: 3.00,  output: 15.00, ctx: 200000 },
  'claude-sonnet-4':      { input: 3.00,  output: 15.00, ctx: 200000 },
  'claude-opus-4':        { input: 15.00, output: 75.00, ctx: 200000 },
  'claude-opus-4-6':      { input: 15.00, output: 75.00, ctx: 200000 },
  'claude-haiku-4':       { input: 0.80,  output: 4.00,  ctx: 200000 },
  'claude-haiku-3-5':     { input: 0.80,  output: 4.00,  ctx: 200000 },
  'claude-3-5-sonnet':    { input: 3.00,  output: 15.00, ctx: 200000 },
  'claude-3-5-haiku':     { input: 0.80,  output: 4.00,  ctx: 200000 },
  'claude-3-opus':        { input: 15.00, output: 75.00, ctx: 200000 },
  'claude-3-sonnet':      { input: 3.00,  output: 15.00, ctx: 200000 },
  'claude-3-haiku':       { input: 0.25,  output: 1.25,  ctx: 200000 },
  // --- OpenAI ---
  'gpt-5':                { input: 2.00,  output: 8.00,  ctx: 400000 },
  'gpt-4.1':              { input: 2.00,  output: 8.00,  ctx: 1047576 },
  'gpt-4.1-mini':         { input: 0.40,  output: 1.60,  ctx: 1047576 },
  'gpt-4.1-nano':         { input: 0.10,  output: 0.40,  ctx: 1047576 },
  'gpt-4o':               { input: 2.50,  output: 10.00, ctx: 128000 },
  'gpt-4o-mini':          { input: 0.15,  output: 0.60,  ctx: 128000 },
  'gpt-4-turbo':          { input: 10.00, output: 30.00, ctx: 128000 },
  'o1':                   { input: 15.00, output: 60.00, ctx: 200000 },
  'o1-mini':              { input: 3.00,  output: 12.00, ctx: 128000 },
  'o1-pro':               { input: 150.0, output: 600.0, ctx: 200000 },
  'o3':                   { input: 2.00,  output: 8.00,  ctx: 200000 },
  'o3-mini':              { input: 1.10,  output: 4.40,  ctx: 200000 },
  'o4-mini':              { input: 1.10,  output: 4.40,  ctx: 200000 },
  // --- DeepSeek ---
  'deepseek-chat':        { input: 0.27,  output: 1.10,  ctx: 64000 },
  'deepseek-coder':       { input: 0.27,  output: 1.10,  ctx: 64000 },
  'deepseek-reasoner':    { input: 0.55,  output: 2.19,  ctx: 64000 },
  'deepseek-r1':          { input: 0.55,  output: 2.19,  ctx: 64000 },
  'deepseek-v3':          { input: 0.27,  output: 1.10,  ctx: 64000 },
  'deepseek-v2':          { input: 0.14,  output: 0.28,  ctx: 64000 },
  // --- Google ---
  'gemini-2.5-pro':       { input: 1.25,  output: 10.00, ctx: 1048576 },
  'gemini-2.5-flash':     { input: 0.15,  output: 0.60,  ctx: 1048576 },
  'gemini-2.0-flash':     { input: 0.10,  output: 0.40,  ctx: 1048576 },
  'gemini-1.5-pro':       { input: 1.25,  output: 5.00,  ctx: 1048576 },
  'gemini-1.5-flash':     { input: 0.075, output: 0.30,  ctx: 1048576 },
  'gemma-3':              { input: 0.10,  output: 0.10,  ctx: 128000 },
  // --- Mistral ---
  'mistral-large':        { input: 2.00,  output: 6.00,  ctx: 128000 },
  'mistral-medium':       { input: 2.00,  output: 6.00,  ctx: 32000 },
  'mistral-small':        { input: 0.20,  output: 0.60,  ctx: 32000 },
  'mistral-nemo':         { input: 0.15,  output: 0.15,  ctx: 128000 },
  'mixtral':              { input: 0.24,  output: 0.24,  ctx: 32000 },
  'codestral':            { input: 0.30,  output: 0.90,  ctx: 32000 },
  'pixtral':              { input: 2.00,  output: 6.00,  ctx: 128000 },
  // --- xAI ---
  'grok-4':               { input: 3.00,  output: 15.00, ctx: 131072 },
  'grok-3':               { input: 3.00,  output: 15.00, ctx: 131072 },
  'grok-2':               { input: 2.00,  output: 10.00, ctx: 131072 },
  // --- Meta ---
  'llama-4':              { input: 0.20,  output: 0.20,  ctx: 1048576 },
  'llama-3.3':            { input: 0.20,  output: 0.20,  ctx: 131072 },
  'llama-3.2':            { input: 0.20,  output: 0.20,  ctx: 131072 },
  'llama-3.1':            { input: 0.20,  output: 0.20,  ctx: 131072 },
  'llama-3':              { input: 0.20,  output: 0.20,  ctx: 131072 },
  // --- Qwen ---
  'qwen3':                { input: 0.30,  output: 1.20,  ctx: 131072 },
  'qwen2.5':              { input: 0.30,  output: 1.20,  ctx: 131072 },
  'qwq':                  { input: 0.30,  output: 1.20,  ctx: 32768 },
  // --- Cohere ---
  'command-a':            { input: 2.50,  output: 10.00, ctx: 256000 },
  'command-r-plus':       { input: 2.50,  output: 10.00, ctx: 128000 },
  'command-r':            { input: 0.15,  output: 0.60,  ctx: 128000 },
  // --- Perplexity ---
  'sonar-pro':            { input: 3.00,  output: 15.00, ctx: 200000 },
  'sonar':                { input: 1.00,  output: 1.00,  ctx: 128000 },
  // --- MiniMax ---
  'minimax':              { input: 0.70,  output: 0.70,  ctx: 1000000 },
  // --- Kimi / Moonshot ---
  'moonshot':             { input: 1.00,  output: 1.00,  ctx: 128000 },
  'kimi':                 { input: 1.00,  output: 1.00,  ctx: 128000 },
  // --- Microsoft ---
  'phi-4':                { input: 0.07,  output: 0.14,  ctx: 16000 },
  'phi-3':                { input: 0.07,  output: 0.14,  ctx: 128000 },
  // --- Nvidia ---
  'nemotron':             { input: 0.30,  output: 1.20,  ctx: 131072 },
  // --- Nous ---
  'hermes':               { input: 0.20,  output: 0.20,  ctx: 131072 },
};

// Compat alias
const MODEL_PRICING = MODEL_INFO;

// Image generation cost lookup (per-image, by model × quality × size)
const IMAGE_PRICING = {
  'gpt-image-1.5': { 'low': { '1024x1024': 0.009, '1024x1536': 0.013, '1536x1024': 0.013 }, 'medium': { '1024x1024': 0.034, '1024x1536': 0.05, '1536x1024': 0.05 }, 'high': { '1024x1024': 0.133, '1024x1536': 0.2, '1536x1024': 0.2 } },
  'gpt-image-1':   { 'low': { '1024x1024': 0.011, '1024x1536': 0.016, '1536x1024': 0.016 }, 'medium': { '1024x1024': 0.042, '1024x1536': 0.063, '1536x1024': 0.063 }, 'high': { '1024x1024': 0.167, '1024x1536': 0.25, '1536x1024': 0.25 } },
  'gpt-image-1-mini': { 'low': { '1024x1024': 0.005, '1024x1536': 0.006, '1536x1024': 0.006 }, 'medium': { '1024x1024': 0.011, '1024x1536': 0.015, '1536x1024': 0.015 }, 'high': { '1024x1024': 0.036, '1024x1536': 0.052, '1536x1024': 0.052 } },
};

export function shortModel(name) {
  if (!name) return '...';
  if (typeof name !== 'string') name = String(name);
  let short = name.split('/').pop();
  // Strip .gguf extension
  short = short.replace(/\.gguf$/i, '');
  // Strip quantization suffixes (Q4_K_M, Q8_0, etc.) and shard numbers
  short = short.replace(/-0000\d-of-\d+$/, '');
  short = short.replace(/[-_](Q\d[_A-Z\d]*|F16|F32|BF16|fp16|fp32)$/i, '');
  // Truncate if still too long (keep first meaningful part)
  if (short.length > 25) {
    // Try to find a natural break point (dash after model size like -35B or -7B)
    const sizeMatch = short.match(/^(.+?-\d+[BbMm])/);
    if (sizeMatch) short = sizeMatch[1];
    else short = short.substring(0, 22) + '…';
  }
  return short;
}

function modelValue(name) {
  if (name == null) return '';
  return String(name).trim();
}

export function sameModelName(left, right) {
  const a = modelValue(left);
  const b = modelValue(right);
  if (!a || !b) return false;
  return a.toLowerCase() === b.toLowerCase()
    || shortModel(a).toLowerCase() === shortModel(b).toLowerCase();
}

function shortEndpointLabel(label) {
  const value = modelValue(label);
  if (!value) return '';
  return value.length > 18 ? value.slice(0, 17) + '…' : value;
}

export function modelRouteLabel(
  requestedModel,
  actualModel,
  requestedEndpointLabel = '',
  actualEndpointLabel = '',
  requestedEndpointId = '',
  actualEndpointId = '',
) {
  const requested = modelValue(requestedModel);
  const actual = modelValue(actualModel) || requested;
  const requestedRoute = modelValue(requestedEndpointId || requestedEndpointLabel);
  const actualRoute = modelValue(actualEndpointId || actualEndpointLabel);
  const routeChanged = Boolean(
    actualRoute
    && requestedRoute
    && actualRoute !== requestedRoute
  );
  if (!requested || sameModelName(requested, actual)) {
    const model = shortModel(actual || requested);
    if (!routeChanged) return model;
    const from = shortEndpointLabel(requestedEndpointLabel || 'Selected route');
    const to = shortEndpointLabel(actualEndpointLabel || actualEndpointId);
    return model + ' (' + from + ' -> ' + to + ')';
  }
  return shortModel(requested) + ' -> ' + shortModel(actual);
}

export function replyModelPair(modelName, metadata) {
  const meta = metadata || {};
  const actualFromMeta = modelValue(meta.model || meta.actual_model);
  const requestedFromMeta = modelValue(meta.requested_model || meta.selected_model);
  if (actualFromMeta || requestedFromMeta) {
    const actual = actualFromMeta || requestedFromMeta || modelValue(modelName);
    const requested = requestedFromMeta || actual;
    return {
      requestedModel: requested,
      actualModel: actual,
      requestedEndpointId: meta.requested_endpoint_id || null,
      requestedEndpointLabel: meta.requested_endpoint_label || 'Selected route',
      actualEndpointId: meta.endpoint_id || null,
      actualEndpointLabel: meta.endpoint_label || meta.requested_endpoint_label || 'Selected route',
    };
  }
  const fallback = modelValue(modelName);
  return {
    requestedModel: fallback,
    actualModel: fallback,
    requestedEndpointId: null,
    requestedEndpointLabel: 'Selected route',
    actualEndpointId: null,
    actualEndpointLabel: 'Selected route',
  };
}

/**
 * Generate a consistent HSL color for a model name.
 * Returns an hsl() string. The hue is derived from a string hash,
 * saturation and lightness are fixed for readability on dark/light themes.
 */
export function modelColor(name) {
  if (!name) return null;
  const key = name.toLowerCase();
  let hash = 0;
  for (let i = 0; i < key.length; i++) {
    hash = ((hash << 5) - hash + key.charCodeAt(i)) | 0;
  }
  const hue = ((hash % 360) + 360) % 360;
  return `hsl(${hue}, 55%, 65%)`;
}

/** Look up model info (pricing + context) by substring match */
export function getModelInfo(modelName) {
  if (!modelName) return null;
  const key = matchModelKey(modelName, Object.keys(MODEL_INFO));
  return key ? { key, ...MODEL_INFO[key] } : null;
}

function _fmtCtx(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
  return Math.round(n / 1000) + 'K';
}

/**
 * Apply model color to a role element (sets color + dot color).
 */
export function applyModelColor(roleEl, modelName) {
  if (!modelName) return;
  const color = modelColor(modelName);
  if (color) {
    roleEl.style.color = color;
    roleEl.style.setProperty('--model-dot', color);
  }
  // Replace generic dot with provider logo if available
  const logo = providerLogo(modelName);
  const existingLogo = roleEl.querySelector('.role-provider-logo');
  if (!logo) {
    if (existingLogo) existingLogo.remove();
    roleEl.classList.remove('has-logo');
  } else if (!existingLogo) {
    const span = document.createElement('span');
    span.className = 'role-provider-logo';
    span.innerHTML = logo;
    roleEl.classList.add('has-logo');
    roleEl.prepend(span);
  }
  // Click to show model info popup
  if (!roleEl._hasInfoClick) {
    roleEl._hasInfoClick = true;
    roleEl.style.cursor = 'pointer';
    roleEl.addEventListener('click', (e) => {
      e.stopPropagation();
      document.querySelectorAll('.ctx-popup').forEach(p => { if (typeof p._dismiss === 'function') p._dismiss(); else p.remove(); });
      const info = getModelInfo(modelName);
      const short = shortModel(modelName);
      const logoHtml = providerLogo(modelName);
      const popup = document.createElement('div');
      popup.className = 'ctx-popup';
      let html = '<div style="font-weight:600;margin-bottom:6px;color:var(--fg);display:flex;align-items:center;gap:6px;">';
      if (logoHtml) html += '<span class="role-provider-logo" style="opacity:0.7">' + logoHtml + '</span>';
      html += uiModule.esc(short) + '</div>';
      html += '<div><span class="ctx-label">Model</span> ' + uiModule.esc(modelName.split('/').pop()) + '</div>';
      // Provider = the serving endpoint, distinct from the model vendor/logo
      // (e.g. the same model via OpenRouter vs Copilot vs Anthropic direct).
      const _epUrl = (window.sessionModule && window.sessionModule.getCurrentEndpointUrl)
        ? window.sessionModule.getCurrentEndpointUrl() : null;
      const _provLabel = providerLabel(_epUrl);
      if (_provLabel) html += '<div><span class="ctx-label">Provider</span> ' + uiModule.esc(_provLabel) + '</div>';
      // Show static context initially, then fetch real from server
      const _realCtx = window._realContextLengths && window._realContextLengths[modelName];
      if (_realCtx) {
        html += '<div><span class="ctx-label">Context</span> ' + _fmtCtx(_realCtx) + ' tokens';
        if (info && info.ctx && info.ctx !== _realCtx) html += ' <span style="opacity:0.35">(spec: ' + _fmtCtx(info.ctx) + ')</span>';
        html += '</div>';
      } else if (info && info.ctx) {
        html += '<div><span class="ctx-label">Context</span> <span id="_ctx-val">' + _fmtCtx(info.ctx) + ' tokens</span></div>';
      }
      // Fetch real context from server async
      if (!_realCtx && window.sessionModule) {
        const _sid = window.sessionModule.getCurrentSessionId();
        if (_sid) {
          fetch('/api/session/' + _sid + '/context_info').then(r => r.ok ? r.json() : null).then(d => {
            if (d && d.context_length) {
              if (!window._realContextLengths) window._realContextLengths = {};
              window._realContextLengths[modelName] = d.context_length;
              const el = document.getElementById('_ctx-val');
              if (el) {
                el.innerHTML = _fmtCtx(d.context_length) + ' tokens';
                if (info && info.ctx && info.ctx !== d.context_length) {
                  el.innerHTML += ' <span style="opacity:0.35">(spec: ' + _fmtCtx(info.ctx) + ')</span>';
                }
              }
            }
          }).catch(() => {});
        }
      }
      // Show configured max tokens if set
      if (window.presetsModule) {
        const _pid = window.presetsModule.getSelectedPreset();
        const _preset = _pid ? window.presetsModule.getPreset(_pid) : null;
        const _mt = _preset?.max_tokens;
        if (_mt && _mt > 0 && _mt <= 8192) {
          html += '<div><span class="ctx-label">Max tokens</span> ' + _mt.toLocaleString() + ' <span style="opacity:0.4">(configured)</span></div>';
        }
      }
      if (isCostTrackedEndpoint(_epUrl)) {
        if (info && info.input != null) html += '<div><span class="ctx-label">Input</span> $' + info.input.toFixed(2) + ' / 1M</div>';
        if (info && info.output != null) html += '<div><span class="ctx-label">Output</span> $' + info.output.toFixed(2) + ' / 1M</div>';
        if (!info) html += '<div style="opacity:0.4;font-size:0.85em;margin-top:4px;">No pricing data available</div>';
      }
      popup.innerHTML = html;
      const rect = roleEl.getBoundingClientRect();
      popup.style.top = (rect.bottom + 4) + 'px';
      popup.style.left = rect.left + 'px';
      document.body.appendChild(popup);
      const pr = popup.getBoundingClientRect();
      if (pr.bottom > window.innerHeight - 8) popup.style.top = (rect.top - pr.height - 4) + 'px';
      if (pr.right > window.innerWidth - 8) popup.style.left = (window.innerWidth - pr.width - 8) + 'px';
      bindMenuDismiss(popup, () => popup.remove());
    });
  }
}

export function getModelCost(modelName, inputTokens, outputTokens) {
  if (!modelName) return null;
  const key = matchModelKey(modelName, Object.keys(MODEL_PRICING));
  if (!key) return null;
  const price = MODEL_PRICING[key];
  return (inputTokens * price.input + outputTokens * price.output) / 1_000_000;
}

/**
 * Is this endpoint a local / self-hosted model server (vLLM, Ollama, …)?
 * Local models are free, so we must NOT bill them at cloud rates — the
 * pricing table matches on a name substring, so a local `qwen2.5-coder`
 * would otherwise be charged like cloud `qwen2.5`. When the serving host is
 * loopback, a private LAN range, Tailscale CGNAT (100.64–100.127.x), a
 * `.local` name, or the app's own host, the model is local → free.
 * Unknown / missing endpoint also counts as local (bias to not over-bill).
 */
export function isLocalEndpoint(url) {
  if (!url) return true;
  let host;
  try { host = new URL(url).hostname; } catch (_e) { return true; }
  if (!host) return true;
  if (host === 'localhost' || host === '0.0.0.0' || host === 'host.docker.internal' || host.endsWith('.local')) return true;
  if (typeof window !== 'undefined' && window.location && host === window.location.hostname) return true;
  // A single-label hostname (no dot) is an internal/Docker service name
  // (e.g. "nim-nano", "llamaswap", "nemotron-super-49b") or a LAN shortname —
  // never a public API, which always needs an FQDN. Treat as local → free.
  // (Without this, container-name endpoints get billed at cloud rates because
  // the pricing table matches on a name substring, e.g. "nemotron".)
  if (!host.includes('.')) return true;
  if (/^127\./.test(host)) return true;
  if (/^10\./.test(host)) return true;
  if (/^192\.168\./.test(host)) return true;
  if (/^172\.(1[6-9]|2\d|3[01])\./.test(host)) return true;
  const cg = host.match(/^100\.(\d+)\./);            // Tailscale CGNAT
  if (cg && +cg[1] >= 64 && +cg[1] <= 127) return true;
  return false;
}

export function isSubscriptionEndpoint(url) {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    const path = parsed.pathname.replace(/\/+$/, '');
    return parsed.hostname === 'chatgpt.com'
      && (path === '/backend-api/codex' || path.startsWith('/backend-api/codex/'));
  } catch (_e) {
    return false;
  }
}

function _currentEndpointUrl() {
  return (window.sessionModule && window.sessionModule.getCurrentEndpointUrl)
    ? window.sessionModule.getCurrentEndpointUrl() : null;
}

export function isCostTrackedEndpoint(url) {
  return !isLocalEndpoint(url) && !isSubscriptionEndpoint(url);
}

/** Cost for the current turn, returning null for non-billable endpoints. */
function _billableCost(model, inputTokens, outputTokens, endpointCostTracked, selectedEndpointUrl) {
  // Foreground fallback can answer on a different endpoint than the session's
  // selected route. Prefer the backend's non-secret actual-route
  // classification; retain the selected-endpoint check for older history.
  if (endpointCostTracked === false) return null;
  const selectedUrl = selectedEndpointUrl === undefined
    ? _currentEndpointUrl()
    : selectedEndpointUrl;
  if (endpointCostTracked !== true && !isCostTrackedEndpoint(selectedUrl)) {
    return null;
  }
  return getModelCost(model, inputTokens, outputTokens);
}

/** Sum cost using the route/model that produced each Agent round. */
function _metricsBillableCost(metrics, model, inputTokens, outputTokens, selectedEndpointUrl) {
  const buckets = Array.isArray(metrics.usage_buckets) ? metrics.usage_buckets : [];
  if (!buckets.length) {
    return _billableCost(
      model,
      inputTokens,
      outputTokens,
      metrics.endpoint_cost_tracked,
      selectedEndpointUrl,
    );
  }
  let total = 0;
  let hasPricedUsage = false;
  for (const bucket of buckets) {
    if (!bucket || typeof bucket !== 'object') continue;
    const bucketCost = _billableCost(
      bucket.model || model,
      Number(bucket.input_tokens) || 0,
      Number(bucket.output_tokens) || 0,
      bucket.endpoint_cost_tracked,
      selectedEndpointUrl,
    );
    if (bucketCost === null) continue;
    total += bucketCost;
    hasPricedUsage = true;
  }
  return hasPricedUsage ? total : null;
}

export function getImageCost(model, quality, size) {
  if (!model) return null;
  const m = model.toLowerCase();
  for (const [key, quals] of Object.entries(IMAGE_PRICING)) {
    if (m.includes(key)) {
      const q = quals[(quality || 'medium').toLowerCase()] || quals['medium'];
      return q ? (q[size] || q['1024x1024'] || null) : null;
    }
  }
  return null;
}

/* ── Session cost helpers ─────────────────────────────────────────── */
const _COST_KEY = 'ody-session-cost';
const _COST_RUNS_KEY = 'ody-session-cost-runs';
const _MAX_COST_RUNS_PER_SESSION = 256;
const _COST_LEDGER_LOCK = 'odysseus-session-cost-ledger';

/**
 * Decode one persisted ledger object into a Map.
 *
 * Ledger keys are external identifiers. Keeping them out of ordinary object
 * property assignment means values such as "__proto__" can never interact
 * with Object.prototype. The persisted JSON shape remains an ordinary object
 * for backwards compatibility.
 */
function _readCostLedger(storageKey) {
  const parsed = JSON.parse(localStorage.getItem(storageKey) || '{}');
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return new Map();
  return new Map(Object.entries(parsed));
}

function _writeCostLedger(storageKey, ledger) {
  localStorage.setItem(storageKey, JSON.stringify(Object.fromEntries(ledger)));
}

function _readCostRunLedger() {
  const sessions = _readCostLedger(_COST_RUNS_KEY);
  for (const [sid, runs] of sessions) {
    sessions.set(
      sid,
      runs && typeof runs === 'object' && !Array.isArray(runs)
        ? new Map(Object.entries(runs))
        : new Map(),
    );
  }
  return sessions;
}

function _writeCostRunLedger(sessions) {
  const serialized = new Map();
  for (const [sid, runs] of sessions) {
    serialized.set(sid, Object.fromEntries(runs));
  }
  _writeCostLedger(_COST_RUNS_KEY, serialized);
}

/** Return the accumulated cost for the current (or given) session. */
export function getSessionCost(sessionId) {
  const sid = sessionId || (window.sessionModule && window.sessionModule.getCurrentSessionId());
  if (!sid) return 0;
  try {
    const costs = _readCostLedger(_COST_KEY);
    const runCosts = _readCostRunLedger();
    const recordedRuns = runCosts.get(sid) || new Map();
    return (costs.get(sid) || 0) + Array.from(recordedRuns.values()).reduce(
      (total, value) => total + (Number(value) || 0),
      0,
    );
  } catch (_e) { return 0; }
}

/** Reset session cost for the given session (defaults to current). */
export function resetSessionCost(sessionId) {
  const sid = sessionId || (window.sessionModule && window.sessionModule.getCurrentSessionId());
  if (!sid) return;
  try {
    const costs = _readCostLedger(_COST_KEY);
    costs.delete(sid);
    _writeCostLedger(_COST_KEY, costs);
    const runCosts = _readCostRunLedger();
    runCosts.delete(sid);
    _writeCostRunLedger(runCosts);
  } catch (_e) { /* ignore */ }
  updateSessionCostUI();
}

/** Update the persistent session-cost badge in the input bar. */
export function updateSessionCostUI() {
  const el = document.getElementById('session-cost-display');
  if (!el) return;
  // The ledger records billable work already performed in this session. A
  // selected local endpoint does not erase cost from a paid fallback route.
  const cost = getSessionCost();
  if (cost > 0) {
    el.textContent = '$' + (cost < 0.01 ? cost.toFixed(4) : cost < 1 ? cost.toFixed(3) : cost.toFixed(2));
    el.style.display = '';
  } else {
    el.style.display = 'none';
  }
}

/** Record one metrics payload in a session ledger at most once. */
export function recordSessionMetricsCost(metrics, sessionId, selectedEndpointUrl) {
  if (!metrics || typeof metrics !== 'object') return null;
  const cost = _metricsBillableCost(
    metrics,
    metrics.model || 'Unknown',
    metrics.input_tokens || 0,
    metrics.output_tokens || 0,
    selectedEndpointUrl,
  );
  if (metrics._fromHistory) return cost;
  const sid = sessionId || (
    window.sessionModule && window.sessionModule.getCurrentSessionId()
  );
  if (typeof sid !== 'string' || !sid || cost === null) return cost;
  const runId = typeof metrics._costRecordId === 'string'
    ? metrics._costRecordId.trim()
    : '';
  // Never resolve ledger entries through Object.prototype or its constructor.
  if (['__proto__', 'prototype', 'constructor'].includes(sid)
      || ['__proto__', 'prototype', 'constructor'].includes(runId)) return cost;
  if ((metrics._costRecorded || metrics._costRecordPending) && !runId) return cost;
  // Recorded is only set once the write actually runs; pending covers the
  // window while the write waits on the cross-tab lock, so a replay in that
  // window cannot double-add and a tab closed mid-queue never claims recorded.
  metrics._costRecordPending = true;
  const writeCost = () => {
    if (runId) {
      try {
        const runCosts = _readCostRunLedger();
        const sessionRuns = runCosts.get(sid) || new Map();
        // Detached-run identity is replay-idempotent even when a refresh
        // produces a fresh metrics object. Map keys also avoid all
        // Object.prototype lookup and assignment semantics.
        sessionRuns.set(runId, cost);
        const entries = Array.from(sessionRuns.entries());
        if (entries.length > _MAX_COST_RUNS_PER_SESSION) {
          const overflow = entries.slice(0, entries.length - _MAX_COST_RUNS_PER_SESSION);
          const costs = _readCostLedger(_COST_KEY);
          costs.set(
            sid,
            (costs.get(sid) || 0) + overflow.reduce(
              (total, entry) => total + (Number(entry[1]) || 0),
              0,
            ),
          );
          overflow.forEach(([oldRunId]) => sessionRuns.delete(oldRunId));
          _writeCostLedger(_COST_KEY, costs);
        }
        runCosts.set(sid, sessionRuns);
        _writeCostRunLedger(runCosts);
      } catch (_e) { /* ignore */ }
    } else {
      try {
        const costs = _readCostLedger(_COST_KEY);
        costs.set(sid, (costs.get(sid) || 0) + cost);
        _writeCostLedger(_COST_KEY, costs);
      } catch (_e) { /* ignore */ }
    }
    metrics._costRecorded = true;
    metrics._costRecordPending = false;
    const currentSid = window.sessionModule && window.sessionModule.getCurrentSessionId();
    if (currentSid === sid) updateSessionCostUI();
  };

  let writeStarted = false;
  const guardedWrite = () => {
    writeStarted = true;
    writeCost();
  };
  try {
    if (
      typeof navigator !== 'undefined'
      && navigator.locks
      && typeof navigator.locks.request === 'function'
    ) {
      const pendingWrite = navigator.locks.request(_COST_LEDGER_LOCK, guardedWrite);
      if (pendingWrite && typeof pendingWrite.catch === 'function') {
        pendingWrite.catch(() => {
          if (!writeStarted) guardedWrite();
        });
      }
    } else {
      guardedWrite();
    }
  } catch (_e) {
    if (!writeStarted) guardedWrite();
  }
  return cost;
}

/** Create a timestamp span for role labels.
 * Pass an ISO string / Date / epoch-ms to render the message's own time
 * (used when replaying history). Falls back to "now" when no value is given. */
export function roleTimestamp(when) {
  const ts = document.createElement('span');
  ts.className = 'role-timestamp';
  let d;
  if (when instanceof Date) d = when;
  else if (typeof when === 'number') d = new Date(when);
  else if (typeof when === 'string' && when) d = new Date(when);
  else d = new Date();
  if (isNaN(d.getTime())) d = new Date();
  ts.textContent = d.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
  ts.title = d.toLocaleString();
  return ts;
}

function userModePill(metadata) {
  const mode = String(metadata?.interaction_mode || metadata?.mode || '').toLowerCase();
  if (mode !== 'agent' && mode !== 'chat' && mode !== 'research') return null;
  const pill = document.createElement('span');
  pill.className = 'user-mode-pill user-mode-pill-' + mode;
  if (mode === 'agent' || mode === 'chat') {
    pill.innerHTML = mode === 'agent' ? USER_MODE_AGENT_ICON : USER_MODE_CHAT_ICON;
    pill.setAttribute('aria-label', mode === 'agent' ? 'Agent mode' : 'Chat mode');
  } else {
    pill.textContent = 'research';
  }
  pill.title = metadata?.auto_escalated
    ? 'Auto-promoted from chat for this tool request'
    : `Sent in ${mode} mode`;
  if (metadata?.auto_escalated) pill.dataset.auto = 'true';
  return pill;
}

export function setUserModePill(messageEl, mode, autoEscalated) {
  if (!messageEl) return;
  const role = messageEl.querySelector?.('.role');
  if (!role) return;
  role.querySelectorAll('.user-mode-pill').forEach(el => el.remove());
  const pill = userModePill({
    interaction_mode: mode,
    auto_escalated: !!autoEscalated,
  });
  if (pill) role.appendChild(pill);
}

/**
 * Strip tool invocation blocks from text before rendering.
 */
export function stripToolBlocks(text) {
  let cleaned = text.replace(TOOL_CALL_RE, '');
  if (EXEC_FENCE_RE) cleaned = cleaned.replace(EXEC_FENCE_RE, stripExecutedFence);
  cleaned = cleaned.replace(DSML_TOOL_RE, '');
  cleaned = cleaned.replace(DSML_INVOKE_RE, '');
  cleaned = cleaned.replace(DSML_STRAY_RE, '');
  cleaned = cleaned.replace(XML_TOOL_CALL_RE, '');
  cleaned = cleaned.replace(XML_INVOKE_RE, '');
  cleaned = cleaned.replace(RAW_OPENAI_TOOL_JSON_RE, '');
  cleaned = cleaned.replace(QWEN_ROLE_MARKER_RE, '');
  cleaned = cleaned.replace(QWEN_BARE_MARKER_RE, ' ');
  cleaned = cleaned.replace(TOOL_NARRATION_RE, '');
  cleaned = cleaned.replace(/\n{3,}/g, '\n\n');
  return cleaned.trim();
}

/**
 * Plain-text payload for the message copy buttons: the reply as the renderer
 * displays it — tool blocks and <think> reasoning stripped. dataset.raw keeps
 * the full model output (chat.js even embeds the elapsed time into the
 * <think> tag for reload persistence), so copying it verbatim leaks the
 * thinking block (#3722). Falls back to the raw text when stripping leaves
 * nothing (e.g. turns interrupted mid-thinking).
 */
export function copyMessageText(msgElement) {
  const raw = msgElement.dataset.raw || msgElement.querySelector('.body')?.textContent || '';
  const { content } = markdownModule.extractThinkingBlocks(stripToolBlocks(raw));
  return content || raw;
}

/**
 * Build a collapsible sources box (used by both research and web search).
 */
export function buildSourcesBox(sources, type, expanded) {
  var esc = uiModule.esc;
  var id = 'sources-' + Date.now() + '-' + Math.random().toString(36).substr(2, 5);
  var count = sources.length;
  var label = type === 'research' ? 'Research sources' : 'Web sources';
  var lines = '';
  for (var i = 0; i < count; i++) {
    var s = sources[i];
    var domain = '';
    try { domain = new URL(s.url).hostname.replace('www.', ''); } catch(e) { domain = s.url; }
    var title = esc(s.title || domain || '');
    var safeUrl = _safeHref(s.url);
    lines += '<a href="' + safeUrl + '" target="_blank" rel="noopener noreferrer" class="source-link">'
      + '<span class="source-num">' + (i + 1) + '</span>'
      + '<span class="source-title">' + title + '</span>'
      + '<span class="source-domain">' + esc(domain) + '</span>'
      + '</a>';
  }
  var arrow = expanded ? 'down' : 'right';
  var expandedClass = expanded ? ' expanded' : '';
  return '<div class="sources-section">'
    + '<div class="sources-header" data-sources-id="' + id + '" onclick="window.toggleSources(\'' + id + '\')">'
    + '<div class="sources-header-left">' + SEARCH_ICON + '<span>' + count + ' ' + label + '</span></div>'
    + '<span class="sources-toggle" id="' + id + '-toggle" data-arrow="' + arrow + '"></span>'
    + '</div>'
    + '<div class="sources-content' + expandedClass + '" id="' + id + '">'
    + '<div class="sources-content-inner">' + lines + '</div>'
    + '</div></div>';
}

/**
 * Build the RAG "Sources (N documents)" box — mirrors the live render in
 * chat.js so persisted rag_sources survive a refresh. Items carry a
 * filename, similarity %, and snippet (not URLs, unlike web sources).
 * @param {Array<{filename, similarity, snippet}>} sources
 */
export function buildRagSourcesBox(sources) {
  if (!sources || !sources.length) return '';
  var esc = uiModule.esc;
  var items = '';
  for (var i = 0; i < sources.length; i++) {
    var s = sources[i] || {};
    var pct = (typeof s.similarity === 'number') ? (s.similarity * 100).toFixed(1) + '%' : '';
    items += '<div class="rag-source-item"><strong>' + esc(s.filename || '') + '</strong>'
      + (pct ? ' <span class="rag-similarity">' + pct + '</span>' : '')
      + '<div class="rag-snippet">' + esc(s.snippet || '') + '</div></div>';
  }
  return '<details class="rag-sources"><summary>Sources (' + sources.length + ' documents)</summary>' + items + '</details>';
}

/**
 * Build a collapsible "Raw collected findings" section, styled like the sources box.
 * @param {Array<{url, title, summary}>} findings
 * @param {boolean} [expanded=false]
 */
export function buildFindingsBox(findings, expanded) {
  if (!findings || !findings.length) return '';
  var esc = uiModule.esc;
  var id = 'findings-' + Date.now() + '-' + Math.random().toString(36).substr(2, 5);
  var count = findings.length;
  var lines = '';
  for (var i = 0; i < count; i++) {
    var f = findings[i];
    var domain = '';
    try { domain = new URL(f.url).hostname.replace('www.', ''); } catch(e) { domain = f.url; }
    var title = esc(f.title || domain || '');
    var summary = esc(f.summary || '');
    var safeUrl = _safeHref(f.url);
    lines += '<div class="finding-item">'
      + '<a href="' + safeUrl + '" target="_blank" rel="noopener noreferrer" class="source-link">'
      + '<span class="source-num">' + (i + 1) + '</span>'
      + '<span class="source-title">' + title + '</span>'
      + '<span class="source-domain">' + esc(domain) + '</span>'
      + '</a>'
      + '<div class="finding-summary">' + summary + '</div>'
      + '</div>';
  }
  var FINDINGS_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>';
  var arrow = expanded ? 'down' : 'right';
  var expandedClass = expanded ? ' expanded' : '';
  return '<div class="sources-section">'
    + '<div class="sources-header" data-sources-id="' + id + '" onclick="window.toggleSources(\'' + id + '\')">'
    + '<div class="sources-header-left">' + FINDINGS_ICON + '<span>' + count + ' Raw collected findings</span></div>'
    + '<span class="sources-toggle" id="' + id + '-toggle" data-arrow="' + arrow + '"></span>'
    + '</div>'
    + '<div class="sources-content' + expandedClass + '" id="' + id + '">'
    + '<div class="sources-content-inner">' + lines + '</div>'
    + '</div></div>';
}

/** Append report button + continue research prompt. */
export function appendReportButton(container, sessionId) {
  _appendReportButton(container, sessionId);
  _appendContinuePrompt(container);
}

function _appendContinuePrompt(container) {
  var wrap = document.createElement('div');
  wrap.className = 'continue-research-wrap';
  wrap.innerHTML =
    '<div class="continue-research-hint">'
    + '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>'
    + '<span>Dig deeper? Activate Research again and type a follow-up question to continue this research.</span>'
    + '</div>';
  container.appendChild(wrap);
}
function _appendReportButton(container, sessionId) {
  var apiBase = window.API_BASE || '';

  // Wrapper holds report button + chat-about button
  var wrap = document.createElement('div');
  wrap.className = 'report-btn-wrap';

  var btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'view-report-btn';
  btn.innerHTML = REPORT_ICON + ' Open Visual Report';

  var reportUrl = apiBase + '/api/research/report/' + sessionId;
  btn.addEventListener('click', function() {
    window.open(reportUrl, '_blank');
  });
  wrap.appendChild(btn);

  var chatBtn = document.createElement('button');
  chatBtn.type = 'button';
  chatBtn.className = 'view-report-btn chat-about-btn';
  chatBtn.innerHTML = CHAT_ABOUT_ICON + ' Discuss';
  chatBtn.addEventListener('click', async function() {
    if (chatBtn.disabled) return;
    var origLabel = chatBtn.innerHTML;
    chatBtn.disabled = true;
    chatBtn.innerHTML = CHAT_ABOUT_ICON + ' Creating…';
    try {
      var res = await fetch(apiBase + '/api/research/spinoff/' + sessionId, { method: 'POST' });
      if (!res.ok) {
        var detail = '';
        try { detail = (await res.json()).detail || ''; } catch {}
        throw new Error(detail || ('HTTP ' + res.status));
      }
      var payload = await res.json();
      if (window.sessionModule && payload.session_id) {
        await window.sessionModule.loadSessions().catch(() => {});
        await window.sessionModule.selectSession(payload.session_id);
      }
    } catch (e) {
      chatBtn.disabled = false;
      chatBtn.innerHTML = origLabel;
      if (window.uiModule && uiModule.showError) {
        uiModule.showError('Could not start follow-up chat: ' + e.message);
      } else {
        alert('Could not start follow-up chat: ' + e.message);
      }
    }
  });
  wrap.appendChild(chatBtn);

  container.appendChild(wrap);
}

window.toggleSources = function(id) {
  // Debounce to prevent double-fire from both inline onclick and delegation
  var now = Date.now();
  if (window._lastSourcesToggle && now - window._lastSourcesToggle < 100) return;
  window._lastSourcesToggle = now;

  var content = document.getElementById(id);
  var toggle = document.getElementById(id + '-toggle');
  if (content && toggle) {
    var expanded = content.classList.contains('expanded');
    content.classList.toggle('expanded', !expanded);
    toggle.dataset.arrow = expanded ? 'right' : 'down';
  }
};

// Event delegation for sources toggle (capture phase, handles SVG targets)
document.addEventListener('click', function(e) {
  // Walk up from target manually to handle SVG elements that may not support closest()
  var el = e.target;
  while (el && el !== document) {
    if (el.classList && el.classList.contains('sources-header') && el.dataset && el.dataset.sourcesId) {
      e.stopPropagation();
      window.toggleSources(el.dataset.sourcesId);
      return;
    }
    el = el.parentElement || el.parentNode;
  }
}, true);

function resolveDocumentPlaceholderLinks(text, metadata) {
  if (!text || !metadata || !Array.isArray(metadata.tool_events)) return text;
  const docEvents = metadata.tool_events.filter(ev => ev && ev.doc_id);
  if (!docEvents.length) return text;
  return String(text).replace(/#document-(\d+)\b/g, (match, num) => {
    const idx = Number(num) - 1;
    const ev = Number.isInteger(idx) && idx >= 0 ? docEvents[idx] : null;
    return ev && ev.doc_id ? `#document-${ev.doc_id}` : match;
  });
}

// Jump-to-entity anchors — the agent emits links like
//   [New Chat](#session-89effa28)
//   [Notes](#document-abc123)
//   [Reminder](#note-42)
// and the chat-history click delegate turns them into navigation
// instead of default in-page anchor jumps. Each prefix routes to the
// matching module via a dynamic import (avoids circular deps —
// sessions.js itself imports chatRenderer.js).
function _activateEntityAnchor(e, forcedAnchor = null) {
  // Walk past Text nodes — clicking link text yields a Text node target
  // whose .closest is undefined, so preventDefault never fires and the
  // browser performs a default hash-navigation that resets the session.
  let _t = e.target;
  while (_t && _t.nodeType === Node.TEXT_NODE) _t = _t.parentElement;
  const a = forcedAnchor || (_t && _t.closest && _t.closest('a[href]'));
  if (!a) return;
  const rawHref = a.getAttribute('href') || '';
  let href = rawHref;
  try {
    const parsed = new URL(rawHref, window.location.origin);
    if (parsed.origin === window.location.origin && parsed.pathname === window.location.pathname) {
      href = parsed.hash || rawHref;
    }
  } catch (_) {}
  if (!href.startsWith('#')) return;
  if (href === '#plan') {
    e.preventDefault();
    e.stopPropagation();
    if (typeof window.__odysseusOpenPlanReview === 'function') window.__odysseusOpenPlanReview();
    return;
  }
  const barePanel = href.match(/^#(calendar|memory|notes|tasks|documents|skills|research)$/);
  if (barePanel) {
    e.preventDefault();
    e.stopPropagation();
    const panel = barePanel[1];
    if (panel === 'calendar') {
      document.getElementById('tool-calendar-btn')?.click();
    } else if (panel === 'memory') {
      import('./memory.js').then(mod => {
        const open = mod.openMemoryModal || (mod.default && mod.default.openMemoryModal);
        if (open) open('browse');
      }).catch(() => {});
    } else if (panel === 'notes') {
      document.getElementById('tool-notes-btn')?.click();
    } else if (panel === 'tasks') {
      document.getElementById('tool-tasks-btn')?.click();
    } else if (panel === 'documents') {
      document.getElementById('tool-library-btn')?.click();
    } else if (panel === 'skills') {
      document.getElementById('tool-skills-btn')?.click();
    } else if (panel === 'research') {
        import('./research/panel.js?v=20260913researchrailerrors1').then(mod => {
        const open = mod.openPanel || (mod.default && mod.default.openPanel);
        if (open) open();
      }).catch(() => {});
    }
    return;
  }
  let m = href.match(/^#(session|document|note|memory|image|email|event|task|skill|research|cookbook)-(.+)$/);
  if (!m) {
    const noteOpen = href.match(/^#open=notes&note=([^&]+)/);
    if (noteOpen) m = ['note', 'note', decodeURIComponent(noteOpen[1])];
  }
  if (!m) {
    const bareSession = href.match(/^#([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i);
    if (bareSession) m = ['session', 'session', bareSession[1]];
  }
  if (!m) return;
  e.preventDefault();
  e.stopPropagation();
  const [, kind, rawId] = m;
  let id = rawId;
  try { id = decodeURIComponent(String(rawId || '').replace(/\+/g, ' ')); } catch (_) {}
  if (kind === 'session') {
    const fallback = () => {
      try {
        if (window.sessionModule && typeof window.sessionModule.selectSession === 'function') {
          return window.sessionModule.selectSession(id, { showLoading: true, immediateLoading: true });
        }
      } catch (_) {}
      try { window.location.hash = id; } catch (_) {}
    };
    try {
      a.classList.add('is-loading');
      a.setAttribute('aria-busy', 'true');
    } catch {}
    import('./sessions.js').then(mod => {
      const fn = mod.selectSession || (mod.default && mod.default.selectSession);
      if (fn) return fn(id, { showLoading: true, immediateLoading: true });
      return fallback();
    }).catch(() => {
      return fallback();
    }).finally(() => {
      try {
        a.classList.remove('is-loading');
        a.removeAttribute('aria-busy');
      } catch {}
    });
  } else if (kind === 'document') {
    import('./document.js?v=20260916docctx2').then(mod => {
      const open = mod.loadDocument
        || mod.openDocument
        || (mod.default && (mod.default.loadDocument || mod.default.openDocument));
      if (open) open(id);
    }).catch(() => {});
  } else if (kind === 'note') {
      import('./notes.js?v=20260911notesselectioncancel1').then(mod => {
      const open = mod.openNote || (mod.default && mod.default.openNote);
      if (open) open(id);
      try {
        if (/^#(?:note-|open=notes&note=)/.test(window.location.hash || '')) {
          history.replaceState(null, '', window.location.pathname + window.location.search);
        }
      } catch (_) {}
    }).catch(() => {});
  } else if (kind === 'memory') {
    import('./memory.js').then(mod => {
      const open = mod.openMemory || (mod.default && mod.default.openMemory);
      if (open) open(id);
    }).catch(() => {});
  } else if (kind === 'image') {
    import('./gallery.js?v=20260910promptcopy1').then(mod => {
      const open = mod.openGalleryImage || (mod.default && mod.default.openGalleryImage);
      if (open) open(id);
    }).catch(() => {});
  } else if (kind === 'email') {
    import('./emailLibrary.js?v=20260915trashmove2').then(mod => {
      const open = mod.openEmailLibrary || (mod.default && mod.default.openEmailLibrary);
      if (!open) return;
      const parts = String(id || '').split(':');
      if (parts.length >= 2 && /^\d+$/.test(parts[1])) {
        const opts = { folder: parts[0] || 'INBOX', uid: parts[1] };
        const account = parts.slice(2).join(':');
        if (account) opts.account_id = account;
        open(opts);
      } else {
        open({ uid: id });
      }
    }).catch(() => {});
  } else if (kind === 'event') {
    import('./calendar.js?v=20260914emailsource11').then(mod => {
      const open = mod.openCalendarTo || (mod.default && mod.default.openCalendarTo);
      if (open) open(id);
    }).catch(() => {});
  } else if (kind === 'task') {
    import('./tasks.js?v=20260914taskmodel1').then(mod => {
      const open = mod.openTasks || (mod.default && mod.default.openTasks);
      if (open) open(id);
      else { const b = document.getElementById('tasks-btn'); if (b) b.click(); }
    }).catch(() => { const b = document.getElementById('tasks-btn'); if (b) b.click(); });
  } else if (kind === 'skill') {
    import('./skills.js?v=20260909kebabconsistency1').then(mod => {
      const open = mod.openSkill || (mod.default && mod.default.openSkill);
      if (open) open(id);
    }).catch(() => {});
  } else if (kind === 'research') {
    import('./research/panel.js?v=20260913researchrailerrors1').then(mod => {
      const open = mod.openPanel || (mod.default && mod.default.openPanel);
      if (open) open(id);
    }).catch(() => {});
  } else if (kind === 'cookbook') {
    import('./cookbook.js').then(mod => {
      const open = mod.open || (mod.default && mod.default.open);
      if (!open) return;
      if (id.startsWith('session-')) open({ tab: 'Running', focusSession: id.slice(8) });
      else if (id.startsWith('model-')) open({ tab: 'Serve', focusRepo: id.slice(6).replace('~', '/') });
      else open();
    }).catch(() => {});
  }
}

// The live Markdown tail is replaced as tokens arrive. On touch devices that
// can detach an anchor between pointerdown and the browser's synthetic click,
// making links appear ready but inert until streaming ends. Preserve the
// pointerdown anchor and activate it on a stationary pointerup; suppress the
// duplicate click that browsers emit immediately afterward.
let _streamEntityPointer = null;
let _streamEntitySuppressClick = null;
const _isEntityHref = (href) => /^#(?:(?:calendar|memory|notes|tasks|documents|skills|research)$|(?:session|document|note|memory|image|email|event|task|skill|research|cookbook)-)/.test(String(href || ''));
document.addEventListener('pointerdown', function(e) {
  const a = e.target?.closest?.('#chat-history .msg-ai.streaming a[href]');
  const href = a?.getAttribute('href') || '';
  _streamEntityPointer = a && _isEntityHref(href)
    ? { a, href, x: e.clientX, y: e.clientY, pointerId: e.pointerId }
    : null;
}, true);
document.addEventListener('pointerup', function(e) {
  const pending = _streamEntityPointer;
  _streamEntityPointer = null;
  if (!pending || pending.pointerId !== e.pointerId) return;
  if (Math.hypot(e.clientX - pending.x, e.clientY - pending.y) > 10) return;
  _streamEntitySuppressClick = { href: pending.href, until: performance.now() + 800 };
  _activateEntityAnchor(e, pending.a);
}, true);
document.addEventListener('pointercancel', function() {
  _streamEntityPointer = null;
}, true);
document.addEventListener('click', function(e) {
  const a = e.target?.closest?.('a[href]');
  const href = a?.getAttribute('href') || '';
  if (_streamEntitySuppressClick
      && performance.now() <= _streamEntitySuppressClick.until
      && href === _streamEntitySuppressClick.href) {
    _streamEntitySuppressClick = null;
    e.preventDefault();
    e.stopPropagation();
    return;
  }
  _streamEntitySuppressClick = null;
  _activateEntityAnchor(e);
}, true);

/**
 * Build a generated-image bubble element.
 */
export function buildImageBubble(imageUrl, prompt, model, size, quality, imageId) {
  var esc = uiModule.esc;
  const wrap = document.createElement('div');
  wrap.className = 'msg msg-ai generated-image-wrap';
  wrap.dataset.imageUrl = imageUrl || '';
  wrap.dataset.imageKey = String(imageId || imageUrl || '');
  if (imageId) wrap.dataset.imageId = imageId;

  const role = document.createElement('div');
  role.className = 'role';
  role.textContent = (model || 'image').split('/').pop();
  wrap.appendChild(role);

  const body = document.createElement('div');
  body.className = 'body';

  const safeImageUrl = safeDisplayImageSrc(imageUrl);
  if (!safeImageUrl) {
    body.textContent = '[Image unavailable]';
    wrap.appendChild(body);
    return wrap;
  }

  const img = document.createElement('img');
  img.className = 'generated-image';
  img.alt = prompt || 'Generated image';
  img.title = prompt || 'Generated image';
  img.src = safeImageUrl;
  img.addEventListener('click', () => { window.open(safeImageUrl, '_blank', 'noopener,noreferrer'); });
  body.appendChild(img);

  if (prompt) {
    const caption = document.createElement('div');
    caption.className = 'generated-image-caption';
    caption.textContent = prompt;
    body.appendChild(caption);
  }

  wrap.appendChild(body);

  const footer = document.createElement('div');
  footer.className = 'msg-footer';

  const actions = document.createElement('span');
  actions.className = 'msg-actions';

  const copyBtn = document.createElement('button');
  copyBtn.className = 'footer-copy-btn';
  copyBtn.type = 'button';
  copyBtn.title = 'Copy prompt';
  copyBtn.innerHTML = COPY_ICON;
  copyBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    uiModule.copyToClipboard(prompt || '');
    copyBtn.innerHTML = CHECK_ICON;
    setTimeout(() => { copyBtn.innerHTML = COPY_ICON; }, 1500);
  });
  actions.appendChild(copyBtn);

  const dlBtn = document.createElement('button');
  dlBtn.className = 'footer-copy-btn';
  dlBtn.type = 'button';
  dlBtn.title = 'Download image';
  dlBtn.textContent = '\u2913';
  dlBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    try {
      const resp = await fetch(imageUrl);
      const blob = await resp.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = (prompt || 'image').slice(0, 40).replace(/[^a-zA-Z0-9 ]/g, '') + '.png';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(a.href);
      dlBtn.textContent = '\u2713';
      setTimeout(() => { dlBtn.textContent = '\u2913'; }, 1500);
    } catch { dlBtn.textContent = '\u2717'; setTimeout(() => { dlBtn.textContent = '\u2913'; }, 1500); }
  });
  actions.appendChild(dlBtn);

  const reuseBtn = document.createElement('button');
  reuseBtn.className = 'footer-copy-btn';
  reuseBtn.type = 'button';
  reuseBtn.title = 'Attach image to new prompt';
  reuseBtn.innerHTML = PAPERCLIP_ICON;
  reuseBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    reuseBtn.disabled = true;
    try {
      const resp = await fetch(safeImageUrl, { credentials: 'same-origin' });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const ext = (blob.type || '').includes('jpeg') ? 'jpg'
        : (blob.type || '').includes('webp') ? 'webp'
        : (blob.type || '').includes('gif') ? 'gif'
        : 'png';
      const base = (prompt || 'generated-image').slice(0, 36).replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'generated-image';
      const file = new File([blob], `${base}.${ext}`, { type: blob.type || 'image/png', lastModified: Date.now() });
      const mod = await import('./fileHandler.js?v=20260909mobileattachmentedit1');
      const addFiles = mod.addFiles || (mod.default && mod.default.addFiles);
      if (!addFiles) throw new Error('attachment handler unavailable');
      await addFiles([file], { skipCrop: true });
      const input = document.getElementById('message');
      if (input) input.focus();
      reuseBtn.innerHTML = CHECK_ICON;
      if (window.showToast) window.showToast('Image attached');
      setTimeout(() => { reuseBtn.innerHTML = PAPERCLIP_ICON; reuseBtn.disabled = false; }, 1400);
    } catch (err) {
      console.warn('Attach generated image failed', err);
      reuseBtn.textContent = '\u2717';
      if (window.showToast) window.showToast('Could not attach image');
      setTimeout(() => { reuseBtn.innerHTML = PAPERCLIP_ICON; reuseBtn.disabled = false; }, 1600);
    }
  });
  actions.appendChild(reuseBtn);

  const editBtn = document.createElement('button');
  editBtn.className = 'footer-copy-btn';
  editBtn.type = 'button';
  editBtn.title = 'Edit in image editor';
  editBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>';
  editBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    try {
      const [galleryMod, editorMod] = await Promise.all([
        import('./gallery.js?v=20260910promptcopy1'),
        loadPanel('editor'),
      ]);
      // Ensure the Gallery modal is open so the editor has a container
      // to render into; switch its tabs to the Edit tab.
      galleryMod.default.openGallery();
      const modal = document.getElementById('gallery-modal');
      if (modal) {
        modal.querySelectorAll('.gallery-tab').forEach(t => t.classList.remove('active'));
        modal.querySelector('.gallery-tab[data-tab="editor"]')?.classList.add('active');
      }
      const imagesContainer = document.getElementById('gallery-images-container');
      const albumsContainer = document.getElementById('gallery-albums-container');
      if (imagesContainer) imagesContainer.style.display = 'none';
      if (albumsContainer) albumsContainer.style.display = 'none';
      const editorContainer = document.getElementById('gallery-editor-container');
      if (editorContainer) editorContainer.style.display = 'flex';
      const label = (prompt || '').trim().slice(0, 60) || 'Generated image';
      editorMod.openEditor(imageUrl, null, null, label);
    } catch (err) {
      console.error('[chat] open in editor failed', err);
    }
  });
  actions.appendChild(editBtn);

  if (imageId) {
    const galleryBtn = document.createElement('button');
    galleryBtn.className = 'footer-copy-btn footer-open-gallery-btn';
    galleryBtn.type = 'button';
    galleryBtn.title = 'Open in gallery';
    galleryBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg><span>Open in gallery</span>';
    galleryBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      try {
        const mod = await import('./gallery.js?v=20260910promptcopy1');
        const open = mod.openGalleryImage || (mod.default && mod.default.openGalleryImage);
        if (open) open(imageId);
      } catch (err) {
        console.error('[chat] open in gallery failed', err);
      }
    });
    actions.appendChild(galleryBtn);
  }

  const delBtn = document.createElement('button');
  delBtn.className = 'footer-copy-btn footer-delete-btn';
  delBtn.type = 'button';
  delBtn.title = 'Delete image';
  delBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>';
  delBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    const ok = await uiModule.styledConfirm('Delete this image?', {
      confirmText: 'Delete',
      cancelText: 'Cancel',
      danger: true,
    });
    if (!ok) return;
    // If we have a gallery id, delete server-side; otherwise just remove
    // the bubble from chat (e.g. external DALL-E url that wasn't saved).
    if (imageId) {
      try {
        const res = await fetch(`/api/gallery/${encodeURIComponent(imageId)}`, {
          method: 'DELETE', credentials: 'same-origin',
        });
        if (!res.ok && res.status !== 404) {
          uiModule.showToast?.('Delete failed', 4000);
          return;
        }
        window.dispatchEvent(new CustomEvent('gallery-refresh'));
      } catch (_) {
        uiModule.showToast?.('Delete failed', 4000);
        return;
      }
    }
    wrap.remove();
  });
  actions.appendChild(delBtn);

  footer.appendChild(actions);

  const metrics = document.createElement('span');
  metrics.className = 'response-metrics';
  const parts = [];
  if (model) parts.push(model.split('/').pop());
  if (size) parts.push(size);
  if (quality) parts.push(quality);
  const cost = getImageCost(model, quality, size);
  if (cost !== null) parts.push('$' + (cost < 0.01 ? cost.toFixed(4) : cost.toFixed(3)));
  metrics.textContent = parts.join(' \u00B7 ');
  footer.appendChild(metrics);

  wrap.appendChild(footer);
  return wrap;
}

export function hideWelcomeScreen() {
  const ws = document.getElementById('welcome-screen');
  const cc = document.getElementById('chat-container');
  if (ws) ws.classList.add('hidden');
  if (cc) cc.classList.remove('welcome-active');
  // Update send button — switches from muted arrow to + Chat
  if (window._updateSendBtnIcon) setTimeout(window._updateSendBtnIcon, 50);
  const ib = document.getElementById('incognito-btn');
  if (ib) ib.style.display = ib.classList.contains('active') ? '' : 'none';
}

export function showWelcomeScreen() {
  const ws = document.getElementById('welcome-screen');
  const cc = document.getElementById('chat-container');
  const alreadyVisible = !!(ws && !ws.classList.contains('hidden'));
  if (ws) ws.classList.remove('hidden');
  if (cc) cc.classList.add('welcome-active');
  if (alreadyVisible) {
    return;
  }
  // Entering the New Chat / welcome state: discard any stale draft left in the
  // composer from the previous session so the input starts empty (issue #1343).
  // Switching between existing sessions loads them directly and does NOT call
  // this, so genuine drafts are not erased. Reset the autosized height and fire
  // an `input` event so the send button + autosize listeners update.
  const _msg = document.getElementById('message');
  if (_msg) {
    _msg.value = '';
    _msg.style.height = '';
    _msg.dispatchEvent(new Event('input', { bubbles: true }));
  }
  // Re-trigger the L→R clip-wipe reveal on the welcome name each time the
  // welcome screen is shown (new session, deleted last session, etc.) — without
  // this, the CSS animation only fires on initial DOM insertion.
  const wn = document.querySelector('.welcome-name');
  if (wn) {
    wn.style.animation = 'none';
    // force reflow so the next assignment registers as a new animation
    void wn.offsetHeight;
    wn.style.animation = '';
  }
  // Update send button — switches from + Chat to muted arrow on empty session
  if (window._updateSendBtnIcon) setTimeout(window._updateSendBtnIcon, 50);
  const ib = document.getElementById('incognito-btn');
  const _researchChk = document.getElementById('research-toggle');
  if (ib && !(_researchChk && _researchChk.checked)) ib.style.display = '';
  if (window.innerWidth > 768) {
    const msg = document.getElementById('message');
    if (msg) msg.focus();
  }
}

// ── Dynamic action buttons (show 3 most recent, rest under ···) ──
const _ACTION_RECENTS_KEY = 'odysseus-msg-actions-recent';
const _MAX_VISIBLE = 2;

function _getRecentActions() {
  try { return JSON.parse(localStorage.getItem(_ACTION_RECENTS_KEY) || '[]'); } catch { return []; }
}
function _trackAction(id) {
  let recent = _getRecentActions().filter(x => x !== id);
  recent.unshift(id);
  if (recent.length > 10) recent.length = 10;
  localStorage.setItem(_ACTION_RECENTS_KEY, JSON.stringify(recent));
}

/**
 * Create a footer row for an AI message with timestamp and action buttons.
 */
export function createMsgFooter(msgElement, { animate = false } = {}) {
  const footer = document.createElement('div');
  footer.className = 'msg-footer';
  if (animate) footer.classList.add('msg-footer-enter');

  const actions = document.createElement('span');
  actions.className = 'msg-actions';

  // Define all available actions: { id, icon, title, className, handler }
  const allActions = [
    { id: 'copy', icon: COPY_ICON, title: 'Copy message', cls: 'footer-copy-btn', html: true, handler(e) {
      e.stopPropagation();
      const btn = e.currentTarget;
      uiModule.copyToClipboard(copyMessageText(msgElement));
      btn.innerHTML = CHECK_ICON;
      setTimeout(() => { btn.innerHTML = COPY_ICON; }, 1500);
    }},
    { id: 'edit', icon: '\u270E', title: 'Edit', cls: 'msg-action-btn', handler(e) {
      e.stopPropagation();
      if (window.chatModule?.editAIMessage) window.chatModule.editAIMessage(msgElement);
    }},
    { id: 'regen', icon: RESEND_ICON, title: 'Regenerate from here', cls: 'msg-action-btn', html: true, handler(e) {
      e.stopPropagation();
      if (window.chatModule?.regenerateFrom) window.chatModule.regenerateFrom(msgElement);
    }},
    { id: 'shorten', icon: '\u2702', title: 'Rewrite shorter', cls: 'msg-action-btn', handler(e) {
      e.stopPropagation();
      if (window.chatModule?.rewriteWith) window.chatModule.rewriteWith(msgElement, 'Rewrite your last response to be shorter and more concise. Keep the key information but cut the fluff.');
    }},
    { id: 'explain', icon: '?', title: 'Explain simpler', cls: 'msg-action-btn', handler(e) {
      e.stopPropagation();
      if (window.chatModule?.rewriteWith) window.chatModule.rewriteWith(msgElement, 'Explain your last response in simpler terms. Use plain language and short sentences.');
    }},
    { id: 'fork', icon: '\u2ADD', title: 'Fork conversation', cls: 'msg-action-btn', handler(e) {
      e.stopPropagation();
      if (window.chatModule?.forkFrom) window.chatModule.forkFrom(msgElement);
    }},
    { id: 'delete', icon: TRASH_ICON, title: 'Delete message', cls: 'msg-action-btn msg-delete-btn', html: true, handler(e) {
      e.stopPropagation();
      if (window.chatModule?.deleteMessage) window.chatModule.deleteMessage(msgElement);
    }},
  ];

  // Filter out unavailable actions (e.g. TTS when not enabled)
  const availableActions = allActions.filter(a => !a.available || a.available());

  // Determine which 3 to show: use recent order, fallback to defaults
  const recent = _getRecentActions();
  const defaults = ['copy', 'delete', 'fork'];
  const order = recent.length > 0 ? recent : defaults;
  const sorted = [...availableActions].sort((a, b) => {
    const ai = order.indexOf(a.id), bi = order.indexOf(b.id);
    if (ai >= 0 && bi >= 0) return ai - bi;
    if (ai >= 0) return -1;
    if (bi >= 0) return 1;
    return 0;
  });
  const visible = sorted.slice(0, _MAX_VISIBLE);
  const overflow = sorted.slice(_MAX_VISIBLE);

  // Render visible buttons
  function _addBtn(action, container) {
    const btn = _makeActionBtn(action.cls, action.title, action.html ? '' : action.icon, (e) => {
      _trackAction(action.id);
      action.handler(e);
    });
    if (action.html) btn.innerHTML = action.icon;
    btn.dataset.action = action.id;
    container.appendChild(btn);
  }

  visible.forEach(a => _addBtn(a, actions));

  // Overflow "···" button
  if (overflow.length > 0) {
    const moreBtn = document.createElement('button');
    moreBtn.className = 'msg-action-btn msg-more-btn';
    moreBtn.type = 'button';
    moreBtn.title = 'More actions';
    moreBtn.textContent = '\u00B7\u00B7\u00B7';
    moreBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      // Toggle overflow menu — close any existing one first (through its own
      // dismiss so the Escape registry entry goes with it).
      const existing = document.querySelector('.msg-overflow-menu');
      if (existing) {
        if (typeof existing._dismiss === 'function') existing._dismiss(); else existing.remove();
        if (existing._trigger === moreBtn) return;
      }

      const menu = document.createElement('div');
      menu.className = 'msg-overflow-menu';
      let closeMenu = () => menu.remove();
      overflow.forEach(a => {
        const item = document.createElement('button');
        item.className = 'msg-overflow-item';
        item.type = 'button';
        item.title = a.title;
        item.innerHTML = `<span class="overflow-icon">${a.icon}</span> ${a.title}`;
        item.addEventListener('click', (ev) => {
          ev.stopPropagation();
          _trackAction(a.id);
          closeMenu();
          a.handler(ev);
        });
        menu.appendChild(item);
      });
      menu._trigger = moreBtn;
      document.body.appendChild(menu);
      // Position fixed relative to the ··· button
      const btnRect = moreBtn.getBoundingClientRect();
      menu.style.top = (btnRect.top - menu.offsetHeight - 4) + 'px';
      menu.style.left = btnRect.left + 'px';
      // Flip down if above viewport
      if (parseFloat(menu.style.top) < 8) menu.style.top = (btnRect.bottom + 4) + 'px';
      // Keep within right edge
      const mr = menu.getBoundingClientRect();
      if (mr.right > window.innerWidth - 8) menu.style.left = (window.innerWidth - mr.width - 8) + 'px';
      // Close on outside click or Escape. The trigger button is treated as
      // "inside" so its own click toggles rather than double-fires.
      closeMenu = bindMenuDismiss(menu, () => menu.remove(), (ev) => !menu.contains(ev.target) && ev.target !== moreBtn);    });
    actions.appendChild(moreBtn);
  }

  // Memory-used indicator pill
  const mems = msgElement._memoriesUsed;
  if (mems && mems.length > 0) {
    const pill = document.createElement('button');
    pill.className = 'memory-used-pill';
    pill.type = 'button';
    const pinnedCount = mems.filter(m => m.type === 'pinned').length;
    const recalledCount = mems.filter(m => m.type === 'recalled').length;
    const parts = [];
    if (pinnedCount) parts.push(`${pinnedCount} pinned`);
    if (recalledCount) parts.push(`${recalledCount} recalled`);
    pill.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:3px"><path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/><path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4"/></svg><span class="memory-used-pill-text">${parts.join(', ')}</span>`;
    pill.title = mems.map(m => `[${m.type}] ${m.text}`).join('\n');

    pill.addEventListener('click', (e) => {
      e.stopPropagation();
      let detail = pill._openDetail || document.querySelector('.memory-used-detail');
      if (detail) {
        if (typeof detail._dismiss === 'function') detail._dismiss();
        else { detail.remove(); pill._openDetail = null; }
        return;
      }
      detail = document.createElement('div');
      detail.className = 'memory-used-detail';
      let closeDetail = () => { detail.remove(); pill._openDetail = null; };
      mems.forEach(m => {
        const row = document.createElement('div');
        row.className = 'memory-used-row';
        row.style.cursor = 'pointer';
        row.title = 'Click to open memory manager';
        const badge = document.createElement('span');
        badge.className = 'memory-used-badge ' + (m.type === 'pinned' ? 'pinned' : 'recalled');
        badge.textContent = m.type === 'pinned' ? '\u25CF' : '\u21BB';
        const text = document.createElement('span');
        text.className = 'memory-used-text';
        text.textContent = m.text;
        row.appendChild(badge);
        row.appendChild(text);
        row.addEventListener('click', (ev) => {
          ev.stopPropagation();
          closeDetail();
          import('./memory.js')
            .then(mod => {
              const open = mod.openMemoryModal || mod.default?.openMemoryModal;
              if (open) open('browse');
              else document.getElementById('memory-modal')?.classList.remove('hidden');
            })
            .catch(() => document.getElementById('memory-modal')?.classList.remove('hidden'));
        });
        detail.appendChild(row);
      });
      detail.style.visibility = 'hidden';
      document.body.appendChild(detail);
      const pillRect = pill.getBoundingClientRect();
      const detailRect = detail.getBoundingClientRect();
      const spaceAbove = pillRect.top;
      const spaceBelow = window.innerHeight - pillRect.bottom;
      if (spaceAbove >= detailRect.height + 8 || spaceAbove > spaceBelow) {
        detail.style.top = (pillRect.top - detailRect.height - 8) + 'px';
      } else {
        detail.style.top = (pillRect.bottom + 8) + 'px';
      }
      detail.style.left = pillRect.left + 'px';
      if (pillRect.left + detailRect.width > window.innerWidth - 8) {
        detail.style.left = (window.innerWidth - detailRect.width - 8) + 'px';
      }
      if (parseFloat(detail.style.left) < 8) detail.style.left = '8px';
      detail.style.visibility = '';
      pill._openDetail = detail;
      // Close on outside click or Escape (pill click toggles, so it's inside).
      closeDetail = bindMenuDismiss(detail, () => { detail.remove(); pill._openDetail = null; }, (ev) => !detail.contains(ev.target) && ev.target !== pill);    });

    footer.appendChild(pill);
  }

  footer.appendChild(actions);
  return footer;
}

/**
 * Create a footer row for a user message with action buttons (same system as AI footer).
 */
const _USER_ACTION_RECENTS_KEY = 'odysseus-user-actions-recent';

function _getUserRecentActions() {
  try { return JSON.parse(localStorage.getItem(_USER_ACTION_RECENTS_KEY) || '[]'); } catch { return []; }
}
function _trackUserAction(id) {
  let recent = _getUserRecentActions().filter(x => x !== id);
  recent.unshift(id);
  if (recent.length > 10) recent.length = 10;
  localStorage.setItem(_USER_ACTION_RECENTS_KEY, JSON.stringify(recent));
}

export function createUserMsgFooter(msgElement) {
  const footer = document.createElement('div');
  footer.className = 'msg-footer';

  const actions = document.createElement('span');
  actions.className = 'msg-actions';

  const allActions = [
    { id: 'edit', icon: '\u270E', title: 'Edit message', cls: 'msg-action-btn', handler(e) {
      e.stopPropagation();
      if (window.chatModule?.editUserMessage) window.chatModule.editUserMessage(msgElement);
    }},
    { id: 'delete', icon: TRASH_ICON, title: 'Delete message', cls: 'msg-action-btn msg-delete-btn', html: true, handler(e) {
      e.stopPropagation();
      if (window.chatModule?.deleteMessage) window.chatModule.deleteMessage(msgElement);
    }},
    { id: 'copy', icon: COPY_ICON, title: 'Copy message', cls: 'footer-copy-btn', html: true, handler(e) {
      e.stopPropagation();
      const btn = e.currentTarget;
      uiModule.copyToClipboard(msgElement.querySelector('.body')?.textContent || '');
      btn.innerHTML = CHECK_ICON;
      setTimeout(() => { btn.innerHTML = COPY_ICON; }, 1500);
    }},
    { id: 'resend', icon: RESEND_ICON, title: 'Resend message', cls: 'msg-action-btn', html: true, handler(e) {
      e.stopPropagation();
      if (window.chatModule?.resendUserMessage) window.chatModule.resendUserMessage(msgElement);
    }},
  ];

  const recent = _getUserRecentActions();
  const defaults = ['edit', 'delete', 'copy'];
  const order = recent.length > 0 ? recent : defaults;
  const sorted = [...allActions].sort((a, b) => {
    const ai = order.indexOf(a.id), bi = order.indexOf(b.id);
    if (ai >= 0 && bi >= 0) return ai - bi;
    if (ai >= 0) return -1;
    if (bi >= 0) return 1;
    return 0;
  });
  const visible = sorted.slice(0, _MAX_VISIBLE);
  const overflow = sorted.slice(_MAX_VISIBLE);

  visible.forEach(a => {
    const btn = _makeActionBtn(a.cls, a.title, a.html ? '' : a.icon, (ev) => {
      _trackUserAction(a.id);
      a.handler(ev);
    });
    if (a.html) btn.innerHTML = a.icon;
    btn.dataset.action = a.id;
    actions.appendChild(btn);
  });

  if (overflow.length > 0) {
    const moreBtn = document.createElement('button');
    moreBtn.className = 'msg-action-btn msg-more-btn';
    moreBtn.type = 'button';
    moreBtn.title = 'More actions';
    moreBtn.textContent = '\u00B7\u00B7\u00B7';
    moreBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const existing = document.querySelector('.msg-overflow-menu');
      if (existing) {
        if (typeof existing._dismiss === 'function') existing._dismiss(); else existing.remove();
        if (existing._trigger === moreBtn) return;
      }

      const menu = document.createElement('div');
      menu.className = 'msg-overflow-menu';
      let closeMenu = () => menu.remove();
      overflow.forEach(a => {
        const item = document.createElement('button');
        item.className = 'msg-overflow-item';
        item.type = 'button';
        item.title = a.title;
        item.innerHTML = `<span class="overflow-icon">${a.icon}</span> ${a.title}`;
        item.addEventListener('click', (ev) => {
          ev.stopPropagation();
          _trackUserAction(a.id);
          closeMenu();
          a.handler(ev);
        });
        menu.appendChild(item);
      });
      menu._trigger = moreBtn;
      document.body.appendChild(menu);
      const btnRect = moreBtn.getBoundingClientRect();
      menu.style.top = (btnRect.top - menu.offsetHeight - 4) + 'px';
      menu.style.left = btnRect.left + 'px';
      if (parseFloat(menu.style.top) < 8) menu.style.top = (btnRect.bottom + 4) + 'px';
      const mr = menu.getBoundingClientRect();
      if (mr.right > window.innerWidth - 8) menu.style.left = (window.innerWidth - mr.width - 8) + 'px';
      closeMenu = bindMenuDismiss(menu, () => menu.remove(), (ev) => !menu.contains(ev.target) && ev.target !== moreBtn);    });
    actions.appendChild(moreBtn);
  }

  footer.appendChild(actions);
  return footer;
}

/**
 * Display performance metrics for a message.
 */
export function displayMetrics(messageElement, metrics) {
  messageElement
    .querySelectorAll('.response-metrics, .metrics-divider, .ctx-divider, .ctx-ring')
    .forEach((el) => el.remove());

  const metricsContainer = document.createElement('span');
  metricsContainer.className = 'response-metrics';

  const responseTime = metrics.response_time;
  const inputTokens = metrics.input_tokens || 0;
  const outputTokens = metrics.output_tokens || 0;
  const tps = metrics.tokens_per_second;
  const ttft = metrics.client_ttft ?? metrics.time_to_first_token;
  const isReal = metrics.usage_source === 'real';
  const ctxPct = metrics.context_percent;
  const model = metrics.model || 'Unknown';
  const cost = _metricsBillableCost(
    metrics,
    model,
    inputTokens,
    outputTokens,
  );

  // Nothing useful to show — bail out (only if ALL metrics are missing)
  if (!responseTime && !inputTokens && !outputTokens && tps == null && !ctxPct) return;

  // Rendering can occur when metrics arrive and again after [DONE]. The
  // ledger mutation is idempotent for that shared payload.
  recordSessionMetricsCost(metrics);

  // Keep token counts in the Message Stats popup; the footer should stay slim.
  const costStr0 = cost !== null ? `$${cost < 0.01 ? cost.toFixed(4) : cost.toFixed(3)}` : null;
  const hasTps = tps != null && tps !== 'undefined' && Number.isFinite(Number(tps));
  const tpsText = hasTps ? `${Number(tps).toFixed(2)} tok/s` : '';
  const metricsLabel = hasTps
    ? tpsText
    : costStr0
      ? costStr0
      : responseTime != null
        ? `${Number(responseTime).toFixed(3)}s`
        : '';
  if (!metricsLabel) return;
  metricsContainer.textContent = metricsLabel;
  metricsContainer.style.cursor = 'pointer';
  metricsContainer.title = 'Click for details';
  const metricsDivider = document.createElement('span');
  metricsDivider.className = 'metrics-divider';
  metricsDivider.textContent = ' | ';
  metricsDivider.style.color = 'var(--color-muted-alt)';
  metricsDivider.style.pointerEvents = 'none';
  metricsContainer.addEventListener('click', (e) => {
    e.stopPropagation();
    document.querySelectorAll('.ctx-popup').forEach(p => { if (typeof p._dismiss === 'function') p._dismiss(); else p.remove(); });

    const costStr = cost !== null ? `$${cost < 0.01 ? cost.toFixed(4) : cost.toFixed(3)}` : '';
    const costRows = costStr ? `<div class="ctx-stat-row"><span class="ctx-label">Cost</span><span class="ctx-stat-value">${costStr}</span></div>` : '';
    const speedStr = hasTps ? tpsText : 'n/a';
    const speedLabel = metrics.tps_source === 'computed' ? 'Speed (wall)' : 'Speed';
    const totalTok = inputTokens + outputTokens;
    const ctxColor = ctxPct >= 85 ? 'var(--red, #e06c75)' : ctxPct >= 70 ? '#ff9900' : 'var(--color-muted-alt, #6b7280)';
    const prepTime = metrics.agent_prep_time;
    const modelWaitTime = metrics.agent_model_wait_time;
    const visibleTtft = metrics.client_ttft ?? metrics.time_to_first_token;
    const schemaCount = metrics.tool_schema_count;
    const agentRounds = metrics.agent_rounds;
    const toolCalls = metrics.tool_calls;
    const prepBreakdown = metrics.agent_prep_breakdown || null;
    const prepDetails = prepBreakdown
      ? Object.entries(prepBreakdown).map(([k, v]) =>
          `<div class="ctx-stat-row"><span class="ctx-label">${uiModule.esc(k.replaceAll('_', ' '))}</span><span class="ctx-stat-value">${Number(v).toFixed(3)}s</span></div>`).join('')
      : '';

    // Session total cost
    let sessionCostStr = '';
    const sc = getSessionCost();
    if (costStr && sc > 0) {
      sessionCostStr = `<div class="ctx-stat-row"><span class="ctx-label">Session</span><span class="ctx-stat-value">$${sc < 0.01 ? sc.toFixed(4) : sc.toFixed(3)}</span></div>`;
    }

    const popup = document.createElement('div');
    popup.className = 'ctx-popup';
    popup.innerHTML = `
      <div class="ctx-popup-title">Message stats</div>
      <div class="ctx-stat-section">
        <div class="ctx-stat-row"><span class="ctx-label">Model</span><span class="ctx-stat-value">${model.split('/').pop()}</span></div>
        <div class="ctx-stat-row"><span class="ctx-label">Input</span><span class="ctx-stat-value">${inputTokens.toLocaleString()} tokens${isReal ? '' : '~'}</span></div>
        <div class="ctx-stat-row"><span class="ctx-label">Output</span><span class="ctx-stat-value">${outputTokens.toLocaleString()} tokens${isReal ? '' : '~'}</span></div>
        <div class="ctx-stat-row"><span class="ctx-label">Total</span><span class="ctx-stat-value">${totalTok.toLocaleString()} tokens</span></div>
      </div>
      <div class="ctx-stat-section">
        <div class="ctx-stat-row"><span class="ctx-label">${speedLabel}</span><span class="ctx-stat-value">${speedStr}</span></div>
        <div class="ctx-stat-row"><span class="ctx-label">Time</span><span class="ctx-stat-value">${Number(responseTime).toFixed(3)}s</span></div>
        ${prepTime != null ? `<div class="ctx-stat-row"><span class="ctx-label">Prep</span><span class="ctx-stat-value">${prepTime}s</span></div>` : ''}
        ${modelWaitTime != null ? `<div class="ctx-stat-row"><span class="ctx-label">Model wait</span><span class="ctx-stat-value">${modelWaitTime}s</span></div>` : ''}
        ${visibleTtft != null ? `<div class="ctx-stat-row"><span class="ctx-label">TTFT</span><span class="ctx-stat-value">${Number(visibleTtft).toFixed(3)}s</span></div>` : ''}
      </div>
      <div class="ctx-stat-section">
        ${schemaCount != null ? `<div class="ctx-stat-row ctx-tool-schemas" tabindex="0"><span class="ctx-label">Tool schemas</span><span class="ctx-stat-value">${Number(schemaCount).toLocaleString()}</span></div>` : ''}
        ${agentRounds != null ? `<div class="ctx-stat-row"><span class="ctx-label">Agent rounds</span><span class="ctx-stat-value">${Number(agentRounds).toLocaleString()}</span></div>` : ''}
        ${toolCalls != null ? `<div class="ctx-stat-row"><span class="ctx-label">Tool calls</span><span class="ctx-stat-value">${Number(toolCalls).toLocaleString()}</span></div>` : ''}
        ${costRows}
        ${sessionCostStr}
      </div>
      ${prepDetails ? `<div class="ctx-stat-section">${prepDetails}</div>` : ''}
      ${ctxPct !== undefined && ctxPct > 0 ? `<div style="margin-top:6px;padding-top:6px;border-top:1px solid var(--border);">
        <span class="ctx-label">Context</span> <span style="color:${ctxColor};font-weight:600;">${ctxPct}%</span> used
      </div>` : ''}
      ${isReal ? '' : '<div style="margin-top:4px;font-size:0.8em;opacity:0.4;">~ estimated token count</div>'}
    `;

    const schemaRow = popup.querySelector('.ctx-tool-schemas');
    if (schemaRow) {
      const names = Array.isArray(metrics.tool_schema_names)
        ? metrics.tool_schema_names.filter(name => typeof name === 'string' && name.trim())
        : [];
      const details = names.length
        ? `Available tools:\n${names.join('\n')}`
        : Number(schemaCount) === 0 ? 'No tools offered.' : 'Tool names were not recorded for this response.';
      schemaRow.title = details;
      schemaRow.setAttribute('aria-label', `Tool schemas: ${schemaCount}. ${details}`);
      schemaRow.style.cursor = 'help';
    }

    const rect = metricsContainer.getBoundingClientRect();
    popup.style.left = rect.left + 'px';
    popup.style.visibility = 'hidden';
    document.body.appendChild(popup);
    const pr = popup.getBoundingClientRect();
    const spaceAbove = rect.top;
    const spaceBelow = window.innerHeight - rect.bottom;
    if (spaceAbove >= pr.height + 8 || spaceAbove > spaceBelow) {
      popup.style.top = (rect.top - pr.height - 8) + 'px';
    } else {
      popup.style.top = (rect.bottom + 8) + 'px';
    }
    if (pr.right > window.innerWidth - 8) popup.style.left = (window.innerWidth - pr.width - 8) + 'px';
    if (parseFloat(popup.style.left) < 8) popup.style.left = '8px';
    popup.style.visibility = '';

    bindMenuDismiss(popup, () => popup.remove());
  });

  // Store real context length for model info popup
  if (metrics.context_length && metrics.model) {
    if (!window._realContextLengths) window._realContextLengths = {};
    window._realContextLengths[metrics.model] = metrics.context_length;
  }

  // Context usage ring
  let ctxRing = null;
  const ctxLen = metrics.context_length || 0;
  if (ctxPct !== undefined && ctxPct > 0) {
    const r = 6, stroke = 1.5;
    const circ = 2 * Math.PI * r;
    const fill = circ * (ctxPct / 100);
    const ctxColor = ctxPct >= 85 ? 'var(--red, #e06c75)' : ctxPct >= 70 ? '#ff9900' : 'var(--green, #98c379)';
    ctxRing = document.createElement('span');
    ctxRing.className = 'ctx-ring';
    ctxRing.title = `${ctxPct}% context used — click for details`;
    ctxRing.style.cursor = 'pointer';
    ctxRing.style.setProperty('--ctx-color', ctxColor);
    ctxRing.innerHTML = `<svg width="14" height="14" viewBox="0 0 14 14">
      <circle cx="7" cy="7" r="${r}" fill="none" stroke="var(--border, #333)" stroke-width="${stroke}" opacity="0.3"/>
      <circle cx="7" cy="7" r="${r}" fill="none" stroke="var(--ctx-stroke)" stroke-width="${stroke}"
        stroke-dasharray="${fill} ${circ - fill}" stroke-dashoffset="${circ * 0.25}"
        stroke-linecap="round" transform="rotate(-90 7 7)"/>
    </svg><span class="ctx-ring-pct">${Math.round(ctxPct)}%</span>`;

    ctxRing.addEventListener('click', (e) => {
      e.stopPropagation();
      document.querySelectorAll('.ctx-detail-popup').forEach(p => { if (typeof p._dismiss === 'function') p._dismiss(); else p.remove(); });

      const usedTokens = inputTokens || 0;
      const totalCtx = ctxLen || 0;
      const modelShort = model.split('/').pop();
      const fmtNum = n => n ? n.toLocaleString() : '?';

      const popup = document.createElement('div');
      popup.className = 'ctx-detail-popup';
      popup.innerHTML = `
        <div style="font-weight:600;margin-bottom:8px;color:var(--fg);">Context Window</div>
        <div class="ctx-bar-wrap">
          <div class="ctx-bar-fill" style="width:${Math.min(ctxPct, 100)}%;background:${ctxColor};"></div>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:0.75rem;margin-top:4px;opacity:0.6;">
          <span>${fmtNum(usedTokens)} used</span>
          <span>${fmtNum(totalCtx)} total</span>
        </div>
        <div style="margin-top:8px;font-size:0.8rem;">
          <div><span class="ctx-label">Model</span> ${modelShort}</div>
          <div><span class="ctx-label">Usage</span> <span style="color:${ctxColor};font-weight:600;">${ctxPct}%</span></div>
          <div><span class="ctx-label">Window</span> ${fmtNum(totalCtx)} tokens</div>
        </div>
        ${ctxPct >= 70 ? `<button class="ctx-compact-btn" title="Summarize older messages to free up context">Compact context</button>` : ''}
      `;

      const compactBtn = popup.querySelector('.ctx-compact-btn');
      if (compactBtn) {
        compactBtn.addEventListener('click', async (e) => {
          e.stopPropagation();
          const sid = window.sessionModule && window.sessionModule.getCurrentSessionId();
          if (!sid) return;
          popup.remove();

          // Add a spinner bubble at the bottom of chat
          const chatBox = document.getElementById('chat-history');
          if (!chatBox) return;
          const compactMsg = document.createElement('div');
          compactMsg.className = 'msg msg-ai';
          const compactRole = document.createElement('div');
          compactRole.className = 'role';
          compactRole.textContent = 'Odysseus';
          const compactBody = document.createElement('div');
          compactBody.className = 'body';
          compactBody.innerHTML = 'Compacting context <span class="compact-wave">▁▂▃▅▂▁</span>';
          compactMsg.appendChild(compactRole);
          compactMsg.appendChild(compactBody);
          chatBox.appendChild(compactMsg);
          chatBox.scrollTop = chatBox.scrollHeight;

          // Animate the wave
          const waveFrames = ['▁▂▃▅▂▁', '▂▃▅▃▂▁', '▃▅▃▂▁▂', '▅▃▂▁▂▃', '▃▂▁▂▃▅', '▂▁▂▃▅▃'];
          let frame = 0;
          const waveEl = compactBody.querySelector('.compact-wave');
          const waveInterval = setInterval(() => {
            frame = (frame + 1) % waveFrames.length;
            if (waveEl) waveEl.textContent = waveFrames[frame];
          }, 150);

          try {
            const res = await fetch(window.location.origin + '/api/session/' + sid + '/compact', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
            });
            clearInterval(waveInterval);
            if (res.ok) {
              const data = await res.json();
              // Reload session — the compacted history will show
              if (window.sessionModule) await window.sessionModule.selectSession(sid);
              // Scroll to the compacted message (first msg with compacted metadata)
              setTimeout(() => {
                const msgs = document.querySelectorAll('#chat-history .msg');
                for (const m of msgs) {
                  if (m.querySelector('.body')?.textContent.includes('Conversation compacted')) {
                    m.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    break;
                  }
                }
              }, 200);
            } else {
              let detail = 'Compaction failed. Try again later.';
              try {
                const err = await res.json();
                if (err.detail) detail = err.detail;
              } catch {}
              compactBody.textContent = detail;
              compactBody.style.color = 'var(--red)';
            }
          } catch (err) {
            clearInterval(waveInterval);
            console.warn('compact failed:', err);
            compactBody.innerHTML = '<span style="color:var(--red);">Compaction failed: ' + err.message + '</span>';
          }
        });
      }

      const rect = ctxRing.getBoundingClientRect();
      popup.style.visibility = 'hidden';
      document.body.appendChild(popup);
      const pr = popup.getBoundingClientRect();
      // Position above the ring, right-aligned
      popup.style.left = Math.max(8, rect.right - pr.width) + 'px';
      const spaceAbove = rect.top;
      if (spaceAbove >= pr.height + 8) {
        popup.style.top = (rect.top - pr.height - 8) + 'px';
      } else {
        popup.style.top = (rect.bottom + 8) + 'px';
      }
      popup.style.visibility = '';

      bindMenuDismiss(popup, () => popup.remove(), (ev) => !popup.contains(ev.target) && ev.target !== ctxRing && !ctxRing.contains(ev.target));
    });
  }

  let footer = messageElement.querySelector('.msg-footer');
  if (!footer) {
    footer = createMsgFooter(messageElement, {
      animate: messageElement.classList?.contains('streaming'),
    });
    if (messageElement.classList?.contains('agent-thread')) {
      footer.classList.add('agent-thread-footer');
    }
    messageElement.appendChild(footer);
  }
  if (footer) {
    const actions = footer.querySelector('.msg-actions');
    if (actions) {
      footer.insertBefore(metricsDivider, actions);
      footer.insertBefore(metricsContainer, metricsDivider);
    } else {
      footer.appendChild(metricsContainer);
      footer.appendChild(metricsDivider);
    }
    if (ctxRing) {
      const ctxDiv = document.createElement('span');
      ctxDiv.textContent = ' | ';
      ctxDiv.style.color = 'var(--color-muted-alt)';
      ctxDiv.style.pointerEvents = 'none';
      ctxDiv.className = 'ctx-divider';
      footer.appendChild(ctxDiv);
      footer.appendChild(ctxRing);
    }
  } else {
    messageElement.appendChild(metricsContainer);
    if (ctxRing) messageElement.appendChild(ctxRing);
  }

  if (uiModule) uiModule.scrollHistory();
}

/** Remove any unanswered multiple-choice cards currently in the chat. */
export function removeAskUserCards(root) {
  const scope = root || document.getElementById('chat-history') || document;
  scope.querySelectorAll('.ask-user-card:not(.ask-user-answered)').forEach((node) => {
    const previous = node.previousElementSibling;
    if (previous?.classList?.contains('agent-thread')) {
      previous.classList.remove('has-ask-user-bottom');
      if (previous.dataset.askUserAttachedBottom === 'true') {
        previous.classList.remove('has-bottom');
        delete previous.dataset.askUserAttachedBottom;
      }
    }
    node.remove();
  });
}

function _markAskUserAnswered(card, text) {
  if (!card || card.classList.contains('ask-user-answered')) return;
  const answer = String(text || '').trim();
  card.classList.add('ask-user-answered');
  card.dataset.askUserAnswered = answer;
  card.querySelectorAll('button, input').forEach((el) => { el.disabled = true; });
  card.querySelector('.ask-user-options')?.remove();
  card.querySelector('.ask-user-other')?.remove();

  const row = document.createElement('div');
  row.className = 'ask-user-answer';
  const label = document.createElement('span');
  label.className = 'ask-user-answer-label';
  label.textContent = 'Answered';
  const value = document.createElement('span');
  value.className = 'ask-user-answer-value';
  value.innerHTML = svgifyEmoji(uiModule.esc(answer || 'Sent'));
  row.appendChild(label);
  row.appendChild(value);
  card.appendChild(row);
}

function _answerAskUserCards(root, text) {
  const scope = root || document.getElementById('chat-history') || document;
  scope.querySelectorAll('.ask-user-card:not(.ask-user-answered)').forEach((node) => {
    _markAskUserAnswered(node, text);
  });
}

// While a choice card is visible, let plain 1–3 activate the corresponding
// rendered option. Reuse the option's click path so the question keeps its
// existing submission semantics. Tool approval cards are excluded: that card
// exists to make consent deliberate after untrusted context influenced the
// run, and its first option is the widest grant, so a stray digit must not
// answer it.
function _handleAskUserShortcut(event) {
  if (
    event.defaultPrevented
    || event.repeat
    || event.isComposing
    || event.ctrlKey
    || event.altKey
    || event.metaKey
    || event.shiftKey
  ) return;
  if (!/^[1-3]$/.test(event.key)) return;

  const target = event.target;
  if (target?.closest?.('input, textarea, select, [contenteditable="true"]')) return;

  const focusedCard = document.activeElement?.closest?.('.ask-user-card') || null;
  const mainCard = document.querySelector('#chat-history .ask-user-card');
  const compareCards = document.querySelectorAll('.compare-pane .ask-user-card');
  const card = focusedCard || mainCard || (compareCards.length === 1 ? compareCards[0] : null);
  if (!card) return;
  if (card.dataset.askUserKind === 'tool_approval') return;
  const option = card.querySelectorAll('.ask-user-option')[Number(event.key) - 1];
  if (!option || option.disabled) return;

  event.preventDefault();
  option.click();
}

document.addEventListener('keydown', _handleAskUserShortcut);

/**
 * Render an ask_user payload as a durable choice card.
 *
 * This lives in the history renderer rather than the streaming loop so the
 * same UI can be used both for a live SSE event and for a persisted tool event
 * after a session reload.
 */
export function renderAskUserCard(payload, options) {
  const aq = payload || {};
  if (aq.resolved) return null;
  const opts = Array.isArray(aq.options) ? aq.options : [];
  const renderOptions = options || {};
  const chatBox = renderOptions.root || document.getElementById('chat-history');
  const onSubmit = typeof renderOptions.onSubmit === 'function'
    ? renderOptions.onSubmit
    : null;
  if (!chatBox || !aq.question || opts.length < 2) return null;

  removeAskUserCards(chatBox);

  const card = document.createElement('div');
  card.className = 'ask-user-card';
  card.setAttribute('role', 'group');
  card.tabIndex = -1;
  const multi = !!aq.multi;
  const isToolApproval = aq.kind === 'tool_approval' && !!aq.approval_id;
  card.dataset.askUserKind = isToolApproval ? 'tool_approval' : 'question';
  const emojiText = (value) => svgifyEmoji(uiModule.esc(String(value)));

  const question = document.createElement('div');
  question.className = 'ask-user-question';
  question.id = `ask-user-q-${Date.now()}-${Math.floor(Math.random() * 1e4)}`;
  question.innerHTML = emojiText(aq.question);
  card.appendChild(question);
  card.setAttribute('aria-labelledby', question.id);

  if (isToolApproval && aq.action) {
    const action = document.createElement('div');
    action.className = 'ask-user-option-desc';
    const effects = Array.isArray(aq.action.effects) ? aq.action.effects.join(', ') : '';
    action.textContent = [
      aq.action.tool || 'tool',
      aq.action.content || '',
      effects ? `Effects: ${effects}` : '',
      aq.action.workspace ? `Workspace: ${aq.action.workspace}` : '',
      aq.action.document_id ? `Document: ${aq.action.document_id}` : '',
      aq.action.document_version != null ? `Document version: ${aq.action.document_version}` : '',
      aq.action.digest ? `Approval fingerprint: ${aq.action.digest}` : '',
    ].filter(Boolean).join('\n');
    action.style.whiteSpace = 'pre-wrap';
    card.appendChild(action);
  }

  const list = document.createElement('div');
  list.className = 'ask-user-options';
  card.appendChild(list);

  const send = (text) => {
    if (!text) return;
    if (onSubmit) {
      const accepted = onSubmit({
        kind: 'answer',
        text,
        label: text,
        payload: aq,
        card,
      });
      if (accepted !== false) _markAskUserAnswered(card, text);
      return;
    }
    _markAskUserAnswered(card, text);
    const input = uiModule.el('message');
    if (input) input.value = text;
    const sendButton = document.querySelector('.send-btn');
    if (sendButton) sendButton.click();
  };

  opts.forEach((opt) => {
    const label = (opt && opt.label) ? String(opt.label) : String(opt || '');
    if (!label) return;
    const description = (opt && opt.description) ? String(opt.description) : '';
    const row = document.createElement(multi ? 'label' : 'button');
    row.className = 'ask-user-option';
    if (multi) {
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.value = label;
      row.appendChild(checkbox);
    }
    const labelText = document.createElement('span');
    labelText.className = 'ask-user-option-label';
    labelText.innerHTML = emojiText(label);
    row.appendChild(labelText);
    if (description) {
      const descriptionText = document.createElement('span');
      descriptionText.className = 'ask-user-option-desc';
      descriptionText.innerHTML = emojiText(description);
      row.appendChild(descriptionText);
    }
    if (!multi) {
      row.type = 'button';
      row.addEventListener('click', () => {
        if (isToolApproval) {
          const detail = {
            approval_id: aq.approval_id,
            decision: String((opt && opt.value) || '').toLowerCase(),
            label,
            document_id: aq.action && aq.action.document_id
              ? String(aq.action.document_id)
              : '',
          };
          if (onSubmit) {
            const accepted = onSubmit({
              kind: 'tool_approval',
              ...detail,
              payload: aq,
              card,
            });
            if (accepted !== false) _markAskUserAnswered(card, label);
          } else {
            _markAskUserAnswered(card, label);
            document.dispatchEvent(new CustomEvent('odysseus:tool-approval', { detail }));
          }
        } else {
          send(label);
        }
      });
    }
    list.appendChild(row);
  });

  const other = document.createElement('div');
  other.className = 'ask-user-other';
  const otherInput = document.createElement('input');
  otherInput.type = 'text';
  otherInput.className = 'styled-prompt-input ask-user-other-input';
  otherInput.placeholder = multi ? 'Other (added to selection)…' : 'Other… (type your own answer)';
  otherInput.setAttribute('aria-label', multi ? 'Add a custom option' : 'Type a custom answer');
  const otherSend = document.createElement('button');
  otherSend.type = 'button';
  otherSend.className = 'confirm-btn confirm-btn-primary ask-user-other-send';
  otherSend.setAttribute('aria-label', 'Send answer');
  otherSend.title = multi ? 'Send selection' : 'Send answer';
  otherSend.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
  const submit = () => {
    const freeText = otherInput.value.trim();
    if (multi) {
      const picked = Array.from(card.querySelectorAll('.ask-user-option input:checked')).map((input) => input.value);
      if (freeText) picked.push(freeText);
      if (picked.length) send(picked.join(', '));
    } else if (freeText) {
      send(freeText);
    }
  };
  otherSend.addEventListener('click', submit);
  otherInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      submit();
    }
  });
  other.appendChild(otherInput);
  other.appendChild(otherSend);
  if (!isToolApproval) card.appendChild(other);

  const previous = chatBox.lastElementChild;
  const previousIsThread = previous?.classList?.contains('agent-thread');
  const previousIsAssistant = previous?.classList?.contains('msg-ai');
  if (previousIsThread || previousIsAssistant) {
    card.classList.add('ask-user-card-attached');
    if (previousIsThread) {
      const hadBottom = previous.classList.contains('has-bottom');
      previous.classList.add('has-bottom', 'has-ask-user-bottom');
      if (!hadBottom) previous.dataset.askUserAttachedBottom = 'true';
    }
  }

  chatBox.appendChild(card);
  if (renderOptions.scroll !== false) {
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
  if (renderOptions.focus !== false) {
    try { card.focus(); } catch (_) {}
  }
  return card;
}

/**
 * Add a message to the chat history.
 */
export function addMessage(role, content, modelName, metadata) {
  try {
    hideWelcomeScreen();
    const box = document.getElementById('chat-history');
    if (!box) { console.error('Chat history element not found'); return; }

    // Loading a later user message means any earlier ask_user card was answered.
    // During history replay this is what keeps the ask_user block visible after
    // refresh: assistant turn renders the question, next user turn stamps the
    // answer onto that card instead of deleting it.
    if (role === 'user') _answerAskUserCards(box, Array.isArray(content) ? markdownModule.renderContent(content) : content);

    var esc = uiModule.esc;
    const textRaw = Array.isArray(content) ? markdownModule.renderContent(content) : content;

    // --- Agent multi-bubble reconstruction from saved metadata ---
    if (
      role === 'assistant'
      && metadata
      && (
        (Array.isArray(metadata.tool_events) && metadata.tool_events.length > 0)
        || (Array.isArray(metadata.round_texts) && metadata.round_texts.length > 1)
      )
    ) {
      let roundTexts = metadata.round_texts || [];
      const toolEvents = metadata.tool_events || [];
      const replacesTurn = metadata.replacement_scope === 'turn'
        && ['structured', 'streamed'].includes(metadata.render_owner);
      if (replacesTurn) {
        // Persisted content owns the answer. Round metadata still owns the
        // reasoning/tool chronology, but its drafts must not become answers.
        roundTexts = (Array.isArray(roundTexts) ? roundTexts : []).map(text =>
          markdownModule.extractThinkingBlocks(markdownModule.normalizeThinkingMarkup(String(text || '')))
            .thinkingBlocks.map(thinking => `<think>${thinking}</think>`).join('\n')
        );
        const finalIndex = Math.max(roundTexts.length, ...toolEvents.map(event => Number(event.round ?? 1) || 0));
        roundTexts.length = finalIndex + 1;
        roundTexts[finalIndex] = typeof textRaw === 'string' ? textRaw : '';
      } else if (metadata?._fromHistory && typeof textRaw === 'string' && textRaw.trim()) {
        roundTexts = Array.isArray(roundTexts) ? [...roundTexts] : [];
        let lastTextIdx = -1;
        for (let i = roundTexts.length - 1; i >= 0; i--) {
          if (String(roundTexts[i] || '').trim()) {
            lastTextIdx = i;
            break;
          }
        }
        if (lastTextIdx >= 0) {
          roundTexts[lastTextIdx] = textRaw;
        } else {
          roundTexts.push(textRaw);
        }
      } else if (
        roundTexts.length === 1
        && typeof textRaw === 'string'
        && textRaw.trim()
        && textRaw !== roundTexts[0]
      ) {
        roundTexts = [textRaw];
      }
      const roundModels = metadata.round_models || [];
      const roundEndpointIds = metadata.round_endpoint_ids || [];
      const roundEndpointLabels = metadata.round_endpoint_labels || [];
      let pendingAskUser = null;
      let lastWrap = null;
      let firstMsgAi = null;
      let lastMsgAi = null;

      const toolsByRound = {};
      let lastPrivateBrowserUrl = '';
      for (const ev of toolEvents) {
        const r = ev.round ?? 1;
        if (!toolsByRound[r]) toolsByRound[r] = [];
        toolsByRound[r].push(ev);
      }

      const toolRounds = Object.keys(toolsByRound).map(Number);
      const maxRound = Math.max(toolRounds.length ? Math.max(...toolRounds) : 0, roundTexts.length);

      const firstRound = (toolsByRound[0] || []).length ? 0 : 1;
      for (let roundNum = firstRound; roundNum <= maxRound; roundNum++) {
        const r = roundNum - 1;
	        let txt = r >= 0
	          ? resolveDocumentPlaceholderLinks((roundTexts[r] || '').trim(), metadata)
	          : '';
	        if (
	          role === 'assistant'
	          && r === 0
	          && metadata?.thinking
	          && !metadata?.character_name
	          && !roundTexts.some(t => /<\s*(?:think|thinking|thought)\b/i.test(String(t || '')))
	        ) {
	          const thinkTime = metadata.thinking_time || null;
	          txt = '<think' + (thinkTime ? ` time="${thinkTime}"` : '') + '>'
	            + String(metadata.thinking || '')
	            + '</think>'
	            + (txt ? '\n\n' + txt : '');
	        }

        if (txt) {
          const wrap = document.createElement('div');
          wrap.className = 'msg msg-ai' + (r > 0 ? ' msg-continuation' : '');
          const roleEl = document.createElement('div');
          roleEl.className = 'role';
          const pair = replyModelPair(modelName, metadata);
          const contModel = roundModels[r] || pair.actualModel || pair.requestedModel;
          const contEndpointId = r < roundEndpointIds.length
            ? roundEndpointIds[r]
            : pair.actualEndpointId;
          const contEndpointLabel = r < roundEndpointLabels.length
            ? roundEndpointLabels[r]
            : pair.actualEndpointLabel;
          roleEl.textContent = modelRouteLabel(
            pair.requestedModel,
            contModel,
            pair.requestedEndpointLabel,
            contEndpointLabel,
            pair.requestedEndpointId,
            contEndpointId,
          );
          if (
            pair.requestedModel
            && contModel
            && (
              !sameModelName(pair.requestedModel, contModel)
              || (pair.requestedEndpointId && contEndpointId && pair.requestedEndpointId !== contEndpointId)
            )
          ) {
            roleEl.title = pair.requestedModel + ' -> ' + contModel
              + ' (' + pair.requestedEndpointLabel + ' -> ' + contEndpointLabel + ')';
          }
          applyModelColor(roleEl, contModel);
          if (r === 0) roleEl.appendChild(roleTimestamp(metadata?.timestamp));
          wrap.appendChild(roleEl);
          const body = document.createElement('div');
          body.className = 'body';
          // Check if this is the last text round — sources go on top of final response
          var agentSourcesPrefix = '';
          var isLastTextRound = true;
          for (let rr = r + 1; rr < maxRound; rr++) {
            if ((roundTexts[rr] || '').trim()) { isLastTextRound = false; break; }
          }
          var agentFindingsSuffix = '';
          if (isLastTextRound && metadata?.web_sources?.length) {
            agentSourcesPrefix = buildSourcesBox(metadata.web_sources, 'web');
          } else if (isLastTextRound && metadata?.research_sources?.length) {
            agentSourcesPrefix = buildSourcesBox(metadata.research_sources, 'research');
          }
          if (isLastTextRound && metadata?.research_findings?.length) {
            agentFindingsSuffix = buildFindingsBox(metadata.research_findings);
          }
          // RAG document sources — restored on the final text round.
          if (isLastTextRound && metadata?.rag_sources?.length) {
            agentFindingsSuffix += buildRagSourcesBox(metadata.rag_sources);
          }
          body.innerHTML = agentSourcesPrefix + markdownModule.processWithThinking(markdownModule.squashOutsideCode(txt)) + agentFindingsSuffix;
          wrap.appendChild(body);
          wrap.dataset.raw = txt;
          if (metadata?._db_id) wrap.dataset.dbId = metadata._db_id;
          box.appendChild(wrap);
          lastWrap = wrap;
          if (!firstMsgAi) firstMsgAi = wrap;
          lastMsgAi = wrap;
        }

        const roundTools = toolsByRound[roundNum] || [];
        if (roundTools.length > 0) {
          // Reuse previous thread if no text separated us (merge consecutive tool rounds)
          let threadWrap = null;
          if (!txt && lastWrap && lastWrap.classList.contains('agent-thread')) {
            threadWrap = lastWrap;
          } else {
            threadWrap = document.createElement('div');
            threadWrap.className = 'agent-thread';
            // Extend line up if there's a chat bubble above
            if (txt) threadWrap.classList.add('has-top');
            box.appendChild(threadWrap);
          }
          for (const ev of roundTools) {
            if (ev.ask_user && !ev.ask_user.resolved) pendingAskUser = ev.ask_user;
            const ok = (ev.exit_code === 0 || ev.exit_code == null);
            let outHtml = '';
            if (!_suppressRawToolOutput(ev.tool, ok) && ev.output && ev.output.trim()) {
              outHtml = `<details class="agent-tool-output"><summary>Output</summary><pre>${esc(ev.output)}</pre></details>`;
            }
            const screenshotSrc = safeToolScreenshotSrc(ev.screenshot);
            if (_isPrivateBrowserTool(ev.tool)) {
              const currentBrowserUrl = _privateBrowserUrlFromCommand(ev.command) || lastPrivateBrowserUrl;
              if (currentBrowserUrl) lastPrivateBrowserUrl = currentBrowserUrl;
              outHtml += _browserPreviewHtml(ev.command, screenshotSrc, currentBrowserUrl);
            } else if (screenshotSrc) {
              outHtml += `<details class="agent-tool-output"><summary>Screenshot</summary><img src="${uiModule.esc(screenshotSrc)}" style="max-width:100%;border-radius:6px;margin-top:6px;border:1px solid var(--border)" /></details>`;
            }
            // File-write/edit diff (persisted in the tool event) \u2014 re-render it
            // so it survives reload, matching the live stream.
            let evDiffHtml = '';
            if (ev.diff && ev.diff.text) {
              const d = ev.diff;
              const stat = [
                d.new_file ? '<span class="diff-stat-new">new</span>' : '',
                d.added ? `<span class="diff-stat-add">+${d.added}</span>` : '',
                d.removed ? `<span class="diff-stat-del">\u2212${d.removed}</span>` : '',
              ].filter(Boolean).join(' ');
              const rows = d.text.split('\n').map(line => {
                let cls = 'diff-ctx', text = line;
                if (line.startsWith('+++') || line.startsWith('---')) cls = 'diff-meta';
                else if (line.startsWith('@@')) cls = 'diff-hunk';
                // Drop the leading diff marker (+/-/space) — colour encodes add/del.
                else if (line.startsWith('+')) { cls = 'diff-add'; text = line.slice(1); }
                else if (line.startsWith('-')) { cls = 'diff-del'; text = line.slice(1); }
                else if (line.startsWith(' ')) { text = line.slice(1); }
                return `<span class="${cls}">${esc(text) || '&nbsp;'}</span>`;
              }).join('');  // spans are display:block \u2014 a literal \n would double-space
              evDiffHtml = `<details class="agent-tool-output agent-tool-diff"><summary><span class="diff-file">${esc(d.file || 'diff')}</span> <span class="diff-summary-stats">${stat}</span></summary><pre class="diff-pre">${rows}</pre></details>`;
            }
            const node = document.createElement('div');
            const browserTool = _isPrivateBrowserTool(ev.tool);
            node.className = 'agent-thread-node' + (ok ? '' : ' error') + (browserTool ? ' open browser-preview-node' : '');
            // Hide raw JSON for high-level app tools and diffs; show the same
            // compact command summary that live streaming uses.
            const evToolInfo = _toolDisplayInfo(ev.tool, ev.command, ev);
            const evHeaderAction = evToolInfo.headerActionHtml || '';
            const evToolIcon = renderToolIcon(ev.tool, ev.command, ev);
            const evCmdHtml = browserTool ? '' : (evToolInfo.commandHtml || ((ev.command && !(ev.diff && ev.diff.text)) ? `<pre class="agent-thread-cmd">${esc(ev.command)}</pre>` : ''));
            node.innerHTML = `<div class="agent-thread-dot"></div><div class="agent-thread-header"><span class="agent-thread-icon">${ok ? '\u2713' : '\u2717'}</span>${evToolIcon}<span class="agent-thread-tool">${esc(evToolInfo.label || ev.tool)}</span>${evHeaderAction}<span class="agent-thread-status">${ok ? 'done' : 'failed'}</span><span class="agent-thread-chevron" aria-hidden="true"></span></div><div class="agent-thread-content">${evCmdHtml}${outHtml}${evDiffHtml}</div>`;
            // Click handling is delegated globally \u2014 see chat.js init.
            threadWrap.appendChild(node);
          }
          // Check if next round has text — extend line down to connect
          const nextTxt = (roundTexts[r + 1] || '').trim();
          if (nextTxt) threadWrap.classList.add('has-bottom');
          lastWrap = threadWrap;

          for (const ev of roundTools) {
            const image = generatedImageResult(ev);
            if (image) {
              box.appendChild(buildImageBubble(image.image_url, image.image_prompt, image.image_model, image.image_size, image.image_quality, image.image_id));
            }
          }
        }
      }

      const firstWrap = lastMsgAi || lastWrap;
      if (firstWrap && firstWrap.classList.contains('msg-ai')) {
        if (metadata?.memories_used?.length) firstWrap._memoriesUsed = metadata.memories_used;
        firstWrap.appendChild(createMsgFooter(firstWrap));
        if (metadata) displayMetrics(firstWrap, metadata);
      }

      if (window.hljs) {
        box.querySelectorAll('pre code:not(.hljs)').forEach(b => window.hljs.highlightElement(b));
      }
      if (markdownModule.renderMermaid) markdownModule.renderMermaid(box);
      if (pendingAskUser) {
        // Session history is rendered oldest-to-newest.  A later user message
        // removes this card; if there is none, the pending choice survives a
        // refresh.  Avoid stealing focus while the history is loading.
        renderAskUserCard(pendingAskUser, { focus: false, scroll: false });
      }
      return lastWrap;
    }

    // --- Wake-task / supervisor system check-in ---
    // The self-wake mechanism injects "Did you finish?" as a user message
    // (or persisted history shows a "[Task] Self-check: <id>" envelope)
    // so the agent loop re-enters and re-checks status. Render as a
    // normal user-style bubble — same chrome as a real user message,
    // just with role "Supervisor" and a short summary body — instead of
    // a slim system chip. Matches chat style and integrates cleanly
    // into the conversation flow.
    let _isWakeCheck = !!(metadata?.wake_check_in || metadata?.hidden_from_user_view);
    if (!_isWakeCheck && typeof textRaw === 'string') {
      // Also catch historical messages persisted as "[Task] Self-check: <sid>"
      // (older wake tasks that didn't set wake_check_in metadata).
      if (/^\s*\[Task\]\s+Self-check:/i.test(textRaw)) {
        _isWakeCheck = true;
      }
    }
    if (_isWakeCheck) {
      // Supervisor self-check messages are an internal control signal —
      // skip rendering entirely so they don't show up in the conversation.
      return null;
    }

    // Discuss sessions carry the full research report in a seeded system
    // message. Render it through the same thinking block used by model
    // reasoning, rather than introducing a second collapsible UI.
    if (role === 'system' && metadata?.research_spinoff_from) {
      const wrap = document.createElement('div');
      wrap.className = 'msg msg-ai';
      const roleEl = document.createElement('div');
      roleEl.className = 'role';
      roleEl.textContent = 'Research Context';
      const body = document.createElement('div');
      body.className = 'content';
      body.innerHTML = markdownModule.createCollapsible(String(textRaw || ''), 'Research Context', true);
      const hint = document.createElement('div');
      hint.className = 'research-context-continue-hint';
      hint.innerHTML = 'Continue conversation below <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"></polyline></svg>';
      body.appendChild(hint);
      wrap.appendChild(roleEl);
      wrap.appendChild(body);
      box.appendChild(wrap);
      return wrap;
    }

    // --- Standard single-bubble message ---
    const wrap = document.createElement('div');
    wrap.className = 'msg ' + (role === 'user' ? 'msg-user' : 'msg-ai');

    const r = document.createElement('div');
    r.className = 'role';
    const isSlash = metadata?.source === 'slash';
    const isCompacted = metadata?.compacted;
    const replyModels = replyModelPair(modelName, metadata);
    const resolvedModel = replyModels.actualModel || replyModels.requestedModel;
    var _roleText = role === 'user' ? 'You' : (isSlash || isCompacted) ? 'Odysseus' : modelRouteLabel(
      replyModels.requestedModel,
      resolvedModel,
      replyModels.requestedEndpointLabel,
      replyModels.actualEndpointLabel,
      replyModels.requestedEndpointId,
      replyModels.actualEndpointId,
    );
    if (role === 'assistant' && (metadata?.research || metadata?.research_clarification)) {
      _roleText += ' (Research)';
    }
    if (metadata?.group_model && role !== 'user') {
      _roleText = metadata.group_model;
    } else if (metadata?.character_name && getShowPersonaName() && role !== 'user' && !isSlash && !isCompacted) {
      _roleText = metadata.character_name;
    }
    r.textContent = _roleText;
    if (role === 'user') {
      const pill = userModePill(metadata);
      if (pill) r.appendChild(pill);
    }
    if (role !== 'user') {
      const endpointChanged = Boolean(
        replyModels.requestedEndpointId
        && replyModels.actualEndpointId
        && replyModels.requestedEndpointId !== replyModels.actualEndpointId
      );
      if (!isSlash && !isCompacted && replyModels.requestedModel && resolvedModel && (!sameModelName(replyModels.requestedModel, resolvedModel) || endpointChanged)) {
        r.title = replyModels.requestedModel + ' -> ' + resolvedModel
          + ' (' + replyModels.requestedEndpointLabel + ' -> ' + replyModels.actualEndpointLabel + ')';
      }
      if (!isSlash && !isCompacted) applyModelColor(r, resolvedModel);
      r.appendChild(roleTimestamp(metadata?.timestamp));
    }

    const b = document.createElement('div');
    b.className = 'body';

    let text = markdownModule.squashOutsideCode(stripToolBlocks(textRaw || ''));
    if (role === 'assistant') {
      text = resolveDocumentPlaceholderLinks(text, metadata);
    }

    // For user messages, pull out vision-model image descriptions ([Image: name]\n
    // <multi-line desc>) into a collapsible "image description" section. Done for
    // ALL user messages (not just ones with attachment metadata) so it rebuilds
    // from the stored text even after a browser restart drops the cached attachments.
    const attachments = metadata?.attachments;
    const _visionBlocks = [];
    if (role === 'user') {
      text = text.replace(
        /\n*\[Image: ([^\]]+)\]\n([\s\S]*?)(?=\n*\[Image: |\n*\[Image attached: |\n*=== File: |\n*\[PDF content\]:|$)/g,
        (_m, name, desc) => { const d = desc.trim(); if (d) _visionBlocks.push({ name: name, desc: d }); return ''; }
      );
    }
    // With attachments present, also strip the embedded file/PDF/image-marker text.
    if (role === 'user' && attachments?.length) {
      // Strip === File: ... === blocks, [PDF content]: blocks, and [Image attached: ...] lines
      text = text
        .replace(/\n*=== File: .+? ===\n\[Type: .+?\]\n+```[\s\S]*?```/g, '')
        .replace(/\n*=== File: .+? ===\n\[Type: .+?\]\n+[\s\S]*?(?=\n*=== File:|$)/g, '')
        .replace(/\n*\[PDF content[^\]]*\]:[\s\S]*?(?=\n*\[PDF content[^\]]*\]:|\n*=== File:|$)/g, '')
        .replace(/\n*\[Image attached: [^\]]+\]/g, '')
        .replace(/\n*\[Attached (?:document|non-text) file\]/g, '')
        .trim();
    }

	    wrap.dataset.raw = text;
	    if (metadata?._db_id) wrap.dataset.dbId = metadata._db_id;
    // Prepend sources box if saved in metadata
    var sourcesPrefix = '';
    var findingsSuffix = '';
    if (role === 'assistant' && metadata?.research_sources?.length) {
      sourcesPrefix = buildSourcesBox(metadata.research_sources, 'research');
    } else if (role === 'assistant' && metadata?.web_sources?.length) {
      sourcesPrefix = buildSourcesBox(metadata.web_sources, 'web');
    }
    if (role === 'assistant' && metadata?.research_findings?.length) {
      findingsSuffix = buildFindingsBox(metadata.research_findings);
    }
    // RAG document sources — restored from metadata so they survive refresh.
    if (role === 'assistant' && metadata?.rag_sources?.length) {
      findingsSuffix += buildRagSourcesBox(metadata.rag_sources);
    }
    // If thinking is stored in metadata (not in text), reconstruct the full display.
    // Persona/character chats suppress the thinking UI so the persona answer stays
    // in-character; the metadata remains saved for debugging/export.
    if (role === 'assistant' && metadata?.thinking && !metadata?.character_name) {
      const thinkTime = metadata.thinking_time || null;
      const thinkHtml = markdownModule.processWithThinking(
        '<think' + (thinkTime ? ` time="${thinkTime}"` : '') + '>' + metadata.thinking + '</think>\n\n' + text
      );
      b.innerHTML = sourcesPrefix + thinkHtml + findingsSuffix;
		    } else {
		      b.innerHTML = sourcesPrefix + _renderAssistantBody(text, role === 'assistant' ? metadata : null) + findingsSuffix;
		    }
	    b.dataset.raw = text;

    // The vision/OCR caption is stripped from the displayed text above (so the
    // bubble doesn't show the raw model output) but no longer rendered as an
    // inline collapsible — the user can still view/edit it via the "Caption"
    // button on the photo thumbnail. _visionBlocks is intentionally left unused
    // so the parsing-and-strip side-effect on `text` still happens.
    void _visionBlocks;

    // Add "Open Visual Report" button for persisted research messages
    if (role === 'assistant' && metadata?.research) {
      var _sid = window.sessionModule?.getCurrentSessionId?.();
      if (_sid) _appendReportButton(b, _sid);
    }

    // Style [Doc edit: ...] prefix in user messages
    if (role === 'user') {
      // Match both the optimistic live format (L1) and the persisted-history
      // format (line 1). Keep one interactive element in either path so the
      // bubble does not visibly gain styling only after a refresh.
      b.innerHTML = b.innerHTML.replace(
        /\[Doc edit: ((?:L|lines?)\s*[\d–\-]+)\]\s*/i,
        '<button type="button" class="doc-edit-tag" data-doc-edit-ref="$1" title="Select this text again">Doc edit: $1</button> '
      );
      // Match raw format: "In the document, edit this specific text (line X):\n```\n...\n```\n\nInstruction: ..."
      // After markdown processing this becomes a <p> + <pre><code> block + <p>Instruction: text</p>
      const rawDocMatch = b.innerHTML.match(/In the document, edit this specific text \((lines? [\d–\-]+)\)/);
      if (rawDocMatch) {
        const lineRef = rawDocMatch[1];
        // Extract instruction text (after "Instruction: ")
        const instrMatch = b.textContent.match(/Instruction:\s*([\s\S]*)$/);
        const instrText = instrMatch ? instrMatch[1].trim() : '';
        b.innerHTML = '<button type="button" class="doc-edit-tag" data-doc-edit-ref="' + lineRef + '" title="Select this text again">Doc edit: ' + lineRef + '</button> ' + markdownModule.processWithThinking(instrText);
      }

      b.querySelectorAll('[data-doc-edit-ref]').forEach(button => {
        button.addEventListener('click', () => {
          import('./document.js?v=20260916docctx2').then(mod => {
            const restore = mod.restoreSelectionReference
              || mod.default?.restoreSelectionReference;
            return restore?.(button.dataset.docEditRef || '', {
              documentId: metadata?.document_id || '',
              selections: metadata?.document_selections || null,
            });
          }).catch(() => {});
        });
      });

      // Render attachment cards
      if (attachments?.length) {
        b.appendChild(buildAttachCards(attachments));
      }
    }

    wrap.appendChild(r);
    wrap.appendChild(b);

    // Add stopped indicator + continue button for messages that were stopped by user
    if (role === 'assistant' && metadata?.stopped) {
      const stoppedIndicator = document.createElement('div');
      stoppedIndicator.className = 'stopped-indicator';
      const stoppedLabel = document.createElement('span');
      // Differentiate between "stopped mid-stream" (had content, can continue)
      // and "cancelled before any content" — the latter has no Continue affordance.
      stoppedLabel.textContent = metadata.cancelled
        ? '[Cancelled by user]'
        : '[Message interrupted]';
      stoppedIndicator.appendChild(stoppedLabel);
      // Continue button only makes sense when there's partial content to
      // resume from \u2014 skip it for fully-cancelled (empty) turns.
      if (!metadata.cancelled) {
        const continueBtn = document.createElement('button');
        continueBtn.className = 'continue-btn resume-btn';
        continueBtn.title = 'Resume response';
        continueBtn.innerHTML = '<span class="resume-btn-label">Resume</span><svg class="resume-btn-icon" width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="m8 5 10 7-10 7z"></path></svg>';
        continueBtn.addEventListener('click', () => {
          stoppedIndicator.remove();
          if (window.chatModule) {
            window.chatModule.setHideUserBubble();
            window.chatModule.setPendingContinue(wrap);
            const msgInput = document.getElementById('message');
            if (msgInput) {
              msgInput.value = 'Continue from where you left off.';
              const sb = document.querySelector('.send-btn');
              if (sb) sb.click();
            }
          }
        });
        stoppedIndicator.appendChild(continueBtn);
      }
      b.appendChild(stoppedIndicator);
    }

    if (metadata?.edited) {
      const editedIndicator = document.createElement('div');
      editedIndicator.className = 'edited-indicator';
      editedIndicator.textContent = '[Message edited]';
      b.appendChild(editedIndicator);
    }

    // Restore variant navigation from saved metadata
    if (role === 'assistant' && metadata?.variants && metadata.variants.length > 1) {
      wrap.dataset.variants = JSON.stringify(metadata.variants);
      const idx = metadata.variantIndex ?? metadata.variants.length - 1;
      wrap.dataset.variantIndex = String(idx);

      // Re-render from `raw` markdown rather than trusting cached `v.html`.
      // Variants ride through localStorage / chat export-import; cached HTML
      // would let an attacker-controlled session JSON inject markup.
      const _renderVariant = (v) => (v && v.raw)
        ? markdownModule.processWithThinking(markdownModule.squashOutsideCode(v.raw))
        : (v && v.html) || '';

      // Show the selected variant's content
      const v = metadata.variants[idx];
      if (v) {
        b.innerHTML = _renderVariant(v);
        wrap.dataset.raw = v.raw;
      }

      // Render nav
      const nav = document.createElement('span');
      nav.className = 'variant-nav';
      nav.addEventListener('click', (e) => e.stopPropagation());

      const divider = document.createElement('span');
      divider.className = 'variant-divider';
      divider.textContent = '|';
      nav.appendChild(divider);

      const tagLabel = document.createElement('span');
      const _icons = { regen: '\u21BB', shorter: '\u2702', simpler: '?', original: '\u25CB' };
      const _tl0 = metadata.variants[idx]?.label;
      tagLabel.className = 'variant-tag' + (_tl0 === 'shorter' ? ' variant-tag-scissors' : '');
      tagLabel.textContent = _icons[_tl0] || '';
      nav.appendChild(tagLabel);

      const prevBtn = document.createElement('button');
      prevBtn.className = 'variant-btn';
      prevBtn.textContent = '<';
      prevBtn.disabled = idx === 0;
      nav.appendChild(prevBtn);

      const numLeft = document.createElement('button');
      numLeft.className = 'variant-num';
      numLeft.textContent = String(idx + 1);
      numLeft.disabled = idx === 0;
      nav.appendChild(numLeft);

      const slash = document.createElement('span');
      slash.className = 'variant-slash';
      slash.textContent = '/';
      nav.appendChild(slash);

      const numRight = document.createElement('button');
      numRight.className = 'variant-num';
      numRight.textContent = String(metadata.variants.length);
      numRight.disabled = idx === metadata.variants.length - 1;
      nav.appendChild(numRight);

      const nextBtn = document.createElement('button');
      nextBtn.className = 'variant-btn';
      nextBtn.textContent = '>';
      nextBtn.disabled = idx === metadata.variants.length - 1;
      nav.appendChild(nextBtn);

      const switchFn = (newIdx) => {
        const vars = metadata.variants;
        if (newIdx < 0 || newIdx >= vars.length) return;
        const sv = vars[newIdx];
        b.innerHTML = _renderVariant(sv);
        wrap.dataset.raw = sv.raw;
        wrap.dataset.variantIndex = String(newIdx);
        if (window.hljs) wrap.querySelectorAll('pre code').forEach(bl => window.hljs.highlightElement(bl));
        tagLabel.textContent = _icons[sv.label] || '';
        tagLabel.className = 'variant-tag' + (sv.label === 'shorter' ? ' variant-tag-scissors' : '');
        numLeft.textContent = String(newIdx + 1);
        numLeft.disabled = newIdx === 0;
        numRight.disabled = newIdx === vars.length - 1;
        prevBtn.disabled = newIdx === 0;
        nextBtn.disabled = newIdx === vars.length - 1;
      };
      prevBtn.addEventListener('click', (e) => { e.stopPropagation(); switchFn(parseInt(wrap.dataset.variantIndex) - 1); });
      numLeft.addEventListener('click', (e) => { e.stopPropagation(); switchFn(parseInt(wrap.dataset.variantIndex) - 1); });
      numRight.addEventListener('click', (e) => { e.stopPropagation(); switchFn(parseInt(wrap.dataset.variantIndex) + 1); });
      nextBtn.addEventListener('click', (e) => { e.stopPropagation(); switchFn(parseInt(wrap.dataset.variantIndex) + 1); });

      r.appendChild(nav);
    }

    if (role === 'assistant') {
      // The "N pinned" / "N recalled" pill in the footer reads from
      // wrap._memoriesUsed — propagate it from saved metadata so the pill
      // survives a page refresh (live-stream path sets it via SSE, but
      // history reloads need this assignment).
      if (metadata?.memories_used?.length) wrap._memoriesUsed = metadata.memories_used;
      wrap.appendChild(createMsgFooter(wrap));
      if (metadata) displayMetrics(wrap, metadata);
    } else {
      // Add timestamp to user header (like AI messages)
      r.appendChild(roleTimestamp(metadata?.timestamp));

      wrap.appendChild(createUserMsgFooter(wrap));
    }

    box.appendChild(wrap);

    // TTS is now part of the msg-actions system
    if (role === 'assistant' && markdownModule.renderMermaid) {
      markdownModule.renderMermaid(wrap);
    }
    return wrap;
  } catch (error) {
    console.error('Error in addMessage:', error);
    if (uiModule) uiModule.showError('Failed to add message: ' + error.message);
  }
}

const chatRenderer = {
  shortModel,
  sameModelName,
  modelRouteLabel,
  replyModelPair,
  modelColor,
  applyModelColor,
  getModelCost,
  isCostTrackedEndpoint,
  isSubscriptionEndpoint,
  getImageCost,
  getSessionCost,
  resetSessionCost,
  updateSessionCostUI,
  recordSessionMetricsCost,
  roleTimestamp,
  stripToolBlocks,
  copyMessageText,
  safeToolScreenshotSrc,
  safeDisplayImageSrc,
  removeAskUserCards,
  renderAskUserCard,
  buildSourcesBox,
  buildFindingsBox,
  buildRagSourcesBox,
  appendReportButton,
  buildImageBubble,
  hideWelcomeScreen,
  showWelcomeScreen,
  createMsgFooter,
  displayMetrics,
  addMessage,
  buildAttachCards,
  updateMessageAttachments,
};

export default chatRenderer;
