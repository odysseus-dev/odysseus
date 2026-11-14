const API = '/api/projects-dashboard';
const STATE_LABEL = { working: 'Working', waiting: 'Waiting for you', blocked: 'Blocked', completed: 'Completed', idle: 'Idle', interrupted: 'Interrupted', unknown: 'Unknown' };
const ATTENTION = ['waiting', 'blocked', 'interrupted'];
const FILTERS = [['all', 'All'], ['attention', 'Needs attention'], ['granted', 'File-tool write access'], ['git', 'Git']];
const TABS = [['overview', 'Overview'], ['sessions', 'Sessions'], ['git', 'Git'], ['wiki', 'Wiki'], ['access', 'Access']];

const $ = id => document.getElementById(id);
const store = { overview: null, detail: null, filter: 'all', query: '', selected: null, tab: 'overview', error: null, loading: false };

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}

function ago(ts) {
  if (!ts) return 'never';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return 'just now';
  if (s < 5400) return Math.round(s / 60) + ' min ago';
  if (s < 129600) return Math.round(s / 3600) + ' h ago';
  return Math.round(s / 86400) + ' d ago';
}

function toast(text) {
  const t = $('toast');
  t.textContent = text;
  t.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove('show'), 1800);
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast('Copied'); } catch (_) { toast('Copy failed: select the text manually'); }
}

async function api(path, payload) {
  const response = await fetch(API + path, { credentials: 'same-origin', signal: AbortSignal.timeout(20000),
    ...(payload ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) } : {}) });
  let body = null;
  try { body = await response.json(); } catch (_) { /* non-JSON error page */ }
  if (!response.ok) {
    const error = new Error(body?.detail || 'Request failed (' + response.status + ')');
    error.status = response.status;
    throw error;
  }
  return body;
}

function stateBadge(state, evidence) {
  return h('span', { class: 'state', title: evidence || '' }, h('span', { class: 'dot ' + state }), STATE_LABEL[state] || state);
}

function permissionChip(permission) {
  if (!permission) return null;
  if (permission.state === 'granted') return h('span', { class: 'chip ok', title: permission.reason }, permission.partial ? 'File-tool write access (some folders)' : 'File-tool write access');
  if (permission.state === 'blocked') return h('span', { class: 'chip bad', title: permission.reason }, 'Blocked folder');
  return h('span', { class: 'chip', title: permission.reason }, 'No file-tool write grant');
}

function visibleProjects() {
  const q = store.query.trim().toLowerCase();
  return (store.overview?.projects || []).filter(p => {
    if (store.filter === 'attention' && !(p.sessions.attention || p.wiki?.open_handoffs)) return false;
    if (store.filter === 'granted' && p.permission.state !== 'granted') return false;
    if (store.filter === 'git' && p.kind !== 'git') return false;
    if (!q) return true;
    return [p.name, p.path, p.branch, ...p.sources, ...p.paths].some(x => x && String(x).toLowerCase().includes(q));
  });
}

// The grant exchange needs a Referer from this origin, and Odysseus sends Referrer-Policy: no-referrer on every page, so this one link
// opts in to sending the origin (never the path) to the page it points at: the same pattern as the Odysseus mode selector.
function follow(href) {
  if (cameFromHermes()) { location.replace(href); return; }   // same tab: this page leaves the history, so Back from Hermes returns to where the user was
  const link = document.createElement('a');
  link.href = href;
  link.target = '_blank';
  link.referrerPolicy = 'origin';
  link.rel = 'noopener';
  document.body.append(link);
  link.click();
  link.remove();
}

// Conversation to return to: ?from=<id> (same id syntax the server accepts for resume), kept for the tab so a reload keeps it. Anything else is dropped;
// the destination is never a URL, only an id handed to the server's own launch ticket, which validates it again.
const RETURN_ID = /^[A-Za-z0-9._-]{1,200}$/;
function returnConversation() {
  const given = new URLSearchParams(location.search).get('from');
  if (given !== null) {
    try { RETURN_ID.test(given) ? sessionStorage.setItem('projects-return', given) : sessionStorage.removeItem('projects-return'); } catch (e) { /* storage unavailable */ }
  }
  try { const kept = sessionStorage.getItem('projects-return'); return kept && RETURN_ID.test(kept) ? kept : ''; } catch (e) { return ''; }
}

