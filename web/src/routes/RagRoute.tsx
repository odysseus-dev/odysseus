import { useRef, useState } from "react"
import { Database, Upload, Trash2, FileText, RefreshCw, Cpu, Download, Check, Plug } from "lucide-react"
import {
  useRagStats, useRagDocuments, useEmbeddingModels, useEmbeddingEndpoint, useRagMutations,
  type RagFile, type EmbeddingModel,
} from "@/api/rag"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const inp = "h-9 rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"

function fmtSize(bytes?: number): string {
  if (bytes == null) return "—"
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="text-xs uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="mt-1 truncate text-sm font-medium" title={value}>{value}</div>
    </div>
  )
}

function StatsSection() {
  const { data: stats, isLoading } = useRagStats()
  return (
    <section>
      <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><Database className="size-3.5" />Knowledge base</h2>
      {isLoading ? <p className="text-sm text-muted-foreground">Loading stats…</p>
        : stats?.error || stats?.healthy === false ? <p className="rounded-lg border bg-card p-3 text-sm text-muted-foreground">RAG system not available{stats?.error ? ` — ${stats.error}` : ""}.</p>
        : (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <StatCard label="Documents" value={stats?.document_count != null ? String(stats.document_count) : "—"} />
            <StatCard label="Embedding model" value={stats?.embedding_model || "—"} />
            <StatCard label="Collection" value={stats?.collection_name || "—"} />
            <StatCard label="Lanes" value={String(stats?.embedding_lanes?.length ?? 0)} />
          </div>
        )}
    </section>
  )
}

function UploadBar() {
  const { upload, reload } = useRagMutations()
  const fileRef = useRef<HTMLInputElement>(null)
  const [msg, setMsg] = useState("")
  const [dragging, setDragging] = useState(false)
  const onFiles = (input: FileList | File[] | null) => {
    const files = Array.from(input || [])
    if (!files.length) return
    setMsg("")
    upload.mutate(files, {
      onSuccess: (r) => setMsg(`Indexed ${r.indexed_count ?? 0} chunk${r.indexed_count === 1 ? "" : "s"} from ${r.uploaded?.length ?? files.length} file${(r.uploaded?.length ?? files.length) === 1 ? "" : "s"}${r.failed_count ? `, ${r.failed_count} failed` : ""}.`),
      onError: (err) => setMsg(err instanceof Error ? err.message : "Upload failed"),
    })
    if (fileRef.current) fileRef.current.value = ""
  }
  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false) }}
      onDrop={(e) => { e.preventDefault(); setDragging(false); onFiles(e.dataTransfer.files) }}
      className={cn("space-y-2 rounded-lg border bg-card p-3 transition-colors", dragging && "border-ring bg-accent/40 ring-[3px] ring-ring/25")}
    >
      <input ref={fileRef} type="file" multiple accept=".txt,.md,.pdf,.json,.csv" onChange={(e) => onFiles(e.target.files)} className="hidden" />
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="flex min-w-0 flex-1 items-start gap-3">
          <div className="rounded-md border bg-background p-2 text-muted-foreground"><Upload className="size-4" /></div>
          <div className="min-w-0">
            <div className="text-sm font-medium">{upload.isPending ? "Uploading…" : dragging ? "Drop to index documents" : "Drop files here or click to upload"}</div>
            <p className="mt-0.5 text-xs text-muted-foreground">Text and PDF files are chunked and embedded into the knowledge base.</p>
          </div>
        </div>
        <Button size="sm" disabled={upload.isPending} onClick={() => fileRef.current?.click()}><Upload className="size-4" />Choose files</Button>
        <Button size="sm" variant="outline" disabled={reload.isPending} onClick={() => reload.mutate()}><RefreshCw className={cn("size-4", reload.isPending && "animate-spin")} />Reload index</Button>
      </div>
      {msg && <p className="text-xs text-muted-foreground">{msg}</p>}
    </div>
  )
}

function DocRow({ doc }: { doc: RagFile }) {
  const { removeFile } = useRagMutations()
  const target = doc.path || doc.name
  return (
    <div className="group flex items-center gap-3 px-3 py-2.5">
      <FileText className="size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{doc.name}</div>
        {doc.path && <div className="truncate text-xs text-muted-foreground">{doc.path}</div>}
      </div>
      <span className="shrink-0 text-xs text-muted-foreground">{fmtSize(doc.size)}</span>
      <button
        onClick={() => { if (confirm(`Remove ${doc.name} from the knowledge base?`)) removeFile.mutate(target) }}
        title="Remove" className="shrink-0 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
      ><Trash2 className="size-4" /></button>
    </div>
  )
}

function DocumentsSection() {
  const { data, isLoading } = useRagDocuments()
  const files = data?.files || []
  const dirs = data?.directories || []
  return (
    <section>
      <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><FileText className="size-3.5" />Indexed documents <span className="normal-case text-muted-foreground/70">· {files.length}</span></h2>
      <UploadBar />
      <div className="mt-2">
        {isLoading ? <p className="text-sm text-muted-foreground">Loading documents…</p>
          : !data?.ok ? <p className="rounded-lg border bg-card p-3 text-sm text-muted-foreground">Document list unavailable (admin only).</p>
          : files.length === 0 ? <p className="rounded-lg border bg-card p-3 text-sm text-muted-foreground">No documents indexed yet.</p>
          : <div className="divide-y rounded-lg border bg-card">{files.map((f) => <DocRow key={f.path || f.name} doc={f} />)}</div>}
        {dirs.length > 0 && (
          <div className="mt-2 text-xs text-muted-foreground">Indexed directories: {dirs.join(", ")}</div>
        )}
      </div>
    </section>
  )
}

