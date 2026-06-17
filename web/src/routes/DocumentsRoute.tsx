import { FileText } from "lucide-react"
import { useDocuments } from "@/api/documents"

export function DocumentsRoute() {
  const { data: docs } = useDocuments()
  const list = docs || []
  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col">
      <header className="flex h-13 shrink-0 items-center border-b px-4 text-sm font-semibold">Library</header>
      <div className="flex-1 overflow-y-auto p-4">
        <div className="space-y-2">
          {list.map((d) => (
            <div key={d.id} className="flex items-center gap-3 rounded-lg border bg-card p-3">
              <FileText className="size-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{d.title || d.name || "Untitled"}</div>
                {d.session_name && <div className="truncate text-xs text-muted-foreground">{d.session_name}</div>}
              </div>
              {d.language && <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">{d.language}</span>}
            </div>
          ))}
          {list.length === 0 && <p className="py-8 text-center text-sm text-muted-foreground">No documents yet.</p>}
        </div>
      </div>
    </div>
  )
}
