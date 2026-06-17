import { useState } from "react"
import { Trash2, UserPlus, Plus } from "lucide-react"
import { useUi } from "@/stores/ui"
import { useAuthStatus, useUsers, useUserMutations, logout, changePassword, setup2FA, confirm2FA } from "@/api/auth"
import { useModels, useDefaultChat, useDeleteEndpoint, useEndpointMutations, useSetDefaultModel, testEndpoint } from "@/api/models"
import { usePresets, useCreatePreset } from "@/api/presets"
import { AdminSections } from "@/components/settings/AdminSections"
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
  const doChange = async () => {
    if (nw.length < 8) { setPwMsg("New password must be 8+ chars"); return }
    const r = await changePassword(cur, nw)
    if (r.ok) { setPwMsg("Password changed."); setCur(""); setNw("") } else setPwMsg(r.error || "Failed")
  }
  const startTwo = async () => { const r = await setup2FA(); if (r.qr_code) { setQr(r.qr_code); setTwoMsg("") } else setTwoMsg(r.error || "2FA unavailable") }
  const confirmTwo = async () => { const r = await confirm2FA(code); if (r.ok) { setTwoMsg("2FA enabled."); setQr(null); setCode("") } else setTwoMsg(r.error || "Invalid code") }
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
          <div className="flex items-center justify-between"><span className="text-sm font-medium">Two-factor (TOTP)</span>{!qr && <Button size="sm" variant="outline" onClick={startTwo}>Enable 2FA</Button>}</div>
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
  const { data: models } = useModels()
  const { data: def } = useDefaultChat()
  const setDefault = useSetDefaultModel()
  const { data: users } = useUsers()
  const del = useDeleteEndpoint()
  const allModels = (models?.items || []).flatMap((e) => [...(e.models || []), ...(e.models_extra || [])])
  const { create: createUser, remove: removeUser } = useUserMutations()
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

  return (
    <div className="mx-auto flex h-full w-full max-w-2xl flex-col">
      <header className="flex h-13 shrink-0 items-center border-b px-4 text-sm font-semibold">Settings</header>
      <div className="flex-1 space-y-6 overflow-y-auto p-4">
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

        {userList.length > 0 && (
          <section>
            <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Users <span className="normal-case text-muted-foreground/70">(admin)</span></h2>
            <div className="space-y-2">
              {userList.map((u) => (
                <div key={u.username} className="group flex items-center justify-between rounded-lg border bg-card p-3">
                  <span className="text-sm font-medium">{u.username}{u.username === user && <span className="ml-1.5 text-xs font-normal text-muted-foreground">(you)</span>}</span>
                  <div className="flex items-center gap-2">
                    {u.is_admin && <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">admin</span>}
                    {u.username !== user && (
                      <button onClick={() => { if (confirm(`Delete user "${u.username}"?`)) removeUser.mutate(u.username) }} className="text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100" title="Delete user"><Trash2 className="size-4" /></button>
                    )}
                  </div>
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
        )}

        <PresetSection />

        <AccountSecurity />

        <AdminSections />

        <section>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Account</h2>
          <div className="flex items-center justify-between rounded-lg border bg-card p-3">
            <div className="text-sm"><span className="text-muted-foreground">Signed in as </span><span className="font-medium">{user}</span></div>
            <Button variant="outline" size="sm" onClick={logout}>Log out</Button>
          </div>
        </section>
      </div>
    </div>
  )
}
