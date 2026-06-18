import { useRef, useState } from "react"
import { Plus, Trash2, Pencil, Copy, Check, X, Upload } from "lucide-react"
import { useSettings, useSaveSettings, type Settings } from "@/api/settings"
import { useModels } from "@/api/models"
import { useIntegrations } from "@/api/integrations"
import { useTokens, useTokenProfiles, useTokenMutations } from "@/api/tokens"
import { apiFetch } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { SectionCard, Row, SettingSwitch, SettingSelect, SettingText, SettingNumber, SettingTextarea, StringListEditor } from "./fields"

const tokInp = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"
const WIPE_KINDS = ["chats", "memory", "skills", "notes", "tasks", "documents", "gallery", "calendar"] as const

// Shared accessor for the admin settings store. `set` posts a partial patch.
function useStore() {
  const { data } = useSettings()
  const save = useSaveSettings()
  const s = (data || {}) as Settings
  const set = (patch: Settings) => save.mutate(patch)
  const str = (k: string, d = "") => (s[k] == null ? d : String(s[k]))
  const num = (k: string, d = 0) => (typeof s[k] === "number" ? (s[k] as number) : Number(s[k] ?? d) || d)
  const bool = (k: string) => !!s[k]
  const list = (k: string): string[] => (Array.isArray(s[k]) ? (s[k] as unknown[]).map(String) : [])
  return { s, set, str, num, bool, list, saving: save.isPending }
}

function useModelOptions() {
  const { data: models } = useModels()
  const all = (models?.items || []).flatMap((e) => [...(e.models || []), ...(e.models_extra || [])])
  const opts = [{ value: "", label: "— (default)" }, ...all.map((m) => ({ value: m, label: m }))]
  return opts
}

const H = "mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground"

// ─────────────────────────── Search ───────────────────────────
export function SearchSettings() {
  const { str, num, set, list } = useStore()
  const [test, setTest] = useState("")
  const [testing, setTesting] = useState(false)
  const provider = str("search_provider", "searxng")
  const runTest = async () => {
    setTesting(true); setTest("Searching…")
    try {
      const r = await apiFetch("/api/search/query", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query: "odysseus test", count: 3 }) })
      const j = await r.json().catch(() => ({}))
      const n = (j.results || j.items || []).length
      setTest(r.ok ? `OK · ${n} results via ${j.provider || provider}` : `Failed: ${j.detail || j.error || r.status}`)
    } catch { setTest("Test failed") } finally { setTesting(false) }
  }
  const needsKey: Record<string, string[]> = {
    brave: ["brave_api_key"], tavily: ["tavily_api_key"], serper: ["serper_api_key"],
    google_pse: ["google_pse_key", "google_pse_cx"],
  }
  return (
    <section>
      <h2 className={H}>Search</h2>
      <SectionCard>
        <SettingSelect label="Provider" value={provider} onChange={(v) => set({ search_provider: v })}
          options={["searxng", "duckduckgo", "brave", "google_pse", "tavily", "serper", "disabled"].map((p) => ({ value: p, label: p }))} />
        <SettingNumber label="Results per query" value={num("search_result_count", 5)} onCommit={(v) => set({ search_result_count: v })} min={1} max={20} />
        <SettingSelect label="SafeSearch" value={str("search_safesearch", "strict")} onChange={(v) => set({ search_safesearch: v })}
          options={["strict", "moderate", "off"].map((p) => ({ value: p, label: p }))} />
        <SettingText label="Search URL" hint="Self-hosted SearXNG or custom backend (optional)" value={str("search_url")} onCommit={(v) => set({ search_url: v })} placeholder="https://searx.example.com" />
        {(needsKey[provider] || []).map((k) => (
          <SettingText key={k} label={k.replace(/_/g, " ")} value={str(k)} onCommit={(v) => set({ [k]: v })} type="password" placeholder="API key" />
        ))}
        <StringListEditor label="Fallback chain" hint="Providers tried if the primary fails" value={list("search_fallback_chain")} onChange={(v) => set({ search_fallback_chain: v })} placeholder="e.g. duckduckgo" />
        <Row label="Test search"><Button size="sm" variant="outline" onClick={runTest} disabled={testing}>Run test</Button></Row>
        {test && <p className="text-xs text-muted-foreground">{test}</p>}
      </SectionCard>
    </section>
  )
}

