import { Play, Pause, RotateCw, Trash2 } from "lucide-react"
import { useTasks, useTaskMutations } from "@/api/tasks"
import { cn } from "@/lib/utils"

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
  const list = tasks || []
  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col">
      <header className="flex h-13 shrink-0 items-center border-b px-4 text-sm font-semibold">Automations</header>
      <div className="flex-1 overflow-y-auto p-4">
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
