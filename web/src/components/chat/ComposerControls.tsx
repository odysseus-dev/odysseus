import { useEffect, useState } from "react"
import type { ReactNode } from "react"
import { ChevronDown, SlidersHorizontal } from "lucide-react"
import { useModels, useDefaultChat } from "@/api/models"
import { usePresets } from "@/api/presets"
import { useComposer } from "@/stores/composer"
import { cn } from "@/lib/utils"

function Row({ label, children }: { label: string; children: ReactNode }) {
  return <div className="flex items-center justify-between gap-3 py-1.5"><span className="text-sm text-muted-foreground">{label}</span>{children}</div>
}
function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={cn("relative h-5 w-9 shrink-0 rounded-full transition-colors", on ? "bg-primary" : "bg-input")}>
      <span className={cn("absolute top-0.5 size-4 rounded-full bg-background transition-transform", on ? "translate-x-4" : "translate-x-0.5")} />
    </button>
  )
}
const trigger = "flex items-center gap-1.5 rounded-md px-2 py-1 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"

export function ModePicker() {
  const c = useComposer()
  return (
    <div className="flex rounded-lg bg-muted p-0.5">
      {(["chat", "agent"] as const).map((mode) => (
        <button key={mode} onClick={() => c.setMode(mode)} className={cn("rounded-md px-2.5 py-1 text-xs font-medium capitalize transition-colors", c.mode === mode ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>{mode}</button>
      ))}
    </div>
  )
}

export function ModelPicker() {
  const { data: models } = useModels()
  const { data: def } = useDefaultChat()
  const c = useComposer()
  const [open, setOpen] = useState(false)

  // Seed the default model on first load (moved here from the old ConfigPanel).
  useEffect(() => {
    if (c.model || !def?.model) return
    const ep = models?.items?.find((e) => (e.models || []).includes(def.model) || (e.models_extra || []).includes(def.model))
    c.setModel(def.model, def.endpoint_id || ep?.endpoint_id || "", def.endpoint_url || ep?.url || "")
  }, [def, models, c])

  const pick = (epId: string, model: string, url: string) => { c.setModel(model, epId, url); setOpen(false) }
  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} className={trigger}>
        <span className="max-w-[160px] truncate">{c.model || "Select model"}</span>
        <ChevronDown className="size-3.5" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute bottom-full right-0 z-20 mb-1 max-h-80 w-64 overflow-y-auto rounded-xl border bg-popover p-1 shadow-lg">
            {(models?.items || []).map((ep) => (
              <div key={ep.endpoint_id} className="mb-1">
                <div className="px-2 py-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">{ep.endpoint_name || ep.url}</div>
                {[...(ep.models || []), ...(ep.models_extra || [])].map((m) => (
                  <button key={ep.endpoint_id + m} onClick={() => pick(ep.endpoint_id, m, ep.url || "")}
                    className={cn("flex w-full items-center rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent", c.model === m && c.endpointId === ep.endpoint_id && "bg-accent text-foreground")}>
                    <span className="truncate">{m}</span>
                  </button>
                ))}
              </div>
            ))}
            {(models?.items || []).length === 0 && <p className="px-2 py-3 text-sm text-muted-foreground">No models.</p>}
          </div>
        </>
      )}
    </div>
  )
}

export function ToolsMenu() {
  const { data: presets } = usePresets()
  const c = useComposer()
  const [open, setOpen] = useState(false)
  const activeCount = [c.useWeb, c.useResearch].filter(Boolean).length
  const selectCls = "h-9 w-full rounded-md border bg-background px-2 text-sm outline-none focus-visible:border-ring"
  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} className={cn(trigger, activeCount && "text-foreground")} title="Tools & options">
        <SlidersHorizontal className="size-4" />
        <span className="hidden sm:inline">Tools</span>
        {activeCount > 0 && <span className="rounded-full bg-primary/15 px-1.5 text-[10px] font-medium text-foreground">{activeCount}</span>}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute bottom-full left-0 z-20 mb-1 w-64 rounded-xl border bg-popover p-3 shadow-lg">
            <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Tools</div>
            <Row label="Web search"><Toggle on={c.useWeb} onClick={() => c.toggle("useWeb")} /></Row>
            <Row label="Deep research"><Toggle on={c.useResearch} onClick={() => c.toggle("useResearch")} /></Row>
            <Row label="Allow bash"><Toggle on={c.allowBash} onClick={() => c.toggle("allowBash")} /></Row>
            <Row label="Memory (RAG)"><Toggle on={c.useRag} onClick={() => c.toggle("useRag")} /></Row>
            <Row label="Incognito"><Toggle on={c.incognito} onClick={() => c.toggle("incognito")} /></Row>
            {(presets || []).length > 0 && (
              <div className="mt-2 border-t pt-2">
                <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Preset</div>
                <select value={c.presetId} onChange={(e) => c.setPreset(e.target.value)} className={selectCls}>
                  <option value="">None</option>
                  {(presets || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
