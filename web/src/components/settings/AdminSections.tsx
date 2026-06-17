import { useState } from "react"
import { Trash2, Plus, RefreshCw, Send, Server, Webhook as WebhookIcon, Plug, Pencil } from "lucide-react"
import { useMcpServers, useWebhooks, useAdminMutations, useFeatures, useSetFeature } from "@/api/admin"
import { useIntegrations, useIntegrationPresets, useIntegrationMutations, type Integration } from "@/api/integrations"
import { Button } from "@/components/ui/button"
import { Markdown } from "@/components/chat/Markdown"
import { cn } from "@/lib/utils"

const inp = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"
const dot = (s?: string) => s === "connected" ? "bg-emerald-500" : s === "connecting" ? "bg-amber-500" : "bg-muted-foreground/40"

function McpSection() {
  const { data } = useMcpServers()
  const { addServer, reconnectServer, removeServer } = useAdminMutations()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [transport, setTransport] = useState("stdio")
  const [command, setCommand] = useState("")
  const [url, setUrl] = useState("")
  const [err, setErr] = useState("")
  if (!data?.admin) return null
  const submit = () => {
    if (!name.trim()) { setErr("Name required"); return }
    setErr("")
    addServer.mutate({ name: name.trim(), transport, command, url }, {
      onSuccess: () => { setName(""); setCommand(""); setUrl(""); setOpen(false) },
      onError: (e) => setErr(e instanceof Error ? e.message : "Failed"),
    })
  }
  return (
    <section>
      <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><Server className="size-3.5" />MCP servers <span className="normal-case text-muted-foreground/70">(admin)</span></h2>
      <div className="space-y-2">
        {data.items.map((s) => (
          <div key={s.id} className="group flex items-center gap-3 rounded-lg border bg-card p-3">
            <span className={cn("size-2 shrink-0 rounded-full", dot(s.status))} title={s.status} />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{s.name}</div>
              <div className="truncate text-xs text-muted-foreground">{s.transport}{s.command ? ` · ${s.command}` : s.url ? ` · ${s.url}` : ""} · {s.tool_count ?? 0} tools{s.error ? ` · ${s.error}` : ""}</div>
            </div>
            <div className="flex gap-1.5 opacity-0 transition-opacity group-hover:opacity-100">
              <button onClick={() => reconnectServer.mutate(s.id)} title="Reconnect" className="text-muted-foreground hover:text-foreground"><RefreshCw className="size-4" /></button>
              <button onClick={() => { if (confirm(`Delete MCP server "${s.name}"?`)) removeServer.mutate(s.id) }} title="Delete" className="text-muted-foreground hover:text-destructive"><Trash2 className="size-4" /></button>
            </div>
          </div>
        ))}
        {data.items.length === 0 && <p className="py-2 text-sm text-muted-foreground">No MCP servers configured.</p>}
      </div>
      {open ? (
        <div className="mt-2 space-y-2 rounded-lg border bg-card p-3">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Server name" className={inp} />
          <select value={transport} onChange={(e) => setTransport(e.target.value)} className={inp}>
            <option value="stdio">stdio</option>
            <option value="sse">sse</option>
            <option value="http">http</option>
          </select>
          {transport === "stdio"
            ? <input value={command} onChange={(e) => setCommand(e.target.value)} placeholder="Command (e.g. npx -y @scope/server)" className={inp} />
            : <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="Server URL" className={inp} />}
          {err && <p className="text-xs text-destructive">{err}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setOpen(false)}>Cancel</Button>
            <Button size="sm" disabled={addServer.isPending} onClick={submit}>{addServer.isPending ? "Adding…" : "Add server"}</Button>
          </div>
        </div>
      ) : (
        <Button variant="outline" size="sm" className="mt-2" onClick={() => setOpen(true)}><Plus className="size-4" />Add MCP server</Button>
      )}
    </section>
  )
}