// ─────────────────────────── Reminders ───────────────────────────
export function RemindersSettings() {
  const { str, bool, set } = useStore()
  const { data: integrations } = useIntegrations()
  const channel = str("reminder_channel", "browser")
  const [test, setTest] = useState("")
  const fireTest = async () => {
    setTest("Sending…")
    try {
      const r = await apiFetch("/api/notes/fire-reminder", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: "Test reminder", message: "This is a test reminder from Odysseus." }) })
      setTest(r.ok ? "Sent — check your channel." : `Failed: ${r.status}`)
    } catch { setTest("Test failed") } finally { setTimeout(() => setTest(""), 6000) }
  }
  return (
    <section>
      <h2 className={H}>Reminders</h2>
      <SectionCard>
        <SettingSelect label="Channel" value={channel} onChange={(v) => set({ reminder_channel: v })}
          options={["browser", "email", "ntfy", "webhook"].map((p) => ({ value: p, label: p }))} />
        {channel === "email" && <SettingText label="Send to email" value={str("reminder_email_to")} onCommit={(v) => set({ reminder_email_to: v })} type="email" placeholder="you@example.com" />}
        {channel === "ntfy" && <SettingText label="ntfy topic" value={str("reminder_ntfy_topic", "Reminders")} onCommit={(v) => set({ reminder_ntfy_topic: v })} placeholder="Reminders" />}
        {channel === "webhook" && <>
          <SettingSelect label="Webhook integration" value={str("reminder_webhook_integration_id")} onChange={(v) => set({ reminder_webhook_integration_id: v })}
            options={[{ value: "", label: "— select —" }, ...(integrations?.items || []).map((i) => ({ value: i.id, label: i.name || i.id }))]} />
          <SettingTextarea label="Payload template (JSON)" hint="Use {{title}} and {{message}} placeholders" value={str("reminder_webhook_payload_template")} onCommit={(v) => set({ reminder_webhook_payload_template: v })} mono rows={3} />
        </>}
        <SettingSwitch label="AI synthesis" hint="Rewrite reminders in a chosen persona before sending" value={bool("reminder_llm_synthesis")} onChange={(v) => set({ reminder_llm_synthesis: v })} />
        {bool("reminder_llm_synthesis") && <SettingText label="Persona" value={str("reminder_llm_persona")} onCommit={(v) => set({ reminder_llm_persona: v })} placeholder="e.g. a cheerful assistant" />}
        <Row label="Test reminder"><Button size="sm" variant="outline" onClick={fireTest}>Send test</Button></Row>
        {test && <p className="text-xs text-muted-foreground">{test}</p>}
      </SectionCard>
    </section>
  )
}

// ─────────────────────── Agent / Tools (admin) ───────────────────────
export function AgentToolsSettings() {
  const { num, bool, set, list } = useStore()
  return (
    <section>
      <h2 className={H}>Agent &amp; tools <span className="normal-case text-muted-foreground/70">(admin)</span></h2>
      <SectionCard>
        <SettingNumber label="Max tool calls / message" hint="0 = unlimited" value={num("agent_max_tool_calls", 0)} onCommit={(v) => set({ agent_max_tool_calls: v })} min={0} max={1000} />
        <SettingNumber label="Max steps / message" value={num("agent_max_rounds", 20)} onCommit={(v) => set({ agent_max_rounds: v })} min={1} max={200} />
        <SettingNumber label="Input token budget" hint="6000 = auto (scale to context window); 0 = off" value={num("agent_input_token_budget", 6000)} onCommit={(v) => set({ agent_input_token_budget: v })} min={0} />
        <SettingNumber label="Input token hard max" value={num("agent_input_token_hard_max", 200000)} onCommit={(v) => set({ agent_input_token_hard_max: v })} min={0} />
        <SettingNumber label="Stream timeout (s)" value={num("agent_stream_timeout_seconds", 300)} onCommit={(v) => set({ agent_stream_timeout_seconds: v })} min={10} />
        <SettingSwitch label="Confirm before sending email" hint="Agent stages emails for review instead of sending directly" value={bool("agent_email_confirm")} onChange={(v) => set({ agent_email_confirm: v })} />
        <StringListEditor label="Extra file path roots" hint="Absolute paths read_file/write_file may also access" value={list("tool_path_extra_roots")} onChange={(v) => set({ tool_path_extra_roots: v })} placeholder="/abs/path" />
      </SectionCard>
    </section>
  )
}

