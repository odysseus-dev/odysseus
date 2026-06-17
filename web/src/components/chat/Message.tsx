import { useRef, useState } from "react"
import { ChevronRight, Brain, Telescope, Loader2, Volume2, Square, BookOpen } from "lucide-react"
import { usePanel } from "@/stores/panel"
import { Markdown } from "./Markdown"
import { ToolThread } from "./ToolThread"
import { useVoiceCaps, speak } from "@/api/voice"
import { cn } from "@/lib/utils"
import type { ChatMessage } from "@/types"

function SpeakButton({ text }: { text: string }) {
  const { data: caps } = useVoiceCaps()
  const [busy, setBusy] = useState(false)
  const [playing, setPlaying] = useState(false)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  if (!caps?.tts) return null
  const toggle = async () => {
    if (playing) { audioRef.current?.pause(); audioRef.current = null; setPlaying(false); return }
    setBusy(true)
    try {
      const a = await speak(text)
      audioRef.current = a; setPlaying(true)
      a.addEventListener("ended", () => setPlaying(false), { once: true })
    } catch { /* unavailable */ } finally { setBusy(false) }
  }
  return (
    <button onClick={toggle} title={playing ? "Stop" : "Read aloud"} className="inline-flex items-center gap-1 hover:text-foreground">
      {busy ? <Loader2 className="size-3.5 animate-spin" /> : playing ? <Square className="size-3.5" /> : <Volume2 className="size-3.5" />}
    </button>
  )
}

function Reasoning({ text, live }: { text: string; live: boolean }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-lg border bg-card text-xs">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-2 px-3 py-2 text-muted-foreground hover:text-foreground">
        <ChevronRight className={cn("size-3.5 transition-transform", open && "rotate-90")} />
        <Brain className="size-3.5" />
        {live ? "Thinking…" : "Reasoning"}
      </button>
      {open && <div className="whitespace-pre-wrap border-t px-3 py-2 text-muted-foreground">{text}</div>}
    </div>
  )
}

export function Message({ m }: { m: ChatMessage }) {
  if (m.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[75%] whitespace-pre-wrap rounded-2xl bg-secondary px-4 py-2.5 text-[15px]">{m.content}</div>
      </div>
    )
  }
  const mt = m.metrics
  return (
    <div className="space-y-3">
      {m.reasoning && <Reasoning text={m.reasoning} live={!!m.streaming && !m.content} />}
      {m.tools && m.tools.length > 0 && <ToolThread tools={m.tools} />}
      {m.research && m.streaming && (
        <div className="flex items-center gap-2.5 rounded-lg border bg-card px-3 py-2.5 text-sm">
          <Telescope className="size-4 shrink-0 text-muted-foreground" />
          <span className="font-medium capitalize">{m.research.phase}</span>
          {m.research.detail && <span className="min-w-0 truncate text-muted-foreground">— {m.research.detail}</span>}
          <Loader2 className="ml-auto size-3.5 shrink-0 animate-spin text-muted-foreground" />
        </div>
      )}
      {m.content ? <Markdown>{m.content}</Markdown> : m.streaming && !m.reasoning && !m.research ? <div className="text-sm text-muted-foreground">Thinking…</div> : null}
      {m.sources && m.sources.length > 0 && (
        <button onClick={() => usePanel.getState().show("sources", { title: `Sources · ${m.sources!.length}`, payload: m.sources })}
          className="mt-1 inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground">
          <BookOpen className="size-3.5" /> {m.sources.length} source{m.sources.length === 1 ? "" : "s"}
        </button>
      )}
      {!m.streaming && (m.model || mt || m.content) && (
        <div className="flex flex-wrap items-center gap-3 pt-0.5 text-[11px] text-muted-foreground">
          {m.model && <span>{m.model}</span>}
          {mt?.tokens_out != null && <span>{mt.tokens_out} tok</span>}
          {mt?.tok_per_sec != null && <span>{Math.round(mt.tok_per_sec)} tok/s</span>}
          {mt?.cost != null && <span>${Number(mt.cost).toFixed(4)}</span>}
          {m.content && <SpeakButton text={m.content} />}
        </div>
      )}
    </div>
  )
}
