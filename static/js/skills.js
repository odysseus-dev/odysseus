// skills.js — Skills tab in the Memory modal.
//
// Skills are SKILL.md files (frontmatter + body) under data/skills/.
// This UI supports: list, search, view (read SKILL.md), edit (replace
// content), publish/draft toggle, delete, and "run as slash" via the
// /<skill-name> path.

import uiModule from './ui.js';
import * as spinnerModule from './spinner.js';

const API = window.location.origin;
let skills = [];
let builtinSkills = [];   // read-only agent tool capabilities (TOOL_SECTIONS)
let loaded = false;
let _loadPromise = null;

function esc(s) { return uiModule.esc(String(s ?? '')); }

let _pendingFocusSkill = null;
let _cascadeNext = false;   // set true to play the domino-in entrance on the next render

function _playSkillsCascade(container = document.getElementById('skills-list')) {
  if (!container || !container.querySelector('.skill-card')) return false;
  container.classList.remove('doclib-just-opened');
  void container.offsetWidth;
  container.classList.add('doclib-just-opened');
  setTimeout(() => container.classList.remove('doclib-just-opened'), 900);
  return true;
}

// Cache of SKILL.md text by skill name, so expanding is instant (no async
// fetch + content-settle jump). Populated lazily on expand AND eagerly in
// the background for all visible cards right after render.
const _mdCache = new Map();
async function _fetchSkillMarkdown(name) {
  if (_mdCache.has(name)) return _mdCache.get(name);
  const res = await fetch(`${API}/api/skills/${encodeURIComponent(name)}/markdown`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  const md = data.markdown || '';
  _mdCache.set(name, md);
  return md;
}
// Background-load the markdown for every currently-rendered skill card so it
// is ready (in the card's <pre> + _mdLoaded) before the user expands it.
function _preloadVisibleMarkdown() {
  document.querySelectorAll('#skills-list .skill-card[data-skill-name]').forEach(card => {
    const name = card.dataset.skillName;
    if (!name || card._mdLoaded) return;
    const pre = card.querySelector('.skill-md-pre');
    const apply = (md) => { if (pre) pre.textContent = md || '(empty)'; card._mdLoaded = true; card._md = md || ''; };
    if (_mdCache.has(name)) { apply(_mdCache.get(name)); return; }
    _fetchSkillMarkdown(name).then(apply).catch(() => {});
  });
}

// Collapsed skills sections ("user" / "builtin"), persisted so the
// choice survives reloads. Built-in defaults to collapsed (it's
// reference info, not the user's own skills).
const _collapsedSections = (() => {
  try {
    const raw = localStorage.getItem('skillsSectionsCollapsed');
    if (raw) return new Set(JSON.parse(raw));
  } catch (_) {}
  return new Set(['builtin']);
})();
function _saveCollapsedSections() {
  try { localStorage.setItem('skillsSectionsCollapsed', JSON.stringify([..._collapsedSections])); } catch (_) {}
}
function _applySectionCollapse(container) {
  if (!container) return;
  container.querySelectorAll('.skills-section-header').forEach(h => {
    h.classList.toggle('collapsed', _collapsedSections.has(h.dataset.section));
  });
  container.querySelectorAll('.doclib-card[data-skill-section]').forEach(c => {
    c.classList.toggle('skill-card-section-hidden', _collapsedSections.has(c.dataset.skillSection));
  });
}

export async function loadSkills(cascade = false) {
  // Play the domino-in entrance on this load (set when the tab is opened,
  // not for the silent re-loads after an edit/delete).
  if (cascade) _cascadeNext = true;
  if (cascade && loaded && !_loadPromise && _playSkillsCascade()) {
    _cascadeNext = false;
    updateCount();
    return;
  }
  if (_loadPromise) return _loadPromise;
  _loadPromise = (async () => {
  try {
    const res = await fetch(`${API}/api/skills`);
    const data = await res.json();
    skills = data.skills || [];
    _loadSkillApprovalThreshold();
    // Built-in capabilities are no longer surfaced in the Skills menu.
    loaded = true;
    renderSkillsList();
    updateCount();
    if (_pendingFocusSkill) {
      _focusSkillRow(_pendingFocusSkill);
      _pendingFocusSkill = null;
    }
    // If a background audit is running, re-show its progress panel.
    if (!_auditPoll) {
      _fetchAuditStatus().then(st => {
        if (st.status === 'running') _auditAllSkills();
      }).catch(() => {});
    }
  } catch (e) {
    console.error('Failed to load skills:', e);
  } finally {
    _loadPromise = null;
  }
  })();
  return _loadPromise;
}

function _focusSkillRow(name) {
  setTimeout(() => {
    const card = document.querySelector(`.skill-card[data-skill-name="${CSS.escape(name)}"]`);
    if (!card) return;
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    card.classList.add('skill-row-flash');
    setTimeout(() => card.classList.remove('skill-row-flash'), 2000);
    // Expand it so the linked skill opens to its SKILL.md directly.
    _expandSkillCard(card, name);
  }, 200);
}

// Open the Memory modal → Skills tab → focus a specific skill row.
// Used by the chat anchor-link delegate ([name](#skill-<name>)).
export function openSkill(name) {
  _pendingFocusSkill = name || null;
  // Open the memory modal if not already open.
  const memBtn = document.getElementById('tool-memory-btn');
  if (memBtn) memBtn.click();
  // Switch to the skills tab (triggers lazy loadSkills()).
  setTimeout(() => {
    const tab = document.querySelector('.memory-tab[data-memory-tab="skills"]');
    if (tab) tab.click();
    else loadSkills();  // fallback if tab structure differs
  }, 120);
}

let _skillsSort = 'confidence';
let _showDraftsOnly = false;
let _showPublishedOnly = false;
let _confMax = null;   // confidence ceiling filter (%, e.g. 90 = show ≤90%); null = off
let _selectMode = false;
const _selectedNames = new Set();
let _skillApprovalThreshold = 0.85;

function updateCount() {
  const el = document.getElementById('skills-count');
  if (el) el.textContent = skills.length || '0';
  const elH = document.getElementById('skills-count-h2');
  if (elH) elH.textContent = skills.length + ' skill' + (skills.length === 1 ? '' : 's');
}

function _sortSkills(list) {
  const arr = list.slice();
  if (_skillsSort === 'confidence') {
    arr.sort((a, b) => (b.confidence || 0) - (a.confidence || 0) || (a.name || '').localeCompare(b.name || ''));
  } else if (_skillsSort === 'uses') {
    arr.sort((a, b) => (b.uses || 0) - (a.uses || 0) || (a.name || '').localeCompare(b.name || ''));
  } else if (_skillsSort === 'recent') {
    arr.sort((a, b) => (b.updated_at || b.created_at || 0) - (a.updated_at || a.created_at || 0));
  } else {
    arr.sort((a, b) => (a.name || '').localeCompare(b.name || ''));
  }
  return arr;
}

function _matches(sk, query) {
  const q = query.toLowerCase();
  return (
    (sk.name || '').toLowerCase().includes(q) ||
    (sk.description || '').toLowerCase().includes(q) ||
    (sk.when_to_use || sk.problem || '').toLowerCase().includes(q) ||
    (sk.category || '').toLowerCase().includes(q) ||
    (sk.tags || []).some(t => (t || '').toLowerCase().includes(q))
  );
}

function _statusPill(sk) {
  const s = sk.status || (sk._legacy ? 'legacy' : 'draft');
  if (s === 'published') return '<span class="memory-cat-badge skill-status-pill" data-status="published" style="background:color-mix(in srgb, var(--accent, #4ade80) 30%, transparent)">published</span>';
  if (s === 'draft')     return '<span class="memory-cat-badge skill-status-pill" data-status="draft" style="background:color-mix(in srgb, var(--fg) 14%, transparent)">draft</span>';
  return `<span class="memory-cat-badge skill-status-pill" data-status="${esc(s)}" style="opacity:0.6">${esc(s)}</span>`;
}

// Show a "teacher" badge for skills written by the auto-escalation
// teacher loop. Lets the user tell at-a-glance which procedures were
// hand-authored vs auto-generated so they can audit (and demote /
// edit / publish) before trusting them.
function _sourcePill(sk) {
  if (sk.source !== 'teacher-escalation') return '';
  const teacher = sk.teacher_model || 'teacher';
  return `<span class="memory-cat-badge" title="Created by teacher escalation: ${esc(teacher)}" style="background:color-mix(in srgb, var(--color-warning, #f0ad4e) 22%, transparent);">teacher-created</span>`;
}

function _modelShortName(model) {
  return String(model || '').split('/').filter(Boolean).pop() || String(model || '');
}

function _skillTokens(sk) {
  return new Set(String([
    sk.name || '',
    sk.description || '',
    sk.when_to_use || '',
    ...(sk.tags || []),
  ].join(' ')).toLowerCase()
    .replace(/-\d+\b/g, '')
    .split(/[^a-z0-9]+/)
    .filter(t => t.length > 2 && !['the', 'and', 'with', 'for', 'from', 'using'].includes(t)));
}

function _skillSimilarity(a, b) {
  const A = _skillTokens(a), B = _skillTokens(b);
  if (!A.size || !B.size) return 0;
  let inter = 0;
  for (const t of A) if (B.has(t)) inter++;
  return inter / (A.size + B.size - inter);
}

function _baseSkillName(name) {
  return String(name || '').replace(/-\d+$/, '');
}

function _scoreDuplicateKeeper(sk) {
  return [
    (sk.status === 'published') ? 100000 : 0,
    (sk.uses || 0) * 100,
    Math.round((sk.confidence || 0) * 100),
    sk.audit_by_teacher ? -5 : 0,
    -String(sk.name || '').length / 1000,
  ].reduce((a, b) => a + b, 0);
}

function _duplicateMeta(list) {
  const parent = new Map();
  const names = list.map(s => s.name || s.id).filter(Boolean);
  names.forEach(n => parent.set(n, n));
  const find = (x) => {
    let p = parent.get(x) || x;
    while (p !== parent.get(p)) p = parent.get(p);
    return p;
  };
  const unite = (a, b) => {
    const pa = find(a), pb = find(b);
    if (pa !== pb) parent.set(pb, pa);
  };
  for (let i = 0; i < list.length; i++) {
    for (let j = i + 1; j < list.length; j++) {
      const a = list[i], b = list[j];
      const an = a.name || a.id, bn = b.name || b.id;
      if (!an || !bn) continue;
      if (_baseSkillName(an) === _baseSkillName(bn) || _skillSimilarity(a, b) >= 0.38) {
        unite(an, bn);
      }
    }
  }
  const groups = new Map();
  for (const sk of list) {
    const n = sk.name || sk.id;
    if (!n) continue;
    const root = find(n);
    if (!groups.has(root)) groups.set(root, []);
    groups.get(root).push(sk);
  }
  const meta = new Map();
  let idx = 1;
  for (const group of groups.values()) {
    if (group.length < 2) continue;
    const sorted = group.slice().sort((a, b) => _scoreDuplicateKeeper(b) - _scoreDuplicateKeeper(a));
    const keep = sorted[0].name || sorted[0].id;
    const groupNames = sorted.map(s => s.name || s.id).filter(Boolean);
    for (const sk of sorted) {
      const n = sk.name || sk.id;
      meta.set(n, { group: idx, keep: n === keep, keepName: keep, names: groupNames });
    }
    idx++;
  }
  return meta;
}

function _auditModelPills(sk) {
  const worker = sk.audit_worker_model || '';
  const teacher = sk.audit_teacher_model || '';
  let html = '';
  if (worker) {
    html += `<span class="memory-cat-badge skill-model-pill skill-model-student" title="Last audited by default audit model: ${esc(worker)}">audit</span>`;
  }
  if (sk.audit_by_teacher || teacher) {
    const title = teacher
      ? `Teacher rewrote this skill; audit model passed after the rewrite. Teacher: ${teacher}`
      : 'Teacher rewrote this skill; audit model passed after the rewrite.';
    html += `<span class="memory-cat-badge skill-model-pill skill-model-teacher" title="${esc(title)}">teacher-fixed</span>`;
  }
  return html;
}

function _necessityKind(sk) {
  const nec = sk && sk.necessity;
  if (sk && sk._duplicateGroup) return 'duplicate';
  if (!nec || nec.necessary !== false) return null;
  const reason = String(nec.reason || '').toLowerCase();
  const redundant = (nec.redundant_with || []).filter(Boolean);
  if (redundant.length || /duplicat|redundan|overlap|same skill|same procedure/.test(reason)) return 'duplicate';
  if (/trivial|generic|capable assistant|without a saved|not need|unnecessary/.test(reason)) return 'trivial';
  return 'irrelevant';
}

function _necessityPill(sk) {
  const kind = _necessityKind(sk);
  if (!kind) return '';
  const nec = sk.necessity || {};
  const dup = (nec.redundant_with || []).filter(Boolean);
  const label = kind === 'duplicate' ? (sk._duplicateGroup ? `duplicate #${sk._duplicateGroup}` : 'duplicate')
    : kind === 'trivial' ? 'generic'
    : 'possibly-irrelevant';
  const group = sk._duplicateNames || [];
  const why = sk._duplicateGroup
    ? `Duplicate group #${sk._duplicateGroup}. Recommended keep: ${sk._duplicateKeepName}. Group: ${group.join(', ')}`
    : (nec.reason || 'May not be worth keeping') + (dup.length ? ' | overlaps: ' + dup.join(', ') : '');
  return `<span class="memory-cat-badge skill-necessity-pill skill-necessity-${kind}" title="${esc(why)}">${label}</span>`;
}

function _duplicatePriorityPill(sk) {
  if (!sk._duplicateGroup) return '';
  if (sk._duplicateKeep) {
    return `<span class="memory-cat-badge skill-duplicate-keep" title="Best duplicate candidate by published status, uses, confidence, and specificity">recommended</span>`;
  }
  return `<span class="memory-cat-badge skill-duplicate-lower" title="Lower-priority duplicate. Suggested keeper: ${esc(sk._duplicateKeepName || '')}">lower-priority</span>`;
}

// Verified-by-test indicators shown next to the confidence %. A check when a
// test/audit run passed; a graduation-cap when the teacher model had to
// rewrite the skill to make it pass. SVG (no Unicode emoji).
function _auditMarks(sk) {
  let html = '';
  if (sk.audit_verdict === 'pass') {
    html += `<span class="skill-verified" title="Passed an automated test"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg></span>`;
  }
  if (sk.audit_by_teacher) {
    const teacher = sk.audit_teacher_model ? `: ${sk.audit_teacher_model}` : '';
    html += `<span class="skill-teachermark" title="Teacher rewrote this skill; audit model passed after the rewrite${esc(teacher)}"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 10L12 5 2 10l10 5 10-5z"/><path d="M6 12v5c0 1 3 2 6 2s6-1 6-2v-5"/></svg></span>`;
  }
  return html;
}

// Audit verdict dot — removed at user request. The ✓ check-mark next to the
// confidence % still indicates a pass. Stub returns empty so the surrounding
// header HTML still composes without changing other layout.
function _auditDot(sk) { return ''; }

function _isDraftsFilter() { return !!_showDraftsOnly; }

// Confidence → colour. 90%+ is solidly green, scaling down through
// yellow/orange to red at 50% and below (hue 120→0 over 90→50).
function _confColor(conf) {
  const hue = Math.max(0, Math.min(120, ((conf - 50) / 40) * 120));
  return `hsl(${Math.round(hue)}, 70%, 42%)`;
}

// Shared action icons (collapsed kebab menu + expanded footer use the same).
const _ICON = {
  del:   '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>',
  edit:  '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>',
  approve: '<polyline points="20 6 9 17 4 12"/>',
  unpublish: '<path d="M5 12l5 5L20 7"/>',
  test:  '<polygon points="5 3 19 12 5 21 5 3"/>',
};
function _svg(paths, { fill = 'none', size = 13 } = {}) {
  const stroke = fill === 'currentColor' ? '' : 'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="${fill}" ${stroke} style="vertical-align:-2px;flex-shrink:0;">${paths}</svg>`;
}

// Kebab dropdown for a collapsed skill card — same actions + icons as the
// expanded footer (Publish/Unpublish · Edit · Delete).
function _openSkillMenu(btn, card, sk, name, isPublished) {
  document.querySelectorAll('.skill-kebab-menu').forEach(m => m.remove());
  const menu = document.createElement('div');
  menu.className = 'skill-kebab-menu';
  const mk = (paths, label, opts, onClick) => {
    const item = document.createElement('button');
    item.className = 'skill-kebab-item' + (opts && opts.danger ? ' danger' : '');
    item.innerHTML = _svg(paths, opts) + `<span>${label}</span>`;
    item.addEventListener('click', (e) => { e.stopPropagation(); menu.remove(); onClick(); });
    menu.appendChild(item);
  };
  if (isPublished) mk(_ICON.unpublish, 'Unpublish', {}, () => _setSkillStatus(name, 'draft'));
  else mk(_ICON.approve, 'Publish', {}, () => _setSkillStatus(name, 'published'));
  mk(_ICON.edit, 'Edit', {}, async () => {
    if (!card.classList.contains('doclib-card-expanded')) await _expandSkillCard(card, name);
    _toggleSkillEdit(card, name);
  });
  mk(_ICON.test, 'Test', {}, () => _testSkill(card, name));
  // Audit kicks off the bulk audit-all loop (test → judge → fix → retry → demote).
  // Starts at the top of the list and walks down.
  mk(_ICON.test, 'Audit', {}, () => _auditAllSkills());
  mk(_ICON.del, 'Delete', { danger: true }, () => _deleteSkill(name, card));

  // Select — enters bulk-select mode and pre-selects this skill. Same pattern
  // as the email/documents/brain Select item, with the email bullet icon.
  const selItem = document.createElement('button');
  selItem.className = 'skill-kebab-item';
  selItem.innerHTML = '<span style="display:inline-flex;width:14px;height:14px;align-items:center;justify-content:center;"><span style="font-size:16px;line-height:1;">●</span></span><span>Select</span>';
  selItem.addEventListener('click', (e) => {
    e.stopPropagation();
    menu.remove();
    if (!_selectMode) _enterSelectMode();
    _selectedNames.add(name);
    renderSkillsList();
  });
  menu.appendChild(selItem);

  // Mobile-only Cancel — mirrors the email/documents/brain popup pattern.
  // CSS hides `.dropdown-cancel-mobile` on desktop where outside-click
  // already dismisses cleanly.
  const cancelItem = document.createElement('button');
  cancelItem.className = 'skill-kebab-item dropdown-cancel-mobile';
  cancelItem.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg><span>Cancel</span>';
  cancelItem.addEventListener('click', (e) => { e.stopPropagation(); menu.remove(); });
  menu.appendChild(cancelItem);

  document.body.appendChild(menu);
  const r = btn.getBoundingClientRect();
  menu.style.top = (r.bottom + 4) + 'px';
  menu.style.right = Math.max(6, window.innerWidth - r.right) + 'px';
  // Keep it on-screen (mobile): flip above the button if it would overflow the
  // bottom, clamp the left edge, and cap the height as a last resort.
  const mr = menu.getBoundingClientRect();
  if (mr.bottom > window.innerHeight - 6) {
    menu.style.top = Math.max(6, r.top - mr.height - 4) + 'px';
  }
  if (mr.left < 6) {
    menu.style.right = Math.max(6, window.innerWidth - 6 - mr.width) + 'px';
  }
  const mr2 = menu.getBoundingClientRect();
  if (mr2.bottom > window.innerHeight - 6) {
    menu.style.maxHeight = Math.max(80, window.innerHeight - 12 - mr2.top) + 'px';
    menu.style.overflowY = 'auto';
  }
  const close = (ev) => { if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener('click', close, true); } };
  setTimeout(() => document.addEventListener('click', close, true), 0);
}

