import { useEffect, useRef } from "react"
import { useParams } from "react-router-dom"
import { useChat } from "@/lib/useChat"
import { useSessions } from "@/api/sessions"
import { Message } from "@/components/chat/Message"
import { Composer } from "@/components/chat/Composer"

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
        <header className="flex h-13 shrink-0 items-center border-b px-4 text-sm font-medium text-foreground">
          <span className="truncate">{title || "New chat"}</span>
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
            <div className="mx-auto w-full max-w-[768px] space-y-6 px-4 py-6">{messages.map((m, i) => <Message key={i} m={m} />)}</div>
          )}
        </div>
        <Composer onSend={send} onStop={stop} streaming={streaming} />
      </div>
    </div>
  )
}
