import { useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Trash2, UserPlus, Plus, Pencil, SlidersHorizontal, UserCircle, Boxes, Plug, Users, Server, Webhook, Wrench, Sparkles, ChevronLeft } from "lucide-react"
import { useUi } from "@/stores/ui"
import { useAuthStatus, useUsers, useUserMutations, logout, changePassword, setup2FA, confirm2FA, disable2FA, useTwoFAStatus, setOpenSignup } from "@/api/auth"
import { Switch } from "@/components/ui/switch"
import { useModels, useDefaultChat, useDeleteEndpoint, useEndpointMutations, useSetDefaultModel, testEndpoint } from "@/api/models"
import { usePresetConfig, useCustomPresetMutations, useExpandPreset, type PresetConfig } from "@/api/presets"
import { AdminSections } from "@/components/settings/AdminSections"
import { AppSettingsSections, SidebarItemsSettings } from "@/components/settings/AppSettings"
import { IntegrationsExtraSections } from "@/components/settings/IntegrationsExtra"
import { AdvancedSections } from "@/components/settings/AdvancedSettings"
import { PersonalizationSection } from "@/components/settings/Personalization"
import { UserPrivileges } from "@/components/settings/UserPrivileges"
import { useSetUserAdmin, useProviders, useDeviceFlow, COPILOT_PROVIDER, CHATGPT_PROVIDER, type DeviceFlowProvider } from "@/api/advanced"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const inpCls = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"

// Device-flow "Connect" button for subscription providers (Copilot / ChatGPT).
// Replicates the chat composer's `/setup` flow: start → show code + open the
// verification tab → poll to completion. Admin-only on the backend.
function DeviceConnectButton({ provider }: { provider: DeviceFlowProvider }) {
  const { state, start, cancel, reset } = useDeviceFlow(provider)
  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium">{provider.label}</div>
          <div className="text-xs text-muted-foreground">Sign in with your subscription via device authorization.</div>
        </div>
        {state.active
          ? <Button variant="outline" size="sm" onClick={cancel}>Cancel</Button>
          : <Button variant="outline" size="sm" onClick={start}>Connect</Button>}
      </div>
      {(state.active || state.message || state.error || state.userCode) && (
        <div className="mt-2 space-y-1 border-t pt-2 text-xs">
          {state.userCode && (
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground">Code</span>
              <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-sm tracking-widest">{state.userCode}</code>
              {state.verifyUrl && <a href={state.verifyUrl} target="_blank" rel="noopener noreferrer" className="text-foreground underline underline-offset-2">Open authorization page</a>}
            </div>
          )}
          {state.message && <p className={cn("text-muted-foreground", state.done && "text-emerald-600 dark:text-emerald-400")}>{state.message}</p>}
          {state.error && <p className="text-destructive">{state.error}</p>}
          {(state.done || state.error) && <button onClick={reset} className="text-muted-foreground underline underline-offset-2 hover:text-foreground">Dismiss</button>}
        </div>
      )}
    </div>
  )
}

function DeviceConnectSection() {
  return (
    <section>
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Connect provider accounts <span className="normal-case text-muted-foreground/70">(admin)</span></h2>
      <div className="space-y-2">
        <DeviceConnectButton provider={COPILOT_PROVIDER} />
        <DeviceConnectButton provider={CHATGPT_PROVIDER} />
      </div>
    </section>
  )
}