// ?via=hermes is added by the HermesGUI sidebar entry. It only decides new tab or same tab for links that are this page's own launch exchange.
function cameFromHermes() {
  try {
    if (new URLSearchParams(location.search).get('via') === 'hermes') sessionStorage.setItem('projects-via', 'hermes');
    return sessionStorage.getItem('projects-via') === 'hermes';
  } catch (e) { return false; }
}

async function openProjects(resume) {
  try {
    const response = await fetch('/api/application-modes/projects/launch' + (resume ? '?resume=' + encodeURIComponent(resume) : ''), { method: 'POST', credentials: 'same-origin' });
    const body = await response.json().catch(() => ({}));
    if (!response.ok || !body.url) throw new Error(body.detail || 'Projects could not be opened (' + response.status + ')');
    const url = new URL(body.url);
    if (url.protocol !== 'http:' || url.hostname !== 'hermes.localhost' || url.port !== location.port || url.pathname !== '/__odysseus/enter' || !url.search.startsWith('?ticket=') || url.hash || url.username || url.password) {
      throw new Error('Projects returned an unexpected address; nothing was opened');
    }
    follow(url.href);
    toast(resume ? 'Opening the conversation in Hermes. If it opens on the start page, choose it in the sidebar.' : 'Opening Hermes');
  } catch (error) { toast(error.message); }
}

function wireHermesButton() {
  const button = $('toHermes');
  if (!button) return;
  const id = returnConversation();
  const where = cameFromHermes() ? 'in this tab' : 'in a new tab';
  button.title = id ? 'Return to the Hermes conversation you came from (' + where + ', signed in)' : 'Open HermesGUI (' + where + ', signed in)';
  button.onclick = () => openProjects(id);
}

function renderEngines() {
  const o = store.overview;
  $('engines').replaceChildren(...(o ? [
    h('span', { title: 'Managed Hermes gateway (Webapp), from its heartbeat record' }, 'Gateway: ' + (o.gateway_live ? 'running' : 'stopped')),
    h('span', { title: 'Legacy Hermes WebUI port' }, 'WebUI: ' + (o.webui_live ? 'running' : 'stopped')),
  ] : []));
}

function renderFilters() {
  $('filters').replaceChildren(...FILTERS.map(([id, label]) => h('button', { type: 'button', 'aria-pressed': String(store.filter === id), onclick: () => { store.filter = id; renderList(); renderFilters(); } }, label)));
}

function renderList() {
  const list = $('list');
  if (store.error && !store.overview) { list.replaceChildren(); return; }
  const projects = visibleProjects();
  const nodes = [];
  nodes.push(h('div', { class: 'group' }, projects.length + ' project' + (projects.length === 1 ? '' : 's')));
  for (const p of projects) {
    const worst = ATTENTION.find(s => p.sessions.states[s]) || (p.sessions.states.working ? 'working' : null);
    nodes.push(h('div', { class: 'row', role: 'option', tabindex: '0', 'data-id': p.id, 'aria-selected': String(store.selected === p.id), onclick: () => select(p.id), onkeydown: e => { if (e.key === 'Enter') select(p.id); } },
      h('span', { class: 'dot ' + (worst || (p.sessions.total ? 'idle' : 'none')), title: worst ? STATE_LABEL[worst] : 'No agent activity recorded' }),
      h('div', { class: 'body' },
        h('div', { class: 'name' }, p.name),
        h('div', { class: 'meta' }, p.kind === 'git' ? (p.branch || 'detached HEAD') : p.kind === 'missing' ? 'folder not found' : 'folder (not a Git repository)'),
        h('div', { class: 'chips' },
          p.sessions.total ? h('span', { class: 'chip' }, p.sessions.total + ' session' + (p.sessions.total === 1 ? '' : 's')) : null,
          p.sessions.attention ? h('span', { class: 'chip warn' }, p.sessions.attention + ' need attention') : null,
          p.wiki?.open_handoffs ? h('span', { class: 'chip info' }, p.wiki.open_handoffs + ' handoff' + (p.wiki.open_handoffs === 1 ? '' : 's')) : null,
          p.permission.state === 'granted' ? h('span', { class: 'chip ok' }, 'write') : null,
          p.permission.state === 'blocked' ? h('span', { class: 'chip bad' }, 'blocked') : null))));
  }
  if (!projects.length) nodes.push(h('p', { class: 'empty', style: 'padding:12px' }, store.overview?.projects?.length ? 'No project matches the filter.' : 'No projects found in the WebUI, Webapp, write grants or Wiki.'));
  const s = store.overview?.sessions;
  if (s) {
    nodes.push(h('div', { class: 'group' }, 'Other'));
    nodes.push(h('div', { class: 'row', role: 'option', tabindex: '0', 'data-id': '_sessions', 'aria-selected': String(store.selected === '_sessions'), onclick: () => select('_sessions'), onkeydown: e => { if (e.key === 'Enter') select('_sessions'); } },
      h('span', { class: 'dot none' }), h('div', { class: 'body' }, h('div', { class: 'name' }, 'All conversations'), h('div', { class: 'meta' }, s.total + ' total, ' + s.unplaced + ' not tied to a project folder'))));
  }
  list.replaceChildren(...nodes);
  list.setAttribute('role', 'listbox');
}

