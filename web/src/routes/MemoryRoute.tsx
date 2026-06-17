import { useState } from "react"
import { Trash2, Plus } from "lucide-react"
import { useMemory, useMemoryMutations } from "@/api/memory"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const CATS = ["fact", "preference", "identity", "project", "goal", "task", "contact"]

export function MemoryRoute() {
  const { data: memories } = useMemory()
  const { add, remove } = useMemoryMutations()
  const [text, setText] = useState("")
  const [cat, setCat] = useState("fact")
  const [filter, setFilter] = useState<string | null>(null)
  const list = (memories || []).filter((m) => !filter || m.category === filter || (m.categories || []).includes(filter))
  const submit = () => { if (text.trim()) { add.mutate({ text, category: cat }); setText("") } }

  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col">
      <header className="flex h-13 shrink-0 items-center border-b px-4 text-sm font-semibold">Memory</header>
      <div className="flex-1 overflow-y-auto p-4">
        <div className="mb-4 flex gap-2">
          <input
            value={text} onChange={(e) => setText(e.target.value)} placeholder="Add a memory…"
            onKeyDown={(e) => { if (e.key === "Enter") submit() }}
            className="h-9 flex-1 rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"
          />
          <select value={cat} onChange={(e) => setCat(e.target.value)} className="h-9 rounded-md border bg-background px-2 text-sm capitalize">
            {CATS.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <Button onClick={submit}><Plus className="size-4" />Add</Button>
        </div>
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
                <p className="text-sm">{m.text}</p>
                <span className="mt-1 inline-block rounded-full bg-muted px-2 py-0.5 text-[11px] capitalize text-muted-foreground">{m.category || (m.categories || [])[0] || "fact"}</span>
              </div>
              <button onClick={() => remove.mutate(m.id)} className="text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"><Trash2 className="size-4" /></button>
            </div>
          ))}
          {list.length === 0 && <p className="py-8 text-center text-sm text-muted-foreground">No memories yet.</p>}
        </div>
      </div>
    </div>
  )
}
