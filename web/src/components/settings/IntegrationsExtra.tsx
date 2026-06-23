import { useRef, useState } from "react"
import { Plus, Trash2, Pencil, Star, ChevronRight, Upload, Download, Loader2, Search, ExternalLink, Copy, Check, Package } from "lucide-react"
import {
  useEmailAccounts, useEmailAccountMutations, testEmailAccount, useEmailStyle, saveEmailStyle, extractEmailStyle,
  useCalDavAccounts, useCalDavMutations, testCalDav,
  useMcpServerTools, useSetMcpDisabledTools, useContactsCount, clearContacts, useContactList, useContactMutations, useCardDavConfig,
  type EmailAccount, type EmailAccountInput, type CalDavAccount, type Contact,
} from "@/api/accounts"
import { useTokenMutations } from "@/api/tokens"
import { useMcpServers } from "@/api/admin"
import { apiFetch } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { cn } from "@/lib/utils"

const H = "mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
const inp = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"

// ───────────────────────── Email accounts ─────────────────────────
const EMPTY_EMAIL: EmailAccountInput = { name: "", from_address: "", display_name: "", imap_host: "", imap_port: 993, imap_user: "", imap_starttls: true, smtp_host: "", smtp_port: 465, smtp_security: "ssl", smtp_user: "", is_default: false }

function EmailAccountForm({ initial, onClose }: { initial?: EmailAccount; onClose: () => void }) {
  const { create, update } = useEmailAccountMutations()
  const [f, setF] = useState<EmailAccountInput>(initial ? { ...initial } : { ...EMPTY_EMAIL })
  const [msg, setMsg] = useState("")
  const [testing, setTesting] = useState(false)
  const set = (k: keyof EmailAccountInput, v: unknown) => setF((p) => ({ ...p, [k]: v }))
  const save = () => {
    if (!f.name?.trim()) { setMsg("Name required"); return }
    const opts = { onSuccess: onClose, onError: (e: unknown) => setMsg(e instanceof Error ? e.message : "Failed") }
    if (initial) update.mutate({ id: initial.id, ...f }, opts); else create.mutate(f, opts)
  }
  const test = async () => {
    setTesting(true); setMsg("Testing…")
    try { const r = await testEmailAccount(initial ? { account_id: initial.id, ...f } : f); setMsg(r.imap?.ok ? `IMAP ✓${r.smtp ? ` · SMTP ${r.smtp.ok ? "✓" : "✗"}` : ""}` : `IMAP failed: ${r.imap?.error || "?"}`) }
    catch { setMsg("Test failed") } finally { setTesting(false) }
  }
  return (
    <div className="mt-2 space-y-2 rounded-lg border bg-card p-3">
      <div className="flex gap-2"><input value={f.name || ""} onChange={(e) => set("name", e.target.value)} placeholder="Account name" className={inp} /><input value={f.from_address || ""} onChange={(e) => set("from_address", e.target.value)} placeholder="from@example.com" className={inp} /></div>
      <input value={f.display_name || ""} onChange={(e) => set("display_name", e.target.value)} placeholder="Display name (optional)" className={inp} />
      <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">IMAP (incoming)</div>
      <div className="flex gap-2"><input value={f.imap_host || ""} onChange={(e) => set("imap_host", e.target.value)} placeholder="imap host" className={inp} /><input value={f.imap_port ?? ""} onChange={(e) => set("imap_port", Number(e.target.value) || 993)} type="number" placeholder="993" className={cn(inp, "w-24")} /></div>
      <div className="flex gap-2"><input value={f.imap_user || ""} onChange={(e) => set("imap_user", e.target.value)} placeholder="imap user" className={inp} /><input onChange={(e) => set("imap_password", e.target.value)} type="password" placeholder={initial?.has_imap_password ? "•••• (unchanged)" : "imap password"} autoComplete="off" className={inp} /></div>
      <label className="flex items-center justify-between text-sm text-muted-foreground">STARTTLS<Switch checked={!!f.imap_starttls} onCheckedChange={(v) => set("imap_starttls", v)} /></label>
      <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">SMTP (outgoing)</div>
      <div className="flex gap-2"><input value={f.smtp_host || ""} onChange={(e) => set("smtp_host", e.target.value)} placeholder="smtp host" className={inp} /><input value={f.smtp_port ?? ""} onChange={(e) => set("smtp_port", Number(e.target.value) || 465)} type="number" placeholder="465" className={cn(inp, "w-24")} /></div>
      <div className="flex gap-2">
        <select value={f.smtp_security || "ssl"} onChange={(e) => set("smtp_security", e.target.value)} className={inp}><option value="ssl">SSL/TLS</option><option value="starttls">STARTTLS</option><option value="none">None</option></select>
        <input value={f.smtp_user || ""} onChange={(e) => set("smtp_user", e.target.value)} placeholder="smtp user" className={inp} />
      </div>
      <input onChange={(e) => set("smtp_password", e.target.value)} type="password" placeholder={initial?.has_smtp_password ? "•••• (unchanged)" : "smtp password"} autoComplete="off" className={inp} />
      <label className="flex items-center justify-between text-sm text-muted-foreground">Set as default<Switch checked={!!f.is_default} onCheckedChange={(v) => set("is_default", v)} /></label>
      {msg && <p className="text-xs text-muted-foreground">{msg}</p>}
      <div className="flex justify-end gap-2"><Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button><Button variant="outline" size="sm" disabled={testing} onClick={test}>Test</Button><Button size="sm" disabled={create.isPending || update.isPending} onClick={save}>{initial ? "Save" : "Add"}</Button></div>
    </div>
  )
}

