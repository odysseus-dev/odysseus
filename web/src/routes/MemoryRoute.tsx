import { useState } from "react"
import { Trash2, Plus, Pencil, Check, Settings2 } from "lucide-react"
import { useMemory, useMemoryMutations } from "@/api/memory"
import { usePrefs, useSetPref } from "@/api/prefs"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { cn } from "@/lib/utils"

const CATS = ["fact", "preference", "identity", "project", "goal", "task", "contact"]

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return <Switch checked={on} onCheckedChange={onClick} />
}
// Module-scope so it isn't recreated each render (which would reset state).
function SettingRow({ label, on, onClick }: { label: string; on: boolean; onClick: () => void }) {
  return <div className="flex items-center justify-between py-1.5"><span className="text-sm text-muted-foreground">{label}</span><Toggle on={on} onClick={onClick} /></div>
}
function MemorySettings() {
  const { data: prefs } = usePrefs()
  const setPref = useSetPref()
  const b = (k: string, def = true) => (prefs?.[k] as boolean | undefined) ?? def
  const num = (prefs?.skill_min_confidence as number | undefined) ?? 0.85
  const row = (k: string, label: string) => <SettingRow label={label} on={b(k)} onClick={() => setPref.mutate({ key: k, value: !b(k) })} />
  return (
    <div className="mb-4 space-y-1 rounded-lg border bg-card p-3">
      <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Memory settings</div>
      {row("memory_enabled", "Inject memories into chat")}
      {row("auto_skills", "Auto-extract skills from agent runs")}
      {row("auto_approve_skills", "Auto-approve extracted skills")}
      <div className="flex items-center justify-between py-1.5">
        <span className="text-sm text-muted-foreground">Skill min-confidence</span>
        <input type="number" min={0} max={1} step={0.05} defaultValue={num}
          onBlur={(e) => setPref.mutate({ key: "skill_min_confidence", value: parseFloat(e.target.value) })}
          className="h-8 w-20 rounded-md border bg-background px-2 text-sm outline-none focus-visible:border-ring" />
      </div>
    </div>
  )
}

export function MemoryRoute() {
  const { data: memories } = useMemory()
  const { add, update, remove } = useMemoryMutations()
  const [text, setText] = useState("")
  const [cat, setCat] = useState("fact")
  const [filter, setFilter] = useState<string | null>(null)
  const [q, setQ] = useState("")
  const [showSettings, setShowSettings] = useState(false)
  const [editId, setEditId] = useState<string | null>(null)
  const [editText, setEditText] = useState("")
  const list = (memories || [])
    .filter((m) => !filter || m.category === filter || (m.categories || []).includes(filter))
    .filter((m) => !q || (m.text || "").toLowerCase().includes(q.toLowerCase()))
  const submit = () => { if (text.trim()) { add.mutate({ text, category: cat }); setText("") } }
  const startEdit = (id: string, t: string) => { setEditId(id); setEditText(t) }
  const saveEdit = () => { if (editId && editText.trim()) update.mutate({ id: editId, text: editText }); setEditId(null) }

  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col">
      <header className="flex h-13 shrink-0 items-center justify-between border-b px-4">
        <span className="text-sm font-semibold">Memory</span>
        <button onClick={() => setShowSettings((s) => !s)} title="Memory settings" className={cn("rounded-md p-1.5 hover:bg-accent hover:text-foreground", showSettings ? "text-foreground" : "text-muted-foreground")}><Settings2 className="size-4" /></button>
      </header>
      <div className="flex-1 overflow-y-auto p-4">
        {showSettings && <MemorySettings />}
        <div className="mb-4 flex gap-2">
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Add a memory…" onKeyDown={(e) => { if (e.key === "Enter") submit() }}
            className="h-9 flex-1 rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring" />
          <select value={cat} onChange={(e) => setCat(e.target.value)} className="h-9 rounded-md border bg-background px-2 text-sm capitalize">
            {CATS.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <Button onClick={submit}><Plus className="size-4" />Add</Button>
        </div>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search memories…"
          className="mb-3 h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring" />
        <div className="mb-4 flex flex-wrap gap-1.5">
          <button onClick={() => setFilter(null)} className={cn("rounded-full border px-3 py-1 text-xs", !filter ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}>All</button>
          {CATS.map((c) => (
            <button key={c} onClick={() => setFilter(c)} className={cn("rounded-full border px-3 py-1 text-xs capitalize", filter === c ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}>{c}</button>
          ))}
        </div>
        <div className="space-y-2">
          {list.map((m) => (
            <div key={m.id} className="group flex items-start gap-3 rounded-lg border bg-card p-3">
              <div className="flex-1">
                {editId === m.id ? (
                  <div className="flex gap-2">
                    <input autoFocus value={editText} onChange={(e) => setEditText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") saveEdit(); if (e.key === "Escape") setEditId(null) }}
                      className="h-8 flex-1 rounded-md border bg-background px-2 text-sm outline-none focus-visible:border-ring" />
                    <button onClick={saveEdit} className="text-muted-foreground hover:text-foreground"><Check className="size-4" /></button>
                  </div>
                ) : (
                  <>
                    <p className="text-sm">{m.text}</p>
                    <span className="mt-1 inline-block rounded-full bg-muted px-2 py-0.5 text-[11px] capitalize text-muted-foreground">{m.category || (m.categories || [])[0] || "fact"}</span>
                  </>
                )}
              </div>
              {editId !== m.id && (
                <div className="flex gap-1.5 opacity-0 transition-opacity group-hover:opacity-100">
                  <button onClick={() => startEdit(m.id, m.text)} className="text-muted-foreground hover:text-foreground"><Pencil className="size-3.5" /></button>
                  <button onClick={() => remove.mutate(m.id)} className="text-muted-foreground hover:text-destructive"><Trash2 className="size-4" /></button>
                </div>
              )}
            </div>
          ))}
          {list.length === 0 && <p className="py-8 text-center text-sm text-muted-foreground">No memories yet.</p>}
        </div>
      </div>
    </div>
  )
}
