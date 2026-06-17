import { useState } from "react"
import { X, ExternalLink, Copy, Check, FileText } from "lucide-react"
import { useNavigate } from "react-router-dom"
import { usePanel } from "@/stores/panel"
import { Markdown } from "./Markdown"
import { Button } from "@/components/ui/button"
import type { Source } from "@/types"

// On-demand right panel (Claude artifact-style). Hosts sources / streamed docs.
export function ContextPanel() {
  const { open, kind, title, payload, doc, close } = usePanel()
  const navigate = useNavigate()
  const [copied, setCopied] = useState(false)
  if (!open) return null
  const sources = (payload as Source[]) || []
  const copy = async () => { try { await navigator.clipboard.writeText(doc?.content || ""); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { /* ignore */ } }
  return (
    <aside className="hidden w-[44%] max-w-[560px] shrink-0 flex-col border-l bg-card lg:flex">
      <header className="flex h-13 shrink-0 items-center justify-between gap-2 border-b px-4">
        <span className="flex min-w-0 items-center gap-2 text-sm font-semibold">
          {kind === "doc" && <FileText className="size-4 shrink-0 text-muted-foreground" />}
          <span className="truncate">{kind === "doc" ? doc?.title || "Document" : title || "Details"}</span>
          {kind === "doc" && doc?.language && <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[11px] font-normal text-muted-foreground">{doc.language}</span>}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          {kind === "doc" && <button onClick={copy} title="Copy" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">{copied ? <Check className="size-4" /> : <Copy className="size-4" />}</button>}
          <button onClick={close} title="Close" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><X className="size-4" /></button>
        </div>
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
        {kind === "doc" && (
          <div className="prose-chat"><Markdown>{doc?.content || "_Generating…_"}</Markdown></div>
        )}
      </div>
      {kind === "doc" && doc?.docId && (
        <div className="shrink-0 border-t p-3">
          <Button variant="outline" size="sm" className="w-full" onClick={() => { close(); navigate("/library") }}>Open in Library</Button>
        </div>
      )}
    </aside>
  )
}