function ModelRow({ m }: { m: EmbeddingModel }) {
  const { downloadModel, deleteModel } = useRagMutations()
  return (
    <div className="flex items-center gap-3 px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">{m.model}</span>
          {m.active && <span className="shrink-0 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] text-emerald-600 dark:text-emerald-400">active</span>}
          {m.recommended && !m.active && <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">recommended</span>}
        </div>
        <div className="flex flex-wrap gap-1.5 text-xs text-muted-foreground">
          {m.dim != null && <span>{m.dim}d</span>}
          {m.size_gb != null && <span>· {m.size_gb} GB</span>}
          {m.cached_size_mb != null && <span>· {m.cached_size_mb} MB cached</span>}
          {m.description && <span className="truncate">· {m.description}</span>}
        </div>
      </div>
      {m.downloading ? <span className="shrink-0 rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] text-amber-600 dark:text-amber-400">downloading…</span>
        : m.downloaded ? (
          <span className="flex shrink-0 items-center gap-1.5">
            <span className="flex items-center gap-1 text-xs text-muted-foreground"><Check className="size-3.5" />downloaded</span>
            {!m.active && <button onClick={() => { if (confirm(`Delete cached model ${m.model}?`)) deleteModel.mutate(m.model) }} title="Delete cache" className="text-muted-foreground hover:text-destructive"><Trash2 className="size-4" /></button>}
          </span>
        ) : (
          <Button size="sm" variant="outline" disabled={downloadModel.isPending} onClick={() => downloadModel.mutate(m.model)}><Download className="size-4" />Download</Button>
        )}
    </div>
  )
}

function ModelsSection() {
  const { data, isLoading } = useEmbeddingModels()
  const models = data?.models || []
  return (
    <section>
      <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><Cpu className="size-3.5" />Embedding models</h2>
      {isLoading ? <p className="text-sm text-muted-foreground">Loading models…</p>
        : data && !data.admin ? <p className="rounded-lg border bg-card p-3 text-sm text-muted-foreground">Embedding model management is admin only.</p>
        : data && !data.available ? <p className="rounded-lg border bg-card p-3 text-sm text-muted-foreground">fastembed is not installed.</p>
        : models.length === 0 ? <p className="rounded-lg border bg-card p-3 text-sm text-muted-foreground">No embedding models available.</p>
        : <div className="divide-y rounded-lg border bg-card">{models.map((m) => <ModelRow key={m.model} m={m} />)}</div>}
    </section>
  )
}

function EndpointSection() {
  const { data } = useEmbeddingEndpoint()
  const { setEndpoint, clearEndpoint } = useRagMutations()
  const [url, setUrl] = useState("")
  const [model, setModel] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [msg, setMsg] = useState("")
  const save = () => {
    if (!url.trim()) { setMsg("URL is required"); return }
    setMsg("")
    setEndpoint.mutate({ url: url.trim(), model: model.trim() || undefined, api_key: apiKey.trim() || undefined }, {
      onSuccess: () => { setMsg("Custom endpoint saved."); setUrl(""); setModel(""); setApiKey("") },
      onError: (e) => setMsg(e instanceof Error ? e.message : "Save failed"),
    })
  }
  return (
    <section>
      <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><Plug className="size-3.5" />Custom embedding endpoint</h2>
      <div className="space-y-2 rounded-lg border bg-card p-3">
        {data?.active && (
          <div className="flex items-center justify-between gap-2 rounded-md bg-muted px-3 py-2 text-xs">
            <span className="min-w-0 truncate text-muted-foreground">Active: {data.url}{data.model ? ` · ${data.model}` : ""}</span>
            <Button size="sm" variant="outline" disabled={clearEndpoint.isPending} onClick={() => { clearEndpoint.mutate(undefined, { onSuccess: () => setMsg("Reverted to local fastembed.") }) }}>Use local</Button>
          </div>
        )}
        <div className="flex flex-col gap-2 sm:flex-row">
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://host/v1/embeddings" className={cn(inp, "flex-1")} />
          <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="model (optional)" className={cn(inp, "sm:w-44")} />
        </div>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input value={apiKey} onChange={(e) => setApiKey(e.target.value)} type="password" placeholder="API key (optional)" className={cn(inp, "flex-1")} />
          <Button disabled={setEndpoint.isPending} onClick={save}>{setEndpoint.isPending ? "Saving…" : "Save endpoint"}</Button>
        </div>
        {msg && <p className="text-xs text-muted-foreground">{msg}</p>}
      </div>
    </section>
  )
}

export function RagRoute() {
  return (
    <div className="mx-auto flex h-full w-full max-w-4xl flex-col">
      <header className="flex h-13 shrink-0 items-center gap-2 border-b px-4 text-sm font-semibold"><Database className="size-4" />Knowledge base</header>
      <div className="flex-1 space-y-6 overflow-y-auto p-4">
        <StatsSection />
        <DocumentsSection />
        <ModelsSection />
        <EndpointSection />
      </div>
    </div>
  )
}
