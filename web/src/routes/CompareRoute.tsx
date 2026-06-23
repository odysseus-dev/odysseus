import { useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import {
  Bot,
  Check,
  ChevronDown,
  Code2,
  Copy,
  Download,
  EyeOff,
  Eye,
  GitCompareArrows,
  History,
  Loader2,
  Maximize2,
  Minimize2,
  MessagesSquare,
  Play,
  Plus,
  Printer,
  RefreshCw,
  RotateCcw,
  Search,
  Send,
  Share2,
  Shuffle,
  Square,
  Trash2,
  Trophy,
  X,
  Zap,
} from "lucide-react"
import { useAuthStatus } from "@/api/auth"
import { useModels } from "@/api/models"
import { createSession, deleteSession } from "@/api/sessions"
import {
  listSearchProviders,
  probeSelectedModels,
  recordCompareVote,
  searchWithProvider,
  startCompare,
  stopChatSession,
  revealCompare,
  useCompareHistory,
  voteCompare,
  type CompareHistoryItem,
  type CompareStart,
  type SearchProviderInfo,
  type SearchProviderResponse,
  type SearchResultItem,
} from "@/api/compare"
import { streamChat } from "@/lib/sse"
import { Markdown } from "@/components/chat/Markdown"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { toast } from "@/stores/toast"
import { cn } from "@/lib/utils"

interface Sel { model: string; endpointId: string; endpointUrl: string }
type CompareMode = "chat" | "agent" | "search" | "research"
type RevealedModels = Record<string, string>
type GradeStatus = "pass" | "fail"
type ProbeStatus = { kind: "ok" | "error" | "info"; text: string }

interface ComparePaneState {
  id: string
  sel: Sel
  synthSel: Sel
  body: string
  met: PaneMetrics | null
  err: string
  elapsedMs: number | null
  grade: GradeStatus | null
  sessionId?: string
}

interface EvalPrompt {
  sub: string
  label: string
  prompt: string
  answer?: string
}

const MODES: Array<{ value: CompareMode; label: string; icon: typeof MessagesSquare }> = [
  { value: "chat", label: "Chat", icon: MessagesSquare },
  { value: "agent", label: "Agent", icon: Bot },
  { value: "search", label: "Search", icon: Search },
  { value: "research", label: "Research", icon: Search },
]

const EVAL_PROMPTS: Record<CompareMode, EvalPrompt[]> = {
  chat: [
    { sub: "Featured", label: "Sum digits 2^100", answer: "115", prompt: "Compute the sum of the decimal digits of 2^100. Do NOT use code execution - work it out by reasoning about the number. Show every step, then end with the final number on its own line." },
    { sub: "Featured", label: "Three jugs", answer: "2 pours: 7->5, 7->3", prompt: "You have three jugs of capacities 7, 5, and 3 liters. The 7-liter jug starts full; the others empty. Using only pouring (no markings), produce the shortest sequence of pours that leaves exactly 2 liters in the 3-liter jug. Output each step as `pour A -> B` on its own line. Then state the total number of pours on a final line." },
    { sub: "Visual", label: "Draw SVG", prompt: "Output a complete self-contained HTML file (```html block, no explanation, no other text) that centers a single SVG illustration on a simple background. The SVG must use only inline shapes - no <img>, no external assets, no JavaScript. Make it expressive and detailed. The SVG should depict: a friendly robot" },
    { sub: "Visual explain", label: "Black hole HTML", prompt: "Output a complete HTML file (```html block, no explanation outside the code) that visually explains how a black hole forms. Use four labeled \"frames\" laid out left-to-right (or stacked on small screens) showing: 1) a glowing massive star, 2) the star going supernova with shockwave rings, 3) collapse into a singularity, 4) the final black hole with a curved accretion disk and bent light around it. Use only vanilla HTML, CSS, and inline SVG - no JavaScript, no images. Each frame should have a one-sentence caption." },
    { sub: "Visual explain", label: "Butterfly ASCII", prompt: "Explain the butterfly lifecycle using ASCII art. Produce four separate frames in fenced code blocks, in order: egg, caterpillar, chrysalis, adult butterfly. Each frame must be drawn with monospace ASCII characters only and be visually recognizable as the creature/stage. Below each frame add one playful one-line caption (no longer than 15 words) describing what is happening at that stage." },
  ],
  agent: [
    { sub: "Web tasks", label: "Multi-step", prompt: "Search the web for the current population of the 3 largest cities in the world, then calculate what percentage of the world's total population lives in those cities." },
    { sub: "Web tasks", label: "Fact check", prompt: "Fact-check these claims: 1) The Great Wall of China is visible from space. 2) Humans only use 10% of their brains. 3) Lightning never strikes the same place twice. Cite sources." },
    { sub: "Web tasks", label: "Compare prices", prompt: "Find and compare the pricing, features, and limitations of the top 3 cloud GPU providers for machine learning training. Create a markdown comparison table." },
    { sub: "Code tasks", label: "Script + run", prompt: "Write a Python script that generates a bar chart of the 5 most common programming languages in 2025 and save it as chart.png. Then run it." },
    { sub: "Math", label: "Proof + verify", prompt: "Prove that the square root of 2 is irrational. Then write a Python program that approximates it using Newton's method to 50 decimal places and verify." },
  ],
  search: [
    { sub: "Factual", label: "Current events", prompt: "latest AI regulation news 2026" },
    { sub: "Technical", label: "Programming", prompt: "Rust vs Go performance benchmarks 2026" },
    { sub: "Comparison", label: "GPU providers", prompt: "cloud GPU providers pricing comparison 2026" },
    { sub: "Science", label: "CRISPR therapy", prompt: "CRISPR gene therapy breakthroughs" },
    { sub: "Market", label: "Laptop deals", prompt: "best lightweight laptops for developers 2026" },
  ],
  research: [
    { sub: "Factual", label: "Current events", prompt: "latest AI regulation news 2025" },
    { sub: "Technical", label: "Programming", prompt: "Rust vs Go performance benchmarks 2025" },
    { sub: "Research", label: "Academic", prompt: "transformer architecture improvements since attention is all you need" },
    { sub: "Comparison", label: "GPU providers", prompt: "cloud GPU providers pricing comparison 2025" },
    { sub: "Factual", label: "Science", prompt: "CRISPR gene therapy breakthroughs" },
  ],
}

const IMAGE_MODEL_PREFIXES = ["dall-e", "gpt-image", "chatgpt-image", "stable-diffusion", "sdxl", "flux", "midjourney"]
const MIN_COMPARE_PANES = 2
const MAX_COMPARE_PANES = 8
const EMPTY_SEL: Sel = { model: "", endpointId: "", endpointUrl: "" }

function isImageModel(model: string) {
  const lower = model.toLowerCase()
  return IMAGE_MODEL_PREFIXES.some((prefix) => lower.includes(prefix))
}

function newPane(id?: string, sel: Sel = EMPTY_SEL, synthSel: Sel = EMPTY_SEL): ComparePaneState {
  return {
    id: id || `pane-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    sel: { ...sel },
    synthSel: { ...synthSel },
    body: "",
    met: null,
    err: "",
    elapsedMs: null,
    grade: null,
  }
}

function clearPaneRun(pane: ComparePaneState): ComparePaneState {
  return {
    ...pane,
    body: "",
    met: null,
    err: "",
    elapsedMs: null,
    grade: null,
    sessionId: undefined,
  }
}

function paneSlot(index: number, parallel: boolean) {
  return parallel ? String.fromCharCode(65 + index) : String(index + 1)
}

function paneLabel(index: number, parallel: boolean) {
  return `Model ${paneSlot(index, parallel)}`
}

function ModelSelect({
  value,
  onChange,
  label,
  allowEmpty = false,
  emptyLabel = "Select a model...",
}: {
  value: Sel
  onChange: (s: Sel) => void
  label: string
  allowEmpty?: boolean
  emptyLabel?: string
}) {
  const { data: models } = useModels()
  const items = models?.items || []
  const onPick = (val: string) => {
    if (val === "::") {
      onChange({ ...EMPTY_SEL })
      return
    }
    const i = val.indexOf("::"); const epId = val.slice(0, i); const model = val.slice(i + 2)
    const ep = items.find((e) => e.endpoint_id === epId)
    onChange({ model, endpointId: epId, endpointUrl: ep?.url || "" })
  }
  return (
    <div className="flex-1">
      <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-muted-foreground">{label}</label>
      <select value={value.endpointId + "::" + value.model} onChange={(e) => onPick(e.target.value)} className="h-9 w-full rounded-md border bg-background px-2 text-sm outline-none focus-visible:border-ring">
        {(allowEmpty || !value.model) && <option value="::">{emptyLabel}</option>}
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

function ProviderSelect({
  value,
  onChange,
  label,
  providers,
  loading,
  disabled,
}: {
  value: Sel
  onChange: (s: Sel) => void
  label: string
  providers: SearchProviderInfo[]
  loading: boolean
  disabled: boolean
}) {
  const onPick = (providerId: string) => {
    onChange({ model: providerId, endpointId: "", endpointUrl: "" })
  }
  return (
    <div className="flex-1">
      <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-muted-foreground">{label}</label>
      <select
        value={value.model}
        onChange={(e) => onPick(e.target.value)}
        disabled={disabled || loading || providers.length === 0}
        className="h-9 w-full rounded-md border bg-background px-2 text-sm outline-none focus-visible:border-ring disabled:opacity-50"
      >
        {!value.model && <option value="">{loading ? "Loading providers..." : "Select a provider..."}</option>}
        {providers.map((provider) => (
          <option key={provider.id} value={provider.id}>{provider.label}</option>
        ))}
      </select>
    </div>
  )
}

interface PaneMetrics { tokens_out?: number; tok_per_sec?: number; cost?: number; results?: number; time?: number; context_percent?: number }
async function streamPane(
  sessionId: string,
  prompt: string,
  mode: CompareMode,
  onDelta: (d: string) => void,
  onMetrics: (m: PaneMetrics) => void,
  onError: (msg: string) => void,
  controller: AbortController,
  timeoutSeconds: number,
) {
  const fd = new FormData()
  fd.set("message", prompt)
  fd.set("session", sessionId)
  fd.set("compare_mode", "true")
  fd.set("no_documents", "true")
  fd.set("no_memory", "true")
  fd.set("use_rag", "false")
  if (mode === "agent") {
    fd.set("mode", "agent")
    fd.set("allow_web_search", "true")
    fd.set("allow_bash", "true")
  } else {
    fd.set("mode", "chat")
    if (mode === "research") fd.set("use_research", "true")
  }

  let timedOut = false
  let timeoutId: number | undefined
  const resetTimeout = () => {
    if (timeoutId) window.clearTimeout(timeoutId)
    timeoutId = window.setTimeout(() => {
      timedOut = true
      controller.abort()
    }, Math.max(15, timeoutSeconds) * 1000)
  }

  resetTimeout()
  try {
    await streamChat(fd, (e) => {
      resetTimeout()
      const ev = e as Record<string, unknown>
      if (typeof ev.delta === "string" && !ev.thinking) onDelta(ev.delta as string)
      else if (e.type === "metrics") {
        const dm = (ev.data as Record<string, unknown>) || ev
        onMetrics({ tokens_out: (dm.output_tokens ?? dm.tokens_out) as number, tok_per_sec: (dm.tokens_per_second ?? dm.tok_per_sec) as number, cost: dm.cost as number, context_percent: (dm.context_percent ?? dm.context_pct) as number })
      } else if (e.type === "error") {
        onError((ev.text as string) || (ev.error as string) || "Model error")
      }
    }, controller.signal)
  } catch (e) {
    if (controller.signal.aborted) throw new Error(timedOut ? `Timed out after ${timeoutSeconds}s` : "Stopped", { cause: e })
    throw e
  } finally {
    if (timeoutId) window.clearTimeout(timeoutId)
  }
}

function shortName(model: string) {
  return model.split("/").pop() || model
}

function markdownEscape(text: string) {
  return text.replace(/([\\`*_{}[\]()#+\-.!|>])/g, "\\$1")
}

function formatSearchResult(result: SearchResultItem, index: number) {
  const title = String(result.title || result.url || `Result ${index + 1}`)
  const url = typeof result.url === "string" ? result.url : ""
  const snippet = typeof result.snippet === "string" ? result.snippet.trim() : ""
  const heading = url ? `### ${index + 1}. [${markdownEscape(title)}](${url})` : `### ${index + 1}. ${markdownEscape(title)}`
  const parts = [heading]
  if (snippet) parts.push(markdownEscape(snippet))
  if (url) parts.push(`<${url}>`)
  return parts.join("\n\n")
}

function formatSearchResponse(data: SearchProviderResponse) {
  if (data.error) return `**Error:** ${markdownEscape(data.error)}`
  const results = data.results || []
  if (results.length === 0) return "_No results found._"
  return results.map(formatSearchResult).join("\n\n")
}

function searchResultCount(data: SearchProviderResponse) {
  return Array.isArray(data.results) ? data.results.length : 0
}

function searchResultsForSynthesis(data: SearchProviderResponse) {
  return (data.results || []).map((result, index) => {
    const title = String(result.title || result.url || `Result ${index + 1}`)
    const snippet = typeof result.snippet === "string" ? result.snippet : ""
    const url = typeof result.url === "string" ? result.url : ""
    return `[${index + 1}] ${title}\n${snippet}\nURL: ${url}`
  }).join("\n\n")
}

function buildSearchSynthesisPrompt(query: string, data: SearchProviderResponse) {
  return `Analyze these search results for the query "${query}". Summarize the key findings, note any consensus or conflicting information, and provide a brief synthesis.\n\nSearch Results:\n${searchResultsForSynthesis(data)}`
}

function extractHtmlFromText(text: string): string | null {
  const fenceRe = /`{3,}(?:html)?\s*\r?\n([\s\S]*?)`{3,}/gi
  let match: RegExpExecArray | null
  while ((match = fenceRe.exec(text)) !== null) {
    const code = match[1].trim()
    if (/<!doctype\s+html|<html[\s>]/i.test(code)) return code
  }
  const bare = text.match(/(<!doctype\s+html[\s\S]*<\/html>)/i) || text.match(/(<html[\s>][\s\S]*<\/html>)/i)
  return bare ? bare[1].trim() : null
}

function formatElapsed(ms: number) {
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`
}

function paneMetricsLine(met: PaneMetrics | null, elapsedMs: number | null) {
  const parts: string[] = []
  if (met?.results != null) parts.push(`${met.results} results`)
  if (met?.time != null) parts.push(`${Number(met.time).toFixed(2)}s search`)
  if (met?.tokens_out != null) parts.push(`${met.tokens_out} tok`)
  if (met?.tok_per_sec != null) parts.push(`${Math.round(met.tok_per_sec)} tok/s`)
  if (met?.context_percent != null) parts.push(`${Number(met.context_percent).toFixed(met.context_percent < 10 ? 1 : 0)}% ctx`)
  if (met?.cost != null) parts.push(`$${Number(met.cost).toFixed(4)} · $${(Number(met.cost) * 1000).toFixed(2)}/1k`)
  if (elapsedMs != null) parts.push(formatElapsed(elapsedMs))
  return parts.join(" · ")
}

// Build a self-contained Markdown document of the current comparison:
// the shared prompt, then each pane's label, model, metrics, and response.
function buildComparisonMarkdown(
  prompt: string,
  panes: ComparePaneState[],
  modelLabel: (pane: ComparePaneState, index: number) => string,
) {
  const lines: string[] = ["# Model comparison", ""]
  if (prompt.trim()) {
    lines.push("## Prompt", "", prompt.trim(), "")
  }
  panes.forEach((pane, index) => {
    lines.push(`## ${modelLabel(pane, index)}`, "")
    const meta = paneMetricsLine(pane.met, pane.elapsedMs)
    if (meta) lines.push(`_${meta}_`, "")
    if (pane.body) lines.push(pane.body.trim(), "")
    else if (pane.err) lines.push(`**Error:** ${pane.err}`, "")
    else lines.push("_No output._", "")
  })
  return lines.join("\n").replace(/\n{3,}/g, "\n\n").trimEnd() + "\n"
}

