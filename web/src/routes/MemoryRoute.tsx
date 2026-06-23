import { useMemo, useRef, useState } from "react"
import {
  Check,
  CheckSquare,
  Download,
  Loader2,
  MessageSquare,
  Pencil,
  Pin,
  Plus,
  Settings2,
  Sparkles,
  Square,
  Trash2,
  Upload,
  X,
} from "lucide-react"
import { useMemory, useMemoryMutations, type MemoryImportSuggestion } from "@/api/memory"
import { usePrefs, useSetPref } from "@/api/prefs"
import { useSessions } from "@/api/sessions"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { cn } from "@/lib/utils"
import { toast } from "@/stores/toast"
import type { Memory } from "@/types"

const CATS = ["fact", "preference", "identity", "project", "goal", "task", "contact"]
type SortMode = "newest" | "oldest" | "az" | "uses" | "category" | "source"
type ReviewItem = { text: string; category: string; active: boolean }

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return <Switch checked={on} onCheckedChange={onClick} />
}

function SettingRow({ label, on, onClick }: { label: string; on: boolean; onClick: () => void }) {
  return <div className="flex items-center justify-between py-1.5"><span className="text-sm text-muted-foreground">{label}</span><Toggle on={on} onClick={onClick} /></div>
}

function NumberSettingRow({
  label,
  prefKey,
  value,
  min,
  max,
  step,
  onSave,
}: {
  label: string
  prefKey: string
  value: number
  min: number
  max: number
  step: number
  onSave: (next: number) => void
}) {
  return (
    <div className="flex items-center justify-between py-1.5">
      <span className="text-sm text-muted-foreground">{label}</span>
      <input
        key={`${prefKey}-${value}`}
        type="number"
        min={min}
        max={max}
        step={step}
        defaultValue={value}
        onBlur={(e) => {
          const raw = Number(e.target.value)
          if (!Number.isFinite(raw)) return
          const next = Math.max(min, Math.min(max, raw))
          e.target.value = String(next)
          onSave(next)
        }}
        className="h-8 w-20 rounded-md border bg-background px-2 text-sm outline-none focus-visible:border-ring"
      />
    </div>
  )
}

function MemorySettings() {
  const { data: prefs } = usePrefs()
  const setPref = useSetPref()
  const b = (k: string, def = true) => (prefs?.[k] as boolean | undefined) ?? def
  const num = (k: string, def: number) => typeof prefs?.[k] === "number" ? prefs[k] as number : def
  const row = (k: string, label: string) => <SettingRow label={label} on={b(k)} onClick={() => setPref.mutate({ key: k, value: !b(k) })} />
  return (
    <div className="mb-4 space-y-1 rounded-md border bg-card p-3">
      <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Memory settings</div>
      {row("memory_enabled", "Inject memories into chat")}
      {row("skills_enabled", "Inject skills into agent chats")}
      {row("auto_memory", "Auto-extract memories from chats")}
      {row("auto_skills", "Auto-extract skills from agent runs")}
      {row("auto_approve_skills", "Auto-approve extracted skills")}
      <NumberSettingRow label="Skill min-confidence" prefKey="skill_min_confidence" value={num("skill_min_confidence", 0.85)} min={0} max={1} step={0.05} onSave={(value) => setPref.mutate({ key: "skill_min_confidence", value })} />
      <NumberSettingRow label="Max injected skills" prefKey="skill_max_injected" value={num("skill_max_injected", 3)} min={0} max={20} step={1} onSave={(value) => setPref.mutate({ key: "skill_max_injected", value: Math.round(value) })} />
    </div>
  )
}

function normalizeSuggestion(item: MemoryImportSuggestion | string): ReviewItem | null {
  if (typeof item === "string") {
    const text = item.trim()
    return text ? { text, category: "fact", active: true } : null
  }
  const text = (item.text || "").trim()
  return text ? { text, category: item.category || "fact", active: true } : null
}

function memoryCategory(m: Memory) {
  return m.category || (m.categories || [])[0] || "fact"
}

function memoryTimestamp(m: Memory) {
  return typeof m.timestamp === "number" ? m.timestamp : 0
}

function memoryUses(m: Memory) {
  return typeof m.uses === "number" ? m.uses : 0
}