// ─────────────────────── AI / Models (admin) ───────────────────────
export function AiModelsSettings() {
  const { str, num, bool, set } = useStore()
  const models = useModelOptions()
  return (
    <section>
      <h2 className={H}>AI models <span className="normal-case text-muted-foreground/70">(admin)</span></h2>
      <SectionCard>
        <SettingText label="Public app URL" hint="Base URL for deep-links in alerts" value={str("app_public_url")} onCommit={(v) => set({ app_public_url: v })} placeholder="https://chat.example.com" />
        <div className="border-t pt-2 text-xs font-medium text-muted-foreground">Utility model (summaries, naming)</div>
        <SettingSelect label="Utility model" value={str("utility_model")} onChange={(v) => set({ utility_model: v })} options={models} />
        <div className="border-t pt-2 text-xs font-medium text-muted-foreground">Vision</div>
        <SettingSwitch label="Vision enabled" value={bool("vision_enabled")} onChange={(v) => set({ vision_enabled: v })} />
        <SettingSelect label="Vision model" value={str("vision_model")} onChange={(v) => set({ vision_model: v })} options={models} />
        <div className="border-t pt-2 text-xs font-medium text-muted-foreground">Image generation</div>
        <SettingSwitch label="Image generation enabled" value={bool("image_gen_enabled")} onChange={(v) => set({ image_gen_enabled: v })} />
        <SettingSelect label="Image model" value={str("image_model")} onChange={(v) => set({ image_model: v })} options={models} />
        <SettingSelect label="Image quality" value={str("image_quality", "medium")} onChange={(v) => set({ image_quality: v })} options={["low", "medium", "high"].map((q) => ({ value: q, label: q }))} />
        <div className="border-t pt-2 text-xs font-medium text-muted-foreground">Deep research</div>
        <SettingSelect label="Research model" value={str("research_model")} onChange={(v) => set({ research_model: v })} options={models} />
        <SettingNumber label="Research max tokens" value={num("research_max_tokens", 16384)} onCommit={(v) => set({ research_max_tokens: v })} min={256} />
        <SettingNumber label="Research run timeout (s)" hint="0 = unlimited" value={num("research_run_timeout_seconds", 1800)} onCommit={(v) => set({ research_run_timeout_seconds: v })} min={0} />
        <div className="border-t pt-2 text-xs font-medium text-muted-foreground">Teacher &amp; skills</div>
        <SettingSwitch label="Teacher enabled" value={bool("teacher_enabled")} onChange={(v) => set({ teacher_enabled: v })} />
        <SettingSelect label="Teacher model" value={str("teacher_model")} onChange={(v) => set({ teacher_model: v })} options={models} />
        <SettingNumber label="Skill autosave min confidence" hint="0–1; 0 disables the gate" value={num("skill_autosave_min_confidence", 0.85)} onCommit={(v) => set({ skill_autosave_min_confidence: v })} min={0} max={1} step={0.05} />
        <SettingNumber label="Max skills injected" value={num("skill_max_injected", 3)} onCommit={(v) => set({ skill_max_injected: v })} min={0} max={20} />
        <div className="border-t pt-2 text-xs font-medium text-muted-foreground">Speech</div>
        <SettingSwitch label="TTS enabled" value={bool("tts_enabled")} onChange={(v) => set({ tts_enabled: v })} />
        <SettingText label="TTS provider" value={str("tts_provider", "disabled")} onCommit={(v) => set({ tts_provider: v })} placeholder="openai · elevenlabs · kokoro · disabled" />
        <SettingText label="TTS model" value={str("tts_model")} onCommit={(v) => set({ tts_model: v })} placeholder="tts-1" />
        <SettingText label="TTS voice" value={str("tts_voice")} onCommit={(v) => set({ tts_voice: v })} placeholder="alloy" />
        <SettingText label="TTS speed" value={str("tts_speed", "1")} onCommit={(v) => set({ tts_speed: v })} placeholder="1.0" />
        <SettingSwitch label="STT enabled" value={bool("stt_enabled")} onChange={(v) => set({ stt_enabled: v })} />
        <SettingText label="STT provider" value={str("stt_provider", "disabled")} onCommit={(v) => set({ stt_provider: v })} placeholder="openai · whisper · disabled" />
        <SettingText label="STT model" value={str("stt_model")} onCommit={(v) => set({ stt_model: v })} placeholder="base" />
      </SectionCard>
    </section>
  )
}