function sessionRow(s, showProject) {
  const webapp = s.resume_kind === 'webapp';
  const link = s.resume_url
    ? h('a', { href: s.resume_url, target: '_blank', rel: 'noopener', onclick: webapp ? event => { event.preventDefault(); openProjects(s.id); } : null, title: s.resume_kind === 'legacy-webui' ? 'Opens in the legacy WebUI on its own port (temporary route: must be running and signed in)' : 'Signs this browser in to the Projects Webapp and opens the conversation there' }, s.resume_kind === 'legacy-webui' ? 'Resume (legacy)' : 'Resume')
    : null;
  return h('tr', null,
    h('td', null, stateBadge(s.state, s.evidence)),
    h('td', null, h('div', null, (s.pinned ? '\u2605 ' : '') + s.title), h('div', { class: 'evidence' }, s.evidence)),
    h('td', null, s.engines.includes('gateway') ? 'Hermes' : 'WebUI only'),
    showProject ? h('td', { class: 'mono' }, s.workspace || '-') : null,
    h('td', { class: 'num' }, s.messages != null ? s.messages : '-'),
    h('td', { class: 'num' }, ago(s.updated)),
    h('td', null, link));
}

function sessionTable(rows, showProject) {
  if (!rows.length) return h('p', { class: 'empty' }, 'No conversations recorded for this scope.');
  return h('table', null,
    h('thead', null, h('tr', null, h('th', null, 'State'), h('th', null, 'Conversation and evidence'), h('th', null, 'Engine'), showProject ? h('th', null, 'Workspace') : null, h('th', { class: 'num' }, 'Msgs'), h('th', { class: 'num' }, 'Last'), h('th', null, ''))),
    h('tbody', null, rows.map(r => sessionRow(r, showProject))));
}

function sessionsView(d) {
  const related = d.related_sessions || [];
  const relatedNote = d.related_pending ? h('p', { class: 'evidence' }, 'Looking for related conversations\u2026') : null;
  return h('div', null, sessionTable(d.session_list, false),
    relatedNote,
    related.length ? h('div', null, h('h3', null, 'Related conversations (inferred, not counted above)'),
      h('p', { class: 'evidence' }, 'These ran in another folder, but their agent tool calls referenced files in this project. This is evidence from recorded tool calls, not a recorded link.'),
      sessionTable(related, true)) : null);
}

function sameFolder(a, b) { return String(a || '').replace(/[\\/]+$/, '').toLowerCase() === String(b || '').replace(/[\\/]+$/, '').toLowerCase(); }

