import { useState } from "react"
import { ChevronRight, Terminal, Loader2, Check, X } from "lucide-react"
import { cn } from "@/lib/utils"
import type { ToolEvent } from "@/types"

function ToolRow({ t }: { t: ToolEvent }) {
  const err = t.exitCode != null && t.exitCode !== 0
  const rawCmd = t.command || (typeof t.input === "string" ? t.input : "")
  // Doc-edit tools carry their FIND/REPLACE body as the command; its first line
  // is a `<<<FIND>>>` marker — raw tool syntax that shouldn't surface. Hide it.
  const cmd = rawCmd.startsWith("<<<") ? "" : rawCmd
  const image = t.imageUrl || t.screenshot
  const diff = t.diff?.text
  return (
    <div className="animate-fade-in">
      <div className="flex items-center gap-1.5">
        {t.running
          ? <Loader2 className="size-3.5 shrink-0 animate-spin text-muted-foreground" />
          : err
            ? <X className="size-3.5 shrink-0 text-destructive" />
            : <Check className="size-3.5 shrink-0 text-emerald-500" />}
        <span className="font-medium text-foreground">{t.name}</span>
        {cmd && !diff && <span className="min-w-0 truncate font-mono text-[11px] text-muted-foreground">{cmd.split("\n")[0].slice(0, 90)}</span>}
      </div>
      {t.running && t.progress && <div className="mt-1 truncate pl-5 font-mono text-[11px] text-muted-foreground">{t.progress}</div>}
      {/* File-write/edit diff — re-rendered from the persisted tool event. */}
      {diff && <pre className="mt-1 max-h-48 overflow-auto rounded bg-muted p-2 text-[11px] leading-relaxed">
        {(t.diff?.file ? `${t.diff.file}\n` : "") + diff.split("\n").map((l) => l).join("\n").slice(0, 4000)}
      </pre>}
      {t.output && !diff && <pre className="mt-1 max-h-48 overflow-auto rounded bg-muted p-2 text-[11px] leading-relaxed">{String(t.output).slice(0, 4000)}</pre>}
      {/* Generated image / browser screenshot. */}
      {image && <img src={image} alt={t.imagePrompt || t.name} className="mt-1 max-h-64 rounded border" />}
    </div>
  )
}

export function ToolThread({ tools, defaultOpen = false }: { tools: ToolEvent[]; defaultOpen?: boolean }) {
  const anyRunning = tools.some((t) => t.running)
  // Collapsed by default — expand to inspect each step's command and output.
  // Agent turns pass defaultOpen so the steps are visible like the legacy UI.
  const [open, setOpen] = useState(defaultOpen)
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