// ─────────────────────── API tokens (admin) ───────────────────────
export function ApiTokensSection() {
  const { data: tokens } = useTokens()
  const { data: profiles } = useTokenProfiles()
  const { create, rename, remove } = useTokenMutations()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [profile, setProfile] = useState("")
  const [created, setCreated] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [err, setErr] = useState("")
  const profileNames = Object.keys(profiles?.profiles || {})
  const add = () => {
    if (!name.trim()) { setErr("Name required"); return }
    setErr("")
    create.mutate({ name: name.trim(), profile: profile || undefined }, {
      onSuccess: (t) => { setCreated(t.token); setName(""); setProfile(""); setOpen(false) },
      onError: (e) => setErr(e instanceof Error ? e.message : "Failed"),
    })
  }
  const copy = async () => { if (!created) return; try { await navigator.clipboard.writeText(created); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { /* ignore */ } }
  return (
    <section>
      <h2 className={H}>API tokens <span className="normal-case text-muted-foreground/70">(admin)</span></h2>
      <div className="space-y-2">
        {(tokens || []).map((t) => (
          <div key={t.id} className="group flex items-center gap-2 rounded-lg border bg-card p-3">
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{t.name} <span className="font-mono text-xs font-normal text-muted-foreground">{t.token_prefix}…</span></div>
              <div className="truncate text-xs text-muted-foreground">{t.scopes.join(", ") || "default"}{t.last_used_at ? ` · used ${new Date(t.last_used_at).toLocaleDateString()}` : " · never used"}</div>
            </div>
            <button onClick={() => { const n = prompt("Rename token", t.name); if (n && n.trim()) rename.mutate({ id: t.id, name: n.trim() }) }} className="shrink-0 text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover:opacity-100" title="Rename"><Pencil className="size-4" /></button>
            <button onClick={() => { if (confirm(`Revoke token "${t.name}"? Apps using it will stop working.`)) remove.mutate(t.id) }} className="shrink-0 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100" title="Revoke"><Trash2 className="size-4" /></button>
          </div>
        ))}
        {(tokens || []).length === 0 && <p className="py-1 text-sm text-muted-foreground">No API tokens.</p>}
      </div>
      {created && (
        <div className="mt-2 space-y-1.5 rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-3">
          <div className="text-xs font-medium text-muted-foreground">Copy this token now — it won't be shown again.</div>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 truncate rounded bg-muted px-2 py-1.5 font-mono text-xs">{created}</code>
            <button onClick={copy} className="shrink-0 rounded-md p-1.5 hover:bg-accent">{copied ? <Check className="size-4" /> : <Copy className="size-4" />}</button>
            <button onClick={() => setCreated(null)} className="shrink-0 rounded-md p-1.5 hover:bg-accent"><X className="size-4" /></button>
          </div>
        </div>
      )}
      {open ? (
        <div className="mt-2 space-y-2 rounded-lg border bg-card p-3">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Token name (e.g. Claude Code)" className={tokInp} />
          {profileNames.length > 0 && (
            <select value={profile} onChange={(e) => setProfile(e.target.value)} className={tokInp}>
              <option value="">Default scopes</option>
              {profileNames.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          )}
          {err && <p className="text-xs text-destructive">{err}</p>}
          <div className="flex justify-end gap-2"><Button variant="ghost" size="sm" onClick={() => setOpen(false)}>Cancel</Button><Button size="sm" disabled={create.isPending} onClick={add}>{create.isPending ? "Creating…" : "Create token"}</Button></div>
        </div>
      ) : <Button variant="outline" size="sm" className="mt-2" onClick={() => setOpen(true)}><Plus className="size-4" />New API token</Button>}
    </section>
  )
}

// ─────────────────────── Data & backup (admin) ───────────────────────
export function DataSection() {
  const [importMsg, setImportMsg] = useState("")
  const [wipeMsg, setWipeMsg] = useState("")
  const fileRef = useRef<HTMLInputElement>(null)
  const onImport = async (file?: File) => {
    if (!file) return
    setImportMsg("Importing…")
    try {
      const data = JSON.parse(await file.text())
      const r = await apiFetch("/api/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) })
      const j = await r.json().catch(() => ({}))
      setImportMsg(r.ok ? `Imported: ${(j.imported || []).join(", ") || "done"}` : `Failed: ${j.detail || r.status}`)
    } catch (e) { setImportMsg(e instanceof SyntaxError ? "Not valid JSON" : "Import failed") }
    finally { if (fileRef.current) fileRef.current.value = "" }
  }
  const wipe = async (kind: string) => {
    if (!confirm(`Permanently delete ALL ${kind}? This cannot be undone.`)) return
    setWipeMsg(`Wiping ${kind}…`)
    try {
      const r = await apiFetch(`/api/admin/wipe/${kind}`, { method: "DELETE" })
      const j = await r.json().catch(() => ({}))
      setWipeMsg(r.ok ? `Deleted ${j.count ?? ""} ${kind}.` : `Failed: ${j.detail || r.status}`)
    } catch { setWipeMsg("Wipe failed") }
  }
  return (
    <section>
      <h2 className={H}>Data &amp; backup <span className="normal-case text-muted-foreground/70">(admin)</span></h2>
      <SectionCard>
        <Row label="Export all data"><Button size="sm" variant="outline" onClick={() => window.open("/api/export", "_blank")}>Download JSON</Button></Row>
        <Row label="Import data" hint="Merge a previously exported JSON export">
          <input ref={fileRef} type="file" accept="application/json,.json" className="hidden" onChange={(e) => onImport(e.target.files?.[0])} />
          <Button size="sm" variant="outline" onClick={() => fileRef.current?.click()}><Upload className="size-4" />Choose file</Button>
        </Row>
        {importMsg && <p className="text-xs text-muted-foreground">{importMsg}</p>}
      </SectionCard>
      <div className="mt-3 rounded-lg border border-destructive/40 bg-destructive/5 p-3">
        <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-destructive">Danger zone</div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {WIPE_KINDS.map((k) => (
            <Button key={k} size="sm" variant="outline" className="border-destructive/40 capitalize text-destructive hover:bg-destructive/10" onClick={() => wipe(k)}>Wipe {k}</Button>
          ))}
        </div>
        {wipeMsg && <p className="mt-2 text-xs text-muted-foreground">{wipeMsg}</p>}
      </div>
    </section>
  )
}

// All admin settings-store sections, gated by the caller on is_admin.
export function AppSettingsSections() {
  return (<><AiModelsSettings /><SearchSettings /><RemindersSettings /><AgentToolsSettings /><ApiTokensSection /><DataSection /></>)
}
