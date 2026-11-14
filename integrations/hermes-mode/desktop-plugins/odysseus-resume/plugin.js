/**
 * Odysseus: Projects inside HermesGUI, and resume a conversation from the Odysseus dashboard.
 *
 * 1. A "Projects" row in the sidebar's nav opens a Projects page inside the Webapp's own Settings window (a supported plugin settings page:
 *    `ctx.registerSettingsPage` + `sidebar.nav`). The page is not reloaded and no sign-in is repeated. The Webapp itself stashes the route under any
 *    Settings page and restores it on close (Esc, X, backdrop or browser Back), and the conversation's composer stays mounted underneath, so the same
 *    conversation, its unsent draft and its model/workspace come back (verified in the lab; Home and missing conversations fall back to Home).
 *    Read data comes from read-only routes the Odysseus proxy answers on THIS origin (/__odysseus/api/...), under the existing access grant.
 *    Management (create/rename/archive, folders, move a conversation) calls Hermes' own projects.* and session.workspace.move methods through
 *    host.request, i.e. the Webapp's already-authenticated gateway connection: the gateway token never reaches Odysseus and Odysseus keeps no
 *    project database of its own.
 *    A "diagnostic dashboard" link still leaves for the standalone Odysseus page (http://localhost:<this port>/static/projects.html), built from
 *    this page's own port and carrying only the open conversation's id (host.state.focusedStoredSessionId).
 * 2.
 * The dashboard asks Odysseus for a Webapp sign-in link that lands on /?odysseus_resume=<session id>. This plugin reads that
 * one parameter, removes it from the address bar, and asks the Webapp to open the session through its own host API
 * (host.openSession), exactly as a click in the session list would. It validates the id, never navigates anywhere else,
 * does nothing when the parameter is absent, and opens a session at most once per page load.
 */
import { Button, Codicon, ConfirmDialog, Input, PanelEmpty, PanelListRow, PanelPill, PanelSectionLabel, Skeleton, Tip, host, pluginSettingsHref } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const PARAM = 'odysseus_resume'
const VALID = /^[A-Za-z0-9._-]{1,200}$/

// The Webapp's router may rewrite the address before plugins load, so the parameter is also read from the URL this page was
// navigated to (the navigation timing entry), which the router cannot change.
function requestedFrom(href) {
  try {
    return new URL(href).searchParams.get(PARAM)
  } catch {
    return null
  }
}

function takeRequestedSession() {
  try {
    const navigation = window.performance && window.performance.getEntriesByType('navigation')[0]
    const id = requestedFrom(window.location.href) ?? (navigation ? requestedFrom(navigation.name) : null)

    if (id === null) {
      return null
    }

    const url = new URL(window.location.href)

    if (url.searchParams.has(PARAM)) {
      url.searchParams.delete(PARAM)
      window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash)
    }

    return VALID.test(id) ? id : null
  } catch {
    return null
  }
}

function whenConnected(open) {
  const state = host.state && host.state.gateway
  let done = false

  const attempt = () => {
    if (done || !state || state.get() !== 'open') {
      return
    }

    done = true
    open()
  }

  if (!state || typeof state.subscribe !== 'function') {
    window.setTimeout(open, 1500)

    return
  }

  state.subscribe(attempt)
  attempt()
}

function projectsUrl() {
  const { protocol, hostname, port } = window.location

  if (protocol !== 'http:' || hostname !== 'hermes.localhost' || !port) {
    return null
  }

  const params = new URLSearchParams({ via: 'hermes' })

  try {
    const active = host.state.focusedStoredSessionId && host.state.focusedStoredSessionId.get()

    if (typeof active === 'string' && VALID.test(active)) {
      params.set('from', active)
    }
  } catch {
    /* no readable conversation: open Projects without a return destination */
  }

  return `http://localhost:${port}/static/projects.html?${params.toString()}`
}

const API = '/__odysseus/api/projects'
const OVERVIEW_REFRESH_MS = 30000
const REQUEST_TIMEOUT_MS = 20000
const TABS = [['overview', 'Overview'], ['sessions', 'Sessions'], ['git', 'Git'], ['wiki', 'Wiki'], ['access', 'Access'], ['manage', 'Manage']]
const MUTED = { color: 'var(--ui-text-tertiary)' }
const LINE = '1px solid color-mix(in srgb, currentColor 14%, transparent)'
const STATE_TONE = { working: 'good', waiting: 'warn', blocked: 'bad', failed: 'bad', interrupted: 'bad', unknown: 'muted', completed: 'muted', idle: 'muted' }

// Void elements (<input>) and components given no children must not receive a `children` key at all.
const h = (type, props, ...children) => jsx(type, children.length === 0 ? { ...(props || {}) } : { ...(props || {}), children: children.length === 1 ? children[0] : children })

async function getJson(path, signal) {
  const timer = AbortSignal.timeout ? AbortSignal.timeout(REQUEST_TIMEOUT_MS) : undefined
  const combined = signal && timer && AbortSignal.any ? AbortSignal.any([signal, timer]) : signal || timer
  let response
  try {
    response = await fetch(path, { credentials: 'same-origin', headers: { Accept: 'application/json' }, signal: combined })
  } catch (error) {
    if (signal && signal.aborted) throw error
    const timedOut = timer && timer.aborted
    throw Object.assign(new Error(timedOut ? 'The project data took too long to load.' : 'Could not reach the Odysseus project data.'), { retryable: true })
  }
  let body = null
  try { body = await response.json() } catch { /* not JSON */ }
  if (!response.ok) {
    const expired = response.status === 401 || response.status === 403
    throw Object.assign(new Error(expired ? 'Your Odysseus access to Projects ended. Open Hermes again from Odysseus.' : (body && body.detail) || `Request failed (${response.status})`), { status: response.status, retryable: !expired })
  }
  return body
}

// One request per key; a newer key (or unmount) aborts the older one, so a slow answer can never replace a newer selection or leave "Loading" forever.
function useRequest(key, loader, deps = []) {
  const [state, setState] = useState({ status: key ? 'loading' : 'idle', data: null, error: null })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!key) { setState({ status: 'idle', data: null, error: null }); return undefined }
    const controller = new AbortController()
    setState(old => ({ status: 'loading', data: old.key === key ? old.data : null, error: null, key }))
    loader(controller.signal).then(
      data => { if (!controller.signal.aborted) setState({ status: 'ready', data, error: null, key }) },
      error => { if (!controller.signal.aborted) setState({ status: 'error', data: null, error, key }) }
    )
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, attempt, ...deps])
  return [state, useCallback(() => setAttempt(n => n + 1), [])]
}

