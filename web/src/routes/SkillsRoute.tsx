import { useEffect, useMemo, useRef, useState } from "react"
import { Trash2, Sparkles, Wrench, Pencil, ArrowLeft, Save, Plus, Play, FlaskConical, Link2, RotateCcw, CheckCircle2, XCircle, AlertTriangle, HelpCircle, Loader2, Search, X, Eye, EyeOff, ListChecks } from "lucide-react"
import { useSkills, useBuiltinSkills, useSkillMarkdown, useSkillMutations, useRunSkill, useStartSkillTest, useSkillTestStatus, useBuiltinSkill, useAuditAllStatus, useCancelAuditAll } from "@/api/skills"
import type { SkillVerdict, SkillRow, AuditResult } from "@/api/skills"
import { Button } from "@/components/ui/button"
import { Markdown } from "@/components/chat/Markdown"
import { cn } from "@/lib/utils"

const inp = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"
const ctrl = "h-9 rounded-md border bg-background px-2 text-sm outline-none focus-visible:border-ring"

type SortKey = "confidence" | "uses" | "az" | "recent"
type StatusFilter = "all" | "published" | "draft"
type ConfFilter = "all" | "high" | "med" | "low"

const AUDIT_RESULT_STYLE: Record<string, string> = {
  pass: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  pass_after_self_edit: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  pass_after_teacher: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  inconclusive: "bg-muted text-muted-foreground",
  skipped: "bg-muted text-muted-foreground",
  flagged: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  error: "bg-destructive/15 text-destructive",
}

// A skill is "non-passing" if it was audited and the verdict isn't a pass, or
// it has been demoted to draft after audit. Used by "delete non-passing".
function isNonPassing(s: SkillRow): boolean {
  const v = (s.audit_verdict || "").toLowerCase()
  if (!v) return false
  return v !== "pass" && v !== "passed"
}

