import { useState } from "react"
import { ChevronRight, Terminal } from "lucide-react"
import { cn } from "@/lib/utils"
import type { ToolEvent } from "@/types"

export function ToolThread({ tools }: { tools: ToolEvent[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-lg border bg-card text-xs">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-2 px-3 py-2 text-muted-foreground hover:text-foreground">
        <ChevronRight className={cn("size-3.5 transition-transform", open && "rotate-90")} />
        <Terminal className="size-3.5" />
        {tools.length} tool call{tools.length > 1 ? "s" : ""}
      </button>
      {open && (
        <div className="space-y-2 border-t px-3 py-2">
          {tools.map((t, i) => (
            <div key={i}>
              <div className="font-medium text-foreground">{t.name}{t.progress ? ` — ${t.progress}` : ""}</div>
              {t.output && <pre className="mt-1 overflow-auto rounded bg-muted p-2 text-[11px]">{String(t.output).slice(0, 4000)}</pre>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