// Cards for the agent's built-in tool capabilities (from
// /api/skills/builtin → TOOL_SECTIONS). Expandable to preview the
// instruction block; editable with a warning + a revert-to-default
// button (overrides stored in settings, applied to the prompt).
function _buildBuiltinCards() {
  return builtinSkills.map(b => {
    const card = document.createElement('div');
    card.className = 'doclib-card skill-card skill-builtin-card';
    card.dataset.builtinName = b.name;

    const header = document.createElement('div');
    header.className = 'doclib-card-header skill-card-header';
    header.innerHTML = `
      <span class="skill-conf-dot" style="display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--accent, var(--red));flex-shrink:0;margin-right:6px;opacity:0.55;"></span>
      <div style="flex:1;min-width:0;overflow:hidden;">
        <div class="doclib-card-title" style="display:flex;align-items:center;gap:6px;min-width:0;">
          <code style="font-weight:600;font-size:0.9em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex-shrink:1;min-width:0;">${esc(b.name)}</code>
          <span class="memory-cat-badge" style="background:color-mix(in srgb, var(--fg) 14%, transparent)">built-in</span>
          ${b.is_overridden ? '<span class="memory-cat-badge" title="You have edited this built-in capability" style="background:color-mix(in srgb, var(--color-warning, #f0ad4e) 30%, transparent);">edited</span>' : ''}
        </div>
        ${b.description ? `<div class="doclib-card-session" title="${esc(b.description)}" style="font-size:10px;opacity:0.55;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc(b.description)}</div>` : ''}
      </div>
      <span class="doclib-card-chevron"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></span>
    `;
    card.appendChild(header);

    const preview = document.createElement('div');
    preview.className = 'doclib-card-preview skill-card-preview';
    // Warning banner — editing a built-in changes how the assistant uses a native tool.
    const warn = document.createElement('div');
    warn.className = 'skill-builtin-warn';
    warn.innerHTML = '⚠ This is a built-in capability. Editing changes how the assistant is instructed to use this native tool — it can break or alter core behaviour. Use Revert to restore the shipped default.';
    preview.appendChild(warn);
    const pre = document.createElement('pre');
    pre.className = 'skill-md-pre';
    pre.textContent = '';  // filled on expand
    preview.appendChild(pre);

    // Footer: Revert (left, only meaningful when overridden) · Edit/Save (right).
    const actions = document.createElement('div');
    actions.className = 'doclib-card-expanded-actions';

    const revertBtn = document.createElement('button');
    revertBtn.className = 'doclib-card-text-btn doclib-card-action-btn doclib-card-text-btn-danger';
    revertBtn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>Revert';
    revertBtn.title = 'Restore the original shipped instructions';
    revertBtn.addEventListener('click', (e) => { e.stopPropagation(); _revertBuiltin(b.name); });

    const editBtn = document.createElement('button');
    editBtn.className = 'doclib-card-text-btn doclib-card-action-btn';
    editBtn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>Edit';
    editBtn.addEventListener('click', (e) => { e.stopPropagation(); _toggleBuiltinEdit(card, b.name); });

    const rightGroup = document.createElement('div');
    rightGroup.className = 'doclib-action-group';
    const btnRow = document.createElement('div');
    btnRow.className = 'doclib-action-btn-row';
    btnRow.appendChild(editBtn);
    rightGroup.appendChild(btnRow);

    actions.appendChild(revertBtn);
    actions.appendChild(rightGroup);
    preview.appendChild(actions);
    card.appendChild(preview);

    card.addEventListener('click', (e) => {
      if (e.target.closest('button, input, textarea')) return;
      _expandBuiltinCard(card, b.name);
    });
    return card;
  });
}

async function _expandBuiltinCard(card, name) {
  const grid = card.closest('.doclib-grid');
  if (card.classList.contains('doclib-card-expanded')) {
    card.classList.remove('doclib-card-expanded');
    return;
  }
  if (grid) grid.querySelectorAll('.doclib-card-expanded').forEach(c => c.classList.remove('doclib-card-expanded'));
  card.classList.add('doclib-card-expanded');
  if (grid) grid.scrollTop = 0;
  const pre = card.querySelector('.skill-md-pre');
  if (pre && !card._loaded) {
    pre.textContent = 'Loading…';
    try {
      const res = await fetch(`${API}/api/skills/builtin/${encodeURIComponent(name)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      pre.textContent = data.text || '(empty)';
      card._loaded = true;
      card._text = data.text || '';
      card._default = data.default || '';
    } catch (e) {
      pre.textContent = 'Failed to load.';
    }
  }
}

function _toggleBuiltinEdit(card, name) {
  const preview = card.querySelector('.skill-card-preview');
  if (!preview) return;
  if (preview.querySelector('.skill-md-editor')) { _saveBuiltinEdit(card, name); return; }
  const pre = preview.querySelector('.skill-md-pre');
  const ta = document.createElement('textarea');
  ta.className = 'skill-md-editor';
  ta.spellcheck = false;
  ta.value = (card._text != null ? card._text : (pre ? pre.textContent : '')) || '';
  ta.addEventListener('click', (e) => e.stopPropagation());
  if (pre) pre.style.display = 'none';
  preview.insertBefore(ta, preview.querySelector('.doclib-card-expanded-actions'));
  ta.focus();
  const editBtn = [...preview.querySelectorAll('.doclib-card-action-btn')].find(b => /Edit|Save/.test(b.textContent));
  if (editBtn) editBtn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>Save';
}

async function _saveBuiltinEdit(card, name) {
  const ta = card.querySelector('.skill-md-editor');
  if (!ta) return;
  try {
    const res = await fetch(`${API}/api/skills/builtin/${encodeURIComponent(name)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: ta.value }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    uiModule.showToast('Built-in capability updated');
    builtinSkills = [];  // force reload of built-in list (refreshes "edited" badge)
    await loadSkills();
  } catch (e) { uiModule.showError('Save failed: ' + e.message); }
}

async function _revertBuiltin(name) {
  if (!(await uiModule.styledConfirm(`Revert "${name}" to its original built-in instructions?`, { confirmText: 'Revert', danger: true }))) return;
  try {
    const res = await fetch(`${API}/api/skills/builtin/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    uiModule.showToast('Reverted to default');
    builtinSkills = [];
    await loadSkills();
  } catch (e) { uiModule.showError('Revert failed: ' + e.message); }
}

function _getFilteredSkills() {
  const query = (document.getElementById('skills-search')?.value || '').toLowerCase();
  let filtered = query ? skills.filter(sk => _matches(sk, query)) : skills;
  if (_showDraftsOnly) {
    filtered = filtered.filter(sk => (sk.status || 'draft') !== 'published');
  }
  if (_showPublishedOnly) {
    filtered = filtered.filter(sk => (sk.status || 'draft') === 'published');
  }
  if (_confMax != null) {
    // "≤ X%" — surface the lower-confidence skills that may need review.
    filtered = filtered.filter(sk => Math.round((sk.confidence || 0) * 100) <= _confMax);
  }
  return _sortSkills(filtered);
}

function renderSkillsList() {
  const container = document.getElementById('skills-list');
  if (!container) return;
  // Re-render rebuilds the cards (none expanded), so clear the expand flag
  // on the admin-card or it would keep the toolbar hidden with nothing open.
  container.closest('.admin-card')?.classList.remove('skills-has-expanded');

  const sorted = _getFilteredSkills();
  // Built-in capabilities show as their own read-only section (skipped when
  // the user is filtering to drafts, since built-ins aren't drafts).
  // Skills menu shows the user's own skills only (built-in capabilities
  // are intentionally not surfaced here).
  const showBuiltin = false;

  if (!sorted.length && !showBuiltin) {
    const selectBtn = document.getElementById('skills-select-btn');
    if (selectBtn) selectBtn.disabled = true;
    if (_selectMode) _exitSelectMode();
    container.innerHTML = `<div style="text-align:center;opacity:0.4;padding:24px 0;font-size:11px;">${loaded ? 'No skills yet, use agent for it to auto extract them.' : 'Loading…'}</div>`;
    return;
  }

  const selectBtn = document.getElementById('skills-select-btn');
  if (selectBtn) selectBtn.disabled = false;

  // Library-style cards: a compact bar that expands in-place to show the
  // SKILL.md, with a footer (Delete left; Edit / Run / Approve right).
  // Reuses the proven .doclib-card / .doclib-card-preview /
  // .doclib-card-expanded-actions markup so the desktop+mobile expand +
  // footer behaviour matches the document/chat library exactly.
  //
  // #skills-list itself becomes the .doclib-grid (rather than a nested
  // grid) so the global "hide non-grid children when a card is expanded"
  // rule (.admin-card:has(.doclib-card-expanded) > *:not(.doclib-grid))
  // doesn't hide the list container along with everything else.
  container.classList.add('doclib-grid');
  const cards = [];
  const dupeMeta = _duplicateMeta(sorted);

  for (const sk of sorted) {
    const name = sk.name || sk.id;
    const dm = dupeMeta.get(name);
    if (dm) {
      sk._duplicateGroup = dm.group;
      sk._duplicateKeep = dm.keep;
      sk._duplicateKeepName = dm.keepName;
      sk._duplicateNames = dm.names;
    } else {
      delete sk._duplicateGroup;
      delete sk._duplicateKeep;
      delete sk._duplicateKeepName;
      delete sk._duplicateNames;
    }
    const conf = Math.round((sk.confidence || 0) * 100);
    const uses = sk.uses || 0;
    const isPublished = (sk.status === 'published');
    const confColor = _confColor(conf);

    const card = document.createElement('div');
    card.className = 'doclib-card skill-card';
    card.dataset.skillName = name;
    card.dataset.skillStatus = sk.status || 'draft';

    const checked = _selectedNames.has(name) ? 'checked' : '';
    const cbHtml = _selectMode
      ? `<input type="checkbox" class="memory-select-cb skill-select-cb" data-name="${esc(name)}" ${checked} style="margin-right:6px;flex-shrink:0;cursor:pointer;" />`
      : '';

    // Collapsed header bar: dot · name (wraps) · [pills (right) · stats · menu].
    const header = document.createElement('div');
    header.className = 'doclib-card-header skill-card-header';
    header.innerHTML = `
      ${cbHtml}
      ${_auditDot(sk)}
      <div class="skill-card-textcol">
        <code class="skill-card-name">${esc(name)}</code>
        ${sk.description ? `<div class="skill-card-desc">${esc(sk.description)}</div>` : ''}
      </div>
      <div class="skill-card-right">
        ${_statusPill(sk)}
        ${_sourcePill(sk)}
        ${_auditModelPills(sk)}
        ${_necessityPill(sk)}
        ${_duplicatePriorityPill(sk)}
        <span class="skill-stats">${_auditMarks(sk)}<span class="skill-conf" style="color:${confColor};">${conf}%</span> · ${uses}u</span>
        <span class="skill-chevron-up" title="Collapse"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"/></svg></span>
        <button class="skill-kebab-btn" title="Actions" aria-label="Actions"><svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="12" cy="19" r="1.6"/></svg></button>
      </div>
    `;
    card.appendChild(header);

    // Kebab dropdown (collapsed-bar quick actions: same set + icons as the
    // expanded footer). Clicking the kebab opens it; it doesn't expand.
    header.querySelector('.skill-kebab-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      _openSkillMenu(e.currentTarget, card, sk, name, isPublished);
    });

    // Preview (hidden until expanded) — SKILL.md goes here + footer.
    const preview = document.createElement('div');
    preview.className = 'doclib-card-preview skill-card-preview';
    const pre = document.createElement('pre');
    pre.className = 'skill-md-pre';
    pre.textContent = '';  // filled on expand
    preview.appendChild(pre);

    // Footer: Approve/Unpublish on the left, destructive delete on the right.
    const actions = document.createElement('div');
    actions.className = 'doclib-card-expanded-actions';

    const delBtn = document.createElement('button');
    delBtn.className = 'doclib-card-text-btn doclib-card-action-btn doclib-card-text-btn-danger';
    delBtn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>Delete';
    delBtn.addEventListener('click', (e) => { e.stopPropagation(); _deleteSkill(name, card); });

    const editBtn = document.createElement('button');
    editBtn.className = 'doclib-card-text-btn doclib-card-action-btn';
    editBtn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>Edit';
    editBtn.addEventListener('click', (e) => { e.stopPropagation(); _toggleSkillEdit(card, name); });

    const pubBtn = document.createElement('button');
    pubBtn.className = 'doclib-card-text-btn doclib-card-action-btn';
    if (isPublished) {
      pubBtn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 12l5 5L20 7"/></svg>Unpublish';
      pubBtn.title = 'Move back to draft';
      pubBtn.addEventListener('click', (e) => { e.stopPropagation(); _setSkillStatus(name, 'draft'); });
    } else {
      pubBtn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="20 6 9 17 4 12"/></svg>Publish';
      pubBtn.title = 'Publish — appears in the skills index';
      pubBtn.style.color = 'var(--color-success, #4caf50)';
      pubBtn.addEventListener('click', (e) => { e.stopPropagation(); _setSkillStatus(name, 'published'); });
    }

    // Test/audit this one skill — same action that's in the kebab, surfaced in
    // the footer too so it's not buried under the "⋯" menu.
    const testBtn = document.createElement('button');
    testBtn.className = 'doclib-card-text-btn doclib-card-action-btn';
    testBtn.innerHTML = _svg(_ICON.test, { size: 11 }) + 'Test';
    testBtn.title = 'Test this skill — run it + AI judge';
    testBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      // Immediate visual feedback: previously the click looked like nothing
      // happened because _testSkill awaits a status fetch before overwriting
      // the preview — so users would tap a second time. Mark the button as
      // pending right away so the first tap is obviously registered.
      if (testBtn.dataset.busy === '1') return;  // also dedupe rapid double-tap
      testBtn.dataset.busy = '1';
      testBtn.disabled = true;
      const _origHTML = testBtn.innerHTML;
      testBtn.innerHTML = _svg(_ICON.test, { size: 11 }) + 'Starting…';
      Promise.resolve(_testSkill(card, name)).finally(() => {
        // The preview gets overwritten by _testSkill, which removes the
        // testBtn from the DOM. The cleanup below only matters if the
        // button still exists (e.g. _testSkill bailed early).
        if (document.body.contains(testBtn)) {
          testBtn.disabled = false;
          testBtn.dataset.busy = '';
          testBtn.innerHTML = _origHTML;
        }
      });
    });

    const rightGroup = document.createElement('div');
    rightGroup.className = 'doclib-action-group';
    const btnRow = document.createElement('div');
    btnRow.className = 'doclib-action-btn-row';
    btnRow.appendChild(testBtn);
    btnRow.appendChild(editBtn);
    btnRow.appendChild(delBtn);
    rightGroup.appendChild(btnRow);

    actions.appendChild(pubBtn);
    actions.appendChild(rightGroup);
    preview.appendChild(actions);
    card.appendChild(preview);

    // Click to expand/collapse (unless in select mode → toggle checkbox).
    card.addEventListener('click', (e) => {
      if (card._suppressNextClick) { card._suppressNextClick = false; return; }
      if (e.target.closest('button, input, textarea')) return;
      if (_selectMode) {
        const cb = card.querySelector('.skill-select-cb');
        if (cb) { cb.checked = !cb.checked; cb.dispatchEvent(new Event('change')); }
        return;
      }
      _expandSkillCard(card, name);
    });

    // Long-press anywhere on the card opens the kebab dropdown — mirrors the
    // documents library + brain memory pattern. Skip when touch starts on a
    // button/input so per-control handlers keep working.
    {
      const kebab = header.querySelector('.skill-kebab-btn');
      let hold = null;
      let start = null;
      const _lpCancel = () => { if (hold) { clearTimeout(hold); hold = null; } start = null; };
      card.addEventListener('pointerdown', (e) => {
        if (e.target.closest('.skill-kebab-btn, .skill-select-cb, button, input, textarea')) return;
        start = { x: e.clientX, y: e.clientY };
        hold = setTimeout(() => {
          hold = null;
          card._suppressNextClick = true;
          setTimeout(() => { card._suppressNextClick = false; }, 400);
          if (navigator.vibrate) try { navigator.vibrate(15); } catch {}
          if (kebab) kebab.click();
        }, 500);
      });
      card.addEventListener('pointermove', (e) => {
        if (!start) return;
        if (Math.hypot(e.clientX - start.x, e.clientY - start.y) > 10) _lpCancel();
      });
      card.addEventListener('pointerup', _lpCancel);
      card.addEventListener('pointercancel', _lpCancel);
    }

    cards.push(card);
  }
  container.innerHTML = '';

  // Two collapsible sections — "Your skills" and "Built-in". Headers and
  // cards are all DIRECT children of the grid (cards tagged with
  // data-skill-section) so the global expand rule — which hides sibling
  // .doclib-card elements by direct-child selector — keeps working.
  // Collapse just toggles display on the tagged cards.
  const _mkSectionHeader = (sectionId, title, count) => {
    const collapsed = _collapsedSections.has(sectionId);
    const hdr = document.createElement('div');
    hdr.className = 'skills-section-label skills-section-header' + (collapsed ? ' collapsed' : '');
    hdr.dataset.section = sectionId;
    hdr.innerHTML =
      `<svg class="skills-section-chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>` +
      `<span>${esc(title)}</span>` +
      `<span class="skills-section-count">${count}</span>`;
    hdr.addEventListener('click', () => {
      if (_collapsedSections.has(sectionId)) _collapsedSections.delete(sectionId);
      else _collapsedSections.add(sectionId);
      _saveCollapsedSections();
      _applySectionCollapse(container);
    });
    return hdr;
  };

  // "Your skills" section — show the header only when there's also a
  // built-in section to distinguish from (otherwise it's just the list).
  if (cards.length) {
    if (showBuiltin) container.appendChild(_mkSectionHeader('user', 'Your skills', cards.length));
    cards.forEach(c => { c.dataset.skillSection = 'user'; container.appendChild(c); });
  }

  // Built-in capabilities — read-only cards (the agent's native tools).
  if (showBuiltin) {
    const builtinCards = _buildBuiltinCards();
    container.appendChild(_mkSectionHeader('builtin', 'Built-in capabilities', builtinCards.length));
    builtinCards.forEach(c => { c.dataset.skillSection = 'builtin'; container.appendChild(c); });
  }

  _applySectionCollapse(container);

  // Domino-in cascade when the Skills tab is (re)opened — same sleek
  // staggered entrance the document/chat library uses (.doclib-just-opened
  // → section-domino-in on each .doclib-card child). Only consumes the flag
  // set on tab-open, so search/sort/edit re-renders stay instant.
  if (_cascadeNext && cards.length) {
    _cascadeNext = false;
    _playSkillsCascade(container);
  }

  // Select-mode checkbox wiring (card-body click is handled in the card's
  // own click listener above).
  if (_selectMode) {
    container.querySelectorAll('.skill-select-cb').forEach(cb => {
      cb.addEventListener('change', () => {
        const name = cb.dataset.name;
        if (cb.checked) _selectedNames.add(name); else _selectedNames.delete(name);
        const all = document.getElementById('skills-select-all');
        if (all) {
          const visible = _getFilteredSkills().map(s => s.name || s.id);
          all.checked = visible.length > 0 && visible.every(n => _selectedNames.has(n));
        }
        _updateBulkBar();
      });
    });
  }

  // Background-load the visible skills' SKILL.md so expanding any of them is
  // instant (no first-time async fetch → no jump). Deferred so it never
  // competes with the render/cascade paint.
  setTimeout(_preloadVisibleMarkdown, 0);
}