function WebhookSection() {
  const { data } = useWebhooks()
  const { addWebhook, testWebhook, toggleWebhook, removeWebhook } = useAdminMutations()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [url, setUrl] = useState("")
  const [events, setEvents] = useState("")
  const [err, setErr] = useState("")
  if (!data?.admin) return null
  const submit = () => {
    if (!name.trim() || !url.trim()) { setErr("Name and URL required"); return }
    setErr("")
    addWebhook.mutate({ name: name.trim(), url: url.trim(), events: events.trim() }, {
      onSuccess: () => { setName(""); setUrl(""); setEvents(""); setOpen(false) },
      onError: (e) => setErr(e instanceof Error ? e.message : "Failed"),
    })
  }
  return (
    <section>
      <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><WebhookIcon className="size-3.5" />Webhooks <span className="normal-case text-muted-foreground/70">(admin)</span></h2>
      <div className="space-y-2">
        {data.items.map((w) => (
          <div key={w.id} className="group flex items-center gap-3 rounded-lg border bg-card p-3">
            <span className={cn("size-2 shrink-0 rounded-full", w.is_active ? "bg-emerald-500" : "bg-muted-foreground/40")} title={w.is_active ? "active" : "disabled"} />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{w.name}</div>
              <div className="truncate text-xs text-muted-foreground">{w.url}{w.events.length ? ` · ${w.events.join(", ")}` : ""}{w.last_status_code ? ` · last ${w.last_status_code}` : ""}</div>
            </div>
            <div className="flex gap-1.5 opacity-0 transition-opacity group-hover:opacity-100">
              <button onClick={() => testWebhook.mutate(w.id)} title="Test" className="text-muted-foreground hover:text-foreground"><Send className="size-4" /></button>
              <button onClick={() => toggleWebhook.mutate({ id: w.id, is_active: !w.is_active })} title={w.is_active ? "Disable" : "Enable"} className="text-xs text-muted-foreground hover:text-foreground">{w.is_active ? "On" : "Off"}</button>
              <button onClick={() => { if (confirm(`Delete webhook "${w.name}"?`)) removeWebhook.mutate(w.id) }} title="Delete" className="text-muted-foreground hover:text-destructive"><Trash2 className="size-4" /></button>
            </div>
          </div>
        ))}
        {data.items.length === 0 && <p className="py-2 text-sm text-muted-foreground">No webhooks configured.</p>}
      </div>
      {open ? (
        <div className="mt-2 space-y-2 rounded-lg border bg-card p-3">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Webhook name" className={inp} />
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="Endpoint URL (https://…)" className={inp} />
          <input value={events} onChange={(e) => setEvents(e.target.value)} placeholder="Events (comma-separated, optional)" className={inp} />
          {err && <p className="text-xs text-destructive">{err}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setOpen(false)}>Cancel</Button>
            <Button size="sm" disabled={addWebhook.isPending} onClick={submit}>{addWebhook.isPending ? "Adding…" : "Add webhook"}</Button>
          </div>
        </div>
      ) : (
        <Button variant="outline" size="sm" className="mt-2" onClick={() => setOpen(true)}><Plus className="size-4" />Add webhook</Button>
      )}
    </section>
  )
}

function IntegrationRow({ integ }: { integ: Integration }) {
  const { update, remove, test } = useIntegrationMutations()
  const [editing, setEditing] = useState(false)
  const [baseUrl, setBaseUrl] = useState(integ.base_url)
  const [apiKey, setApiKey] = useState("")
  const [result, setResult] = useState("")
  const [err, setErr] = useState("")
  const save = () => {
    if (!baseUrl.trim()) { setErr("Base URL required"); return }
    setErr("")
    // Only send api_key when the operator typed a new one — the listed value
    // is masked, so an empty field means "keep the stored secret".
    const data: { base_url: string; api_key?: string } = { base_url: baseUrl.trim() }
    if (apiKey.trim()) data.api_key = apiKey.trim()
    update.mutate({ id: integ.id, data }, {
      onSuccess: () => { setApiKey(""); setEditing(false) },
      onError: (e) => setErr(e instanceof Error ? e.message : "Failed"),
    })
  }
  const runTest = () => {
    setResult("Testing…")
    test.mutate(integ.id, {
      onSuccess: (r) => setResult(r.message || (r.ok ? "Connection successful" : "Connection failed")),
      onError: (e) => setResult(e instanceof Error ? e.message : "Test failed"),
    })
  }
  return (
    <div className="group rounded-lg border bg-card p-3">
      <div className="flex items-center gap-3">
        <span className={cn("size-2 shrink-0 rounded-full", integ.enabled === false ? "bg-muted-foreground/40" : "bg-emerald-500")} title={integ.enabled === false ? "disabled" : "enabled"} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{integ.name}</div>
          <div className="truncate text-xs text-muted-foreground">{integ.base_url}{integ.preset ? ` · ${integ.preset}` : ""}{integ.auth_type && integ.auth_type !== "none" ? ` · ${integ.auth_type}` : ""}{integ.api_key ? ` · key ${integ.api_key}` : ""}</div>
        </div>
        <div className="flex gap-1.5 opacity-0 transition-opacity group-hover:opacity-100">
          <button onClick={runTest} title="Test" className="text-muted-foreground hover:text-foreground"><Send className="size-4" /></button>
          <button onClick={() => setEditing((v) => !v)} title="Edit" className="text-muted-foreground hover:text-foreground"><Pencil className="size-4" /></button>
          <button onClick={() => { if (confirm(`Delete integration "${integ.name}"?`)) remove.mutate(integ.id) }} title="Delete" className="text-muted-foreground hover:text-destructive"><Trash2 className="size-4" /></button>
        </div>
      </div>
      {result && <p className="mt-2 text-xs text-muted-foreground">{result}</p>}
      {editing && (
        <div className="mt-2 space-y-2 border-t pt-2">
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="Base URL" className={inp} />
          <input value={apiKey} onChange={(e) => setApiKey(e.target.value)} type="password" placeholder="API key (leave blank to keep current)" autoComplete="off" className={inp} />
          {err && <p className="text-xs text-destructive">{err}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => { setEditing(false); setApiKey(""); setBaseUrl(integ.base_url) }}>Cancel</Button>
            <Button size="sm" disabled={update.isPending} onClick={save}>{update.isPending ? "Saving…" : "Save"}</Button>
          </div>
        </div>
      )}
    </div>
  )
}

