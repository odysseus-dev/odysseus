import { useEffect, useRef } from "react"
import { useParams } from "react-router-dom"
import { Moon, Sun } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useUi } from "@/stores/ui"
import { useChat } from "@/lib/useChat"
import { SessionsSidebar } from "@/components/chat/SessionsSidebar"
import { ConfigPanel } from "@/components/shell/ConfigPanel"
import { Message } from "@/components/chat/Message"
import { Composer } from "@/components/chat/Composer"

export function ChatConsole() {
  const { theme, toggleTheme } = useUi()
  const { sessionId } = useParams()
  const { messages, streaming, send, stop } = useChat(sessionId)
  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }) }, [messages])

  return (
    <div className="flex h-full min-w-0 flex-1">
      <SessionsSidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-13 shrink-0 items-center justify-between border-b px-4">
          <div className="text-sm font-semibold">Odysseus <span className="font-normal text-muted-foreground">/ v2</span></div>
          <Button variant="ghost" size="icon" onClick={toggleTheme} title="Toggle theme">{theme === "dark" ? <Sun /> : <Moon />}</Button>
        </header>
        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          {messages.length === 0 ? (
            <div className="flex h-full items-center justify-center p-8 text-center">
              <div><h1 className="text-2xl font-semibold tracking-tight">How can I help?</h1><p className="mt-2 text-sm text-muted-foreground">Start a conversation below.</p></div>
            </div>
          ) : (
            <div className="mx-auto w-full max-w-[768px] space-y-6 px-4 py-6">{messages.map((m, i) => <Message key={i} m={m} />)}</div>
          )}
        </div>
        <Composer onSend={send} onStop={stop} streaming={streaming} />
      </div>
      <ConfigPanel />
    </div>
  )
}
