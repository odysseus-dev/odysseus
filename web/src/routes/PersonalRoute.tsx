import { useRef, useState } from "react"
import {
  FolderOpen, FileText, Upload, RefreshCw, Trash2, Plus, ChevronRight, Folder, CornerLeftUp, FolderInput,
} from "lucide-react"
import {
  usePersonalIndex, usePersonalMutations, useWorkspaceBrowse,
  type BrowseDir,
} from "@/api/personal"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

function fmtSize(bytes: number): string {
  if (!bytes) return "—"
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const inp = "h-9 rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"

function AddDirectoryForm({ onPick }: { onPick: (path: string) => void }) {
  const { addDirectory } = usePersonalMutations()
  const [path, setPath] = useState("")
  const [msg, setMsg] = useState("")
  const go = () => {
    const dir = path.trim()
    if (!dir) { setMsg("Directory path is required"); return }
    addDirectory.mutate(dir, {
      onSuccess: (r) => { setMsg(r.message || `Indexed ${dir}`); setPath("") },
      onError: (e) => setMsg(e instanceof Error ? e.message : "Failed"),
    })
  }
  return (
    <div className="space-y-2 rounded-lg border bg-card p-3">
      <div className="flex gap-2">
        <input
          value={path}
          onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") go() }}
          placeholder="Path inside personal documents"
          className={cn(inp, "flex-1")}
        />
        <Button disabled={addDirectory.isPending} onClick={go}><Plus className="size-4" />{addDirectory.isPending ? "Indexing…" : "Index"}</Button>
      </div>
      {msg && <p className="text-xs text-muted-foreground">{msg}</p>}
      <p className="text-xs text-muted-foreground/70">Tip: use the browser below to fill this in. Paths must be inside the personal documents root.</p>
      <Browser onPick={(p) => { setPath(p); onPick(p) }} />
    </div>
  )
}

function BrowserRow({ dir, onOpen }: { dir: BrowseDir; onOpen: (path: string) => void }) {
  return (
    <button onClick={() => onOpen(dir.path)} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent/60">
      <Folder className="size-4 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 truncate">{dir.name}</span>
      <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />
    </button>
  )
}

function Browser({ onPick }: { onPick: (path: string) => void }) {
  const [open, setOpen] = useState(false)
  const [path, setPath] = useState<string | null>(null)
  const { data, isLoading, isError } = useWorkspaceBrowse(open ? path : null)

  if (!open) {
    return (
      <Button variant="outline" size="sm" onClick={() => { setOpen(true); setPath("") }}>
        <FolderInput className="size-4" />Browse server folders
      </Button>
    )
  }
  if (data && data.admin === false) {
    return <p className="rounded-md border bg-muted/30 p-2 text-xs text-muted-foreground">Folder browsing is admin-only.</p>
  }
  const dirs = data && data.admin ? data.dirs : []
  return (
    <div className="rounded-md border bg-background">
      <div className="flex items-center gap-2 border-b px-2 py-1.5 text-xs text-muted-foreground">
        <FolderOpen className="size-3.5 shrink-0" />
        <span className="min-w-0 flex-1 truncate font-mono">{data && data.admin ? data.path : "…"}</span>
        {data && data.admin && data.parent && (
          <button onClick={() => setPath(data.parent)} title="Up" className="shrink-0 hover:text-foreground"><CornerLeftUp className="size-3.5" /></button>
        )}
      </div>
      <div className="max-h-56 overflow-y-auto p-1">
        {isLoading ? <p className="p-2 text-xs text-muted-foreground">Loading…</p>
          : isError ? <p className="p-2 text-xs text-muted-foreground">Could not list this folder.</p>
          : dirs.length === 0 ? <p className="p-2 text-xs text-muted-foreground">No subfolders here.</p>
          : dirs.map((d) => <BrowserRow key={d.path} dir={d} onOpen={setPath} />)}
      </div>
      {data && data.admin && (
        <div className="flex items-center justify-between gap-2 border-t px-2 py-1.5">
          <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
            {data.selectable ? "This folder can be used." : "Browse into a subfolder to select."}
          </span>
          <Button size="sm" variant="ghost" disabled={!data.selectable} onClick={() => onPick(data.path)}>Use this folder</Button>
        </div>
      )}
    </div>
  )
}

function UploadDropZone() {
  const { upload } = usePersonalMutations()
  const ref = useRef<HTMLInputElement>(null)
  const [msg, setMsg] = useState("")
  const [dragging, setDragging] = useState(false)
  const onFiles = (files: FileList | File[] | null) => {
    const picked = Array.from(files || [])
    if (picked.length === 0) return
    setMsg("")
    upload.mutate(picked, {
      onSuccess: (r) => setMsg(`Uploaded ${r.uploaded.length} file${r.uploaded.length === 1 ? "" : "s"}; ${r.indexed_count} chunk${r.indexed_count === 1 ? "" : "s"} indexed${r.failed_count ? `, ${r.failed_count} failed` : ""}.`),
      onError: (e) => setMsg(e instanceof Error ? e.message : "Upload failed"),
    })
  }
  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false) }}
      onDrop={(e) => { e.preventDefault(); setDragging(false); onFiles(e.dataTransfer.files) }}
      className={cn("rounded-lg border bg-card p-3 transition-colors", dragging && "border-ring bg-accent/40 ring-[3px] ring-ring/25")}
    >
      <input ref={ref} type="file" multiple className="hidden" onChange={(e) => { onFiles(e.target.files); e.target.value = "" }} />
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="flex min-w-0 flex-1 items-start gap-3">
          <div className="rounded-md border bg-background p-2 text-muted-foreground"><Upload className="size-4" /></div>
          <div className="min-w-0">
            <div className="text-sm font-medium">{upload.isPending ? "Uploading…" : dragging ? "Drop to index files" : "Drop files here or click to upload"}</div>
            <p className="mt-0.5 text-xs text-muted-foreground">Text and PDF files are added to personal document RAG.</p>
          </div>
        </div>
        <Button size="sm" disabled={upload.isPending} onClick={() => ref.current?.click()}>
          <Upload className="size-4" />Choose files
        </Button>
      </div>
      {msg && <p className="mt-2 text-xs text-muted-foreground">{msg}</p>}
    </div>
  )
}

