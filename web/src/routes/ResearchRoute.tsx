import { useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "react-router-dom"
import {
  Telescope, Loader2, Archive, ArchiveRestore, Trash2, MessageSquarePlus,
  ExternalLink, Eye, FileText, BookOpen, Copy, Check, Play, Plus, ChevronDown,
} from "lucide-react"
import {
  useResearchActive, useResearchLibrary, useResearchDetail, useResearchReport, useResearchMutations, useResearchStart,
  streamResearch, fetchResearchPeek,
  type ResearchActiveItem, type ResearchLibraryItem, type ResearchStartBody, type ResearchStreamEvent, type ResearchPeek,
} from "@/api/research"
import { useModels } from "@/api/models"
import { Markdown } from "@/components/chat/Markdown"
import { HtmlPreview } from "@/components/ui/HtmlPreview"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { toast } from "@/stores/toast"
import type { Source } from "@/types"

// Ordered research phases — used to derive a coarse progress fraction when the
// backend doesn't send an explicit `percent`. Mirrors the legacy synapse panel.
const PHASE_ORDER = ["planning", "searching", "reading", "analyzing", "writing"] as const

// Best-effort progress fraction (0..1) from a stream/poll progress object.
// Prefers an explicit percent; otherwise interpolates across known phases and
// nudges forward within a phase using round/source counts.
function progressFraction(p?: ResearchStreamEvent): number | null {
  if (!p) return null
  if (typeof p.percent === "number") return Math.max(0, Math.min(1, p.percent > 1 ? p.percent / 100 : p.percent))
  const phase = (p.phase as string) || ""
  const idx = PHASE_ORDER.indexOf(phase as (typeof PHASE_ORDER)[number])
  if (idx < 0) return null
  const base = idx / PHASE_ORDER.length
  const within = 1 / PHASE_ORDER.length
  // Within "searching"/"reading", creep toward the next phase as rounds add up.
  const round = typeof p.round === "number" ? Math.min(p.round, 5) : 0
  const nudge = round ? (round / 5) * within * 0.8 : within * 0.25
  return Math.min(0.97, base + nudge)
}

function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const m = Math.floor(s / 60)
  const r = s % 60
  return m ? `${m}m ${String(r).padStart(2, "0")}s` : `${r}s`
}

// Live elapsed-seconds counter that ticks every second from `startedAt`
// (unix seconds). Returns elapsed seconds.
function useElapsed(startedAt?: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  if (!startedAt) return 0
  return now / 1000 - startedAt
}

// Desktop notification on completion, with a toast fallback (legacy
// jobs.js:309-311). Requests permission lazily the first time we'd notify.
function notifyComplete(query: string, ok: boolean) {
  const title = ok ? "Research complete" : "Research failed"
  const body = query || "Deep research run"
  const fire = () => {
    try { new Notification(title, { body, tag: "odysseus-research" }) } catch { toast(`${title}: ${body}`, ok ? "success" : "error") }
  }
  if (typeof Notification === "undefined") { toast(`${title}: ${body}`, ok ? "success" : "error"); return }
  if (Notification.permission === "granted") { fire(); return }
  if (Notification.permission === "denied") { toast(`${title}: ${body}`, ok ? "success" : "error"); return }
  Notification.requestPermission().then((perm) => {
    if (perm === "granted") fire()
    else toast(`${title}: ${body}`, ok ? "success" : "error")
  }).catch(() => toast(`${title}: ${body}`, ok ? "success" : "error"))
}

function progressLabel(p?: ResearchStreamEvent): string {
  if (!p) return "Working…"
  const phase = (p.phase as string) || "researching"
  const round = p.round ? ` · round ${p.round}` : ""
  const sources = p.total_sources != null ? ` · ${p.total_sources} sources` : ""
  switch (phase) {
    case "planning": return "Planning the research…"
    case "searching": return `Searching${round}${sources}`
    case "reading": return p.title ? `Reading: ${p.title}` : "Reading sources…"
    case "analyzing": return `Analyzing findings${round}`
    case "writing": return (p.message as string) || "Writing the report…"
    default: return (p.message as string) || "Working…"
  }
}

