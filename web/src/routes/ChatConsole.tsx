import { useEffect, useRef, useState } from "react"
import { useParams, useSearchParams } from "react-router-dom"
import { MoreHorizontal, Download, Copy, EyeOff, FileText, Users, ArrowDown } from "lucide-react"
import { useChat } from "@/lib/useChat"
import { useComposer } from "@/stores/composer"
import { useSessions } from "@/api/sessions"
import { useSessionDocuments } from "@/api/documents"
import { useAuthStatus } from "@/api/auth"
import { usePersonalization } from "@/api/prefs"
import { greeting } from "@/lib/personalization"
import { getPersistentPersonaName } from "@/lib/persistentPersona"
import { usePanel } from "@/stores/panel"
import { Message } from "@/components/chat/Message"
import { Composer } from "@/components/chat/Composer"
import { ContextPanel } from "@/components/chat/ContextPanel"
import { ShareMenu } from "@/components/chat/ShareMenu"
import { ProjectPicker } from "@/components/chat/ProjectPicker"
import { Mascot } from "@/components/ui/Mascot"
import { apiJson } from "@/lib/api"
import { cn } from "@/lib/utils"
import type { ChatMessage } from "@/types"

const LAST_CHAT_SESSION_KEY = "odysseus-last-chat-session"

function ExportMenu({ sid, messages }: { sid: string; messages: ChatMessage[] }) {
  const [open, setOpen] = useState(false)
  const item = "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
  const exp = (fmt: string) => { window.open(`/api/session/${sid}/export?fmt=${fmt}`, "_blank"); setOpen(false) }
  const copy = async () => { try { await navigator.clipboard.writeText(messages.map((m) => `${m.role === "user" ? "You" : "Assistant"}: ${m.content}`).join("\n\n")) } catch { /* ignore */ } setOpen(false) }
  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} title="Export / more" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><MoreHorizontal className="size-4" /></button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-20 mt-1 w-52 origin-top-right animate-pop-in rounded-xl border bg-popover p-1 shadow-lg">
            <button onClick={copy} className={item}><Copy className="size-4" />Copy transcript</button>
            <button onClick={() => exp("md")} className={item}><Download className="size-4" />Export Markdown</button>
            <button onClick={() => exp("txt")} className={item}><Download className="size-4" />Export Text</button>
            <button onClick={() => exp("html")} className={item}><Download className="size-4" />Export HTML</button>
            <button onClick={() => exp("json")} className={item}><Download className="size-4" />Export JSON</button>
          </div>
        </>
      )}
    </div>
  )
}

const SUGGESTIONS = [
  "What can you help me with?",
  "Summarize my recent notes",
  "What's on my calendar this week?",
  "Brainstorm ideas for a project",
]

