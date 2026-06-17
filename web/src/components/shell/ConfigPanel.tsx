import { useEffect } from "react"
import type { ReactNode } from "react"
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
const selectCls = "h-9 w-full rounded-md border bg-background px-2 text-sm outline-none focus-visible:border-ring"

export function ConfigPanel() {
  const { data: models } = useModels()
  const { data: def } = useDefaultChat()
  const { data: presets } = usePresets()
  const c = useComposer()

  useEffect(() => {
    if (c.model || !def?.model) return
    const ep = models?.items?.find((e) => (e.models || []).includes(def.model) || (e.models_extra || []).includes(def.model))
    c.setModel(def.model, def.endpoint_id || ep?.endpoint_id || "", def.endpoint_url || ep?.url || "")
  }, [def, models, c])

  const onModelChange = (val: string) => {
    const i = val.indexOf("::"); const epId = val.slice(0, i); const model = val.slice(i + 2)
    const ep = models?.items?.find((e) => e.endpoint_id === epId)
    c.setModel(model, epId, ep?.url || "")
  }

  return (
    <aside className="hidden w-80 shrink-0 flex-col gap-5 overflow-y-auto border-l bg-card p-4 lg:flex">
      <div>
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Model</h2>
        <select value={c.endpointId + "::" + c.model} onChange={(e) => onModelChange(e.target.value)} className={selectCls}>
          {!c.model && <option value="::">Select a model…</option>}
          {(models?.items || []).map((ep) => (
            <optgroup key={ep.endpoint_id} label={ep.endpoint_name || ep.url}>
              {[...(ep.models || []), ...(ep.models_extra || [])].map((m) => (
                <option key={ep.endpoint_id + m} value={ep.endpoint_id + "::" + m}>{m}</option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>

      {(presets || []).length > 0 && (
        <div>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Preset</h2>
          <select value={c.presetId} onChange={(e) => c.setPreset(e.target.value)} className={selectCls}>
            <option value="">None</option>
            {(presets || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
      )}

      <div>
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Mode</h2>
        <div className="flex rounded-lg bg-muted p-0.5">
          {(["chat", "agent"] as const).map((mode) => (
            <button key={mode} onClick={() => c.setMode(mode)} className={cn("flex-1 rounded-md py-1.5 text-sm font-medium capitalize transition-colors", c.mode === mode ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>{mode}</button>
          ))}
        </div>
      </div>

      <div>
        <h2 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Tools</h2>
        <Row label="Web search"><Toggle on={c.useWeb} onClick={() => c.toggle("useWeb")} /></Row>
        <Row label="Deep research"><Toggle on={c.useResearch} onClick={() => c.toggle("useResearch")} /></Row>
        <Row label="Allow bash"><Toggle on={c.allowBash} onClick={() => c.toggle("allowBash")} /></Row>
        <Row label="Memory (RAG)"><Toggle on={c.useRag} onClick={() => c.toggle("useRag")} /></Row>
        <Row label="Incognito"><Toggle on={c.incognito} onClick={() => c.toggle("incognito")} /></Row>
      </div>
    </aside>
  )
}
