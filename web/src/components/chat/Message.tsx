import { useEffect, useRef, useState } from "react"
import { ChevronRight, Brain, Telescope, Loader2, Volume2, Square, BookOpen, Copy, Check, RotateCcw, Pencil, ArrowRight, FileCode2, File, AlertTriangle, CircleAlert, Play, MoreHorizontal, Trash2, GitFork, Scissors, Sparkles, ScanText, ListChecks } from "lucide-react"
import { usePanel } from "@/stores/panel"
import { Mascot } from "@/components/ui/Mascot"
import { StreamingMarkdown, Markdown } from "./Markdown"
import { ToolThread } from "./ToolThread"
import { parseArtifact, cleanRoundText } from "@/lib/artifact"
import { useVoiceCaps, speak } from "@/api/voice"
import { cn } from "@/lib/utils"
import type { AskUserPrompt, ChatAttachment, ChatMessage, Artifact } from "@/types"

function formatSize(bytes?: number) {
  if (bytes == null) return ""
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function Attachments({ items }: { items: ChatAttachment[] }) {
  return <div className="flex max-w-[600px] flex-wrap justify-end gap-2">
    {items.map((a, i) => {
      const image = (a.mime || "").startsWith("image/") || /\.(png|jpe?g|gif|webp|bmp)$/i.test(a.name)
      if (image && a.id) return <ImageAttachment key={a.id || i} attachment={a} />
      return <a key={a.id || i} href={a.id ? `/api/upload/${a.id}` : undefined} download={a.name} className="flex max-w-72 items-center gap-2 rounded-xl border bg-card px-3 py-2 text-left hover:bg-accent">
        <File className="size-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0"><span className="block truncate text-sm">{a.name}</span>{a.size != null && <span className="block text-[11px] text-muted-foreground">{formatSize(a.size)}</span>}</span>
      </a>
    })}
  </div>
}

function ImageAttachment({ attachment }: { attachment: ChatAttachment }) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState(attachment.visionText || "")
  const [loading, setLoading] = useState(false)
  const [saved, setSaved] = useState(false)
  const load = async () => {
    setOpen(true)
    if (text || !attachment.id) return
    setLoading(true)
    try { const response = await fetch(`/api/upload/${attachment.id}/vision`, { credentials: "same-origin" }); const data = await response.json(); if (response.ok) setText(data.text || "") }
    catch { /* keep editor available for manual text */ } finally { setLoading(false) }
  }
  const save = async () => {
    if (!attachment.id) return
    const response = await fetch(`/api/upload/${attachment.id}/vision`, { method: "PUT", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) })
    if (response.ok) { setSaved(true); setTimeout(() => setSaved(false), 1500) }
  }
  return <div className="overflow-hidden rounded-xl border bg-card text-left">
    <button onClick={() => window.open(`/api/upload/${attachment.id}`, "_blank")} className="block"><img src={attachment.previewUrl || `/api/upload/${attachment.id}?thumb=1`} alt={attachment.name} className="max-h-48 max-w-72 object-contain" /></button>
    <div className="flex max-w-72 items-center gap-2 px-2.5 py-1.5"><span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{attachment.name}</span><button onClick={open ? () => setOpen(false) : load} title="Review image description / OCR" className="text-muted-foreground hover:text-foreground"><ScanText className="size-3.5" /></button></div>
    {open && <div className="space-y-1.5 border-t p-2"><textarea value={text} onChange={(event) => setText(event.target.value)} rows={4} placeholder={loading ? "Analyzing image…" : "Image description or OCR text"} className="w-64 resize-y rounded-md border bg-background p-2 text-xs outline-none focus-visible:border-ring" /><div className="flex justify-end"><button onClick={save} disabled={loading} className="rounded-md border px-2 py-1 text-[11px] hover:bg-accent disabled:opacity-50">{saved ? "Saved" : "Save text"}</button></div></div>}
  </div>
}