// Inline partial-findings peek for an in-progress run.
function PeekPanel({ sessionId }: { sessionId: string }) {
  const [state, setState] = useState<{ loading: boolean; data?: ResearchPeek; error?: string }>({ loading: true })
  useEffect(() => {
    let alive = true
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset to loading when the peeked session changes
    setState({ loading: true })
    // Debounce the fetch so rapid Peek toggles (mount→unmount within 250ms)
    // cancel the pending request instead of firing a burst of POSTs.
    const timer = window.setTimeout(() => {
      fetchResearchPeek(sessionId)
        .then((data) => { if (alive) setState({ loading: false, data }) })
        .catch((e) => { if (alive) setState({ loading: false, error: e instanceof Error ? e.message : "Couldn't load partial findings" }) })
    }, 250)
    return () => { alive = false; window.clearTimeout(timer) }
  }, [sessionId])

  if (state.loading) {
    return <div className="mt-2 flex items-center gap-2 rounded-md border bg-background px-2.5 py-2 text-xs text-muted-foreground"><Loader2 className="size-3.5 animate-spin" />Loading partial findings…</div>
  }
  if (state.error) {
    return <div className="mt-2 rounded-md border bg-background px-2.5 py-2 text-xs text-muted-foreground">No partial findings yet.</div>
  }
  const d = state.data
  const hasResult = !!d?.result?.trim()
  const sources = d?.sources || []
  if (!hasResult && !sources.length) {
    return <div className="mt-2 rounded-md border bg-background px-2.5 py-2 text-xs text-muted-foreground">No partial findings yet — check back in a moment.</div>
  }
  return (
    <div className="mt-2 max-h-72 overflow-y-auto rounded-md border bg-background px-3 py-2">
      {hasResult ? (
        <div className="prose-sm"><Markdown>{d!.result}</Markdown></div>
      ) : (
        <p className="text-xs text-muted-foreground">Sources gathered so far:</p>
      )}
      {sources.length > 0 && (
        <div className="mt-2 space-y-1 border-t pt-2">
          {sources.slice(0, 12).map((s, i) => (
            <a key={(s.url || "") + i} href={s.url} target="_blank" rel="noopener noreferrer"
              className="flex items-start gap-1.5 truncate text-xs text-muted-foreground hover:text-foreground">
              <span className="w-4 shrink-0 text-right">{i + 1}.</span>
              <span className="min-w-0 flex-1 truncate">{s.title || s.url || "Untitled source"}</span>
            </a>
          ))}
        </div>
      )}
    </div>
  )
}

