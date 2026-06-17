import { useEffect, useState } from "react"
import { Trash2, Sparkles, Wrench, Pencil, ArrowLeft, Save, Plus } from "lucide-react"
import { useSkills, useBuiltinSkills, useSkillMarkdown, useSkillMutations } from "@/api/skills"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const inp = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"

function SkillEditor({ id, onBack }: { id: string; onBack: () => void }) {
  const { data, isLoading } = useSkillMarkdown(id)
  const { saveMarkdown } = useSkillMutations()
  const [md, setMd] = useState("")
  const [dirty, setDirty] = useState(false)
  const [err, setErr] = useState("")
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

  if (editId) return <div className="mx-auto h-full w-full max-w-3xl"><SkillEditor id={editId} onBack={() => setEditId(null)} /></div>

  return (
    <div className="relative mx-auto flex h-full w-full max-w-3xl flex-col">
      {creating && <CreateForm onClose={() => setCreating(false)} />}
      <header className="flex h-13 shrink-0 items-center justify-between border-b px-4">
        <span className="text-sm font-semibold">Skills</span>
        <div className="flex items-center gap-2">
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
              <div key={b.name} className="rounded-lg border bg-card p-3">
                <div className="text-sm font-medium">{b.name}</div>
                {b.description && <p className="mt-0.5 line-clamp-3 text-xs text-muted-foreground">{b.description}</p>}
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}
