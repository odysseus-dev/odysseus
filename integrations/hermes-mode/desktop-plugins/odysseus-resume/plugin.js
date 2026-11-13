/**
 * Odysseus: Projects inside HermesGUI, and resume a conversation from the Odysseus dashboard.
 *
 * 1. A "Projects" row in the sidebar's nav opens a Projects page inside the Webapp's own Settings window (a supported plugin settings page:
 *    `ctx.registerSettingsPage` + `sidebar.nav`). The page is not reloaded and no sign-in is repeated (verified in the lab). Limitation inherited
 *    from the Webapp: closing ANY Settings page returns to the home route, not the conversation, and an unsent draft is not kept. Data comes from
 *    read-only routes the Odysseus proxy answers on THIS origin (/__odysseus/api/...), under the existing access grant; no Hermes credential is
 *    read, stored or sent by this plugin.
 *    A "diagnostic dashboard" link still leaves for the standalone Odysseus page (http://localhost:<this port>/static/projects.html), built from
 *    this page's own port and carrying only the open conversation's id (host.state.focusedStoredSessionId).
 * 2.
 * The dashboard asks Odysseus for a Webapp sign-in link that lands on /?odysseus_resume=<session id>. This plugin reads that
 * one parameter, removes it from the address bar, and asks the Webapp to open the session through its own host API
 * (host.openSession), exactly as a click in the session list would. It validates the id, never navigates anywhere else,
 * does nothing when the parameter is absent, and opens a session at most once per page load.
 */