export function AccountSecurity() {
  const [cur, setCur] = useState("")
  const [nw, setNw] = useState("")
  const [pwMsg, setPwMsg] = useState("")
  const [qr, setQr] = useState<string | null>(null)
  const [code, setCode] = useState("")
  const [twoMsg, setTwoMsg] = useState("")
  const [backupCodes, setBackupCodes] = useState<string[]>([])
  const [backupSaved, setBackupSaved] = useState(false)
  const { data: twoFA } = useTwoFAStatus()
  const qc = useQueryClient()
  const doChange = async () => {
    if (nw.length < 8) { setPwMsg("New password must be 8+ chars"); return }
    const r = await changePassword(cur, nw)
    if (r.ok) { setPwMsg("Password changed."); setCur(""); setNw("") } else setPwMsg(r.error || "Failed")
  }
  const startTwo = async () => { const r = await setup2FA(); if (r.qr_code) { setQr(r.qr_code); setTwoMsg("") } else setTwoMsg(r.error || "2FA unavailable") }
  const confirmTwo = async () => {
    const r = await confirm2FA(code)
    if (r.ok) {
      setTwoMsg("2FA enabled. Save the recovery codes below before leaving this page.")
      setBackupCodes(r.backup_codes || [])
      setBackupSaved(false)
      setQr(null); setCode("")
      qc.invalidateQueries({ queryKey: ["2fa-status"] })
    } else setTwoMsg(r.error || "Invalid code")
  }
  const backupText = backupCodes.join("\n")
  const copyBackupCodes = async () => { try { await navigator.clipboard.writeText(backupText) } catch { /* ignore */ } }
  const downloadBackupCodes = () => {
    const url = URL.createObjectURL(new Blob([`Odysseus 2FA recovery codes\n\n${backupText}\n`], { type: "text/plain" }))
    const a = document.createElement("a"); a.href = url; a.download = "odysseus-recovery-codes.txt"; a.click(); URL.revokeObjectURL(url)
  }
  const doDisable = async () => {
    const pw = prompt("Enter your password to disable 2FA")
    if (!pw) return
    const r = await disable2FA(pw)
    if (r.ok) { setTwoMsg("2FA disabled."); qc.invalidateQueries({ queryKey: ["2fa-status"] }) } else setTwoMsg(r.error || "Failed")
  }
  return (
    <section>
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Security</h2>
      <div className="space-y-3 rounded-lg border bg-card p-3">
        <div className="space-y-2">
          <div className="text-sm font-medium">Change password</div>
          <input value={cur} onChange={(e) => setCur(e.target.value)} type="password" placeholder="Current password" autoComplete="current-password" className={inpCls} />
          <input value={nw} onChange={(e) => setNw(e.target.value)} type="password" placeholder="New password (8+ chars)" autoComplete="new-password" className={inpCls} />
          {pwMsg && <p className="text-xs text-muted-foreground">{pwMsg}</p>}
          <div className="flex justify-end"><Button size="sm" variant="outline" onClick={doChange} disabled={!cur || !nw}>Update password</Button></div>
        </div>
        <div className="border-t pt-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">Two-factor (TOTP){twoFA?.enabled && <span className="ml-1.5 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-normal text-emerald-600 dark:text-emerald-400">Enabled</span>}</span>
            {twoFA?.enabled ? <Button size="sm" variant="outline" onClick={doDisable}>Disable</Button> : (!qr && <Button size="sm" variant="outline" onClick={startTwo}>Enable 2FA</Button>)}
          </div>
          {qr && (
            <div className="mt-2 space-y-2">
              <img src={qr} alt="2FA QR" className="size-40 rounded-md border bg-white p-1" />
              <div className="flex gap-2"><input value={code} onChange={(e) => setCode(e.target.value)} placeholder="6-digit code" className={inpCls} /><Button size="sm" onClick={confirmTwo}>Confirm</Button></div>
            </div>
          )}
          {twoMsg && <p className="mt-1 text-xs text-muted-foreground">{twoMsg}</p>}
          {backupCodes.length > 0 && (
            <div className="mt-3 space-y-3 rounded-lg border bg-background p-3" role="region" aria-label="Two-factor recovery codes">
              <div><p className="text-sm font-medium">Recovery codes</p><p className="text-xs text-muted-foreground">Each code works once. Store them somewhere safe; they will not be shown again.</p></div>
              <div className="grid grid-cols-1 gap-1 rounded-md bg-muted p-3 font-mono text-sm sm:grid-cols-2" data-testid="backup-codes">
                {backupCodes.map((backup) => <code key={backup}>{backup}</code>)}
              </div>
              <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={copyBackupCodes}>Copy codes</Button><Button size="sm" variant="outline" onClick={downloadBackupCodes}>Download .txt</Button></div>
              <label className="flex items-start gap-2 text-xs"><input type="checkbox" className="mt-0.5" checked={backupSaved} onChange={(e) => setBackupSaved(e.target.checked)} /><span>I saved these recovery codes.</span></label>
              <Button size="sm" disabled={!backupSaved} onClick={() => { setBackupCodes([]); setTwoMsg("2FA enabled.") }}>Done</Button>
            </div>
          )}
        </div>
        <div className="flex items-center justify-between border-t pt-3">
          <span className="text-sm font-medium">Export my data</span>
          <Button size="sm" variant="outline" onClick={() => window.open("/api/export", "_blank")}>Download JSON</Button>
        </div>
      </div>
    </section>
  )
}