function Retry({ error, onRetry }) {
  return h(PanelEmpty, {
    icon: 'warning', title: 'Could not load this', description: error.message,
    action: error.retryable ? h(Button, { size: 'xs', onClick: onRetry, children: 'Retry' }) : null
  })
}

function Loading({ rows = 4 }) {
  return h('div', { style: { display: 'grid', gap: '0.5rem', padding: '0.75rem' }, 'aria-busy': 'true', 'aria-label': 'Loading' },
    ...Array.from({ length: rows }, (_, i) => h(Skeleton, { key: i, style: { height: '1.5rem', width: `${90 - i * 12}%` } })))
}

function ago(seconds) {
  if (!seconds) return ''
  const delta = Math.max(0, Date.now() / 1000 - seconds)
  if (delta < 90) return 'now'
  if (delta < 5400) return `${Math.round(delta / 60)}m`
  if (delta < 172800) return `${Math.round(delta / 3600)}h`
  return `${Math.round(delta / 86400)}d`
}

function permissionLabel(permission) {
  const states = { granted: 'Write access', partial: 'Write access (some folders)', none: 'No write grant', blocked: 'Blocked', legacy: 'Write access (legacy)', unknown: 'Unknown' }
  return states[permission && permission.state] || (permission && permission.state) || 'Unknown'
}

