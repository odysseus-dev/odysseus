/**
 * Odysseus: AI Usage inside HermesGUI.
 *
 * A Settings page (plus a sidebar entry and an optional status-bar indicator) that shows
 *   1. account allowances per provider, from the gateway's own `usage.accounts` method (host.request: the Webapp's authenticated gateway socket;
 *      credentials and tokens stay in the gateway and are never part of the reply), and
 *   2. what usage cost, from the read-only route `/__odysseus/api/usage/costs`, which reads Hermes' own stored accounting.
 * Account allowances and local token counts are different things and are never converted into each other. A figure the provider does not
 * expose reads "Not available" with the reason. Nothing here polls per message: one request when the page opens, one every 5 minutes while
 * visible (the gateway itself throttles provider calls to 120 s), and the explicit Refresh button.
 */
import { Button, Codicon, PanelEmpty, PanelPill, PanelSectionLabel, Skeleton, host, pluginSettingsHref } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const COSTS = '/__odysseus/api/usage/costs'
const POLL_MS = 5 * 60 * 1000
const SETTINGS_KEY = 'odysseus.usage.settings'
const MUTED = { color: 'var(--ui-text-tertiary)' }
const LINE = '1px solid color-mix(in srgb, currentColor 14%, transparent)'
const h = (type, props, ...children) => jsx(type, children.length === 0 ? { ...(props || {}) } : { ...(props || {}), children: children.length === 1 ? children[0] : children })

const KIND = {
  billed: { label: 'Billed by provider', help: 'A provider-confirmed charge.' },
  estimated_api: { label: 'Estimated API cost', help: 'Recorded tokens times API list prices. An estimate, not an invoice.' },
  subscription_equivalent: { label: 'API-equivalent value', help: 'What the same usage would cost at API prices. NOT a charge: a subscription is billed separately.' },
  included: { label: 'Included / free', help: 'Free, subscription-included or local. No API charge is invented.' },
  unpriced: { label: 'Unpriced', help: 'No price data for this usage. It is not zero.' }
}
const PERIODS = [['today', 'Today'], ['last_7_days', '7 days'], ['last_30_days', '30 days'], ['all_recorded', 'All recorded']]
const STATE_TONE = { ok: 'good', stale: 'warn', unavailable: 'muted', auth_required: 'warn', unsupported: 'muted' }
const STATE_TEXT = { ok: 'Live', stale: 'Stale', unavailable: 'Unavailable', auth_required: 'Sign-in needed', unsupported: 'Not supported' }