function IntegrationsSection() {
  const { data } = useIntegrations()
  const { data: presets } = useIntegrationPresets()
  const { add } = useIntegrationMutations()
  const [open, setOpen] = useState(false)
  const [preset, setPreset] = useState("")
  const [name, setName] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [err, setErr] = useState("")
  if (!data?.admin) return null
  const presetEntries = Object.entries(presets || {})
  const chosen = preset ? (presets || {})[preset] : undefined
  const reset = () => { setPreset(""); setName(""); setBaseUrl(""); setApiKey(""); setErr(""); setOpen(false) }
  const submit = () => {
    const finalName = name.trim() || chosen?.name || ""
    if (!finalName) { setErr("Name required"); return }
    if (!baseUrl.trim()) { setErr("Base URL required"); return }
    setErr("")
    add.mutate(
      { preset: preset || undefined, name: finalName, base_url: baseUrl.trim(), api_key: apiKey.trim() || undefined },
      { onSuccess: reset, onError: (e) => setErr(e instanceof Error ? e.message : "Failed") },
    )
  }
  return (
    <section>
      <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><Plug className="size-3.5" />Integrations <span className="normal-case text-muted-foreground/70">(admin)</span></h2>
      <div className="space-y-2">
        {data.items.map((i) => <IntegrationRow key={i.id} integ={i} />)}
        {data.items.length === 0 && <p className="py-2 text-sm text-muted-foreground">No integrations configured.</p>}
      </div>
      {open ? (
        <div className="mt-2 space-y-2 rounded-lg border bg-card p-3">
          <select value={preset} onChange={(e) => { const p = e.target.value; setPreset(p); const pr = (presets || {})[p]; if (pr && !name.trim()) setName(pr.name) }} className={inp}>
            <option value="">Custom (no preset)</option>
            {presetEntries.map(([k, v]) => <option key={k} value={k}>{v.name}</option>)}
          </select>
          {chosen?.description && (
            <div className="max-h-40 overflow-y-auto rounded-md border bg-muted/40 p-2 text-xs text-muted-foreground">
              <Markdown>{chosen.description}</Markdown>
            </div>
          )}
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (e.g. Home Assistant)" className={inp} />
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="Base URL (https://…)" className={inp} />
          <input value={apiKey} onChange={(e) => setApiKey(e.target.value)} type="password" placeholder={chosen?.auth_type === "none" ? "API key (not needed)" : "API key / token"} autoComplete="off" className={inp} />
          {err && <p className="text-xs text-destructive">{err}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={reset}>Cancel</Button>
            <Button size="sm" disabled={add.isPending} onClick={submit}>{add.isPending ? "Adding…" : "Add integration"}</Button>
          </div>
        </div>
      ) : (
        <Button variant="outline" size="sm" className="mt-2" onClick={() => setOpen(true)}><Plus className="size-4" />Add integration</Button>
      )}
    </section>
  )
}

function FeaturesSection() {
  const { data } = useFeatures()
  const setFeature = useSetFeature()
  if (!data || Object.keys(data).length === 0) return null
  return (
    <section>
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Features <span className="normal-case text-muted-foreground/70">(admin)</span></h2>
      <div className="rounded-lg border bg-card p-3">
        {Object.entries(data).map(([k, v]) => (
          <div key={k} className="flex items-center justify-between py-1.5">
            <span className="text-sm capitalize text-muted-foreground">{k.replace(/_/g, " ")}</span>
            <button onClick={() => setFeature.mutate({ key: k, value: !v })} className={cn("relative h-5 w-9 shrink-0 rounded-full transition-colors", v ? "bg-primary" : "bg-input")}><span className={cn("absolute top-0.5 size-4 rounded-full bg-background transition-transform", v ? "translate-x-4" : "translate-x-0.5")} /></button>
          </div>
        ))}
      </div>
    </section>
  )
}

export function AdminSections() {
  return (<><FeaturesSection /><IntegrationsSection /><McpSection /><WebhookSection /></>)
}