// ---- Card expand / edit / actions ----

// Collapse an expanded skill card: drop the class AND clear the inline
// heights skills.js pinned on the card/preview/<pre> (otherwise a collapsed
// card keeps its full expanded height) and detach its resize listener.
function _collapseSkillCardEl(c) {
  c.classList.remove('doclib-card-expanded', 'skill-expand-instant');
  c.style.removeProperty('height');
  const pv = c.querySelector('.doclib-card-preview');
  const pr = c.querySelector('.skill-md-pre') || c.querySelector('.skill-md-editor');
  if (pv) { pv.style.removeProperty('height'); pv.style.removeProperty('flex'); pv.style.removeProperty('max-height'); }
  if (pr) { pr.style.removeProperty('height'); pr.style.removeProperty('flex'); }
  if (c._fillH) window.removeEventListener('resize', c._fillH);
}

async function _expandSkillCard(card, name) {
  const grid = card.closest('.doclib-grid');
  const adminCard = card.closest('.admin-card');
  // Toggle collapse if already open.
  if (card.classList.contains('doclib-card-expanded')) {
    _collapseSkillCardEl(card);
    if (adminCard) adminCard.classList.remove('skills-has-expanded');
    return;
  }
  // Were we already showing another expanded card? If so this is a SWITCH,
  // not a fresh open — skip the fade-in. The fade reveals the previous card
  // collapsing behind the new (semi-transparent) one, which read as a jump.
  const switching = !!(grid && grid.querySelector('.doclib-card-expanded'));
  // Collapse any other expanded sibling (full cleanup, not just the class).
  if (grid) grid.querySelectorAll('.doclib-card-expanded').forEach(_collapseSkillCardEl);
  card.classList.add('doclib-card-expanded');
  if (switching) card.classList.add('skill-expand-instant');
  // Explicit class on the admin-card so CSS doesn't depend on :has()
  // (Firefox mobile builds without :has left the expand at ~50%).
  if (adminCard) adminCard.classList.add('skills-has-expanded');
  if (grid) grid.scrollTop = 0;

  // Firefox doesn't treat the absolutely-positioned card's stretched height
  // (inset:0) or height:100% as DEFINITE, so grid/flex children won't fill.
  // Pin an explicit px height = the card's already-rendered height. A px
  // value is unambiguously definite, so the preview + <pre> finally fill.
  card._fillH = () => {
    // Reset any prior inline heights so we measure the natural box first
    // (and so switching desktop<->mobile never leaves stale px values).
    card.style.removeProperty('height');
    const preview = card.querySelector('.doclib-card-preview');
    const header = card.querySelector('.skill-card-header');
    const pre = card.querySelector('.skill-md-pre') || card.querySelector('.skill-md-editor');
    if (preview) { preview.style.removeProperty('height'); preview.style.removeProperty('flex'); preview.style.removeProperty('max-height'); }
    if (pre) { pre.style.removeProperty('height'); pre.style.removeProperty('flex'); }

    // The px-pinning is ONLY for the mobile layout (position:absolute fill,
    // where Firefox won't propagate a definite height). On desktop the card
    // expands via normal flex/flow — pinning measured heights there just
    // under-sizes it. So bail on desktop and let the CSS handle it.
    if (!window.matchMedia('(max-width: 768px)').matches) return;

    const cardH = card.getBoundingClientRect().height;
    if (cardH <= 0) return;
    card.style.setProperty('height', cardH + 'px', 'important');
    if (!preview) return;

    const px = (el, prop) => parseFloat(getComputedStyle(el)[prop]) || 0;
    const headerH = header ? header.getBoundingClientRect().height : 0;
    const cardPad = px(card, 'paddingTop') + px(card, 'paddingBottom');
    const previewH = Math.max(0, cardH - headerH - cardPad);
    // Force the preview to an explicit height (flex:none so nothing fights it).
    // A max-height (~335px, resolved from a % rule) was capping it — clear it.
    preview.style.setProperty('flex', '0 0 auto', 'important');
    preview.style.setProperty('max-height', 'none', 'important');
    preview.style.setProperty('height', previewH + 'px', 'important');

    if (pre) {
      // Pre = preview height minus its non-pre siblings (footer, warn banner).
      const prevPad = px(preview, 'paddingTop') + px(preview, 'paddingBottom');
      let siblings = 0;
      for (const child of preview.children) {
        if (child !== pre) siblings += child.getBoundingClientRect().height;
      }
      const preH = Math.max(0, previewH - prevPad - siblings);
      pre.style.setProperty('height', preH + 'px', 'important');
      pre.style.setProperty('flex', '0 0 auto', 'important');
    }
  };
  // Size SYNCHRONOUSLY (not in rAF) so the pinned heights are in place before
  // the browser's first paint of the expanded card. Running it a frame later
  // let the first frame paint at content-height, then snap — the "explosion"
  // that showed on the first expand (when the SKILL.md was still loading).
  card._fillH();
  window.addEventListener('resize', card._fillH);

  const pre = card.querySelector('.skill-md-pre');
  if (pre && !card._mdLoaded) {
    // Use the cache when available (the bg preload usually has it already),
    // so the content is in place synchronously — no async settle/jump.
    if (_mdCache.has(name)) {
      const md = _mdCache.get(name);
      pre.textContent = md || '(empty)';
      card._mdLoaded = true;
      card._md = md || '';
    } else {
      pre.textContent = 'Loading…';
      try {
        const md = await _fetchSkillMarkdown(name);
        pre.textContent = md || '(empty)';
        card._mdLoaded = true;
        card._md = md;
      } catch (e) {
        pre.textContent = 'Failed to load SKILL.md';
      }
    }
  }
}