function grantButton(d, w) {
  if (!store.overview?.grants?.enabled) return null;
  const exact = w.permission.state === 'granted' && sameFolder(w.permission.grant, w.path);
  if (exact) return h('button', { type: 'button', onclick: () => review('revoke', w.path, d.id) }, 'Revoke\u2026');
  if (w.permission.state === 'none') return h('button', { type: 'button', onclick: () => review('grant', w.path, d.id) }, 'Grant file-tool write access\u2026');
  return h('span', { class: 'evidence' }, w.permission.state === 'blocked' ? 'blocked folder' : 'covered by a parent grant');
}

function worktreeTable(d, actions) {
  if (!d.worktrees.length) return h('p', { class: 'empty' }, d.kind === 'git' ? 'Git reported no worktrees.' : 'Not a Git repository.');
  return h('table', null,
    h('thead', null, h('tr', null, h('th', null, 'Worktree'), h('th', null, 'Branch'), h('th', null, 'File-tool write access'), actions ? h('th', null, '') : null)),
    h('tbody', null, d.worktrees.map(w => h('tr', null,
      h('td', null, h('div', { class: 'mono' }, w.path), w.current ? h('span', { class: 'chip' }, 'this project folder') : null, w.prunable ? h('span', { class: 'chip warn' }, 'missing on disk (prunable)') : null, w.locked ? h('span', { class: 'chip' }, 'locked') : null),
      h('td', null, w.branch || (w.detached ? 'detached ' + w.head : w.head)),
      h('td', null, permissionChip(w.permission)),
      actions ? h('td', null, grantButton(d, w)) : null))));
}

function gitView(d) {
  const g = d.git;
  if (d.kind !== 'git') return h('p', { class: 'empty' }, d.kind === 'missing' ? 'The folder does not exist on this machine (drive offline or moved).' : 'This folder is not a Git repository.');
  if (!g.available) return h('div', { class: 'err' }, 'Git status could not be read (timeout or Git unavailable).');
  return h('div', null,
    h('div', { class: 'chips' }, h('span', { class: 'chip' }, g.header), g.ahead ? h('span', { class: 'chip info' }, g.ahead + ' ahead') : null, g.behind ? h('span', { class: 'chip warn' }, g.behind + ' behind') : null,
      h('span', { class: g.changed ? 'chip warn' : 'chip ok' }, g.changed + ' changed'), g.untracked ? h('span', { class: 'chip' }, g.untracked + ' untracked') : null),
    h('h3', null, 'Working tree'),
    g.files.length ? h('table', null, h('tbody', null, g.files.map(f => h('tr', null, h('td', { class: 'mono', style: 'width:34px' }, f.xy.trim() || f.xy), h('td', { class: 'mono' }, f.path))))) : h('p', { class: 'empty' }, 'Clean: no changes.'),
    g.truncated ? h('p', { class: 'evidence' }, 'List truncated to the first 300 entries.') : null,
    h('h3', null, 'Recent commits'),
    g.commits.length ? h('table', null, h('tbody', null, g.commits.map(c => h('tr', null, h('td', { class: 'mono' }, c.hash), h('td', null, c.subject), h('td', { class: 'evidence' }, c.author), h('td', { class: 'num evidence' }, ago(Date.parse(c.date) / 1000)))))) : h('p', { class: 'empty' }, 'No commits.'));
}

