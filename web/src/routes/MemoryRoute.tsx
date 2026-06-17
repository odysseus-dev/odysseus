import { useState } from "react"
import { Trash2, Plus, Pencil, Check } from "lucide-react"
import { useMemory, useMemoryMutations } from "@/api/memory"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const CATS = ["fact", "preference", "identity", "project", "goal", "task", "contact"]

export function MemoryRoute() {
  const { data: memories } = useMemory()
  const { add, update, remove } = useMemoryMutations()
  const [text, setText] = useState("")
  const [cat, setCat] = useState("fact")
  const [filter, setFilter] = useState<string | null>(null)
  const [q, setQ] = useState("")
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
      <header className="flex h-13 shrink-0 items-center border-b px-4 text-sm font-semibold">Memory</header>
      <div className="flex-1 overflow-y-auto p-4">
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
