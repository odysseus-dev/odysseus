import { useState } from "react"
import { Trash2, Pin } from "lucide-react"
import { useNotes, useNoteMutations } from "@/api/notes"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export function NotesRoute() {
  const { data: notes } = useNotes()
  const { create, remove, pin } = useNoteMutations()
  const [title, setTitle] = useState("")
  const [content, setContent] = useState("")
  const submit = () => { if (title.trim() || content.trim()) { create.mutate({ title, content }); setTitle(""); setContent("") } }

  return (
    <div className="mx-auto flex h-full w-full max-w-4xl flex-col">
      <header className="flex h-13 shrink-0 items-center border-b px-4 text-sm font-semibold">Notes</header>
      <div className="flex-1 overflow-y-auto p-4">
        <div className="mb-5 space-y-2 rounded-lg border bg-card p-3">
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title" className="w-full bg-transparent text-sm font-medium outline-none placeholder:text-muted-foreground" />
          <textarea value={content} onChange={(e) => setContent(e.target.value)} placeholder="Take a note…" rows={2} className="w-full resize-none bg-transparent text-sm outline-none placeholder:text-muted-foreground" />
          <div className="flex justify-end"><Button size="sm" onClick={submit}>Add note</Button></div>
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {(notes || []).map((n) => (
            <div key={n.id} className="group relative rounded-lg border bg-card p-3">
              {n.title && <div className="mb-1 pr-12 text-sm font-semibold">{n.title}</div>}
              {n.content && <p className="whitespace-pre-wrap text-sm text-muted-foreground">{n.content}</p>}
              <div className="absolute right-2 top-2 flex gap-1.5 opacity-0 transition-opacity group-hover:opacity-100">
                <button onClick={() => pin.mutate(n.id)} title="Pin" className={cn("text-muted-foreground hover:text-foreground", n.pinned && "text-foreground")}><Pin className="size-3.5" /></button>
                <button onClick={() => remove.mutate(n.id)} title="Delete" className="text-muted-foreground hover:text-destructive"><Trash2 className="size-3.5" /></button>
              </div>
            </div>
          ))}
        </div>
        {(notes || []).length === 0 && <p className="py-8 text-center text-sm text-muted-foreground">No notes yet.</p>}
      </div>
    </div>
  )
}
