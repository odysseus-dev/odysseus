import { useState } from "react"
import { RotateCcw, ArrowLeft, Loader2, Check } from "lucide-react"
import { useDocVersions, useDocMutations, type DocVersion } from "@/api/documents"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

function ago(iso?: string): string {
  if (!iso) return ""
  const t = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").getTime()
  if (Number.isNaN(t)) return ""
  const s = Math.max(0, (Date.now() - t) / 1000)
  if (s < 60) return "just now"
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  if (s < 604800) return `${Math.floor(s / 86400)}d ago`
  return new Date(t).toLocaleDateString()
}

// Version-history pane for a saved document. Lists versions (newest first),
// previews a selected one read-only, and restores it via POST .../restore/{num}.
export function DocHistory({ docId, onBack, onRestored }: { docId: string; onBack: () => void; onRestored: (content: string) => void }) {
  const { data: versions, isLoading } = useDocVersions(docId, true)
  const { restore } = useDocMutations()
  const [selected, setSelected] = useState<DocVersion | null>(null)
  const [justRestored, setJustRestored] = useState<number | null>(null)

  const doRestore = (v: DocVersion) => {
    restore.mutate({ id: docId, num: v.version_number }, {
      onSuccess: () => { setJustRestored(v.version_number); onRestored(v.content); setTimeout(() => setJustRestored(null), 1800) },
    })
  }

  if (selected) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex shrink-0 items-center justify-between gap-2 border-b px-3 py-2">
          <button onClick={() => setSelected(null)} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
            <ArrowLeft className="size-4" />Version {selected.version_number}
          </button>
          <Button size="sm" disabled={restore.isPending} onClick={() => doRestore(selected)}>
            {restore.isPending ? <Loader2 className="size-4 animate-spin" /> : <RotateCcw className="size-3.5" />}Restore
          </Button>
        </div>
        <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-[13px] leading-relaxed text-foreground">{selected.content}</pre>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b px-3 py-2">
        <button onClick={onBack} className="flex items-center gap-1.5 text-sm font-medium text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" />Version history
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {isLoading ? (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />Loading versions…</div>
        ) : !versions || versions.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No saved versions yet.</p>
        ) : (
          <div className="space-y-1.5">
            {versions.map((v, i) => (
              <div key={v.id} className="flex items-center gap-2 rounded-lg border bg-background p-2.5">
                <button onClick={() => setSelected(v)} className="min-w-0 flex-1 text-left">
                  <span className="flex items-center gap-2 text-sm font-medium">
                    Version {v.version_number}
                    {i === 0 && <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">current</span>}
                    {v.source === "user" && <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">manual</span>}
                  </span>
                  <span className="mt-0.5 block truncate text-xs text-muted-foreground">{v.summary || "—"} · {ago(v.created_at)}</span>
                </button>
                {i !== 0 && (
                  <Button size="icon" variant="ghost" title={`Restore version ${v.version_number}`} disabled={restore.isPending}
                    onClick={() => doRestore(v)} className={cn(justRestored === v.version_number && "text-emerald-500")}>
                    {justRestored === v.version_number ? <Check className="size-4" /> : <RotateCcw className="size-4" />}
                  </Button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