function EmailWritingStyle() {
  const { data } = useEmailStyle()
  const [val, setVal] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const current = val ?? (data?.style || data?.writing_style || "")
  return (
    <div className="mt-2 space-y-2 rounded-lg border bg-card p-3">
      <div className="text-sm font-medium">Writing style</div>
      <textarea value={current} onChange={(e) => setVal(e.target.value)} rows={3} placeholder="How the agent should write emails on your behalf…" className="w-full resize-y rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring" />
      <div className="flex justify-end gap-2">
        <Button variant="outline" size="sm" disabled={busy} onClick={async () => { setBusy(true); try { const r = await extractEmailStyle(); setVal(r.style || r.writing_style || current) } finally { setBusy(false) } }}>{busy ? <Loader2 className="size-4 animate-spin" /> : null}Extract from sent</Button>
        <Button size="sm" onClick={() => saveEmailStyle(current)}>Save</Button>
      </div>
    </div>
  )
}

export function EmailAccountsSection() {
  const { data: accounts } = useEmailAccounts()
  const { remove, setDefault } = useEmailAccountMutations()
  const [adding, setAdding] = useState(false)
  const [editId, setEditId] = useState<string | null>(null)
  return (
    <section>
      <h2 className={H}>Email accounts</h2>
      <div className="space-y-2">
        {(accounts || []).map((a) => editId === a.id ? <EmailAccountForm key={a.id} initial={a} onClose={() => setEditId(null)} /> : (
          <div key={a.id} className="group flex items-center gap-2 rounded-lg border bg-card p-3">
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{a.name} {a.is_default && <span className="ml-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">default</span>}</div>
              <div className="truncate text-xs text-muted-foreground">{a.from_address || a.imap_user} · {a.imap_host || "no imap"}</div>
            </div>
            {!a.is_default && <button onClick={() => setDefault.mutate(a.id)} title="Set default" className="shrink-0 text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover:opacity-100"><Star className="size-4" /></button>}
            <button onClick={() => window.open(`/api/email/oauth/google/authorize?account_id=${encodeURIComponent(a.id)}`, "_blank", "noopener")} title="Connect Google Workspace" className="shrink-0 text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover:opacity-100"><ExternalLink className="size-4" /></button>
            <button onClick={() => setEditId(a.id)} title="Edit" className="shrink-0 text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover:opacity-100"><Pencil className="size-4" /></button>
            <button onClick={() => { if (confirm(`Delete account "${a.name}"?`)) remove.mutate(a.id) }} title="Delete" className="shrink-0 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"><Trash2 className="size-4" /></button>
          </div>
        ))}
        {(accounts || []).length === 0 && <p className="py-1 text-sm text-muted-foreground">No email accounts.</p>}
      </div>
      {adding ? <EmailAccountForm onClose={() => setAdding(false)} /> : <Button variant="outline" size="sm" className="mt-2" onClick={() => setAdding(true)}><Plus className="size-4" />Add email account</Button>}
      <EmailWritingStyle />
    </section>
  )
}

