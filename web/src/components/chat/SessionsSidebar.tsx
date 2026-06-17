import { useNavigate, useParams } from "react-router-dom"
import { Plus, MessageSquare, Trash2 } from "lucide-react"
import { useSessions, useSessionMutations } from "@/api/sessions"
import { cn } from "@/lib/utils"

export function SessionsSidebar() {
  const { data: sessions } = useSessions()
  const { remove } = useSessionMutations()
  const navigate = useNavigate()
  const { sessionId } = useParams()
  const list = (sessions || []).filter((s) => !s.archived)
  return (
    <div className="flex h-full w-64 shrink-0 flex-col border-r bg-sidebar">
      <div className="p-2">
        <button onClick={() => navigate("/chat")} className="flex w-full items-center gap-2 rounded-md border px-3 py-2 text-sm font-medium hover:bg-accent">
          <Plus className="size-4" /> New chat
        </button>
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
              className="opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
            >
              <Trash2 className="size-3.5" />
            </button>
          </div>
        ))}
        {list.length === 0 && <p className="px-2 py-4 text-xs text-muted-foreground">No chats yet.</p>}
      </div>
    </div>
  )
}
