import { FlaskConical, Cpu, HardDrive } from "lucide-react"
import { useCachedModels, useGpus } from "@/api/cookbook"
import { cn } from "@/lib/utils"

function gb(mb?: number) { return mb != null ? `${(mb / 1024).toFixed(1)} GB` : "—" }

export function CookbookRoute() {
  const { data: cached, isLoading: cl } = useCachedModels()
  const { data: gpu, isLoading: gl } = useGpus()
  const gpus = gpu?.gpus || []
  const models = cached?.models || []

  return (
    <div className="mx-auto flex h-full w-full max-w-4xl flex-col">
      <header className="flex h-13 shrink-0 items-center gap-2 border-b px-4 text-sm font-semibold"><FlaskConical className="size-4" />Cookbook</header>
      <div className="flex-1 space-y-6 overflow-y-auto p-4">
        <section>
          <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><Cpu className="size-3.5" />Hardware</h2>
          {gl ? <p className="text-sm text-muted-foreground">Probing GPUs…</p>
            : gpus.length === 0 ? <p className="rounded-lg border bg-card p-3 text-sm text-muted-foreground">{gpu?.error ? `No GPU probe available (${gpu.error}).` : "No GPUs detected."}</p>
            : (
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {gpus.map((g) => {
                  const pct = g.total_mb ? Math.round(((g.used_mb ?? (g.total_mb - (g.free_mb || 0))) / g.total_mb) * 100) : 0
                  return (
                    <div key={g.index} className="rounded-lg border bg-card p-3">
                      <div className="flex items-center justify-between">
                        <span className="truncate text-sm font-medium">{g.name || `GPU ${g.index}`}</span>
                        {g.busy && <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">busy</span>}
                      </div>
                      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted">
                        <div className={cn("h-full rounded-full", pct > 85 ? "bg-destructive" : "bg-primary")} style={{ width: `${pct}%` }} />
                      </div>
                      <div className="mt-1.5 flex justify-between text-xs text-muted-foreground">
                        <span>{gb(g.used_mb ?? (g.total_mb && g.free_mb != null ? g.total_mb - g.free_mb : undefined))} / {gb(g.total_mb)}</span>
                        {g.util_pct != null && <span>{g.util_pct}% util</span>}
                      </div>
                      {g.processes && g.processes.length > 0 && (
                        <div className="mt-2 space-y-0.5 border-t pt-1.5 text-[11px] text-muted-foreground">
                          {g.processes.slice(0, 4).map((p) => <div key={p.pid} className="flex justify-between gap-2"><span className="truncate">{p.name} ({p.pid})</span><span>{gb(p.used_mb)}</span></div>)}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
        </section>

        <section>
          <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><HardDrive className="size-3.5" />Cached models <span className="normal-case text-muted-foreground/70">· {cached?.host}</span></h2>
          {cl ? <p className="text-sm text-muted-foreground">Scanning cache…</p>
            : !cached?.ok ? <p className="rounded-lg border bg-card p-3 text-sm text-muted-foreground">Model cache unavailable (admin only, or no host configured).</p>
            : models.length === 0 ? <p className="rounded-lg border bg-card p-3 text-sm text-muted-foreground">No models cached.</p>
            : (
              <div className="divide-y rounded-lg border bg-card">
                {models.map((m) => (
                  <div key={m.repo_id} className="flex items-center gap-3 px-3 py-2.5">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{m.repo_id}</div>
                      <div className="flex flex-wrap gap-1.5 text-xs text-muted-foreground">
                        <span>{m.size}</span>
                        {m.nb_files != null && <span>· {m.nb_files} files</span>}
                        {m.backend && <span>· {m.backend}</span>}
                        {m.is_gguf && <span>· GGUF</span>}
                        {m.is_ollama && <span>· Ollama</span>}
                        {m.is_diffusion && <span>· diffusion</span>}
                      </div>
                    </div>
                    <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[11px]", m.status === "downloading" ? "bg-amber-500/15 text-amber-600 dark:text-amber-400" : "bg-muted text-muted-foreground")}>{m.status || "ready"}</span>
                  </div>
                ))}
              </div>
            )}
        </section>
      </div>
    </div>
  )
}