function AskUserCard({ prompt, onRespond }: { prompt: AskUserPrompt; onRespond?: (answer: string) => void }) {
  const [selected, setSelected] = useState<string[]>([])
  const choose = (label: string) => {
    if (!prompt.multi) { onRespond?.(label); return }
    setSelected((prev) => prev.includes(label) ? prev.filter((v) => v !== label) : [...prev, label])
  }
  return <div className="space-y-2 rounded-xl border bg-card p-3">
    <div className="text-xs font-medium text-muted-foreground">{prompt.multi ? "Choose one or more" : "Choose an option"}</div>
    <div className="grid gap-2">
      {prompt.options.map((option) => <button key={option.label} onClick={() => choose(option.label)} disabled={!onRespond}
        className={cn("rounded-lg border px-3 py-2 text-left text-sm transition-colors hover:bg-accent disabled:opacity-50", selected.includes(option.label) && "border-ring bg-accent")}>
        <span className="font-medium">{option.label}</span>
        {option.description && <span className="mt-0.5 block text-xs text-muted-foreground">{option.description}</span>}
      </button>)}
    </div>
    {prompt.multi && <button onClick={() => onRespond?.(selected.join(", "))} disabled={!onRespond || !selected.length} className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50">Continue</button>}
  </div>
}

function ArtifactCard({ artifact }: { artifact: Artifact }) {
  const open = () => { const p = usePanel.getState(); p.showDoc(artifact.title, artifact.language); p.setDocContent(artifact.content) }
  return (
    <button onClick={open} className="group flex w-full items-center gap-3 rounded-xl border bg-card p-3 text-left transition-all hover:border-ring/60 hover:bg-accent/40">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground"><FileCode2 className="size-[18px]" /></span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">{artifact.title}</span>
        <span className="block text-xs text-muted-foreground">{artifact.language || "document"}{artifact.closed ? "" : " · generating…"}</span>
      </span>
      <span className="shrink-0 text-xs text-muted-foreground opacity-100 transition-opacity md:opacity-0 md:group-hover:opacity-100">Open ›</span>
    </button>
  )
}

const actionBtn = "inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false)
  const copy = async () => { try { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500) } catch { /* ignore */ } }
  return <button onClick={copy} title="Copy" className={actionBtn}>{done ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}</button>
}