function SessionRow({ session, onOpen }) {
  const openable = session.resume_kind === 'webapp'
  return h('div', { style: { display: 'grid', gridTemplateColumns: '1fr auto', gap: '0.5rem', padding: '0.5rem 0.25rem', borderBottom: LINE, alignItems: 'center' } },
    h('div', { style: { minWidth: 0 } },
      h('div', { style: { fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, session.title || session.id),
      h('div', { style: { ...MUTED, fontSize: '0.72rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: session.state_evidence || '' },
        [session.workspace || 'no recorded folder', ago(session.updated), session.model].filter(Boolean).join('  \u00b7  '))),
    h('div', { style: { display: 'flex', gap: '0.5rem', alignItems: 'center' } },
      h(Tip, { label: session.state_evidence || 'No recorded evidence for a more specific state', children: h('span', null, h(PanelPill, { tone: STATE_TONE[session.state] || 'muted', children: session.state || 'unknown' })) }),
      openable
        ? h(Button, { size: 'xs', onClick: () => onOpen(session), children: 'Open' })
        : h(Tip, { label: 'This conversation only exists in the legacy WebUI (port 8787); it cannot open here.', children: h('span', { style: MUTED }, 'legacy') })))
}

function OverviewTab({ d }) {
  const states = Object.entries((d.sessions && d.sessions.states) || {})
  const open = ((d.wiki_detail && d.wiki_detail.handoffs) || []).filter(x => x.status === 'open')
  return h('div', { style: { display: 'grid', gap: '1rem' } },
    h('div', { style: { display: 'flex', gap: '1.5rem', flexWrap: 'wrap' } },
      h('div', null, h(PanelSectionLabel, null, 'Conversations'), h('div', { style: { fontSize: '1.4rem' } }, (d.sessions && d.sessions.total) || 0)),
      h('div', null, h(PanelSectionLabel, null, 'Branch'), h('div', null, d.branch || (d.kind === 'git' ? 'detached HEAD' : '\u2014'))),
      h('div', null, h(PanelSectionLabel, null, 'Folder'), h('div', { style: { maxWidth: '26rem', overflowWrap: 'anywhere' } }, d.exists ? d.path : `${d.path} (not found)`))),
    states.length ? h('div', { style: { display: 'flex', gap: '0.4rem', flexWrap: 'wrap' } }, ...states.map(([name, n]) => h(PanelPill, { key: name, tone: STATE_TONE[name] || 'muted', children: `${n} ${name}` }))) : null,
    open.length ? h('div', null, h(PanelSectionLabel, null, 'Open handoffs (Wiki Steward)'), ...open.slice(0, 4).map(x => h('div', { key: x.id, style: { padding: '0.35rem 0', borderBottom: LINE } }, x.summary))) : null,
    h('div', { style: { ...MUTED, fontSize: '0.78rem', lineHeight: 1.5, borderTop: LINE, paddingTop: '0.75rem' } },
      h('strong', null, 'Four different things share a folder path: '),
      'the project folder (a registration that groups conversations), a folder added as read-only context to one message, a conversation\u2019s working directory (where its tools run, recorded when it starts), and a write grant (what the agent may change; see Access). Choosing one never implies the others.'),
    h('div', { style: { ...MUTED, fontSize: '0.78rem', lineHeight: 1.5 } },
      h('strong', null, 'Manage projects here or in the sidebar. '),
      'Use the Manage tab to rename, change folders, archive or add conversations. In the sidebar: Sidebar \u25B8 Sessions \u25B8 Filters \u25B8 Grouping \u25B8 Project turns on project groups; then + creates a project, a project\u2019s own menu renames, adds folders or archives it, ',
      'and a conversation\u2019s menu has Move to project. Moving a conversation changes its working folder (it does not move files or change write grants); do it only when the conversation is idle.'))
}

function SessionsTab({ d, onOpen }) {
  const list = d.session_list || []
  const related = d.related_sessions || []
  return h('div', null,
    list.length ? list.map(s => h(SessionRow, { key: s.id, session: s, onOpen })) : h(PanelEmpty, { icon: 'comment-discussion', title: 'No conversations ran in this project\u2019s folders yet' }),
    d.related_pending ? h('p', { style: { ...MUTED, fontSize: '0.78rem' } }, 'Looking for related conversations\u2026') : null,
    related.length ? h('div', { style: { marginTop: '1rem' } }, h(PanelSectionLabel, null, 'Related (inferred from tool calls, not counted above)'), related.map(s => h(SessionRow, { key: s.id, session: s, onOpen }))) : null)
}

function GitTab({ d }) {
  const git = d.git || {}
  if (d.kind !== 'git') return h(PanelEmpty, { icon: 'git-branch', title: d.exists ? 'This folder is not a Git repository' : 'This folder was not found' })
  return h('div', { style: { display: 'grid', gap: '1rem' } },
    h('div', null, h(PanelSectionLabel, null, 'Status'), h('div', null, git.available ? `${git.header || ''}  \u00b7  ${git.changed} changed, ${git.untracked} untracked${git.ahead ? `, ${git.ahead} ahead` : ''}${git.behind ? `, ${git.behind} behind` : ''}` : 'Git status unavailable')),
    (d.worktrees || []).length ? h('div', null, h(PanelSectionLabel, null, 'Worktrees'), d.worktrees.map(w => h('div', { key: w.path, style: { padding: '0.35rem 0', borderBottom: LINE, display: 'flex', justifyContent: 'space-between', gap: '0.5rem' } },
      h('span', { style: { overflowWrap: 'anywhere' } }, `${w.path}${w.current ? '  (this one)' : ''}  \u00b7  ${w.branch || 'detached'}`), h(PanelPill, { tone: w.permission && w.permission.state === 'granted' ? 'good' : 'muted', children: permissionLabel(w.permission) })))) : null,
    (git.commits || []).length ? h('div', null, h(PanelSectionLabel, null, 'Recent commits'), git.commits.map(c => h('div', { key: c.hash, style: { padding: '0.25rem 0', fontSize: '0.8rem' } }, h('code', null, c.hash), `  ${c.subject}  `, h('span', { style: MUTED }, `${c.author}`)))) : null,
    (git.files || []).length ? h('div', null, h(PanelSectionLabel, null, `Changed files${git.truncated ? ' (first 300)' : ''}`), git.files.slice(0, 60).map(f => h('div', { key: f.path, style: { fontFamily: 'var(--font-mono, monospace)', fontSize: '0.72rem' } }, `${f.xy}  ${f.path}`))) : null)
}

function WikiTab({ d }) {
  const wiki = d.wiki_detail
  if (!wiki) return h(PanelEmpty, { icon: 'book', title: 'No Wiki Steward project is linked to this folder' })
  const claims = wiki.claims || { counts: {}, items: [] }
  return h('div', { style: { display: 'grid', gap: '1rem' } },
    h('div', null, h(PanelSectionLabel, null, `Knowledge${claims.items.length ? ` (${Object.entries(claims.counts).map(([k, n]) => `${n} ${k}`).join(', ')})` : ''}`),
      claims.items.length ? claims.items.map(c => h('div', { key: c.id, style: { padding: '0.4rem 0', borderBottom: LINE } },
        h(PanelPill, { tone: c.status === 'verified' ? 'good' : 'warn', children: c.status === 'verified' ? 'verified' : 'needs review' }), `  ${c.claim}`,
        h('div', { style: { ...MUTED, fontSize: '0.7rem' } }, [c.id, c.subject, c.grade, c.verified_commit && `at ${c.verified_commit}`].filter(Boolean).join('  \u00b7  ')))) : h('div', { style: MUTED }, 'No knowledge claims recorded for this project.'),
      h('div', { style: { ...MUTED, fontSize: '0.72rem', marginTop: '0.4rem' } }, 'Only verified claims are treated as facts in a new conversation; claims awaiting review are shown to the agent with a clear label. Review them from the Wiki Steward terminal.')),
    h('div', null, h(PanelSectionLabel, null, 'Handoffs'), (wiki.handoffs || []).length ? wiki.handoffs.map(x => h('div', { key: x.id, style: { padding: '0.4rem 0', borderBottom: LINE } }, h(PanelPill, { tone: x.status === 'open' ? 'warn' : 'muted', children: x.status }), `  ${x.summary || ''}`)) : h('div', { style: MUTED }, 'None')),
    h('div', null, h(PanelSectionLabel, null, 'Recent tasks'), (wiki.tasks || []).length ? wiki.tasks.map(t => h('div', { key: t.id, style: { padding: '0.4rem 0', borderBottom: LINE } }, h(PanelPill, { tone: STATE_TONE[t.state] || 'muted', children: t.state }), `  ${t.summary || ''}`)) : h('div', { style: MUTED }, 'None')))
}

function AccessTab({ d }) {
  const p = d.permission_detail || {}
  const roots = p.write_roots || []
  return h('div', { style: { display: 'grid', gap: '0.75rem' } },
    h('div', null, h(PanelSectionLabel, null, 'This folder'), h(PanelPill, { tone: d.permission && d.permission.state === 'granted' ? 'good' : 'muted', children: permissionLabel(d.permission) }), h('span', { style: { ...MUTED, marginLeft: '0.5rem' } }, (d.permission && d.permission.reason) || '')),
    h('div', null, h(PanelSectionLabel, null, 'Write roots on this machine'), roots.length ? roots.map(r => h('div', { key: r, style: { overflowWrap: 'anywhere' } }, r)) : h('div', { style: MUTED }, 'none')),
    h('div', { style: { ...MUTED, fontSize: '0.78rem', lineHeight: 1.5 } }, p.note || ''),
    p.request ? h('div', null, h(PanelSectionLabel, null, 'To allow writes here (run by an operator, not from this page)'), h('code', { style: { fontSize: '0.72rem', overflowWrap: 'anywhere' } }, p.request)) : null)
}

// ---- Project management: Hermes' own project registry, reached through the Webapp's own gateway connection -----------------------------------------
// host.request is the Webapp's authenticated gateway socket, so the gateway session token never leaves the Webapp and Odysseus stores nothing. The Hermes
// gateway stays the single owner of projects.db; this page only calls its documented projects.* and session.workspace.move methods.

const isAbsolute = value => /^(?:[A-Za-z]:[\\/]|\\\\|\/)/.test(value)
const normPath = value => String(value || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
const insidePath = (path, root) => { const a = normPath(path), b = normPath(root); return !!b && (a === b || a.startsWith(`${b}/`)) }
const BUSY_STATES = new Set(['working', 'waiting'])

function describeError(error) {
  const reason = error && error.data && error.data.reason
  if (reason === 'session_busy') return 'That conversation is running a turn. Wait for it to finish, then try again.'
  const data = error && error.data
  const detail = data && typeof data === 'object' ? [data.detail, data.field && `field: ${data.field}`].filter(Boolean).join(', ') : ''
  return `${(error && error.message) || 'The request failed.'}${detail ? ` (${detail})` : ''}`
}

async function rpc(method, params) {
  try {
    return await host.request(method, params)
  } catch (error) {
    throw Object.assign(new Error(describeError(error)), { cause: error })
  }
}

function useNativeProjects() {
  const [state, setState] = useState({ status: 'loading', projects: [], error: null })
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])
  const load = useCallback(async () => {
    try {
      const result = await rpc('projects.list', {})
      if (alive.current) setState({ status: 'ready', projects: (result && result.projects) || [], error: null })
    } catch (error) {
      if (alive.current) setState({ status: 'error', projects: [], error })
    }
  }, [])
  useEffect(() => { void load() }, [load])
  return [state, load]
}

function useNativeTree() {
  const [state, setState] = useState({ status: 'loading', projects: [] })
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])
  const load = useCallback(async () => {
    try {
      const result = await rpc('projects.tree', { preview_limit: 0 })
      if (alive.current) setState({ status: 'ready', projects: (result && result.projects) || [] })
    } catch { if (alive.current) setState({ status: 'error', projects: [] }) }
  }, [])
  useEffect(() => { void load() }, [load])
  return [state, load]
}

// Re-read every Hermes project list that is on screen: the sidebar and its Move to project menu share the native store, this page keeps its own copies.
async function refreshHermesLists() {
  try { if (host.projects && host.projects.refresh) await host.projects.refresh() } catch { /* the sidebar refreshes itself on its next tick */ }
}

const matchNative = (projects, d) => projects.filter(p => (p.folders || []).some(f => (d.paths || [d.path]).some(x => normPath(x) === normPath(f.path))))

function Note({ note }) {
  return note ? h('div', { role: note.ok ? 'status' : 'alert', style: { fontSize: '0.8rem', padding: '0.4rem 0.6rem', borderRadius: '0.4rem', background: note.ok ? 'color-mix(in srgb, #3fb950 14%, transparent)' : 'color-mix(in srgb, #f85149 16%, transparent)' } }, note.text) : null
}

// A folder path with the machine's drives and a live check. The check is advice only: Hermes still decides what it accepts, and nothing here lists a
// folder's contents, changes permissions or creates anything.
function FolderField({ value, onChange, label, placeholder }) {
  const [drives] = useRequest('drives', signal => getJson('/__odysseus/api/fs/drives', signal))
  const trimmed = value.trim()
  const [debounced, setDebounced] = useState('')
  useEffect(() => { const t = window.setTimeout(() => setDebounced(trimmed), 350); return () => window.clearTimeout(t) }, [trimmed])
  const [check] = useRequest(debounced && debounced === trimmed ? `check:${debounced}` : null, signal => getJson(`/__odysseus/api/fs/check?path=${encodeURIComponent(debounced)}`, signal))
  const c = check.status === 'ready' ? check.data : null
  const list = (drives.status === 'ready' && drives.data && drives.data.drives) || []
  return h('div', { style: { display: 'grid', gap: '0.3rem' } },
    h(Input, { value, placeholder, 'aria-label': label, onChange: e => onChange(e.target.value), spellCheck: false }),
    list.length ? h('div', { style: { display: 'flex', gap: '0.3rem', flexWrap: 'wrap', alignItems: 'center' }, role: 'group', 'aria-label': 'Drives' },
      h('span', { style: { ...MUTED, fontSize: '0.72rem' } }, 'Drives:'),
      ...list.filter(x => x.ready).map(x => h(Button, { key: x.path, size: 'xs', variant: 'ghost', title: `${x.label || 'Drive'} \u00b7 ${x.filesystem || 'unknown file system'} \u00b7 ${x.type}`,
        onClick: () => onChange(isAbsolute(trimmed) && trimmed.slice(0, 2).toUpperCase() === x.path.slice(0, 2).toUpperCase() ? value : x.path), children: `${x.path.slice(0, 2)} ${x.filesystem || ''}`.trim() }))) : null,
    trimmed && !isAbsolute(trimmed) ? h('div', { style: { color: 'var(--ui-text-warning, #b7791f)', fontSize: '0.72rem' } }, 'Enter a full path that starts with a drive letter, for example F:\\Odysseus.') : null,
    c ? h('div', { style: { display: 'flex', gap: '0.4rem', flexWrap: 'wrap', alignItems: 'center', fontSize: '0.72rem' } },
      h(PanelPill, { tone: c.valid ? 'good' : 'warn', children: c.valid ? (c.kind === 'git' ? `Git folder${c.git && c.git.branch ? ` \u00b7 ${c.git.branch}` : ''}` : 'Folder exists') : (c.kind === 'missing' ? 'Does not exist yet' : 'Not usable') }),
      c.filesystem ? h(PanelPill, { tone: 'muted', children: `${c.drive || ''} ${c.filesystem}`.trim() }) : null,
      c.permission ? h(PanelPill, { tone: c.permission.state === 'granted' ? 'good' : 'muted', children: permissionLabel(c.permission) }) : null,
      c.problem ? h('span', { style: MUTED }, c.problem) : null) : null)
}

function NewProjectForm({ onCreated, onCancel, initialName = '', initialPath = '' }) {
  const [name, setName] = useState(initialName)
  const [path, setPath] = useState(initialPath)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState(null)
  const submit = async event => {
    event && event.preventDefault()
    const folder = path.trim()
    if (!name.trim()) return setNote({ ok: false, text: 'Give the project a name.' })
    if (folder && !isAbsolute(folder)) return setNote({ ok: false, text: 'Enter the full folder path, for example C:\\Users\\you\\work\\app.' })
    setBusy(true); setNote(null)
    try {
      const result = await rpc('projects.create', { name: name.trim(), folders: folder ? [folder] : [] })
      onCreated(result && result.project, folder)
    } catch (error) {
      setNote({ ok: false, text: error.message })
      setBusy(false)
    }
  }
  return h('form', { onSubmit: submit, style: { display: 'grid', gap: '0.4rem', padding: '0.5rem', border: LINE, borderRadius: '0.5rem' } },
    h(Input, { value: name, placeholder: 'Project name', 'aria-label': 'Project name', autoFocus: true, onChange: e => setName(e.target.value) }),
    h(FolderField, { value: path, onChange: setPath, label: 'Working folder', placeholder: 'Working folder (full path, e.g. F:\\Odysseus)' }),
    h('div', { style: { ...MUTED, fontSize: '0.72rem' } }, 'Creates a Hermes project. It does not change write permissions or move any conversation.'),
    h(Note, { note }),
    h('div', { style: { display: 'flex', gap: '0.4rem' } },
      h(Button, { size: 'xs', type: 'submit', disabled: busy, children: busy ? 'Creating\u2026' : 'Create project' }),
      h(Button, { size: 'xs', variant: 'ghost', type: 'button', onClick: onCancel, children: 'Cancel' })))
}

function AddConversation({ d, project, onMoved }) {
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState('')
  const [pending, setPending] = useState(null)
  const [sessions] = useRequest(open ? 'sessions' : null, signal => getJson('/__odysseus/api/sessions', signal))
  const roots = (project.folders || []).map(f => f.path)
  const target = project.primary_path || roots[0] || ''
  const candidates = useMemo(() => {
    const q = filter.trim().toLowerCase()
    return ((sessions.data && sessions.data.sessions) || [])
      .filter(x => (x.engines || []).includes('gateway') && !roots.some(r => insidePath(x.workspace, r)))
      .filter(x => !q || [x.title, x.workspace, x.id].some(v => v && String(v).toLowerCase().includes(q)))
      .slice(0, 40)
  }, [sessions.data, filter, roots.join('|')])
  const move = async () => {
    const live = await rpc('session.list', { limit: 200 })
    const row = ((live && live.sessions) || []).find(x => x.id === pending.id)
    if (row && row.running) throw new Error('That conversation is running a turn right now. Wait for it to finish, then try again.')
    // The Webapp's own move passes the owner profile: the session's execution generation is tracked per profile, so omitting it fails as invalid_params.
    const profile = host.state.profile.get()
    await rpc('session.workspace.move', { cwd: target, session_key: pending.id, ...(profile ? { profile } : {}) })
    onMoved()
  }
  if (!target) return h('div', { style: MUTED }, 'Add a folder to this project first; conversations are placed by working folder.')
  if (!open) return h(Button, { size: 'xs', variant: 'secondary', onClick: () => setOpen(true), children: '+ Add conversation' })
  return h('div', { style: { display: 'grid', gap: '0.5rem', padding: '0.5rem', border: LINE, borderRadius: '0.5rem' } },
    h('div', { style: { display: 'flex', gap: '0.5rem', alignItems: 'center' } },
      h(Input, { value: filter, placeholder: 'Find a conversation to move into this project', 'aria-label': 'Find conversation', onChange: e => setFilter(e.target.value) }),
      h(Button, { size: 'xs', variant: 'ghost', onClick: () => setOpen(false), children: 'Close' })),
    sessions.status === 'error' ? h(Retry, { error: sessions.error, onRetry: () => { setOpen(false); setTimeout(() => setOpen(true), 0) } })
      : sessions.status !== 'ready' ? h(Loading, { rows: 3 })
      : candidates.length ? candidates.map(x => h('div', { key: x.id, style: { display: 'grid', gridTemplateColumns: '1fr auto', gap: '0.5rem', alignItems: 'center', padding: '0.35rem 0', borderBottom: LINE } },
          h('div', { style: { minWidth: 0 } }, h('div', { style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, x.title || x.id),
            h('div', { style: { ...MUTED, fontSize: '0.72rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, `${x.workspace || 'no recorded folder'}  \u00b7  ${x.state || 'unknown'}`)),
          BUSY_STATES.has(x.state)
            ? h(Tip, { label: 'This conversation is running. Moving it would change its folder under an active agent.', children: h('span', { style: MUTED }, 'running') })
            : h(Button, { size: 'xs', onClick: () => setPending(x), children: 'Move here\u2026' })))
      : h('div', { style: MUTED }, 'No other conversations to add.'),
    h(ConfirmDialog, {
      open: !!pending, onClose: () => setPending(null), onConfirm: move, confirmLabel: 'Move conversation', title: 'Move this conversation into the project?',
      description: pending ? `\u201c${pending.title || pending.id}\u201d will run in ${target} instead of ${pending.workspace || 'its current folder'}.` : '',
      children: h('div', { style: { fontSize: '0.8rem', lineHeight: 1.5, display: 'grid', gap: '0.35rem' } },
        h('div', null, 'This changes the conversation\u2019s real working folder. No files are moved or copied, and the transcript is kept.'),
        h('div', null, 'Write permissions do not change: the agent can write in the new folder only if it is already allowed (see the Access tab).'),
        h('div', null, 'The conversation will see the new folder\u2019s files from its next message on.'))
    }))
}

function ManageTab({ d, native, reloadNative, onChanged }) {
  const matches = native.status === 'ready' ? matchNative(native.projects, d) : []
  const [note, setNote] = useState(null)
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(null)
  const [newFolder, setNewFolder] = useState('')
  const [rename, setRename] = useState({})
  const [del, setDel] = useState(null)
  const [delText, setDelText] = useState('')
  const run = async (fn, ok) => {
    setBusy(true); setNote(null)
    try { await fn(); await refreshHermesLists(); await reloadNative(); onChanged(); setNote({ ok: true, text: ok }) } catch (error) { setNote({ ok: false, text: error.message }) } finally { setBusy(false) }
  }
  if (native.status === 'loading') return h(Loading, { rows: 3 })
  if (native.status === 'error') return h(Retry, { error: Object.assign(native.error, { retryable: true }), onRetry: reloadNative })
  if (!matches.length) {
    return h('div', { style: { display: 'grid', gap: '0.6rem' } },
      h('div', null, 'This folder is not a Hermes project yet. Creating one lets Hermes group its conversations by project.'),
      h(Note, { note }),
      h('div', null, h(Button, { size: 'xs', disabled: busy, onClick: () => run(() => rpc('projects.create', { name: d.name, folders: [d.path] }), `Created the Hermes project \u201c${d.name}\u201d.`), children: 'Create Hermes project from this folder' })),
      h('div', { style: { ...MUTED, fontSize: '0.75rem' } }, 'No write access is granted and no conversation is moved.'))
  }
  return h('div', { style: { display: 'grid', gap: '1.25rem' } },
    h(Note, { note }),
    ...matches.map(p => {
      const folders = p.folders || []
      const value = rename[p.id] !== undefined ? rename[p.id] : p.name
      return h('div', { key: p.id, style: { display: 'grid', gap: '0.75rem' } },
        h('div', { style: { display: 'flex', gap: '0.5rem', alignItems: 'center' } },
          h(Input, { value, 'aria-label': 'Project name', onChange: e => setRename({ ...rename, [p.id]: e.target.value }), style: { maxWidth: '22rem' } }),
          h(Button, { size: 'xs', disabled: busy || !value.trim() || value.trim() === p.name, onClick: () => run(() => rpc('projects.update', { id: p.id, name: value.trim() }), 'Renamed.'), children: 'Rename' }),
          p.archived ? h(PanelPill, { tone: 'warn', children: 'archived' }) : null),
        h('div', null, h(PanelSectionLabel, null, 'Folders'),
          folders.map(f => h('div', { key: f.path, style: { display: 'flex', gap: '0.5rem', alignItems: 'center', padding: '0.25rem 0', borderBottom: LINE } },
            h('span', { style: { flex: 1, overflowWrap: 'anywhere', fontSize: '0.8rem' } }, f.path),
            f.is_primary ? h(PanelPill, { tone: 'good', children: 'primary' }) : h(Button, { size: 'xs', variant: 'ghost', disabled: busy, onClick: () => run(() => rpc('projects.set_primary', { id: p.id, path: f.path }), 'Primary folder changed.'), children: 'Make primary' }),
            h(Button, { size: 'xs', variant: 'ghost', disabled: busy || folders.length < 2, onClick: () => setConfirm({ kind: 'remove', project: p, path: f.path }), children: 'Remove' }))),
          h('div', { style: { display: 'flex', gap: '0.5rem', marginTop: '0.5rem' } },
            h('div', { style: { flex: 1 } }, h(FolderField, { value: newFolder, onChange: setNewFolder, label: 'Add folder', placeholder: 'Add a folder (full path)' })),
            h(Button, { size: 'xs', disabled: busy || !newFolder.trim(), onClick: () => {
              const path = newFolder.trim()
              if (!isAbsolute(path)) return setNote({ ok: false, text: 'Enter the full folder path.' })
              void run(async () => { await rpc('projects.add_folder', { id: p.id, path }); setNewFolder('') }, 'Folder added. Existing conversations keep their own working folder.')
            }, children: 'Add folder' })),
          h('div', { style: { ...MUTED, fontSize: '0.72rem', marginTop: '0.25rem' } }, 'A project folder is a registration: it groups conversations. It is not a write grant, and adding or removing one never moves files or changes permissions. Hermes accepts a folder that does not exist yet.')),
        p.archived ? null : h(AddConversation, { d, project: p, onMoved: () => { void reloadNative(); onChanged(); setNote({ ok: true, text: 'Conversation moved. Its working folder is now this project\u2019s folder.' }) } }),
        p.archived ? null : h('div', { style: { display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' } },
          h(Button, { size: 'xs', variant: 'secondary', disabled: busy, onClick: () => {
            const path = p.primary_path || folders[0] && folders[0].path || ''
            if (path && host.supportsNewChatCwd) {
              host.newChat(undefined, { cwd: path })
              host.notify && host.notify({ kind: 'info', message: `New conversation will work in ${path}.` })
              return
            }
            try { if (path && navigator.clipboard) void navigator.clipboard.writeText(path) } catch { /* best effort */ }
            host.newChat()
            host.notify && host.notify({ kind: 'info', message: path ? `New conversation started. Choose the workspace ${path} in the composer (path copied).` : 'New conversation started.' })
          }, children: host.supportsNewChatCwd ? '+ New conversation here' : '+ New conversation' }),
          h('span', { style: { ...MUTED, fontSize: '0.72rem' } }, host.supportsNewChatCwd
            ? 'Starts a draft whose working folder is this project\u2019s primary folder, exactly as if you had picked it in the composer. It does not grant write access.'
            : 'Opens a fresh draft. This Hermes Desktop cannot preset the folder, so pick it in the composer\u2019s workspace chip (the path is copied); existing permissions apply.')),
        h('div', { style: { borderTop: LINE, paddingTop: '0.75rem', display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' } },
          h(Button, { size: 'xs', variant: 'ghost', disabled: busy, onClick: () => p.archived ? run(() => rpc('projects.archive', { id: p.id, restore: true }), 'Restored.') : setConfirm({ kind: 'archive', project: p }), children: p.archived ? 'Restore project' : 'Archive project' }),
          h('span', { style: { ...MUTED, fontSize: '0.72rem' } }, 'Archiving hides the project in Hermes. Conversations, folders and files stay as they are.')),
        h('div', { style: { display: 'grid', gap: '0.4rem', border: '1px solid color-mix(in srgb, var(--ui-text-danger, #e5534b) 40%, transparent)', borderRadius: '0.5rem', padding: '0.75rem' } },
          h(PanelSectionLabel, null, 'Delete project'),
          h('div', { style: { fontSize: '0.78rem' } }, 'Deleting is permanent and different from archiving: it removes this project\u2019s registration from Hermes (its name, colour and folder list).'),
          h('ul', { style: { ...MUTED, fontSize: '0.72rem', margin: 0, paddingLeft: '1.1rem' } },
            h('li', null, 'Conversations are not deleted. They stay in your history and fall back to Recents.'),
            h('li', null, `The folder${folders.length === 1 ? '' : 's'} and every file on disk (${folders.map(f => f.path).join(', ') || 'none registered'}) are not touched.`),
            h('li', null, 'Write permissions are not changed. Deleting never grants or revokes access.')),
          h('div', null, h(Button, { size: 'xs', variant: 'ghost', disabled: busy, style: { color: 'var(--ui-text-danger, #e5534b)' }, onClick: () => { setDelText(''); setDel(p) }, children: 'Delete project\u2026' })))
      )
    }),
    h('div', { style: { ...MUTED, fontSize: '0.72rem' } }, 'A default model per project is not supported by Hermes projects yet, so there is no control for it here.'),
    h(ConfirmDialog, {
      open: !!del, onClose: () => setDel(null), destructive: true, confirmLabel: 'Delete project', busyLabel: 'Deleting\u2026', doneLabel: 'Deleted',
      title: del ? `Delete \u201c${del.name}\u201d?` : '',
      description: del ? 'This removes the project from Hermes permanently. Conversations, folders and files are not deleted, and permissions do not change. To only hide it, use Archive instead.' : '',
      onConfirm: async () => {
        const target = del
        if (!target) return
        if (delText.trim() !== target.name) throw new Error(`Type the exact project name \u201c${target.name}\u201d to confirm.`)
        if (host.projects && typeof host.projects.delete === 'function') await host.projects.delete(target.id)
        else { await rpc('projects.delete', { id: target.id }); await refreshHermesLists() }
        await reloadNative(); onChanged()
        const text = `Deleted the Hermes project \u201c${target.name}\u201d. Its conversations and folders were left untouched.`
        setNote({ ok: true, text })
        if (host.notify) host.notify({ kind: 'success', message: text })
      }
    }, del ? h('div', { style: { display: 'grid', gap: '0.4rem', marginTop: '0.5rem' } },
      h('label', { style: { fontSize: '0.8rem' } }, `Type ${del.name} to confirm`),
      h(Input, { value: delText, autoFocus: true, spellCheck: false, 'aria-label': 'Project name to confirm', placeholder: del.name, onChange: e => setDelText(e.target.value), onKeyDown: e => { if (e.key === 'Enter' && delText.trim() !== del.name) { e.preventDefault(); e.stopPropagation() } } })) : null),
    h(ConfirmDialog, {
      open: !!confirm, onClose: () => setConfirm(null), destructive: true,
      confirmLabel: confirm && confirm.kind === 'archive' ? 'Archive' : 'Remove folder',
      title: confirm && confirm.kind === 'archive' ? `Archive \u201c${confirm.project.name}\u201d?` : 'Remove this folder from the project?',
      description: confirm && confirm.kind === 'archive' ? 'It disappears from Hermes\u2019 project list. Conversations and files are not touched. You can restore it from this tab.' : confirm ? `${confirm.path} stops belonging to the project. Nothing on disk changes.` : '',
      onConfirm: async () => {
        const current = confirm
        await rpc(current.kind === 'archive' ? 'projects.archive' : 'projects.remove_folder', current.kind === 'archive' ? { id: current.project.id } : { id: current.project.id, path: current.path })
        await refreshHermesLists(); await reloadNative(); onChanged(); setNote({ ok: true, text: current.kind === 'archive' ? 'Archived.' : 'Folder removed.' })
      }
    }))
}

function ProjectDetail({ id, onOpenSession, diagnosticUrl, onChanged }) {
  const [tab, setTab] = useState('overview')
  const [base, reload] = useRequest(id, signal => getJson(`${API}/${encodeURIComponent(id)}`, signal))
  const [native, reloadNative] = useNativeProjects()
  const changed = useCallback(() => { reload(); if (onChanged) onChanged() }, [reload, onChanged])
  const [related] = useRequest(id && base.status === 'ready' ? `${id}:related` : null, signal => getJson(`${API}/${encodeURIComponent(id)}/related`, signal), [base.data])
  useEffect(() => { setTab('overview') }, [id])
  // The registry changes while this page is open (a project was just created, a folder added elsewhere): re-read it whenever Manage is shown or the project changes,
  // otherwise Manage would call an existing project \u201cnot a Hermes project yet\u201d.
  useEffect(() => { if (tab === 'manage') void reloadNative() }, [tab, id, reloadNative])
  if (!id) return h(PanelEmpty, { icon: 'project', title: 'Select a project' })
  if (base.status === 'error' && !base.data) return h(Retry, { error: base.error, onRetry: reload })
  if (!base.data) return h(Loading, { rows: 5 })
  const d = { ...base.data, ...(related.status === 'ready' ? { related_sessions: related.data.related_sessions, related_pending: false } : related.status === 'error' ? { related_pending: false } : {}) }
  return h('div', { style: { display: 'grid', gap: '0.75rem', alignContent: 'start', minWidth: 0, overflowWrap: 'anywhere' } },
    h('div', { style: { display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' } },
      h('h3', { style: { margin: 0, fontSize: '1.05rem', fontWeight: 600 } }, d.name),
      h(PanelPill, { tone: d.permission && d.permission.state === 'granted' ? 'good' : 'muted', children: permissionLabel(d.permission) }),
      d.branch ? h(PanelPill, { children: d.branch }) : null,
      ...(d.sources || []).slice(0, 3).map(src => h(PanelPill, { key: src, children: src })),
      h('span', { style: { flex: 1 } }),
      diagnosticUrl ? h(Tip, { label: 'Opens the standalone Odysseus dashboard (leaves Hermes)', children: h(Button, { size: 'xs', variant: 'ghost', onClick: () => openDiagnostic(), children: 'Diagnostic dashboard' }) }) : null),
    h('div', { role: 'tablist', style: { display: 'flex', flexWrap: 'wrap', gap: '0.25rem', borderBottom: LINE, paddingBottom: '0.25rem' } },
      ...TABS.map(([key, label]) => h(Button, { key, role: 'tab', 'aria-selected': tab === key, size: 'xs', variant: tab === key ? 'secondary' : 'ghost', onClick: () => setTab(key), children: label }))),
    tab === 'overview' ? h(OverviewTab, { d }) : tab === 'sessions' ? h(SessionsTab, { d, onOpen: onOpenSession }) : tab === 'git' ? h(GitTab, { d }) : tab === 'wiki' ? h(WikiTab, { d }) : tab === 'manage' ? h(ManageTab, { d, native, reloadNative, onChanged: changed }) : h(AccessTab, { d }))
}

function ProjectsPage() {
  const [overview, reloadOverviewOnly] = useRequest('overview', signal => getJson(API, signal))
  const [nativeAll, reloadNativeAll] = useNativeProjects()
  const [tree, reloadTree] = useNativeTree()
  const reloadOverview = useCallback(() => { reloadOverviewOnly(); void reloadNativeAll(); void reloadTree() }, [reloadOverviewOnly, reloadNativeAll, reloadTree])
  const [tick, setTick] = useState(0)
  const [selected, setSelected] = useState(() => { try { return sessionStorage.getItem('odysseus.projects.selected') || null } catch { return null } })
  const [query, setQuery] = useState('')
  const [creating, setCreating] = useState(false)
  const [selectPath, setSelectPath] = useState(null)
  const searchRef = useRef(null)
  const rootRef = useRef(null)
  const [narrow, setNarrow] = useState(false)
  useEffect(() => {
    const node = rootRef.current
    if (!node || typeof ResizeObserver === 'undefined') return undefined
    // Inline styles cannot use media queries and the page lives inside Settings' card, so the layout follows the card's own width.
    const observer = new ResizeObserver(entries => setNarrow(entries[0].contentRect.width < 640))
    observer.observe(node)
    return () => observer.disconnect()
  }, [])
  useEffect(() => { const t = window.setInterval(() => { if (!document.hidden) reloadOverview() }, OVERVIEW_REFRESH_MS); return () => window.clearInterval(t) }, [reloadOverview])
  const projects = (overview.data && overview.data.projects) || []
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? projects.filter(p => [p.name, p.path, p.branch, ...(p.sources || [])].some(x => x && String(x).toLowerCase().includes(q))) : projects
  }, [projects, query])
  useEffect(() => {
    if (!selectPath || overview.status !== 'ready') return
    const made = projects.find(p => (p.paths || [p.path]).some(x => normPath(x) === normPath(selectPath)))
    if (made) { setSelected(made.id); setSelectPath(null) }
  }, [selectPath, overview.status, projects])
  useEffect(() => {
    if (overview.status === 'ready' && projects.length && !projects.some(p => p.id === selected)) setSelected(projects[0].id)
  }, [overview.status, projects, selected])
  useEffect(() => { try { if (selected) sessionStorage.setItem('odysseus.projects.selected', selected) } catch { /* ignore */ } }, [selected])
  const open = useCallback(session => {
    Promise.resolve(host.openSession(session.id)).then(() => {
      // openSession keeps the Settings route when the conversation is already the focused one; leave the overlay explicitly so Open always lands on it.
      if (window.location.hash.startsWith('#/settings')) host.navigate(`/${encodeURIComponent(session.id)}`)
    }).catch(() => host.notify && host.notify({ kind: 'error', message: 'That conversation could not be opened.' }))
  }, [])
  void tick; void setTick
  const diagnostic = projectsUrl()

  const registeredOf = p => nativeAll.status === 'ready' ? matchNative(nativeAll.projects.filter(x => !x.archived), p) : null
  const registered = shown.filter(p => { const m = registeredOf(p); return m && m.length })
  const isArchivedRow = p => nativeAll.status === 'ready' && matchNative(nativeAll.projects.filter(x => x.archived), p).length > 0
  const unregistered = shown.filter(p => { const m = registeredOf(p); return m && !m.length && !isArchivedRow(p) })
  const archivedRows = shown.filter(p => { const m = registeredOf(p); return m && !m.length && isArchivedRow(p) })
  const detected = tree.status === 'ready' ? tree.projects.filter(x => x.isAuto && x.path && !nativeAll.projects.some(n => (n.folders || []).some(f => normPath(f.path) === normPath(x.path))) && !projects.some(p => (p.paths || [p.path]).some(q => normPath(q) === normPath(x.path)))) : []
  const reasonOf = p => (p.exists === false ? 'its folder is missing on disk \u00b7 ' : '') + `known from: ${(p.sources || []).join(', ') || 'conversation folders'}`
  const row = (p, note) => h(PanelListRow, {
    key: p.id, rowKey: p.id, active: p.id === selected, onSelect: () => setSelected(p.id), icon: p.kind === 'git' ? 'git-branch' : 'root-folder',
    dotClassName: p.sessions && p.sessions.attention ? 'bg-amber-500' : undefined, title: p.name, tooltip: note,
    meta: p.sessions && p.sessions.total ? String(p.sessions.total) : undefined
  })
  const groupLabel = (text, help) => h('div', { style: { margin: '0.6rem 0 0.2rem' } }, h(PanelSectionLabel, null, text), help ? h('div', { style: { ...MUTED, fontSize: '0.68rem', lineHeight: 1.3 } }, help) : null)
  const list = overview.status === 'error' ? h(Retry, { error: overview.error, onRetry: reloadOverview })
    : overview.status === 'loading' && !projects.length ? h(Loading, { rows: 6 })
    : shown.length || detected.length ? h('div', { role: 'listbox', 'aria-label': 'Projects' },
        registered.length ? groupLabel('Hermes projects', 'Registered in Hermes. These appear in the sidebar and in a conversation\u2019s \u201cMove to project\u201d menu.') : null,
        ...registered.map(p => row(p, 'Registered Hermes project')),
        unregistered.length ? groupLabel('Not registered', 'Known to Odysseus, but not Hermes projects, so \u201cMove to project\u201d does not list them. Open one and use Manage \u203a Create Hermes project.') : null,
        ...unregistered.map(p => h('div', { key: p.id, title: reasonOf(p) }, row(p, reasonOf(p)), h('div', { style: { ...MUTED, fontSize: '0.66rem', padding: '0 0.75rem 0.25rem 2rem', overflowWrap: 'anywhere' } }, reasonOf(p)))),
        detected.length ? groupLabel('Detected from conversations', 'Hermes groups these by conversation folder automatically. They are not registered and are not changed here.') : null,
        ...detected.map(x => h('div', { key: x.id, style: { display: 'flex', alignItems: 'center', gap: '0.4rem', padding: '0.25rem 0.75rem' } },
          h('span', { style: { flex: 1, minWidth: 0, fontSize: '0.8rem', overflowWrap: 'anywhere' }, title: x.path }, x.label || x.path),
          h(Button, { size: 'xs', variant: 'ghost', title: `Register ${x.path} as a Hermes project (no write access is granted)`, onClick: async () => { try { await rpc('projects.create', { name: x.label || x.path, folders: [x.path] }); await refreshHermesLists(); reloadOverview() } catch (error) { host.notify && host.notify({ kind: 'error', message: error.message }) } }, children: 'Register' }))),
        archivedRows.length ? groupLabel('Archived', 'Hidden in Hermes, so not offered by \u201cMove to project\u201d. Open one and use Manage \u203a Restore project.') : null,
        ...archivedRows.map(p => row(p, 'Archived Hermes project')))
    : h(PanelEmpty, { icon: 'search', title: 'No projects match' })

  return h('div', { ref: rootRef, style: { display: 'grid', gridTemplateColumns: narrow ? 'minmax(0, 1fr)' : 'minmax(12rem, 16rem) minmax(0, 1fr)', gap: '1rem', minHeight: narrow ? undefined : '28rem', alignItems: 'start', minWidth: 0 } },
    h('div', { style: { display: 'grid', gap: '0.5rem', alignContent: 'start', minWidth: 0, ...(narrow ? { borderBottom: LINE, paddingBottom: '0.75rem', maxHeight: '14rem', overflowY: 'auto' } : { borderRight: LINE, paddingRight: '0.75rem' }) } },
      h(Button, { size: 'xs', variant: 'secondary', onClick: () => setCreating(v => !v), 'aria-expanded': creating, children: '+ New project' }),
      creating ? h(NewProjectForm, { onCancel: () => setCreating(false), onCreated: (project, folder) => { setCreating(false); setSelectPath(folder || null); reloadOverview() } }) : null,
      h(Input, { ref: searchRef, value: query, placeholder: 'Search projects', 'aria-label': 'Search projects', onChange: e => setQuery(e.target.value), onKeyDown: e => { if (e.key === 'Escape' && query) { e.stopPropagation(); setQuery('') } } }),
      list),
    h('div', { style: { minWidth: 0 } }, h(ProjectDetail, { id: selected, onOpenSession: open, diagnosticUrl: diagnostic, onChanged: reloadOverview })))
}

function openDiagnostic() {
  const href = projectsUrl()
  if (href) window.location.assign(href)
}

export default {
  id: 'odysseus-resume',
  name: 'Odysseus: Projects and resume',
  register(ctx) {
    // Projects lives inside Settings (a supported plugin page) so it opens as an overlay over the still-mounted conversation.
    if (ctx && typeof ctx.registerSettingsPage === 'function') {
      ctx.registerSettingsPage({ id: 'projects', title: 'Projects', icon: 'project', order: 5, render: () => jsx(ProjectsPage, {}) })
    }

    if (ctx && typeof ctx.registerMany === 'function') {
      ctx.registerMany([{ id: 'projects-nav', area: 'sidebar.nav', order: 40, data: { codicon: 'project', label: 'Projects', path: pluginSettingsHref('odysseus-resume') } }])
    }

    const id = takeRequestedSession()

    if (id) {
      whenConnected(() => {
        Promise.resolve(host.openSession(id)).catch(() => {
          /* the session may not exist on this gateway; the Webapp then stays where it is */
        })
      })
    }
  }
}