const VERDICT_STYLE: Record<string, { cls: string; Icon: typeof CheckCircle2; label: string }> = {
  pass: { cls: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400", Icon: CheckCircle2, label: "Pass" },
  needs_work: { cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400", Icon: AlertTriangle, label: "Needs work" },
  fail: { cls: "bg-destructive/15 text-destructive", Icon: XCircle, label: "Fail" },
  inconclusive: { cls: "bg-muted text-muted-foreground", Icon: HelpCircle, label: "Inconclusive" },
  unknown: { cls: "bg-muted text-muted-foreground", Icon: HelpCircle, label: "Unknown" },
}

function VerdictBadge({ verdict }: { verdict: SkillVerdict }) {
  const v = VERDICT_STYLE[verdict.verdict] || VERDICT_STYLE.unknown
  const { Icon } = v
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium", v.cls)}>
      <Icon className="size-3.5" />{v.label}
      {verdict.confidence != null && <span className="opacity-70">{Math.round(verdict.confidence * 100)}%</span>}
    </span>
  )
}

function RunPanel({ id, name, onClose }: { id: string; name: string; onClose: () => void }) {
  const run = useRunSkill()
  const [request, setRequest] = useState("")
  const [output, setOutput] = useState<string | null>(null)
  const [err, setErr] = useState("")
  const submit = () => {
    setErr(""); setOutput(null)
    run.mutate({ id, request: request.trim() }, {
      onSuccess: (d) => setOutput(d.message),
      onError: (e) => setErr(e instanceof Error ? e.message : "Run failed"),
    })
  }
  return (
    <div className="absolute inset-0 z-10 flex animate-fade-in items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col animate-pop-in rounded-xl border bg-popover p-4 shadow-lg">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold"><Play className="size-4" /> Run {name}</div>
        <textarea value={request} onChange={(e) => setRequest(e.target.value)} placeholder="Your request for this skill…" rows={3} className="w-full resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring" />
        {err && <p className="mt-2 text-xs text-destructive">{err}</p>}
        {output != null && (
          <div className="mt-3 min-h-0 flex-1 overflow-y-auto rounded-md border bg-card p-3 text-sm">
            <Markdown>{output}</Markdown>
          </div>
        )}
        <div className="mt-3 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onClose}>Close</Button>
          <Button size="sm" disabled={run.isPending} onClick={submit}><Play className="size-4" />{run.isPending ? "Running…" : "Run"}</Button>
        </div>
      </div>
    </div>
  )
}

function TestPanel({ id, name, onClose }: { id: string; name: string; onClose: () => void }) {
  const start = useStartSkillTest()
  const [polling, setPolling] = useState(true)
  const sawRunningRef = useRef(false)
  const pendingRef = useRef(false)
  const { data } = useSkillTestStatus(id, polling)
  const running = data?.status === "running"
  // Stop polling once the run completes — but if we just kicked off a run, keep
  // polling until the backend flips to "running" first, so a stale terminal
  // status from a previous run can't disable polling before this run starts.
  useEffect(() => {
    if (!data) return
    if (data.status === "running") { sawRunningRef.current = true; pendingRef.current = false; return }
    if (pendingRef.current && !sawRunningRef.current) return
    setPolling(false)
  }, [data])
  // A failed start means there's no run to poll for.
  // eslint-disable-next-line react-hooks/set-state-in-effect -- stop polling when the start fails
  useEffect(() => { if (start.isError) { pendingRef.current = false; setPolling(false) } }, [start.isError])
  const begin = () => { pendingRef.current = true; sawRunningRef.current = false; setPolling(true); start.mutate(id) }
  const verdict = data?.verdict
  const log = data?.log || []
  return (
    <div className="absolute inset-0 z-10 flex animate-fade-in items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col animate-pop-in rounded-xl border bg-popover p-4 shadow-lg">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold"><FlaskConical className="size-4" /> Test {name}</div>
        <div className="flex items-center gap-2">
          <Button size="sm" disabled={start.isPending || running} onClick={begin}>
            {running ? <><Loader2 className="size-4 animate-spin" />Testing…</> : <><FlaskConical className="size-4" />{data?.status === "done" ? "Re-run test" : "Run test"}</>}
          </Button>
          {verdict && <VerdictBadge verdict={verdict} />}
        </div>
        {start.isError && <p className="mt-2 text-xs text-destructive">{start.error instanceof Error ? start.error.message : "Failed to start"}</p>}
        {verdict?.summary && <p className="mt-3 text-sm text-muted-foreground">{verdict.summary}</p>}
        {verdict?.issues && verdict.issues.length > 0 && (
          <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
            {verdict.issues.map((iss, i) => <li key={i}>{iss}</li>)}
          </ul>
        )}
        {(running || log.length > 0) && (
          <div className="mt-3 min-h-0 flex-1 overflow-y-auto rounded-md border bg-card p-3 font-mono text-[11px] leading-relaxed">
            {log.map((e, i) => {
              if (e.type === "say" && e.text) return <p key={i} className="whitespace-pre-wrap">{e.text}</p>
              if (e.type === "tool_start") return <p key={i} className="text-muted-foreground">[{e.tool}] {e.command}</p>
              if (e.type === "tool_output") return <p key={i} className="text-muted-foreground">→ {e.output}</p>
              if (e.type === "agent_step") return <p key={i} className="text-muted-foreground">— round {e.round} —</p>
              if (e.type === "evaluating") return <p key={i} className="text-muted-foreground">Evaluating…</p>
              if (e.type === "error") return <p key={i} className="text-destructive">error: {e.error}</p>
              return null
            })}
          </div>
        )}
        <div className="mt-3 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onClose}>Close</Button>
        </div>
      </div>
    </div>
  )
}

function ImportForm({ onClose }: { onClose: () => void }) {
  const { importFromUrl } = useSkillMutations()
  const [url, setUrl] = useState("")
  const [err, setErr] = useState("")
  const [done, setDone] = useState("")
  const submit = () => {
    if (url.trim().length < 8) { setErr("Enter a valid URL"); return }
    setErr(""); setDone("")
    importFromUrl.mutate(url.trim(), {
      onSuccess: (d) => { setDone(`Imported ${d.skill?.name || "skill"} (${d.files} file${d.files === 1 ? "" : "s"})`); setUrl("") },
      onError: (e) => setErr(e instanceof Error ? e.message : "Import failed"),
    })
  }
  return (
    <div className="absolute inset-0 z-10 flex animate-fade-in items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg animate-pop-in rounded-xl border bg-popover p-4 shadow-lg">
        <div className="mb-1 flex items-center gap-2 text-sm font-semibold"><Link2 className="size-4" /> Import from URL</div>
        <p className="mb-3 text-xs text-muted-foreground">Install a SKILL.md bundle from a public GitHub or skills.sh URL.</p>
        <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://github.com/…/SKILL.md" className={inp} />
        {err && <p className="mt-2 text-xs text-destructive">{err}</p>}
        {done && <p className="mt-2 text-xs text-emerald-600 dark:text-emerald-400">{done}</p>}
        <div className="mt-3 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onClose}>Close</Button>
          <Button size="sm" disabled={importFromUrl.isPending} onClick={submit}>{importFromUrl.isPending ? "Importing…" : "Import"}</Button>
        </div>
      </div>
    </div>
  )
}

function BuiltinEditor({ name, onClose }: { name: string; onClose: () => void }) {
  const { data, isLoading } = useBuiltinSkill(name)
  const { saveBuiltinOverride, resetBuiltinOverride } = useSkillMutations()
  const [text, setText] = useState("")
  const [dirty, setDirty] = useState(false)
  const [err, setErr] = useState("")
  // eslint-disable-next-line react-hooks/set-state-in-effect -- seed editor from async-loaded builtin override text
  useEffect(() => { if (data?.text != null) { setText(data.text); setDirty(false) } }, [data])
  const save = () => {
    setErr("")
    saveBuiltinOverride.mutate({ name, text }, { onSuccess: () => setDirty(false), onError: (e) => setErr(e instanceof Error ? e.message : "Save failed") })
  }
  const revert = () => {
    setErr("")
    resetBuiltinOverride.mutate(name, {
      onSuccess: () => { if (data?.default != null) { setText(data.default); setDirty(false) } },
      onError: (e) => setErr(e instanceof Error ? e.message : "Reset failed"),
    })
  }
  return (
    <div className="absolute inset-0 z-10 flex animate-fade-in items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col animate-pop-in rounded-xl border bg-popover p-4 shadow-lg">
        <div className="mb-1 flex items-center gap-2 text-sm font-semibold"><Wrench className="size-4" /> Override {name}</div>
        <p className="mb-3 text-xs text-amber-600 dark:text-amber-400">Editing changes how the assistant is told to use this built-in tool.</p>
        {err && <p className="mb-2 text-xs text-destructive">{err}</p>}
        {isLoading ? <div className="py-6 text-sm text-muted-foreground">Loading…</div> : (
          <textarea value={text} onChange={(e) => { setText(e.target.value); setDirty(true) }} spellCheck={false} rows={14} className="min-h-0 flex-1 resize-none rounded-md border bg-background p-3 font-mono text-xs leading-relaxed outline-none focus-visible:border-ring" />
        )}
        <div className="mt-3 flex items-center justify-between gap-2">
          <Button variant="outline" size="sm" disabled={!data?.is_overridden || resetBuiltinOverride.isPending} onClick={revert}><RotateCcw className="size-4" />Revert to default</Button>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={onClose}>Close</Button>
            <Button size="sm" disabled={!dirty || saveBuiltinOverride.isPending} onClick={save}><Save className="size-4" />{saveBuiltinOverride.isPending ? "Saving…" : "Save"}</Button>
          </div>
        </div>
      </div>
    </div>
  )
}

function SkillEditor({ id, onBack }: { id: string; onBack: () => void }) {
  const { data, isLoading } = useSkillMarkdown(id)
  const { saveMarkdown } = useSkillMutations()
  const [md, setMd] = useState("")
  const [dirty, setDirty] = useState(false)
  const [err, setErr] = useState("")
  // eslint-disable-next-line react-hooks/set-state-in-effect -- seed editor from async-loaded skill markdown
  useEffect(() => { if (data?.markdown != null) { setMd(data.markdown); setDirty(false) } }, [data])
  const save = () => { setErr(""); saveMarkdown.mutate({ id, markdown: md }, { onSuccess: () => setDirty(false), onError: (e) => setErr(e instanceof Error ? e.message : "Save failed") }) }
  return (
    <div className="flex h-full flex-col">
      <header className="flex h-13 shrink-0 items-center gap-2 border-b px-3">
        <Button variant="ghost" size="icon" onClick={onBack} title="Back"><ArrowLeft className="size-4" /></Button>
        <div className="min-w-0 flex-1 truncate text-sm font-semibold">{data?.name || id}</div>
        <Button size="sm" onClick={save} disabled={!dirty || saveMarkdown.isPending}><Save className="size-4" />{saveMarkdown.isPending ? "Saving…" : dirty ? "Save" : "Saved"}</Button>
      </header>
      {err && <p className="px-4 pt-2 text-xs text-destructive">{err}</p>}
      {isLoading ? <div className="p-6 text-sm text-muted-foreground">Loading…</div> : (
        <textarea value={md} onChange={(e) => { setMd(e.target.value); setDirty(true) }} spellCheck={false} className="min-h-0 flex-1 resize-none bg-transparent p-4 font-mono text-xs leading-relaxed outline-none" />
      )}
    </div>
  )
}

function CreateForm({ onClose }: { onClose: () => void }) {
  const { create } = useSkillMutations()
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [whenToUse, setWhenToUse] = useState("")
  const [procedure, setProcedure] = useState("")
  const [err, setErr] = useState("")
  const submit = () => {
    if (!name.trim() || !description.trim() || !procedure.trim()) { setErr("Name, description and procedure are required"); return }
    setErr("")
    create.mutate({ name: name.trim(), description: description.trim(), when_to_use: whenToUse.trim(), procedure: procedure.trim() }, {
      onSuccess: onClose, onError: (e) => setErr(e instanceof Error ? e.message : "Create failed"),
    })
  }
  return (
    <div className="absolute inset-0 z-10 flex animate-fade-in items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg animate-pop-in rounded-xl border bg-popover p-4 shadow-lg">
        <div className="mb-3 text-sm font-semibold">New skill</div>
        <div className="space-y-2">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (e.g. deploy-checklist)" className={inp} />
          <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="One-line description" className={inp} />
          <input value={whenToUse} onChange={(e) => setWhenToUse(e.target.value)} placeholder="When to use (optional)" className={inp} />
          <textarea value={procedure} onChange={(e) => setProcedure(e.target.value)} placeholder="Procedure / steps…" rows={6} className="w-full resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring" />
        </div>
        {err && <p className="mt-2 text-xs text-destructive">{err}</p>}
        <div className="mt-3 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" disabled={create.isPending} onClick={submit}>{create.isPending ? "Creating…" : "Create"}</Button>
        </div>
      </div>
    </div>
  )
}

function AuditAllPanel({ onClose }: { onClose: () => void }) {
  const [polling, setPolling] = useState(true)
  const { data } = useAuditAllStatus(polling)
  const cancel = useCancelAuditAll()
  const running = data?.status === "running"
  // eslint-disable-next-line react-hooks/set-state-in-effect -- stop polling once the audit job settles
  useEffect(() => { if (data && data.status !== "running") setPolling(false) }, [data])
  const total = data?.total || 0
  const done = data?.done || 0
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  const results = data?.results || []
  return (
    <div className="absolute inset-0 z-10 flex animate-fade-in items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col animate-pop-in rounded-xl border bg-popover p-4 shadow-lg">
        <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
          {running ? <Loader2 className="size-4 animate-spin" /> : <CheckCircle2 className="size-4" />}
          Audit all
          {data?.model && <span className="text-[11px] font-normal text-muted-foreground">· {data.model}{data.teacher ? ` + ${data.teacher}` : ""}</span>}
        </div>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
            <div className="h-full bg-primary transition-all" style={{ width: `${pct}%` }} />
          </div>
          <span className="tabular-nums">{done}/{total}</span>
        </div>
        {running && data?.current && <p className="mt-2 text-xs text-muted-foreground">Auditing <span className="font-medium text-foreground">{data.current}</span>…</p>}
        {data?.status === "cancelled" && <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">Cancelled.</p>}
        {data?.status === "done" && <p className="mt-2 text-xs text-emerald-600 dark:text-emerald-400">Done — {results.length} skill{results.length === 1 ? "" : "s"} audited.</p>}
        {data?.status === "none" && <p className="mt-2 text-xs text-muted-foreground">No audit is running.</p>}
        {results.length > 0 && (
          <div className="mt-3 min-h-0 flex-1 space-y-1 overflow-y-auto rounded-md border bg-card p-2 text-xs">
            {results.map((r: AuditResult, i) => (
              <div key={i} className="flex items-center justify-between gap-2">
                <span className="truncate font-medium">{r.skill}</span>
                <span className="flex shrink-0 items-center gap-1.5">
                  {r.skill_state?.status && <span className={cn("rounded-full px-1.5 py-0.5 text-[10px] capitalize", r.skill_state.status === "published" ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" : "bg-muted text-muted-foreground")}>{r.skill_state.status}</span>}
                  <span className={cn("rounded-full px-1.5 py-0.5 text-[10px]", AUDIT_RESULT_STYLE[r.result] || "bg-muted text-muted-foreground")}>{r.result.replace(/_/g, " ")}</span>
                </span>
              </div>
            ))}
          </div>
        )}
        <div className="mt-3 flex justify-end gap-2">
          {running
            ? <Button variant="outline" size="sm" disabled={cancel.isPending} onClick={() => cancel.mutate()}><X className="size-4" />{cancel.isPending ? "Cancelling…" : "Cancel"}</Button>
            : <Button variant="outline" size="sm" onClick={onClose}>Close</Button>}
        </div>
      </div>
    </div>
  )
}

export function SkillsRoute() {
  const { data: skills } = useSkills()
  const { data: builtin } = useBuiltinSkills()
  const { remove, auditAll, setStatus } = useSkillMutations()
  const [editId, setEditId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [importing, setImporting] = useState(false)
  const [runTarget, setRunTarget] = useState<{ id: string; name: string } | null>(null)
  const [testTarget, setTestTarget] = useState<{ id: string; name: string } | null>(null)
  const [builtinTarget, setBuiltinTarget] = useState<string | null>(null)
  const [auditing, setAuditing] = useState(false)

  // Search / sort / filter
  const [query, setQuery] = useState("")
  const [sort, setSort] = useState<SortKey>("confidence")
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all")
  const [confFilter, setConfFilter] = useState<ConfFilter>("all")

  // Bulk select-mode
  const [selectMode, setSelectMode] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const skillKey = (s: SkillRow) => s.id || s.name

  const visible = useMemo(() => {
    let list = (skills || []).slice()
    const q = query.trim().toLowerCase()
    if (q) list = list.filter((s) => [s.name, s.description, s.category, ...(s.tags || [])].filter(Boolean).some((t) => String(t).toLowerCase().includes(q)))
    if (statusFilter !== "all") list = list.filter((s) => (s.status || "draft") === statusFilter)
    if (confFilter !== "all") list = list.filter((s) => {
      const c = s.confidence ?? 0
      return confFilter === "high" ? c >= 0.8 : confFilter === "med" ? c >= 0.5 && c < 0.8 : c < 0.5
    })
    const cmp: Record<SortKey, (a: SkillRow, b: SkillRow) => number> = {
      confidence: (a, b) => (b.confidence ?? 0) - (a.confidence ?? 0),
      uses: (a, b) => (b.uses ?? 0) - (a.uses ?? 0),
      az: (a, b) => a.name.localeCompare(b.name),
      recent: (a, b) => (b.last_used ?? 0) - (a.last_used ?? 0),
    }
    return list.sort(cmp[sort])
  }, [skills, query, sort, statusFilter, confFilter])

  const allVisibleSelected = visible.length > 0 && visible.every((s) => selected.has(skillKey(s)))
  const toggleSel = (k: string) => setSelected((prev) => { const n = new Set(prev); if (n.has(k)) n.delete(k); else n.add(k); return n })
  const toggleAll = () => setSelected(allVisibleSelected ? new Set() : new Set(visible.map(skillKey)))
  const exitSelect = () => { setSelectMode(false); setSelected(new Set()) }

  const selectedSkills = (skills || []).filter((s) => selected.has(skillKey(s)))
  const bulkDelete = () => { selectedSkills.forEach((s) => { if (s.id) remove.mutate(s.id) }); exitSelect() }
  const bulkPublish = () => { selectedSkills.forEach((s) => setStatus.mutate({ id: skillKey(s), status: "published" })); exitSelect() }
  const bulkAudit = () => { const names = selectedSkills.map((s) => s.name).filter(Boolean); if (names.length) { auditAll.mutate({ names }); setAuditing(true) } exitSelect() }
  const deleteNonPassing = () => {
    const targets = (skills || []).filter((s) => isNonPassing(s) && s.id)
    targets.forEach((s) => remove.mutate(s.id!))
  }
  const nonPassingCount = (skills || []).filter((s) => isNonPassing(s) && s.id).length

  if (editId) return <div className="mx-auto h-full w-full max-w-3xl"><SkillEditor id={editId} onBack={() => setEditId(null)} /></div>

  return (
    <div className="relative mx-auto flex h-full w-full max-w-3xl flex-col">
      {creating && <CreateForm onClose={() => setCreating(false)} />}
      {importing && <ImportForm onClose={() => setImporting(false)} />}
      {runTarget && <RunPanel id={runTarget.id} name={runTarget.name} onClose={() => setRunTarget(null)} />}
      {testTarget && <TestPanel id={testTarget.id} name={testTarget.name} onClose={() => setTestTarget(null)} />}
      {builtinTarget && <BuiltinEditor name={builtinTarget} onClose={() => setBuiltinTarget(null)} />}
      {auditing && <AuditAllPanel onClose={() => setAuditing(false)} />}
      <header className="flex h-13 shrink-0 items-center justify-between border-b px-4">
        <span className="text-sm font-semibold">Skills</span>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => (selectMode ? exitSelect() : setSelectMode(true))} title="Select multiple skills"><ListChecks className="size-4" />{selectMode ? "Done" : "Select"}</Button>
          <Button variant="outline" size="sm" onClick={() => setImporting(true)} title="Install a SKILL.md bundle from a URL"><Link2 className="size-4" />Import</Button>
          <Button variant="outline" size="sm" disabled={auditAll.isPending} onClick={() => { auditAll.mutate(undefined); setAuditing(true) }} title="Test, judge & auto-publish skills">{auditAll.isPending ? "Auditing…" : "Audit all"}</Button>
          <Button size="sm" onClick={() => setCreating(true)}><Plus className="size-4" />New skill</Button>
        </div>
      </header>
      <div className="flex-1 space-y-6 overflow-y-auto p-4">
        <section>
          <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><Sparkles className="size-3.5" /> My skills</h2>
          {/* Search + sort + filter toolbar */}
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <div className="relative min-w-[10rem] flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search skills…" className={cn(inp, "pl-8")} />
            </div>
            <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} className={ctrl} title="Sort">
              <option value="confidence">Confidence</option>
              <option value="uses">Most used</option>
              <option value="az">A–Z</option>
              <option value="recent">Recent</option>
            </select>
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as StatusFilter)} className={ctrl} title="Status">
              <option value="all">All status</option>
              <option value="published">Published</option>
              <option value="draft">Draft</option>
            </select>
            <select value={confFilter} onChange={(e) => setConfFilter(e.target.value as ConfFilter)} className={ctrl} title="Confidence">
              <option value="all">Any confidence</option>
              <option value="high">High (≥80%)</option>
              <option value="med">Medium (50–79%)</option>
              <option value="low">Low (&lt;50%)</option>
            </select>
          </div>
          {/* Bulk action bar */}
          {selectMode && (
            <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border bg-muted/40 p-2 text-xs">
              <label className="flex cursor-pointer items-center gap-1.5 px-1">
                <input type="checkbox" checked={allVisibleSelected} onChange={toggleAll} className="size-3.5 accent-primary" />
                Select all
              </label>
              <span className="text-muted-foreground">{selected.size} selected</span>
              <span className="ml-auto flex flex-wrap items-center gap-1.5">
                <Button variant="outline" size="sm" disabled={selected.size === 0} onClick={bulkPublish}><Eye className="size-3.5" />Publish</Button>
                <Button variant="outline" size="sm" disabled={selected.size === 0} onClick={bulkAudit}><FlaskConical className="size-3.5" />Audit</Button>
                <Button variant="outline" size="sm" disabled={selected.size === 0} onClick={bulkDelete} className="text-destructive hover:text-destructive"><Trash2 className="size-3.5" />Delete</Button>
                {nonPassingCount > 0 && <Button variant="outline" size="sm" onClick={deleteNonPassing} className="text-destructive hover:text-destructive" title="Delete every audited skill that did not pass"><Trash2 className="size-3.5" />Clean up non-passing ({nonPassingCount})</Button>}
              </span>
            </div>
          )}
          <div className="space-y-2">
            {visible.map((s) => {
              const k = skillKey(s)
              const published = (s.status || "draft") === "published"
              return (
              <div key={k} className={cn("group flex items-start gap-3 rounded-lg border bg-card p-3", selectMode && selected.has(k) && "border-primary/60 bg-primary/5")}>
                {selectMode && (
                  <input type="checkbox" checked={selected.has(k)} onChange={() => toggleSel(k)} className="mt-0.5 size-4 shrink-0 accent-primary" />
                )}
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{s.name}</span>
                    {s.status && <span className={cn("rounded-full px-2 py-0.5 text-[10px] capitalize", published ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" : "bg-muted text-muted-foreground")}>{s.status}</span>}
                    {s.confidence != null && <span className="text-[10px] text-muted-foreground">{Math.round(s.confidence * 100)}%</span>}
                    {s.uses != null && s.uses > 0 && <span className="text-[10px] text-muted-foreground">· {s.uses} use{s.uses === 1 ? "" : "s"}</span>}
                  </div>
                  {s.description && <p className="mt-0.5 text-xs text-muted-foreground">{s.description}</p>}
                </div>
                {!selectMode && (
                  <div className="flex gap-1.5 opacity-0 transition-opacity group-hover:opacity-100">
                    <button onClick={() => setStatus.mutate({ id: k, status: published ? "draft" : "published" })} title={published ? "Unpublish" : "Publish"} className="text-muted-foreground hover:text-foreground">{published ? <EyeOff className="size-4" /> : <Eye className="size-4" />}</button>
                    <button onClick={() => setRunTarget({ id: k, name: s.name })} title="Run" className="text-muted-foreground hover:text-foreground"><Play className="size-4" /></button>
                    <button onClick={() => setTestTarget({ id: k, name: s.name })} title="Test" className="text-muted-foreground hover:text-foreground"><FlaskConical className="size-4" /></button>
                    <button onClick={() => setEditId(k)} title="Edit" className="text-muted-foreground hover:text-foreground"><Pencil className="size-4" /></button>
                    {s.id && <button onClick={() => remove.mutate(s.id!)} title="Delete" className="text-muted-foreground hover:text-destructive"><Trash2 className="size-4" /></button>}
                  </div>
                )}
              </div>
              )
            })}
            {(skills || []).length === 0 && <p className="py-3 text-sm text-muted-foreground">No custom skills yet.</p>}
            {(skills || []).length > 0 && visible.length === 0 && <p className="py-3 text-sm text-muted-foreground">No skills match your filters.</p>}
          </div>
        </section>
        <section>
          <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><Wrench className="size-3.5" /> Built-in</h2>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {(builtin || []).map((b) => (
              <div key={b.name} className="group rounded-lg border bg-card p-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{b.name}</span>
                    {b.is_overridden && <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] text-amber-600 dark:text-amber-400">overridden</span>}
                  </div>
                  <button onClick={() => setBuiltinTarget(b.name)} title="Override" className="text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover:opacity-100"><Pencil className="size-4" /></button>
                </div>
                {b.description && <p className="mt-0.5 line-clamp-3 text-xs text-muted-foreground">{b.description}</p>}
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}