// Form used for both "new" and "edit" of a custom preset. Exposes the full set
// of fields the backend supports, including inject_prefix/inject_suffix, plus an
// "AI expand" action that turns a rough description into a full system prompt.
function PresetEditor({ initial, onCancel, onSaved }: { initial?: PresetConfig & { id?: string }; onCancel: () => void; onSaved: () => void }) {
  const { save } = useCustomPresetMutations()
  const expand = useExpandPreset()
  const [name, setName] = useState(initial?.character_name || initial?.name || "")
  const [sys, setSys] = useState(initial?.system_prompt || "")
  const [prefix, setPrefix] = useState(initial?.inject_prefix || "")
  const [suffix, setSuffix] = useState(initial?.inject_suffix || "")
  const [temp, setTemp] = useState(String(initial?.temperature ?? 1.0))
  const [maxTok, setMaxTok] = useState(String(initial?.max_tokens ?? 0))
  const [err, setErr] = useState("")
  const taCls = "w-full resize-y rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring"
  const doExpand = () => {
    setErr("")
    expand.mutate({ name: name.trim(), prompt: sys.trim() }, {
      onSuccess: (d) => { if (d.prompt) setSys(d.prompt) },
      onError: (e) => setErr(e instanceof Error ? e.message : "Expand failed"),
    })
  }
  const submit = () => {
    if (!name.trim()) { setErr("Name required"); return }
    setErr("")
    save.mutate({ name: name.trim(), system_prompt: sys, inject_prefix: prefix, inject_suffix: suffix, temperature: parseFloat(temp) || 1, max_tokens: parseInt(maxTok) || 0, enabled: true }, {
      onSuccess: onSaved,
      onError: (e) => setErr(e instanceof Error ? e.message : "Failed"),
    })
  }
  return (
    <div className="mt-2 space-y-2 rounded-lg border bg-card p-3">
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Preset name" className={inpCls} />
      <div className="space-y-1">
        <textarea value={sys} onChange={(e) => setSys(e.target.value)} placeholder="System prompt (optional)" rows={3} className={taCls} />
        <div className="flex justify-end">
          <Button variant="ghost" size="sm" disabled={expand.isPending || (!name.trim() && !sys.trim())} onClick={doExpand}>
            <Sparkles className="size-4" />{expand.isPending ? "Expanding…" : "AI expand"}
          </Button>
        </div>
      </div>
      <label className="block text-xs text-muted-foreground">Inject prefix (prepended to each message)
        <textarea value={prefix} onChange={(e) => setPrefix(e.target.value)} rows={2} placeholder="Optional" className={cn(taCls, "mt-1")} /></label>
      <label className="block text-xs text-muted-foreground">Inject suffix (appended to each message)
        <textarea value={suffix} onChange={(e) => setSuffix(e.target.value)} rows={2} placeholder="Optional" className={cn(taCls, "mt-1")} /></label>
      <div className="flex gap-2">
        <label className="flex-1 text-xs text-muted-foreground">Temperature<input value={temp} onChange={(e) => setTemp(e.target.value)} type="number" min={0} max={2} step={0.1} className={cn(inpCls, "mt-1")} /></label>
        <label className="flex-1 text-xs text-muted-foreground">Max tokens (0=auto)<input value={maxTok} onChange={(e) => setMaxTok(e.target.value)} type="number" min={0} className={cn(inpCls, "mt-1")} /></label>
      </div>
      {err && <p className="text-xs text-destructive">{err}</p>}
      <div className="flex justify-end gap-2"><Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button><Button size="sm" disabled={save.isPending} onClick={submit}>{save.isPending ? "Saving…" : "Save preset"}</Button></div>
    </div>
  )
}