function nowMs() {
  return performance.now()
}

function gradeResponse(response: string, expected: string): GradeStatus | null {
  const norm = (value: string) => value.toLowerCase().replace(/\s+/g, " ").trim()
  const r = norm(response)
  const e = norm(expected)
  if (!r || !e) return null
  if (e.includes("yourself") || e.includes("verify") || e.length > 120) return null

  let pass = r.includes(e)
  if (!pass) {
    const match = expected.match(/-?\d[\d,]*(?:\.\d+)?/)
    if (match) {
      const number = match[0].replace(/,/g, "")
      const escaped = number.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
      pass = new RegExp(`(^|[^\\d.])${escaped}(?![\\d.])`).test(response)
    }
  }
  return pass ? "pass" : "fail"
}

function groupedEvalPrompts(mode: CompareMode) {
  const groups: Array<{ sub: string; items: Array<EvalPrompt & { index: number }> }> = []
  EVAL_PROMPTS[mode].forEach((prompt, index) => {
    let group = groups.find((item) => item.sub === prompt.sub)
    if (!group) {
      group = { sub: prompt.sub, items: [] }
      groups.push(group)
    }
    group.items.push({ ...prompt, index })
  })
  return groups
}

function EvalPromptSelect({ mode, disabled, onPick }: { mode: CompareMode; disabled: boolean; onPick: (prompt: EvalPrompt) => void }) {
  const groups = useMemo(() => groupedEvalPrompts(mode), [mode])
  return (
    <select
      aria-label="Eval prompts"
      value=""
      disabled={disabled}
      onChange={(e) => {
        const index = Number(e.target.value)
        const item = Number.isFinite(index) ? EVAL_PROMPTS[mode][index] : undefined
        if (item) onPick(item)
      }}
      className="h-9 w-full rounded-md border bg-background px-2 text-sm text-muted-foreground outline-none focus-visible:border-ring disabled:opacity-50 md:w-44"
    >
      <option value="">Eval prompts</option>
      {groups.map((group) => (
        <optgroup key={group.sub} label={group.sub}>
          {group.items.map((item) => (
            <option key={`${item.sub}-${item.label}`} value={item.index}>
              {item.label}{item.answer ? " ✓" : ""}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  )
}

function winnerModel(item: CompareHistoryItem) {
  if (!item.winner) return ""
  if (item.winner === "tie") return "tie"
  if (item.winner === "a") return item.model_a
  if (item.winner === "b") return item.model_b
  return item.winner
}

function Scoreboard({ items }: { items: CompareHistoryItem[] }) {
  const rows = useMemo(() => {
    const byModel = new Map<string, { model: string; wins: number; losses: number; ties: number; games: number }>()
    const ensure = (model: string) => {
      if (!byModel.has(model)) byModel.set(model, { model, wins: 0, losses: 0, ties: 0, games: 0 })
      return byModel.get(model)!
    }

    items.filter((item) => item.winner).forEach((item) => {
      const a = ensure(item.model_a)
      const b = ensure(item.model_b)
      a.games += 1
      b.games += 1
      const winner = winnerModel(item)
      if (winner === "tie") {
        a.ties += 1
        b.ties += 1
      } else if (winner === item.model_a) {
        a.wins += 1
        b.losses += 1
      } else if (winner === item.model_b) {
        b.wins += 1
        a.losses += 1
      }
    })

    return [...byModel.values()].sort((x, y) => (y.wins / Math.max(1, y.games)) - (x.wins / Math.max(1, x.games)) || y.games - x.games).slice(0, 8)
  }, [items])
  const recent = items.filter((item) => item.winner).slice(0, 6)

  return (
    <div className="grid max-h-64 shrink-0 gap-3 overflow-hidden rounded-md border bg-card p-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
      <section className="min-w-0 overflow-hidden">
        <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><Trophy className="size-3.5" />Scoreboard</div>
        <div className="overflow-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-muted-foreground">
              <tr><th className="pb-1 font-medium">Model</th><th className="pb-1 font-medium">W</th><th className="pb-1 font-medium">L</th><th className="pb-1 font-medium">T</th><th className="pb-1 font-medium">Win</th></tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr><td className="py-3 text-muted-foreground" colSpan={5}>No votes yet.</td></tr>
              ) : rows.map((row) => (
                <tr key={row.model} className="border-t">
                  <td className="max-w-44 truncate py-1.5 pr-2" title={row.model}>{shortName(row.model)}</td>
                  <td className="py-1.5">{row.wins}</td>
                  <td className="py-1.5">{row.losses}</td>
                  <td className="py-1.5">{row.ties}</td>
                  <td className="py-1.5">{Math.round((row.wins / Math.max(1, row.games)) * 100)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="min-w-0 overflow-hidden">
        <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><History className="size-3.5" />Recent Votes</div>
        <div className="max-h-48 space-y-1.5 overflow-auto pr-1">
          {recent.length === 0 ? (
            <div className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">No completed comparisons.</div>
          ) : recent.map((item) => {
            const winner = winnerModel(item)
            return (
              <div key={item.id} className="rounded-md border bg-background px-2.5 py-2 text-xs">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="truncate font-medium" title={`${item.model_a} vs ${item.model_b}`}>{shortName(item.model_a)} vs {shortName(item.model_b)}</span>
                  <span className="ml-auto shrink-0 text-muted-foreground">{winner === "tie" ? "Tie" : shortName(winner)}</span>
                </div>
                <div className="mt-1 truncate text-muted-foreground" title={item.prompt}>{item.prompt}</div>
              </div>
            )
          })}
        </div>
      </section>
    </div>
  )
}

function Pane({
  side,
  model,
  body,
  win,
  met,
  running,
  err,
  onCopy,
  copied,
  onExpand,
  expanded,
  hidden,
  fastest,
  elapsedMs,
  previewOpen,
  onTogglePreview,
  onReroll,
  canReroll,
  rerolling,
  grade,
  activityLabel = "Generating...",
  onStop,
  canStop,
}: {
  side: string
  model: string
  body: string
  win: boolean
  met: PaneMetrics | null
  running: boolean
  err?: string
  onCopy: () => void
  copied: boolean
  onExpand: () => void
  expanded: boolean
  hidden: boolean
  fastest: boolean
  elapsedMs: number | null
  previewOpen: boolean
  onTogglePreview: () => void
  onReroll: () => void
  canReroll: boolean
  rerolling: boolean
  grade: GradeStatus | null
  activityLabel?: string
  onStop: () => void
  canStop: boolean
}) {
  const htmlPreview = useMemo(() => extractHtmlFromText(body), [body])
  const previewActive = previewOpen && !!htmlPreview

  return (
    <div className={cn("flex min-h-0 min-w-0 flex-1 flex-col rounded-md border bg-card", win && "ring-2 ring-primary", hidden && "hidden")}>
      <div className="flex items-center justify-between border-b px-3 py-2">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-1.5">
            <span className="truncate text-sm font-medium">{side}</span>
            {grade && (
              <span
                title={grade === "pass" ? "Response contains the expected answer" : "Expected answer not found in response"}
                className={cn(
                  "inline-flex size-5 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold",
                  grade === "pass" ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" : "bg-destructive/15 text-destructive",
                )}
              >
                {grade === "pass" ? <Check className="size-3" /> : <X className="size-3" />}
              </span>
            )}
            {fastest && <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-primary"><Zap className="size-3" />Fastest</span>}
          </div>
          <div className="truncate text-xs text-muted-foreground" title={model}>{model}</div>
        </div>
        <div className="ml-2 flex shrink-0 flex-wrap items-center justify-end gap-0.5 md:flex-nowrap">
          {canStop && (
            <button onClick={onStop} title="Stop this model" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-destructive">
              <Square className="size-3.5" />
            </button>
          )}
          {htmlPreview && (
            <button onClick={onTogglePreview} title={previewActive ? "Show code" : "Run preview"} className={cn("rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground", previewActive && "text-primary")}>
              {previewActive ? <Code2 className="size-3.5" /> : <Play className="size-3.5" />}
            </button>
          )}
          <button onClick={onReroll} disabled={!canReroll} title="Re-roll response" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-35">
            {rerolling ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
          </button>
          <button onClick={onCopy} disabled={!body} title="Copy response" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-35">
            {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
          </button>
          <button onClick={onExpand} title={expanded ? "Collapse pane" : "Expand pane"} className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">
            {expanded ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {previewActive && htmlPreview ? (
          <iframe title={`${side} HTML preview`} sandbox="allow-scripts" srcDoc={htmlPreview} className="min-h-[24rem] w-full rounded-md border bg-white" />
        ) : body ? <Markdown>{body}</Markdown> : err ? <span className="text-sm text-destructive">{err}</span> : running ? <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-3.5 animate-spin" />{activityLabel}</div> : <span className="text-sm text-muted-foreground">No output yet.</span>}
      </div>
      {(met || elapsedMs != null) && (
        <div className="flex flex-wrap gap-3 border-t px-3 py-1.5 text-[11px] text-muted-foreground">
          {met?.results != null && <span>{met.results} results</span>}
          {met?.time != null && <span>{Number(met.time).toFixed(2)}s search</span>}
          {met?.tokens_out != null && <span>{met.tokens_out} tok</span>}
          {met?.tok_per_sec != null && <span>{Math.round(met.tok_per_sec)} tok/s</span>}
          {met?.context_percent != null && <span title="Share of the model's context window used by the prompt">{Number(met.context_percent).toFixed(met.context_percent < 10 ? 1 : 0)}% ctx</span>}
          {met?.cost != null && <span title="Estimated total · equivalent cost for 1,000 responses">${Number(met.cost).toFixed(4)} · ${(Number(met.cost) * 1000).toFixed(2)}/1k</span>}
          {elapsedMs != null && <span>{formatElapsed(elapsedMs)}</span>}
        </div>
      )}
    </div>
  )
}

export function CompareRoute() {
  const qc = useQueryClient()
  const { data: auth } = useAuthStatus()
  const history = useCompareHistory()
  const [panes, setPanes] = useState<ComparePaneState[]>(() => [newPane("pane-a"), newPane("pane-b")])
  const [prompt, setPrompt] = useState("")
  const [mode, setMode] = useState<CompareMode>("chat")
  const [blind, setBlind] = useState(true)
  const [parallel, setParallel] = useState(true)
  const [saveOnClose, setSaveOnClose] = useState(false)
  const [timeoutSeconds, setTimeoutSeconds] = useState(300)
  const [scoreOpen, setScoreOpen] = useState(false)
  const [running, setRunning] = useState(false)
  const [comp, setComp] = useState<CompareStart | null>(null)
  const [voted, setVoted] = useState<string | null>(null)
  const [revealed, setRevealed] = useState<RevealedModels | null>(null)
  const [err, setErr] = useState("")
  const [copied, setCopied] = useState<string | null>(null)
  const [expandedPane, setExpandedPane] = useState<string | null>(null)
  const [previewPane, setPreviewPane] = useState<string | null>(null)
  const [revealing, setRevealing] = useState(false)
  const [rerolling, setRerolling] = useState<string | null>(null)
  const [expectedAnswer, setExpectedAnswer] = useState("")
  const [probing, setProbing] = useState(false)
  const [probeStatus, setProbeStatus] = useState<ProbeStatus | null>(null)
  const [probedModels, setProbedModels] = useState<Set<string>>(() => new Set())
  const [searchProviders, setSearchProviders] = useState<SearchProviderInfo[]>([])
  const [providersLoading, setProvidersLoading] = useState(false)
  const [providersError, setProvidersError] = useState("")
  const [exportOpen, setExportOpen] = useState(false)
  const exportMenuRef = useRef<HTMLDivElement | null>(null)
  const controllersRef = useRef<Record<string, AbortController | null>>({})
  const sessionIdsRef = useRef<string[]>([])
  const saveOnCloseRef = useRef(false)
  const panesRef = useRef<ComparePaneState[]>(panes)
  const expectedAnswerRef = useRef("")
  const searchProvidersRequestedRef = useRef(false)
  const streamBusy = running || rerolling !== null
  const anyBusy = streamBusy || probing
  const selectedModels = useMemo(() => mode === "search" ? [] : panes.map((pane) => pane.sel).filter((sel) => !!sel.model), [mode, panes])
  const unprobedModels = useMemo(() => selectedModels.filter((sel) => !probedModels.has(sel.model)), [selectedModels, probedModels])
  const canProbeModels = !!auth?.is_admin
  const availableSearchProviders = useMemo(() => searchProviders.filter((provider) => provider.available), [searchProviders])
  const searchProviderIds = useMemo(() => new Set(availableSearchProviders.map((provider) => provider.id)), [availableSearchProviders])
  const providerLabelById = useMemo(() => new Map(searchProviders.map((provider) => [provider.id, provider.label])), [searchProviders])
  const providerLabel = (providerId: string) => providerLabelById.get(providerId) || providerId
  const paneModelName = (pane: ComparePaneState) => mode === "search" ? providerLabel(pane.sel.model) : pane.sel.model
  const allPanesReady = panes.length >= MIN_COMPARE_PANES && panes.every((pane) => (
    mode === "search" ? searchProviderIds.has(pane.sel.model) : !!pane.sel.model
  ))
  const roundStarted = panes.some((pane) => !!pane.sessionId || !!pane.body || !!pane.err)
  const canVote = roundStarted && !anyBusy && panes.some((pane) => !!pane.body || !!pane.err)
  const canShuffle = !anyBusy && !roundStarted && panes.length > 1
  const fastestPane = !anyBusy && panes.length > 0 && panes.every((pane) => pane.elapsedMs != null)
    ? panes.reduce((best, pane) => (pane.elapsedMs! < best.elapsedMs! ? pane : best), panes[0])
    : null

  const patchPane = (paneId: string, patch: Partial<ComparePaneState>) => {
    setPanes((prev) => prev.map((pane) => (pane.id === paneId ? { ...pane, ...patch } : pane)))
  }

  const updatePane = (paneId: string, updater: (pane: ComparePaneState) => ComparePaneState) => {
    setPanes((prev) => prev.map((pane) => (pane.id === paneId ? updater(pane) : pane)))
  }

  const currentSessionIds = () => panes.map((pane) => pane.sessionId).filter((id): id is string => !!id)

  const abortControllers = () => {
    Object.values(controllersRef.current).forEach((ctrl) => ctrl?.abort())
  }

  const cleanupSessions = async (ids: string[]) => {
    const unique = [...new Set(ids.filter(Boolean))]
    if (unique.length === 0) return
    await Promise.allSettled(unique.map((id) => deleteSession(id)))
  }

  const clearRoundState = (sessionIds: string[] = currentSessionIds(), options: { resetSelections?: boolean; searchProviders?: SearchProviderInfo[] } = {}) => {
    abortControllers()
    if (sessionIds.length > 0) void cleanupSessions(sessionIds)
    controllersRef.current = {}
    setRunning(false)
    setErr("")
    setComp(null)
    setVoted(null)
    setRevealed(null)
    setCopied(null)
    setExpandedPane(null)
    setPreviewPane(null)
    setRevealing(false)
    setRerolling(null)
    const resetProviders = options.searchProviders || []
    setPanes((prev) => prev.map((pane, index) => {
      const provider = resetProviders[Math.min(index, resetProviders.length - 1)]
      const sel = options.resetSelections
        ? provider ? { model: provider.id, endpointId: "", endpointUrl: "" } : { ...EMPTY_SEL }
        : pane.sel
      const synthSel = options.resetSelections ? { ...EMPTY_SEL } : pane.synthSel
      return clearPaneRun({ ...pane, sel, synthSel })
    }))
  }

  useEffect(() => {
    expectedAnswerRef.current = expectedAnswer
  }, [expectedAnswer])

  useEffect(() => {
    if (!exportOpen) return
    const onDocClick = (e: MouseEvent) => {
      if (exportMenuRef.current && !exportMenuRef.current.contains(e.target as Node)) setExportOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setExportOpen(false) }
    document.addEventListener("mousedown", onDocClick)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", onDocClick)
      document.removeEventListener("keydown", onKey)
    }
  }, [exportOpen])

  useEffect(() => {
    sessionIdsRef.current = panes.map((pane) => pane.sessionId).filter((id): id is string => !!id)
    panesRef.current = panes
  }, [panes])

  useEffect(() => { saveOnCloseRef.current = saveOnClose }, [saveOnClose])

  useEffect(() => {
    const onBeforeUnload = () => {
      const ids = sessionIdsRef.current
      if (ids.length === 0) return
      if (saveOnCloseRef.current) {
        const names = panesRef.current.map((pane) => shortName(pane.sel.model)).filter(Boolean)
        const folder = `Compare: ${names.join(" vs ") || "Saved"}`
        ids.forEach((id) => { const body = new URLSearchParams({ folder }); void fetch(`/api/session/${id}`, { method: "PATCH", credentials: "same-origin", body, keepalive: true }) })
        return
      }
      if (!navigator.sendBeacon) return
      navigator.sendBeacon(
        "/api/sessions/bulk-delete",
        new Blob([JSON.stringify({ ids })], { type: "application/json" }),
      )
    }
    window.addEventListener("beforeunload", onBeforeUnload)
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload)
      abortControllers()
      const ids = sessionIdsRef.current
      if (saveOnCloseRef.current) {
        const names = panesRef.current.map((pane) => shortName(pane.sel.model)).filter(Boolean)
        const folder = `Compare: ${names.join(" vs ") || "Saved"}`
        ids.forEach((id) => { const body = new FormData(); body.set("folder", folder); void fetch(`/api/session/${id}`, { method: "PATCH", credentials: "same-origin", body }).catch(() => {}) })
      } else ids.forEach((id) => { void deleteSession(id).catch(() => {}) })
    }
  }, [])

  useEffect(() => {
    if (mode !== "search" || searchProvidersRequestedRef.current) return
    let active = true
    searchProvidersRequestedRef.current = true
    setProvidersLoading(true)
    setProvidersError("")
    listSearchProviders().then((providers) => {
      if (!active) return
      setSearchProviders(providers)
      const available = providers.filter((provider) => provider.available)
      if (available.length > 0) {
        setPanes((prev) => prev.map((pane, index) => {
          if (available.some((provider) => provider.id === pane.sel.model)) return pane
          const provider = available[Math.min(index, available.length - 1)]
          return clearPaneRun({ ...pane, sel: { model: provider.id, endpointId: "", endpointUrl: "" } })
        }))
      }
    }).catch((e) => {
      if (!active) return
      setProvidersError(e instanceof Error ? e.message : "Failed to load search providers")
    }).finally(() => {
      if (active) setProvidersLoading(false)
    })
    return () => { active = false }
  }, [mode])

  const streamPaneById = async (paneId: string, sessionId: string, controller: AbortController) => {
    patchPane(paneId, { body: "", met: null, err: "", elapsedMs: null, grade: null })
    setCopied((pane) => pane === paneId ? null : pane)
    setPreviewPane((pane) => pane === paneId ? null : pane)
    const started = nowMs()
    let output = ""
    await streamPane(sessionId, prompt, mode, (d) => {
      output += d
      updatePane(paneId, (pane) => ({ ...pane, body: pane.body + d }))
    }, (metrics) => patchPane(paneId, { met: metrics }), (message) => patchPane(paneId, { err: message }), controller, timeoutSeconds)
    patchPane(paneId, { elapsedMs: nowMs() - started })
    const expected = expectedAnswerRef.current
    if (expected) patchPane(paneId, { grade: gradeResponse(output, expected) })
  }

  const appendPaneBody = (paneId: string, text: string) => {
    updatePane(paneId, (pane) => ({ ...pane, body: pane.body + text }))
  }

  const streamSearchSynthesis = async (pane: ComparePaneState, data: SearchProviderResponse, controller: AbortController) => {
    if (!pane.synthSel.model || data.error || searchResultCount(data) === 0) return
    let sessionId = ""
    try {
      const session = await createSession({
        name: `[CMP] Search analysis ${shortName(pane.synthSel.model)}`,
        model: pane.synthSel.model,
        endpoint_id: pane.synthSel.endpointId,
        endpoint_url: pane.synthSel.endpointUrl,
        skip_validation: !!pane.synthSel.endpointId,
      })
      sessionId = session.id
      appendPaneBody(pane.id, "\n\n---\n\n## Analysis\n\n")
      await streamPane(session.id, buildSearchSynthesisPrompt(prompt, data), "chat", (delta) => {
        appendPaneBody(pane.id, delta)
      }, (metrics) => {
        updatePane(pane.id, (current) => ({ ...current, met: { ...(current.met || {}), ...metrics } }))
      }, (message) => {
        appendPaneBody(pane.id, `\n\n**Analysis error:** ${markdownEscape(message)}`)
      }, controller, timeoutSeconds)
    } catch (e) {
      const message = controller.signal.aborted ? "Analysis stopped" : e instanceof Error ? e.message : "Analysis failed"
      appendPaneBody(pane.id, `\n\n**${markdownEscape(message)}**`)
    } finally {
      if (sessionId) await deleteSession(sessionId).catch(() => {})
    }
  }

  const searchPaneById = async (pane: ComparePaneState, controller: AbortController) => {
    const paneId = pane.id
    patchPane(paneId, { body: "", met: null, err: "", elapsedMs: null, grade: null })
    setCopied((pane) => pane === paneId ? null : pane)
    setPreviewPane((pane) => pane === paneId ? null : pane)
    const started = nowMs()
    try {
      const data = await searchWithProvider(prompt, pane.sel.model, 10, controller.signal)
      patchPane(paneId, {
        body: data.error ? "" : formatSearchResponse(data),
        err: data.error || "",
        met: { results: searchResultCount(data), time: data.time },
        elapsedMs: nowMs() - started,
      })
      if (!data.error) await streamSearchSynthesis(pane, data, controller)
      patchPane(paneId, { elapsedMs: nowMs() - started })
    } catch (e) {
      const message = controller.signal.aborted ? "Stopped" : e instanceof Error ? e.message : "Search failed"
      patchPane(paneId, { err: message, elapsedMs: nowMs() - started })
    }
  }

  const probeModels = async () => {
    if (!canProbeModels || anyBusy || selectedModels.length === 0) return
    const pending = unprobedModels
    if (pending.length === 0) {
      const message = "All selected models verified"
      setProbeStatus({ kind: "ok", text: message })
      toast(message, "success")
      return
    }

    setProbing(true)
    setProbeStatus({ kind: "info", text: `Checking ${pending.length} model${pending.length === 1 ? "" : "s"}...` })
    try {
      const skipped = pending.filter((sel) => isImageModel(sel.model))
      const toProbe = pending.filter((sel) => !isImageModel(sel.model))
      const results = toProbe.length > 0
        ? await probeSelectedModels(toProbe.map((sel) => ({
          endpoint_id: sel.endpointId,
          model: sel.model,
          endpoint: sel.endpointUrl,
          with_tools: mode === "agent",
        })))
        : []
      const passed = results.filter((result) => result.status === "ok" && result.model)
      const failed = results.filter((result) => result.status !== "ok")
      const verifiedCount = passed.length + skipped.length

      setProbedModels((prev) => {
        const next = new Set(prev)
        skipped.forEach((sel) => next.add(sel.model))
        passed.forEach((result) => { if (result.model) next.add(result.model) })
        return next
      })

      if (failed.length > 0) {
        const first = failed[0]
        const label = blind ? "A model" : shortName(first.model || "model")
        const detail = first.error ? `: ${first.error}` : ""
        const message = `${failed.length} model${failed.length === 1 ? "" : "s"} failed. ${label}${detail}`
        setProbeStatus({ kind: "error", text: message })
        toast(message, "error", 5000)
      } else {
        const message = `${verifiedCount} model${verifiedCount === 1 ? "" : "s"} verified`
        setProbeStatus({ kind: "ok", text: message })
        toast(message, "success")
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : "Probe failed"
      setProbeStatus({ kind: "error", text: message })
      toast(message, "error", 5000)
    } finally {
      setProbing(false)
    }
  }

  const run = async () => {
    if (!prompt.trim() || !allPanesReady || anyBusy) return
    const roundPanes = panes.map((pane) => ({ ...pane, sel: { ...pane.sel }, synthSel: { ...pane.synthSel } }))
    const oldSessionIds = roundPanes.map((pane) => pane.sessionId).filter((id): id is string => !!id)
    setRunning(true)
    setErr("")
    setComp(null)
    setVoted(null)
    setRevealed(null)
    setExpandedPane(null)
    setPreviewPane(null)
    setRevealing(false)
    setRerolling(null)
    setPanes((prev) => prev.map(clearPaneRun))
    try {
      await cleanupSessions(oldSessionIds)
      if (mode === "search") {
        const controllers: Record<string, AbortController> = {}
        roundPanes.forEach((pane) => { controllers[pane.id] = new AbortController() })
        controllersRef.current = controllers
        const runPane = async (pane: ComparePaneState) => {
          const ctrl = controllers[pane.id]
          if (!ctrl) return
          await searchPaneById(pane, ctrl)
        }

        if (parallel) {
          await Promise.allSettled(roundPanes.map(runPane))
        } else {
          for (const pane of roundPanes) {
            const ctrl = controllers[pane.id]
            if (!ctrl || ctrl.signal.aborted) break
            await runPane(pane)
          }
        }
        return
      }

      const sessions: Record<string, string> = {}

      if (roundPanes.length === 2) {
        const first = roundPanes[0]
        const second = roundPanes[1]
        const c = await startCompare({
          prompt,
          model_a: first.sel.model, endpoint_a_id: first.sel.endpointId, endpoint_a: first.sel.endpointUrl,
          model_b: second.sel.model, endpoint_b_id: second.sel.endpointId, endpoint_b: second.sel.endpointUrl,
          is_blind: blind,
        })
        setComp(c)
        sessions[first.id] = c.session_left
        sessions[second.id] = c.session_right
        if (!c.is_blind) {
          setRevealed({
            [first.id]: c.model_left || first.sel.model,
            [second.id]: c.model_right || second.sel.model,
          })
        }
      } else {
        const created = await Promise.all(roundPanes.map(async (pane, index) => {
          const session = await createSession({
            name: `[CMP] ${blind ? paneLabel(index, parallel) : shortName(pane.sel.model)}`,
            model: pane.sel.model,
            endpoint_id: pane.sel.endpointId,
            endpoint_url: pane.sel.endpointUrl,
            skip_validation: !!pane.sel.endpointId,
          })
          return [pane.id, session.id] as const
        }))
        created.forEach(([paneId, sessionId]) => { sessions[paneId] = sessionId })
        if (!blind) {
          setRevealed(Object.fromEntries(roundPanes.map((pane) => [pane.id, pane.sel.model])))
        }
      }

      setPanes((prev) => prev.map((pane) => sessions[pane.id] ? { ...pane, sessionId: sessions[pane.id] } : pane))
      const controllers: Record<string, AbortController> = {}
      roundPanes.forEach((pane) => { controllers[pane.id] = new AbortController() })
      controllersRef.current = controllers
      const runPane = async (pane: ComparePaneState) => {
        const ctrl = controllers[pane.id]
        const sessionId = sessions[pane.id]
        if (!ctrl || !sessionId) return
        try {
          await streamPaneById(pane.id, sessionId, ctrl)
        } catch (e) {
          const message = e instanceof Error ? e.message : `${paneLabel(roundPanes.indexOf(pane), parallel)} failed`
          patchPane(pane.id, { err: message })
        }
      }

      if (parallel) {
        await Promise.allSettled(roundPanes.map(runPane))
      } else {
        for (const pane of roundPanes) {
          const ctrl = controllers[pane.id]
          if (!ctrl || ctrl.signal.aborted) break
          await runPane(pane)
        }
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Comparison failed")
    } finally {
      setRunning(false)
      controllersRef.current = {}
    }
  }

  const stop = () => {
    abortControllers()
  }

  // Abort a single pane: close its SSE socket *and* cancel the detached
  // backend run via its session id (the run keeps generating otherwise).
  const stopPane = (paneId: string) => {
    controllersRef.current[paneId]?.abort()
    const pane = panes.find((item) => item.id === paneId)
    if (pane?.sessionId) void stopChatSession(pane.sessionId).catch(() => {})
  }

  const reset = () => {
    clearRoundState()
  }

  const changeMode = (nextMode: CompareMode) => {
    if (nextMode === mode || anyBusy || roundStarted) return
    clearRoundState(currentSessionIds(), { resetSelections: true, searchProviders: nextMode === "search" ? availableSearchProviders : undefined })
    setMode(nextMode)
    setParallel(nextMode === "chat" || nextMode === "agent")
    setExpectedAnswer("")
    setProbeStatus(null)
  }

  const rerollPane = async (paneId: string) => {
    const pane = panes.find((item) => item.id === paneId)
    if (!pane || !prompt.trim() || anyBusy || voted) return
    if (mode !== "search" && !pane.sessionId) return
    const ctrl = new AbortController()
    controllersRef.current[paneId]?.abort()
    controllersRef.current[paneId] = ctrl
    setRerolling(paneId)
    try {
      if (mode === "search") await searchPaneById(pane, ctrl)
      else await streamPaneById(paneId, pane.sessionId!, ctrl)
    } catch (e) {
      patchPane(paneId, { err: e instanceof Error ? e.message : mode === "search" ? "Search failed" : "Model failed" })
    } finally {
      if (controllersRef.current[paneId] === ctrl) controllersRef.current[paneId] = null
      setRerolling(null)
    }
  }

  const reveal = async () => {
    if (revealed || revealing || !roundStarted) return
    setRevealing(true)
    try {
      if (comp && panes.length === 2) {
        const result = await revealCompare(comp.id)
        setRevealed({
          [panes[0].id]: result.revealed.left,
          [panes[1].id]: result.revealed.right,
        })
      } else {
        setRevealed(Object.fromEntries(panes.map((pane) => [pane.id, paneModelName(pane)])))
      }
    } catch {
      toast("Couldn't reveal model names")
    } finally {
      setRevealing(false)
    }
  }

  const vote = async (winner: string) => {
    if (voted || !canVote) return
    try {
      if (comp && panes.length === 2) {
        const result = await voteCompare(comp.id, winner === "tie" ? "tie" : winner === panes[0].id ? "left" : "right")
        setRevealed({
          [panes[0].id]: result.revealed.left,
          [panes[1].id]: result.revealed.right,
        })
      } else {
        const modelNames = panes.map(paneModelName)
        const winningPane = panes.find((pane) => pane.id === winner)
        await recordCompareVote({
          prompt,
          models: modelNames,
          winner: winner === "tie" ? "tie" : winningPane ? paneModelName(winningPane) : "",
          is_blind: blind,
        })
        setRevealed(Object.fromEntries(panes.map((pane) => [pane.id, paneModelName(pane)])))
      }
      setVoted(winner)
      qc.invalidateQueries({ queryKey: ["compare-history"] })
    } catch {
      toast("Couldn't record your vote")
    }
  }

  const copyPane = async (paneId: string, text: string) => {
    if (!text) return
    await navigator.clipboard.writeText(text)
    setCopied(paneId)
    window.setTimeout(() => setCopied(null), 1200)
  }

  // Model heading used in exports: reveal the real model name when it's known
  // (non-blind or already revealed/voted), otherwise keep the blind label.
  const exportModelLabel = (pane: ComparePaneState, index: number) => {
    const revealedName = revealed?.[pane.id]
    if (revealedName) return `${paneLabel(index, parallel)} — ${shortName(revealedName)}`
    if (!blind) {
      const name = paneModelName(pane)
      return name ? `${paneLabel(index, parallel)} — ${shortName(name)}` : paneLabel(index, parallel)
    }
    return paneLabel(index, parallel)
  }

  const hasExportableRound = panes.some((pane) => !!pane.body || !!pane.err)

  const buildExportMarkdown = () => buildComparisonMarkdown(prompt, panes, exportModelLabel)

  const copyExportMarkdown = async () => {
    setExportOpen(false)
    if (!hasExportableRound) return
    try {
      await navigator.clipboard.writeText(buildExportMarkdown())
      toast("Comparison copied as Markdown", "success")
    } catch {
      toast("Couldn't copy to clipboard", "error")
    }
  }

  const downloadExportMarkdown = () => {
    setExportOpen(false)
    if (!hasExportableRound) return
    const blob = new Blob([buildExportMarkdown()], { type: "text/markdown;charset=utf-8" })
    const url = URL.createObjectURL(blob)
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")
    const a = document.createElement("a")
    a.href = url
    a.download = `comparison-${stamp}.md`
    document.body.appendChild(a)
    a.click()
    a.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 2000)
  }

  const printExport = () => {
    setExportOpen(false)
    if (!hasExportableRound) return
    const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    const sections = panes.map((pane, index) => {
      const meta = paneMetricsLine(pane.met, pane.elapsedMs)
      const content = pane.body
        ? `<pre>${esc(pane.body.trim())}</pre>`
        : pane.err
          ? `<p class="err">Error: ${esc(pane.err)}</p>`
          : `<p class="muted">No output.</p>`
      return `<section><h2>${esc(exportModelLabel(pane, index))}</h2>${meta ? `<p class="meta">${esc(meta)}</p>` : ""}${content}</section>`
    }).join("")
    const html = `<!doctype html><html><head><meta charset="utf-8"><title>Model comparison</title><style>
      body{font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#111;margin:2rem;max-width:54rem}
      h1{font-size:1.4rem;margin:0 0 1rem} h2{font-size:1.05rem;margin:1.5rem 0 .35rem;border-bottom:1px solid #ddd;padding-bottom:.25rem}
      pre{white-space:pre-wrap;word-break:break-word;background:#f6f6f6;padding:.75rem;border-radius:.4rem;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
      .prompt{background:#f0f0f0;padding:.75rem;border-radius:.4rem;white-space:pre-wrap}
      .meta,.muted{color:#666;font-size:12px} .meta{margin:.1rem 0 .5rem} .err{color:#b00020}
      section{break-inside:avoid}
    </style></head><body><h1>Model comparison</h1>${prompt.trim() ? `<h2>Prompt</h2><div class="prompt">${esc(prompt.trim())}</div>` : ""}${sections}</body></html>`
    const w = window.open("", "_blank")
    if (!w) {
      toast("Allow pop-ups to print the comparison", "error")
      return
    }
    w.document.write(html)
    w.document.close()
    w.focus()
    window.setTimeout(() => w.print(), 250)
  }

  const pickEvalPrompt = (item: EvalPrompt) => {
    setPrompt(item.prompt)
    setExpectedAnswer(item.answer || "")
    setPanes((prev) => prev.map((pane) => ({ ...pane, grade: null })))
  }

  const replacePaneSelection = (paneId: string, sel: Sel) => {
    const sessionIds = currentSessionIds()
    abortControllers()
    if (sessionIds.length > 0) void cleanupSessions(sessionIds)
    setPanes((prev) => prev.map((pane) => clearPaneRun(pane.id === paneId ? { ...pane, sel } : pane)))
    setErr("")
    setComp(null)
    setVoted(null)
    setRevealed(null)
    setCopied(null)
    setExpandedPane(null)
    setPreviewPane(null)
    setProbeStatus(null)
  }

  const replacePaneSynthSelection = (paneId: string, synthSel: Sel) => {
    const sessionIds = currentSessionIds()
    abortControllers()
    if (sessionIds.length > 0) void cleanupSessions(sessionIds)
    setPanes((prev) => prev.map((pane) => clearPaneRun(pane.id === paneId ? { ...pane, synthSel } : pane)))
    setErr("")
    setComp(null)
    setVoted(null)
    setRevealed(null)
    setCopied(null)
    setExpandedPane(null)
    setPreviewPane(null)
  }

  const addPane = () => {
    if (anyBusy || panes.length >= MAX_COMPARE_PANES) return
    clearRoundState()
    setPanes((prev) => {
      const provider = mode === "search" && availableSearchProviders.length > 0
        ? availableSearchProviders[Math.min(prev.length, availableSearchProviders.length - 1)]
        : null
      return [...prev.map(clearPaneRun), newPane(undefined, provider ? { model: provider.id, endpointId: "", endpointUrl: "" } : EMPTY_SEL)]
    })
    setProbeStatus(null)
  }

  const removePane = (paneId: string) => {
    if (anyBusy || panes.length <= MIN_COMPARE_PANES) return
    const sessionIds = currentSessionIds()
    abortControllers()
    if (sessionIds.length > 0) void cleanupSessions(sessionIds)
    setPanes((prev) => prev.filter((pane) => pane.id !== paneId).map(clearPaneRun))
    setErr("")
    setComp(null)
    setVoted(null)
    setRevealed(null)
    setCopied(null)
    setExpandedPane(null)
    setPreviewPane(null)
    setProbeStatus(null)
  }

  const shufflePanes = () => {
    if (!canShuffle) return
    setPanes((prev) => {
      const next = [...prev]
      for (let i = next.length - 1; i > 0; i -= 1) {
        const j = Math.floor(Math.random() * (i + 1))
        ;[next[i], next[j]] = [next[j], next[i]]
      }
      return next
    })
    setProbeStatus(null)
  }

  const votedLabel = voted === "tie"
    ? "Tie"
    : voted
      ? shortName(revealed?.[voted] || panes.find((pane) => pane.id === voted)?.sel.model || "Model")
      : ""

  const setTimeoutFromInput = (value: string) => {
    const next = Number(value)
    if (!Number.isFinite(next)) return
    setTimeoutSeconds(Math.max(15, Math.min(3600, Math.round(next))))
  }

  return (
    <div className="mx-auto flex h-full w-full max-w-6xl flex-col" data-tour="compare-root">
      <header className="flex h-13 shrink-0 items-center gap-2 border-b px-2 text-sm font-semibold md:px-4">
        <GitCompareArrows className="size-4" />Compare models
        <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">{panes.length}/{MAX_COMPARE_PANES}</span>
        <div className="ml-auto flex items-center gap-1">
          {canProbeModels && selectedModels.length > 0 && unprobedModels.length > 0 && (
            <Button variant="outline" size="sm" onClick={probeModels} disabled={anyBusy} title="Probe unverified models with a small test request">
              {probing ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}Probe
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={addPane} disabled={anyBusy || panes.length >= MAX_COMPARE_PANES} title="Add model pane">
            <Plus className="size-4" />Add
          </Button>
          <Button variant="outline" size="sm" onClick={shufflePanes} disabled={!canShuffle} title={roundStarted ? "Reset before shuffling this round" : "Shuffle pane positions"}>
            <Shuffle className="size-4" />Shuffle
          </Button>
          <Button variant="outline" size="sm" onClick={() => setScoreOpen((v) => !v)}><History className="size-4" />Score</Button>
          <div className="relative" ref={exportMenuRef}>
            <Button variant="outline" size="sm" onClick={() => setExportOpen((v) => !v)} disabled={!hasExportableRound} title={hasExportableRound ? "Export this comparison" : "Run a comparison to enable export"}>
              <Share2 className="size-4" />Export<ChevronDown className="size-3.5 opacity-60" />
            </Button>
            {exportOpen && (
              <div className="absolute right-0 z-20 mt-1 w-56 overflow-hidden rounded-md border bg-popover p-1 text-sm text-popover-foreground shadow-md">
                <button onClick={copyExportMarkdown} className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left hover:bg-accent hover:text-accent-foreground">
                  <Copy className="size-4 text-muted-foreground" />Copy all as Markdown
                </button>
                <button onClick={downloadExportMarkdown} className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left hover:bg-accent hover:text-accent-foreground">
                  <Download className="size-4 text-muted-foreground" />Download .md
                </button>
                <button onClick={printExport} className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left hover:bg-accent hover:text-accent-foreground">
                  <Printer className="size-4 text-muted-foreground" />Print / Save as PDF
                </button>
              </div>
            )}
          </div>
          {streamBusy ? (
            <Button variant="outline" size="sm" onClick={stop}><Square className="size-4" />Stop</Button>
          ) : (
            <Button variant="ghost" size="icon" title="Reset" onClick={reset} disabled={probing}><RotateCcw className="size-4" /></Button>
          )}
        </div>
      </header>
      <div className="flex min-h-0 flex-1 flex-col gap-3 p-2 md:p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="inline-flex rounded-md border bg-background p-0.5" data-tour="compare-mode">
            {MODES.map(({ value, label, icon: Icon }) => (
              <button
                key={value}
                type="button"
                onClick={() => changeMode(value)}
                disabled={anyBusy || roundStarted}
                className={cn("inline-flex h-8 items-center gap-1.5 rounded px-2.5 text-xs font-medium text-muted-foreground transition-colors disabled:opacity-50", mode === value && "bg-accent text-foreground")}
              >
                <Icon className="size-3.5" />{label}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-2 text-xs font-medium text-muted-foreground" data-tour="compare-blind">
            <Switch checked={blind} onCheckedChange={setBlind} disabled={anyBusy || roundStarted} />
            <EyeOff className="size-3.5" />Blind
          </label>
          <label className="flex items-center gap-2 text-xs font-medium text-muted-foreground" data-tour="compare-parallel">
            <Switch checked={parallel} onCheckedChange={setParallel} disabled={anyBusy || roundStarted} />
            Parallel
          </label>
          <label className="flex items-center gap-2 text-xs font-medium text-muted-foreground" title="Keep comparison chats in a Compare folder when you leave this page">
            <Switch checked={saveOnClose} onCheckedChange={setSaveOnClose} />
            Save on close
          </label>
          <label className="ml-auto flex items-center gap-2 text-xs font-medium text-muted-foreground">
            Timeout
            <input type="number" min={15} max={3600} step={15} value={timeoutSeconds} onChange={(e) => setTimeoutFromInput(e.target.value)} disabled={anyBusy} className="h-8 w-20 rounded-md border bg-background px-2 text-xs text-foreground outline-none focus-visible:border-ring disabled:opacity-50" />
          </label>
          {probeStatus && (
            <span className={cn("inline-flex min-w-0 items-center gap-1.5 text-xs", probeStatus.kind === "error" ? "text-destructive" : probeStatus.kind === "ok" ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground")}>
              {probeStatus.kind === "info" && <Loader2 className="size-3.5 animate-spin" />}
              {probeStatus.kind === "ok" && <Check className="size-3.5" />}
              {probeStatus.kind === "error" && <X className="size-3.5" />}
              <span className="truncate">{probeStatus.text}</span>
            </span>
          )}
        </div>
        {scoreOpen && <Scoreboard items={history.data || []} />}
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4" data-tour="compare-models">
          {panes.map((pane, index) => (
            <div key={pane.id} className="flex min-w-0 items-end gap-2">
              {mode === "search" ? (
                <div className="grid min-w-0 flex-1 gap-2 sm:grid-cols-2">
                  <ProviderSelect
                    value={pane.sel}
                    onChange={(sel) => replacePaneSelection(pane.id, sel)}
                    label={`Provider ${paneSlot(index, parallel)}`}
                    providers={availableSearchProviders}
                    loading={providersLoading}
                    disabled={anyBusy}
                  />
                  <ModelSelect
                    value={pane.synthSel}
                    onChange={(sel) => replacePaneSynthSelection(pane.id, sel)}
                    label={`Analysis ${paneSlot(index, parallel)}`}
                    allowEmpty
                    emptyLabel="No analysis"
                  />
                </div>
              ) : (
                <ModelSelect value={pane.sel} onChange={(sel) => replacePaneSelection(pane.id, sel)} label={paneLabel(index, parallel)} />
              )}
              <Button
                variant="ghost"
                size="icon"
                title="Remove pane"
                disabled={anyBusy || panes.length <= MIN_COMPARE_PANES}
                onClick={() => removePane(pane.id)}
                className="mb-0 shrink-0 text-muted-foreground hover:text-destructive"
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
          ))}
        </div>
        {mode === "search" && providersError && <p className="text-xs text-destructive">{providersError}</p>}
        {mode === "search" && !providersLoading && !providersError && availableSearchProviders.length === 0 && (
          <p className="text-xs text-muted-foreground">No configured search providers are available.</p>
        )}
        <div className="flex flex-col items-stretch gap-2 md:flex-row md:items-end" data-tour="compare-prompt">
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Prompt to send to all models…" rows={2} className="flex-1 resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring" />
          <EvalPromptSelect mode={mode} disabled={anyBusy} onPick={pickEvalPrompt} />
          <Button onClick={run} disabled={anyBusy || !prompt.trim() || !allPanesReady}>{running ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}Run</Button>
        </div>
        {expectedAnswer && (
          <div className="flex max-w-full items-center gap-2 rounded-md border bg-muted/35 px-3 py-2 text-xs text-muted-foreground">
            <span className="shrink-0 font-medium">Expected:</span>
            <strong className="min-w-0 truncate font-semibold text-foreground" title={expectedAnswer}>{expectedAnswer}</strong>
            <button
              type="button"
              title="Dismiss expected answer"
              onClick={() => { setExpectedAnswer(""); setPanes((prev) => prev.map((pane) => ({ ...pane, grade: null }))) }}
              className="ml-auto shrink-0 rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              <X className="size-3.5" />
            </button>
          </div>
        )}
        {err && <p className="text-xs text-destructive">{err}</p>}
        <div className={cn(
          "grid min-h-0 flex-1 auto-rows-[minmax(18rem,1fr)] gap-3 overflow-auto pr-1",
          expandedPane ? "grid-cols-1" : "grid-cols-1 lg:grid-cols-2 2xl:grid-cols-3",
        )} data-tour="compare-panes">
          {panes.map((pane, index) => {
            const label = paneLabel(index, parallel)
            const visibleModel = revealed?.[pane.id] || (blind ? "Hidden until vote" : paneModelName(pane) || (mode === "search" ? "Select a provider" : "Select a model"))
            return (
              <Pane
                key={pane.id}
                side={label}
                model={visibleModel}
                body={pane.body}
                win={voted === pane.id}
                met={pane.met}
                running={running || rerolling === pane.id}
                err={pane.err}
                copied={copied === pane.id}
                onCopy={() => copyPane(pane.id, pane.body)}
                onExpand={() => setExpandedPane((current) => current === pane.id ? null : pane.id)}
                expanded={expandedPane === pane.id}
                hidden={!!expandedPane && expandedPane !== pane.id}
                fastest={fastestPane?.id === pane.id}
                elapsedMs={pane.elapsedMs}
                previewOpen={previewPane === pane.id}
                onTogglePreview={() => setPreviewPane((current) => current === pane.id ? null : pane.id)}
                onReroll={() => rerollPane(pane.id)}
                canReroll={(mode === "search" || !!pane.sessionId) && !anyBusy && !voted && (!!pane.body || !!pane.err)}
                rerolling={rerolling === pane.id}
                grade={pane.grade}
                activityLabel={mode === "search" ? "Searching..." : "Generating..."}
                onStop={() => stopPane(pane.id)}
                canStop={mode !== "search" && (running || rerolling === pane.id) && !!pane.sessionId && pane.elapsedMs == null}
              />
            )
          })}
        </div>
        {canVote && (
          <div className="flex flex-wrap items-center justify-center gap-2 pt-1">
            {voted ? (
              <span className="flex items-center gap-1.5 text-sm text-muted-foreground"><Trophy className="size-4" />Voted: {votedLabel}</span>
            ) : (
              <>
                <span className="mr-1 text-sm text-muted-foreground">Which is better?</span>
                {blind && !revealed && <Button variant="ghost" size="sm" disabled={revealing} onClick={reveal}>{revealing ? <Loader2 className="size-3.5 animate-spin" /> : <Eye className="size-3.5" />}Reveal</Button>}
                {panes.map((pane, index) => (
                  <Button key={pane.id} variant="outline" size="sm" onClick={() => vote(pane.id)}>
                    {revealed?.[pane.id] ? shortName(revealed[pane.id]) : paneLabel(index, parallel)}
                  </Button>
                ))}
                <Button variant="outline" size="sm" onClick={() => vote("tie")}>Tie</Button>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