// Swap the read-only <pre> for an editable <textarea> (and back). The
// Edit button toggles; a Save button commits via the markdown endpoint.
function _toggleSkillEdit(card, name) {
  const preview = card.querySelector('.skill-card-preview');
  if (!preview) return;
  const existing = preview.querySelector('.skill-md-editor');
  if (existing) {
    // Already editing — treat Edit as Save.
    _saveSkillEdit(card, name);
    return;
  }
  const pre = preview.querySelector('.skill-md-pre');
  const ta = document.createElement('textarea');
  ta.className = 'skill-md-editor';
  ta.spellcheck = false;
  ta.value = (card._md != null ? card._md : (pre ? pre.textContent : '')) || '';
  ta.addEventListener('click', (e) => e.stopPropagation());
  if (pre) pre.style.display = 'none';
  preview.insertBefore(ta, preview.querySelector('.doclib-card-expanded-actions'));
  ta.focus();
  // Flip the Edit button label to "Save".
  const editBtn = [...preview.querySelectorAll('.doclib-card-action-btn')].find(b => /Edit|Save/.test(b.textContent));
  if (editBtn) editBtn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>Save';
}

async function _saveSkillEdit(card, name) {
  const preview = card.querySelector('.skill-card-preview');
  const ta = preview?.querySelector('.skill-md-editor');
  if (!ta) return;
  try {
    const res = await fetch(`${API}/api/skills/${encodeURIComponent(name)}/markdown`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ markdown: ta.value }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    // Refresh the cached markdown so the preload/expand show the new text.
    _mdCache.set(name, ta.value);
    uiModule.showToast('Saved');
    await loadSkills();  // re-render (frontmatter changes like name/status may have changed)
  } catch (e) {
    uiModule.showError('Save failed: ' + e.message);
  }
}