// ───────────────────────── CalDAV calendar accounts ─────────────────────────
function CalDavForm({ initial, onClose }: { initial?: CalDavAccount; onClose: () => void }) {
  const { create, update } = useCalDavMutations()
  const [label, setLabel] = useState(initial?.label || "")
  const [url, setUrl] = useState(initial?.url || "")
  const [username, setUsername] = useState(initial?.username || "")
  const [password, setPassword] = useState("")
  const [msg, setMsg] = useState("")
  const [testing, setTesting] = useState(false)
  const save = () => {
    if (!url.trim()) { setMsg("URL required"); return }
    const opts = { onSuccess: onClose, onError: (e: unknown) => setMsg(e instanceof Error ? e.message : "Failed") }
    if (initial) update.mutate({ id: initial.id, label, url, username, password: password || undefined }, opts)
    else { if (!password) { setMsg("Password required"); return } create.mutate({ label, url, username, password }, opts) }
  }
  const test = async () => { setTesting(true); setMsg("Testing…"); try { const r = await testCalDav(initial && !password ? { account_id: initial.id } : { url, username, password }); setMsg(r.ok ? `OK · ${(r.calendars || []).length} calendars` : `Failed: ${r.error || "?"}`) } catch { setMsg("Test failed") } finally { setTesting(false) } }
  return (
    <div className="mt-2 space-y-2 rounded-lg border bg-card p-3">
      <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Label (e.g. Fastmail)" className={inp} />
      <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="CalDAV URL" className={inp} />
      <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="Username" autoComplete="off" className={inp} />
      <input value={password} onChange={(e) => setPassword(e.target.value)} type="password" placeholder={initial?.has_password ? "•••• (unchanged)" : "Password"} autoComplete="off" className={inp} />
      {msg && <p className="text-xs text-muted-foreground">{msg}</p>}
      <div className="flex justify-end gap-2"><Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button><Button variant="outline" size="sm" disabled={testing} onClick={test}>Test</Button><Button size="sm" disabled={create.isPending || update.isPending} onClick={save}>{initial ? "Save" : "Add"}</Button></div>
    </div>
  )
}

export function CalendarAccountsSection() {
  const { data: accounts } = useCalDavAccounts()
  const { remove } = useCalDavMutations()
  const [adding, setAdding] = useState(false)
  const [editId, setEditId] = useState<string | null>(null)
  return (
    <section>
      <h2 className={H}>Calendar accounts <span className="normal-case text-muted-foreground/70">(CalDAV)</span></h2>
      <div className="space-y-2">
        {(accounts || []).map((a) => editId === a.id ? <CalDavForm key={a.id} initial={a} onClose={() => setEditId(null)} /> : (
          <div key={a.id} className="group flex items-center gap-2 rounded-lg border bg-card p-3">
            <div className="min-w-0 flex-1"><div className="truncate text-sm font-medium">{a.label}</div><div className="truncate text-xs text-muted-foreground">{a.username} · {a.url}</div></div>
            <button onClick={() => setEditId(a.id)} title="Edit" className="shrink-0 text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover:opacity-100"><Pencil className="size-4" /></button>
            <button onClick={() => { if (confirm(`Delete "${a.label}"?`)) remove.mutate(a.id) }} title="Delete" className="shrink-0 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"><Trash2 className="size-4" /></button>
          </div>
        ))}
        {(accounts || []).length === 0 && <p className="py-1 text-sm text-muted-foreground">No calendar accounts.</p>}
      </div>
      {adding ? <CalDavForm onClose={() => setAdding(false)} /> : <Button variant="outline" size="sm" className="mt-2" onClick={() => setAdding(true)}><Plus className="size-4" />Add CalDAV account</Button>}
    </section>
  )
}