function wikiView(d) {
  const w = d.wiki_detail;
  if (!w) return h('p', { class: 'empty' }, 'This project is not registered in the Wiki Steward (steward.json), so there are no shared tasks or handoffs for it.');
  const openHandoffs = w.handoffs.filter(x => x.status === 'open');
  return h('div', null,
    h('h3', null, 'Handoffs (' + openHandoffs.length + ' open)'),
    w.handoffs.length ? w.handoffs.map(x => h('div', { class: 'card' },
      h('div', { class: 'chips' }, h('span', { class: x.status === 'open' ? 'chip info' : 'chip' }, x.status), h('span', { class: 'chip' }, x.from + ' \u2192 ' + x.to), x.auto ? h('span', { class: 'chip warn' }, 'automatic: session interrupted') : null, h('span', { class: 'chip' }, ago(x.created_ts))),
      h('p', null, x.summary),
      x.next_steps.length ? h('div', null, h('strong', null, 'Next steps'), h('ul', null, x.next_steps.map(n => h('li', null, n)))) : null,
      x.open_questions.length ? h('div', null, h('strong', null, 'Open questions'), h('ul', null, x.open_questions.map(n => h('li', null, n)))) : null)) : h('p', { class: 'empty' }, 'No handoffs recorded.'),
    h('h3', null, 'Recent tasks'),
    w.tasks.length ? h('table', null, h('tbody', null, w.tasks.map(t => h('tr', null,
      h('td', null, stateBadge(t.state, t.evidence)),
      h('td', null, h('div', null, t.summary), h('div', { class: 'evidence' }, t.evidence + (t.files.length ? ' \u00b7 ' + t.files.join(', ') : ''))),
      h('td', null, t.agent),
      h('td', { class: 'mono' }, t.commits.join(' ')),
      h('td', { class: 'num evidence' }, ago(t.created_ts)))))) : h('p', { class: 'empty' }, 'No tasks recorded.'),
    h('p', { class: 'evidence' }, 'Read from ' + w.root + '. The dashboard never writes to the wiki; agents and the reviewer do that through the Wiki Steward.'));
}

function commandBlock(label, text) {
  return h('div', null, h('div', { class: 'evidence' }, label), h('pre', { class: 'cmd' }, text), h('button', { type: 'button', onclick: () => copy(text) }, 'Copy command'));
}

function auditView() {
  if (store.audit === undefined) { loadAudit(); return h('p', { class: 'empty' }, 'Loading\u2026'); }
  if (!store.audit.length) return h('p', { class: 'empty' }, 'No grant or revoke has been made from this page yet.');
  return h('table', null, h('tbody', null, store.audit.slice(0, 8).map(a => h('tr', null,
    h('td', { class: 'evidence' }, new Date(a.ts * 1000).toLocaleString()), h('td', null, a.user), h('td', null, a.action), h('td', { class: 'mono' }, a.path), h('td', null, a.result)))));
}

async function loadAudit() {
  try { store.audit = (await api('/grants/audit?limit=8')).audit; } catch (_) { store.audit = []; }
  if (store.tab === 'access') renderDetail();
}