async function _deleteSkill(name, card = null) {
  if (!(await uiModule.styledConfirm(`Delete skill "${name}"? This removes the SKILL.md.`, { confirmText: 'Delete', danger: true }))) return;
  // Locate the card if the caller didn't hand one over, so we can collapse it
  // away gracefully (same fade+shrink as the document library) instead of
  // re-rendering the whole list.
  if (!card) {
    card = [...document.querySelectorAll('.skill-card')]
      .find(c => { const n = c.querySelector('.skill-card-name'); return n && n.textContent === name; }) || null;
  }
  try {
    await fetch(`${API}/api/skills/${encodeURIComponent(name)}`, { method: 'DELETE' });
    _mdCache.delete(name);
    if (card) {
      if (card._testPoll) { clearInterval(card._testPoll); card._testPoll = null; }
      _setCardRunning(card, false);
      card.classList.add('doclib-card-deleting');
      card.addEventListener('transitionend', () => card.remove(), { once: true });
      setTimeout(() => { if (card.parentElement) card.remove(); }, 400);
    }
    await loadSkills();
    uiModule.showToast('Skill deleted');
  } catch (e) { uiModule.showError('Delete failed: ' + e.message); }
}

async function _setSkillStatus(name, status) {
  try {
    await fetch(`${API}/api/skills/${encodeURIComponent(name)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    await loadSkills();
    uiModule.showToast(status === 'published' ? 'Skill approved' : 'Skill moved to draft');
  } catch (e) { uiModule.showError('Update failed: ' + e.message); }
}

// ---- Test a skill (sandbox agent run + AI eval) ----

async function _fetchTestStatus(name) {
  try {
    const r = await fetch(`${API}/api/skills/${encodeURIComponent(name)}/test-status`);
    return r.ok ? await r.json() : { status: 'none' };
  } catch { return { status: 'none' }; }
}

function _renderTestLog(logEl, verdictEl, job, card, name) {
  if (!logEl) return;
  logEl.innerHTML = '';
  const add = (txt, cls) => { const d = document.createElement('div'); if (cls) d.className = cls; d.textContent = txt; logEl.appendChild(d); };
  for (const ev of (job.log || [])) {
    if (ev.type === 'skill_test_start') { add('Task: ' + ev.task, 'skill-test-task'); add('Model: ' + ev.model, 'skill-test-meta'); }
    else if (ev.type === 'agent_step') add('— round ' + ev.round + ' —', 'skill-test-round');
    else if (ev.type === 'tool_start') add('▸ ' + ev.tool + '  ' + String(ev.command || '').slice(0, 200), 'skill-test-tool');
    else if (ev.type === 'tool_output') add(String(ev.output || '').slice(0, 500), 'skill-test-out');
    else if (ev.type === 'say') add(ev.text || '', 'skill-test-say');
    else if (ev.type === 'evaluating') add('Evaluating run…', 'skill-test-meta');
    else if (ev.type === 'error') add('Error: ' + (ev.error || 'run failed'), 'skill-test-err');
  }
  if (job.status === 'running') add('…running (you can close this — it keeps going)', 'skill-test-meta');
  logEl.scrollTop = logEl.scrollHeight;
  if (job.status === 'done' && job.verdict) _renderTestVerdict(verdictEl, job.verdict, card, name);
  else if (verdictEl) verdictEl.innerHTML = '';
}

// `force` = start a fresh run even if a finished result already exists (Retry).
async function _testSkill(card, name, force = false) {
  if (!card.classList.contains('doclib-card-expanded')) await _expandSkillCard(card, name);
  const preview = card.querySelector('.skill-card-preview');
  if (!preview) return;
  preview.innerHTML =
    '<div class="skill-test"><div class="skill-test-log"></div>' +
    '<div class="skill-test-verdict"></div></div>';
  const logEl = preview.querySelector('.skill-test-log');
  const verdictEl = preview.querySelector('.skill-test-verdict');
  if (card._testPoll) { clearInterval(card._testPoll); card._testPoll = null; }

  // Attach to an existing job unless forcing a fresh run.
  let job = force ? { status: 'none' } : await _fetchTestStatus(name);

  if (job.status === 'none') {
    logEl.innerHTML = '<div class="skill-test-meta">Starting test…</div>';
    let model = '', endpoint_url = '';
    try {
      const sm = window.sessionModule;
      model = (sm && sm.getCurrentModel && sm.getCurrentModel()) || '';
      endpoint_url = (sm && sm.getCurrentEndpointUrl && sm.getCurrentEndpointUrl()) || '';
    } catch (_) {}
    try {
      const res = await fetch(`${API}/api/skills/${encodeURIComponent(name)}/test`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model, endpoint_url }),
      });
      if (!res.ok) { logEl.innerHTML = '<div class="skill-test-err">Test failed: HTTP ' + res.status + '</div>'; return; }
    } catch (e) { logEl.innerHTML = '<div class="skill-test-err">Test failed: ' + (e.message || e) + '</div>'; return; }
    job = await _fetchTestStatus(name);
  }

  _renderTestLog(logEl, verdictEl, job, card, name);
  _setCardRunning(card, job.status === 'running');

  if (job.status === 'running') {
    card._testPoll = setInterval(async () => {
      // Keep polling even if the card is collapsed (the test runs server-side);
      // only stop once the card itself is gone from the DOM.
      if (!document.body.contains(card)) { clearInterval(card._testPoll); card._testPoll = null; _setCardRunning(card, false); return; }
      const s = await _fetchTestStatus(name);
      // Update the expanded log only while it's still on screen.
      if (document.body.contains(logEl)) _renderTestLog(logEl, verdictEl, s, card, name);
      if (s.status !== 'running') {
        clearInterval(card._testPoll); card._testPoll = null;
        _setCardRunning(card, false);
        // If the log isn't visible (card was collapsed), still update the
        // header dot/% so the result shows on the folded card.
        if (!document.body.contains(logEl) && s.verdict && s.verdict.verdict) {
          _applyVerdictToHeader(card, s.verdict.verdict);
        }
      }
    }, 1300);
  }
}

// Show/hide the app-wide whirlpool spinner next to the skill name while a test
// is in flight. Works on the collapsed header too, since we inject a real DOM
// element rather than a CSS pseudo on a class.
function _setCardRunning(card, on) {
  if (!card) return;
  card.classList.toggle('skill-test-running', !!on);
  if (on) {
    if (card._testSpinner) return;
    const nameEl = card.querySelector('.skill-card-name');
    if (!nameEl) return;
    const wp = spinnerModule.createWhirlpool(12);
    wp.element.style.cssText = 'display:inline-flex;width:12px;height:12px;margin:0 0 0 7px;vertical-align:middle;flex-shrink:0;';
    // Append INSIDE the <code> name (inline-flow), not after it. The textcol
    // is a flex column, so a sibling-after lands on its own line — putting
    // the spinner inside the inline code keeps it on the title row.
    nameEl.appendChild(wp.element);
    card._testSpinner = wp;
  } else if (card._testSpinner) {
    try { card._testSpinner.destroy(); } catch (_) {}
    if (card._testSpinner.element && card._testSpinner.element.parentElement) {
      card._testSpinner.element.remove();
    }
    card._testSpinner = null;
  }
}

// Reflect a test/audit verdict on the (possibly collapsed) card header without
// a full reload: the glowing audit dot, the confidence %, and the pass check.
// Works whether the card is expanded or folded so a test that finishes after
// you collapse still updates the card.
function _applyVerdictToHeader(card, verdict) {
  if (!card || !verdict) return;
  const dotColor = {
    pass: 'var(--color-success, #4ade80)',
    needs_work: 'var(--color-warning, #f0ad4e)',
    inconclusive: 'var(--color-warning, #f0ad4e)',
    fail: 'var(--color-danger, #e06c75)',
  }[verdict];
  // Audit dot removed at user request — strip any pre-existing one so the
  // post-audit live update doesn't leave a stale dot from an old render.
  const header = card.querySelector('.skill-card-header');
  if (header) {
    header.querySelectorAll('.skill-audit-dot').forEach(n => n.remove());
  }
  const newConf = { pass: 95, needs_work: 60, fail: 40 }[verdict];
  const statsEl = card.querySelector('.skill-stats');
  if (statsEl && newConf != null) {
    const confEl = statsEl.querySelector('.skill-conf');
    if (confEl) { confEl.textContent = newConf + '%'; confEl.style.color = _confColor(newConf); }
  }
  // Fold the verdict into the status (draft / published) pill — colour the
  // pill itself and append a tiny check/warn/cross glyph so the audit result
  // lives next to the label instead of dangling in the stats row.
  const pill = card.querySelector('.skill-status-pill');
  if (pill) {
    // Inline glyphs for the per-verdict pill — appear next to the "checked"
    // label so the verdict reads as a real badge.
    const ICON = {
      pass: '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><polyline points="20 6 9 17 4 12"/></svg>',
      needs_work: '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><line x1="12" y1="8" x2="12" y2="13"/><line x1="12" y1="17" x2="12" y2="17"/></svg>',
      inconclusive: '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><line x1="12" y1="8" x2="12" y2="13"/><line x1="12" y1="17" x2="12" y2="17"/></svg>',
      fail: '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    }[verdict];
    // Wash the pill's bg + tint the text so a glance at the badge tells you
    // pass/needs-work/fail without expanding the card.
    const tint = {
      pass:       { bg: 'color-mix(in srgb, var(--color-success, #4ade80) 30%, transparent)', fg: 'var(--color-success, #4ade80)' },
      needs_work: { bg: 'color-mix(in srgb, var(--color-warning, #f0ad4e) 30%, transparent)', fg: 'var(--color-warning, #f0ad4e)' },
      inconclusive: { bg: 'color-mix(in srgb, var(--color-warning, #f0ad4e) 30%, transparent)', fg: 'var(--color-warning, #f0ad4e)' },
      fail:       { bg: 'color-mix(in srgb, var(--color-danger, #e06c75) 30%, transparent)',  fg: 'var(--color-danger, #e06c75)' },
    }[verdict];
    // The status pill (draft / published) keeps its own colours now — the
    // verdict lives in a separate "checked" pill that's inserted next to it.
    // Remove any prior audit glyph (was previously inserted inside the pill;
    // now scrub both the in-pill and sibling locations on every refresh).
    pill.querySelectorAll('.skill-pill-verdict').forEach(n => n.remove());
    if (pill.parentElement) {
      pill.parentElement.querySelectorAll(':scope > .skill-pill-verdict').forEach(n => n.remove());
    }
    if (ICON) {
      // Full "checked" pill badge — sits LEFT of the draft/published pill,
      // styled like the other memory-cat-badges so it reads as a real chip.
      const span = document.createElement('span');
      span.className = 'memory-cat-badge skill-pill-verdict';
      span.title = 'Audited: ' + verdict.replace(/_/g, ' ');
      span.innerHTML = ICON + '<span>checked</span>';
      if (tint) {
        span.style.background = tint.bg;
        span.style.color = tint.fg;
      }
      if (pill.parentElement) {
        pill.parentElement.insertBefore(span, pill);
      } else {
        pill.insertAdjacentElement('beforebegin', span);
      }
    }
  }
  // Old free-floating .skill-verified check (next to confidence %) is no
  // longer added — the pill carries the verdict glyph now. Remove a stale
  // one in case the card was rendered by an earlier build.
  card.querySelectorAll('.skill-verified').forEach(n => n.remove());
}

function _renderTestVerdict(el, v, card, name) {
  if (!el) return;
  const verdict = (v && v.verdict) || 'unknown';
  const cls = { pass: 'ok', needs_work: 'warn', fail: 'bad', inconclusive: 'unknown' }[verdict] || 'unknown';
  const label = { pass: 'PASS', needs_work: 'NEEDS WORK', fail: 'FAIL', inconclusive: 'INCONCLUSIVE', unknown: 'UNCLEAR' }[verdict] || 'UNCLEAR';
  const conf = v && typeof v.confidence === 'number' ? Math.round(v.confidence * 100) + '%' : '';
  const issues = Array.isArray(v && v.issues) ? v.issues : [];
  // Reflect the skill's current state: if it's already published, the button
  // confirms "Approved" (click to unpublish) rather than offering to approve.
  const isPub = card && card.dataset && card.dataset.skillStatus === 'published';
  const approveLabel = isPub ? 'Approved' : 'Approve';
  const approveCls = 'skill-eval-approve' + (isPub ? ' is-approved' : (verdict === 'pass' ? ' suggested' : ''));
  const approveTitle = isPub ? 'Already approved — click to unpublish' : 'Publish — appears in the skills index';
  el.innerHTML =
    '<div class="skill-eval-head"><span class="skill-eval-badge skill-eval-' + cls + '">' + label + (conf ? ' · ' + conf : '') + '</span>' +
    '<span class="skill-eval-summary">' + esc((v && v.summary) || '') + '</span></div>' +
    (issues.length ? '<ul class="skill-eval-issues">' + issues.map(i => '<li>' + esc(i) + '</li>').join('') + '</ul>' : '') +
    '<div class="doclib-card-expanded-actions skill-eval-actions-wrap">' +
      '<button class="doclib-card-text-btn doclib-card-action-btn ' + approveCls + '" data-act="approve" title="' + approveTitle + '">' + approveLabel + '</button>' +
      '<div class="doclib-action-group"><div class="doclib-action-btn-row">' +
        '<button class="doclib-card-text-btn doclib-card-action-btn" data-act="retry" title="Run the test again">Retry</button>' +
        '<button class="doclib-card-text-btn doclib-card-action-btn" data-act="copy" title="Copy the run output + verdict">Copy</button>' +
        '<button class="doclib-card-text-btn doclib-card-action-btn" data-act="edit">Edit</button>' +
        '<button class="doclib-card-text-btn doclib-card-action-btn doclib-card-text-btn-danger" data-act="del"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>Delete</button>' +
      '</div></div>' +
    '</div>';
  _applyVerdictToHeader(card, verdict);
  el.querySelector('[data-act="approve"]')?.addEventListener('click', async (e) => {
    e.stopPropagation();
    const nowPub = card.dataset.skillStatus === 'published';
    await _setSkillStatus(name, nowPub ? 'draft' : 'published');
    // _setSkillStatus reloads the list, but if this card survives, relabel it.
    card.dataset.skillStatus = nowPub ? 'draft' : 'published';
    const btn = el.querySelector('[data-act="approve"]');
    if (btn) {
      const pub = card.dataset.skillStatus === 'published';
      btn.textContent = pub ? 'Approved' : 'Approve';
      btn.title = pub ? 'Already approved — click to unpublish' : 'Publish — appears in the skills index';
      btn.classList.toggle('is-approved', pub);
      btn.classList.toggle('suggested', !pub && verdict === 'pass');
    }
  });
  el.querySelector('[data-act="del"]')?.addEventListener('click', (e) => { e.stopPropagation(); _deleteSkill(name, card); });
  el.querySelector('[data-act="edit"]')?.addEventListener('click', (e) => { e.stopPropagation(); _toggleSkillEdit(card, name); });
  el.querySelector('[data-act="retry"]')?.addEventListener('click', (e) => { e.stopPropagation(); _testSkill(card, name, true); });
  el.querySelector('[data-act="copy"]')?.addEventListener('click', (e) => {
    e.stopPropagation();
    const logEl = card.querySelector('.skill-test-log');
    const issuesTxt = issues.length ? '\nIssues:\n- ' + issues.join('\n- ') : '';
    const text = (logEl ? logEl.innerText.trim() + '\n\n' : '') +
      '=== Eval: ' + label + (conf ? ' (' + conf + ')' : '') + ' ===\n' + ((v && v.summary) || '') + issuesTxt;
    // Shared helper falls back to execCommand on plain HTTP (navigator.clipboard
    // is unavailable in non-secure contexts, which is why the raw call failed).
    uiModule.copyToClipboard(text);
  });
}

// ---- Audit all skills (autonomous: test → fix → retry → teacher → flag) ----

let _auditPoll = null;
let _auditSeenResults = 0;

function _confirmAuditSkills(label) {
  return new Promise(resolve => {
    let overlay = document.getElementById('skills-audit-confirm-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'skills-audit-confirm-overlay';
      overlay.className = 'modal';
      overlay.innerHTML =
        '<div class="modal-content styled-confirm-box">' +
          '<div class="modal-header"><h4>Audit Skills</h4></div>' +
          '<div class="modal-body">' +
            '<p id="skills-audit-confirm-msg"></p>' +
            '<label class="memory-bulk-check-all" style="margin-top:10px;display:inline-flex;align-items:center;gap:7px;">' +
              '<input type="checkbox" id="skills-audit-skip-audited" checked />' +
              '<span>Skip already audited</span>' +
            '</label>' +
          '</div>' +
          '<div class="modal-footer">' +
            '<button id="skills-audit-confirm-cancel" class="confirm-btn confirm-btn-secondary">Cancel</button>' +
            '<button id="skills-audit-confirm-ok" class="confirm-btn confirm-btn-primary">Audit</button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(overlay);
    }

    const msg = overlay.querySelector('#skills-audit-confirm-msg');
    const skip = overlay.querySelector('#skills-audit-skip-audited');
    const okBtn = overlay.querySelector('#skills-audit-confirm-ok');
    const cancelBtn = overlay.querySelector('#skills-audit-confirm-cancel');
    msg.textContent = `Audit ${label}? Each is tested from top to bottom, then published or moved to draft using your auto-approve confidence threshold.`;
    skip.checked = true;
    overlay.classList.remove('hidden');
    overlay.style.display = '';

    function cleanup(result) {
      overlay.classList.add('hidden');
      overlay.style.display = 'none';
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      overlay.removeEventListener('click', onBackdrop);
      document.removeEventListener('keydown', onKey);
      resolve(result);
    }
    function onOk() { cleanup({ ok: true, skipAudited: !!skip.checked }); }
    function onCancel() { cleanup({ ok: false, skipAudited: false }); }
    function onBackdrop(e) { if (e.target === overlay) onCancel(); }
    function onKey(e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        cleanup({ ok: false, skipAudited: false });
      }
    }

    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
    overlay.addEventListener('click', onBackdrop);
    document.addEventListener('keydown', onKey);
    okBtn.focus();
  });
}

async function _auditAllSkills(opts = {}) {
  const panel = document.getElementById('skills-audit-panel');
  if (!panel) return;
  // If a run is already going, just (re)attach to it.
  let st = await _fetchAuditStatus();
  if (st.status !== 'running') {
    const explicitNames = Array.isArray(opts.names) ? opts.names.filter(Boolean) : null;
    const visibleNames = _getFilteredSkills()
      .map(sk => sk.name || sk.id)
      .filter(Boolean);
    const names = explicitNames || visibleNames;
    const label = explicitNames
      ? `${names.length} selected ${names.length === 1 ? 'skill' : 'skills'}`
      : `${names.length} visible ${names.length === 1 ? 'skill' : 'skills'}`;
    if (!names.length) {
      uiModule.showToast(explicitNames ? 'No selected skills to audit' : 'No visible skills to audit');
      return;
    }
    const confirmed = await _confirmAuditSkills(label);
    if (!confirmed.ok) return;
    try {
      const r = await fetch(`${API}/api/skills/audit-all`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope: explicitNames ? 'selected' : 'all', names, skip_audited: confirmed.skipAudited }),
      });
      if (!r.ok) { uiModule.showError('Audit failed to start (HTTP ' + r.status + ')'); return; }
      st = await _fetchAuditStatus();
    } catch (e) { uiModule.showError('Audit failed: ' + (e.message || e)); return; }
    _auditSeenResults = 0;
  }
  panel.classList.remove('hidden');
  _auditSeenResults = Math.min(_auditSeenResults, (st.results || []).length);
  _renderAuditPanel(panel, st);
  _applyAuditResults(st);
  _highlightAuditCard(st.status === 'running' ? st.current : null);
  if (_auditPoll) clearInterval(_auditPoll);
  if (st.status === 'running') {
    _auditPoll = setInterval(async () => {
      const s = await _fetchAuditStatus();
      _renderAuditPanel(panel, s);
      _applyAuditResults(s);
      _highlightAuditCard(s.status === 'running' ? s.current : null);
      if (s.status !== 'running') {
        clearInterval(_auditPoll); _auditPoll = null;
        _highlightAuditCard(null);
        loadSkills();  // refresh statuses (some may have been demoted/edited)
      }
    }, 1500);
  }
}

function _findSkillCard(name) {
  if (!name) return null;
  return [...document.querySelectorAll('.skill-card[data-skill-name]')]
    .find(c => c.dataset.skillName === name) || null;
}

function _mergeSkillState(state) {
  if (!state || !state.name) return;
  const idx = skills.findIndex(s => (s.name || s.id) === state.name);
  if (idx >= 0) skills[idx] = { ...skills[idx], ...state };
}

function _applySkillStateToHeader(card, state, fallbackVerdict) {
  if (!card) return;
  const verdict = state?.audit_verdict || fallbackVerdict;
  if (verdict) _applyVerdictToHeader(card, verdict);
  if (state && typeof state.confidence === 'number') {
    const conf = Math.round(state.confidence * 100);
    const confEl = card.querySelector('.skill-conf');
    if (confEl) { confEl.textContent = conf + '%'; confEl.style.color = _confColor(conf); }
  }
  if (state?.status) {
    card.dataset.skillStatus = state.status;
    const oldPill = card.querySelector('.skill-status-pill');
    if (oldPill) {
      const wrap = document.createElement('span');
      wrap.innerHTML = _statusPill(state);
      const next = wrap.firstElementChild;
      if (next) oldPill.replaceWith(next);
    }
  }
  const right = card.querySelector('.skill-card-right');
  if (right && state) {
    right.querySelectorAll('.skill-model-pill, .skill-necessity-pill').forEach(n => n.remove());
    const stats = right.querySelector('.skill-stats');
    const wrap = document.createElement('span');
    wrap.innerHTML = _auditModelPills(state) + _necessityPill(state);
    [...wrap.children].forEach(p => {
      if (stats) right.insertBefore(p, stats);
      else right.appendChild(p);
    });
  }
}

function _applyAuditResults(st) {
  const results = st && Array.isArray(st.results) ? st.results : [];
  if (!results.length) return;
  for (const r of results.slice(_auditSeenResults)) {
    const name = r && r.skill;
    if (!name) continue;
    const state = r.skill_state || null;
    _mergeSkillState(state);
    const verdict = state?.audit_verdict || r.verdict?.verdict || (r.result === 'flagged' ? 'fail' : null);
    _applySkillStateToHeader(_findSkillCard(name), state, verdict);
  }
  _auditSeenResults = results.length;
}

// Make the card currently being audited glow, so it's obvious which one the
// "Audit now" run is processing. Pass null to clear all highlights.
function _highlightAuditCard(name) {
  document.querySelectorAll('.skill-card.skill-audit-active')
    .forEach(c => { c.classList.remove('skill-audit-active'); _setCardRunning(c, false); });
  if (!name) return;
  const card = _findSkillCard(name);
  if (card) {
    card.classList.add('skill-audit-active');
    _setCardRunning(card, true);
    card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
}

async function _fetchAuditStatus() {
  try {
    const r = await fetch(`${API}/api/skills/audit-all/status`);
    return r.ok ? await r.json() : { status: 'none' };
  } catch { return { status: 'none' }; }
}

function _renderAuditPanel(panel, st) {
  if (st.status === 'none') { panel.classList.add('hidden'); panel.innerHTML = ''; return; }
  const done = st.done || 0, total = st.total || 0;
  const pct = total ? Math.round((done / total) * 100) : 0;
  const counts = {};
  for (const r of (st.results || [])) counts[r.result] = (counts[r.result] || 0) + 1;
  const summary = Object.entries(counts).map(([k, v]) => v + ' ' + k.replace(/_/g, ' ')).join(' · ');
  const running = st.status === 'running';
  const cancelled = st.status === 'cancelled';
  const head = running
    ? `Auditing ${done}/${total}${st.current ? ' — ' + esc(st.current) : ''}`
    : cancelled
      ? `Audit cancelled — ${done}/${total}`
    : `Audit complete — ${total} skill${total === 1 ? '' : 's'}`;
  panel.innerHTML =
    '<div class="skills-audit-head">' +
      '<span class="skills-audit-title-wrap" style="display:inline-flex;align-items:center;gap:8px;">' +
        '<span class="skills-audit-title">' + head + '</span>' +
      '</span>' +
      (running
        ? '<button class="memory-toolbar-btn" data-act="audit-cancel">Cancel</button>'
        : '<button class="memory-toolbar-btn" data-act="audit-close">Close</button>') +
    '</div>' +
    '<div class="skills-audit-bar"><div class="skills-audit-fill" style="width:' + pct + '%"></div></div>' +
    (summary ? '<div class="skills-audit-summary">' + esc(summary) + (st.teacher ? ' · teacher: ' + esc(st.teacher) : '') + '</div>' : '') +
    '<div class="skills-audit-log">' + (st.log || []).slice(-40).map(l => '<div>' + esc(l) + '</div>').join('') + '</div>';
  // Whirlpool sits next to the title while the audit is actually running.
  if (running) {
    const titleWrap = panel.querySelector('.skills-audit-title-wrap');
    if (titleWrap) {
      const wp = spinnerModule.createWhirlpool(12);
      wp.element.style.cssText = 'display:inline-flex;width:12px;height:12px;margin:0;vertical-align:middle;flex-shrink:0;';
      titleWrap.appendChild(wp.element);
    }
  }
  const cancel = panel.querySelector('[data-act="audit-cancel"]');
  if (cancel) cancel.addEventListener('click', async (e) => {
    e.stopPropagation();
    cancel.disabled = true;
    cancel.textContent = 'Cancelling...';
    try {
      await fetch(`${API}/api/skills/audit-all/cancel`, { method: 'POST', credentials: 'same-origin' });
      const s = await _fetchAuditStatus();
      _renderAuditPanel(panel, { ...s, status: s.status === 'none' ? 'cancelled' : s.status });
      _highlightAuditCard(null);
    } catch {
      cancel.disabled = false;
      cancel.textContent = 'Cancel';
    }
  });
  const close = panel.querySelector('[data-act="audit-close"]');
  if (close) close.addEventListener('click', () => { panel.classList.add('hidden'); panel.innerHTML = ''; });
  const logEl = panel.querySelector('.skills-audit-log');
  if (logEl) logEl.scrollTop = logEl.scrollHeight;
}

// ---- Select mode / bulk actions ----

function _enterSelectMode() {
  _selectMode = true;
  _selectedNames.clear();
  const bar = document.getElementById('skills-bulk-bar');
  const btn = document.getElementById('skills-select-btn');
  if (bar) bar.classList.remove('hidden');
  if (btn) { btn.classList.add('active'); btn.textContent = 'Cancel'; }
  _updateBulkBar();
  renderSkillsList();
}

function _exitSelectMode() {
  _selectMode = false;
  _selectedNames.clear();
  const bar = document.getElementById('skills-bulk-bar');
  const btn = document.getElementById('skills-select-btn');
  const all = document.getElementById('skills-select-all');
  if (bar) bar.classList.add('hidden');
  if (btn) { btn.classList.remove('active'); btn.textContent = 'Select'; }
  if (all) all.checked = false;
  renderSkillsList();
}

function _updateBulkBar() {
  const countEl = document.getElementById('skills-selected-count');
  const delBtn = document.getElementById('skills-bulk-delete');
  const delNonPassingBtn = document.getElementById('skills-bulk-delete-nonpassing');
  const pubBtn = document.getElementById('skills-bulk-publish');
  const auditBtn = document.getElementById('skills-bulk-audit');
  if (countEl) countEl.textContent = `${_selectedNames.size} Selected`;
  if (delBtn) delBtn.disabled = _selectedNames.size === 0;
  if (auditBtn) auditBtn.disabled = _selectedNames.size === 0;
  if (delNonPassingBtn) {
    const count = _selectedNonPassingSkills().length;
    delNonPassingBtn.disabled = count === 0;
    delNonPassingBtn.title = count
      ? `Delete ${count} selected non-passing ${count === 1 ? 'skill' : 'skills'}`
      : 'No selected non-passing skills';
  }
  // Approve is only meaningful when at least one selected skill is still a draft.
  const anyDraft = [..._selectedNames].some(n => {
    const sk = skills.find(s => (s.name || s.id) === n);
    return sk && (sk.status || 'draft') !== 'published';
  });
  if (pubBtn) pubBtn.disabled = !anyDraft;
}

function _toggleSelectAll() {
  const all = document.getElementById('skills-select-all');
  if (!all) return;
  const visible = _getFilteredSkills().map(s => s.name || s.id);
  if (all.checked) visible.forEach(n => _selectedNames.add(n));
  else visible.forEach(n => _selectedNames.delete(n));
  _updateBulkBar();
  renderSkillsList();
}

async function _bulkDelete() {
  if (!_selectedNames.size) return;
  const n = _selectedNames.size;
  const ok = await uiModule.styledConfirm(
    `Delete ${n} ${n === 1 ? 'skill' : 'skills'}? This removes their SKILL.md files.`,
    { confirmText: 'Delete', danger: true }
  );
  if (!ok) return;
  let deleted = 0;
  const deletedNames = [];
  for (const name of _selectedNames) {
    try {
      const res = await fetch(`${API}/api/skills/${encodeURIComponent(name)}`, { method: 'DELETE' });
      if (res.ok) {
        deleted++;
        deletedNames.push(name);
      }
    } catch {}
  }
  for (const name of deletedNames) {
    const card = document.querySelector(`.skill-card[data-skill-name="${CSS.escape(name)}"]`);
    if (card) card.classList.add('doclib-card-deleting');
  }
  if (deletedNames.length) await new Promise(resolve => setTimeout(resolve, 320));
  _exitSelectMode();
  await loadSkills();
  uiModule.showToast(`Deleted ${deleted}`);
}

async function _loadSkillApprovalThreshold() {
  try {
    const res = await fetch(`${API}/api/prefs`, { credentials: 'same-origin' });
    if (!res.ok) return;
    const prefs = await res.json();
    const raw = prefs.skill_min_confidence ?? prefs.skill_autosave_min_confidence;
    const val = Number(raw);
    if (Number.isFinite(val)) _skillApprovalThreshold = Math.max(0, Math.min(1, val));
  } catch {}
}

function _selectedNonPassingSkills() {
  const selected = new Set(_selectedNames);
  return skills.filter(sk => {
    const name = sk.name || sk.id;
    if (!selected.has(name)) return false;
    const conf = Number(sk.confidence || 0);
    const necessity = _necessityKind(sk);
    if (necessity === 'duplicate' || necessity === 'trivial' || necessity === 'irrelevant') return true;
    if ((sk.audit_verdict || '') !== 'pass') return true;
    return conf < _skillApprovalThreshold;
  });
}

async function _bulkDeleteNonPassing() {
  const targets = _selectedNonPassingSkills();
  if (!targets.length) {
    uiModule.showToast('No selected non-passing skills');
    return;
  }
  const thresholdPct = Math.round(_skillApprovalThreshold * 100);
  const names = targets.map(sk => sk.name || sk.id).filter(Boolean);
  const ok = await uiModule.styledConfirm(
    `Delete ${names.length} selected non-passing ${names.length === 1 ? 'skill' : 'skills'}? This removes duplicates, generic/irrelevant skills, failed audits, and anything below ${thresholdPct}%.`,
    { confirmText: 'Delete non passing', danger: true }
  );
  if (!ok) return;
  let deleted = 0;
  const deletedNames = [];
  for (const name of names) {
    try {
      const res = await fetch(`${API}/api/skills/${encodeURIComponent(name)}`, { method: 'DELETE' });
      if (res.ok) {
        deleted++;
        deletedNames.push(name);
        _mdCache.delete(name);
      }
    } catch {}
  }
  for (const name of deletedNames) {
    const card = document.querySelector(`.skill-card[data-skill-name="${CSS.escape(name)}"]`);
    if (card) card.classList.add('doclib-card-deleting');
  }
  if (deletedNames.length) await new Promise(resolve => setTimeout(resolve, 320));
  _exitSelectMode();
  await loadSkills();
  uiModule.showToast(`Deleted ${deleted} non-passing`);
}

async function _bulkApprove() {
  if (!_selectedNames.size) return;
  let published = 0;
  for (const name of _selectedNames) {
    const sk = skills.find(s => (s.name || s.id) === name);
    if (sk && sk.status === 'published') continue;
    try {
      const res = await fetch(`${API}/api/skills/${encodeURIComponent(name)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'published' }),
      });
      if (res.ok) published++;
    } catch {}
  }
  _exitSelectMode();
  await loadSkills();
  uiModule.showToast(`Published ${published}`);
}

