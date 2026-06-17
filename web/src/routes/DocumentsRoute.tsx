import { useEffect, useState } from "react"
import { FileText, ArrowLeft, Save, Trash2, Plus } from "lucide-react"
import { useDocuments, useDocument, useDocMutations } from "@/api/documents"
import { Button } from "@/components/ui/button"

function Editor({ id, onBack }: { id: string; onBack: () => void }) {
  const { data, isLoading } = useDocument(id)
  const { update, remove } = useDocMutations()
  const [content, setContent] = useState("")
  const [dirty, setDirty] = useState(false)
  useEffect(() => { if (data?.current_content != null) { setContent(data.current_content); setDirty(false) } }, [data])
  const save = () => update.mutate({ id, content }, { onSuccess: () => setDirty(false) })
  const del = () => { if (confirm("Delete this document?")) remove.mutate(id, { onSuccess: onBack }) }
  return (
    <div className="flex h-full flex-col">
      <header className="flex h-13 shrink-0 items-center gap-2 border-b px-3">
        <Button variant="ghost" size="icon" onClick={onBack} title="Back"><ArrowLeft className="size-4" /></Button>
        <div className="min-w-0 flex-1 truncate text-sm font-semibold">{data?.title || "Untitled"}</div>
        <button onClick={del} title="Delete" className="rounded-md p-1.5 text-muted-foreground hover:text-destructive"><Trash2 className="size-4" /></button>
        <Button size="sm" onClick={save} disabled={!dirty || update.isPending}><Save className="size-4" />{update.isPending ? "Saving…" : dirty ? "Save" : "Saved"}</Button>
      </header>
      {isLoading ? <div className="p-6 text-sm text-muted-foreground">Loading…</div> : (
        <textarea
          value={content}
          onChange={(e) => { setContent(e.target.value); setDirty(true) }}
          spellCheck={false}
          className="min-h-0 flex-1 resize-none bg-transparent p-4 font-mono text-sm outline-none"
        />
      )}
    </div>
  )
}

export function DocumentsRoute() {
  const { data: docs } = useDocuments()
  const { create } = useDocMutations()
  const [openId, setOpenId] = useState<string | null>(null)
  const list = docs || []
  const newDoc = () => create.mutate({ title: "Untitled" }, { onSuccess: (d) => { if (d?.id) setOpenId(d.id) } })
  if (openId) return <div className="mx-auto h-full w-full max-w-3xl"><Editor id={openId} onBack={() => setOpenId(null)} /></div>
  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col">
      <header className="flex h-13 shrink-0 items-center justify-between border-b px-4">
        <span className="text-sm font-semibold">Library</span>
        <Button size="sm" disabled={create.isPending} onClick={newDoc}><Plus className="size-4" />New document</Button>
      </header>
      <div className="flex-1 overflow-y-auto p-4">
        <div className="space-y-2">
          {list.map((d) => (
            <button key={d.id} onClick={() => setOpenId(d.id)} className="flex w-full items-center gap-3 rounded-lg border bg-card p-3 text-left hover:bg-accent/50">
              <FileText className="size-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{d.title || d.name || "Untitled"}</div>
                {d.session_name && <div className="truncate text-xs text-muted-foreground">{d.session_name}</div>}
              </div>
              {d.language && <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">{d.language}</span>}
            </button>
          ))}
          {list.length === 0 && <p className="py-8 text-center text-sm text-muted-foreground">No documents yet.</p>}
        </div>
      </div>
    </div>
  )
}
