import { useEffect, useRef, useState } from "react"
import { useParams } from "react-router-dom"
import { MoreHorizontal, Download, Copy } from "lucide-react"
import { useChat } from "@/lib/useChat"
import { useSessions } from "@/api/sessions"
import { Message } from "@/components/chat/Message"
import { Composer } from "@/components/chat/Composer"
import { ContextPanel } from "@/components/chat/ContextPanel"
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
          <div className="absolute right-0 z-20 mt-1 w-52 rounded-xl border bg-popover p-1 shadow-lg">
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
  const { messages, streaming, send, stop } = useChat(sessionId)
  const { data: sessions } = useSessions()
  const title = sessions?.find((s) => s.id === sessionId)?.name
  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }) }, [messages])

  return (
    <div className="flex h-full min-w-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-13 shrink-0 items-center justify-between border-b px-4 text-sm font-medium text-foreground">
          <span className="truncate">{title || "New chat"}</span>
          {sessionId && messages.length > 0 && <ExportMenu sid={sessionId} messages={messages} />}
        </header>
        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          {messages.length === 0 ? (
            <div className="flex h-full items-center justify-center p-8">
              <div className="w-full max-w-[768px] text-center">
                <h1 className="text-2xl font-semibold tracking-tight">How can I help?</h1>
                <p className="mt-2 text-sm text-muted-foreground">Start a conversation below.</p>
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
                  for (let j = i - 1; j >= 0; j--) { if (messages[j].role === "user") { send(messages[j].content); break } }
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