function ActiveRow({ item, onCancel, cancelling, onDone }: {
  item: ResearchActiveItem
  onCancel: () => void
  cancelling: boolean
  onDone: (sessionId: string, query: string, ok: boolean) => void
}) {
  // Live progress driven by the SSE stream; falls back to the 4s-polled
  // `item.progress` until the first stream frame arrives.
  const [live, setLive] = useState<ResearchStreamEvent | null>(null)
  const [showPeek, setShowPeek] = useState(false)
  const onDoneRef = useRef(onDone)
  useEffect(() => { onDoneRef.current = onDone })

  useEffect(() => {
    const stop = streamResearch(
      item.session_id,
      (e) => {
        setLive((prev) => ({ ...prev, ...e }))
        if (e.final) {
          const ok = e.status !== "error" && e.status !== "cancelled"
          onDoneRef.current(item.session_id, item.query || "", ok)
        }
      },
      () => { /* stream errored; the 4s poll remains the fallback */ },
    )
    return stop
  }, [item.session_id, item.query])

  const progress = (live || item.progress) as ResearchStreamEvent | undefined
  const frac = progressFraction(progress)
  const elapsed = useElapsed(item.started_at)

  return (
    <div className="rounded-md border bg-card px-3 py-2">
      <div className="flex items-start gap-2">
        <Loader2 className="mt-0.5 size-3.5 shrink-0 animate-spin text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{item.query || "Untitled research"}</div>
          <div className="truncate text-xs text-muted-foreground">{progressLabel(progress)}</div>
        </div>
        {item.started_at ? (
          <span className="shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">{formatElapsed(elapsed)}</span>
        ) : null}
      </div>
      <div className="mt-2 h-1 overflow-hidden rounded-full bg-muted">
        <div
          className={cn("h-full rounded-full bg-foreground/70 transition-all duration-500", frac == null && "animate-pulse")}
          style={{ width: `${Math.round((frac ?? 0.08) * 100)}%` }}
        />
      </div>
      <div className="mt-1.5 flex items-center justify-end gap-1.5">
        <Button variant="ghost" size="sm" onClick={() => setShowPeek((v) => !v)} title="Preview partial findings">
          <Eye className="size-3.5" />Peek
          <ChevronDown className={cn("size-3 transition-transform", showPeek && "rotate-180")} />
        </Button>
        <Button variant="outline" size="sm" disabled={cancelling} onClick={onCancel}>
          {cancelling ? <Loader2 className="size-3.5 animate-spin" /> : null}Cancel
        </Button>
      </div>
      {showPeek && <PeekPanel sessionId={item.session_id} />}
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
  { key: "comparison", label: "Compare" },
  { key: "howto", label: "How-to" },
  { key: "factcheck", label: "Fact-check" },
]
const ROUNDS = [
  { v: 0, l: "Auto" },
  ...Array.from({ length: 20 }, (_, i) => ({ v: i + 1, l: String(i + 1) })),
]
const SEARCH_PROVIDERS = [
  { key: "", label: "Default" },
  { key: "searxng", label: "SearXNG" },
  { key: "duckduckgo", label: "DuckDuckGo" },
  { key: "tavily", label: "Tavily" },
  { key: "brave", label: "Brave" },
  { key: "google_pse", label: "Google PSE" },
  { key: "serper", label: "Serper" },
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
  const [searchProvider, setSearchProvider] = useState("")
  const [model, setModel] = useState("") // "endpointId::model" or "" for default
  const [queued, setQueued] = useState<(ResearchStartBody & { id: string; model_label?: string })[]>([])
  const [runMode, setRunMode] = useState<"parallel" | "sequential">("parallel")
  const [batchError, setBatchError] = useState("")
  const [startingBatch, setStartingBatch] = useState(false)
  const flat = useMemo(
    () => (models?.items || []).flatMap((ep) =>
      [...(ep.models || []), ...(ep.models_extra || [])].map((m) => ({
        id: `${ep.endpoint_id}::${m}`,
        model: m,
        label: `${m}${ep.endpoint_name ? ` · ${ep.endpoint_name}` : ""}`,
      }))),
    [models],
  )
  const busy = start.isPending || startingBatch
  const draftCount = queued.length + (query.trim() ? 1 : 0)
  const resetDraft = () => {
    setQuery("")
    setCategory("")
  }
  const buildBody = (): (ResearchStartBody & { model_label?: string }) | null => {
    const q = query.trim()
    if (!q) return null
    const [endpointId, m] = model ? model.split("::") : ["", ""]
    const selectedModel = flat.find((f) => f.id === model)
    return {
      query: q,
      category: category || undefined,
      max_rounds: rounds,
      search_provider: searchProvider || undefined,
      endpoint_id: endpointId || undefined,
      model: m || undefined,
      model_label: selectedModel?.model,
    }
  }
  const queueDraft = () => {
    const body = buildBody()
    if (!body || busy) return
    setQueued((items) => [...items, { ...body, id: `${Date.now()}-${items.length}` }])
    setBatchError("")
    resetDraft()
  }
  const submit = async () => {
    if (busy) return
    const current = buildBody()
    const bodies = queued.length ? [...queued, ...(current ? [{ ...current, id: "current" }] : [])] : (current ? [{ ...current, id: "current" }] : [])
    if (!bodies.length) return
    setStartingBatch(true)
    setBatchError("")
    try {
      const clean: ResearchStartBody[] = bodies.map((body) => ({
        query: body.query,
        category: body.category,
        max_rounds: body.max_rounds,
        search_provider: body.search_provider,
        endpoint_id: body.endpoint_id,
        model: body.model,
      }))
      if (runMode === "parallel" && clean.length > 1) {
        await Promise.all(clean.map((body) => start.mutateAsync(body)))
      } else {
        for (const body of clean) await start.mutateAsync(body)
      }
      setQueued([])
      resetDraft()
      onStarted()
    } catch (e) {
      setBatchError(e instanceof Error ? e.message : "Couldn't start research")
    } finally {
      setStartingBatch(false)
    }
  }
  return (
    <div className="mx-auto w-full max-w-2xl px-3 py-8 md:px-6" data-tour="research-start-form">
      <div className="mb-1 flex items-center gap-2 text-sm font-semibold"><Telescope className="size-4" />Deep Research</div>
      <p className="mb-4 text-sm text-muted-foreground">Multi-step web research with an LLM-in-the-loop agent. It runs in the background; the report lands in your library.</p>
      <textarea
        data-tour="research-query"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit() }}
        rows={4}
        placeholder="e.g. Compare Rust and Go for building a high-throughput web API in 2026"
        className="w-full resize-none rounded-xl border bg-card p-3 text-sm outline-none focus:border-ring focus:ring-[3px] focus:ring-ring/35"
      />
      <div className="mt-3 flex flex-wrap gap-1.5" data-tour="research-categories">
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
      <div className="mt-4 flex flex-wrap items-end gap-4" data-tour="research-settings">
        <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
          Rounds
          <select value={rounds} onChange={(e) => setRounds(Number(e.target.value))} className="h-9 rounded-md border bg-background px-2 text-sm text-foreground outline-none focus:border-ring">
            {ROUNDS.map((r) => <option key={r.v} value={r.v}>{r.l}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
          Search engine
          <select value={searchProvider} onChange={(e) => setSearchProvider(e.target.value)} className="h-9 rounded-md border bg-background px-2 text-sm text-foreground outline-none focus:border-ring">
            {SEARCH_PROVIDERS.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
        </label>
        <label className="flex min-w-[200px] flex-col gap-1 text-xs font-medium text-muted-foreground">
          Model
          <select value={model} onChange={(e) => setModel(e.target.value)} className="h-9 rounded-md border bg-background px-2 text-sm text-foreground outline-none focus:border-ring">
            <option value="">Default (research)</option>
            {flat.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
          </select>
        </label>
        {draftCount > 1 && (
          <div className="flex rounded-lg bg-muted p-0.5">
            {(["parallel", "sequential"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => setRunMode(mode)}
                className={cn("rounded-md px-2.5 py-1 text-xs font-medium capitalize transition-colors",
                  runMode === mode ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}
              >
                {mode}
              </button>
            ))}
          </div>
        )}
        <Button variant="outline" disabled={!query.trim() || busy} onClick={queueDraft} data-tour="research-queue">
          <Plus className="size-4" />Queue
        </Button>
        <Button className="ml-auto" disabled={!draftCount || busy} onClick={() => { void submit() }} data-tour="research-start">
          {busy ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
          {draftCount > 1 ? `Start ${draftCount}` : "Start research"}
        </Button>
      </div>
      {queued.length > 0 && (
        <div className="mt-4 space-y-1.5">
          {queued.map((item, i) => (
            <div key={item.id} className="flex items-center gap-2 rounded-md border bg-card px-3 py-2 text-sm">
              <span className="shrink-0 text-xs text-muted-foreground">{i + 1}.</span>
              <span className="min-w-0 flex-1 truncate">{item.query}</span>
              {item.category && <span className="hidden rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground sm:inline">{item.category}</span>}
              {item.model_label && <span className="hidden max-w-32 truncate rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground md:inline">{item.model_label}</span>}
              <button
                type="button"
                onClick={() => setQueued((items) => items.filter((q) => q.id !== item.id))}
                className="shrink-0 rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-destructive"
                title="Remove from queue"
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}
      {start.isError && <p className="mt-2 text-xs text-destructive">Couldn't start research: {(start.error as Error)?.message}</p>}
      {batchError && <p className="mt-2 text-xs text-destructive">{batchError}</p>}
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
        <div className="mr-1 flex shrink-0 flex-wrap rounded-lg bg-muted p-0.5 md:flex-nowrap">
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
  const qc = useQueryClient()
  const [showArchived, setShowArchived] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const { data: active } = useResearchActive()
  const { data: library, isLoading } = useResearchLibrary(showArchived)
  const { cancel, archive, remove, spinoff } = useResearchMutations()

  // Fire the completion notification at most once per session, then refresh the
  // active + library lists so the finished run moves into the library promptly.
  const notifiedRef = useRef<Set<string>>(new Set())
  const handleDone = (sessionId: string, query: string, ok: boolean) => {
    if (notifiedRef.current.has(sessionId)) return
    notifiedRef.current.add(sessionId)
    notifyComplete(query, ok)
    qc.invalidateQueries({ queryKey: ["research"] })
  }

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
    <div className="flex h-full w-full" data-tour="research-root">
      <aside className="flex w-[min(85vw,320px)] shrink-0 flex-col border-r md:w-[320px]" data-tour="research-library">
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
                  onDone={handleDone}
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