function PresetSection() {
  const { data: config } = usePresetConfig()
  const { disable } = useCustomPresetMutations()
  const [editing, setEditing] = useState<(PresetConfig & { id: string }) | null>(null)
  const [creating, setCreating] = useState(false)
  // Surface every enabled custom preset from the config map.
  const presets = Object.entries(config || {})
    .map(([id, c]) => ({ id, ...(c as PresetConfig) }))
    .filter((p) => p.enabled !== false && (p.character_name || p.name || p.system_prompt))
  return (
    <section>
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Presets</h2>
      <div className="space-y-2">
        {presets.map((p) => (
          <div key={p.id} className="group rounded-lg border bg-card p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate text-sm font-medium">{p.character_name || p.name || p.id}</span>
              <div className="flex items-center gap-1.5 opacity-0 transition-opacity group-hover:opacity-100">
                <button onClick={() => { setCreating(false); setEditing(p) }} title="Edit" className="text-muted-foreground hover:text-foreground"><Pencil className="size-4" /></button>
                <button onClick={() => { if (confirm(`Delete preset "${p.character_name || p.name || p.id}"?`)) disable.mutate(p) }} title="Delete" className="text-muted-foreground hover:text-destructive"><Trash2 className="size-4" /></button>
              </div>
            </div>
            {p.system_prompt && <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{p.system_prompt}</p>}
            {editing?.id === p.id && <PresetEditor initial={editing} onCancel={() => setEditing(null)} onSaved={() => setEditing(null)} />}
          </div>
        ))}
        {presets.length === 0 && <p className="py-1 text-sm text-muted-foreground">No presets yet.</p>}
      </div>
      {creating
        ? <PresetEditor onCancel={() => setCreating(false)} onSaved={() => setCreating(false)} />
        : <Button variant="outline" size="sm" className="mt-2" onClick={() => { setEditing(null); setCreating(true) }}><Plus className="size-4" />New preset</Button>}
    </section>
  )
}

