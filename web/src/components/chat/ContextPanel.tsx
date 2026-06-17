import { X, ExternalLink } from "lucide-react"
import { usePanel } from "@/stores/panel"
import { Markdown } from "./Markdown"
import type { Source } from "@/types"

// On-demand right panel (Claude artifact-style). Hosts sources / streamed docs.
export function ContextPanel() {
  const { open, kind, title, payload, close } = usePanel()
  if (!open) return null
  const sources = (payload as Source[]) || []
  return (
    <aside className="hidden w-[40%] max-w-[520px] shrink-0 flex-col border-l bg-card lg:flex">
      <header className="flex h-13 shrink-0 items-center justify-between border-b px-4">
        <span className="truncate text-sm font-semibold">{title || "Details"}</span>
        <button onClick={close} title="Close" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><X className="size-4" /></button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {kind === "sources" && (
          <div className="space-y-2">
            {sources.map((s, i) => (
              <a key={i} href={s.url} target="_blank" rel="noreferrer" className="block rounded-lg border bg-background p-3 transition-colors hover:bg-accent/50">
                <div className="flex items-center gap-1.5 text-sm font-medium"><ExternalLink className="size-3.5 shrink-0 text-muted-foreground" /><span className="truncate">{s.title || s.url}</span></div>
                {s.snippet && <p className="mt-1 line-clamp-3 text-xs text-muted-foreground">{s.snippet}</p>}
                {s.url && <p className="mt-1 truncate text-[11px] text-muted-foreground/70">{s.url}</p>}
              </a>
            ))}
            {sources.length === 0 && <p className="text-sm text-muted-foreground">No sources.</p>}
          </div>
        )}
        {kind === "doc" && <div className="prose-chat"><Markdown>{String(payload || "")}</Markdown></div>}
      </div>
    </aside>
  )
}
