import { useState } from "react"
import { Play, Pause, RotateCw, Trash2, Plus } from "lucide-react"
import { useTasks, useTaskMutations } from "@/api/tasks"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const inp = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"

function NewTask({ onClose }: { onClose: () => void }) {
  const { create } = useTaskMutations()
  const [name, setName] = useState("")
  const [prompt, setPrompt] = useState("")
  const [schedule, setSchedule] = useState("daily")
  const [time, setTime] = useState("09:00")
  const [cron, setCron] = useState("")
  const [err, setErr] = useState("")
  const submit = () => {
    if (!prompt.trim()) { setErr("Prompt required"); return }
    if (schedule === "cron" && !cron.trim()) { setErr("Cron expression required"); return }
    setErr("")
    create.mutate({ name: name.trim() || undefined, prompt: prompt.trim(), schedule, scheduled_time: time, cron_expression: schedule === "cron" ? cron.trim() : undefined }, {
      onSuccess: onClose, onError: (e) => setErr(e instanceof Error ? e.message : "Failed"),
    })
  }
  return (
    <div className="mb-3 space-y-2 rounded-lg border bg-card p-3">
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (optional)" className={inp} />
      <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="What should this automation do? (prompt)" rows={2} className="w-full resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring" />
      <div className="flex gap-2">
        <select value={schedule} onChange={(e) => setSchedule(e.target.value)} className={cn(inp, "flex-1")}>
          {["daily", "weekly", "monthly", "once", "cron"].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        {schedule === "cron"
          ? <input value={cron} onChange={(e) => setCron(e.target.value)} placeholder="0 9 * * *" className={cn(inp, "flex-1")} />
          : <input type="time" value={time} onChange={(e) => setTime(e.target.value)} className={cn(inp, "w-32")} />}
      </div>
      {err && <p className="text-xs text-destructive">{err}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
        <Button size="sm" disabled={create.isPending} onClick={submit}>{create.isPending ? "Creating…" : "Create"}</Button>
      </div>
    </div>
  )
}

function StatusBadge({ status }: { status?: string }) {
  const s = (status || "").toLowerCase()
  const tone = s === "active" || s === "running" || s === "enabled"
    ? "text-[color:var(--ok)] [--ok:#3dd68c]"
    : s === "paused" || s === "disabled"
    ? "text-[color:var(--warn)] [--warn:#e0a93f]"
    : "text-muted-foreground"
  return <span className={cn("rounded-full bg-muted px-2 py-0.5 text-[11px] capitalize", tone)}>{status || "—"}</span>
}

export function TasksRoute() {
  const { data: tasks } = useTasks()
  const { run, pause, resume, remove } = useTaskMutations()
  const [creating, setCreating] = useState(false)
  const list = tasks || []
  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col">
      <header className="flex h-13 shrink-0 items-center justify-between border-b px-4">
        <span className="text-sm font-semibold">Automations</span>
        <Button size="sm" onClick={() => setCreating((c) => !c)}><Plus className="size-4" />New</Button>
      </header>
      <div className="flex-1 overflow-y-auto p-4">
        {creating && <NewTask onClose={() => setCreating(false)} />}
        <div className="space-y-2">
          {list.map((t) => {
            const paused = (t.status || "").toLowerCase() === "paused" || t.enabled === false
            return (
              <div key={t.id} className="group flex items-center gap-3 rounded-lg border bg-card p-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium">{t.name || t.title || t.action || "Task"}</span>
                    <StatusBadge status={t.status} />
                  </div>
                  {(t.schedule || t.cron || t.next_run_at) && (
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">{t.schedule || t.cron || `next: ${t.next_run_at}`}</p>
                  )}
                </div>
                <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                  <button onClick={() => run.mutate(t.id)} title="Run now" className="rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><RotateCw className="size-4" /></button>
                  {paused
                    ? <button onClick={() => resume.mutate(t.id)} title="Resume" className="rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><Play className="size-4" /></button>
                    : <button onClick={() => pause.mutate(t.id)} title="Pause" className="rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><Pause className="size-4" /></button>}
                  <button onClick={() => remove.mutate(t.id)} title="Delete" className="rounded p-1.5 text-muted-foreground hover:text-destructive"><Trash2 className="size-4" /></button>
                </div>
              </div>
            )
          })}
          {list.length === 0 && <p className="py-8 text-center text-sm text-muted-foreground">No automations yet.</p>}
        </div>
      </div>
    </div>
  )
}