function AddEndpointForm() {
  const { create } = useEndpointMutations()
  const { data: providers } = useProviders()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [url, setUrl] = useState("")
  const [key, setKey] = useState("")
  const [msg, setMsg] = useState("")
  const [testing, setTesting] = useState(false)
  const test = async () => {
    if (!url.trim()) { setMsg("Base URL required"); return }
    setTesting(true); setMsg("Testing…")
    try { const r = await testEndpoint({ base_url: url.trim(), api_key: key.trim() }); setMsg(r.error ? `Failed: ${r.error}` : `Reachable · ${r.models?.length ?? 0} models`) }
    catch { setMsg("Test failed") } finally { setTesting(false) }
  }
  const add = () => {
    if (!url.trim()) { setMsg("Base URL required"); return }
    create.mutate({ name: name.trim(), base_url: url.trim(), api_key: key.trim() }, {
      onSuccess: () => { setName(""); setUrl(""); setKey(""); setMsg(""); setOpen(false) },
      onError: (e: unknown) => setMsg(e instanceof Error ? e.message : "Add failed"),
    })
  }
  if (!open) return <Button variant="outline" size="sm" className="mt-2" onClick={() => setOpen(true)}><Plus className="size-4" />Add endpoint</Button>
  return (
    <div className="mt-2 space-y-2 rounded-lg border bg-card p-3">
      {(providers || []).length > 0 && (
        <select defaultValue="" onChange={(e) => { const p = (providers || []).find((x) => x.provider === e.target.value); if (p) { setName(p.provider); if (p.items?.[0]?.url) setUrl(p.items[0].url) } }} className={inpCls}>
          <option value="">Provider preset…</option>
          {(providers || []).map((p) => <option key={p.provider} value={p.provider}>{p.provider}</option>)}
        </select>
      )}
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (optional, e.g. OpenAI)" className={inpCls} />
      <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="Base URL — e.g. https://api.openai.com/v1" className={inpCls} />
      <input value={key} onChange={(e) => setKey(e.target.value)} type="password" placeholder="API key (optional for local)" autoComplete="off" className={inpCls} />
      {msg && <p className="text-xs text-muted-foreground">{msg}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>Cancel</Button>
        <Button variant="outline" size="sm" disabled={testing} onClick={test}>Test</Button>
        <Button size="sm" disabled={create.isPending} onClick={add}>{create.isPending ? "Adding…" : "Add"}</Button>
      </div>
    </div>
  )
}

export function SettingsRoute() {
  const { theme, setTheme, accent, setAccent, font, setFont, density, setDensity } = useUi()
  const { data: status } = useAuthStatus()
  const qc = useQueryClient()
  const { data: models } = useModels()
  const { data: def } = useDefaultChat()
  const setDefault = useSetDefaultModel()
  const { data: users } = useUsers()
  const del = useDeleteEndpoint()
  const endpointMutations = useEndpointMutations()
  const allModels = (models?.items || []).flatMap((e) => [...(e.models || []), ...(e.models_extra || [])])
  const { create: createUser, remove: removeUser, rename: renameUser } = useUserMutations()
  const setAdmin = useSetUserAdmin()
  const user = status?.username || status?.user || "—"
  const endpoints = (models?.items || []).filter((e) => e.endpoint_id)
  const userList = users || []
  const [nu, setNu] = useState("")
  const [np, setNp] = useState("")
  const [nAdmin, setNAdmin] = useState(false)
  const [uErr, setUErr] = useState("")
  const [discovering, setDiscovering] = useState(false)
  const [discoveryMsg, setDiscoveryMsg] = useState("")
  const addUser = () => {
    if (!nu.trim() || np.length < 8) { setUErr("Username and 8+ char password required"); return }
    setUErr("")
    createUser.mutate({ username: nu.trim(), password: np, is_admin: nAdmin }, {
      onSuccess: () => { setNu(""); setNp(""); setNAdmin(false) },
      onError: (e: unknown) => setUErr(e instanceof Error ? e.message : "Failed"),
    })
  }
  const discoverEndpoints = async () => {
    setDiscovering(true); setDiscoveryMsg("Scanning common local model ports…")
    try {
      const response = await fetch("/api/discover", { credentials: "same-origin" })
      const data = await response.json() as { items?: Array<{ url?: string; models?: string[] }> }
      if (!response.ok) throw new Error(data && "detail" in data ? String(data.detail) : `HTTP ${response.status}`)
      const items = data.items || []
      let added = 0
      for (const item of items) {
        if (!item.url) continue
        const base_url = item.url.replace("/chat/completions", "").replace(/\/$/, "")
        try { await endpointMutations.create.mutateAsync({ name: "Discovered local", base_url }); added++ } catch { /* backend deduplicates existing endpoints */ }
      }
      await qc.invalidateQueries({ queryKey: ["models"] })
      setDiscoveryMsg(items.length ? `Found ${items.length} server${items.length === 1 ? "" : "s"}; added ${added}.` : "No local model servers found.")
    } catch (error) { setDiscoveryMsg(error instanceof Error ? error.message : "Discovery failed.") }
    finally { setDiscovering(false) }
  }
  const probeLocalEndpoints = async () => {
    setDiscoveryMsg("Probing local endpoints…")
    try {
      const response = await fetch("/api/model-endpoints/probe-local", { credentials: "same-origin" })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const result = await response.json() as Record<string, { alive?: boolean }>
      const values = Object.values(result)
      setDiscoveryMsg(`${values.filter((v) => v.alive).length}/${values.length} local endpoints online.`)
      await qc.invalidateQueries({ queryKey: ["models"] })
    } catch (error) { setDiscoveryMsg(error instanceof Error ? error.message : "Probe failed.") }
  }

  const isAdmin = !!status?.is_admin
  const NAV = [
    { id: "general", label: "General", icon: SlidersHorizontal },
    { id: "personalization", label: "Personalization", icon: Sparkles },
    { id: "account", label: "Account", icon: UserCircle },
    { id: "models", label: "Models", icon: Boxes },
    { id: "integrations", label: "Integrations", icon: Plug },
    { id: "users", label: "Users", icon: Users, admin: true },
    { id: "system", label: "System", icon: Server, admin: true },
    { id: "tools", label: "Tools & webhooks", icon: Webhook, admin: true },
    { id: "advanced", label: "Advanced", icon: Wrench, admin: true },
  ].filter((n) => !n.admin || isAdmin)
  const [page, setPage] = useState(() => {
    const requested = new URLSearchParams(window.location.search).get("section")?.toLowerCase() || "general"
    return NAV.some((n) => n.id === requested) ? requested : "general"
  })
  const current = NAV.find((n) => n.id === page) || NAV[0]
  // On mobile the master-detail collapses to a single pane: false = section
  // list, true = section detail. Desktop (md+) shows both panes regardless.
  const [mobileDetail, setMobileDetail] = useState(false)
  const navRow = (active: boolean) =>
    cn("flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
      active ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground")

  const appearanceSection = (
    <section data-tour="settings-appearance">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Appearance</h2>
      <div className="space-y-3 rounded-lg border bg-card p-3">
        <div className="flex items-center justify-between">
          <span className="text-sm">Theme</span>
          <div className="flex rounded-lg bg-muted p-0.5">
            {(["light", "dark"] as const).map((t) => (
              <button key={t} onClick={() => setTheme(t)} className={cn("rounded-md px-3 py-1 text-sm capitalize transition-colors", theme === t ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>{t}</button>
            ))}
          </div>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-sm">Accent</span>
          <div className="flex flex-wrap items-center justify-end gap-1.5">
            {["", "#2563eb", "#7c3aed", "#db2777", "#059669", "#ea580c"].map((c) => (
              <button key={c || "default"} onClick={() => setAccent(c)} title={c || "Default (zinc)"}
                className={cn("size-6 rounded-full border", accent === c && "ring-2 ring-ring ring-offset-2 ring-offset-card")}
                style={{ background: c || "var(--muted-foreground)" }} />
            ))}
            <input type="color" value={accent || "#000000"} onChange={(e) => setAccent(e.target.value)} title="Custom" className="size-6 cursor-pointer rounded-full border bg-transparent p-0" />
          </div>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-sm">Font</span>
          <div className="flex rounded-lg bg-muted p-0.5">
            {(["sans", "serif", "mono"] as const).map((f) => (
              <button key={f} onClick={() => setFont(f)} className={cn("rounded-md px-3 py-1 text-sm capitalize transition-colors", font === f ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>{f}</button>
            ))}
          </div>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-sm">Density</span>
          <div className="flex rounded-lg bg-muted p-0.5">
            {(["compact", "comfortable", "spacious"] as const).map((d) => (
              <button key={d} onClick={() => setDensity(d)} className={cn("rounded-md px-2.5 py-1 text-sm capitalize transition-colors", density === d ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>{d}</button>
            ))}
          </div>
        </div>
      </div>
    </section>
  )

  const aiDefaultsSection = (
    <section data-tour="settings-models">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">AI defaults</h2>
      <div className="flex flex-col gap-2 rounded-lg border bg-card p-3 sm:flex-row sm:items-center sm:justify-between sm:gap-0">
        <span className="text-sm">Default chat model</span>
        <select value={def?.model || ""} onChange={(e) => setDefault.mutate(e.target.value)} className="h-9 w-full rounded-md border bg-background px-2 text-sm outline-none focus-visible:border-ring sm:max-w-[60%]">
          {!def?.model && <option value="">—</option>}
          {allModels.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </div>
    </section>
  )

  const endpointsSection = (
    <section data-tour="settings-endpoints">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Model endpoints</h2>
      <div className="space-y-2">
        {endpoints.map((e) => (
          <div key={e.endpoint_id} className="group flex items-center gap-3 rounded-lg border bg-card p-3">
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{e.endpoint_name || e.url}</div>
              <div className="truncate text-xs text-muted-foreground">{e.url} · {(e.models?.length || 0) + (e.models_extra?.length || 0)} models{e.category ? ` · ${e.category}` : ""}</div>
            </div>
            {isAdmin && (
              <button onClick={() => { if (confirm("Delete this endpoint?")) del.mutate(e.endpoint_id) }} className="text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"><Trash2 className="size-4" /></button>
            )}
          </div>
        ))}
        {endpoints.length === 0 && <p className="py-2 text-sm text-muted-foreground">{isAdmin ? "No saved endpoints." : "No model endpoints are available for your account."}</p>}
      </div>
      {!isAdmin && <p className="mt-2 rounded-md border bg-muted/30 p-2 text-xs text-muted-foreground">Model endpoint management is admin-only. You can still use any models shared with your account.</p>}
      {isAdmin && <div className="mt-2 flex flex-wrap gap-2"><AddEndpointForm /><Button variant="outline" size="sm" disabled={discovering} onClick={discoverEndpoints}>{discovering ? "Scanning…" : "Discover local"}</Button><Button variant="outline" size="sm" onClick={probeLocalEndpoints}>Probe local</Button></div>}
      {isAdmin && discoveryMsg && <p className="mt-2 text-xs text-muted-foreground">{discoveryMsg}</p>}
    </section>
  )

  const usersSection = userList.length > 0 && (
    <section>
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Users</h2>
      <div className="mb-2 flex items-center justify-between rounded-lg border bg-card p-3">
        <span className="min-w-0"><span className="block text-sm">Open registration</span><span className="block text-xs text-muted-foreground">Allow new users to sign up</span></span>
        <Switch checked={!!status?.signup_enabled} onCheckedChange={async (v) => { await setOpenSignup(v); qc.invalidateQueries({ queryKey: ["auth-status"] }) }} />
      </div>
      <div className="space-y-2">
        {userList.map((u) => (
          <div key={u.username} className="rounded-lg border bg-card p-3">
            <div className="group flex items-center justify-between">
            <span className="text-sm font-medium">{u.username}{u.username === user && <span className="ml-1.5 text-xs font-normal text-muted-foreground">(you)</span>}</span>
            <div className="flex items-center gap-2">
              {u.username === user
                ? (u.is_admin && <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">admin</span>)
                : (
                  <>
                    <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground" title="Administrator">admin<Switch checked={!!u.is_admin} onCheckedChange={(v) => setAdmin.mutate({ username: u.username, is_admin: v })} /></label>
                    <button onClick={() => { const n = prompt("Rename user", u.username); if (n && n.trim() && n.trim() !== u.username) renameUser.mutate({ username: u.username, new_username: n.trim() }) }} className="text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover:opacity-100" title="Rename user"><Pencil className="size-4" /></button>
                    <button onClick={() => { if (confirm(`Delete user "${u.username}"?`)) removeUser.mutate(u.username) }} className="text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100" title="Delete user"><Trash2 className="size-4" /></button>
                  </>
                )}
            </div>
            </div>
            {u.username !== user && !u.is_admin && <UserPrivileges user={u} />}
          </div>
        ))}
      </div>
      <div className="mt-2 space-y-2 rounded-lg border bg-card p-3">
        <div className="flex flex-col gap-2 sm:flex-row">
          <input value={nu} onChange={(e) => setNu(e.target.value)} placeholder="Username" autoComplete="off" className="h-9 flex-1 rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring" />
          <input value={np} onChange={(e) => setNp(e.target.value)} type="password" placeholder="Password (8+ chars)" autoComplete="new-password" className="h-9 flex-1 rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring" />
        </div>
        <div className="flex items-center justify-between">
          <label className="flex items-center gap-2 text-sm text-muted-foreground"><input type="checkbox" checked={nAdmin} onChange={(e) => setNAdmin(e.target.checked)} className="size-3.5 accent-foreground" />Administrator</label>
          <Button size="sm" disabled={createUser.isPending} onClick={addUser}><UserPlus className="size-4" />{createUser.isPending ? "Adding…" : "Add user"}</Button>
        </div>
        {uErr && <p className="text-xs text-destructive">{uErr}</p>}
      </div>
    </section>
  )

  const accountSection = (
    <section>
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Account</h2>
      <div className="flex items-center justify-between rounded-lg border bg-card p-3">
        <div className="text-sm"><span className="text-muted-foreground">Signed in as </span><span className="font-medium">{user}</span></div>
        <Button variant="outline" size="sm" onClick={logout}>Log out</Button>
      </div>
    </section>
  )

  return (
    <div className="flex h-full w-full" data-tour="settings-root">
      <aside className={cn("w-full shrink-0 flex-col border-r md:flex md:w-[220px]", mobileDetail ? "hidden" : "flex")} data-tour="settings-nav">
        <header className="flex h-13 shrink-0 items-center border-b px-4 text-sm font-semibold">Settings</header>
        <nav className="flex-1 space-y-0.5 overflow-y-auto p-2">
          {NAV.map((n) => (
            <button key={n.id} onClick={() => { setPage(n.id); setMobileDetail(true) }} className={navRow(page === n.id)}>
              <n.icon className="size-4 shrink-0" />{n.label}
            </button>
          ))}
        </nav>
      </aside>
      <div className={cn("min-w-0 flex-1 flex-col md:flex", mobileDetail ? "flex" : "hidden")}>
        <header className="flex h-13 shrink-0 items-center gap-1 border-b px-4 text-sm font-semibold">
          <button type="button" onClick={() => setMobileDetail(false)} className="-ml-1.5 rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground md:hidden" aria-label="Back to settings"><ChevronLeft className="size-4" /></button>
          {current?.label || "Settings"}
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-2xl space-y-6 p-4" data-tour="settings-current-panel">
            {page === "general" && <>{appearanceSection}<SidebarItemsSettings /></>}
            {page === "personalization" && <PersonalizationSection />}
            {page === "account" && <><AccountSecurity /><PresetSection />{accountSection}</>}
            {page === "models" && <>{aiDefaultsSection}{endpointsSection}{isAdmin && <DeviceConnectSection />}</>}
            {page === "integrations" && <IntegrationsExtraSections />}
            {page === "users" && isAdmin && (usersSection || <p className="text-sm text-muted-foreground">No users to manage.</p>)}
            {page === "system" && isAdmin && <AppSettingsSections />}
            {page === "tools" && isAdmin && <AdminSections />}
            {page === "advanced" && isAdmin && <AdvancedSections />}
          </div>
        </div>
      </div>
    </div>
  )
}