export function PersonalRoute() {
  const { data, isLoading } = usePersonalIndex()
  const { reload, removeDirectory, removeFile } = usePersonalMutations()
  const files = data?.files || []
  const directories = data?.directories || []

  return (
    <div className="mx-auto flex h-full w-full max-w-4xl flex-col">
      <header className="flex h-13 shrink-0 items-center gap-2 border-b px-4 text-sm font-semibold">
        <FolderOpen className="size-4" />Personal files
      </header>
      <div className="flex-1 space-y-6 overflow-y-auto p-4">
        <div className="space-y-2">
          <UploadDropZone />
          <Button variant="outline" size="sm" disabled={reload.isPending} onClick={() => reload.mutate()}>
            <RefreshCw className={cn("size-4", reload.isPending && "animate-spin")} />Reindex
          </Button>
        </div>

        {data && !data.ok && (
          <p className="rounded-lg border bg-card p-3 text-sm text-muted-foreground">
            Personal files are unavailable (admin only, or RAG not configured).
          </p>
        )}

        <section>
          <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            <FolderInput className="size-3.5" />Indexed directories
          </h2>
          <AddDirectoryForm onPick={() => undefined} />
          {directories.length > 0 && (
            <div className="mt-2 divide-y rounded-lg border bg-card">
              {directories.map((d) => (
                <div key={d} className="flex items-center gap-3 px-3 py-2.5">
                  <Folder className="size-4 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate font-mono text-sm">{d}</span>
                  <button
                    onClick={() => { if (confirm(`Remove ${d} from the index?`)) removeDirectory.mutate(d) }}
                    title="Remove directory"
                    className="shrink-0 rounded-md p-1.5 text-muted-foreground hover:text-destructive"
                  ><Trash2 className="size-4" /></button>
                </div>
              ))}
            </div>
          )}
        </section>

        <section>
          <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            <FileText className="size-3.5" />Indexed files <span className="normal-case text-muted-foreground/70">· {files.length}</span>
          </h2>
          {isLoading ? <p className="text-sm text-muted-foreground">Loading…</p>
            : files.length === 0 ? <p className="rounded-lg border bg-card p-3 text-sm text-muted-foreground">No personal files indexed yet.</p>
            : (
              <div className="divide-y rounded-lg border bg-card">
                {files.map((f) => (
                  <div key={f.path || f.name} className="flex items-center gap-3 px-3 py-2.5">
                    <FileText className="size-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{f.name}</div>
                      {f.path && <div className="truncate font-mono text-xs text-muted-foreground">{f.path}</div>}
                    </div>
                    <span className="shrink-0 text-xs text-muted-foreground">{fmtSize(f.size)}</span>
                    <button
                      onClick={() => { if (f.path && confirm(`Remove ${f.name}?`)) removeFile.mutate(f.path) }}
                      title="Remove file"
                      disabled={!f.path}
                      className="shrink-0 rounded-md p-1.5 text-muted-foreground hover:text-destructive disabled:opacity-40"
                    ><Trash2 className="size-4" /></button>
                  </div>
                ))}
              </div>
            )}
        </section>
      </div>
    </div>
  )
}
