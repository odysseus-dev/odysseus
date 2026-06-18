import { useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Trash2, UserPlus, Plus, Pencil, SlidersHorizontal, UserCircle, Boxes, Plug, Users, Server, Webhook, Wrench, Sparkles } from "lucide-react"
import { useUi } from "@/stores/ui"
import { useAuthStatus, useUsers, useUserMutations, logout, changePassword, setup2FA, confirm2FA, disable2FA, useTwoFAStatus, setOpenSignup } from "@/api/auth"
import { Switch } from "@/components/ui/switch"
import { useModels, useDefaultChat, useDeleteEndpoint, useEndpointMutations, useSetDefaultModel, testEndpoint } from "@/api/models"
import { usePresets, useCreatePreset } from "@/api/presets"
import { AdminSections } from "@/components/settings/AdminSections"
import { AppSettingsSections, SidebarItemsSettings } from "@/components/settings/AppSettings"
import { IntegrationsExtraSections } from "@/components/settings/IntegrationsExtra"
import { AdvancedSections } from "@/components/settings/AdvancedSettings"
import { PersonalizationSection } from "@/components/settings/Personalization"
import { UserPrivileges } from "@/components/settings/UserPrivileges"
import { useSetUserAdmin, useProviders } from "@/api/advanced"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const inpCls = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"

function AccountSecurity() {
  const [cur, setCur] = useState("")
  const [nw, setNw] = useState("")
  const [pwMsg, setPwMsg] = useState("")
  const [qr, setQr] = useState<string | null>(null)
  const [code, setCode] = useState("")
  const [twoMsg, setTwoMsg] = useState("")
  const { data: twoFA } = useTwoFAStatus()
  const qc = useQueryClient()
  const doChange = async () => {
    if (nw.length < 8) { setPwMsg("New password must be 8+ chars"); return }
    const r = await changePassword(cur, nw)
    if (r.ok) { setPwMsg("Password changed."); setCur(""); setNw("") } else setPwMsg(r.error || "Failed")
  }
  const startTwo = async () => { const r = await setup2FA(); if (r.qr_code) { setQr(r.qr_code); setTwoMsg("") } else setTwoMsg(r.error || "2FA unavailable") }
  const confirmTwo = async () => { const r = await confirm2FA(code); if (r.ok) { setTwoMsg("2FA enabled."); setQr(null); setCode(""); qc.invalidateQueries({ queryKey: ["2fa-status"] }) } else setTwoMsg(r.error || "Invalid code") }
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
        </div>
        <div className="flex items-center justify-between border-t pt-3">
          <span className="text-sm font-medium">Export my data</span>
          <Button size="sm" variant="outline" onClick={() => window.open("/api/export", "_blank")}>Download JSON</Button>
        </div>
      </div>
    </section>
  )
}

