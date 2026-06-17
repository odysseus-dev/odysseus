import { Markdown } from "./Markdown"
import { ToolThread } from "./ToolThread"
import type { ChatMessage } from "@/types"

export function Message({ m }: { m: ChatMessage }) {
  if (m.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[75%] whitespace-pre-wrap rounded-2xl bg-secondary px-4 py-2.5 text-[15px]">{m.content}</div>
      </div>
    )
  }
  return (
    <div className="space-y-3">
      {m.tools && m.tools.length > 0 && <ToolThread tools={m.tools} />}
      {m.content ? <Markdown>{m.content}</Markdown> : m.streaming ? <div className="text-sm text-muted-foreground">Thinking…</div> : null}
      {m.sources && m.sources.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pt-1">
          {m.sources.slice(0, 8).map((s, i) => (
            <a key={i} href={s.url} target="_blank" rel="noreferrer" className="max-w-[220px] truncate rounded-md border px-2 py-0.5 text-xs text-muted-foreground hover:text-foreground">
              {s.title || s.url}
            </a>
          ))}
        </div>
      )}
    </div>
  )
}
