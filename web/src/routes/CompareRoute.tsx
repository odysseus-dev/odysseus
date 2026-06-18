import { useState } from "react"
import { GitCompareArrows, Send, Loader2, Trophy } from "lucide-react"
import { useModels } from "@/api/models"
import { startCompare, voteCompare, type CompareStart } from "@/api/compare"
import { streamChat } from "@/lib/sse"
import { Markdown } from "@/components/chat/Markdown"
import { Button } from "@/components/ui/button"
import { toast } from "@/stores/toast"
import { cn } from "@/lib/utils"

interface Sel { model: string; endpointId: string; endpointUrl: string }

function ModelSelect({ value, onChange, label }: { value: Sel; onChange: (s: Sel) => void; label: string }) {
  const { data: models } = useModels()
  const items = models?.items || []
  const onPick = (val: string) => {
    const i = val.indexOf("::"); const epId = val.slice(0, i); const model = val.slice(i + 2)
    const ep = items.find((e) => e.endpoint_id === epId)
    onChange({ model, endpointId: epId, endpointUrl: ep?.url || "" })
  }
  return (
    <div className="flex-1">
      <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-muted-foreground">{label}</label>
      <select value={value.endpointId + "::" + value.model} onChange={(e) => onPick(e.target.value)} className="h-9 w-full rounded-md border bg-background px-2 text-sm outline-none focus-visible:border-ring">
        {!value.model && <option value="::">Select a model…</option>}
        {items.map((ep) => (
          <optgroup key={ep.endpoint_id} label={ep.endpoint_name || ep.url}>
            {[...(ep.models || []), ...(ep.models_extra || [])].map((m) => (
              <option key={ep.endpoint_id + m} value={ep.endpoint_id + "::" + m}>{m}</option>
            ))}
          </optgroup>
        ))}
      </select>
    </div>
  )
}

interface PaneMetrics { tokens_out?: number; tok_per_sec?: number; cost?: number }
function streamPane(sessionId: string, prompt: string, sel: Sel, onDelta: (d: string) => void, onMetrics: (m: PaneMetrics) => void, onError: (msg: string) => void, signal: AbortSignal) {
  const fd = new FormData()
  fd.set("message", prompt)
  fd.set("session", sessionId)
  fd.set("compare_mode", "true")
  fd.set("mode", "chat")
  if (sel.model) fd.set("model", sel.model)
  if (sel.endpointId) fd.set("endpoint_id", sel.endpointId)
  return streamChat(fd, (e) => {
    const ev = e as Record<string, unknown>
    if (typeof ev.delta === "string" && !ev.thinking) onDelta(ev.delta as string)
    else if (e.type === "metrics") {
      // Metrics are nested under `data` and named output_tokens/tokens_per_second.
      const dm = (ev.data as Record<string, unknown>) || ev
      onMetrics({ tokens_out: (dm.output_tokens ?? dm.tokens_out) as number, tok_per_sec: (dm.tokens_per_second ?? dm.tok_per_sec) as number, cost: dm.cost as number })
    } else if (e.type === "error") {
      onError((ev.text as string) || (ev.error as string) || "Model error")
    }
  }, signal)
}