// ───────────────────────── MCP per-server tool toggles ─────────────────────────
function McpServerTools({ serverId }: { serverId: string }) {
  const { data: tools } = useMcpServerTools(serverId)
  const setDisabled = useSetMcpDisabledTools()
  const toggle = (name: string, enabled: boolean) => {
    const disabled = new Set((tools || []).filter((t) => t.is_disabled).map((t) => t.name))
    if (enabled) disabled.delete(name); else disabled.add(name)
    setDisabled.mutate({ serverId, disabled: [...disabled] })
  }
  if (!tools || tools.length === 0) return <p className="px-3 py-2 text-xs text-muted-foreground">No tools discovered.</p>
  return (
    <div className="space-y-1 border-t px-3 py-2">
      {tools.map((t) => (
        <div key={t.name} className="flex items-center justify-between gap-2 py-0.5">
          <span className="min-w-0 truncate text-sm">{t.name}</span>
          <Switch checked={!t.is_disabled} onCheckedChange={(v) => toggle(t.name, v)} />
        </div>
      ))}
    </div>
  )
}

export function McpToolsSection() {
  const { data } = useMcpServers()
  const [openId, setOpenId] = useState<string | null>(null)
  const servers = data?.items || []
  if (!data?.admin || servers.length === 0) return null
  return (
    <section>
      <h2 className={H}>MCP tools <span className="normal-case text-muted-foreground/70">(admin)</span></h2>
      <div className="space-y-2">
        {servers.map((s) => (
          <div key={s.id} className="rounded-lg border bg-card">
            <button onClick={() => setOpenId(openId === s.id ? null : s.id)} className="flex w-full items-center gap-2 px-3 py-2.5 text-sm font-medium">
              <ChevronRight className={cn("size-4 transition-transform duration-200", openId === s.id && "rotate-90")} />
              {s.name || s.id}
            </button>
            {openId === s.id && <McpServerTools serverId={s.id} />}
          </div>
        ))}
      </div>
    </section>
  )
}