async function _bulkAudit() {
  if (!_selectedNames.size) return;
  const selected = new Set(_selectedNames);
  const ordered = _getFilteredSkills()
    .map(sk => sk.name || sk.id)
    .filter(n => selected.has(n));
  _exitSelectMode();
  await _auditAllSkills({ names: ordered });
}

async function _showSkillSource(name) {
  let md = '';
  try {
    const res = await fetch(`${API}/api/skills/${encodeURIComponent(name)}/markdown`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    md = data.markdown || '';
  } catch (e) {
    uiModule.showError('Failed to load SKILL.md');
    return;
  }

  // Lightweight modal — reuses the .modal CSS the rest of the app uses.
  const wrap = document.createElement('div');
  wrap.className = 'modal';
  wrap.style.display = 'block';
  wrap.innerHTML = `
    <div class="modal-content" style="max-width:760px;max-height:85vh;display:flex;flex-direction:column">
      <div class="modal-header">
        <h4>SKILL.md — <code>${esc(name)}</code></h4>
        <span style="flex:1"></span>
        <button class="memory-toolbar-btn" id="skill-save-btn">Save</button>
        <button class="close-btn" id="skill-md-close">✖</button>
      </div>
      <div class="modal-body" style="display:flex;flex-direction:column;gap:8px">
        <textarea id="skill-md-textarea" spellcheck="false" style="flex:1;min-height:50vh;width:100%;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;padding:10px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--fg);box-sizing:border-box"></textarea>
        <p class="memory-desc" style="margin:0">Edit the frontmatter and body directly. Save replaces the file via PUT /api/skills/{name}.</p>
      </div>
    </div>
  `;
  document.body.appendChild(wrap);
  const ta = wrap.querySelector('#skill-md-textarea');
  ta.value = md;
  wrap.querySelector('#skill-md-close').addEventListener('click', () => wrap.remove());
  wrap.addEventListener('click', (e) => { if (e.target === wrap) wrap.remove(); });
  wrap.querySelector('#skill-save-btn').addEventListener('click', async () => {
    try {
      // We use the manage_skills-style edit by going through PUT with a
      // single 'content' field. The route doesn't accept that yet — use the
      // tool call instead. We have a /api/skills/{name} PUT for fields, but
      // a full SKILL.md replace is simpler via the parsed-then-PUT approach
      // below: parse client-side by uploading via the tool route.
      const res = await fetch(`${API}/api/skills/${encodeURIComponent(name)}/markdown`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ markdown: ta.value }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      uiModule.showToast('Saved');
      wrap.remove();
      await loadSkills();
    } catch (e) {
      uiModule.showError('Save failed: ' + e.message);
    }
  });
}

async function importSkillFromUrl() {
  const input = document.getElementById('skill-import-url');
  const url = (input?.value || '').trim();
  if (!url) {
    uiModule.showError('Paste a GitHub or skills.sh URL first');
    return;
  }
  const btn = document.getElementById('skill-import-url-btn');
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(`${API}/api/skills/import-from-url`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
    if (input) input.value = '';
    await loadSkills();
    const name = data.skill?.name || 'skill';
    uiModule.showToast(`Imported ${name} (${data.files || 1} file(s))`);
    if (name) openSkill(name);
  } catch (err) {
    uiModule.showError('Import failed: ' + err.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function addSkill() {
  const name = document.getElementById('new-skill-name')?.value.trim()
    || document.getElementById('new-skill-title')?.value.trim();
  const description = document.getElementById('new-skill-description')?.value.trim()
    || document.getElementById('new-skill-title')?.value.trim();
  const whenToUse = document.getElementById('new-skill-when')?.value.trim()
    || document.getElementById('new-skill-problem')?.value.trim() || '';
  const procedureRaw = document.getElementById('new-skill-procedure')?.value.trim()
    || document.getElementById('new-skill-solution')?.value.trim() || '';
  const tagsRaw = document.getElementById('new-skill-tags')?.value.trim();
  const category = document.getElementById('new-skill-category')?.value.trim() || 'general';

  if (!description && !name) {
    uiModule.showError('Description (or name) is required');
    return;
  }
  const procedure = procedureRaw
    ? procedureRaw.split('\n').map(s => s.replace(/^\s*(?:[-*]|\d+[.)])\s+/, '').trim()).filter(Boolean)
    : [];
  const tags = tagsRaw ? tagsRaw.split(',').map(t => t.trim()).filter(Boolean) : [];

  try {
    const res = await fetch(`${API}/api/skills/add`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: name || undefined,
        description,
        category,
        when_to_use: whenToUse,
        procedure,
        tags,
        status: 'draft',
      }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    ['new-skill-name', 'new-skill-title', 'new-skill-description', 'new-skill-when',
     'new-skill-problem', 'new-skill-procedure', 'new-skill-solution', 'new-skill-tags',
     'new-skill-category']
      .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    await loadSkills();
    uiModule.showToast('Skill added (draft)');
  } catch (err) {
    uiModule.showError('Failed to add skill: ' + err.message);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('skill-import-url-btn')?.addEventListener('click', importSkillFromUrl);
  document.getElementById('skill-import-url')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') importSkillFromUrl();
  });
  document.getElementById('add-skill-btn')?.addEventListener('click', addSkill);
  document.getElementById('skills-search')?.addEventListener('input', renderSkillsList);
  document.getElementById('skills-sort')?.addEventListener('change', (e) => {
    // Dropdown holds two optgroups: Sort (sort:<key>) and Filter (filter:<key>).
    // Picking a sort option leaves the filter alone, and vice-versa.
    const v = e.target.value || '';
    if (v.startsWith('sort:')) {
      _skillsSort = v.slice(5);
    } else if (v.startsWith('filter:')) {
      const f = v.slice(7);
      if (f === 'all') { _showDraftsOnly = false; _showPublishedOnly = false; _confMax = null; }
      else if (f === 'drafts') { _showDraftsOnly = true; _showPublishedOnly = false; _confMax = null; }
      else if (f === 'published') { _showPublishedOnly = true; _showDraftsOnly = false; _confMax = null; }
      else if (f.startsWith('conf')) { _showDraftsOnly = false; _showPublishedOnly = false; _confMax = parseInt(f.slice(4), 10) || null; }
    }
    renderSkillsList();
  });
  document.getElementById('skills-select-btn')?.addEventListener('click', () => {
    if (_selectMode) _exitSelectMode(); else _enterSelectMode();
  });
  document.getElementById('skills-audit-btn')?.addEventListener('click', _auditAllSkills);
  document.getElementById('skills-select-all')?.addEventListener('change', _toggleSelectAll);
  document.getElementById('skills-bulk-cancel')?.addEventListener('click', _exitSelectMode);
  document.getElementById('skills-bulk-audit')?.addEventListener('click', _bulkAudit);
  document.getElementById('skills-bulk-delete')?.addEventListener('click', _bulkDelete);
  document.getElementById('skills-bulk-delete-nonpassing')?.addEventListener('click', _bulkDeleteNonPassing);
  document.getElementById('skills-bulk-publish')?.addEventListener('click', _bulkApprove);
  document.getElementById('new-skill-title')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') addSkill();
  });
  document.getElementById('new-skill-name')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') addSkill();
  });
});

export default { loadSkills, openSkill };

// Populate the Skills badge on first load so the count is right before the
// user clicks into the tab. Cheap fetch — same as the lazy path.
document.addEventListener('DOMContentLoaded', () => { loadSkills(); });
