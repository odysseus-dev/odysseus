import { useEffect, useMemo, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { Loader2, Search, X } from "lucide-react"
import { searchMessages, type SessionSearchResult } from "@/api/sessions"
import { cn } from "@/lib/utils"

function formatTimestamp(iso: string | null) {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ""
  const diff = Date.now() - d.getTime()
  if (diff < 86400000) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
  if (diff < 604800000) return d.toLocaleDateString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })
  return d.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })
}

function highlighted(text: string, query: string) {
  const needle = query.trim()
  if (!needle) return text
  const lower = text.toLowerCase()
  const q = needle.toLowerCase()
  const parts: React.ReactNode[] = []
  let cursor = 0
  let idx = lower.indexOf(q)
  while (idx >= 0) {
    if (idx > cursor) parts.push(text.slice(cursor, idx))
    parts.push(<mark key={`${idx}-${cursor}`} className="rounded bg-primary/20 px-0.5 text-foreground">{text.slice(idx, idx + needle.length)}</mark>)
    cursor = idx + needle.length
    idx = lower.indexOf(q, cursor)
  }
  if (cursor < text.length) parts.push(text.slice(cursor))
  return parts
}

export function ConversationSearch() {
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<SessionSearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState(-1)

  const close = () => {
    setOpen(false)
    setQuery("")
    setResults([])
    setLoading(false)
    setSelected(-1)
  }

  useEffect(() => {
    const handler = (e: Event) => {
      const initial = (e as CustomEvent).detail
      setOpen(true)
      if (typeof initial === "string") {
        setQuery(initial)
        setSelected(-1)
        if (initial.trim()) setLoading(true)
      }
      window.setTimeout(() => inputRef.current?.focus(), 30)
    }
    window.addEventListener("odysseus:open-search", handler)
    return () => window.removeEventListener("odysseus:open-search", handler)
  }, [])

  useEffect(() => {
    if (!open) return
    const q = query.trim()
    if (!q) return
    let alive = true
    const id = window.setTimeout(() => {
      searchMessages(q, 20)
        .then((data) => { if (alive) setResults(data) })
        .catch(() => { if (alive) setResults([]) })
        .finally(() => { if (alive) setLoading(false) })
    }, 250)
    return () => {
      alive = false
      window.clearTimeout(id)
    }
  }, [open, query])

  const changeQuery = (value: string) => {
    setQuery(value)
    setSelected(-1)
    if (value.trim()) {
      setLoading(true)
    } else {
      setResults([])
      setLoading(false)
    }
  }

  const groups = useMemo(() => {
    const map = new Map<string, { name: string; items: SessionSearchResult[] }>()
    results.forEach((result) => {
      if (!map.has(result.session_id)) map.set(result.session_id, { name: result.session_name || "Untitled", items: [] })
      map.get(result.session_id)!.items.push(result)
    })
    return [...map.entries()].map(([sessionId, group]) => ({ sessionId, ...group }))
  }, [results])

  const choose = (result: SessionSearchResult | undefined) => {
    if (!result) return
    close()
    navigate(`/chat/${result.session_id}`)
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex animate-fade-in items-start justify-center bg-black/50 p-4 pt-[12vh]" onClick={close}>
      <div className="flex max-h-[76vh] w-full max-w-[min(94vw,42rem)] animate-pop-in flex-col overflow-hidden rounded-xl border bg-popover shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 border-b px-3 py-2">
          <Search className="size-4 shrink-0 text-muted-foreground" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => changeQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") { e.preventDefault(); close(); return }
              if (e.key === "ArrowDown") { e.preventDefault(); setSelected((i) => Math.min(results.length - 1, i + 1)); return }
              if (e.key === "ArrowUp") { e.preventDefault(); setSelected((i) => Math.max(0, i - 1)); return }
              if (e.key === "Enter") { e.preventDefault(); choose(results[selected >= 0 ? selected : 0]); return }
            }}
            placeholder="Search conversations..."
            className="h-10 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          {loading && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
          <button onClick={close} title="Close search" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><X className="size-4" /></button>
        </div>
        <div className="min-h-0 overflow-y-auto p-2">
          {!query.trim() ? (
            <div className="px-3 py-8 text-center text-sm text-muted-foreground">Type to search message history across your chats.</div>
          ) : !loading && results.length === 0 ? (
            <div className="px-3 py-8 text-center text-sm text-muted-foreground">No results found.</div>
          ) : (
            groups.map((group) => (
              <section key={group.sessionId} className="mb-2">
                <div className="px-2 py-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">{group.name}</div>
                <div className="space-y-1">
                  {group.items.map((item) => {
                    const flatIndex = results.findIndex((r) => r.message_id === item.message_id)
                    const active = flatIndex === selected
                    return (
                      <button
                        key={item.message_id}
                        onClick={() => choose(item)}
                        onMouseEnter={() => setSelected(flatIndex)}
                        className={cn("flex w-full items-start gap-3 rounded-md px-2.5 py-2 text-left text-sm transition-colors", active ? "bg-accent text-foreground" : "hover:bg-accent/70")}
                      >
                        <span className="mt-0.5 w-10 shrink-0 text-xs font-medium text-muted-foreground">{item.role === "user" ? "You" : "AI"}</span>
                        <span className="min-w-0 flex-1">
                          <span className="line-clamp-2 text-sm leading-5">{highlighted(item.content_snippet || "", query)}</span>
                          {item.context_before?.[0]?.content && <span className="mt-0.5 block truncate text-xs text-muted-foreground">{item.context_before[0].content}</span>}
                        </span>
                        <span className="shrink-0 text-xs text-muted-foreground">{formatTimestamp(item.timestamp)}</span>
                      </button>
                    )
                  })}
                </div>
              </section>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