function SpeakButton({ text }: { text: string }) {
  const { data: caps } = useVoiceCaps()
  const [busy, setBusy] = useState(false)
  const [playing, setPlaying] = useState(false)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  // Stop playback if the message unmounts (e.g. switching threads) — otherwise
  // a detached, playing HTMLAudioElement keeps speaking with no way to stop it.
  useEffect(() => () => { audioRef.current?.pause(); audioRef.current = null }, [])
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
  // Collapsed by default (even while thinking) — click to reveal the live stream.
  const [open, setOpen] = useState(false)
  const bodyRef = useRef<HTMLDivElement>(null)
  useEffect(() => { if (live && open && bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight }, [text, live, open])
  return (
    <div className="animate-fade-in rounded-lg border bg-card text-xs">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-2 px-3 py-2 text-muted-foreground transition-colors hover:text-foreground">
        <ChevronRight className={cn("size-3.5 transition-transform duration-200", open && "rotate-90")} />
        <Brain className={cn("size-3.5", live && "animate-pulse-soft")} />
        <span className={cn(live && "shimmer-text")}>{live ? "Thinking…" : "Reasoning"}</span>
      </button>
      {open && <div ref={bodyRef} className="max-h-64 overflow-y-auto whitespace-pre-wrap border-t px-3 py-2 leading-relaxed text-muted-foreground">{text}</div>}
    </div>
  )
}

// Live agent plan checklist (markdown task list). The legacy UI docked this in
// a floating window; here it sits inline as a collapsible card, open by default.
function Plan({ text }: { text: string }) {
  const [open, setOpen] = useState(true)
  const done = (text.match(/^[-*]\s*\[x\]/gim) || []).length
  const total = (text.match(/^[-*]\s*\[[ xX]\]/gim) || []).length
  return (
    <div className="animate-fade-in rounded-lg border bg-card text-xs">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-2 px-3 py-2 text-muted-foreground transition-colors hover:text-foreground">
        <ChevronRight className={cn("size-3.5 transition-transform duration-200", open && "rotate-90")} />
        <ListChecks className="size-3.5" />
        <span>Plan{total ? ` · ${done}/${total}` : ""}</span>
      </button>
      {open && <div className="border-t px-3 py-1"><Markdown>{text}</Markdown></div>}
    </div>
  )
}

function MessageEditor({ initial, assistant = false, onSubmit, onCancel }: { initial: string; assistant?: boolean; onSubmit?: (t: string) => void; onCancel?: () => void }) {
  const [val, setVal] = useState(initial)
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = ref.current
    if (el) { el.focus(); el.style.height = "auto"; el.style.height = `${el.scrollHeight}px`; el.setSelectionRange(el.value.length, el.value.length) }
  }, [])
  const grow = (el: HTMLTextAreaElement) => { el.style.height = "auto"; el.style.height = `${el.scrollHeight}px` }
  const save = () => { if (val.trim()) onSubmit?.(val) }
  return (
    <div className={cn("flex animate-fade-in flex-col gap-2", assistant ? "items-stretch" : "items-end")}>
      <textarea
        ref={ref} value={val} rows={1}
        onChange={(e) => { setVal(e.target.value); grow(e.target) }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); save() }
          if (e.key === "Escape") { e.preventDefault(); onCancel?.() }
        }}
        className={cn("max-h-[300px] w-full resize-none rounded-2xl border px-4 py-2.5 text-[15px] outline-none focus:border-ring focus:ring-[3px] focus:ring-ring/35", assistant ? "bg-background" : "max-w-[600px] bg-secondary")}
      />
      <div className={cn("flex items-center gap-2 text-xs", assistant ? "justify-end" : "")}>
        <button onClick={onCancel} className="rounded-md px-2.5 py-1 text-muted-foreground hover:bg-accent hover:text-foreground">Cancel</button>
        <button onClick={save} disabled={!val.trim()} className="rounded-md bg-primary px-3 py-1 font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50">{assistant ? "Save" : "Send"}</button>
      </div>
    </div>
  )
}

function MessageActions({ assistant, onEdit, onDelete, onFork, onRewrite }: {
  assistant: boolean; onEdit?: () => void; onDelete?: () => void; onFork?: () => void;
  onRewrite?: (instruction: string) => void;
}) {
  const [open, setOpen] = useState(false)
  if (!onEdit && !onDelete && !onFork && !onRewrite) return null
  const act = (fn?: () => void) => { setOpen(false); fn?.() }
  const item = "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
  return <div className="relative">
    <button onClick={() => setOpen((v) => !v)} title="More message actions" className={actionBtn}><MoreHorizontal className="size-3.5" /></button>
    {open && <>
      <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
      <div className="absolute bottom-full right-0 z-30 mb-1 w-44 rounded-xl border bg-popover p-1 shadow-lg">
        {onEdit && <button onClick={() => act(onEdit)} className={item}><Pencil className="size-3.5" />{assistant ? "Edit response" : "Edit & resend"}</button>}
        {assistant && onRewrite && <>
          <button onClick={() => act(() => onRewrite("Rewrite this response to be shorter and more concise. Keep the key information but cut the fluff."))} className={item}><Scissors className="size-3.5" />Make shorter</button>
          <button onClick={() => act(() => onRewrite("Explain this response in simpler terms. Use plain language and short sentences."))} className={item}><Sparkles className="size-3.5" />Explain simpler</button>
        </>}
        {onFork && <button onClick={() => act(onFork)} className={item}><GitFork className="size-3.5" />Fork from here</button>}
        {onDelete && <button onClick={() => act(onDelete)} className={cn(item, "text-destructive hover:text-destructive")}><Trash2 className="size-3.5" />Delete message</button>}
      </div>
    </>}
  </div>
}

