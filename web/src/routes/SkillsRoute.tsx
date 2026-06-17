import { Trash2, Sparkles, Wrench } from "lucide-react"
import { useSkills, useBuiltinSkills, useSkillMutations } from "@/api/skills"

export function SkillsRoute() {
  const { data: skills } = useSkills()
  const { data: builtin } = useBuiltinSkills()
  const { remove } = useSkillMutations()
  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col">
      <header className="flex h-13 shrink-0 items-center border-b px-4 text-sm font-semibold">Skills</header>
      <div className="flex-1 space-y-6 overflow-y-auto p-4">
        <section>
          <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><Sparkles className="size-3.5" /> My skills</h2>
          <div className="space-y-2">
            {(skills || []).map((s) => (
              <div key={s.id || s.name} className="group flex items-start gap-3 rounded-lg border bg-card p-3">
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium">{s.name}</div>
                  {s.description && <p className="mt-0.5 text-xs text-muted-foreground">{s.description}</p>}
                </div>
                {s.id && <button onClick={() => remove.mutate(s.id!)} title="Delete" className="text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"><Trash2 className="size-4" /></button>}
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
