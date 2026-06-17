import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { Telescope, Loader2, Archive, ArchiveRestore, Trash2, MessageSquarePlus, ExternalLink } from "lucide-react"
import {
  useResearchActive, useResearchLibrary, useResearchDetail, useResearchMutations,
  type ResearchActiveItem, type ResearchLibraryItem,
} from "@/api/research"
import { Markdown } from "@/components/chat/Markdown"
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
    <div
      onClick={onClick}
      className={cn("cursor-pointer rounded-md px-3 py-2 text-sm",
        active ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground")}
    >
      <div className="truncate font-medium text-foreground">{item.query || "Untitled research"}</div>
      <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
        {item.category && <span>{item.category}</span>}
        <span>{item.source_count} {item.source_count === 1 ? "source" : "sources"}</span>
        {item.duration && <span>{item.duration}</span>}
        {item.completed_at ? <span>{new Date(item.completed_at * 1000).toLocaleDateString()}</span> : null}
      </div>
    </div>
  )
}

function SourceList({ sources }: { sources: Source[] }) {
  if (!sources.length) return null
  return (
    <div className="mt-6 border-t pt-4">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        Sources <span className="font-normal">({sources.length})</span>
      </div>
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
    </div>
  )
}

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
  const sources = data?.sources || []
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex h-13 shrink-0 items-center gap-2 border-b px-4">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold">{data?.query || "Research"}</div>
          {data?.category && <div className="truncate text-xs text-muted-foreground">{data.category}</div>}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button variant="outline" size="sm" disabled={spinningOff} onClick={onSpinoff} title="Continue in chat">
            {spinningOff ? <Loader2 className="size-3.5 animate-spin" /> : <MessageSquarePlus className="size-3.5" />}
            Continue in chat
          </Button>
          <button onClick={onArchiveToggle} title={archived ? "Restore" : "Archive"} className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">
            {archived ? <ArchiveRestore className="size-4" /> : <Archive className="size-4" />}
          </button>
          <button onClick={() => { if (confirm("Delete this research report?")) onDelete() }} title="Delete" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-destructive">
            <Trash2 className="size-4" />
          </button>
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />Loading…</div>
        ) : data?.result ? (
          <>
            <Markdown>{data.result}</Markdown>
            <SourceList sources={sources} />
          </>
        ) : (
          <p className="text-sm text-muted-foreground">No report available for this research.</p>
        )}
      </div>
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
          <button
            onClick={() => { setShowArchived((v) => !v); setSelected(null) }}
            className={cn("rounded-md px-2 py-1 text-xs font-medium", showArchived ? "bg-accent text-foreground" : "font-normal text-muted-foreground hover:text-foreground")}
          >
            {showArchived ? "Archived" : "Library"}
          </button>
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
                {showArchived ? "No archived research." : "No research yet. Run a deep-research query from chat to get started."}
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
        <div className="flex min-h-0 flex-1 flex-col">
          <header className="flex h-13 shrink-0 items-center gap-2 border-b px-4 text-sm font-semibold">
            <Telescope className="size-4" />Research
          </header>
          <div className="flex flex-1 items-center justify-center p-8 text-center text-sm text-muted-foreground">
            Select a research report to view it.
          </div>
        </div>
      )}
    </div>
  )
}
