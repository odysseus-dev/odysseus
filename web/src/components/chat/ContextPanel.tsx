import { useRef, useState } from "react"
import { X, ExternalLink, Copy, Check, FileText, FileCode2, Eye, Code2, ArrowLeft, Pencil, History, Loader2 } from "lucide-react"
import { useNavigate } from "react-router-dom"
import { usePanel, type PanelFile } from "@/stores/panel"
import { HtmlPreview } from "@/components/ui/HtmlPreview"
import { detectRenderLang } from "@/lib/artifact"
import { useExitTransition } from "@/lib/useExitTransition"
import { apiJson } from "@/lib/api"
import { useDocMutations } from "@/api/documents"
import { DocHistory } from "./DocHistory"
import { ShareMenu } from "./ShareMenu"
import { Markdown } from "./Markdown"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { Source } from "@/types"

// Load a thread file's content into the doc preview. Guards every store write
// behind a docId check so a slow response from a previously-clicked file can't
// stamp its content onto the file the user is now viewing, and surfaces a load
// failure instead of leaving a blank/“Generating…” panel.
async function openFile(f: PanelFile) {
  const p = usePanel.getState()
  p.showDoc(f.title || f.name || "Document", f.language)
  p.setDocId(f.id)
  try {
    const d = await apiJson<{ current_content?: string }>(`/api/document/${f.id}`)
    if (usePanel.getState().doc?.docId === f.id) p.setDocContent(d.current_content || "")
  } catch {
    if (usePanel.getState().doc?.docId === f.id) p.setDocError("Couldn’t load this file.")
  }
}

// On-demand right panel (Claude artifact-style). Hosts a per-thread file list,
// streamed/opened docs, sources, and research. HTML/SVG/XML docs render as a
// sandboxed live preview (with a Code toggle); markdown/prose render via
// Markdown; other code renders as a code block.
export function ContextPanel() {
  const { open, kind, title, payload, doc, files, close, backToFiles } = usePanel()
  const { render, closing } = useExitTransition(open, 150)
  const navigate = useNavigate()
  const [copied, setCopied] = useState(false)
  const lang = doc?.language?.toLowerCase()
  const renderLang = detectRenderLang(doc?.content, doc?.language, doc?.title)
  const renderable = !!renderLang
  const isProse = !renderable && (!lang || ["markdown", "md", "text", "plain", "email"].includes(lang))
  const [view, setView] = useState<"preview" | "code">("preview")
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState("")
  const [mode, setMode] = useState<"view" | "history">("view")
  const { update } = useDocMutations()
  // Reset transient panel state whenever a different document opens (render-time
  // reset, keyed on docId-or-title — avoids a setState-in-effect).
  const docKey = doc?.docId || doc?.title
  const lastDocKey = useRef(docKey)
  if (docKey !== lastDocKey.current) { lastDocKey.current = docKey; setView("preview"); setEditing(false); setMode("view") }

  if (!render) return null
  const editable = kind === "doc" && !!doc?.docId && !doc?.error
  const startEdit = () => { setDraft(doc?.content || ""); setMode("view"); setEditing(true) }
  const saveEdit = () => {
    if (!doc?.docId) return
    update.mutate({ id: doc.docId, content: draft }, { onSuccess: () => { usePanel.getState().setDocContent(draft); setEditing(false) } })
  }
  const sources = (payload as Source[]) || []
  const hasFileList = !!files && files.length > 0
  const copy = async () => { try { await navigator.clipboard.writeText(doc?.content || ""); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { /* ignore */ } }
  const showPreview = kind === "doc" && renderable && view === "preview" && !editing && mode === "view"
  const headerTitle = kind === "doc" ? doc?.title || "Document"
    : kind === "files" ? `Files${files?.length ? ` · ${files.length}` : ""}`
    : title || "Details"

  return (
    <aside className={cn("hidden w-[44%] max-w-[560px] shrink-0 flex-col border-l bg-card lg:flex", closing ? "animate-panel-out" : "animate-panel-in")}>
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
          {editing ? (
            <>
              <Button size="sm" variant="ghost" onClick={() => setEditing(false)} disabled={update.isPending}>Cancel</Button>
              <Button size="sm" onClick={saveEdit} disabled={update.isPending}>{update.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}Save</Button>
            </>
          ) : (
            <>
              {kind === "doc" && renderable && mode === "view" && (
                <div className="mr-1 flex rounded-lg bg-muted p-0.5">
                  <button onClick={() => setView("preview")} className={cn("flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors", view === "preview" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}><Eye className="size-3.5" />Preview</button>
                  <button onClick={() => setView("code")} className={cn("flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors", view === "code" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}><Code2 className="size-3.5" />Code</button>
                </div>
              )}
              {editable && mode === "view" && <button onClick={startEdit} title="Edit" className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"><Pencil className="size-4" /></button>}
              {editable && <button onClick={() => setMode((m) => (m === "history" ? "view" : "history"))} title="Version history" className={cn("rounded-md p-1.5 transition-colors hover:bg-accent hover:text-foreground", mode === "history" ? "bg-accent text-foreground" : "text-muted-foreground")}><History className="size-4" /></button>}
              {kind === "doc" && mode === "view" && <button onClick={copy} title="Copy" className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground">{copied ? <Check className="size-4" /> : <Copy className="size-4" />}</button>}
              <button onClick={close} title="Hide panel" className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"><X className="size-4" /></button>
            </>
          )}
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
      ) : kind === "doc" && mode === "history" && doc?.docId ? (
        <DocHistory docId={doc.docId} onBack={() => setMode("view")}
          onRestored={(content) => { usePanel.getState().setDocContent(content); setMode("view") }} />
      ) : kind === "doc" && editing ? (
        <textarea value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck={false} autoFocus
          className="min-h-0 flex-1 resize-none border-0 bg-background p-4 font-mono text-[13px] leading-relaxed text-foreground outline-none" />
      ) : kind === "doc" && doc?.error ? (
        <div className="flex min-h-0 flex-1 items-center justify-center p-6 text-center text-sm text-destructive">{doc.error}</div>
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

      {kind === "doc" && doc?.docId && !editing && mode === "view" && (
        <div className="flex shrink-0 items-center gap-2 border-t p-3">
          <ShareMenu resourceType="document" resourceId={doc.docId} placement="up" />
          <Button variant="outline" size="sm" className="ml-auto" onClick={() => { close(); navigate("/library") }}>Open in Library</Button>
        </div>
      )}
    </aside>
  )
}