export function ChatConsole() {
  const { sessionId } = useParams()
  const [searchParams] = useSearchParams()
  const requestedDocId = searchParams.get("doc")
  const { messages, streaming, send, stop, regenerate, editResend, editAssistant, deleteMessage, forkFrom, rewriteMessage, localReply, clearLocalMessages } = useChat(sessionId)
  const { data: sessions } = useSessions()
  const { data: auth } = useAuthStatus()
  const { data: personalization } = usePersonalization()
  const incognito = useComposer((s) => s.incognito)
  const { data: threadDocs } = useSessionDocuments(sessionId)
  const panelOpen = usePanel((s) => s.open)
  const panelKind = usePanel((s) => s.kind)
  const filesPanelOpen = panelOpen && panelKind === "files"
  const docCount = threadDocs?.length || 0
  const title = sessions?.find((s) => s.id === sessionId)?.name
  const persistentPersonaName = getPersistentPersonaName(sessionId)
  const scrollRef = useRef<HTMLDivElement>(null)
  const queryDocOpenedRef = useRef<string | null>(null)
  const [atBottom, setAtBottom] = useState(true)
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  // Stick to the bottom as tokens stream in — but ONLY when the user is already
  // there. If they scrolled up to read, don't yank them back down every token.
  useEffect(() => { if (atBottom && scrollRef.current) scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight }) }, [messages, atBottom])
  const onScroll = () => { const el = scrollRef.current; if (el) setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 96) }
  // Reset transient view state when switching threads.
  // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional reset on thread change
  useEffect(() => { setEditingIndex(null); setAtBottom(true) }, [sessionId])
  useEffect(() => {
    if (sessionId) window.localStorage.setItem(LAST_CHAT_SESSION_KEY, sessionId)
  }, [sessionId])

  // The panel is global; reset it when switching threads so a doc/files panel
  // from the previous thread doesn't linger over a different conversation.
  const prevSessionRef = useRef(sessionId)
  useEffect(() => {
    if (prevSessionRef.current === sessionId) return
    prevSessionRef.current = sessionId
    const p = usePanel.getState()
    if (p.open && (p.kind === "files" || p.kind === "doc")) p.close()
  }, [sessionId])

  // Auto-open the per-thread files panel once its files are known — but don't
  // clobber a panel the live stream opened.
  const autoOpenedRef = useRef<string | null>(null)
  useEffect(() => {
    if (!sessionId || docCount === 0) return
    if (autoOpenedRef.current === sessionId) return
    autoOpenedRef.current = sessionId
    if (!usePanel.getState().open) usePanel.getState().showFiles(threadDocs || [])
  }, [sessionId, docCount, threadDocs])

  // Library links use /chat/:sessionId?doc=:docId to mirror Original's
  // "open in original session" action and surface the selected file immediately.
  useEffect(() => {
    if (!sessionId || !requestedDocId || !threadDocs) return
    const key = `${sessionId}:${requestedDocId}`
    if (queryDocOpenedRef.current === key) return
    queryDocOpenedRef.current = key
    const linkedDoc = threadDocs.find((doc) => doc.id === requestedDocId)
    const panel = usePanel.getState()
    if (threadDocs.length > 0) panel.showFiles(threadDocs)
    panel.showDoc(linkedDoc?.title || linkedDoc?.name || "Document", linkedDoc?.language)
    panel.setDocId(requestedDocId)
    let cancelled = false
    void apiJson<{ title?: string; language?: string; current_content?: string }>(`/api/document/${requestedDocId}`)
      .then((full) => {
        if (cancelled || usePanel.getState().doc?.docId !== requestedDocId) return
        const p = usePanel.getState()
        p.showDoc(full.title || linkedDoc?.title || linkedDoc?.name || "Document", full.language || linkedDoc?.language)
        p.setDocId(requestedDocId)
        p.setDocContent(full.current_content || "")
      })
      .catch(() => {
        if (!cancelled && usePanel.getState().doc?.docId === requestedDocId) usePanel.getState().setDocError("Couldn't load this document.")
      })
    return () => { cancelled = true }
  }, [sessionId, requestedDocId, threadDocs])

  const toggleFiles = () => {
    if (filesPanelOpen) usePanel.getState().close()
    else usePanel.getState().showFiles(threadDocs || [])
  }

  return (
    <div className="flex h-full min-w-0 flex-1">
      <div className="relative flex min-w-0 flex-1 flex-col">
        <header className="flex h-13 shrink-0 items-center justify-between border-b px-4 text-sm font-medium text-foreground" data-tour="chat-header">
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate">{incognito ? "Incognito chat" : (title || "New chat")}</span>
            {incognito && (
              <span className="inline-flex shrink-0 items-center gap-1 rounded-full border bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                <EyeOff className="size-3" /> Not saved
              </span>
            )}
            {persistentPersonaName && !incognito && (
              <span className="inline-flex max-w-40 shrink-0 items-center gap-1 rounded-full border bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                <Users className="size-3" />
                <span className="truncate">{persistentPersonaName}</span>
              </span>
            )}
          </span>
          <div className="flex items-center gap-1">
            {docCount > 0 && (
              <button
                onClick={toggleFiles}
                title={filesPanelOpen ? "Hide files panel" : "Show files in this thread"}
                className={cn("flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition-colors",
                  filesPanelOpen ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground")}
              >
                <FileText className="size-3.5" />{docCount} file{docCount === 1 ? "" : "s"}
              </button>
            )}
            {sessionId && messages.length > 0 && !incognito && <ProjectPicker sessionId={sessionId} />}
            {sessionId && messages.length > 0 && !incognito && <ShareMenu resourceType="session" resourceId={sessionId} />}
            {sessionId && messages.length > 0 && <ExportMenu sid={sessionId} messages={messages} />}
          </div>
        </header>
        <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-y-auto">
          {messages.length === 0 ? (
            <div className="flex h-full items-center justify-center p-8">
              <div className="w-full max-w-[768px] text-center" data-tour="chat-welcome">
                <Mascot size={20} className="mx-auto mb-6 animate-pop-in" title="Odysseus" />
                <h1 className="text-2xl font-semibold tracking-tight">
                  {greeting(personalization.nickname || auth?.username || auth?.user)}
                </h1>
                <p className="mt-2 text-sm text-muted-foreground">How can I help?</p>
                <div className="mt-6 flex flex-wrap justify-center gap-2">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} onClick={() => send(s)} disabled={streaming}
                      className="rounded-full border px-3.5 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50">
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="mx-auto w-full max-w-[768px] space-y-6 px-4 py-6">{messages.map((m, i) => (
              <Message key={i} m={m}
                onRegenerate={m.role === "assistant" && !streaming ? () => {
                  for (let j = i - 1; j >= 0; j--) { if (messages[j].role === "user") { regenerate(j); break } }
                } : undefined}
                onRespond={m.role === "assistant" && !streaming ? (text) => send(text) : undefined}
                editing={editingIndex === i}
                onEdit={!streaming ? () => setEditingIndex(i) : undefined}
                onDelete={!streaming ? () => deleteMessage(i) : undefined}
                onFork={!streaming && !incognito ? () => forkFrom(i) : undefined}
                onRewrite={m.role === "assistant" && !streaming ? (instruction) => rewriteMessage(i, instruction) : undefined}
                onEditSubmit={(text) => {
                  if (m.role === "assistant") void editAssistant(i, text).then((saved) => { if (saved) setEditingIndex(null) })
                  else { setEditingIndex(null); editResend(i, text) }
                }}
                onEditCancel={() => setEditingIndex(null)}
              />
            ))}</div>
          )}
        </div>
        {!atBottom && messages.length > 0 && (
          <button onClick={() => { const el = scrollRef.current; if (el) { el.scrollTo({ top: el.scrollHeight, behavior: "smooth" }); setAtBottom(true) } }}
            title="Jump to latest"
            className="absolute bottom-28 left-1/2 z-10 -translate-x-1/2 animate-fade-in rounded-full border bg-popover p-2 text-muted-foreground shadow-md transition-colors hover:text-foreground">
            <ArrowDown className="size-4" />
          </button>
        )}
        <Composer onSend={send} onLocalReply={localReply} onClearMessages={clearLocalMessages} onStop={stop} streaming={streaming} sessionId={sessionId} />
      </div>
      <ContextPanel />
    </div>
  )
}
