import { useState } from "react"
import { ChevronRight, Trash2, Plus, Loader2, Lock, Unlock, RefreshCw } from "lucide-react"
import { useModels } from "@/api/models"
import {
  useEndpointModels, useEndpointAdmin, probeEndpoint,
  useVaultConfig, useVaultMutations, useSystemLogs,
  usePresetTemplates, usePresetTemplateMutations,
} from "@/api/advanced"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { cn } from "@/lib/utils"

const H = "mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
const inp = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"

// ─────────────── Model endpoints: edit + curate models + probe ───────────────
function EndpointModels({ epId }: { epId: string }) {
  const { data: models } = useEndpointModels(epId)
  const { setHidden } = useEndpointAdmin()
  const toggle = (id: string, visible: boolean) => {
    const hidden = new Set((models || []).filter((m) => m.is_hidden).map((m) => m.id))
    if (visible) hidden.delete(id); else hidden.add(id)
    setHidden.mutate({ epId, hidden: [...hidden] })
  }
  if (!models || !models.length) return <p className="px-3 py-2 text-xs text-muted-foreground">No models discovered.</p>
  return (
    <div className="max-h-64 space-y-0.5 overflow-y-auto border-t px-3 py-2">
      {models.map((m) => (
        <div key={m.id} className="flex items-center justify-between gap-2 py-0.5">
          <span className="min-w-0 truncate text-sm" title={m.id}>{m.display}</span>
          <Switch checked={!m.is_hidden} onCheckedChange={(v) => toggle(m.id, v)} />
        </div>
      ))}
    </div>
  )
}

function EndpointRow({ ep }: { ep: { endpoint_id: string; endpoint_name?: string; url?: string } }) {
  const { edit } = useEndpointAdmin()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(ep.endpoint_name || "")
  const [url, setUrl] = useState(ep.url || "")
  const [key, setKey] = useState("")
  const [probe, setProbe] = useState("")
  const doProbe = async () => { setProbe("Probing…"); const r = await probeEndpoint(ep.endpoint_id); setProbe(r.ok || r.status === "ok" || r.models ? `OK · ${r.models?.length ?? 0} models` : `Failed: ${r.error || "?"}`) }
  const save = () => edit.mutate({ epId: ep.endpoint_id, name, base_url: url, api_key: key || undefined }, { onSuccess: () => { setEditing(false); setKey("") } })
  return (
    <div className="rounded-lg border bg-card">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-2 px-3 py-2.5 text-left">
        <ChevronRight className={cn("size-4 shrink-0 transition-transform duration-200", open && "rotate-90")} />
        <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium">{ep.endpoint_name || ep.url}</span><span className="block truncate text-xs text-muted-foreground">{ep.url}</span></span>
      </button>
      {open && (
        <div className="border-t">
          <div className="flex flex-wrap items-center gap-2 px-3 py-2">
            <Button size="sm" variant="outline" onClick={doProbe}><RefreshCw className="size-4" />Probe</Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing((e) => !e)}>{editing ? "Cancel edit" : "Edit"}</Button>
            {probe && <span className="text-xs text-muted-foreground">{probe}</span>}
          </div>
          {editing && (
            <div className="space-y-2 px-3 pb-2">
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" className={inp} />
              <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="Base URL" className={inp} />
              <input value={key} onChange={(e) => setKey(e.target.value)} type="password" placeholder="API key (unchanged if blank)" autoComplete="off" className={inp} />
              <div className="flex justify-end"><Button size="sm" disabled={edit.isPending} onClick={save}>Save</Button></div>
            </div>
          )}
          <EndpointModels epId={ep.endpoint_id} />
        </div>
      )}
    </div>
  )
}

export function ModelEndpointAdmin() {
  const { data: models } = useModels()
  const eps = (models?.items || []).filter((e) => e.endpoint_id)
  if (eps.length === 0) return null
  return (
    <section>
      <h2 className={H}>Manage endpoints &amp; models <span className="normal-case text-muted-foreground/70">(admin)</span></h2>
      <div className="space-y-2">{eps.map((e) => <EndpointRow key={e.endpoint_id} ep={e} />)}</div>
    </section>
  )
}

