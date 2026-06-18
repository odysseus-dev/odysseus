import { useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import {
  Telescope, Loader2, Archive, ArchiveRestore, Trash2, MessageSquarePlus,
  ExternalLink, Eye, FileText, BookOpen, Copy, Check, Play, Plus,
} from "lucide-react"
import {
  useResearchActive, useResearchLibrary, useResearchDetail, useResearchReport, useResearchMutations, useResearchStart,
  type ResearchActiveItem, type ResearchLibraryItem,
} from "@/api/research"
import { useModels } from "@/api/models"
import { Markdown } from "@/components/chat/Markdown"
import { HtmlPreview } from "@/components/ui/HtmlPreview"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { Source } from "@/types"

function progressLabel(p: ResearchActiveItem["progress"]): string {
  if (!p) return "Working…"
  const phase = p.phase || "researching"
  const round = p.round ? ` · round ${p.round}` : ""
  const sources = p.total_sources != null ? ` · ${p.total_sources} sources` : ""
  switch (phase) {
    case "planning": return "Planning the research…"
    case "searching": return `Searching${round}${sources}`
    case "reading": return p.title ? `Reading: ${p.title}` : "Reading sources…"
    case "analyzing": return `Analyzing findings${round}`
    case "writing": return p.message || "Writing the report…"
    default: return p.message || "Working…"
  }
}

function ActiveRow({ item, onCancel, cancelling }: { item: ResearchActiveItem; onCancel: () => void; cancelling: boolean }) {
  return (
    <div className="rounded-md border bg-card px-3 py-2">
      <div className="flex items-start gap-2">
        <Loader2 className="mt-0.5 size-3.5 shrink-0 animate-spin text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{item.query || "Untitled research"}</div>
          <div className="truncate text-xs text-muted-foreground">{progressLabel(item.progress)}</div>
        </div>
      </div>
      <div className="mt-1.5 flex justify-end">
        <Button variant="outline" size="sm" disabled={cancelling} onClick={onCancel}>
          {cancelling ? <Loader2 className="size-3.5 animate-spin" /> : null}Cancel
        </Button>
      </div>
    </div>
  )
}

function LibraryRow({ item, active, onClick }: { item: ResearchLibraryItem; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn("w-full cursor-pointer rounded-md px-3 py-2 text-left text-sm",
        active ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground")}
    >
      <div className="truncate font-medium text-foreground">{item.query || "Untitled research"}</div>
      <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
        {item.category && <span>{item.category}</span>}
        <span>{item.source_count} {item.source_count === 1 ? "source" : "sources"}</span>
        {item.duration && <span>{item.duration}</span>}
        {item.completed_at ? <span>{new Date(item.completed_at * 1000).toLocaleDateString()}</span> : null}
      </div>
    </button>
  )
}

function SourceList({ sources }: { sources: Source[] }) {
  if (!sources.length) return <p className="text-sm text-muted-foreground">No sources.</p>
  return (
    <div className="space-y-1.5">
      {sources.map((s, i) => (
        <a
          key={(s.url || "") + i}
          href={s.url}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-start gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-accent/60 hover:text-foreground"
        >
          <span className="mt-0.5 w-5 shrink-0 text-right text-xs text-muted-foreground">{i + 1}.</span>
          <span className="min-w-0 flex-1">
            <span className="block truncate font-medium text-foreground">{s.title || s.url || "Untitled source"}</span>
            {s.url && <span className="block truncate text-xs">{s.url}</span>}
          </span>
          <ExternalLink className="mt-0.5 size-3.5 shrink-0" />
        </a>
      ))}
    </div>
  )
}

const CATEGORIES = [
  { key: "", label: "Auto" },
  { key: "product", label: "Product" },
  { key: "compare", label: "Compare" },
  { key: "how-to", label: "How-to" },
  { key: "fact-check", label: "Fact-check" },
]
const ROUNDS = [
  { v: 0, l: "Auto" }, { v: 3, l: "3" }, { v: 5, l: "5" }, { v: 8, l: "8" }, { v: 12, l: "12" },
]

// Dedicated research-start form (parity with the legacy Deep Research panel).
// Kicks off a background job via POST /api/research/start; it then appears in
// the Active list (polled) and lands in the library when done.
function StartForm({ onStarted }: { onStarted: () => void }) {
  const start = useResearchStart()
  const { data: models } = useModels()
  const [query, setQuery] = useState("")
  const [category, setCategory] = useState("")
  const [rounds, setRounds] = useState(0)
  const [model, setModel] = useState("") // "endpointId::model" or "" for default
  const flat = useMemo(
    () => (models?.items || []).flatMap((ep) =>
      [...(ep.models || []), ...(ep.models_extra || [])].map((m) => ({ id: `${ep.endpoint_id}::${m}`, model: m }))),
    [models],
  )
  const submit = () => {
    if (!query.trim() || start.isPending) return
    const [endpointId, m] = model ? model.split("::") : ["", ""]
    start.mutate(
      { query: query.trim(), category: category || undefined, max_rounds: rounds, endpoint_id: endpointId || undefined, model: m || undefined },
      { onSuccess: () => { setQuery(""); onStarted() } },
    )
  }
  return (
    <div className="mx-auto w-full max-w-2xl px-6 py-8">
      <div className="mb-1 flex items-center gap-2 text-sm font-semibold"><Telescope className="size-4" />Deep Research</div>
      <p className="mb-4 text-sm text-muted-foreground">Multi-step web research with an LLM-in-the-loop agent. It runs in the background; the report lands in your library.</p>
      <textarea
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit() }}
        rows={4}
        placeholder="e.g. Compare Rust and Go for building a high-throughput web API in 2026"
        className="w-full resize-none rounded-xl border bg-card p-3 text-sm outline-none focus:border-ring focus:ring-[3px] focus:ring-ring/35"
      />
      <div className="mt-3 flex flex-wrap gap-1.5">
        {CATEGORIES.map((c) => (
          <button
            key={c.key}
            onClick={() => setCategory(c.key)}
            className={cn("rounded-full border px-3 py-1 text-xs font-medium transition-colors",
              category === c.key ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground")}
          >
            {c.label}
          </button>
        ))}
      </div>
      <div className="mt-4 flex flex-wrap items-end gap-4">
        <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
          Rounds
          <select value={rounds} onChange={(e) => setRounds(Number(e.target.value))} className="h-9 rounded-md border bg-background px-2 text-sm text-foreground outline-none focus:border-ring">
            {ROUNDS.map((r) => <option key={r.v} value={r.v}>{r.l}</option>)}
          </select>
        </label>
        <label className="flex min-w-[200px] flex-col gap-1 text-xs font-medium text-muted-foreground">
          Model
          <select value={model} onChange={(e) => setModel(e.target.value)} className="h-9 rounded-md border bg-background px-2 text-sm text-foreground outline-none focus:border-ring">
            <option value="">Default (research)</option>
            {flat.map((f) => <option key={f.id} value={f.id}>{f.model}</option>)}
          </select>
        </label>
        <Button className="ml-auto" disabled={!query.trim() || start.isPending} onClick={submit}>
          {start.isPending ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}Start research
        </Button>
      </div>
      {start.isError && <p className="mt-2 text-xs text-destructive">Couldn't start research: {(start.error as Error)?.message}</p>}
    </div>
  )
}

const segBtn = (active: boolean) =>
  cn("flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors",
    active ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")

function Detail({
  id, archived, onArchiveToggle, onDelete, onSpinoff, spinningOff,
}: {
  id: string
  archived: boolean
  onArchiveToggle: () => void
  onDelete: () => void
  onSpinoff: () => void
  spinningOff: boolean
}) {
  const { data, isLoading } = useResearchDetail(id)
  const [view, setView] = useState<"visual" | "report" | "sources">("visual")
  const [copied, setCopied] = useState(false)
  const sources = data?.sources || []
  const reportUrl = `/api/research/report/${id}`
  // Fetch the Visual Report HTML only while that tab is selected.
  const { data: reportHtml, isLoading: reportLoading, isError: reportError } = useResearchReport(view === "visual" ? id : undefined)
  const copy = async () => { try { await navigator.clipboard.writeText(data?.result || ""); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { /* ignore */ } }
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex h-13 shrink-0 items-center gap-2 border-b px-4">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold">{data?.query || "Research"}</div>
          {data?.category && <div className="truncate text-xs text-muted-foreground">{data.category}</div>}
        </div>
        <div className="mr-1 flex shrink-0 rounded-lg bg-muted p-0.5">
          <button onClick={() => setView("visual")} className={segBtn(view === "visual")}><Eye className="size-3.5" />Visual</button>
          <button onClick={() => setView("report")} className={segBtn(view === "report")}><FileText className="size-3.5" />Report</button>
          <button onClick={() => setView("sources")} className={segBtn(view === "sources")}><BookOpen className="size-3.5" />Sources{sources.length ? ` ${sources.length}` : ""}</button>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <a href={reportUrl} target="_blank" rel="noopener noreferrer" title="Open report in new tab" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><ExternalLink className="size-4" /></a>
          <button onClick={copy} title="Copy report markdown" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">{copied ? <Check className="size-4" /> : <Copy className="size-4" />}</button>
          <Button variant="outline" size="sm" disabled={spinningOff} onClick={onSpinoff} title="Continue in chat">
            {spinningOff ? <Loader2 className="size-3.5 animate-spin" /> : <MessageSquarePlus className="size-3.5" />}Discuss
          </Button>
          <button onClick={onArchiveToggle} title={archived ? "Restore" : "Archive"} className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">
            {archived ? <ArchiveRestore className="size-4" /> : <Archive className="size-4" />}
          </button>
          <button onClick={() => { if (confirm("Delete this research report?")) onDelete() }} title="Delete" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-destructive">
            <Trash2 className="size-4" />
          </button>
        </div>
      </header>
      {isLoading ? (
        <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />Loading…</div>
      ) : view === "visual" ? (
        reportLoading ? (
          <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />Rendering visual report…</div>
        ) : reportError || !reportHtml ? (
          <div className="flex flex-col items-center justify-center gap-2 p-8 text-center text-sm text-muted-foreground">
            <p>No visual report available for this research.</p>
            <button onClick={() => setView("report")} className="text-foreground underline-offset-2 hover:underline">View the markdown report instead</button>
          </div>
        ) : (
          <HtmlPreview content={reportHtml} renderLang="html" title={data?.query || "Visual report"} />
        )
      ) : view === "sources" ? (
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5"><SourceList sources={sources} /></div>
      ) : data?.result ? (
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5"><Markdown>{data.result}</Markdown></div>
      ) : (
        <div className="min-h-0 flex-1 p-6"><p className="text-sm text-muted-foreground">No report text available for this research.</p></div>
      )}
    </div>
  )
}

export function ResearchRoute() {
  const navigate = useNavigate()
  const [showArchived, setShowArchived] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const { data: active } = useResearchActive()
  const { data: library, isLoading } = useResearchLibrary(showArchived)
  const { cancel, archive, remove, spinoff } = useResearchMutations()

  const activeItems = active?.active || []
  const items = library?.research || []
  const current = items.find((it) => it.id === selected) || null

  const handleArchiveToggle = () => {
    if (!current) return
    archive.mutate({ id: current.id, archived: !current.archived })
    setSelected(null)
  }
  const handleDelete = () => {
    if (!current) return
    remove.mutate(current.id)
    setSelected(null)
  }
  const handleSpinoff = () => {
    if (!current) return
    spinoff.mutate(current.id, { onSuccess: (r) => navigate(`/chat/${r.session_id}`) })
  }

  return (
    <div className="flex h-full w-full">
      <aside className="flex w-[320px] shrink-0 flex-col border-r">
        <header className="flex h-13 shrink-0 items-center justify-between border-b px-4 text-sm font-semibold">
          <span className="flex items-center gap-2"><Telescope className="size-4" />Research</span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setSelected(null)}
              title="New research"
              className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              <Plus className="size-4" />
            </button>
            <button
              onClick={() => { setShowArchived((v) => !v); setSelected(null) }}
              className={cn("rounded-md px-2 py-1 text-xs font-medium", showArchived ? "bg-accent text-foreground" : "font-normal text-muted-foreground hover:text-foreground")}
            >
              {showArchived ? "Archived" : "Library"}
            </button>
          </div>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {!showArchived && activeItems.length > 0 && (
            <div className="mb-3 space-y-1.5">
              <div className="px-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Active</div>
              {activeItems.map((it) => (
                <ActiveRow
                  key={it.session_id}
                  item={it}
                  cancelling={cancel.isPending && cancel.variables === it.session_id}
                  onCancel={() => cancel.mutate(it.session_id)}
                />
              ))}
            </div>
          )}
          <div className="space-y-0.5">
            {!showArchived && activeItems.length > 0 && (
              <div className="px-1 pb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Library</div>
            )}
            {isLoading ? (
              <div className="flex items-center gap-2 px-2 py-4 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />Loading…</div>
            ) : items.length === 0 ? (
              <p className="px-2 py-8 text-center text-sm text-muted-foreground">
                {showArchived ? "No archived research." : "No research yet. Start one on the right."}
              </p>
            ) : (
              items.map((it) => (
                <LibraryRow key={it.id} item={it} active={it.id === selected} onClick={() => setSelected(it.id)} />
              ))
            )}
          </div>
        </div>
      </aside>
      {current ? (
        <Detail
          id={current.id}
          archived={current.archived}
          onArchiveToggle={handleArchiveToggle}
          onDelete={handleDelete}
          onSpinoff={handleSpinoff}
          spinningOff={spinoff.isPending}
        />
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <StartForm onStarted={() => { /* job appears in Active (polled) */ }} />
        </div>
      )}
    </div>
  )
}
