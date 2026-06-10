// ============================================
// Settings index — command-palette provider
// ============================================
// Makes individual settings searchable. Registered into command-registry as a
// provider; perform() deep-links: open settings → switch tab → scroll the
// setting into view → pulse highlight.
//
// Panel classification (what we scrape vs hand-declare vs skip):
//  - SCRAPED (statically-authored markup, present from page load): ai, search,
//    appearance, email, reminders, account, services, integrations, tools,
//    users, system — via the STRICT label/header allowlist below. JS-filled
//    list containers in these panels simply yield nothing.
//  - HAND-DECLARED: the shortcuts panel (#shortcuts-list is EMPTY until
//    settings' initShortcuts() runs) — entries come from SHORTCUT_LABELS.
//    We never force settings initAll() just to scrape (network side effects).
//  - Value-leakage guard: only label/header text is read (h2 DIRECT text
//    nodes, label textContent). NEVER input.value, <option> text, or
//    JS-populated lists (user emails, endpoint URLs).
//
// Admin gating (fail-closed): tools/users/system items are excluded unless
// `window._isAdmin === true` (undefined while the auth fetch is in flight
// counts as NOT admin), and perform() re-checks before open(tab). Server
// endpoints additionally 403 non-admins (verified: routes/admin_wipe_routes.py
// require_admin; routes/auth_routes.py user-management is_admin checks) —
// client gating here is defense-in-depth, not the only line.

// Dependencies are injected via initSettingsIndex (NOT imported): settings.js
// pulls in ui.js, which does top-level DOM work — injecting keeps this module
// importable under bare node so its gating/leakage rules stay unit-testable.
import { registerProvider } from './command-registry.js';

let _settings = null;       // settingsModule ({open})
let _shortcutLabels = {};   // settings.js SHORTCUT_LABELS

// Human tab names, mirroring the settings nav button captions.
const TAB_NAMES = {
  services: 'Add Models', ai: 'AI Defaults', search: 'Search',
  integrations: 'Integrations', email: 'Email', reminders: 'Reminders',
  appearance: 'Appearance', shortcuts: 'Shortcuts', account: 'Account',
  tools: 'Agent Tools', users: 'Users', system: 'System',
};

// Tabs whose nav buttons carry .admin-only — never indexed for non-admins.
const ADMIN_TABS = new Set(['tools', 'users', 'system']);

// STRICT allowlist of "setting name" selectors. Anything else (hints, subs,
// placeholders, options, values) is deliberately NOT scraped.
const LABEL_SELECTORS = '.settings-label, .vis-label, .admin-toggle-label';

const DEEPLINK_TIMEOUT_MS = 2000;
const DEEPLINK_POLL_MS = 50;

function _slug(s) {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'x';
}

// Card header text = the h2's DIRECT text nodes only — skips the inline svg,
// "(Experimental)" spans and embedded toggle labels.
function _h2Text(h2) {
  let t = '';
  for (const n of h2.childNodes) if (n.nodeType === 3) t += n.textContent;
  return t.trim();
}

// Skip cards kept in the DOM but permanently hidden (e.g. the retired Image
// Generation / TTS cards: `hidden style="display:none"`).
function _cardShown(card) {
  return !card.hidden && card.style.display !== 'none';
}

function _adminBlocked(tab) {
  return ADMIN_TABS.has(tab) && window._isAdmin !== true; // fail-closed
}

// ── Deep-link: bounded poll for the target to exist + have layout, then
// scroll + pulse; card-level fallback after timeout. `resolve`/`fallback`
// are elements or () => element.
function _deepLink(resolve, fallback) {
  const t0 = performance.now();
  const get = (r) => (typeof r === 'function' ? r() : r);
  const tick = () => {
    const el = get(resolve);
    if (el && el.getClientRects().length) { _pulse(el); return; }
    if (performance.now() - t0 > DEEPLINK_TIMEOUT_MS) {
      const fb = get(fallback);
      if (fb && fb.getClientRects().length) _pulse(fb);
      return;
    }
    setTimeout(tick, DEEPLINK_POLL_MS);
  };
  // First check next frame, after open(tab)'s synchronous class flips land.
  requestAnimationFrame(tick);
}

