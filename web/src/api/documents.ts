import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { DocItem } from "@/types"

export type DocumentSort = "recent" | "oldest" | "alpha" | "edits"
export interface DocumentLibraryOptions {
  search?: string
  language?: string | null
  sort?: DocumentSort
  archived?: boolean
  limit?: number
  offset?: number
}
export interface DocumentLibraryResponse {
  documents: DocItem[]
  total: number
  languages: Record<string, number>
  session_count?: number
}

function documentLibraryQuery(opts: DocumentLibraryOptions = {}) {
  const params = new URLSearchParams({
    limit: String(opts.limit ?? 50),
    offset: String(opts.offset ?? 0),
    sort: opts.sort || "recent",
  })
  if (opts.search?.trim()) params.set("search", opts.search.trim())
  if (opts.language) params.set("language", opts.language)
  if (opts.archived) params.set("archived", "true")
  return params.toString()
}

export async function fetchDocumentLibrary(opts: DocumentLibraryOptions = {}): Promise<DocumentLibraryResponse> {
  const r = await apiJson<Partial<DocumentLibraryResponse> & { items?: DocItem[] }>(`/api/documents/library?${documentLibraryQuery(opts)}`)
  return {
    documents: r.documents || r.items || [],
    total: r.total ?? (r.documents || r.items || []).length,
    languages: r.languages || {},
    session_count: r.session_count,
  }
}

export function useDocuments(opts: DocumentLibraryOptions = {}) {
  return useQuery({
    queryKey: ["documents", {
      search: opts.search || "",
      language: opts.language || "",
      sort: opts.sort || "recent",
      archived: !!opts.archived,
      limit: opts.limit ?? 50,
      offset: opts.offset ?? 0,
    }],
    queryFn: () => fetchDocumentLibrary(opts),
  })
}

// Documents/artifacts/files belonging to a specific chat thread.
export function useSessionDocuments(sid?: string) {
  return useQuery({
    queryKey: ["session-documents", sid],
    enabled: !!sid,
    queryFn: async () => {
      const r = await apiJson<DocItem[] | { documents?: DocItem[]; items?: DocItem[] }>(`/api/documents/${sid}`)
      return Array.isArray(r) ? r : (r.documents || r.items || [])
    },
  })
}

export interface DocFull {
  id: string; session_id?: string | null; title?: string; language?: string; current_content?: string; version_count?: number;
  source_email_uid?: string | null; source_email_folder?: string | null; source_email_account_id?: string | null; source_email_message_id?: string | null;
}
export interface CreateDocumentInput { title: string; content?: string; language?: string; session_id?: string | null }
export interface SignedReplyAttachment { token: string; filename: string; size?: number }
export interface SignedReplyResult {
  ok?: boolean
  error?: string
  attachment?: SignedReplyAttachment
  reply?: {
    to?: string
    to_name?: string
    subject?: string
    in_reply_to?: string
    references?: string
    account_id?: string | null
    source_uid?: string
    source_folder?: string
    source_message_id?: string
  }
}
export function useDocument(id: string | null) {
  return useQuery({
    queryKey: ["document", id],
    enabled: !!id,
    queryFn: () => apiJson<DocFull>(`/api/document/${id}`),
  })
}

export interface DocVersion { id: string; version_number: number; content: string; summary?: string; source?: string; created_at?: string }
export function useDocVersions(id: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: ["doc-versions", id],
    enabled: !!id && enabled,
    queryFn: () => apiJson<DocVersion[]>(`/api/document/${id}/versions`),
  })
}

