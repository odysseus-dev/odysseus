import { useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { Plus, MessageSquare, Trash2, Search } from "lucide-react"
import { useSessions, useSessionMutations } from "@/api/sessions"
import { cn } from "@/lib/utils"

export function SessionsSidebar() {
  const { data: sessions } = useSessions()
  const { remove } = useSessionMutations()
  const navigate = useNavigate()
  const { sessionId } = useParams()
  const [q, setQ] = useState("")
  const list = (sessions || [])
    .filter((s) => !s.archived)
    .filter((s) => !q || (s.name || "").toLowerCase().includes(q.toLowerCase()))

  return (
    <div className="flex h-full w-64 shrink-0 flex-col border-r bg-sidebar">
      <div className="space-y-2 p-2">
        <button onClick={() => navigate("/chat")} className="flex w-full items-center gap-2 rounded-md border px-3 py-2 text-sm font-medium hover:bg-accent">
          <Plus className="size-4" /> New chat
        </button>
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search chats…"
            className="h-8 w-full rounded-md border bg-background pl-8 pr-2 text-sm outline-none focus-visible:border-ring"
          />
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-2">
        {list.map((s) => (
          <div
            key={s.id}
            onClick={() => navigate(`/chat/${s.id}`)}
            className={cn(
              "group flex cursor-pointer items-center gap-2 rounded-md px-2 py-2 text-sm",
              s.id === sessionId ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
            )}
          >
            <MessageSquare className="size-4 shrink-0" />
            <span className="flex-1 truncate">{s.name || "Untitled"}</span>
            <button
              onClick={(e) => { e.stopPropagation(); if (confirm("Delete this chat?")) { remove.mutate(s.id); if (s.id === sessionId) navigate("/chat") } }}
              className="text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
            >
              <Trash2 className="size-3.5" />
            </button>
          </div>
        ))}
        {list.length === 0 && <p className="px-2 py-4 text-xs text-muted-foreground">{q ? "No matches." : "No chats yet."}</p>}
      </div>
    </div>
  )
}