function PresetSection() {
  const { data: presets } = usePresets()
  const { mutate, isPending } = useCreatePreset()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [sys, setSys] = useState("")
  const [temp, setTemp] = useState("1.0")
  const [maxTok, setMaxTok] = useState("0")
  const [err, setErr] = useState("")
  const add = () => {
    if (!name.trim()) { setErr("Name required"); return }
    setErr("")
    mutate({ name: name.trim(), system_prompt: sys, temperature: parseFloat(temp) || 1, max_tokens: parseInt(maxTok) || 0 }, {
      onSuccess: () => { setName(""); setSys(""); setTemp("1.0"); setMaxTok("0"); setOpen(false) },
      onError: (e) => setErr(e instanceof Error ? e.message : "Failed"),
    })
  }
  return (
    <section>
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Presets</h2>
      <div className="space-y-2">
        {(presets || []).map((p) => <div key={p.id} className="rounded-lg border bg-card p-3 text-sm">{p.name}</div>)}
        {(presets || []).length === 0 && <p className="py-1 text-sm text-muted-foreground">No presets yet.</p>}
      </div>
      {open ? (
        <div className="mt-2 space-y-2 rounded-lg border bg-card p-3">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Preset name" className={inpCls} />
          <textarea value={sys} onChange={(e) => setSys(e.target.value)} placeholder="System prompt (optional)" rows={3} className="w-full resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring" />
          <div className="flex gap-2">
            <label className="flex-1 text-xs text-muted-foreground">Temperature<input value={temp} onChange={(e) => setTemp(e.target.value)} type="number" min={0} max={2} step={0.1} className={cn(inpCls, "mt-1")} /></label>
            <label className="flex-1 text-xs text-muted-foreground">Max tokens (0=auto)<input value={maxTok} onChange={(e) => setMaxTok(e.target.value)} type="number" min={0} className={cn(inpCls, "mt-1")} /></label>
          </div>
          {err && <p className="text-xs text-destructive">{err}</p>}
          <div className="flex justify-end gap-2"><Button variant="ghost" size="sm" onClick={() => setOpen(false)}>Cancel</Button><Button size="sm" disabled={isPending} onClick={add}>{isPending ? "Saving…" : "Save preset"}</Button></div>
        </div>
      ) : <Button variant="outline" size="sm" className="mt-2" onClick={() => setOpen(true)}><Plus className="size-4" />New preset</Button>}
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
  const addUser = () => {
    if (!nu.trim() || np.length < 8) { setUErr("Username and 8+ char password required"); return }
    setUErr("")
    createUser.mutate({ username: nu.trim(), password: np, is_admin: nAdmin }, {
      onSuccess: () => { setNu(""); setNp(""); setNAdmin(false) },
      onError: (e: unknown) => setUErr(e instanceof Error ? e.message : "Failed"),
    })
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
  const [page, setPage] = useState("general")
  const current = NAV.find((n) => n.id === page) || NAV[0]
  const navRow = (active: boolean) =>
    cn("flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm transition-colors",
      active ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground")

  const appearanceSection = (
    <section>
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
          <div className="flex items-center gap-1.5">
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
    <section>
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">AI defaults</h2>
      <div className="flex items-center justify-between rounded-lg border bg-card p-3">
        <span className="text-sm">Default chat model</span>
        <select value={def?.model || ""} onChange={(e) => setDefault.mutate(e.target.value)} className="h-9 max-w-[60%] rounded-md border bg-background px-2 text-sm outline-none focus-visible:border-ring">
          {!def?.model && <option value="">—</option>}
          {allModels.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </div>
    </section>
  )

  const endpointsSection = (
    <section>
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Model endpoints</h2>
      <div className="space-y-2">
        {endpoints.map((e) => (
          <div key={e.endpoint_id} className="group flex items-center gap-3 rounded-lg border bg-card p-3">
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{e.endpoint_name || e.url}</div>
              <div className="truncate text-xs text-muted-foreground">{e.url} · {(e.models?.length || 0) + (e.models_extra?.length || 0)} models{e.category ? ` · ${e.category}` : ""}</div>
            </div>
            <button onClick={() => { if (confirm("Delete this endpoint?")) del.mutate(e.endpoint_id) }} className="text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"><Trash2 className="size-4" /></button>
          </div>
        ))}
        {endpoints.length === 0 && <p className="py-2 text-sm text-muted-foreground">No saved endpoints.</p>}
      </div>
      <AddEndpointForm />
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
    <div className="flex h-full w-full">
      <aside className="flex w-[220px] shrink-0 flex-col border-r">
        <header className="flex h-13 shrink-0 items-center border-b px-4 text-sm font-semibold">Settings</header>
        <nav className="flex-1 space-y-0.5 overflow-y-auto p-2">
          {NAV.map((n) => (
            <button key={n.id} onClick={() => setPage(n.id)} className={navRow(page === n.id)}>
              <n.icon className="size-4 shrink-0" />{n.label}
            </button>
          ))}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-13 shrink-0 items-center border-b px-4 text-sm font-semibold">{current?.label || "Settings"}</header>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-2xl space-y-6 p-4">
            {page === "general" && <>{appearanceSection}<SidebarItemsSettings /></>}
            {page === "personalization" && <PersonalizationSection />}
            {page === "account" && <><AccountSecurity /><PresetSection />{accountSection}</>}
            {page === "models" && <>{aiDefaultsSection}{endpointsSection}</>}
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