async function review(action, path, projectId) {
  let report;
  try { report = await api('/grants/preview', { action, path, project_id: projectId }); } catch (error) { toast(error.message); return; }
  const dialog = h('dialog', { class: 'confirm', 'aria-labelledby': 'confirm-title' });
  const close = () => { dialog.close(); dialog.remove(); };
  const typed = report.requires_typed_name ? h('input', { type: 'text', 'aria-label': 'Type the folder name to confirm', autocomplete: 'off', placeholder: report.typed_name }) : null;
  const confirmButton = h('button', { type: 'button', class: 'primary', disabled: report.problems.length ? true : null, onclick: async () => {
    confirmButton.disabled = true;
    try {
      const result = await api('/grants/apply', { confirmation: report.confirmation, typed_name: typed ? typed.value : '' });
      toast(action === 'grant' ? 'File-tool write access granted. ' + result.live : 'File-tool write access revoked. ' + result.live);
      store.audit = undefined;
      close();
      select(store.selected);
    } catch (error) { toast(error.message); confirmButton.disabled = false; }
  } }, action === 'grant' ? 'Grant write access' : 'Revoke write access');
  if (typed) { confirmButton.disabled = true; typed.addEventListener('input', () => { confirmButton.disabled = typed.value !== report.typed_name || !!report.problems.length; }); }
  dialog.append(...[
    h('h2', { id: 'confirm-title' }, action === 'grant' ? 'Grant write access?' : 'Revoke write access?'),
    h('p', null, 'The Hermes file tools ', action === 'grant' ? 'will be allowed to write in' : 'will no longer be allowed to write in', ':'),
    h('pre', { class: 'cmd' }, report.canonical),
    report.worktree ? h('p', { class: 'evidence' }, 'This is a ' + report.worktree.kind + (report.worktree.branch ? ' on branch ' + report.worktree.branch : '') + ' of the selected project.') : null,
    report.problems.length ? h('div', { class: 'err' }, report.problems.map(x => h('p', null, x))) : null,
    report.warnings.length ? h('div', { class: 'warnbox' }, h('strong', null, 'Check before you continue'), h('ul', null, report.warnings.map(x => h('li', null, x)))) : null,
    h('p', { class: 'evidence' }, report.note),
    report.roots_now.length ? h('p', { class: 'evidence' }, 'Currently granted: ' + report.roots_now.join('; ')) : h('p', { class: 'evidence' }, 'Nothing is currently granted.'),
    typed ? h('label', null, 'Type ', h('code', null, report.typed_name), ' to confirm', h('br'), typed) : null,
    h('div', { class: 'actions' }, h('button', { type: 'button', onclick: close }, 'Cancel'), confirmButton),
    report.expires_in ? h('p', { class: 'evidence' }, 'This confirmation expires in ' + report.expires_in + ' seconds and works once.') : null].filter(Boolean));
  dialog.addEventListener('cancel', () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  (typed || dialog.querySelector('button')).focus();
}

function accessView(d) {
  const p = d.permission_detail;
  const roots = p.write_roots;
  return h('div', null,
    h('h3', null, 'Who may write here'),
    h('div', { class: 'card' },
      h('p', null, h('strong', null, 'Hermes Webapp gateway: '), permissionChip(d.permission), ' ', h('span', { class: 'evidence' }, d.permission.reason)),
      h('p', null, h('strong', null, 'Legacy WebUI / HERMES_WRITE_SAFE_ROOT: '), d.permission.legacy_env ? h('span', { class: 'chip ok' }, 'covered by ' + d.permission.legacy_env) : h('span', { class: 'chip' }, 'not covered')),
      h('p', { class: 'evidence' }, 'Registering a project never grants write access. The gateway re-reads its grant list on every file operation, so a grant or revocation needs no restart.')),
    h('h3', null, 'Authorized write roots (' + roots.length + ')'),
    roots.length ? h('table', null, h('tbody', null, roots.map(r => h('tr', null, h('td', { class: 'mono' }, r))))) : h('p', { class: 'empty' }, 'No folder is currently authorized for the Webapp gateway.'),
    p.blocked.length ? h('div', null, h('h3', null, 'Blocked folders'), h('table', null, h('tbody', null, p.blocked.map(r => h('tr', null, h('td', { class: 'mono' }, r)))))) : null,
    h('h3', null, 'Request access'),
    h('p', { class: 'evidence' }, 'Run in a terminal (outside the agent) from F:\\Odysseus. Only the exact top folder of a Git repository or worktree is accepted; credential folders, drive roots and the Odysseus install are refused.'),
    commandBlock('Authorize ' + d.path, p.request),
    p.revoke ? commandBlock('Revoke the grant that covers this project', p.revoke) : null,
    d.worktrees.length > 1 ? h('p', { class: 'evidence' }, 'A Git worktree keeps its Git data outside the worktree folder; authorize the worktree you want the agent to edit, and check the Git view for commit access.') : null,
    h('h3', null, store.overview?.grants?.enabled ? 'Worktrees and folders (grant or revoke here)' : 'Worktrees and folders'),
    store.overview?.grants?.enabled ? null : h('p', { class: 'evidence' }, 'Granting and revoking from this page is switched off on this server. Use the commands above, or ask the administrator to set ' + (store.overview?.grants?.switch || 'ODYSSEUS_PROJECTS_GRANTS') + '=1.'),
    worktreeTable(d, store.overview?.grants?.enabled),
    h('h3', null, 'Recent permission changes'), auditView(),
    h('p', { class: 'evidence', style: 'margin-top:14px' }, p.note));
}

function overviewView(d) {
  const attention = d.session_list.filter(s => ATTENTION.includes(s.state));
  const recent = d.session_list.slice(0, 5);
  const wiki = d.wiki_detail;
  return h('div', { class: 'grid' },
    h('div', null,
      h('h3', { style: 'margin-top:0' }, attention.length ? 'Needs attention' : 'Conversations'),
      sessionTable(attention.length ? attention.slice(0, 5) : recent, false),
      h('h3', null, 'Worktrees'), worktreeTable(d)),
    h('div', null,
      h('h3', { style: 'margin-top:0' }, 'Git'),
      d.git.available ? h('div', { class: 'chips' }, h('span', { class: 'chip' }, d.git.header), h('span', { class: d.git.changed ? 'chip warn' : 'chip ok' }, d.git.changed + ' changed'), d.git.untracked ? h('span', { class: 'chip' }, d.git.untracked + ' untracked') : null) : h('p', { class: 'empty' }, d.kind === 'git' ? 'Git status unavailable.' : 'No repository.'),
      d.git.commits?.length ? h('table', null, h('tbody', null, d.git.commits.slice(0, 4).map(c => h('tr', null, h('td', { class: 'mono' }, c.hash), h('td', null, c.subject))))) : null,
      h('h3', null, 'Wiki handoffs and tasks'),
      wiki ? h('div', null, h('p', null, wiki.handoffs.filter(x => x.status === 'open').length + ' open handoff(s), ' + wiki.tasks.filter(t => ['working', 'interrupted', 'blocked'].includes(t.state)).length + ' open or interrupted task(s)'),
        h('table', null, h('tbody', null, wiki.tasks.slice(0, 4).map(t => h('tr', null, h('td', null, stateBadge(t.state, t.evidence)), h('td', null, t.summary), h('td', { class: 'num evidence' }, ago(t.created_ts))))))) : h('p', { class: 'empty' }, 'Not registered in the Wiki Steward.')));
}

// Project management lives in the Hermes Webapp itself (create, rename, archive, folders, move a conversation). This page only opens it; it holds no copy of those controls.
const MANAGE_HINT = 'Opens Hermes. There: Filters \u25B8 Grouping \u25B8 Project, then + creates a project, a project\u2019s own menu renames, adds folders or archives it, and a conversation\u2019s menu has Move to project (that changes its working folder).';

function renderDetail() {
  const el = $('detail');
  if (store.error) {
    const auth = store.error.status === 401 || store.error.status === 403;
    el.replaceChildren(h('div', { class: 'err' }, h('strong', null, auth ? 'Administrator sign-in required. ' : 'Could not load. '), store.error.message, auth ? [' ', h('a', { href: '/' }, 'Sign in to Odysseus'), ' as the local administrator, then reload.'] : [' ', h('button', { type: 'button', onclick: () => select(store.selected) }, 'Retry')]));
    return;
  }
  if (store.selected === '_sessions') {
    el.replaceChildren(h('h2', null, 'All conversations'), h('p', { class: 'evidence' }, 'Every conversation from the Hermes gateway and the legacy WebUI, with the workspace it ran in. States come from records, not guesses.'), store.allSessions ? sessionTable(store.allSessions, true) : h('p', { class: 'empty' }, 'Loading\u2026'));
    return;
  }
  const d = store.detail;
  if (!d) { el.replaceChildren(h('p', { class: 'empty' }, store.selected ? 'Loading project\u2026' : 'Select a project.')); return; }
  const counts = { sessions: d.session_list.length, wiki: d.wiki_detail ? d.wiki_detail.handoffs.filter(x => x.status === 'open').length : 0 };
  const tabs = h('div', { class: 'tabs', role: 'tablist' }, TABS.map(([id, label], i) => h('button', { type: 'button', role: 'tab', 'aria-selected': String(store.tab === id), title: 'Key ' + (i + 1), onclick: () => { store.tab = id; setHash(); renderDetail(); } }, label, counts[id] ? h('span', { class: 'count' }, counts[id]) : null)));
  const body = { overview: overviewView, sessions: sessionsView, git: gitView, wiki: wikiView, access: accessView }[store.tab](d);
  el.replaceChildren(...[
    h('h2', null, d.name, permissionChip(d.permission), d.sources.map(s => h('span', { class: 'chip' }, s))),
    h('div', { class: 'path' }, h('span', null, d.path), h('button', { type: 'button', onclick: () => copy(d.path) }, 'Copy path'),
      d.kind === 'git' ? h('span', { class: 'chip' }, d.branch || 'detached HEAD') : null, d.is_worktree ? h('span', { class: 'chip' }, 'linked worktree') : null,
      d.git.available ? h('span', { class: d.git.changed ? 'chip warn' : 'chip ok' }, d.git.changed + ' changed') : null,
      h('button', { type: 'button', class: 'to-hermes', title: MANAGE_HINT, onclick: () => openProjects() }, 'Manage in Hermes')),
    d.sources.includes('WebUI project') && !d.sources.includes('Hermes project')
      ? h('p', { class: 'evidence' }, 'Legacy project: not yet in the Hermes project list. An operator can import it with scripts/hermes-projects-import.py (dry run first). Conversations are not moved.') : null,
    tabs, body].filter(Boolean));
}

function setHash() { history.replaceState(null, '', '#p=' + encodeURIComponent(store.selected || '') + '&t=' + store.tab); }

let selection = 0;
async function select(id) {
  const mine = ++selection;                                  // a newer selection makes every older request's result irrelevant
  store.selected = id;
  store.detail = null;
  store.error = null;
  setHash();
  renderList();
  renderDetail();
  try {
    if (id === '_sessions') { const body = await api('/sessions'); if (mine !== selection) return; store.allSessions = body.sessions; }
    else {
      const detail = await api('/project/' + encodeURIComponent(id) + '?related=0');
      if (mine !== selection) return;
      store.detail = detail;
      renderDetail();
      api('/project/' + encodeURIComponent(id) + '/related').then(body => {
        if (mine !== selection || !store.detail) return;
        store.detail = { ...store.detail, related_sessions: body.related_sessions, related_pending: false };
        renderDetail();
      }).catch(() => { if (mine === selection && store.detail) { store.detail = { ...store.detail, related_pending: false }; renderDetail(); } });
    }
    store.error = null;
  } catch (error) { if (mine !== selection) return; store.error = error; }
  renderDetail();
}

async function load(initial, manual) {
  if (store.loading) return;
  store.loading = true;
  $('refresh').disabled = true;
  try {
    store.overview = await api('');
    store.error = null;
    renderEngines();
    renderFilters();
    renderList();
    if (initial) {
      const params = new URLSearchParams(location.hash.slice(1));
      if (TABS.some(([id]) => id === params.get('t'))) store.tab = params.get('t');
      const wanted = params.get('p');
      const first = store.overview.projects.find(p => p.id === wanted) || store.overview.projects[0];
      if (wanted === '_sessions') await select('_sessions'); else if (first) await select(first.id); else renderDetail();
    } else if (manual && store.selected && store.selected !== '_sessions') {
      const detail = await api('/project/' + encodeURIComponent(store.selected)).catch(() => null);
      if (detail) { store.detail = detail; renderDetail(); }
    }
  } catch (error) { store.error = error; renderList(); renderDetail(); }
  store.loading = false;
  $('refresh').disabled = false;
}

document.addEventListener('keydown', event => {
  const typing = event.target.matches?.('input, textarea');
  if (event.key === '/' && !typing) { event.preventDefault(); $('search').focus(); return; }
  if (event.key === 'Escape' && typing) { event.target.blur(); return; }
  if (typing || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.key === 'r') load(false, true);
  else if (event.key === 'j' || event.key === 'k') {
    const ids = [...visibleProjects().map(p => p.id), '_sessions'];
    const at = ids.indexOf(store.selected);
    const next = ids[Math.min(ids.length - 1, Math.max(0, at + (event.key === 'j' ? 1 : -1)))];
    if (next && next !== store.selected) { select(next); $('list').querySelector('[data-id="' + CSS.escape(next) + '"]')?.scrollIntoView({ block: 'nearest' }); }
  } else if (/^[1-5]$/.test(event.key) && store.detail) { store.tab = TABS[Number(event.key) - 1][0]; setHash(); renderDetail(); }
});
$('search').addEventListener('input', event => { store.query = event.target.value; renderList(); });
$('refresh').addEventListener('click', () => load(false, true));
setInterval(() => { if (!document.hidden) load(false); }, 20000);
renderFilters();
wireHermesButton();
load(true);
