import { useEffect, useState } from "react"
import { Trash2, Sparkles, Wrench, Pencil, ArrowLeft, Save, Plus, Play, FlaskConical, Link2, RotateCcw, CheckCircle2, XCircle, AlertTriangle, HelpCircle, Loader2 } from "lucide-react"
import { useSkills, useBuiltinSkills, useSkillMarkdown, useSkillMutations, useRunSkill, useStartSkillTest, useSkillTestStatus, useBuiltinSkill } from "@/api/skills"
import type { SkillVerdict } from "@/api/skills"
import { Button } from "@/components/ui/button"
import { Markdown } from "@/components/chat/Markdown"
import { cn } from "@/lib/utils"

const inp = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"

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
    <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-xl border bg-popover p-4 shadow-lg">
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
  const { data } = useSkillTestStatus(id, polling)
  const running = data?.status === "running"
  // Stop polling once the run completes.
  useEffect(() => { if (data && data.status !== "running") setPolling(false) }, [data]) // eslint-disable-line react-hooks/set-state-in-effect -- stop polling when the background test job finishes
  const begin = () => { setPolling(true); start.mutate(id) }
  const verdict = data?.verdict
  const log = data?.log || []
  return (
    <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-xl border bg-popover p-4 shadow-lg">
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
    <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-xl border bg-popover p-4 shadow-lg">
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
    <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-xl border bg-popover p-4 shadow-lg">
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
    <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-xl border bg-popover p-4 shadow-lg">
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

export function SkillsRoute() {
  const { data: skills } = useSkills()
  const { data: builtin } = useBuiltinSkills()
  const { remove, auditAll } = useSkillMutations()
  const [editId, setEditId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [importing, setImporting] = useState(false)
  const [runTarget, setRunTarget] = useState<{ id: string; name: string } | null>(null)
  const [testTarget, setTestTarget] = useState<{ id: string; name: string } | null>(null)
  const [builtinTarget, setBuiltinTarget] = useState<string | null>(null)

  if (editId) return <div className="mx-auto h-full w-full max-w-3xl"><SkillEditor id={editId} onBack={() => setEditId(null)} /></div>

  return (
    <div className="relative mx-auto flex h-full w-full max-w-3xl flex-col">
      {creating && <CreateForm onClose={() => setCreating(false)} />}
      {importing && <ImportForm onClose={() => setImporting(false)} />}
      {runTarget && <RunPanel id={runTarget.id} name={runTarget.name} onClose={() => setRunTarget(null)} />}
      {testTarget && <TestPanel id={testTarget.id} name={testTarget.name} onClose={() => setTestTarget(null)} />}
      {builtinTarget && <BuiltinEditor name={builtinTarget} onClose={() => setBuiltinTarget(null)} />}
      <header className="flex h-13 shrink-0 items-center justify-between border-b px-4">
        <span className="text-sm font-semibold">Skills</span>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => setImporting(true)} title="Install a SKILL.md bundle from a URL"><Link2 className="size-4" />Import</Button>
          <Button variant="outline" size="sm" disabled={auditAll.isPending} onClick={() => auditAll.mutate()} title="Test, judge & auto-publish skills">{auditAll.isPending ? "Auditing…" : "Audit all"}</Button>
          <Button size="sm" onClick={() => setCreating(true)}><Plus className="size-4" />New skill</Button>
        </div>
      </header>
      <div className="flex-1 space-y-6 overflow-y-auto p-4">
        <section>
          <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><Sparkles className="size-3.5" /> My skills</h2>
          <div className="space-y-2">
            {(skills || []).map((s) => (
              <div key={s.id || s.name} className="group flex items-start gap-3 rounded-lg border bg-card p-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{s.name}</span>
                    {s.status && <span className={cn("rounded-full px-2 py-0.5 text-[10px] capitalize", s.status === "published" ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" : "bg-muted text-muted-foreground")}>{s.status}</span>}
                    {s.confidence != null && <span className="text-[10px] text-muted-foreground">{Math.round(s.confidence * 100)}%</span>}
                  </div>
                  {s.description && <p className="mt-0.5 text-xs text-muted-foreground">{s.description}</p>}
                </div>
                <div className="flex gap-1.5 opacity-0 transition-opacity group-hover:opacity-100">
                  <button onClick={() => setRunTarget({ id: s.id || s.name, name: s.name })} title="Run" className="text-muted-foreground hover:text-foreground"><Play className="size-4" /></button>
                  <button onClick={() => setTestTarget({ id: s.id || s.name, name: s.name })} title="Test" className="text-muted-foreground hover:text-foreground"><FlaskConical className="size-4" /></button>
                  <button onClick={() => setEditId(s.id || s.name)} title="Edit" className="text-muted-foreground hover:text-foreground"><Pencil className="size-4" /></button>
                  {s.id && <button onClick={() => remove.mutate(s.id!)} title="Delete" className="text-muted-foreground hover:text-destructive"><Trash2 className="size-4" /></button>}
                </div>
              </div>
            ))}
            {(skills || []).length === 0 && <p className="py-3 text-sm text-muted-foreground">No custom skills yet.</p>}
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