// ───────────────────────── Preset templates ─────────────────────────
export function PresetTemplatesSection() {
  const { data: templates } = usePresetTemplates()
  const { create, remove } = usePresetTemplateMutations()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [desc, setDesc] = useState("")
  const add = () => { if (!name.trim()) return; create.mutate({ name: name.trim(), description: desc.trim() || undefined }, { onSuccess: () => { setName(""); setDesc(""); setOpen(false) } }) }
  return (
    <section>
      <h2 className={H}>Preset templates</h2>
      <div className="space-y-2">
        {(templates || []).map((t) => (
          <div key={t.id} className="group flex items-center gap-2 rounded-lg border bg-card p-3">
            <span className="min-w-0 flex-1 truncate text-sm font-medium">{t.name}{t.description && <span className="ml-2 font-normal text-muted-foreground">{t.description}</span>}</span>
            <button onClick={() => { if (confirm(`Delete template "${t.name}"?`)) remove.mutate(t.id) }} className="shrink-0 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"><Trash2 className="size-4" /></button>
          </div>
        ))}
        {(templates || []).length === 0 && <p className="py-1 text-sm text-muted-foreground">No templates.</p>}
      </div>
      {open ? (
        <div className="mt-2 space-y-2 rounded-lg border bg-card p-3">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Template name" className={inp} />
          <input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Description (optional)" className={inp} />
          <div className="flex justify-end gap-2"><Button variant="ghost" size="sm" onClick={() => setOpen(false)}>Cancel</Button><Button size="sm" disabled={create.isPending} onClick={add}>Save</Button></div>
        </div>
      ) : <Button variant="outline" size="sm" className="mt-2" onClick={() => setOpen(true)}><Plus className="size-4" />New template</Button>}
    </section>
  )
}

// ───────────────────────── Vault ─────────────────────────
export function VaultSection() {
  const { data: v } = useVaultConfig()
  const m = useVaultMutations()
  const [url, setUrl] = useState<string | null>(null)
  const [email, setEmail] = useState<string | null>(null)
  const [pw, setPw] = useState("")
  const [msg, setMsg] = useState("")
  if (!v) return null
  const su = url ?? v.server_url
  const em = email ?? v.email
  const doUnlock = async () => { const r = await m.unlock(pw); if (!r.ok && em) { const r2 = await m.login(em, pw); setMsg(r2.ok ? "Unlocked." : r2.error || "Failed") } else setMsg(r.ok ? "Unlocked." : r.error || "Failed"); setPw("") }
  return (
    <section>
      <h2 className={H}>Vault <span className="normal-case text-muted-foreground/70">(admin)</span></h2>
      <div className="space-y-2 rounded-lg border bg-card p-3">
        {!v.bw_installed && <p className="text-xs text-destructive">Bitwarden CLI (bw) is not installed on the server.</p>}
        <input value={su} onChange={(e) => setUrl(e.target.value)} placeholder="Vaultwarden server URL" className={inp} />
        <input value={em} onChange={(e) => setEmail(e.target.value)} placeholder="Account email" className={inp} />
        <div className="flex justify-end"><Button size="sm" variant="outline" onClick={async () => { const r = await m.saveConfig(su, em); setMsg(r.ok ? "Config saved." : r.error || "Failed") }}>Save config</Button></div>
        <div className="flex items-center justify-between border-t pt-2">
          <span className="text-sm">Status: {v.unlocked ? <span className="text-emerald-500">unlocked</span> : <span className="text-muted-foreground">locked</span>}</span>
          {v.unlocked && <Button size="sm" variant="outline" onClick={() => m.lock()}><Lock className="size-4" />Lock</Button>}
        </div>
        {!v.unlocked && (
          <div className="flex gap-2"><input value={pw} onChange={(e) => setPw(e.target.value)} type="password" placeholder="Master password" autoComplete="off" className={inp} /><Button size="sm" disabled={!pw} onClick={doUnlock}><Unlock className="size-4" />Unlock</Button></div>
        )}
        {msg && <p className="text-xs text-muted-foreground">{msg}</p>}
      </div>
    </section>
  )
}

// ───────────────────────── System logs ─────────────────────────
export function SystemLogsSection() {
  const [on, setOn] = useState(false)
  const { data: logs, isFetching } = useSystemLogs(300, on)
  return (
    <section>
      <h2 className={H}>System logs <span className="normal-case text-muted-foreground/70">(admin)</span></h2>
      <div className="rounded-lg border bg-card p-3">
        <div className="flex items-center justify-between">
          <span className="text-sm">app.log {on && <span className="text-muted-foreground">· live{isFetching ? "…" : ""}</span>}</span>
          <Button size="sm" variant="outline" onClick={() => setOn((o) => !o)}>{on ? "Stop" : <><Loader2 className={cn("size-4", isFetching && on && "animate-spin")} />Load logs</>}</Button>
        </div>
        {on && <pre className="mt-2 max-h-80 overflow-auto rounded bg-muted p-2 font-mono text-[11px] leading-relaxed">{(logs || []).join("\n") || "…"}</pre>}
      </div>
    </section>
  )
}

export function AdvancedSections() {
  return (<><ModelEndpointAdmin /><PresetTemplatesSection /><VaultSection /><SystemLogsSection /></>)
}