export function useDocMutations() {
  const qc = useQueryClient()
  const invDoc = (id?: string) => {
    if (id) {
      qc.invalidateQueries({ queryKey: ["document", id] })
      qc.invalidateQueries({ queryKey: ["doc-versions", id] })
    }
    qc.invalidateQueries({ queryKey: ["documents"] })
    qc.invalidateQueries({ queryKey: ["session-documents"] })
  }
  return {
    update: useMutation({
      mutationFn: async (v: { id: string; content: string }) => {
        const r = await apiFetch(`/api/document/${v.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: v.content, summary: "Manual edit (v2)" }),
        })
        if (!r.ok) throw new Error("save failed"); return r.json()
      },
      onSuccess: (_d, v) => {
        invDoc(v.id)
      },
      meta: { silent: true },
    }),
    restore: useMutation({
      mutationFn: async (v: { id: string; num: number }) => {
        const r = await apiFetch(`/api/document/${v.id}/restore/${v.num}`, { method: "POST" })
        if (!r.ok) throw new Error("restore failed"); return r.json() as Promise<DocFull>
      },
      onSuccess: (_d, v) => {
        invDoc(v.id)
      },
      meta: { silent: true },
    }),
    remove: useMutation({
      mutationFn: async (id: string) => { const r = await apiFetch(`/api/document/${id}`, { method: "DELETE" }); if (!r.ok) throw new Error("delete failed") },
      onSuccess: () => invDoc(),
    }),
    create: useMutation({
      mutationFn: async (v: CreateDocumentInput) => {
        const r = await apiFetch("/api/document", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: v.title, content: v.content || "", language: v.language, session_id: v.session_id }) })
        if (!r.ok) throw new Error("create failed"); return r.json() as Promise<DocFull>
      },
      onSuccess: () => invDoc(),
    }),
    rename: useMutation({
      mutationFn: async (v: { id: string; title: string }) => {
        const r = await apiFetch(`/api/document/${v.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: v.title }) })
        if (!r.ok) throw new Error("rename failed"); return r.json()
      },
      onSuccess: (_d, v) => invDoc(v.id),
    }),
    patchMeta: useMutation({
      mutationFn: async (v: { id: string; title?: string; language?: string; session_id?: string | null }) => {
        const { id, ...body } = v
        const r = await apiFetch(`/api/document/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
        if (!r.ok) throw new Error("update failed"); return r.json() as Promise<DocFull>
      },
      onSuccess: (_d, v) => invDoc(v.id),
    }),
    archive: useMutation({
      mutationFn: async (v: { id: string; archived: boolean }) => {
        const r = await apiFetch(`/api/document/${v.id}/archive?archived=${v.archived}`, { method: "POST" })
        if (!r.ok) throw new Error("archive failed")
      },
      onSuccess: (_d, v) => invDoc(v.id),
    }),
  }
}

export async function prepareSignedReply(docId: string): Promise<SignedReplyResult> {
  const r = await apiFetch(`/api/document/${docId}/prepare-signed-reply`, { method: "POST" })
  const data = await r.json().catch(() => ({ ok: false, error: `HTTP ${r.status}` }))
  if (!r.ok || data.ok === false) throw new Error(data.error || `HTTP ${r.status}`)
  return data
}

const EXT_BY_LANGUAGE: Record<string, string> = {
  javascript: ".js", python: ".py", html: ".html", css: ".css", markdown: ".md",
  json: ".json", yaml: ".yml", bash: ".sh", sql: ".sql", rust: ".rs", go: ".go",
  java: ".java", c: ".c", cpp: ".cpp", typescript: ".ts", ruby: ".rb", php: ".php",
  text: ".txt", xml: ".xml", toml: ".toml", ini: ".ini", csv: ".csv", svg: ".svg",
}

function safeFilename(title: string, language?: string) {
  const base = (title || "document").replace(/[\\/:*?"<>|]+/g, "").trim() || "document"
  if (/\.[a-z0-9]{1,8}$/i.test(base)) return base
  return `${base}${EXT_BY_LANGUAGE[(language || "text").toLowerCase()] || ".txt"}`
}

export async function downloadDocument(doc: DocItem | DocFull) {
  const full = "current_content" in doc ? doc : await apiJson<DocFull>(`/api/document/${doc.id}`)
  const blob = new Blob([full.current_content || ""], { type: "text/plain;charset=utf-8" })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = safeFilename(full.title || "document", full.language)
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export async function downloadDocumentsZip(ids: string[]) {
  const r = await apiFetch("/api/documents/export-zip", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids }),
  })
  if (!r.ok) throw new Error("zip failed")
  const blob = await r.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = "documents.zip"
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function copyTitle(title?: string) {
  const base = (title || "Untitled").replace(/\s+\(copy\)$/i, "").trim() || "Untitled"
  return `${base} (copy)`
}

export async function cloneDocument(doc: DocItem | DocFull, sessionId?: string | null): Promise<DocFull> {
  const full = "current_content" in doc ? doc : await apiJson<DocFull>(`/api/document/${doc.id}`)
  const r = await apiFetch("/api/document", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: copyTitle(full.title || doc.title || ("name" in doc ? doc.name : "") || "Untitled"),
      content: full.current_content || "",
      language: full.language || doc.language || "markdown",
      session_id: sessionId || null,
    }),
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data.detail || data.error || "clone failed")
  return data as DocFull
}

export async function importPdfDocument(file: File): Promise<DocFull> {
  const fd = new FormData()
  fd.append("file", file)
  const r = await apiFetch("/api/documents/import-pdf", { method: "POST", body: fd })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data.detail || data.error || "PDF import failed")
  return data as DocFull
}

// Detect a PDF-form-backed document by its source marker. Kept local to the api
// layer so the backend export-pdf endpoint (which only accepts source-linked
// docs) is only hit for docs it can actually serve.
const PDF_SOURCE_MARKER = /<!--\s*pdf_(?:form_)?source\s+upload_id="[^"]+"/

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
}

// Render arbitrary document content to PDF via the browser's print dialog
// ("Save as PDF"). The backend export-pdf endpoint only works for docs linked
// to a source PDF form, so this is the path for plain text / markdown / code.
function printToPdf(title: string, content: string) {
  const win = window.open("", "_blank", "noopener,noreferrer,width=820,height=1060")
  if (!win) throw new Error("Pop-up blocked — allow pop-ups to export to PDF.")
  const safeTitle = escapeHtml(title || "Document")
  // Preserve whitespace and line breaks; the content is shown as monospace
  // source text (this is a markdown/source editor, not a rendered preview).
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>${safeTitle}</title>` +
    `<style>` +
    `@page{margin:18mm}` +
    `body{font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#111;margin:0}` +
    `h1{font:600 18px/1.4 system-ui,sans-serif;margin:0 0 16px}` +
    `pre{white-space:pre-wrap;word-wrap:break-word;margin:0}` +
    `</style></head><body>` +
    `<h1>${safeTitle}</h1><pre>${escapeHtml(content)}</pre>` +
    `<script>window.onload=function(){setTimeout(function(){window.print()},120)}</script>` +
    `</body></html>`
  win.document.open()
  win.document.write(html)
  win.document.close()
}

// Export any document to PDF. PDF-form-backed docs stream the filled PDF from
// the backend; all other docs print-to-PDF client-side.
export async function exportDocumentPdf(doc: DocItem | DocFull) {
  const full = "current_content" in doc ? (doc as DocFull) : await apiJson<DocFull>(`/api/document/${doc.id}`)
  const content = full.current_content || ""
  const title = full.title || ("name" in doc ? (doc as DocItem).name : "") || "Document"
  if (PDF_SOURCE_MARKER.test(content)) {
    const r = await apiFetch(`/api/document/${full.id || doc.id}/export-pdf`)
    if (!r.ok) throw new Error("PDF export failed")
    const blob = await r.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `${(title || "document").replace(/[\\/:*?"<>|]+/g, "").trim() || "document"}.pdf`
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    return
  }
  printToPdf(title, content)
}

export interface TidyResult { deleted?: number; reviewed?: number; remaining?: number; fixed_titles?: number; message?: string }

// Library cleanup mutations. `tidy` removes empty/junk/duplicate docs by rule;
// `aiTidy` asks the model to judge junk-vs-keep in batches (call repeatedly
// while `remaining > 0` to work through the whole library).
export function useTidyMutations() {
  const qc = useQueryClient()
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["documents"] })
    qc.invalidateQueries({ queryKey: ["session-documents"] })
  }
  return {
    tidy: useMutation({
      mutationFn: async () => {
        const r = await apiFetch("/api/documents/tidy", { method: "POST" })
        if (!r.ok) throw new Error("Tidy failed")
        return r.json() as Promise<TidyResult>
      },
      onSuccess: invalidate,
      meta: { silent: true },
    }),
    aiTidy: useMutation({
      mutationFn: async () => {
        const r = await apiFetch("/api/documents/ai-tidy", { method: "POST" })
        if (!r.ok) {
          const data = await r.json().catch(() => ({}))
          throw new Error(data.detail || data.error || "AI tidy failed")
        }
        return r.json() as Promise<TidyResult>
      },
      onSuccess: invalidate,
      meta: { silent: true },
    }),
  }
}