// Module-scope so it isn't recreated each render (which would reset its state).
function Pane({ side, model, body, win, met, running, err }: { side: string; model: string; body: string; win: boolean; met: PaneMetrics | null; running: boolean; err?: string }) {
  return (
    <div className={cn("flex min-h-0 flex-1 flex-col rounded-lg border bg-card", win && "ring-2 ring-primary")}>
      <div className="flex items-center justify-between border-b px-3 py-2">
        <span className="truncate text-sm font-medium">{side}</span>
        <span className="truncate text-xs text-muted-foreground">{model}</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {body ? <Markdown>{body}</Markdown> : err ? <span className="text-sm text-destructive">⚠️ {err}</span> : running ? <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-3.5 animate-spin" />Generating…</div> : <span className="text-sm text-muted-foreground">No output yet.</span>}
      </div>
      {met && (
        <div className="flex flex-wrap gap-3 border-t px-3 py-1.5 text-[11px] text-muted-foreground">
          {met.tokens_out != null && <span>{met.tokens_out} tok</span>}
          {met.tok_per_sec != null && <span>{Math.round(met.tok_per_sec)} tok/s</span>}
          {met.cost != null && <span>${Number(met.cost).toFixed(4)}</span>}
        </div>
      )}
    </div>
  )
}

export function CompareRoute() {
  const [a, setA] = useState<Sel>({ model: "", endpointId: "", endpointUrl: "" })
  const [b, setB] = useState<Sel>({ model: "", endpointId: "", endpointUrl: "" })
  const [prompt, setPrompt] = useState("")
  const [running, setRunning] = useState(false)
  const [left, setLeft] = useState("")
  const [right, setRight] = useState("")
  const [lm, setLm] = useState<PaneMetrics | null>(null)
  const [rm, setRm] = useState<PaneMetrics | null>(null)
  const [comp, setComp] = useState<CompareStart | null>(null)
  const [voted, setVoted] = useState<string | null>(null)
  const [err, setErr] = useState("")
  const [lErr, setLErr] = useState("")
  const [rErr, setRErr] = useState("")

  const run = async () => {
    if (!prompt.trim() || !a.model || !b.model || running) return
    setRunning(true); setErr(""); setLErr(""); setRErr(""); setLeft(""); setRight(""); setLm(null); setRm(null); setComp(null); setVoted(null)
    try {
      const c = await startCompare({
        prompt,
        model_a: a.model, endpoint_a_id: a.endpointId, endpoint_a: a.endpointUrl,
        model_b: b.model, endpoint_b_id: b.endpointId, endpoint_b: b.endpointUrl,
        is_blind: false,
      })
      setComp(c)
      const ctrl = new AbortController()
      // session_left maps to whichever slot; in non-blind mode left=a, right=b.
      // allSettled never rejects, so surface each pane's failure individually
      // (both a pre-stream HTTP error and an in-stream `error` event).
      const [resL, resR] = await Promise.allSettled([
        streamPane(c.session_left, prompt, a, (d) => setLeft((p) => p + d), setLm, setLErr, ctrl.signal),
        streamPane(c.session_right, prompt, b, (d) => setRight((p) => p + d), setRm, setRErr, ctrl.signal),
      ])
      if (resL.status === "rejected") setLErr((p) => p || (resL.reason instanceof Error ? resL.reason.message : "Model A failed"))
      if (resR.status === "rejected") setRErr((p) => p || (resR.reason instanceof Error ? resR.reason.message : "Model B failed"))
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Comparison failed")
    } finally {
      setRunning(false)
    }
  }

  const vote = async (w: "left" | "right" | "tie") => {
    if (!comp || voted) return
    try { await voteCompare(comp.id, w); setVoted(w) } catch { toast("Couldn't record your vote") }
  }

  return (
    <div className="mx-auto flex h-full w-full max-w-5xl flex-col">
      <header className="flex h-13 shrink-0 items-center gap-2 border-b px-4 text-sm font-semibold"><GitCompareArrows className="size-4" />Compare models</header>
      <div className="flex min-h-0 flex-1 flex-col gap-3 p-4">
        <div className="flex gap-3">
          <ModelSelect value={a} onChange={setA} label="Model A" />
          <ModelSelect value={b} onChange={setB} label="Model B" />
        </div>
        <div className="flex items-end gap-2">
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Prompt to send to both models…" rows={2} className="flex-1 resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring" />
          <Button onClick={run} disabled={running || !prompt.trim() || !a.model || !b.model}>{running ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}Run</Button>
        </div>
        {err && <p className="text-xs text-destructive">{err}</p>}
        <div className="flex min-h-0 flex-1 gap-3">
          <Pane side="Model A" model={a.model} body={left} win={voted === "left"} met={lm} running={running} err={lErr} />
          <Pane side="Model B" model={b.model} body={right} win={voted === "right"} met={rm} running={running} err={rErr} />
        </div>
        {comp && !running && (left || right) && (
          <div className="flex items-center justify-center gap-2 pt-1">
            {voted ? (
              <span className="flex items-center gap-1.5 text-sm text-muted-foreground"><Trophy className="size-4" />Voted: {voted}</span>
            ) : (
              <>
                <span className="mr-1 text-sm text-muted-foreground">Which is better?</span>
                <Button variant="outline" size="sm" onClick={() => vote("left")}>Model A</Button>
                <Button variant="outline" size="sm" onClick={() => vote("tie")}>Tie</Button>
                <Button variant="outline" size="sm" onClick={() => vote("right")}>Model B</Button>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
