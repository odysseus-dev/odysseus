import { useRef, useState } from "react"
import { X, ExternalLink, Copy, Check, FileText, FileCode2, Eye, Code2, ArrowLeft } from "lucide-react"
import { useNavigate } from "react-router-dom"
import { usePanel, type PanelFile } from "@/stores/panel"
import { HtmlPreview } from "@/components/ui/HtmlPreview"
import { detectRenderLang } from "@/lib/artifact"
import { apiJson } from "@/lib/api"
import { Markdown } from "./Markdown"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { Source } from "@/types"

// Load a thread file's content into the doc preview.
async function openFile(f: PanelFile) {
  const p = usePanel.getState()
  p.showDoc(f.title || f.name || "Document", f.language)
  p.setDocId(f.id)
  try {
    const d = await apiJson<{ current_content?: string }>(`/api/document/${f.id}`)
    p.setDocContent(d.current_content || "")
  } catch { /* leave empty on failure */ }
}

// On-demand right panel (Claude artifact-style). Hosts a per-thread file list,
// streamed/opened docs, sources, and research. HTML/SVG/XML docs render as a
// sandboxed live preview (with a Code toggle); markdown/prose render via
// Markdown; other code renders as a code block.
export function ContextPanel() {
  const { open, kind, title, payload, doc, files, close, backToFiles } = usePanel()
  const navigate = useNavigate()
  const [copied, setCopied] = useState(false)
  const lang = doc?.language?.toLowerCase()
  const renderLang = detectRenderLang(doc?.content, doc?.language, doc?.title)
  const renderable = !!renderLang
  const isProse = !renderable && (!lang || ["markdown", "md", "text", "plain", "email"].includes(lang))
  const [view, setView] = useState<"preview" | "code">("preview")
  // Reset to preview whenever a different document opens (render-time reset —
  // avoids a setState-in-effect).
  const lastTitle = useRef(doc?.title)
  if (doc?.title !== lastTitle.current) { lastTitle.current = doc?.title; setView("preview") }

  if (!open) return null
  const sources = (payload as Source[]) || []
  const hasFileList = !!files && files.length > 0
  const copy = async () => { try { await navigator.clipboard.writeText(doc?.content || ""); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { /* ignore */ } }
  const showPreview = kind === "doc" && renderable && view === "preview"
  const headerTitle = kind === "doc" ? doc?.title || "Document"
    : kind === "files" ? `Files${files?.length ? ` · ${files.length}` : ""}`
    : title || "Details"

  return (
    <aside className="hidden w-[44%] max-w-[560px] shrink-0 animate-panel-in flex-col border-l bg-card lg:flex">
      <header className="flex h-13 shrink-0 items-center justify-between gap-2 border-b px-4">
        <span className="flex min-w-0 items-center gap-2 text-sm font-semibold">
          {kind === "doc" && hasFileList && (
            <button onClick={() => backToFiles()} title="Back to files" className="-ml-1 shrink-0 rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground"><ArrowLeft className="size-4" /></button>
          )}
          {(kind === "doc" || kind === "files") && <FileText className="size-4 shrink-0 text-muted-foreground" />}
          <span className="truncate">{headerTitle}</span>
          {kind === "doc" && doc?.language && <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[11px] font-normal text-muted-foreground">{doc.language}</span>}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          {kind === "doc" && renderable && (
            <div className="mr-1 flex rounded-lg bg-muted p-0.5">
              <button onClick={() => setView("preview")} className={cn("flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors", view === "preview" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}><Eye className="size-3.5" />Preview</button>
              <button onClick={() => setView("code")} className={cn("flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors", view === "code" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}><Code2 className="size-3.5" />Code</button>
            </div>
          )}
          {kind === "doc" && <button onClick={copy} title="Copy" className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground">{copied ? <Check className="size-4" /> : <Copy className="size-4" />}</button>}
          <button onClick={close} title="Hide panel" className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"><X className="size-4" /></button>
        </div>
      </header>

      {kind === "files" ? (
        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          <div className="space-y-1.5">
            {(files || []).map((f) => (
              <button key={f.id} onClick={() => openFile(f)} className="flex w-full items-center gap-3 rounded-lg border bg-background p-3 text-left transition-colors hover:bg-accent/50">
                <FileCode2 className="size-4 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{f.title || f.name || "Untitled"}</span>
                  {f.language && <span className="block text-xs text-muted-foreground">{f.language}</span>}
                </span>
              </button>
            ))}
            {!hasFileList && <p className="px-1 py-6 text-center text-sm text-muted-foreground">No files in this thread.</p>}
          </div>
        </div>
      ) : showPreview ? (
        <HtmlPreview title={doc?.title} content={doc?.content} renderLang={renderLang} />
      ) : (
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
          {kind === "doc" && (isProse
            ? <div className="prose-chat"><Markdown>{doc?.content || "_Generating…_"}</Markdown></div>
            : <pre className="whitespace-pre-wrap break-words rounded-lg border bg-background p-3 font-mono text-[13px] leading-relaxed text-foreground">{doc?.content || ""}</pre>
          )}
        </div>
      )}

      {kind === "doc" && doc?.docId && (
        <div className="shrink-0 border-t p-3">
          <Button variant="outline" size="sm" className="w-full" onClick={() => { close(); navigate("/library") }}>Open in Library</Button>
        </div>
      )}
    </aside>
  )
}