function _pulse(el) {
  el.scrollIntoView({ block: 'center' });
  // Re-trigger safely: cancel any prior cleanup, remove the class, force a
  // reflow so the animation restarts, re-add; remove on animationend with a
  // fallback timeout so the class can never stick.
  if (typeof el._cmdPulseCleanup === 'function') el._cmdPulseCleanup();
  el.classList.remove('cmd-deeplink-pulse');
  void el.offsetWidth; // force reflow
  el.classList.add('cmd-deeplink-pulse');
  let timer = null;
  const done = () => {
    el.classList.remove('cmd-deeplink-pulse');
    el.removeEventListener('animationend', done);
    if (timer) clearTimeout(timer);
    el._cmdPulseCleanup = null;
  };
  timer = setTimeout(done, 4000);
  el.addEventListener('animationend', done);
  el._cmdPulseCleanup = done;
}

function _mkItem({ tab, id, title, keywords, resolve, fallback }) {
  return {
    id, title, keywords, section: 'Settings', _tab: tab,
    perform() {
      // Re-check at activation time — the provider-side filter alone is not
      // enough (admin status can change between render and Enter).
      if (_adminBlocked(tab)) throw new Error('admin only: ' + tab);
      if (!_settings) throw new Error('settings module not initialized');
      _settings.open(tab);
      if (resolve) _deepLink(resolve, fallback);
      return true;
    },
  };
}

// ── Scrape the statically-authored panels. Cheap (a few dozen allowlist
// queries over static markup), so it runs per getSettingsItems() call —
// no cache to invalidate, and tests can swap the document stub freely.
function _scrape() {
  const items = [];
  const seen = new Set();
  const push = (item) => { if (!seen.has(item.id)) { seen.add(item.id); items.push(item); } };

  document.querySelectorAll('[data-settings-panel]').forEach(panel => {
    const tab = panel.dataset.settingsPanel;
    const tabName = TAB_NAMES[tab] || tab;

    // Tab-jump item ("Settings: Appearance").
    push(_mkItem({
      tab, id: 'setting:' + tab, title: 'Settings: ' + tabName,
      keywords: ['settings', 'tab', 'open'],
    }));

    panel.querySelectorAll('.admin-card').forEach(card => {
      if (!_cardShown(card)) return;
      const h2 = card.querySelector('h2');
      const cardName = h2 ? _h2Text(h2) : '';
      if (cardName) {
        // Header-derived item — pulse target is the card.
        push(_mkItem({
          tab, id: `setting:${tab}:${_slug(cardName)}`,
          title: `${tabName} › ${cardName}`,
          keywords: [tabName.toLowerCase(), 'settings'],
          resolve: card,
        }));
      }
      card.querySelectorAll(LABEL_SELECTORS).forEach(label => {
        const text = (label.textContent || '').trim();
        if (!text) return;
        // Label-derived item — pulse target is the row; card is the fallback.
        const row = label.closest('.settings-row, .vis-row') || label.parentElement || card;
        const title = cardName
          ? `${tabName} › ${cardName} › ${text}`   // <Tab> › <Card> › <Label> disambiguates repeated "Endpoint"/"Model"
          : `${tabName} › ${text}`;
        const cardSeg = cardName ? _slug(cardName) + ':' : '';
        push(_mkItem({
          tab, id: `setting:${tab}:${cardSeg}${_slug(text)}`,
          title, keywords: [tabName.toLowerCase(), 'settings'],
          resolve: row, fallback: card,
        }));
      });
    });
  });
  return items;
}

// ── Hand-declared entries for the DYNAMIC shortcuts panel (rows exist only
// after settings opens once). Deep-link polls for the row, falling back to
// the shortcuts card while initShortcuts is still rendering.
function _shortcutItems() {
  return Object.entries(_shortcutLabels).map(([action, label]) => _mkItem({
    tab: 'shortcuts',
    id: 'setting:shortcuts:' + action,
    title: 'Shortcuts › ' + label,
    keywords: ['shortcut', 'keybind', 'hotkey', 'key'],
    resolve: () => document.querySelector(`.shortcut-row[data-action="${action}"]`),
    fallback: () => document.querySelector('[data-settings-panel="shortcuts"] .admin-card'),
  }));
}

/** Provider for command-registry: all currently-permitted settings items. */
export function getSettingsItems() {
  return [..._scrape(), ..._shortcutItems()].filter(it => !_adminBlocked(it._tab));
}

/**
 * Register the provider. Called once at app init.
 * @param {Object} settingsModule - settings.js default export (needs .open)
 * @param {Object} shortcutLabels - settings.js SHORTCUT_LABELS map
 */
export function initSettingsIndex(settingsModule, shortcutLabels) {
  _settings = settingsModule;
  _shortcutLabels = shortcutLabels || {};
  registerProvider(getSettingsItems);
}

export default { initSettingsIndex, getSettingsItems };