export const usd = (value, kind) => {
  if (kind === 'included') return 'Included'
  if (value === null || value === undefined) return 'Unpriced'
  const body = value !== 0 && Math.abs(value) < 0.01 ? value.toFixed(4) : value.toFixed(2)
  return `${kind === 'billed' ? '' : '~'}$${body}`
}
export const tokens = n => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(Math.round(n || 0)))
export function countdown(iso, now = Date.now()) {
  if (!iso) return null
  const ms = Date.parse(iso) - now
  if (!Number.isFinite(ms)) return null
  if (ms <= 0) return 'resetting now'
  const m = Math.round(ms / 60000)
  if (m < 60) return `resets in ${m} min`
  const hrs = Math.floor(m / 60)
  return hrs < 48 ? `resets in ${hrs} h ${m % 60} min` : `resets in ${Math.round(hrs / 24)} d`
}
export const ageText = s => (s === null || s === undefined ? 'never' : s < 90 ? 'just now' : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`)
export function tightest(provider) {
  const rows = (provider.windows || []).filter(w => w.used_percent !== null && w.scope !== 'model')
  return rows.length ? rows.reduce((a, b) => (b.used_percent > a.used_percent ? b : a)) : null
}
const tone = used => (used >= 90 ? '#c0392b' : used >= 70 ? '#d68910' : 'var(--dt-primary, currentColor)')

function loadSettings() {
  try { return { statusbar: true, pinned: [], ...(JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {}) } } catch { return { statusbar: true, pinned: [] } }
}
function saveSettings(next) {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(next)) } catch { /* private mode */ }
  window.dispatchEvent(new CustomEvent('odysseus-usage-settings'))
}
function useSettings() {
  const [value, setValue] = useState(loadSettings)
  useEffect(() => { const f = () => setValue(loadSettings()); window.addEventListener('odysseus-usage-settings', f); return () => window.removeEventListener('odysseus-usage-settings', f) }, [])
  return [value, patch => saveSettings({ ...loadSettings(), ...patch })]
}

function useGatewayOpen() {
  const state = host.state && host.state.gateway
  const [open, setOpen] = useState(() => !state || typeof state.get !== 'function' || state.get() === 'open')
  useEffect(() => {
    if (!state || typeof state.subscribe !== 'function') return undefined
    const check = () => setOpen(state.get() === 'open')
    const off = state.subscribe(check)
    check()
    return typeof off === 'function' ? off : undefined
  }, [state])
  return open
}

function useAccounts(active = true, pinned = []) {
  const connected = useGatewayOpen()
  const [state, setState] = useState({ status: 'loading', data: null, error: null })
  const alive = useRef(true)
  const pinnedRef = useRef(pinned)
  pinnedRef.current = pinned
  useEffect(() => () => { alive.current = false }, [])
  const load = useCallback(async refresh => {
    try {
      const data = await host.request('usage.accounts', { ...(refresh ? { refresh } : {}), pinned: pinnedRef.current })
      if (alive.current) setState({ status: 'ready', data, error: null })
    } catch (error) {
      if (alive.current) setState(prev => ({ status: 'error', data: prev.data, error }))
    }
  }, [])
  useEffect(() => {
    if (!active || !connected) return undefined
    void load()
    const settle = window.setTimeout(() => { if (!document.hidden) void load() }, 4000)  // providers refresh in the background: pick the results up once
    const timer = window.setInterval(() => { if (!document.hidden) void load() }, POLL_MS)
    return () => { window.clearTimeout(settle); window.clearInterval(timer) }
  }, [active, connected, load])
  return [state, load]
}

function Bar({ label, used, reset, detail, now }) {
  const valid = typeof used === 'number'
  return h('div', { style: { display: 'grid', gap: '0.15rem' } },
    h('div', { style: { display: 'flex', justifyContent: 'space-between', gap: '0.5rem', fontSize: '0.75rem' } },
      h('span', null, label),
      h('span', { style: MUTED }, valid ? `${Math.round(100 - used)}% left \u00b7 ${Math.round(used)}% used` : 'Not available')),
    h('div', { role: 'progressbar', 'aria-label': `${label} used`, 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': valid ? Math.round(used) : undefined,
      style: { height: '0.4rem', borderRadius: '999px', background: 'color-mix(in srgb, currentColor 14%, transparent)', overflow: 'hidden' } },
      valid ? h('div', { style: { width: `${used}%`, height: '100%', background: tone(used) } }) : null),
    (reset || detail) ? h('div', { style: { ...MUTED, fontSize: '0.68rem' } }, [detail, countdown(reset, now)].filter(Boolean).join(' \u00b7 ')) : null)
}

function ProviderCard({ provider, pinned, onPin, onRefresh, busy, now }) {
  const tone2 = STATE_TONE[provider.state] || 'muted'
  const shown = provider.state === 'ok' || provider.state === 'stale'
  return h('section', { 'aria-label': provider.label, 'data-provider': provider.provider, 'data-state': provider.state,
    style: { border: LINE, borderRadius: '0.6rem', padding: '0.7rem 0.8rem', display: 'grid', gap: '0.5rem', minWidth: 0, opacity: provider.state === 'stale' ? 0.85 : 1 } },
    h('div', { style: { display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' } },
      h('strong', { style: { fontSize: '0.85rem', overflowWrap: 'anywhere' } }, provider.label),
      provider.plan ? h(PanelPill, { tone: 'muted' }, provider.plan) : null,
      h(PanelPill, { tone: tone2 }, STATE_TEXT[provider.state] || provider.state),
      h('span', { style: { marginLeft: 'auto', display: 'flex', gap: '0.25rem' } },
        shown ? h(Button, { size: 'xs', variant: 'ghost', 'aria-pressed': pinned, title: pinned ? 'Unpin from the status bar' : 'Pin to the status bar', onClick: onPin }, h(Codicon, { name: pinned ? 'pinned' : 'pin' })) : null,
        provider.can_refresh ? h(Button, { size: 'xs', variant: 'ghost', disabled: busy || provider.refreshing, title: provider.retry_in_s ? `Checked recently; a refresh within ${Math.min(provider.retry_in_s, 20)} s reuses the last reading` : 'Check now', onClick: onRefresh }, busy || provider.refreshing ? 'Checking\u2026' : 'Refresh') : null)),
    shown ? h('div', { style: { display: 'grid', gap: '0.55rem' } },
      ...(provider.windows || []).map(w => h(Bar, { key: w.label, label: w.scope === 'model' ? `${w.label} (one model family)` : w.label, used: w.used_percent, reset: w.resets_at, detail: w.detail, now })),
      ...(provider.details || []).map((d, i) => h('div', { key: i, style: { fontSize: '0.75rem' } }, d)))
      : h('div', { style: { ...MUTED, fontSize: '0.75rem' } }, provider.reason || 'Not available.'),
    provider.reason && shown ? h('div', { style: { fontSize: '0.7rem', color: '#d68910' } }, provider.reason) : null,
    h('div', { style: { ...MUTED, fontSize: '0.66rem', overflowWrap: 'anywhere' } },
      shown ? `Account-level \u00b7 source: ${provider.source || 'provider'} \u00b7 checked ${ageText(provider.age_s)}` : 'Account-level allowance \u00b7 not derived from your token counts'))
}

function Providers({ accounts, reload, settings, setSettings }) {
  const [busy, setBusy] = useState(null)
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 30000); return () => window.clearInterval(t) }, [])
  const refresh = async slug => { setBusy(slug); try { await reload(slug) } finally { setBusy(null) } }
  const togglePin = slug => setSettings({ pinned: settings.pinned.includes(slug) ? settings.pinned.filter(x => x !== slug) : [...settings.pinned, slug] })
  if (accounts.status === 'loading') return h('div', { style: { display: 'grid', gap: '0.5rem' } }, h(Skeleton, { style: { height: '5rem' } }), h(Skeleton, { style: { height: '5rem' } }))
  if (!accounts.data) return h(PanelEmpty, { icon: 'warning', title: 'Account usage could not be read', description: (accounts.error && accounts.error.message) || 'The gateway did not answer.' })
  return h('div', { style: { display: 'grid', gap: '0.6rem' } },
    h('div', { style: { ...MUTED, fontSize: '0.72rem' } }, accounts.data.scope),
    accounts.status === 'error' ? h('div', { style: { fontSize: '0.72rem', color: '#d68910' } }, 'The last refresh failed; showing the previous result.') : null,
    h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 17rem), 1fr))', gap: '0.6rem' } },
      ...accounts.data.providers.map(p => h(ProviderCard, { key: p.provider, provider: p, pinned: settings.pinned.includes(p.provider), onPin: () => togglePin(p.provider), onRefresh: () => refresh(p.provider), busy: busy === p.provider, now }))))
}

function useCosts() {
  const [state, setState] = useState({ status: 'loading', data: null, error: null })
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])
  const load = useCallback(async () => {
    try {
      const response = await fetch(COSTS, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      if (!response.ok) throw new Error(response.status === 401 || response.status === 403 ? 'Open Hermes again from Odysseus to see costs.' : `Request failed (${response.status})`)
      const data = await response.json()
      if (alive.current) setState({ status: 'ready', data, error: null })
    } catch (error) {
      if (alive.current) setState(prev => ({ status: 'error', data: prev.data, error }))
    }
  }, [])
  useEffect(() => { void load() }, [load])
  return [state, load]
}

function KindRow({ row }) {
  const meta = KIND[row.kind] || { label: row.kind, help: '' }
  const sources = (row.price_sources || []).map(s => s.label).join(', ')
  return h('div', { title: meta.help, style: { display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', gap: '0.1rem 0.75rem', padding: '0.3rem 0', borderTop: LINE, fontSize: '0.78rem' } },
    h('span', null, meta.label),
    h('strong', { style: { textAlign: 'right' } }, usd(row.usd, row.kind)),
    h('span', { style: { ...MUTED, fontSize: '0.68rem', gridColumn: '1 / -1', overflowWrap: 'anywhere' } },
      `${row.sessions} conversation${row.sessions === 1 ? '' : 's'} \u00b7 ${row.calls} calls${row.aux_calls ? ` (${row.aux_calls} auxiliary)` : ''} \u00b7 in ${tokens(row.input)} \u00b7 out ${tokens(row.output)} \u00b7 cache read ${tokens(row.cache_read)} \u00b7 cache write ${tokens(row.cache_write)}${row.reasoning ? ` \u00b7 reasoning ${tokens(row.reasoning)}` : ''}${sources ? ` \u00b7 price source: ${sources}` : ''}`))
}

function Costs({ costs, reload }) {
  const [period, setPeriod] = useState('last_7_days')
  const [more, setMore] = useState(false)
  if (costs.status === 'loading') return h(Skeleton, { style: { height: '8rem' } })
  if (!costs.data || costs.data.available === false) return h(PanelEmpty, { icon: 'graph', title: 'Cost history unavailable', description: (costs.data && costs.data.reason) || (costs.error && costs.error.message) || 'The usage records could not be read.' })
  const p = costs.data.periods[period]
  return h('div', { style: { display: 'grid', gap: '0.55rem' } },
    h('div', { role: 'tablist', 'aria-label': 'Period', style: { display: 'flex', gap: '0.3rem', flexWrap: 'wrap' } },
      ...PERIODS.map(([id, text]) => h(Button, { key: id, size: 'xs', role: 'tab', 'aria-selected': period === id, variant: period === id ? 'secondary' : 'ghost', onClick: () => setPeriod(id) }, text)),
      h(Button, { size: 'xs', variant: 'ghost', onClick: reload, style: { marginLeft: 'auto' } }, 'Reload')),
    h('div', { style: { ...MUTED, fontSize: '0.7rem' } }, costs.data.scope),
    period !== 'all_recorded' && p.sessions_begun_earlier ? h('div', { style: { fontSize: '0.7rem', color: '#d68910' } }, `${p.sessions_begun_earlier} of ${p.sessions} conversations began before this period. Their whole recorded totals are counted here, so this is not exact daily spending.`) : null,
    p.by_class.length === 0 ? h(PanelEmpty, { icon: 'graph', title: 'No usage recorded in this period' }) : h('div', null, ...p.by_class.map(r => h(KindRow, { key: r.kind, row: r }))),
    h('div', { style: { ...MUTED, fontSize: '0.68rem' } }, 'The kinds above are never added together: only \u201cBilled by provider\u201d was actually charged.'),
    p.by_model.length ? h('div', null,
      h(Button, { size: 'xs', variant: 'ghost', 'aria-expanded': more, onClick: () => setMore(v => !v) }, more ? 'Hide breakdown' : 'Show per-model, profile and project breakdown'),
      more ? h('div', { style: { display: 'grid', gap: '0.6rem', marginTop: '0.4rem' } },
        h('div', null, h(PanelSectionLabel, null, 'By provider and model'),
          ...p.by_model.map((m, i) => h('div', { key: i, style: { display: 'flex', justifyContent: 'space-between', gap: '0.75rem', fontSize: '0.74rem', padding: '0.15rem 0', flexWrap: 'wrap' } },
            h('span', { style: { overflowWrap: 'anywhere', minWidth: 0 } }, `${m.provider || 'unknown route'} \u00b7 ${m.model || 'unknown model'}`),
            h('span', { title: (KIND[m.kind] || {}).help }, `${usd(m.usd, m.kind)} \u00b7 ${(KIND[m.kind] || { label: m.kind }).label}`)))),
        p.by_profile.length ? h('div', null, h(PanelSectionLabel, null, 'By profile'), ...p.by_profile.map((m, i) => h('div', { key: i, style: { display: 'flex', justifyContent: 'space-between', gap: '0.75rem', fontSize: '0.74rem', flexWrap: 'wrap' } }, h('span', null, m.profile), h('span', null, `${usd(m.usd, m.kind)} \u00b7 ${(KIND[m.kind] || { label: m.kind }).label}`)))) : null,
        p.by_project.length ? h('div', null, h(PanelSectionLabel, null, 'By project'), h('div', { style: { ...MUTED, fontSize: '0.66rem' } }, costs.data.project_attribution), ...p.by_project.map((m, i) => h('div', { key: i, style: { display: 'flex', justifyContent: 'space-between', gap: '0.75rem', fontSize: '0.74rem', flexWrap: 'wrap' } }, h('span', null, m.project), h('span', null, `${usd(m.usd, m.kind)} \u00b7 ${(KIND[m.kind] || { label: m.kind }).label}`)))) : null) : null) : null)
}

function UsagePage() {
  const [settings, setSettings] = useSettings()
  const [accounts, reload] = useAccounts(true, settings.pinned)
  const [costs, reloadCosts] = useCosts()
  const rootRef = useRef(null)
  const [narrow, setNarrow] = useState(false)
  useEffect(() => {
    const node = rootRef.current
    if (!node || typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(entries => setNarrow(entries[0].contentRect.width < 560))
    observer.observe(node)
    return () => observer.disconnect()
  }, [])
  const pinnedKey = useMemo(() => settings.pinned.join(','), [settings.pinned])
  useEffect(() => { void reload() }, [pinnedKey, reload])
  const heading = (text, hint) => h('div', { style: { margin: '0.2rem 0 0.4rem' } }, h('h3', { style: { fontSize: '0.9rem', fontWeight: 600, margin: 0 } }, text), hint ? h('div', { style: { ...MUTED, fontSize: '0.7rem' } }, hint) : null)
  return h('div', { ref: rootRef, 'data-slot': 'odysseus-usage-page', style: { display: 'grid', gap: '1.2rem', minWidth: 0, maxWidth: '100%', padding: narrow ? '0' : '0 0.25rem' } },
    h('div', null, heading('Provider allowances', 'What each provider says you have left. Checked through Hermes; your credentials never reach this page.'),
      h(Providers, { accounts, reload, settings, setSettings })),
    h('div', null, heading('Cost history', 'From Hermes\u2019 own usage records on this computer, priced when each call happened.'), h(Costs, { costs, reload: reloadCosts })),
    h('div', null, heading('Status bar'),
      h('label', { style: { display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.78rem' } },
        h('input', { type: 'checkbox', checked: settings.statusbar, onChange: e => setSettings({ statusbar: e.target.checked }) }),
        'Show allowance indicators in the status bar'),
      h('div', { style: { ...MUTED, fontSize: '0.7rem' } }, 'Shows the active provider, plus any provider you pin with the pin button on its card.')))
}

function StatusItem() {
  const [settings] = useSettings()
  const [accounts] = useAccounts(settings.statusbar, settings.pinned)
  if (!settings.statusbar || !accounts.data) return null
  const want = new Set(settings.pinned)
  if (accounts.data.current_provider) want.add(accounts.data.current_provider)
  const rows = accounts.data.providers.filter(p => want.has(p.provider) && (p.state === 'ok' || p.state === 'stale')).map(p => ({ p, w: tightest(p) })).filter(x => x.w)
  if (!rows.length) return null
  return h('button', { type: 'button', 'data-slot': 'odysseus-usage-status', title: rows.map(({ p, w }) => `${p.label}: ${w.label} ${Math.round(w.used_percent)}% used, ${countdown(w.resets_at) || 'no reset time'}${p.state === 'stale' ? ' (stale)' : ''}`).join('\n') + '\nClick for AI Usage',
    'aria-label': 'AI usage allowances. Open AI Usage', onClick: () => host.navigate(pluginSettingsHref('odysseus-usage')),
    style: { display: 'inline-flex', gap: '0.5rem', alignItems: 'center', background: 'none', border: 0, color: 'inherit', cursor: 'pointer', font: 'inherit', padding: '0 0.25rem', maxWidth: '40vw', overflow: 'hidden', whiteSpace: 'nowrap' } },
    ...rows.map(({ p, w }) => h('span', { key: p.provider, style: { opacity: p.state === 'stale' ? 0.6 : 1 } },
      `${p.label.replace(/ \(.*$/, '').split(' / ')[0]} ${Math.round(w.used_percent)}%`, p.state === 'stale' ? '?' : '')))
}

export default {
  id: 'odysseus-usage',
  name: 'Odysseus: AI Usage',
  register(ctx) {
    if (ctx && typeof ctx.registerSettingsPage === 'function') {
      ctx.registerSettingsPage({ id: 'usage', title: 'AI Usage', icon: 'graph', order: 6, render: () => jsx(UsagePage, {}) })
    }
    if (ctx && typeof ctx.registerMany === 'function') {
      ctx.registerMany([
        { id: 'usage-nav', area: 'sidebar.nav', order: 45, data: { codicon: 'graph', label: 'AI Usage', path: pluginSettingsHref('odysseus-usage') } },
        { id: 'usage-status', area: 'statusBar.right', order: 70, render: () => jsx(StatusItem, {}) }
      ])
    }
  }
}