// ───────────────────────── Contacts ─────────────────────────
export function ContactsSection() {
  const { data: count } = useContactsCount()
  const { data: contacts } = useContactList()
  const { data: carddav } = useCardDavConfig()
  const mutations = useContactMutations()
  const [msg, setMsg] = useState("")
  const [query, setQuery] = useState("")
  const [editing, setEditing] = useState<Contact | null>(null)
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState("")
  const [email, setEmail] = useState("")
  const [phone, setPhone] = useState("")
  const [address, setAddress] = useState("")
  const [cardUrl, setCardUrl] = useState<string | null>(null)
  const [cardUser, setCardUser] = useState<string | null>(null)
  const [cardPass, setCardPass] = useState("")
  const fileRef = useRef<HTMLInputElement>(null)
  const reset = () => { setAdding(false); setEditing(null); setName(""); setEmail(""); setPhone(""); setAddress("") }
  const beginEdit = (contact: Contact) => { setEditing(contact); setAdding(false); setName(contact.name); setEmail(contact.emails.join(", ")); setPhone(contact.phones.join(", ")); setAddress(contact.address || "") }
  const saveContact = async () => {
    try {
      if (editing) await mutations.update.mutateAsync({ ...editing, name: name.trim(), emails: email.split(",").map((v) => v.trim()).filter(Boolean), phones: phone.split(",").map((v) => v.trim()).filter(Boolean), address: address.trim() })
      else await mutations.add.mutateAsync({ name: name.trim(), email: email.trim(), phone: phone.trim(), address: address.trim() })
      setMsg(editing ? "Contact updated." : "Contact added."); reset()
    } catch (error) { setMsg(error instanceof Error ? error.message : "Couldn't save contact") }
  }
  const onImport = async (file?: File) => {
    if (!file) return
    setMsg("Importing…")
    try {
      const text = await file.text()
      const body = file.name.toLowerCase().endsWith(".csv") ? { csv: text } : { vcf: text }
      const r = await apiFetch("/api/contacts/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      const result = await r.json().catch(() => ({}))
      setMsg(r.ok && result.success !== false ? `Imported ${result.imported ?? "contacts"}.` : `Failed: ${result.error || r.status}`)
    } catch { setMsg("Import failed") }
  }
  const filtered = (contacts || []).filter((contact) => `${contact.name} ${contact.emails.join(" ")} ${contact.phones.join(" ")} ${contact.address || ""}`.toLowerCase().includes(query.toLowerCase()))
  return (
    <section>
      <h2 className={H}>Contacts</h2>
      <div className="space-y-3 rounded-lg border bg-card p-3">
        <div className="flex items-center justify-between"><span className="text-sm">Saved contacts</span><span className="text-sm text-muted-foreground">{count ?? 0}</span></div>
        <div className="grid gap-2 sm:grid-cols-2"><input value={cardUrl ?? carddav?.url ?? ""} onChange={(e) => setCardUrl(e.target.value)} placeholder="CardDAV URL" className={inp} /><input value={cardUser ?? carddav?.username ?? ""} onChange={(e) => setCardUser(e.target.value)} placeholder="CardDAV username" className={inp} /></div>
        <div className="flex gap-2"><input value={cardPass} onChange={(e) => setCardPass(e.target.value)} type="password" placeholder={carddav?.password ? "•••• (unchanged)" : "CardDAV password"} className={inp} /><Button size="sm" variant="outline" onClick={() => mutations.saveConfig.mutate({ carddav_url: cardUrl ?? carddav?.url ?? "", carddav_username: cardUser ?? carddav?.username ?? "", ...(cardPass ? { carddav_password: cardPass } : {}) }, { onSuccess: () => { setMsg("CardDAV saved."); setCardPass("") }, onError: () => setMsg("Couldn't save CardDAV settings.") })}>Save CardDAV</Button></div>
        <label className="flex items-center gap-2 rounded-md border bg-background px-2"><Search className="size-3.5 text-muted-foreground" /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search contacts" className="h-8 min-w-0 flex-1 bg-transparent text-sm outline-none" /></label>
        <div className="max-h-64 space-y-1 overflow-y-auto">
          {filtered.map((contact) => <div key={contact.uid} className="group flex items-center gap-2 rounded-md border px-2.5 py-2">
            <div className="min-w-0 flex-1"><div className="truncate text-sm font-medium">{contact.name || contact.emails[0]}</div><div className="truncate text-xs text-muted-foreground">{[contact.emails.join(", "), contact.phones.join(", "), contact.address].filter(Boolean).join(" · ")}</div></div>
            <button onClick={() => beginEdit(contact)} title="Edit contact" className="text-muted-foreground opacity-0 hover:text-foreground group-hover:opacity-100"><Pencil className="size-3.5" /></button>
            <button onClick={() => { if (confirm(`Delete ${contact.name || "this contact"}?`)) mutations.remove.mutate(contact.uid) }} title="Delete contact" className="text-muted-foreground opacity-0 hover:text-destructive group-hover:opacity-100"><Trash2 className="size-3.5" /></button>
          </div>)}
          {!filtered.length && <p className="py-2 text-xs text-muted-foreground">No contacts found.</p>}
        </div>
        {(adding || editing) && <div className="space-y-2 rounded-md border bg-background p-2">
          <div className="grid gap-2 sm:grid-cols-2"><input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" className={inp} /><input value={email} onChange={(e) => setEmail(e.target.value)} placeholder={editing ? "Emails, comma separated" : "Email"} className={inp} /></div>
          <div className="grid gap-2 sm:grid-cols-2"><input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder={editing ? "Phones, comma separated" : "Phone"} className={inp} /><input value={address} onChange={(e) => setAddress(e.target.value)} placeholder="Address" className={inp} /></div>
          <div className="flex justify-end gap-2"><Button size="sm" variant="ghost" onClick={reset}>Cancel</Button><Button size="sm" disabled={!email.trim() && !editing} onClick={saveContact}>Save contact</Button></div>
        </div>}
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => { reset(); setAdding(true) }}><Plus className="size-4" />Add contact</Button>
          <Button variant="outline" size="sm" onClick={() => window.open("/api/contacts/export", "_blank")}><Download className="size-4" />Export</Button>
          <input ref={fileRef} type="file" accept=".vcf,.csv,.json" className="hidden" onChange={(e) => onImport(e.target.files?.[0])} />
          <Button variant="outline" size="sm" onClick={() => fileRef.current?.click()}><Upload className="size-4" />Import</Button>
          <Button variant="outline" size="sm" className="border-destructive/40 text-destructive hover:bg-destructive/10" onClick={async () => { if (confirm("Delete ALL contacts?")) { await clearContacts(); setMsg("Cleared.") } }}>Clear all</Button>
        </div>
        {msg && <p className="text-xs text-muted-foreground">{msg}</p>}
      </div>
    </section>
  )
}

