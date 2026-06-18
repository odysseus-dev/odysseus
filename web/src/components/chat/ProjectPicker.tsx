import { useState } from "react"
import { FolderKanban, Check, Plus, X } from "lucide-react"
import { useSessions } from "@/api/sessions"
import { useProjects, useProjectActions } from "@/api/projects"
import { cn } from "@/lib/utils"

// Assign the current chat to a project (folder) from the chat header.
export function ProjectPicker({ sessionId }: { sessionId: string }) {
  const { data: sessions } = useSessions()
  const { projects } = useProjects()
  const actions = useProjectActions()
  const [open, setOpen] = useState(false)
  const current = sessions?.find((s) => s.id === sessionId)?.folder || null
  const item = "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"

  const assign = (name: string | null) => { actions.assign(sessionId, name); setOpen(false) }
  const createAndAssign = async () => {
    const n = prompt("New project name")?.trim()
    if (!n) return
    await actions.create(n)
    await actions.assign(sessionId, n)
    setOpen(false)
  }

  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} title="Add this chat to a project"
        className={cn("flex max-w-[160px] items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition-colors",
          current ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground")}>
        <FolderKanban className="size-3.5 shrink-0" />
        <span className="truncate">{current || "Add to project"}</span>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-20 mt-1 w-56 origin-top-right animate-pop-in rounded-xl border bg-popover p-1 shadow-lg">
            <div className="max-h-64 overflow-y-auto">
              {projects.length === 0 && <p className="px-2 py-2 text-xs text-muted-foreground">No projects yet.</p>}
              {projects.map((p) => (
                <button key={p.name} onClick={() => assign(p.name)} className={item}>
                  <FolderKanban className="size-4 shrink-0" />
                  <span className="min-w-0 flex-1 truncate text-left">{p.name}</span>
                  {current === p.name && <Check className="size-4 shrink-0 text-foreground" />}
                </button>
              ))}
            </div>
            <div className="my-1 h-px bg-border" />
            {current && <button onClick={() => assign(null)} className={item}><X className="size-4" />Remove from project</button>}
            <button onClick={createAndAssign} className={item}><Plus className="size-4" />New project…</button>
          </div>
        </>
      )}
    </div>
  )
}