import { Button, Codicon, Input, PanelEmpty, PanelListRow, PanelPill, PanelSectionLabel, Skeleton, Tip, host, pluginSettingsHref } from '@hermes/plugin-sdk'
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
const TABS = [['overview', 'Overview'], ['sessions', 'Sessions'], ['git', 'Git'], ['wiki', 'Wiki'], ['access', 'Access']]
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
      h('strong', null, 'Manage projects in Hermes. '),
      'Sidebar \u25B8 Sessions \u25B8 Filters \u25B8 Grouping \u25B8 Project turns on project groups; then + creates a project, a project\u2019s own menu renames, adds folders or archives it, ',
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
  return h('div', { style: { display: 'grid', gap: '1rem' } },
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

function ProjectDetail({ id, onOpenSession, diagnosticUrl }) {
  const [tab, setTab] = useState('overview')
  const [base, reload] = useRequest(id, signal => getJson(`${API}/${encodeURIComponent(id)}`, signal))
  const [related] = useRequest(id && base.status === 'ready' ? `${id}:related` : null, signal => getJson(`${API}/${encodeURIComponent(id)}/related`, signal), [base.data])
  useEffect(() => { setTab('overview') }, [id])
  if (!id) return h(PanelEmpty, { icon: 'project', title: 'Select a project' })
  if (base.status === 'error') return h(Retry, { error: base.error, onRetry: reload })
  if (base.status !== 'ready' || !base.data) return h(Loading, { rows: 5 })
  const d = { ...base.data, ...(related.status === 'ready' ? { related_sessions: related.data.related_sessions, related_pending: false } : related.status === 'error' ? { related_pending: false } : {}) }
  return h('div', { style: { display: 'grid', gap: '0.75rem', alignContent: 'start' } },
    h('div', { style: { display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' } },
      h('h3', { style: { margin: 0, fontSize: '1.05rem', fontWeight: 600 } }, d.name),
      h(PanelPill, { tone: d.permission && d.permission.state === 'granted' ? 'good' : 'muted', children: permissionLabel(d.permission) }),
      d.branch ? h(PanelPill, { children: d.branch }) : null,
      ...(d.sources || []).slice(0, 3).map(src => h(PanelPill, { key: src, children: src })),
      h('span', { style: { flex: 1 } }),
      diagnosticUrl ? h(Tip, { label: 'Opens the standalone Odysseus dashboard (leaves Hermes)', children: h(Button, { size: 'xs', variant: 'ghost', onClick: () => openDiagnostic(), children: 'Diagnostic dashboard' }) }) : null),
    h('div', { role: 'tablist', style: { display: 'flex', gap: '0.25rem', borderBottom: LINE, paddingBottom: '0.25rem' } },
      ...TABS.map(([key, label]) => h(Button, { key, role: 'tab', 'aria-selected': tab === key, size: 'xs', variant: tab === key ? 'secondary' : 'ghost', onClick: () => setTab(key), children: label }))),
    tab === 'overview' ? h(OverviewTab, { d }) : tab === 'sessions' ? h(SessionsTab, { d, onOpen: onOpenSession }) : tab === 'git' ? h(GitTab, { d }) : tab === 'wiki' ? h(WikiTab, { d }) : h(AccessTab, { d }))
}

function ProjectsPage() {
  const [overview, reloadOverview] = useRequest('overview', signal => getJson(API, signal))
  const [tick, setTick] = useState(0)
  const [selected, setSelected] = useState(() => { try { return sessionStorage.getItem('odysseus.projects.selected') || null } catch { return null } })
  const [query, setQuery] = useState('')
  const searchRef = useRef(null)
  useEffect(() => { const t = window.setInterval(() => { if (!document.hidden) reloadOverview() }, OVERVIEW_REFRESH_MS); return () => window.clearInterval(t) }, [reloadOverview])
  const projects = (overview.data && overview.data.projects) || []
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? projects.filter(p => [p.name, p.path, p.branch, ...(p.sources || [])].some(x => x && String(x).toLowerCase().includes(q))) : projects
  }, [projects, query])
  useEffect(() => {
    if (overview.status === 'ready' && projects.length && !projects.some(p => p.id === selected)) setSelected(projects[0].id)
  }, [overview.status, projects, selected])
  useEffect(() => { try { if (selected) sessionStorage.setItem('odysseus.projects.selected', selected) } catch { /* ignore */ } }, [selected])
  const open = useCallback(session => {
    Promise.resolve(host.openSession(session.id)).catch(() => host.notify && host.notify({ kind: 'error', message: 'That conversation could not be opened.' }))
  }, [])
  void tick; void setTick
  const diagnostic = projectsUrl()

  const list = overview.status === 'error' ? h(Retry, { error: overview.error, onRetry: reloadOverview })
    : overview.status === 'loading' && !projects.length ? h(Loading, { rows: 6 })
    : shown.length ? h('div', { role: 'listbox', 'aria-label': 'Projects' }, shown.map(p => h(PanelListRow, {
      key: p.id, rowKey: p.id, active: p.id === selected, onSelect: () => setSelected(p.id), icon: p.kind === 'git' ? 'git-branch' : 'root-folder',
      dotClassName: p.sessions && p.sessions.attention ? 'bg-amber-500' : undefined, title: p.name,
      meta: p.sessions && p.sessions.total ? String(p.sessions.total) : undefined
    })))
    : h(PanelEmpty, { icon: 'search', title: 'No projects match' })

  return h('div', { style: { display: 'grid', gridTemplateColumns: 'minmax(13rem, 17rem) 1fr', gap: '1rem', minHeight: '28rem', alignItems: 'start' } },
    h('div', { style: { display: 'grid', gap: '0.5rem', alignContent: 'start', borderRight: LINE, paddingRight: '0.75rem' } },
      h(Input, { ref: searchRef, value: query, placeholder: 'Search projects', 'aria-label': 'Search projects', onChange: e => setQuery(e.target.value), onKeyDown: e => { if (e.key === 'Escape' && query) { e.stopPropagation(); setQuery('') } } }),
      list),
    h('div', { style: { minWidth: 0 } }, h(ProjectDetail, { id: selected, onOpenSession: open, diagnosticUrl: diagnostic })))
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
