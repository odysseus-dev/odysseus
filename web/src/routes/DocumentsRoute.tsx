import { useEffect, useMemo, useRef, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import {
  Archive,
  ArrowLeft,
  CheckCheck,
  Code2,
  Copy,
  Download,
  Eye,
  ExternalLink,
  FileDown,
  FileText,
  History,
  MoreHorizontal,
  Plus,
  Reply,
  RotateCcw,
  Save,
  Search,
  Sparkles,
  Trash2,
  Upload,
  X,
} from "lucide-react"
import {
  cloneDocument,
  downloadDocument,
  downloadDocumentsZip,
  exportDocumentPdf,
  fetchDocumentLibrary,
  importPdfDocument,
  prepareSignedReply,
  useDocument,
  useDocuments,
  useDocMutations,
  useDocVersions,
  useTidyMutations,
  type DocumentSort,
} from "@/api/documents"
import { useSessions } from "@/api/sessions"
import { MarkdownToolbar } from "@/components/documents/MarkdownToolbar"
import { PdfDocumentEditor } from "@/components/documents/PdfDocumentEditor"
import { EmailDraftEditor } from "@/components/email/EmailDraftEditor"
import { HtmlPreview } from "@/components/ui/HtmlPreview"
import { detectRenderLang } from "@/lib/artifact"
import { buildEmailDraft } from "@/lib/emailDraft"
import { readImportedDocuments } from "@/lib/documentImport"
import { isPdfBackedDocument } from "@/lib/pdfDocument"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { DocItem } from "@/types"

const segBtn = (active: boolean) =>
  cn("flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors",
    active ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")

const LANGUAGE_OPTIONS = [
  "markdown", "text", "html", "svg", "css", "javascript", "typescript", "python",
  "json", "yaml", "csv", "bash", "sql", "xml", "toml", "ini", "rust", "go",
  "java", "c", "cpp", "ruby", "php", "email",
]
const LIBRARY_PAGE_SIZE = 50
const EMPTY_LANGUAGES: Record<string, number> = {}
const EMPTY_DOCUMENTS: DocItem[] = []

const LAST_CHAT_SESSION_KEY = "odysseus-last-chat-session"

function rememberedSessionId(): string {
  if (typeof window === "undefined") return ""
  return window.localStorage.getItem(LAST_CHAT_SESSION_KEY) || ""
}

function formatTime(value?: string): string {
  if (!value) return ""
  const then = new Date(value).getTime()
  if (Number.isNaN(then)) return ""
  const diff = Math.max(0, Date.now() - then)
  const min = Math.floor(diff / 60000)
  if (min < 1) return "just now"
  if (min < 60) return `${min}m ago`
  const hours = Math.floor(min / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days === 1) return "yesterday"
  if (days < 14) return `${days}d ago`
  return new Date(value).toLocaleDateString()
}

function shortPreview(text?: string): string {
  return (text || "").replace(/\s+/g, " ").trim()
}

function Editor({ id, onBack, onOpen }: { id: string; onBack: () => void; onOpen: (id: string) => void }) {
  const { data, isLoading } = useDocument(id)
  const { data: versions } = useDocVersions(id)
  const { update, remove, create, patchMeta, restore } = useDocMutations()
  const [content, setContent] = useState("")
  const [title, setTitle] = useState("")
  const [language, setLanguage] = useState("markdown")
  const [dirty, setDirty] = useState(false)
  const [view, setView] = useState<"preview" | "code">("preview")
  const [showVersions, setShowVersions] = useState(false)
  const [previewVersion, setPreviewVersion] = useState<number | null>(null)
  const [signedBusy, setSignedBusy] = useState(false)
  const [signedErr, setSignedErr] = useState("")
  const [notice, setNotice] = useState("")
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const navigate = useNavigate()

  useEffect(() => {
    if (!data) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- seed editor controls from the loaded document record
    setContent(data.current_content || "")
    setTitle(data.title || "Untitled")
    setLanguage(data.language || "markdown")
    setDirty(false)
    setPreviewVersion(null)
    setNotice("")
  }, [data])

  const renderLang = detectRenderLang(content, language, title)
  const renderable = !!renderLang
  const showPreview = renderable && view === "preview"
  const canSignedReply = !!(data?.source_email_uid && data?.source_email_folder)
  const save = async () => {
    const saved = await update.mutateAsync({ id, content })
    setDirty(false)
    setPreviewVersion(null)
    setNotice(`Saved v${saved.version_count || data?.version_count || ""}`.trim())
  }
  const saveTitle = () => {
    const next = title.trim() || "Untitled"
    if (next === (data?.title || "Untitled")) return
    patchMeta.mutate({ id, title: next })
  }
  // Selection-based AI editing lives in the chat ContextPanel (the only surface
  // with a composer to type the instruction). Library docs reach it by opening
  // in chat: the source session if the doc came from one, else the last chat.
  // The ?doc= handler (ChatConsole) loads the doc into the editable panel.
  const aiTarget = data?.session_id || rememberedSessionId()
  const editWithAI = async () => {
    if (!aiTarget) return
    if (dirty) { try { await save() } catch { /* still open the (last-saved) doc */ } }
    window.localStorage.setItem(LAST_CHAT_SESSION_KEY, aiTarget)
    navigate(`/chat/${encodeURIComponent(aiTarget)}?doc=${encodeURIComponent(id)}`)
  }
  const changeLanguage = (next: string) => {
    setLanguage(next)
    patchMeta.mutate({ id, language: next })
  }
  const del = () => { if (confirm("Delete this document?")) remove.mutate(id, { onSuccess: onBack }) }
  const exportPdf = async () => {
    setSignedErr("")
    setNotice("")
    try {
      await exportDocumentPdf({ id, title, language, current_content: content })
      setNotice("Opened print dialog for PDF export.")
    } catch (e) {
      setSignedErr(e instanceof Error ? e.message : "Couldn't export to PDF.")
    }
  }
  const previewOldVersion = (num: number, oldContent: string) => {
    if (dirty && !confirm("Discard unsaved edits and preview this version?")) return
    setPreviewVersion(num)
    setContent(oldContent)
    setDirty(false)
  }
  const returnToLatest = () => {
    setPreviewVersion(null)
    setContent(data?.current_content || "")
    setDirty(false)
  }
  const restoreVersion = async (num: number) => {
    const restored = await restore.mutateAsync({ id, num })
    setContent(restored.current_content || "")
    setTitle(restored.title || title)
    setLanguage(restored.language || language)
    setPreviewVersion(null)
    setDirty(false)
    setNotice(`Restored v${num}`)
  }
  const signedReply = async () => {
    if (!data || signedBusy) return
    setSignedBusy(true)
    setSignedErr("")
    try {
      if (dirty) await save()
      const result = await prepareSignedReply(id)
      if (!result.attachment) throw new Error("Signed attachment was not returned")
      const reply = result.reply || {}
      const firstName = (reply.to_name || "").trim().split(/\s+/)[0]
      const body = `Hi${firstName ? ` ${firstName}` : ""},\n\nPlease find the signed copy attached.\n\nBest,\n`
      const draft = buildEmailDraft({
        to: reply.to || "",
        cc: "",
        bcc: "",
        subject: reply.subject || data.title || "Signed reply",
        inReplyTo: reply.in_reply_to || "",
        references: reply.references || "",
        sourceUid: reply.source_uid || data.source_email_uid || "",
        sourceFolder: reply.source_folder || data.source_email_folder || "",
        sourceAccount: reply.account_id || data.source_email_account_id || "",
        attachments: [result.attachment],
        body,
      })
      const created = await create.mutateAsync({
        session_id: data.session_id || null,
        title: reply.subject || "Signed reply",
        language: "email",
        content: draft,
      })
      if (created?.id) onOpen(created.id)
    } catch (e) {
      setSignedErr(e instanceof Error ? e.message : "Couldn't prepare signed reply.")
    } finally {
      setSignedBusy(false)
    }
  }
  if (!isLoading && data?.language === "email") {
    return <EmailDraftEditor key={id} docId={id} title={data.title} content={data.current_content || ""} onBack={onBack} />
  }
  if (!isLoading && data && isPdfBackedDocument(data.current_content || "")) {
    return <PdfDocumentEditor key={id} doc={data} content={data.current_content || ""} onBack={onBack} onOpen={onOpen} />
  }
  return (
    <div className="flex h-full flex-col">
      <header className="flex min-h-13 shrink-0 flex-wrap items-center gap-2 border-b px-3 py-1.5 sm:flex-nowrap sm:py-0">
        <Button variant="ghost" size="icon" onClick={onBack} title="Back"><ArrowLeft className="size-4" /></Button>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={saveTitle}
          onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur() }}
          className="min-w-0 flex-1 rounded-md border border-transparent bg-transparent px-2 py-1 text-sm font-semibold outline-none hover:border-border focus-visible:border-ring"
          aria-label="Document title"
        />
        <select value={language} onChange={(e) => changeLanguage(e.target.value)} className="h-8 rounded-md border bg-background px-2 text-xs outline-none focus-visible:border-ring" aria-label="Document language">
          {LANGUAGE_OPTIONS.map((lang) => <option key={lang} value={lang}>{lang}</option>)}
        </select>
        {renderable && (
          <div className="mr-1 flex rounded-lg bg-muted p-0.5">
            <button onClick={() => setView("preview")} className={segBtn(view === "preview")}><Eye className="size-3.5" />Preview</button>
            <button onClick={() => setView("code")} className={segBtn(view === "code")}><Code2 className="size-3.5" />Code</button>
          </div>
        )}
        <button onClick={() => setShowVersions((open) => !open)} title="Version history" className={cn("hidden rounded-md p-1.5 hover:bg-accent sm:inline-flex", showVersions ? "text-foreground" : "text-muted-foreground hover:text-foreground")}>
          <History className="size-4" />
        </button>
        {canSignedReply && <Button variant="outline" size="sm" disabled={signedBusy} onClick={signedReply} className="hidden sm:inline-flex"><Reply className="size-4" />{signedBusy ? "Preparing..." : "Signed reply"}</Button>}
        {aiTarget && <button onClick={() => void editWithAI()} title="Edit with AI — opens in chat; select text and ask for a rewrite" className="hidden rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground sm:inline-flex"><Sparkles className="size-4" /></button>}
        <button onClick={() => downloadDocument({ id, title, language, current_content: content })} title="Download" className="hidden rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground sm:inline-flex"><Download className="size-4" /></button>
        <button onClick={() => void exportPdf()} title="Export to PDF" className="hidden rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground sm:inline-flex"><FileDown className="size-4" /></button>
        <button onClick={del} title="Delete" className="hidden rounded-md p-1.5 text-muted-foreground hover:text-destructive sm:inline-flex"><Trash2 className="size-4" /></button>
        <Button size="sm" onClick={save} disabled={!dirty || update.isPending || previewVersion != null}><Save className="size-4" />{update.isPending ? "Saving..." : dirty ? "Save" : "Saved"}</Button>
      </header>
      {(signedErr || notice || previewVersion) && (
        <div className={cn("flex shrink-0 items-center gap-2 border-b px-4 py-2 text-xs", signedErr ? "text-destructive" : "text-muted-foreground")}>
          <span className="min-w-0 flex-1">{signedErr || (previewVersion ? `Previewing v${previewVersion}` : notice)}</span>
          {previewVersion && <Button size="sm" variant="outline" onClick={() => restoreVersion(previewVersion)}><RotateCcw className="size-3.5" />Restore</Button>}
          {previewVersion && <button onClick={returnToLatest} title="Return to latest" className="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground"><X className="size-3.5" /></button>}
        </div>
      )}
      {showVersions && (
        <section className="max-h-56 shrink-0 overflow-y-auto border-b bg-muted/20 px-4 py-3">
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            <History className="size-3.5" />
            <span>Version history</span>
          </div>
          <div className="space-y-1.5">
            {(versions || []).map((version, index) => (
              <div key={version.id || version.version_number} className={cn("flex items-center gap-2 rounded-md border bg-background/70 px-3 py-2 text-sm", previewVersion === version.version_number && "border-foreground")}>
                <button type="button" onClick={() => previewOldVersion(version.version_number, version.content || "")} className="min-w-0 flex-1 text-left">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">v{version.version_number}</span>
                    {index === 0 && <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">latest</span>}
                    {version.source && index !== 0 && <span className="text-xs text-muted-foreground">{version.source}</span>}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">{version.summary || formatTime(version.created_at)}</div>
                </button>
                {index !== 0 && <Button size="sm" variant="ghost" onClick={() => restoreVersion(version.version_number)}><RotateCcw className="size-3.5" />Restore</Button>}
              </div>
            ))}
            {(versions || []).length === 0 && <p className="text-sm text-muted-foreground">No saved versions yet.</p>}
          </div>
        </section>
      )}
      {isLoading ? <div className="p-6 text-sm text-muted-foreground">Loading...</div> : showPreview ? (
        <HtmlPreview content={content} renderLang={renderLang} title={title} />
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          <MarkdownToolbar
            textareaRef={textareaRef}
            value={content}
            onChange={(next) => { setContent(next); setDirty(true); setPreviewVersion(null) }}
            disabled={previewVersion != null}
          />
          <textarea
            ref={textareaRef}
            value={content}
            onChange={(e) => { setContent(e.target.value); setDirty(true); setPreviewVersion(null) }}
            spellCheck={false}
            className="min-h-0 flex-1 resize-none bg-transparent p-4 font-mono text-sm outline-none"
          />
        </div>
      )}
    </div>
  )
}

type LibraryBulkAction = "archive" | "delete" | "export" | "clone"

function DocumentRow({
  doc,
  selected,
  selectMode,
  archived,
  onOpen,
  onOpenSource,
  onClone,
  onExportPdf,
  onToggle,
  onStartSelect,
  onArchive,
  onDelete,
  cloneBusy,
}: {
  doc: DocItem
  selected: boolean
  selectMode: boolean
  archived: boolean
  onOpen: () => void
  onOpenSource: () => void
  onClone: () => void
  onExportPdf: () => void
  onToggle: () => void
  onStartSelect: () => void
  onArchive: () => void
  onDelete: () => void
  cloneBusy: boolean
}) {
  const preview = shortPreview(doc.preview)
  const [actionMenuOpen, setActionMenuOpen] = useState(false)
  const longPressTimerRef = useRef<number | null>(null)
  const longPressFiredRef = useRef(false)
  const row = () => {
    if (actionMenuOpen || longPressFiredRef.current) return
    if (selectMode) onToggle()
    else onOpen()
  }
  const clearLongPress = () => {
    if (longPressTimerRef.current == null) return
    window.clearTimeout(longPressTimerRef.current)
    longPressTimerRef.current = null
  }
  const openActionMenu = () => {
    if (selectMode) return
    clearLongPress()
    longPressFiredRef.current = true
    setActionMenuOpen(true)
    if (window.navigator.vibrate) window.navigator.vibrate(8)
  }
  const closeActionMenu = () => {
    clearLongPress()
    longPressFiredRef.current = false
    setActionMenuOpen(false)
  }
  const startLongPress = () => {
    if (selectMode) return
    clearLongPress()
    longPressTimerRef.current = window.setTimeout(openActionMenu, 500)
  }
  const runMenuAction = (action: () => void) => {
    closeActionMenu()
    action()
  }
  const menuItem = "flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
  useEffect(() => () => clearLongPress(), [])
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={row}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); row() } }}
      onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); openActionMenu() }}
      onTouchStart={startLongPress}
      onTouchMove={clearLongPress}
      onTouchEnd={clearLongPress}
      onTouchCancel={clearLongPress}
      className={cn("relative flex w-full cursor-pointer gap-3 rounded-md border bg-card p-3 text-left outline-none transition-colors hover:bg-accent/50 focus-visible:bg-accent/50", selected && "border-foreground bg-accent/60")}
    >
      {selectMode ? (
        <input type="checkbox" checked={selected} onClick={(e) => e.stopPropagation()} onChange={onToggle} className="mt-0.5 size-3.5 shrink-0 accent-current" aria-label={`Select ${doc.title || "document"}`} />
      ) : (
        <FileText className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-medium">{doc.title || doc.name || "Untitled"}</span>
          <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">v{doc.version_count || 1}</span>
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          {doc.session_name && <span className="max-w-48 truncate">{doc.session_name}</span>}
          {doc.language && <span>{doc.language}</span>}
          {doc.updated_at && <span>{formatTime(doc.updated_at)}</span>}
        </div>
        {preview && <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">{preview}</div>}
      </div>
      {!selectMode && (
        <div className="flex shrink-0 items-center gap-0.5">
          <button type="button" onClick={(e) => { e.stopPropagation(); openActionMenu() }} title="Actions" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground sm:hidden"><MoreHorizontal className="size-4" /></button>
          <div className="hidden items-center gap-0.5 sm:flex">
            {doc.session_id && <button type="button" onClick={(e) => { e.stopPropagation(); onOpenSource() }} title="Open in original chat" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><ExternalLink className="size-4" /></button>}
            <button type="button" onClick={(e) => { e.stopPropagation(); onClone() }} disabled={cloneBusy} title="Clone to selected chat" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"><Copy className="size-4" /></button>
            <button type="button" onClick={(e) => { e.stopPropagation(); void downloadDocument(doc) }} title="Download" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><Download className="size-4" /></button>
            <button type="button" onClick={(e) => { e.stopPropagation(); onExportPdf() }} title="Export to PDF" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><FileDown className="size-4" /></button>
            <button type="button" onClick={(e) => { e.stopPropagation(); onArchive() }} title={archived ? "Restore" : "Archive"} className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">{archived ? <RotateCcw className="size-4" /> : <Archive className="size-4" />}</button>
            <button type="button" onClick={(e) => { e.stopPropagation(); onDelete() }} title="Delete" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-destructive"><Trash2 className="size-4" /></button>
          </div>
        </div>
      )}
      {actionMenuOpen && (
        <>
          <div className="fixed inset-0 z-40 cursor-default bg-transparent" onClick={(e) => { e.stopPropagation(); closeActionMenu() }} onContextMenu={(e) => { e.preventDefault(); closeActionMenu() }} />
          <div
            role="menu"
            aria-label={`${doc.title || doc.name || "Document"} actions`}
            onClick={(e) => e.stopPropagation()}
            className="fixed inset-x-3 bottom-3 z-50 rounded-lg border bg-popover p-1 shadow-lg sm:absolute sm:bottom-auto sm:left-auto sm:right-3 sm:top-12 sm:w-56"
          >
            <button type="button" role="menuitem" className={menuItem} onClick={() => runMenuAction(doc.session_id ? onOpenSource : onOpen)}>
              {doc.session_id ? <ExternalLink className="size-4" /> : <FileText className="size-4" />}
              <span>{doc.session_id ? "Open original chat" : "Open document"}</span>
            </button>
            <button type="button" role="menuitem" disabled={cloneBusy} className={cn(menuItem, "disabled:opacity-50")} onClick={() => runMenuAction(onClone)}>
              <Copy className="size-4" />
              <span>Clone</span>
            </button>
            <button type="button" role="menuitem" className={menuItem} onClick={() => runMenuAction(() => { void downloadDocument(doc) })}>
              <Download className="size-4" />
              <span>Download</span>
            </button>
            <button type="button" role="menuitem" className={menuItem} onClick={() => runMenuAction(onExportPdf)}>
              <FileDown className="size-4" />
              <span>Export to PDF</span>
            </button>
            <button type="button" role="menuitem" className={menuItem} onClick={() => runMenuAction(onStartSelect)}>
              <CheckCheck className="size-4" />
              <span>Select</span>
            </button>
            <button type="button" role="menuitem" className={menuItem} onClick={() => runMenuAction(onArchive)}>
              {archived ? <RotateCcw className="size-4" /> : <Archive className="size-4" />}
              <span>{archived ? "Restore" : "Archive"}</span>
            </button>
            <button type="button" role="menuitem" className={cn(menuItem, "text-destructive hover:text-destructive")} onClick={() => runMenuAction(onDelete)}>
              <Trash2 className="size-4" />
              <span>Delete</span>
            </button>
          </div>
        </>
      )}
    </div>
  )
}

export function DocumentsRoute() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [query, setQuery] = useState("")
  const [sort, setSort] = useState<DocumentSort>("recent")
  const [language, setLanguage] = useState<string | null>(null)
  const [archived, setArchived] = useState(false)
  const [selectMode, setSelectMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set())
  const [notice, setNotice] = useState("")
  const [busy, setBusy] = useState("")
  const [cloneTargetSession, setCloneTargetSession] = useState<string | null>(() => searchParams.get("session") || rememberedSessionId() || null)
  const [loadedPage, setLoadedPage] = useState<{ key: string; documents: DocItem[] }>({ key: "", documents: [] })
  const fileInputRef = useRef<HTMLInputElement>(null)
  const { data: library, isFetching } = useDocuments({ search: query, sort, language, archived, limit: LIBRARY_PAGE_SIZE })
  const { data: sessions } = useSessions()
  const docActions = useDocMutations()
  const tidyActions = useTidyMutations()
  const [openId, setOpenIdState] = useState<string | null>(() => searchParams.get("doc"))
  const libraryKey = useMemo(() => [query.trim(), sort, language || "", archived ? "archived" : "active"].join("\u0000"), [query, sort, language, archived])
  const firstPage = library?.documents || EMPTY_DOCUMENTS
  const extraDocuments = loadedPage.key === libraryKey ? loadedPage.documents : EMPTY_DOCUMENTS
  const list = useMemo(() => {
    const seen = new Set<string>()
    const merged: DocItem[] = []
    for (const doc of [...firstPage, ...extraDocuments]) {
      if (seen.has(doc.id)) continue
      seen.add(doc.id)
      merged.push(doc)
    }
    return merged
  }, [firstPage, extraDocuments])
  const languages = library?.languages || EMPTY_LANGUAGES
  const activeSessions = useMemo(() => (sessions || []).filter((session) => !session.archived), [sessions])
  const cloneTargetSessionId = cloneTargetSession == null
    ? activeSessions[0]?.id || ""
    : (cloneTargetSession && !activeSessions.some((session) => session.id === cloneTargetSession)
        ? activeSessions[0]?.id || ""
        : cloneTargetSession)
  const cloneTarget = activeSessions.find((session) => session.id === cloneTargetSessionId)
  const cloneTargetLabel = cloneTarget?.name || (cloneTargetSessionId ? "selected chat" : "Library")
  const selectedVisibleIds = list.map((doc) => doc.id).filter((id) => selectedIds.has(id))
  const allVisibleSelected = list.length > 0 && selectedVisibleIds.length === list.length
  const languageEntries = useMemo(() => Object.entries(languages).sort((a, b) => b[1] - a[1]), [languages])
  const setOpenId = (id: string | null) => {
    setOpenIdState(id)
    if (id) setSearchParams({ doc: id })
    else setSearchParams({})
  }
  const clearSelection = () => {
    setSelectMode(false)
    setSelectedIds(new Set())
  }
  const resetLoadedPages = () => setLoadedPage({ key: "", documents: [] })
  const newDoc = () => docActions.create.mutate({ title: "Untitled", language: "markdown" }, { onSuccess: (d) => { if (d?.id) setOpenId(d.id) } })
  const toggleSelected = (id: string) => {
    setSelectedIds((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }
  const toggleAll = () => {
    setSelectedIds((current) => {
      if (allVisibleSelected) return new Set()
      const next = new Set(current)
      list.forEach((doc) => next.add(doc.id))
      return next
    })
  }
  const archiveDoc = (id: string) => docActions.archive.mutate({ id, archived: !archived }, { onSuccess: resetLoadedPages })
  const deleteDoc = (id: string) => { if (confirm("Delete this document?")) docActions.remove.mutate(id, { onSuccess: resetLoadedPages }) }
  const openSourceChat = (doc: DocItem) => {
    if (!doc.session_id) return
    window.localStorage.setItem(LAST_CHAT_SESSION_KEY, doc.session_id)
    navigate(`/chat/${encodeURIComponent(doc.session_id)}?doc=${encodeURIComponent(doc.id)}`)
  }
  const exportPdf = async (doc: DocItem) => {
    setNotice("")
    try {
      await exportDocumentPdf(doc)
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Couldn't export to PDF.")
    }
  }
  const cloneOne = async (doc: DocItem, openAfter = true) => {
    if (busy) return
    const targetId = cloneTarget?.id || ""
    setBusy(`clone:${doc.id}`)
    setNotice("")
    try {
      const created = await cloneDocument(doc, targetId || null)
      setNotice(`Cloned "${doc.title || doc.name || "Untitled"}" to ${cloneTargetLabel}.`)
      if (!openAfter || !created.id) return
      if (targetId) {
        window.localStorage.setItem(LAST_CHAT_SESSION_KEY, targetId)
        navigate(`/chat/${encodeURIComponent(targetId)}?doc=${encodeURIComponent(created.id)}`)
      } else {
        setOpenId(created.id)
      }
    } catch {
      setNotice("Couldn't clone this document.")
    } finally {
      setBusy("")
    }
  }
  const runBulk = async (action: LibraryBulkAction) => {
    const ids = selectedVisibleIds
    if (ids.length === 0 || busy) return
    if (action === "delete" && !confirm(`Delete ${ids.length} selected document${ids.length === 1 ? "" : "s"}?`)) return
    setBusy(action)
    setNotice("")
    try {
      if (action === "clone") {
        let cloned = 0
        for (const id of ids) {
          const doc = list.find((item) => item.id === id)
          if (!doc) continue
          await cloneDocument(doc, cloneTarget?.id || null)
          cloned++
        }
        setNotice(`Cloned ${cloned} document${cloned === 1 ? "" : "s"} to ${cloneTargetLabel}.`)
      } else if (action === "export") {
        if (ids.length === 1) {
          const doc = list.find((item) => item.id === ids[0])
          if (doc) await downloadDocument(doc)
        } else {
          await downloadDocumentsZip(ids)
        }
        setNotice(`Exported ${ids.length} document${ids.length === 1 ? "" : "s"}.`)
      } else {
        for (const id of ids) {
          if (action === "archive") await docActions.archive.mutateAsync({ id, archived: !archived })
          if (action === "delete") await docActions.remove.mutateAsync(id)
        }
        setNotice(action === "archive"
          ? `${archived ? "Restored" : "Archived"} ${ids.length} document${ids.length === 1 ? "" : "s"}.`
          : `Deleted ${ids.length} document${ids.length === 1 ? "" : "s"}.`)
        resetLoadedPages()
      }
      clearSelection()
    } catch {
      setNotice("Some selected documents could not be updated.")
    } finally {
      setBusy("")
    }
  }
  const importFiles = async (files: FileList | null) => {
    const picked = Array.from(files || [])
    if (picked.length === 0) return
    setBusy("import")
    setNotice("")
    let imported = 0
    let failed = 0
    try {
      for (const file of picked) {
        try {
          if (/\.pdf$/i.test(file.name)) {
            await importPdfDocument(file)
            imported++
          } else {
            const documents = await readImportedDocuments(file)
            for (const doc of documents) {
              await docActions.create.mutateAsync({ ...doc, language: doc.language || undefined })
              imported++
            }
          }
        } catch {
          failed++
        }
      }
      resetLoadedPages()
      setNotice(failed
        ? `Imported ${imported} document${imported === 1 ? "" : "s"} · ${failed} file${failed === 1 ? "" : "s"} failed.`
        : `Imported ${imported} document${imported === 1 ? "" : "s"}.`)
    } finally {
      setBusy("")
      if (fileInputRef.current) fileInputRef.current.value = ""
    }
  }
  const runTidy = async () => {
    if (busy) return
    if (!confirm("Tidy the library? This permanently deletes empty, junk, and duplicate documents, then asks AI to remove obvious test/throwaway docs.")) return
    setBusy("tidy")
    setNotice("")
    let deleted = 0
    let fixedTitles = 0
    let reviewed = 0
    try {
      const ruleResult = await tidyActions.tidy.mutateAsync()
      deleted += ruleResult.deleted || 0
      fixedTitles += ruleResult.fixed_titles || 0
      // AI tidy reviews in batches of ~30; keep going while docs remain.
      for (let guard = 0; guard < 20; guard++) {
        const ai = await tidyActions.aiTidy.mutateAsync()
        deleted += ai.deleted || 0
        reviewed += ai.reviewed || 0
        if (!ai.remaining || ai.remaining <= 0) break
      }
      resetLoadedPages()
      clearSelection()
      const parts: string[] = []
      if (deleted) parts.push(`removed ${deleted} document${deleted === 1 ? "" : "s"}`)
      if (fixedTitles) parts.push(`fixed ${fixedTitles} title${fixedTitles === 1 ? "" : "s"}`)
      if (reviewed) parts.push(`reviewed ${reviewed}`)
      setNotice(parts.length ? `Tidy complete — ${parts.join(", ")}.` : "Tidy complete — nothing to clean up.")
    } catch (e) {
      setNotice(e instanceof Error ? `Tidy failed: ${e.message}` : "Couldn't tidy the library.")
    } finally {
      setBusy("")
    }
  }
  const loadMore = async () => {
    if (!library || busy || library.total <= list.length) return
    const offset = list.length
    setBusy("load-more")
    setNotice("")
    try {
      const page = await fetchDocumentLibrary({ search: query, sort, language, archived, limit: LIBRARY_PAGE_SIZE, offset })
      setLoadedPage((current) => {
        const currentDocs = current.key === libraryKey ? current.documents : []
        const seen = new Set(firstPage.map((doc) => doc.id))
        currentDocs.forEach((doc) => seen.add(doc.id))
        const nextDocs = [...currentDocs]
        page.documents.forEach((doc) => {
          if (seen.has(doc.id)) return
          seen.add(doc.id)
          nextDocs.push(doc)
        })
        return { key: libraryKey, documents: nextDocs }
      })
    } catch {
      setNotice("Couldn't load more documents.")
    } finally {
      setBusy("")
    }
  }
  if (openId) return <div className="mx-auto h-full w-full max-w-4xl" data-tour="library-editor"><Editor id={openId} onBack={() => setOpenId(null)} onOpen={setOpenId} /></div>
  return (
    <div className="mx-auto flex h-full w-full max-w-4xl flex-col" data-tour="library-root">
      <header className="flex h-13 shrink-0 items-center justify-between gap-2 border-b px-4">
        <div className="min-w-0">
          <div className="text-sm font-semibold">Library</div>
          <div className="text-xs text-muted-foreground">{library?.total ?? 0} document{(library?.total ?? 0) === 1 ? "" : "s"}{isFetching ? " · updating" : ""}</div>
        </div>
        <div className="flex items-center gap-1.5" data-tour="library-actions">
          <input ref={fileInputRef} type="file" multiple accept=".txt,.md,.markdown,.html,.htm,.svg,.css,.scss,.sass,.less,.js,.jsx,.ts,.tsx,.json,.yml,.yaml,.csv,.tsv,.sh,.bash,.sql,.xml,.toml,.ini,.cfg,.conf,.env,.log,.py,.rs,.go,.java,.c,.h,.cpp,.hpp,.rb,.php,.pdf,.docx,.xlsx,.xls,.ods" className="hidden" onChange={(e) => void importFiles(e.currentTarget.files)} />
          <label className="hidden h-8 items-center gap-1.5 rounded-md border px-2 text-xs text-muted-foreground sm:inline-flex">
            <Copy className="size-3.5" />
            <span>Clone to</span>
            <select
              value={cloneTargetSessionId}
              onChange={(e) => {
                const next = e.target.value
                setCloneTargetSession(next)
                if (next) window.localStorage.setItem(LAST_CHAT_SESSION_KEY, next)
              }}
              className="-mr-1 max-w-40 bg-transparent text-foreground outline-none"
              aria-label="Clone target chat"
            >
              <option value="">Library</option>
              {activeSessions.slice(0, 50).map((session) => (
                <option key={session.id} value={session.id}>{session.name || "Untitled chat"}</option>
              ))}
            </select>
          </label>
          <Button size="sm" variant="outline" disabled={busy === "import"} onClick={() => fileInputRef.current?.click()}><Upload className="size-4" />{busy === "import" ? "Importing..." : "Import"}</Button>
          <Button size="sm" variant="outline" disabled={!!busy} title="Remove junk, empty, and duplicate documents" onClick={() => void runTidy()}><Sparkles className="size-4" />{busy === "tidy" ? "Tidying..." : "Tidy"}</Button>
          <Button size="sm" disabled={docActions.create.isPending} onClick={newDoc}><Plus className="size-4" />New document</Button>
        </div>
      </header>
      <div className="shrink-0 border-b px-4 py-2" data-tour="library-filters">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-0 flex-1 sm:min-w-56">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <input
              value={query}
              onChange={(e) => { setQuery(e.target.value); clearSelection() }}
              placeholder="Search documents..."
              className="h-9 w-full rounded-md border bg-background pl-8 pr-8 text-sm outline-none focus-visible:border-ring"
            />
            {query && <button onClick={() => setQuery("")} title="Clear search" className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"><X className="size-4" /></button>}
          </div>
          <select value={sort} onChange={(e) => { setSort(e.target.value as DocumentSort); clearSelection() }} className="h-9 rounded-md border bg-background px-2 text-sm outline-none focus-visible:border-ring" aria-label="Sort documents">
            <option value="recent">Recent</option>
            <option value="oldest">Oldest</option>
            <option value="alpha">A-Z</option>
            <option value="edits">Most edited</option>
          </select>
          <button
            type="button"
            onClick={() => { setArchived((value) => !value); clearSelection() }}
            className={cn("inline-flex h-9 items-center gap-1.5 rounded-md border px-2.5 text-sm", archived ? "border-foreground bg-accent text-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground")}
          >
            {archived ? <RotateCcw className="size-4" /> : <Archive className="size-4" />}
            {archived ? "Archived" : "Active"}
          </button>
          <button
            type="button"
            onClick={() => { setSelectMode((value) => !value); setSelectedIds(new Set()) }}
            className={cn("inline-flex h-9 items-center gap-1.5 rounded-md border px-2.5 text-sm", selectMode ? "border-foreground bg-accent text-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground")}
          >
            {selectMode ? <X className="size-4" /> : <CheckCheck className="size-4" />}
            {selectMode ? "Cancel" : "Select"}
          </button>
        </div>
        {languageEntries.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            <button onClick={() => { setLanguage(null); clearSelection() }} className={cn("rounded-md border px-2 py-1 text-xs", !language ? "border-foreground bg-accent text-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground")}>all ({Object.values(languages).reduce((a, b) => a + b, 0)})</button>
            {languageEntries.map(([lang, count]) => (
              <button key={lang} onClick={() => { setLanguage(lang); clearSelection() }} className={cn("rounded-md border px-2 py-1 text-xs", language === lang ? "border-foreground bg-accent text-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground")}>{lang} ({count})</button>
            ))}
          </div>
        )}
      </div>
      {selectMode && (
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b bg-muted/20 px-4 py-2 text-xs">
          <label className="inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-muted-foreground">
            <input type="checkbox" checked={allVisibleSelected} onChange={toggleAll} className="size-3.5 accent-current" />
            <span>All</span>
          </label>
          <span className="text-muted-foreground">{busy ? `${busy}...` : `${selectedVisibleIds.length} selected`}</span>
          <Button size="sm" variant="outline" disabled={selectedVisibleIds.length === 0 || !!busy} onClick={() => void runBulk("export")}><Download className="size-3.5" />Export</Button>
          <Button size="sm" variant="outline" disabled={selectedVisibleIds.length === 0 || !!busy} onClick={() => void runBulk("clone")}><Copy className="size-3.5" />Clone</Button>
          <Button size="sm" variant="outline" disabled={selectedVisibleIds.length === 0 || !!busy} onClick={() => void runBulk("archive")}>{archived ? <RotateCcw className="size-3.5" /> : <Archive className="size-3.5" />}{archived ? "Restore" : "Archive"}</Button>
          <Button size="sm" variant="destructive" disabled={selectedVisibleIds.length === 0 || !!busy} onClick={() => void runBulk("delete")}><Trash2 className="size-3.5" />Delete</Button>
          <button type="button" onClick={clearSelection} disabled={!!busy} title="Cancel selection" className="ml-auto rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"><X className="size-3.5" /></button>
        </div>
      )}
      {notice && <div className={cn("shrink-0 border-b px-4 py-2 text-xs", /couldn.t|could not|failed/i.test(notice) ? "text-destructive" : "text-muted-foreground")}>{notice}</div>}
      <div className="flex-1 overflow-y-auto p-4" data-tour="library-list">
        <div className="space-y-2">
          {list.map((doc) => (
            <DocumentRow
              key={doc.id}
              doc={doc}
              selected={selectedIds.has(doc.id)}
              selectMode={selectMode}
              archived={archived}
              onOpen={() => setOpenId(doc.id)}
              onOpenSource={() => openSourceChat(doc)}
              onClone={() => void cloneOne(doc)}
              onExportPdf={() => void exportPdf(doc)}
              onToggle={() => toggleSelected(doc.id)}
              onStartSelect={() => {
                setSelectMode(true)
                setSelectedIds(new Set([doc.id]))
              }}
              onArchive={() => archiveDoc(doc.id)}
              onDelete={() => deleteDoc(doc.id)}
              cloneBusy={busy === `clone:${doc.id}`}
            />
          ))}
          {list.length === 0 && (
            <p className="py-10 text-center text-sm text-muted-foreground">
              {query || language ? "No documents match your filters." : archived ? "No archived documents." : "No documents yet."}
            </p>
          )}
          {library && library.total > list.length && (
            <div className="py-3 text-center">
              <Button type="button" size="sm" variant="outline" disabled={!!busy} onClick={() => void loadMore()}>
                {busy === "load-more" ? "Loading..." : `Load more (${list.length} of ${library.total})`}
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