export function Message({ m, onRegenerate, onEdit, onDelete, onFork, onRewrite, editing, onEditSubmit, onEditCancel, onRespond }: {
  m: ChatMessage; onRegenerate?: () => void; onEdit?: () => void
  onDelete?: () => void; onFork?: () => void; onRewrite?: (instruction: string) => void
  editing?: boolean; onEditSubmit?: (text: string) => void; onEditCancel?: () => void
  onRespond?: (text: string) => void
}) {
  if (m.role === "user") {
    if (editing) return <MessageEditor initial={m.content} onSubmit={onEditSubmit} onCancel={onEditCancel} />
    return (
      <div className="group flex flex-col items-end gap-2 animate-msg-in">
        {!!m.attachments?.length && <Attachments items={m.attachments} />}
        {m.content && <div className="max-w-[75%] whitespace-pre-wrap rounded-2xl bg-secondary px-4 py-2.5 text-[15px]">{m.content}</div>}
        <div className="mt-0.5 flex items-center gap-0.5 text-[11px] opacity-100 transition-opacity md:opacity-0 md:group-hover:opacity-100">
          <CopyButton text={m.content} />
          {onEdit && <button onClick={onEdit} title="Edit & resend" className={actionBtn}><Pencil className="size-3.5" /></button>}
          <MessageActions assistant={false} onDelete={onDelete} onFork={onFork} />
        </div>
      </div>
    )
  }
  const mt = m.metrics
  if (editing) return <MessageEditor initial={m.content} assistant onSubmit={onEditSubmit} onCancel={onEditCancel} />
  // Strip the create_document fence out of the bubble — it streams into the
  // side panel as an artifact instead of showing as raw code in the chat.
  const { display, artifact } = parseArtifact(m.content)
  // Agent turns reconstruct the interleaved text → tools → text layout from
  // per-round data (live or saved). A plain reply has no rounds and renders flat.
  const useRounds = !!m.rounds && (m.rounds.length > 1 || m.rounds.some((r) => r.tools.length > 0))
  const cleaned = useRounds ? m.rounds!.map((r) => ({ tools: r.tools, ...cleanRoundText(r.text) })) : null
  const doc = m.artifact || artifact || cleaned?.map((c) => c.artifact).find(Boolean)
  const bodyText = useRounds ? cleaned!.map((c) => c.display).filter(Boolean).join("\n\n") : display
  const hasBody = useRounds ? cleaned!.some((c) => c.display || c.tools.length > 0) : !!display
  return (
    <div className="space-y-3 animate-msg-in">
      {m.groupName && (
        <div className="inline-flex items-center rounded-full border bg-muted px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
          {m.groupName}
        </div>
      )}
      {m.reasoning && <Reasoning text={m.reasoning} live={!!m.streaming && !m.content} />}
      {m.plan && <Plan text={m.plan} />}
      {useRounds ? cleaned!.map((c, i) => (
        <div key={i} className="space-y-3">
          {c.display && <StreamingMarkdown content={c.display} streaming={!!m.streaming && i === cleaned!.length - 1} />}
          {c.tools.length > 0 && <ToolThread tools={c.tools} defaultOpen />}
        </div>
      )) : (m.tools && m.tools.length > 0 && <ToolThread tools={m.tools} />)}
      {m.research && m.streaming && (
        <div className="flex animate-fade-in items-center gap-2.5 rounded-lg border bg-card px-3 py-2.5 text-sm">
          <Telescope className="size-4 shrink-0 text-muted-foreground" />
          <span className="font-medium capitalize">{m.research.phase}</span>
          {m.research.detail && <span className="min-w-0 truncate text-muted-foreground">— {m.research.detail}</span>}
          <Loader2 className="ml-auto size-3.5 shrink-0 animate-spin text-muted-foreground" />
        </div>
      )}
      {!useRounds && display && <StreamingMarkdown content={display} streaming={!!m.streaming} />}
      {doc && <ArtifactCard artifact={doc} />}
      {m.askUser && <AskUserCard prompt={m.askUser} onRespond={onRespond} />}
      {m.notice && (
        <div className={cn("flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm", m.notice.kind === "error" && "border-destructive/40", m.notice.kind === "warning" && "bg-muted/40")}>
          {m.notice.kind === "error" ? <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" /> : <AlertTriangle className="mt-0.5 size-4 shrink-0 text-muted-foreground" />}
          <span className="min-w-0 flex-1">{m.notice.text}</span>
          {m.notice.continuePrompt && onRespond && <button onClick={() => onRespond(m.notice!.continuePrompt!)} className="inline-flex shrink-0 items-center gap-1 rounded-md border px-2 py-1 text-xs font-medium hover:bg-accent"><Play className="size-3" />Continue</button>}
        </div>
      )}
      {m.sources && m.sources.length > 0 && (
        <button onClick={() => usePanel.getState().show("sources", { title: `Sources · ${m.sources!.length}`, payload: m.sources })}
          className="mt-1 inline-flex animate-fade-in items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground">
          <BookOpen className="size-3.5" /> {m.sources.length} source{m.sources.length === 1 ? "" : "s"}
        </button>
      )}
      {/* The animated mascot is the "assistant is working" indicator — it stays
         under the message for the whole stream. The "Thinking…" label shows only
         before any content / reasoning / tools have arrived. */}
      {m.streaming && (
        <div className="flex animate-fade-in items-center gap-2.5 pt-0.5 text-sm text-muted-foreground">
          <Mascot size={9} title="Working" />
          {!hasBody && !doc && !m.reasoning && !m.research && (!m.tools || m.tools.length === 0) && <span className="shimmer-text">Thinking…</span>}
        </div>
      )}
      {!m.streaming && (m.model || mt || m.content) && (
        <div className="flex flex-wrap items-center gap-2 pt-0.5 text-[11px] text-muted-foreground">
          {bodyText && <CopyButton text={bodyText} />}
          {(bodyText || doc) && onRegenerate && <button onClick={onRegenerate} title="Regenerate" className={actionBtn}><RotateCcw className="size-3.5" /></button>}
          {bodyText && <SpeakButton text={bodyText} />}
          <MessageActions assistant onEdit={onEdit} onDelete={onDelete} onFork={onFork} onRewrite={onRewrite} />
          {m.edited && <span>· edited</span>}
          {m.model && (
            <span className="ml-1 inline-flex items-center gap-1">
              {m.model}
              {m.modelActual && m.modelActual !== m.model && <><ArrowRight className="size-3" />{m.modelActual}</>}
            </span>
          )}
          {mt?.tokens_in != null && <span>· {mt.tokens_in} in</span>}
          {mt?.tokens_out != null && <span>· {mt.tokens_out} out</span>}
          {mt?.tokens_total != null && <span>· {mt.tokens_total} total</span>}
          {mt?.context_tokens != null && <span>· {mt.context_tokens} context</span>}
          {mt?.tok_per_sec != null && <span>· {Math.round(mt.tok_per_sec)} tok/s</span>}
          {mt?.cost != null && <span>· ${Number(mt.cost).toFixed(4)}</span>}
          {mt?.prep_seconds != null && <span>· prep {Number(mt.prep_seconds).toFixed(1)}s</span>}
          {mt?.model_wait_seconds != null && <span>· wait {Number(mt.model_wait_seconds).toFixed(1)}s</span>}
          {mt?.response_seconds != null && <span>· {Number(mt.response_seconds).toFixed(1)}s</span>}
        </div>
      )}
    </div>
  )
}
