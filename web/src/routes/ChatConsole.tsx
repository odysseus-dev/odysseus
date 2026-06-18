import { useEffect, useRef, useState } from "react"
import { useParams } from "react-router-dom"
import { MoreHorizontal, Download, Copy, EyeOff, FileText } from "lucide-react"
import { useChat } from "@/lib/useChat"
import { useComposer } from "@/stores/composer"
import { useSessions } from "@/api/sessions"
import { useSessionDocuments } from "@/api/documents"
import { useAuthStatus } from "@/api/auth"
import { usePersonalization } from "@/api/prefs"
import { greeting } from "@/lib/personalization"
import { usePanel } from "@/stores/panel"
import { Message } from "@/components/chat/Message"
import { Composer } from "@/components/chat/Composer"
import { ContextPanel } from "@/components/chat/ContextPanel"
import { ShareMenu } from "@/components/chat/ShareMenu"
import { Mascot } from "@/components/ui/Mascot"
import { cn } from "@/lib/utils"
import type { ChatMessage } from "@/types"

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
  const { messages, streaming, send, stop, regenerate } = useChat(sessionId)
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
  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }) }, [messages])

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

  const toggleFiles = () => {
    if (filesPanelOpen) usePanel.getState().close()
    else usePanel.getState().showFiles(threadDocs || [])
  }

  return (
    <div className="flex h-full min-w-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-13 shrink-0 items-center justify-between border-b px-4 text-sm font-medium text-foreground">
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate">{incognito ? "Incognito chat" : (title || "New chat")}</span>
            {incognito && (
              <span className="inline-flex shrink-0 items-center gap-1 rounded-full border bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                <EyeOff className="size-3" /> Not saved
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
            {sessionId && messages.length > 0 && !incognito && <ShareMenu resourceType="session" resourceId={sessionId} />}
            {sessionId && messages.length > 0 && <ExportMenu sid={sessionId} messages={messages} />}
          </div>
        </header>
        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          {messages.length === 0 ? (
            <div className="flex h-full items-center justify-center p-8">
              <div className="w-full max-w-[768px] text-center">
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
                  for (let j = i - 1; j >= 0; j--) { if (messages[j].role === "user") { regenerate(messages[j].content, j); break } }
                } : undefined}
                onEdit={m.role === "user" && !streaming ? () => window.dispatchEvent(new CustomEvent("odysseus:set-composer", { detail: m.content })) : undefined}
              />
            ))}</div>
          )}
        </div>
        <Composer onSend={send} onStop={stop} streaming={streaming} />
      </div>
      <ContextPanel />
    </div>
  )
}