function AgentPluginCard({ kind, label }: { kind: "codex" | "claude"; label: string }) {
  const { create } = useTokenMutations()
  const [token, setToken] = useState("")
  const [copied, setCopied] = useState(false)
  const scopes = "todos:read,todos:write,documents:read,documents:write,email:read,email:draft,email:send,calendar:read,calendar:write,memory:read,memory:write,cookbook:read,cookbook:launch"
  const root = window.location.origin
  const plugin = `/api/${kind}/plugin.zip`
  const command = kind === "codex"
    ? `export ODYSSEUS_URL=${root}\nexport ODYSSEUS_API_TOKEN='${token}'\nmkdir -p ~/plugins/odysseus && curl -fsSL -H "Authorization: Bearer $ODYSSEUS_API_TOKEN" "$ODYSSEUS_URL${plugin}" -o /tmp/odysseus-plugin.zip\npython3 -m zipfile -e /tmp/odysseus-plugin.zip ~/plugins/odysseus`
    : `export ODYSSEUS_URL=${root}\nexport ODYSSEUS_API_TOKEN='${token}'\nmkdir -p ~/.claude && curl -fsSL -H "Authorization: Bearer $ODYSSEUS_API_TOKEN" "$ODYSSEUS_URL${plugin}" -o /tmp/odysseus-claude.zip\npython3 -m zipfile -e /tmp/odysseus-claude.zip ~/.claude/`
  return <div className="rounded-lg border bg-card p-3">
    <div className="flex items-center justify-between gap-3"><div><div className="text-sm font-medium">{label}</div><div className="text-xs text-muted-foreground">Scoped token and plugin bundle setup.</div></div><Package className="size-5 text-muted-foreground" /></div>
    {!token ? <div className="mt-3 flex gap-2"><Button size="sm" onClick={() => create.mutate({ name: `${label} plugin`, scopes }, { onSuccess: (result) => setToken(result.token) })}>{create.isPending ? "Creating…" : "Create token & setup"}</Button><Button size="sm" variant="outline" onClick={() => window.open(plugin, "_blank")}><Download className="size-4" />Bundle</Button></div>
      : <div className="mt-3 space-y-2"><p className="text-xs text-muted-foreground">Run this once. The token is only shown now.</p><pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-2 text-[11px]">{command}</pre><Button size="sm" variant="outline" onClick={() => navigator.clipboard.writeText(command).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) })}>{copied ? <Check className="size-4" /> : <Copy className="size-4" />}{copied ? "Copied" : "Copy setup"}</Button></div>}
  </div>
}

export function AgentPluginsSection() {
  return <section><h2 className={H}>Coding agents <span className="normal-case text-muted-foreground/70">(admin)</span></h2><div className="grid gap-2 sm:grid-cols-2"><AgentPluginCard kind="codex" label="Codex Agent" /><AgentPluginCard kind="claude" label="Claude Code" /></div></section>
}

export function IntegrationsExtraSections() {
  return (<><EmailAccountsSection /><CalendarAccountsSection /><ContactsSection /><AgentPluginsSection /><McpToolsSection /></>)
}
