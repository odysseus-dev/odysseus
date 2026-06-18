import { useState } from "react"
import { ChevronRight, Terminal, Loader2, Check, X } from "lucide-react"
import { cn } from "@/lib/utils"
import type { ToolEvent } from "@/types"

function ToolRow({ t }: { t: ToolEvent }) {
  const err = t.exitCode != null && t.exitCode !== 0
  const cmd = t.command || (typeof t.input === "string" ? t.input : "")
  return (
    <div className="animate-fade-in">
      <div className="flex items-center gap-1.5">
        {t.running
          ? <Loader2 className="size-3.5 shrink-0 animate-spin text-muted-foreground" />
          : err
            ? <X className="size-3.5 shrink-0 text-destructive" />
            : <Check className="size-3.5 shrink-0 text-emerald-500" />}
        <span className="font-medium text-foreground">{t.name}</span>
        {cmd && <span className="min-w-0 truncate font-mono text-[11px] text-muted-foreground">{cmd.split("\n")[0].slice(0, 90)}</span>}
      </div>
      {t.running && t.progress && <div className="mt-1 truncate pl-5 font-mono text-[11px] text-muted-foreground">{t.progress}</div>}
      {t.output && <pre className="mt-1 max-h-48 overflow-auto rounded bg-muted p-2 text-[11px] leading-relaxed">{String(t.output).slice(0, 4000)}</pre>}
    </div>
  )
}

export function ToolThread({ tools }: { tools: ToolEvent[] }) {
  const anyRunning = tools.some((t) => t.running)
  // Starts expanded so the "flow of thought" is visible as tools stream in.
  const [open, setOpen] = useState(true)
  return (
    <div className="rounded-lg border bg-card text-xs">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-2 px-3 py-2 text-muted-foreground transition-colors hover:text-foreground">
        <ChevronRight className={cn("size-3.5 transition-transform duration-200", open && "rotate-90")} />
        {anyRunning ? <Loader2 className="size-3.5 animate-spin" /> : <Terminal className="size-3.5" />}
        <span>{anyRunning ? "Working…" : `${tools.length} step${tools.length > 1 ? "s" : ""}`}</span>
      </button>
      {open && (
        <div className="space-y-2.5 border-t px-3 py-2">
          {tools.map((t, i) => <ToolRow key={i} t={t} />)}
        </div>
      )}
    </div>
  )
}