function relativeTime(timestamp?: number) {
  if (!timestamp) return ""
  const diff = Math.max(0, Math.floor(Date.now() / 1000) - timestamp)
  if (diff < 60) return "just now"
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`
  if (diff < 2592000) return `${Math.floor(diff / 604800)}w ago`
  if (diff < 31536000) return `${Math.floor(diff / 2592000)}mo ago`
  return `${Math.floor(diff / 31536000)}y ago`
}

function sourceLabel(source?: string) {
  return source === "auto" ? "auto" : "manual"
}

function sortMemories(items: Memory[], sort: SortMode) {
  return [...items].sort((a, b) => {
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1
    if (sort === "oldest") return memoryTimestamp(a) - memoryTimestamp(b)
    if (sort === "az") return (a.text || "").localeCompare(b.text || "")
    if (sort === "uses") return memoryUses(b) - memoryUses(a) || memoryTimestamp(b) - memoryTimestamp(a)
    if (sort === "category") return memoryCategory(a).localeCompare(memoryCategory(b)) || (a.text || "").localeCompare(b.text || "")
    if (sort === "source") return (a.source || "").localeCompare(b.source || "") || memoryTimestamp(b) - memoryTimestamp(a)
    return memoryTimestamp(b) - memoryTimestamp(a)
  })
}

export function MemoryRoute() {
  const fileRef = useRef<HTMLInputElement | null>(null)
  const { data: memories } = useMemory()
  const { data: sessions } = useSessions()
  const { add, update, remove, bulkRemove, pin, tidy, importFile, extract } = useMemoryMutations()
  const [text, setText] = useState("")
  const [cat, setCat] = useState("fact")
  const [filter, setFilter] = useState<string | null>(null)
  const [sort, setSort] = useState<SortMode>("newest")
  const [q, setQ] = useState("")
  const [showSettings, setShowSettings] = useState(false)
  const [editId, setEditId] = useState<string | null>(null)
  const [editText, setEditText] = useState("")
  const [editCat, setEditCat] = useState("fact")
  const [selectMode, setSelectMode] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [review, setReview] = useState<ReviewItem[]>([])
  const [importName, setImportName] = useState("")
  const [showExtract, setShowExtract] = useState(false)
  const [extractSession, setExtractSession] = useState("")

  const list = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const filtered = (memories || [])
      .filter((m) => !filter || m.category === filter || (m.categories || []).includes(filter))
      .filter((m) => !needle || (m.text || "").toLowerCase().includes(needle))
    return sortMemories(filtered, sort)
  }, [filter, memories, q, sort])

  const remainingReview = review.filter((item) => item.active)
  const allVisibleSelected = list.length > 0 && list.every((m) => selected.has(m.id))
  const selectedCount = selected.size

  const submit = async () => {
    const value = text.trim()
    if (!value) return
    await add.mutateAsync({ text: value, category: cat })
    setText("")
    toast("Memory added", "success")
  }

  const startEdit = (m: Memory) => {
    setEditId(m.id)
    setEditText(m.text)
    setEditCat(memoryCategory(m))
  }

  const saveEdit = async () => {
    if (!editId || !editText.trim()) return
    await update.mutateAsync({ id: editId, text: editText, category: editCat })
    setEditId(null)
    toast("Memory updated", "success")
  }

  const toggleSelected = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleSelectAllVisible = () => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (allVisibleSelected) list.forEach((m) => next.delete(m.id))
      else list.forEach((m) => next.add(m.id))
      return next
    })
  }

  const exitSelectMode = () => {
    setSelectMode(false)
    setSelected(new Set())
  }

  const deleteSelected = async () => {
    const ids = Array.from(selected)
    if (!ids.length) return
    if (!confirm(`Delete ${ids.length} selected ${ids.length === 1 ? "memory" : "memories"}?`)) return
    await bulkRemove.mutateAsync(ids)
    exitSelectMode()
    toast(`Deleted ${ids.length} ${ids.length === 1 ? "memory" : "memories"}`, "success")
  }

  const runTidy = async () => {
    const result = await tidy.mutateAsync()
    const removed = result.removed || 0
    toast(removed ? `Tidied memories and removed ${removed}` : "Memory is already tidy", "success")
  }

  const exportMemories = () => {
    const items = memories || []
    if (!items.length) {
      toast("No memories to export", "info")
      return
    }
    const blob = new Blob([JSON.stringify(items, null, 2)], { type: "application/json" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = "memories.json"
    a.click()
    URL.revokeObjectURL(url)
    toast(`Exported ${items.length} memories`, "success")
  }

  const handleImport = async (file: File | undefined) => {
    if (!file) return
    const data = await importFile.mutateAsync(file)
    const items = (data.suggestions || []).map(normalizeSuggestion).filter((x): x is ReviewItem => Boolean(x))
    setImportName(data.filename || file.name)
    setReview(items)
    toast(items.length ? `Found ${items.length} memory suggestions` : data.message || "No useful memories found", items.length ? "success" : "info")
  }

  const runExtract = async () => {
    const sessionId = extractSession.trim()
    if (!sessionId) return
    try {
      const data = await extract.mutateAsync(sessionId)
      const items = (data.suggestions || []).map(normalizeSuggestion).filter((x): x is ReviewItem => Boolean(x))
      const label = sessions?.find((s) => s.id === sessionId)?.name
      setImportName(label ? `session "${label}"` : "session")
      setReview(items)
      setShowExtract(false)
      toast(items.length ? `Found ${items.length} memory suggestions` : "No useful memories found", items.length ? "success" : "info")
    } catch (e) {
      toast(e instanceof Error ? e.message : "Couldn't extract memories from that session", "error")
    }
  }

  const saveReviewItem = async (idx: number) => {
    const item = review[idx]
    if (!item?.active) return
    await add.mutateAsync({ text: item.text, category: item.category })
    setReview((prev) => prev.map((x, i) => i === idx ? { ...x, active: false } : x))
    toast("Saved to memory", "success")
  }

  const saveAllReview = async () => {
    let saved = 0
    for (const item of remainingReview) {
      await add.mutateAsync({ text: item.text, category: item.category })
      saved += 1
    }
    setReview([])
    toast(`Saved ${saved} ${saved === 1 ? "memory" : "memories"}`, "success")
  }

  return (
    <div className="mx-auto flex h-full w-full max-w-4xl flex-col" data-tour="memory-root">
      <header className="flex h-13 shrink-0 items-center justify-between border-b px-4">
        <span className="text-sm font-semibold">Memory</span>
        <div className="flex items-center gap-1.5">
          <Button size="sm" variant="outline" disabled={tidy.isPending} onClick={runTidy} title="Tidy memories" data-tour="memory-tidy">
            {tidy.isPending ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
            Tidy
          </Button>
          <Button size="sm" variant="outline" onClick={() => setShowExtract((s) => !s)} disabled={extract.isPending} title="Extract memories from a chat session">
            {extract.isPending ? <Loader2 className="size-4 animate-spin" /> : <MessageSquare className="size-4" />}
            Extract
          </Button>
          <Button size="sm" variant="outline" onClick={() => fileRef.current?.click()} disabled={importFile.isPending} title="Import memories">
            {importFile.isPending ? <Loader2 className="size-4 animate-spin" /> : <Upload className="size-4" />}
            Import
          </Button>
          <Button size="sm" variant="outline" onClick={exportMemories} title="Export memories"><Download className="size-4" />Export</Button>
          <button data-tour="memory-settings" onClick={() => setShowSettings((s) => !s)} title="Memory settings" className={cn("rounded-md p-1.5 hover:bg-accent hover:text-foreground", showSettings ? "text-foreground" : "text-muted-foreground")}><Settings2 className="size-4" /></button>
        </div>
        <input
          ref={fileRef}
          type="file"
          hidden
          accept=".txt,.md,.pdf,.csv,.log,.json,.py,.js,.html"
          onChange={(e) => { void handleImport(e.currentTarget.files?.[0]); e.currentTarget.value = "" }}
        />
      </header>
      <div className="flex-1 overflow-y-auto p-4">
        {showSettings && <MemorySettings />}

        <div className="mb-4 flex flex-wrap gap-2" data-tour="memory-add">
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Add a memory..."
            onKeyDown={(e) => { if (e.key === "Enter") void submit() }}
            className="h-9 min-w-52 flex-1 rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"
          />
          <select value={cat} onChange={(e) => setCat(e.target.value)} className="h-9 rounded-md border bg-background px-2 text-sm capitalize">
            {CATS.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <Button onClick={submit} disabled={add.isPending}><Plus className="size-4" />Add</Button>
        </div>

        {showExtract && (
          <div className="mb-4 rounded-lg border bg-card p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <div className="text-sm font-medium">Extract from a chat session</div>
              <button onClick={() => setShowExtract(false)} title="Close" className="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground"><X className="size-4" /></button>
            </div>
            <p className="mb-2 text-xs text-muted-foreground">Analyze a conversation for facts worth remembering, then review the suggestions before saving.</p>
            <div className="flex flex-wrap gap-2">
              <select
                value={extractSession}
                onChange={(e) => setExtractSession(e.target.value)}
                className="h-9 min-w-52 flex-1 rounded-md border bg-background px-2 text-sm outline-none focus-visible:border-ring"
              >
                <option value="">Select a session...</option>
                {(sessions || []).map((s) => (
                  <option key={s.id} value={s.id}>{s.name || s.id.slice(0, 8)}</option>
                ))}
              </select>
              <input
                value={extractSession}
                onChange={(e) => setExtractSession(e.target.value)}
                placeholder="...or paste a session id"
                className="h-9 min-w-44 flex-1 rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"
              />
              <Button onClick={runExtract} disabled={!extractSession.trim() || extract.isPending}>
                {extract.isPending ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
                Extract
              </Button>
            </div>
          </div>
        )}

        {review.length > 0 && (
          <div className="mb-4 rounded-lg border bg-card p-3">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-sm font-medium">Imported from {importName || "file"}</div>
                <div className="text-xs text-muted-foreground">{remainingReview.length} suggestion{remainingReview.length === 1 ? "" : "s"} ready to review</div>
              </div>
              <div className="flex items-center gap-2">
                <Button size="sm" disabled={!remainingReview.length || add.isPending} onClick={saveAllReview}><Check className="size-4" />Save all</Button>
                <Button size="sm" variant="outline" onClick={() => setReview([])}><X className="size-4" />Dismiss</Button>
              </div>
            </div>
            <div className="space-y-2">
              {review.map((item, idx) => item.active && (
                <div key={`${idx}-${item.text}`} className="flex items-start gap-3 rounded-md border bg-background p-2">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm">{item.text}</p>
                    <span className="mt-1 inline-block rounded-full bg-muted px-2 py-0.5 text-[11px] capitalize text-muted-foreground">{item.category}</span>
                  </div>
                  <Button size="sm" variant="outline" disabled={add.isPending} onClick={() => saveReviewItem(idx)}><Check className="size-4" />Save</Button>
                  <button onClick={() => setReview((prev) => prev.map((x, i) => i === idx ? { ...x, active: false } : x))} title="Reject suggestion" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><X className="size-4" /></button>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="mb-3 grid gap-2 sm:grid-cols-[1fr_auto_auto]" data-tour="memory-filters">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search memories..."
            className="h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring" />
          <select value={sort} onChange={(e) => setSort(e.target.value as SortMode)} className="h-9 rounded-md border bg-background px-2 text-sm outline-none focus-visible:border-ring" title="Sort memories">
            <option value="newest">Newest</option>
            <option value="oldest">Oldest</option>
            <option value="az">A-Z</option>
            <option value="uses">Most used</option>
            <option value="category">Category</option>
            <option value="source">Source</option>
          </select>
          <Button
            size="sm"
            variant={selectMode ? "secondary" : "outline"}
            onClick={() => selectMode ? exitSelectMode() : setSelectMode(true)}
          >
            {selectMode ? <X className="size-4" /> : <CheckSquare className="size-4" />}
            {selectMode ? "Cancel" : "Select"}
          </Button>
        </div>

        <div className="mb-4 flex flex-wrap gap-1.5">
          <button onClick={() => setFilter(null)} className={cn("rounded-full border px-3 py-1 text-xs", !filter ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}>All</button>
          {CATS.map((c) => (
            <button key={c} onClick={() => setFilter(c)} className={cn("rounded-full border px-3 py-1 text-xs capitalize", filter === c ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}>{c}</button>
          ))}
        </div>

        {selectMode && (
          <div className="mb-3 flex items-center justify-between gap-3 rounded-lg border bg-muted/35 px-3 py-2">
            <button onClick={toggleSelectAllVisible} className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
              {allVisibleSelected ? <CheckSquare className="size-4" /> : <Square className="size-4" />}
              All visible
            </button>
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">{selectedCount} selected</span>
              <Button size="sm" variant="destructive" disabled={!selectedCount || bulkRemove.isPending} onClick={deleteSelected}><Trash2 className="size-4" />Delete</Button>
            </div>
          </div>
        )}

        <div className="space-y-2" data-tour="memory-list">
          {list.map((m) => {
            const category = memoryCategory(m)
            const isSelected = selected.has(m.id)
            const timestamp = memoryTimestamp(m)
            const when = relativeTime(timestamp)
            const uses = memoryUses(m)
            return (
              <div key={m.id} className={cn("group flex items-start gap-3 rounded-md border bg-card p-3", isSelected && "border-primary/70 bg-primary/5")}>
                {selectMode && (
                  <button onClick={() => toggleSelected(m.id)} title={isSelected ? "Deselect memory" : "Select memory"} className="mt-0.5 text-muted-foreground hover:text-foreground">
                    {isSelected ? <CheckSquare className="size-4" /> : <Square className="size-4" />}
                  </button>
                )}
                <div className="min-w-0 flex-1">
                  {editId === m.id ? (
                    <div className="flex flex-wrap gap-2">
                      <input
                        autoFocus
                        value={editText}
                        onChange={(e) => setEditText(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter") void saveEdit(); if (e.key === "Escape") setEditId(null) }}
                        className="h-8 min-w-52 flex-1 rounded-md border bg-background px-2 text-sm outline-none focus-visible:border-ring"
                      />
                      <select value={editCat} onChange={(e) => setEditCat(e.target.value)} className="h-8 rounded-md border bg-background px-2 text-sm capitalize">
                        {CATS.map((c) => <option key={c} value={c}>{c}</option>)}
                      </select>
                      <button onClick={saveEdit} className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><Check className="size-4" /></button>
                    </div>
                  ) : (
                    <>
                      <p className="text-sm" onDoubleClick={() => startEdit(m)}>{m.text}</p>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5">
                        <span className="inline-block rounded-full bg-muted px-2 py-0.5 text-[11px] capitalize text-muted-foreground">{category}</span>
                        <span className="text-[11px] text-muted-foreground">{sourceLabel(m.source)}</span>
                        {uses > 0 && <span className="text-[11px] text-muted-foreground" title={`Injected into chat context ${uses} ${uses === 1 ? "time" : "times"}`}>{uses}x</span>}
                        {when && <span className="text-[11px] text-muted-foreground" title={new Date(timestamp * 1000).toLocaleString()}>{when}</span>}
                        {m.pinned && <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground"><Pin className="size-3" />Pinned</span>}
                      </div>
                    </>
                  )}
                </div>
                {editId !== m.id && (
                  <div className={cn("flex shrink-0 gap-1.5", selectMode ? "opacity-100" : "opacity-100 md:opacity-0 md:transition-opacity md:group-hover:opacity-100")}>
                    <button onClick={async () => { await pin.mutateAsync({ id: m.id, pinned: !m.pinned }); toast(m.pinned ? "Memory unpinned" : "Pinned - always in context", "success") }} title={m.pinned ? "Unpin memory" : "Pin memory"} className={cn("text-muted-foreground hover:text-foreground", m.pinned && "text-foreground")}><Pin className="size-3.5" /></button>
                    <button onClick={() => startEdit(m)} className="text-muted-foreground hover:text-foreground" title="Edit memory"><Pencil className="size-3.5" /></button>
                    <button onClick={() => { if (confirm("Delete this memory?")) remove.mutate(m.id) }} className="text-muted-foreground hover:text-destructive" title="Delete memory"><Trash2 className="size-4" /></button>
                  </div>
                )}
              </div>
            )
          })}
          {list.length === 0 && <p className="py-8 text-center text-sm text-muted-foreground">{q.trim() || filter ? "No matches." : "No memories yet."}</p>}
        </div>
      </div>
    </div>
  )
}
