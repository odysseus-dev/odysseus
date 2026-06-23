import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { useQueryClient } from "@tanstack/react-query"
import { readSse, SseResponseError, StreamInterruptedError, streamChat, streamResume, type SseEvent } from "@/lib/sse"
import { useComposer } from "@/stores/composer"
import { usePanel } from "@/stores/panel"
import { parseArtifact } from "@/lib/artifact"
import { createSession, useHistory } from "@/api/sessions"
import { injectMessages, saveGroupPresetIfMissing, startGroupRuntime, streamGroupReply, type GroupRuntimeParticipant } from "@/lib/groupChat"
import { toast } from "@/stores/toast"
import type { AgentRound, AskUserOption, ChatAttachment, ChatMessage, HistoryMsg, Source, ToolEvent } from "@/types"

function researchPhase(d: Record<string, unknown>): { phase: string; detail?: string } {
  const phase = (d?.phase as string) || "researching"
  const round = d?.round as number | undefined
  const sources = d?.total_sources as number | undefined
  const msg = d?.message as string | undefined
  const title = (d?.title as string) || (d?.url as string)
  let detail: string | undefined
  switch (phase) {
    case "planning": detail = "Planning the research…"; break
    case "searching": detail = `Searching${round ? ` · round ${round}` : ""}${sources != null ? ` · ${sources} sources` : ""}`; break
    case "reading": detail = title ? `Reading: ${title}` : "Reading sources…"; break
    case "analyzing": detail = `Analyzing findings${round ? ` · round ${round}` : ""}`; break
    case "writing": detail = msg || "Writing the report…"; break
    case "warning": case "error": detail = msg; break
    default: detail = msg
  }
  return { phase, detail }
}

function flatten(content: unknown): string {
  if (typeof content === "string") return content
  if (Array.isArray(content)) return content.map((c) => (typeof c === "string" ? c : (c as { text?: string })?.text || "")).join("")
  return content == null ? "" : String(content)
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function normalizeAttachment(value: unknown): ChatAttachment | null {
  const a = asRecord(value)
  const name = String(a.name || a.filename || "Attachment")
  return {
    id: a.id ? String(a.id) : undefined,
    name,
    mime: String(a.mime || a.mime_type || "") || undefined,
    size: typeof a.size === "number" ? a.size : undefined,
    width: typeof a.width === "number" ? a.width : undefined,
    height: typeof a.height === "number" ? a.height : undefined,
    previewUrl: typeof a.previewUrl === "string" ? a.previewUrl : undefined,
    visionText: typeof (a.vision_text ?? a.caption ?? a.ocr) === "string" ? String(a.vision_text ?? a.caption ?? a.ocr) : undefined,
  }
}

function normalizeAttachments(value: unknown): ChatAttachment[] {
  return Array.isArray(value) ? value.map(normalizeAttachment).filter((a): a is ChatAttachment => !!a) : []
}

function normalizeSources(value: unknown): Source[] {
  if (!Array.isArray(value)) return []
  return value.map((s) => {
    const r = asRecord(s)
    return { url: String(r.url || "") || undefined, title: String(r.title || r.name || "") || undefined, snippet: String(r.snippet || r.text || "") || undefined }
  })
}

function normalizeTools(value: unknown): ToolEvent[] {
  if (!Array.isArray(value)) return []
  return value.map((t) => {
    const r = asRecord(t)
    return {
      name: String(r.tool || r.name || "tool"), input: r.input,
      command: typeof r.command === "string" ? r.command : undefined,
      output: typeof r.output === "string" ? r.output : undefined,
      exitCode: typeof r.exit_code === "number" ? r.exit_code : undefined,
      round: typeof r.round === "number" ? r.round : undefined,
      // Rich artifacts the original re-renders on reload — keep them so
      // generated images and file-write diffs survive a refresh.
      imageUrl: typeof r.image_url === "string" ? r.image_url : undefined,
      imagePrompt: typeof r.image_prompt === "string" ? r.image_prompt : undefined,
      diff: r.diff && typeof r.diff === "object" ? r.diff as ToolEvent["diff"] : undefined,
      docId: typeof r.doc_id === "string" ? r.doc_id : undefined,
      docTitle: typeof r.doc_title === "string" ? r.doc_title : undefined,
      running: false,
    }
  })
}

// Rebuild the per-round structure (text + that round's tools) from saved
// metadata, mirroring the legacy chatRenderer reconstruction
// (static/js/chatRenderer.js). `round_texts[r]` is the cleaned text for round
// r+1; tools carry a 1-based `round`. Returns undefined when the turn ran no
// tools (a plain chat reply renders flat).
function buildRounds(roundTexts: unknown, tools: ToolEvent[]): AgentRound[] | undefined {
  if (!tools.length) return undefined
  const texts = Array.isArray(roundTexts) ? roundTexts.map((t) => (typeof t === "string" ? t : "")) : []
  let maxRound = texts.length
  tools.forEach((t) => { maxRound = Math.max(maxRound, t.round || 1) })
  const rounds: AgentRound[] = []
  for (let r = 1; r <= maxRound; r++) {
    rounds.push({ text: (texts[r - 1] || "").trim(), tools: tools.filter((t) => (t.round || 1) === r) })
  }
  return rounds
}

// --- Live round assembly (immutable updates for setState) ---
// Lazily seed rounds from the round-1 text that already streamed into `content`
// before the first tool/step arrived.
function ensureRounds(m: ChatMessage): AgentRound[] {
  return m.rounds && m.rounds.length ? m.rounds : [{ text: m.content, tools: [] }]
}
function appendToLastRound(rounds: AgentRound[], d: string): AgentRound[] {
  const r = rounds.slice(); const i = r.length - 1
  r[i] = { ...r[i], text: r[i].text + d }
  return r
}
function pushToolToRounds(rounds: AgentRound[], tool: ToolEvent): AgentRound[] {
  const r = rounds.slice(); const i = r.length - 1
  r[i] = { ...r[i], tools: [...r[i].tools, tool] }
  return r
}
function patchLastTool(rounds: AgentRound[] | undefined, fn: (t: ToolEvent) => ToolEvent): AgentRound[] | undefined {
  if (!rounds || !rounds.length) return rounds
  const r = rounds.slice(); const i = r.length - 1
  const tools = r[i].tools
  if (!tools.length) return rounds
  const t = tools.slice(); t[t.length - 1] = fn(t[t.length - 1])
  r[i] = { ...r[i], tools: t }
  return r
}

function normalizeAskUser(value: unknown) {
  const q = asRecord(value)
  if (!Array.isArray(q.options)) return undefined
  const options = q.options.map((o): AskUserOption => {
    if (typeof o === "string") return { label: o }
    const r = asRecord(o)
    return { label: String(r.label || "Option"), description: typeof r.description === "string" ? r.description : undefined }
  })
  return { question: typeof q.question === "string" ? q.question : undefined, options, multi: !!q.multi }
}

export function historyToMessages(h: HistoryMsg[]): ChatMessage[] {
  return h
    .filter((m) => m.role === "user" || m.role === "assistant")
    .map((m) => {
      const md = m.metadata || {}
      const sources = [...normalizeSources(md.research_sources), ...normalizeSources(md.web_sources), ...normalizeSources(md.rag_sources)]
      const metrics = m.role === "assistant" ? {
        tokens_in: (md.input_tokens ?? md.tokens_in) as number | undefined,
        tokens_out: (md.output_tokens ?? md.tokens_out) as number | undefined,
        tokens_total: (md.total_tokens ?? md.tokens_total) as number | undefined,
        context_tokens: (md.context_tokens ?? md.prompt_tokens) as number | undefined,
        cost: md.cost as number | undefined,
        tok_per_sec: (md.tokens_per_second ?? md.tok_per_sec ?? md.tokens_per_sec) as number | undefined,
        prep_seconds: (md.prep_seconds ?? md.prep_time) as number | undefined,
        model_wait_seconds: (md.model_wait_seconds ?? md.model_wait) as number | undefined,
        response_seconds: (md.response_seconds ?? md.total_seconds ?? md.elapsed) as number | undefined,
      } : undefined
      const stopped = !!md.stopped
      const cancelled = !!md.cancelled
      const roundsExhausted = Number(md.rounds_exhausted || 0)
      const tools = m.role === "assistant" ? normalizeTools(md.tool_events) : []
      return {
        role: m.role as "user" | "assistant",
        content: flatten(m.content),
        model: (md.requested_model as string | undefined) || (md.model as string | undefined) || m.model,
        modelActual: md.model as string | undefined,
        groupName: m.role === "assistant" ? md.group_model as string | undefined : undefined,
        reasoning: m.role === "assistant" ? md.thinking as string | undefined : undefined,
        attachments: normalizeAttachments(md.attachments || m.attachments),
        tools: m.role === "assistant" ? tools : undefined,
        // Per-round reconstruction so saved agent turns render with the same
        // interleaved text/tool layout as the live stream and the legacy UI.
        rounds: m.role === "assistant" ? buildRounds(md.round_texts, tools) : undefined,
        sources: sources.length ? sources : undefined,
        messageId: md._db_id as string | undefined,
        askUser: m.role === "assistant" ? normalizeAskUser(md.ask_user) : undefined,
        notice: m.role !== "assistant" ? undefined : roundsExhausted ? {
          kind: "warning" as const,
          text: `Reached the ${roundsExhausted}-step limit — the task is not finished.`,
          continuePrompt: "You hit the step limit before finishing — the task is not complete. Continue from exactly where you left off and keep going until it is done. Do NOT repeat work already done.",
        } : stopped ? {
          kind: "stopped" as const,
          text: cancelled ? "Cancelled by user." : "Message interrupted.",
          continuePrompt: cancelled ? undefined : "Your previous response was interrupted. Continue exactly where you left off. Do NOT repeat work already completed.",
        } : undefined,
        edited: !!md.edited,
        metrics,
      }
    })
}

export function useChat(sessionId?: string) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const composer = useComposer()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const groupAbortRefs = useRef<AbortController[]>([])
  const sidRef = useRef<string | undefined>(sessionId)
  const seededRef = useRef<string | null>(null)
  const resumeRef = useRef<string | null>(null)
  const incognitoSidRef = useRef<string | null>(null) // ephemeral incognito session to delete on leave
  const rawRef = useRef<string>("")          // full assistant text this stream (for fence parsing)
  const artifactRef = useRef<string | null>(null) // title of the doc currently open in the panel
  const { data: history } = useHistory(sessionId)

  useEffect(() => { sidRef.current = sessionId }, [sessionId])

  useEffect(() => {
    if (streaming) return
    const sid = sessionId || null
    if (seededRef.current === sid) return
    if (!sid) { setMessages([]); seededRef.current = null; return }
    if (history?.history) { setMessages(historyToMessages(history.history)); seededRef.current = sid }
  }, [sessionId, history, streaming])

  const patchAi = useCallback((fn: (m: ChatMessage) => ChatMessage) =>
    setMessages((prev) => {
      const c = [...prev]; const i = c.length - 1
      if (i >= 0 && c[i].role === "assistant") c[i] = fn(c[i])
      return c
    }), [])

  const patchLastUser = useCallback((fn: (m: ChatMessage) => ChatMessage) =>
    setMessages((prev) => {
      const c = [...prev]
      for (let i = c.length - 1; i >= 0; i--) {
        if (c[i].role === "user") { c[i] = fn(c[i]); break }
      }
      return c
    }), [])

  const applyUiControl = useCallback((value: unknown) => {
    const data = asRecord(value)
    const action = String(data.ui_event || "")
    const state = useComposer.getState()
    if (action === "toggle") {
      const map = { web: "useWeb", bash: "allowBash", rag: "useRag", research: "useResearch", incognito: "incognito" } as const
      const key = map[String(data.toggle_name) as keyof typeof map]
      if (key && state[key] !== !!data.state) state.toggle(key)
    } else if (action === "set_mode" && (data.mode === "agent" || data.mode === "chat")) {
      state.setMode(data.mode)
    } else if (action === "switch_model" && typeof data.model === "string") {
      state.setModel(data.model, state.endpointId, typeof data.endpoint_url === "string" ? data.endpoint_url : state.endpointUrl)
    } else if (action === "open_panel") {
      const routes: Record<string, string> = {
        documents: "/library", gallery: "/gallery", email: "/email", sessions: "/chat",
        notes: "/notes", memories: "/memory", skills: "/skills", settings: "/settings", cookbook: "/cookbook",
      }
      const route = routes[String(data.panel || "")]
      if (route) navigate(route)
    } else if (action === "open_email_reply") {
      const params = new URLSearchParams({ uid: String(data.uid || ""), folder: String(data.folder || "INBOX"), reply: String(data.mode || "reply") })
      if (data.body) params.set("body", String(data.body))
      navigate(`/email?${params.toString()}`)
    }
    // Theme actions are intentionally excluded: this parity pass must not alter the theme.
  }, [navigate])

  const handleEvent = useCallback(async (e: SseEvent, sid: string) => {
    const ev = e as Record<string, unknown>
    if (typeof ev.delta === "string") {
      const d = ev.delta as string
      if (ev.thinking) { patchAi((m) => ({ ...m, reasoning: (m.reasoning || "") + d })); return }
      // Accumulate raw text + detect a `create_document` fence so the doc opens
      // live in the side panel and the fence is stripped from the chat bubble.
      rawRef.current += d
      const { artifact } = parseArtifact(rawRef.current)
      if (artifact) {
        const panel = usePanel.getState()
        if (artifactRef.current !== artifact.title) { panel.showDoc(artifact.title, artifact.language); artifactRef.current = artifact.title }
        panel.setDocContent(artifact.content)
      }
      patchAi((m) => {
        // Once a turn has gone multi-round (a tool ran), append text to the
        // current round so it renders interleaved; otherwise keep flat.
        const rounds = m.rounds ? appendToLastRound(m.rounds, d) : m.rounds
        return { ...m, content: m.content + d, artifact: artifact || m.artifact, rounds }
      })
      return
    }
    switch (e.type) {
      case "model_info": patchAi((m) => ({ ...m, model: ev.model as string })); break
      case "model_actual": patchAi((m) => ({ ...m, modelActual: ev.model as string, model: m.model || (ev.requested_model as string) })); break
      case "model_fallback": case "fallback": patchAi((m) => ({ ...m, modelActual: (ev.model as string) || (ev.to as string) || m.modelActual })); break
      case "doc_stream_open": {
        const title = (ev.title as string) || "Document"
        const language = ev.language as string | undefined
        if (artifactRef.current !== title) { usePanel.getState().showDoc(title, language); artifactRef.current = title }
        patchAi((m) => ({ ...m, artifact: { title, language, content: m.artifact?.content || "", closed: false } }))
        break
      }
      case "doc_stream_delta": { const c = (ev.content as string) || ""; usePanel.getState().setDocContent(c); patchAi((m) => ({ ...m, artifact: m.artifact ? { ...m.artifact, content: c } : { title: "Document", content: c, closed: false } })); break }
      case "doc_update": {
        if (ev.doc_id) usePanel.getState().setDocId(ev.doc_id as string)
        const c = ev.content as string | undefined
        if (c != null) usePanel.getState().setDocContent(c)
        patchAi((m) => ({ ...m, artifact: m.artifact ? { ...m.artifact, content: c ?? m.artifact.content, closed: true } : m.artifact })); break
      }
      case "doc_suggestions": {
        const panel = usePanel.getState()
        const docId = typeof ev.doc_id === "string" ? ev.doc_id : panel.doc?.docId
        if (docId && panel.doc?.docId !== docId) {
          try {
            const full = await fetch(`/api/document/${docId}`, { credentials: "same-origin" }).then((r) => r.json()) as { title?: string; language?: string; current_content?: string }
            panel.showDoc(full.title || "Document", full.language); panel.setDocId(docId); panel.setDocContent(full.current_content || "")
          } catch { /* keep the current panel if the target document cannot be loaded */ }
        }
        const suggestions = Array.isArray(ev.suggestions) ? ev.suggestions.map((value, i) => {
          const s = asRecord(value)
          return { id: String(s.id || `suggestion-${i + 1}`), find: String(s.find || ""), replace: String(s.replace || ""), reason: typeof s.reason === "string" ? s.reason : undefined }
        }).filter((s) => s.find) : []
        const current = usePanel.getState().doc?.suggestions || []
        const ids = new Set(current.map((s) => s.id))
        usePanel.getState().setDocSuggestions([...current, ...suggestions.filter((s) => !ids.has(s.id))])
        break
      }
      // Backend agent loop emits {tool, command, round} / {output, exit_code} / {tail, elapsed_s}.
      case "tool_start": patchAi((m) => {
        const tool: ToolEvent = { name: (ev.tool as string) || (ev.tool_name as string) || "tool", command: (ev.command as string) || (ev.tool_input as string) || undefined, round: ev.round as number | undefined, running: true }
        return { ...m, tools: [...(m.tools || []), tool], rounds: pushToolToRounds(ensureRounds(m), tool) }
      }); break
      case "tool_output": patchAi((m) => {
        const patch = (t: ToolEvent): ToolEvent => ({
          ...t, output: (ev.output as string) ?? (ev.tool_output as string) ?? "", exitCode: ev.exit_code as number | undefined, running: false,
          // Preserve rich artifacts streamed alongside the result.
          imageUrl: (ev.image_url as string) ?? t.imageUrl, imagePrompt: (ev.image_prompt as string) ?? t.imagePrompt,
          screenshot: (ev.screenshot as string) ?? t.screenshot, diff: (ev.diff as ToolEvent["diff"]) ?? t.diff,
        })
        const t = [...(m.tools || [])]; if (t.length) t[t.length - 1] = patch(t[t.length - 1])
        return { ...m, tools: t, rounds: patchLastTool(m.rounds, patch) }
      }); break
      case "tool_progress": patchAi((m) => {
        const tail = (ev.tail as string) || (ev.progress_text as string) || ""; const el = ev.elapsed_s as number | undefined
        const lastLine = tail ? tail.split("\n").filter(Boolean).slice(-1)[0] : ""
        const progress = [el != null ? `${el}s` : "", lastLine].filter(Boolean).join(" · ") || undefined
        const patch = (t: ToolEvent): ToolEvent => ({ ...t, progress, running: true })
        const t = [...(m.tools || [])]; if (t.length) t[t.length - 1] = patch(t[t.length - 1])
        return { ...m, tools: t, rounds: patchLastTool(m.rounds, patch) }
      }); break
      // Round delimiter: open a fresh round so the next round's text/tools render
      // in their own block (and keep the flat content paragraph-separated).
      case "agent_step": rawRef.current += "\n\n"; patchAi((m) => ({ ...m, content: m.content ? m.content + "\n\n" : m.content, rounds: m.rounds ? [...m.rounds, { text: "", tools: [] }] : m.rounds })); break
      case "web_sources": case "sources": case "research_sources": case "rag_sources":
        patchAi((m) => ({ ...m, sources: [...(m.sources || []), ...normalizeSources(ev.data)] })); break
      case "attachments": patchLastUser((m) => ({ ...m, attachments: normalizeAttachments(ev.data) })); break
      case "message_saved": patchAi((m) => ({ ...m, messageId: String(ev.id || "") || m.messageId })); break
      case "rounds_exhausted": patchAi((m) => ({ ...m, notice: {
        kind: "warning", text: `Reached the ${Number(ev.rounds) || "configured"}-step limit — the task is not finished.`,
        continuePrompt: "You hit the step limit before finishing — the task is not complete. Continue from exactly where you left off and keep going until it is done. Do NOT repeat work already done.",
      } })); break
      case "ask_user": patchAi((m) => ({ ...m, askUser: normalizeAskUser(ev.data) })); break
      // Agent wrote back to its plan (ticked a step / revised). Surface the
      // checklist live instead of dropping it (legacy showed a docked window).
      case "plan_update": { const plan = (ev.data as { plan?: string })?.plan; if (typeof plan === "string" && plan.trim()) patchAi((m) => ({ ...m, plan })); break }
      case "budget_exceeded": patchAi((m) => ({ ...m, notice: {
        kind: "warning", text: `Tool budget reached (${Number(ev.used) || 0}/${Number(ev.limit) || 0} calls). Agent stopped.`,
        continuePrompt: "Continue the task from exactly where you stopped. Do not repeat completed work, and finish the remaining work.",
      } })); break
      case "teacher_takeover": patchAi((m) => ({ ...m, notice: { kind: "info", text: `Teacher takeover: escalating to ${String(ev.teacher_model || "teacher")}${ev.student_failure ? ` — ${String(ev.student_failure)}` : ""}.` } })); break
      case "skill_saved": toast(`Learned skill: ${String(ev.skill_name || ev.name || "new skill")}`); break
      case "escalation_failed": case "skill_save_failed":
        patchAi((m) => ({ ...m, notice: { kind: "warning", text: String(ev.error || ev.text || "Agent escalation did not complete.") } })); break
      case "ui_control": applyUiControl(ev.data); break
      case "metrics": {
        // Backend emits input_tokens/output_tokens/tokens_per_second (nested under
        // `data`); keep the old names as fallbacks for safety.
        const dm = (ev.data as Record<string, unknown>) || ev
        patchAi((m) => ({ ...m, metrics: {
          tokens_in: (dm.input_tokens ?? dm.tokens_in) as number,
          tokens_out: (dm.output_tokens ?? dm.tokens_out) as number,
          cost: dm.cost as number,
          tok_per_sec: (dm.tokens_per_second ?? dm.tok_per_sec ?? dm.tokens_per_sec) as number,
        } })); break
      }
      case "workspace_rejected": {
        useComposer.getState().setWorkspace("")
        const path = ((ev.data as { path?: string })?.path || ev.path || "") as string
        toast(path ? `Workspace rejected: ${path}` : "Workspace rejected.")
        break
      }
      case "error": patchAi((m) => ({ ...m, notice: { kind: "error", text: String(ev.text || ev.error || "Stream error") } })); break
      case "research_progress":
        patchAi((m) => ({ ...m, research: researchPhase(ev.data as Record<string, unknown>) })); break
      case "research_done": {
        const rsid = (ev.data as { session_id?: string })?.session_id || sid
        try {
          const r = await fetch(`/api/research/result/${rsid}`, { method: "POST", credentials: "same-origin" })
          if (r.ok) {
            const j = await r.json()
            patchAi((m) => ({
              ...m,
              content: (j.result as string) || m.content,
              sources: (j.sources as Source[])?.length ? (j.sources as Source[]) : m.sources,
              research: undefined,
            }))
          }
        } catch { /* result fetch failed; leave progress as-is */ }
        break
      }
    }
  }, [applyUiControl, patchAi, patchLastUser])

  // Delete the ephemeral incognito session. Incognito messages are never
  // persisted server-side, so the session is an empty shell — dropping it on
  // leave keeps it out of recents (mirrors legacy _cleanupIncognitoSessions).
  // We do NOT name it "Incognito" because the backend deletes such rows
  // mid-flight (auto-sort + lazy purge) and 404s the stream.
  const dropIncognito = useCallback(() => {
    const inco = incognitoSidRef.current
    incognitoSidRef.current = null
    if (inco) {
      fetch(`/api/session/${inco}`, { method: "DELETE", credentials: "same-origin", keepalive: true })
        .then(() => qc.invalidateQueries({ queryKey: ["sessions"] }))
        .catch(() => { /* best-effort cleanup */ })
    }
  }, [qc])

  const reloadCanonicalHistory = useCallback(async (sid: string, failureText?: string) => {
    if (composer.incognito) return false
    try {
      const res = await fetch(`/api/history/${sid}`, { credentials: "same-origin" })
      if (!res.ok) return false
      const data = await res.json() as { history?: HistoryMsg[] }
      const canonical = historyToMessages(data.history || [])
      let lastUser = -1
      for (let i = canonical.length - 1; i >= 0; i--) { if (canonical[i].role === "user") { lastUser = i; break } }
      const hasReply = canonical.slice(lastUser + 1).some((m) => m.role === "assistant")
      if (failureText && !hasReply) canonical.push({ role: "assistant", content: "", notice: { kind: "error", text: failureText } })
      setMessages(canonical)
      seededRef.current = sid
      return hasReply
    } catch { return false }
  }, [composer.incognito])

  // Shared streaming path: appends nothing to the message list (the caller has
  // already placed the streaming assistant message), builds the request, and
  // streams the reply. Used by both send() and regenerate().
  const streamReply = useCallback(async (
    text: string, sid: string,
    opts: { model?: string; endpointId?: string; attachmentIds?: string[]; sendAs?: string; forceWeb?: boolean } = {},
  ) => {
    setStreaming(true)
    rawRef.current = ""; artifactRef.current = null
    const fd = new FormData()
    const panel = usePanel.getState()
    const activeDoc = panel.open && panel.kind === "doc" ? panel.doc : undefined
    const selections = activeDoc?.selections || []
    let payloadText = opts.sendAs || text
    if (selections.length === 1) {
      const s = selections[0]
      const lineRef = s.startLine === s.endLine ? `line ${s.startLine}` : `lines ${s.startLine}-${s.endLine}`
      payloadText = `In the document, edit this specific text (${lineRef}):\n\`\`\`\n${s.text}\n\`\`\`\n\nInstruction: ${payloadText}`
    } else if (selections.length > 1) {
      const parts = selections.map((s, i) => `Selection ${i + 1} (${s.startLine === s.endLine ? `line ${s.startLine}` : `lines ${s.startLine}-${s.endLine}`}):\n\`\`\`\n${s.text}\n\`\`\``)
      payloadText = `In the document, edit these specific sections:\n\n${parts.join("\n\n")}\n\nInstruction: ${payloadText}`
    }
    if (selections.length) panel.setDocSelections([])
    fd.set("message", payloadText)
    fd.set("session", sid)
    if (opts.attachmentIds && opts.attachmentIds.length) fd.set("attachments", JSON.stringify(opts.attachmentIds))
    const effectiveMode = activeDoc?.docId ? "agent" : composer.mode
    fd.set("mode", effectiveMode)
    if (activeDoc?.docId) fd.set("active_doc_id", activeDoc.docId)
    fd.set("allow_bash", String(composer.allowBash))
    const useWeb = opts.forceWeb || composer.useWeb
    if (effectiveMode === "chat" && useWeb) fd.set("use_web", "true")
    if (effectiveMode === "agent") fd.set("allow_web_search", String(useWeb))
    if (composer.useResearch) fd.set("use_research", "true")
    if (!composer.useRag) fd.set("use_rag", "false")
    if (composer.incognito) fd.set("incognito", "true")
    if (composer.workspace) fd.set("workspace", composer.workspace)
    if (opts.model) fd.set("model", opts.model)
    if (opts.endpointId) fd.set("endpoint_id", opts.endpointId)
    if (composer.presetId) fd.set("preset_id", composer.presetId)

    const ctrl = new AbortController(); abortRef.current = ctrl
    try {
      await streamChat(fd, (e: SseEvent) => handleEvent(e, sid), ctrl.signal)
    } catch (err) {
      // A user Stop / unmount aborts the fetch (AbortError) and is expected;
      // anything else is a genuine stream error worth logging + showing.
      if ((err as Error)?.name !== "AbortError") {
        console.error("chat stream failed:", err)
        const message = err instanceof Error ? err.message : "The agent run failed before completion."
        if (err instanceof StreamInterruptedError) {
          patchAi((m) => ({ ...m, notice: { kind: "info", text: "Connection interrupted. Reconnecting to the running agent…" } }))
          try {
            // Resume replays the detached run from event zero, so reset only the
            // current assistant bubble before applying the replayed events.
            patchAi((m) => ({ role: "assistant", content: "", reasoning: "", tools: [], rounds: undefined, sources: [], streaming: true, model: m.model }))
            await streamResume(sid, (e: SseEvent) => handleEvent(e, sid), ctrl.signal)
          } catch (resumeErr) {
            if ((resumeErr as Error)?.name !== "AbortError") {
              const resumeMessage = resumeErr instanceof Error ? resumeErr.message : message
              const restored = await reloadCanonicalHistory(sid, resumeMessage)
              if (!restored) patchAi((m) => ({ ...m, notice: { kind: "error", text: resumeMessage, continuePrompt: "Continue from exactly where you left off. Do not repeat completed work." } }))
            }
          }
        } else {
          const restored = await reloadCanonicalHistory(sid, message)
          if (!restored) patchAi((m) => ({ ...m, notice: { kind: "error", text: message, continuePrompt: err instanceof SseResponseError ? undefined : "Continue from exactly where you left off. Do not repeat completed work." } }))
        }
      }
    } finally {
      patchAi((m) => ({ ...m, streaming: false }))
      setStreaming(false); abortRef.current = null
      qc.invalidateQueries({ queryKey: ["sessions"] })
    }
  }, [composer, handleEvent, patchAi, qc, reloadCanonicalHistory])

  const patchGroupMessage = useCallback((runId: string, participantId: string, fn: (m: ChatMessage) => ChatMessage) =>
    setMessages((prev) => prev.map((m) => (
      m.groupRunId === runId && m.groupParticipantId === participantId ? fn(m) : m
    ))), [])

  const ensureGroupRuntime = useCallback(async (): Promise<{ parentId: string; participants: GroupRuntimeParticipant[]; created: boolean }> => {
    const currentParticipants = composer.groupParticipants
    const complete = currentParticipants.every((p) => p.sessionId)
    if (composer.groupParentId && complete) {
      return {
        parentId: composer.groupParentId,
        participants: currentParticipants.map((p) => ({ ...p, sessionId: p.sessionId!, groupName: p.groupName || p.display })),
        created: false,
      }
    }
    const runtime = await startGroupRuntime(currentParticipants)
    useComposer.getState().setGroupRuntime(runtime.parentId, runtime.participants)
    sidRef.current = runtime.parentId
    seededRef.current = runtime.parentId
    qc.invalidateQueries({ queryKey: ["sessions"] })
    navigate(`/chat/${runtime.parentId}`, { replace: true })
    return { parentId: runtime.parentId, participants: runtime.participants, created: true }
  }, [composer.groupParentId, composer.groupParticipants, navigate, qc])

  const syncParticipantReply = useCallback(async (
    participant: GroupRuntimeParticipant,
    participants: GroupRuntimeParticipant[],
    content: string,
  ) => {
    if (!content) return
    await Promise.allSettled(participants.map((other) => {
      if (other.id === participant.id) return Promise.resolve()
      return injectMessages(other.sessionId, [{ role: "user", content: `[${participant.groupName}]: ${content}` }])
    }))
  }, [])

  const sendGroup = useCallback(async (text: string, attachmentIds?: string[], sendAs?: string, opts: { attachments?: ChatAttachment[] } = {}) => {
    if (composer.groupParticipants.length < 2) { toast("Group chat needs at least 2 participants."); return }
    if (streaming) return

    const display = text
    const payload = sendAs || text
    let runtime: { parentId: string; participants: GroupRuntimeParticipant[]; created: boolean }
    try {
      runtime = await ensureGroupRuntime()
      if (runtime.created) {
        saveGroupPresetIfMissing(runtime.participants, composer.groupMode)
          .finally(() => qc.invalidateQueries({ queryKey: ["preset-groups"] }))
          .catch(() => { /* best-effort parity autosave */ })
      }
    } catch (err) {
      console.error("group setup failed:", err)
      toast("Couldn't start the group chat.")
      return
    }

    const runId = `group-${Date.now()}`
    setStreaming(true)
    rawRef.current = ""; artifactRef.current = null
    const attachments = opts.attachments?.length ? opts.attachments : (attachmentIds || []).map((id) => ({ id, name: "Attachment" }))
    if (runtime.created) setMessages([{ role: "user", content: display, attachments }])
    else setMessages((prev) => [...prev, { role: "user", content: display, attachments }])
    injectMessages(runtime.parentId, [{ role: "user", content: display, metadata: { attachments } }]).catch(() => { /* best-effort parent transcript */ })

    const streamParticipant = async (participant: GroupRuntimeParticipant) => {
      const ctrl = new AbortController()
      groupAbortRefs.current.push(ctrl)
      setMessages((prev) => [...prev, {
        role: "assistant",
        content: "",
        streaming: true,
        model: participant.model,
        groupName: participant.groupName,
        groupParticipantId: participant.id,
        groupRunId: runId,
      }])
      try {
        const content = await streamGroupReply(
          participant,
          payload,
          { useRag: composer.useRag, attachmentIds },
          (delta) => patchGroupMessage(runId, participant.id, (m) => ({ ...m, content: m.content + delta })),
          ctrl.signal,
        )
        patchGroupMessage(runId, participant.id, (m) => ({ ...m, content: content || m.content || "_(no response)_", streaming: false }))
        if (content) {
          injectMessages(runtime.parentId, [{
            role: "assistant",
            content,
            metadata: { group_model: participant.groupName, model: participant.model },
          }]).catch(() => { /* best-effort parent transcript */ })
        }
        return content
      } catch (err) {
        if ((err as Error)?.name === "AbortError") {
          patchGroupMessage(runId, participant.id, (m) => ({ ...m, streaming: false }))
        } else {
          console.error("group stream failed:", err)
          patchGroupMessage(runId, participant.id, (m) => ({ ...m, content: m.content || "_(stream interrupted)_", streaming: false }))
        }
        return ""
      }
    }

    try {
      if (composer.groupMode === "parallel") {
        const replies = await Promise.all(runtime.participants.map((participant) => streamParticipant(participant)))
        await Promise.allSettled(replies.map((reply, i) => syncParticipantReply(runtime.participants[i], runtime.participants, reply)))
      } else {
        const order = [...runtime.participants]
        for (let i = order.length - 1; i > 0; i--) {
          const j = Math.floor(Math.random() * (i + 1))
          ;[order[i], order[j]] = [order[j], order[i]]
        }
        for (const participant of order) {
          const reply = await streamParticipant(participant)
          await syncParticipantReply(participant, runtime.participants, reply)
        }
      }
    } finally {
      groupAbortRefs.current = []
      setStreaming(false)
      qc.invalidateQueries({ queryKey: ["history", runtime.parentId] })
      qc.invalidateQueries({ queryKey: ["sessions"] })
    }
  }, [composer.groupMode, composer.groupParticipants, composer.useRag, ensureGroupRuntime, patchGroupMessage, qc, streaming, syncParticipantReply])

  const send = useCallback(async (text: string, attachmentIds?: string[], sendAs?: string, opts: { forceWeb?: boolean; attachments?: ChatAttachment[] } = {}) => {
    if ((!text.trim() && !attachmentIds?.length) || streaming) return
    if (composer.groupActive) {
      await sendGroup(text, attachmentIds, sendAs, opts)
      return
    }
    // Resolve a model up-front. ModelPicker seeds composer.model from
    // /api/default-chat on mount, but a send fired before that resolves (very
    // first visit, empty persisted store) would create a model-less session,
    // which chat_stream rejects with a 404. Fall back to the default so the
    // first send never silently fails.
    let model = composer.model, endpointId = composer.endpointId, endpointUrl = composer.endpointUrl
    if (!model) {
      try {
        const def = await fetch("/api/default-chat", { credentials: "same-origin" }).then((r) => r.json())
        if (def?.model) { model = def.model; endpointId = def.endpoint_id || endpointId; endpointUrl = def.endpoint_url || endpointUrl }
      } catch { /* backend still surfaces a clear "pick a model" message if empty */ }
    }
    let sid = sidRef.current
    if (!sid) {
      // New chat: clean up any prior ephemeral incognito session first.
      dropIncognito()
      const s = await createSession({
        // Name incognito chats generically so the topic never shows in the
        // sidebar; incognito skips server-side auto-rename so this name sticks.
        // It's a normal name (not "Incognito") so the stream isn't 404'd — we
        // delete the session ourselves on leave instead.
        name: composer.incognito ? "New chat" : (text.slice(0, 48) || "New chat"),
        model, endpoint_id: endpointId, endpoint_url: endpointUrl,
      })
      sid = s.id; sidRef.current = sid; seededRef.current = sid
      if (composer.incognito) incognitoSidRef.current = sid
      qc.invalidateQueries({ queryKey: ["sessions"] })
      navigate(`/chat/${sid}`, { replace: true })
    } else {
      seededRef.current = sid
    }
    const optimisticAttachments = opts.attachments?.length
      ? opts.attachments
      : (attachmentIds || []).map((id) => ({ id, name: "Attachment" }))
    setMessages((prev) => [...prev, { role: "user", content: text, attachments: optimisticAttachments }, { role: "assistant", content: "", reasoning: "", tools: [], sources: [], streaming: true }])
    await streamReply(text, sid, { model, endpointId, attachmentIds, sendAs, forceWeb: opts.forceWeb })
  }, [streaming, composer, sendGroup, navigate, qc, dropIncognito, streamReply])

  const localReply = useCallback((display: string, reply: string) => {
    if (!display.trim()) return
    setMessages((prev) => [...prev, { role: "user", content: display }, { role: "assistant", content: reply }])
  }, [])

  const clearLocalMessages = useCallback((reply?: string) => {
    setMessages(reply ? [{ role: "assistant", content: reply }] : [])
  }, [])

  // Regenerate the assistant reply to the user message at `userIndex`: drop that
  // reply (and anything after) and stream a fresh one — without re-appending a
  // duplicate user turn.
  const regenerate = useCallback(async (userIndex: number) => {
    if (streaming) return
    const sid = sidRef.current
    if (!sid) return
    const user = messages[userIndex]
    if (!user || user.role !== "user") return
    const attachmentIds = (user.attachments || []).map((a) => a.id).filter((id): id is string => !!id)
    if (!user.content.trim() && !attachmentIds.length) return
    setMessages((prev) => [...prev.slice(0, userIndex + 1), { role: "assistant", content: "", reasoning: "", tools: [], sources: [], streaming: true }])
    await streamReply(user.content, sid, { model: composer.model || undefined, endpointId: composer.endpointId || undefined, attachmentIds })
  }, [streaming, messages, composer.model, composer.endpointId, streamReply])

  // Edit a previous user turn and resend it: truncate the thread (server + local)
  // back to before that turn, then stream a fresh reply — so the edited message
  // REPLACES the original instead of appending a duplicate turn (parity with the
  // legacy edit-in-place flow).
  const editResend = useCallback(async (userIndex: number, newText: string) => {
    if (streaming) return
    const sid = sidRef.current
    if (!sid) return
    const original = messages[userIndex]
    const attachments = original?.role === "user" ? (original.attachments || []) : []
    const attachmentIds = attachments.map((a) => a.id).filter((id): id is string => !!id)
    if (!newText.trim() && !attachmentIds.length) return
    try {
      await fetch(`/api/session/${sid}/truncate`, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keep_count: userIndex }),
      })
    } catch { /* best-effort; the resend still replaces the local turn */ }
    setMessages((prev) => [...prev.slice(0, userIndex), { role: "user", content: newText, attachments }, { role: "assistant", content: "", reasoning: "", tools: [], sources: [], streaming: true }])
    await streamReply(newText, sid, { model: composer.model || undefined, endpointId: composer.endpointId || undefined, attachmentIds })
  }, [streaming, messages, composer.model, composer.endpointId, streamReply])

  const editAssistant = useCallback(async (index: number, content: string) => {
    if (streaming) return false
    const sid = sidRef.current
    const message = messages[index]
    if (!sid || message?.role !== "assistant" || !message.messageId) {
      toast("This message is still being saved. Try again in a moment.")
      return false
    }
    const res = await fetch(`/api/session/${encodeURIComponent(sid)}/edit-message`, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ msg_id: message.messageId, content }),
    })
    if (!res.ok) { toast("Couldn't save the message edit."); return false }
    // Drop the agent round/tool reconstruction so the manually edited text
    // renders flat instead of the now-stale interleaved rounds.
    setMessages((prev) => prev.map((m, i) => i === index ? { ...m, content, rounds: undefined, tools: undefined, edited: true } : m))
    toast("Message edited", "success")
    return true
  }, [messages, streaming])

  const deleteMessage = useCallback(async (index: number) => {
    if (streaming) return
    const sid = sidRef.current
    const message = messages[index]
    if (!sid || !message) return
    const payload = message.messageId ? { msg_ids: [message.messageId] } : { indices: [index] }
    const res = await fetch(`/api/session/${encodeURIComponent(sid)}/delete-messages`, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    })
    if (!res.ok) { toast("Couldn't delete the message."); return }
    setMessages((prev) => prev.filter((_, i) => i !== index))
    qc.invalidateQueries({ queryKey: ["sessions"] })
    toast("Message deleted", "success")
  }, [messages, qc, streaming])

  const forkFrom = useCallback(async (index: number) => {
    if (streaming) return
    const sid = sidRef.current
    if (!sid) return
    const res = await fetch(`/api/session/${encodeURIComponent(sid)}/fork`, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keep_count: index + 1 }),
    })
    if (!res.ok) { toast("Couldn't fork the conversation."); return }
    const fork = await res.json() as { id: string; name?: string }
    await qc.invalidateQueries({ queryKey: ["sessions"] })
    toast(`Forked${fork.name ? ` → ${fork.name}` : ""}`, "success")
    navigate(`/chat/${fork.id}`)
  }, [navigate, qc, streaming])

  const rewriteMessage = useCallback(async (index: number, instruction: string) => {
    if (streaming) return
    const sid = sidRef.current
    const original = messages[index]
    if (!sid || original?.role !== "assistant" || !original.content.trim()) return
    // Clearing rounds/tools makes the fresh rewrite render flat; on error we
    // restore `original` (which still carries them).
    setMessages((prev) => prev.map((m, i) => i === index ? { ...m, content: "", reasoning: "", rounds: undefined, tools: undefined, streaming: true } : m))
    setStreaming(true)
    let content = ""
    try {
      const res = await fetch("/api/rewrite", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sid, msg_id: original.messageId, original_text: original.content, instruction }),
      })
      if (!res.ok || !res.body) throw new Error(`Rewrite failed (${res.status})`)
      await readSse(res.body, (event) => {
        if (event.thinking || typeof event.delta !== "string") return
        content += event.delta
        setMessages((prev) => prev.map((m, i) => i === index ? { ...m, content } : m))
      })
      if (!content.trim()) throw new Error("The model returned an empty rewrite.")
      setMessages((prev) => prev.map((m, i) => i === index ? { ...m, content: content.trim(), streaming: false, edited: true } : m))
    } catch (error) {
      setMessages((prev) => prev.map((m, i) => i === index ? { ...original, streaming: false } : m))
      toast(error instanceof Error ? error.message : "Couldn't rewrite the message.")
    } finally {
      setStreaming(false)
    }
  }, [messages, streaming])

  // Clean up the ephemeral incognito session when leaving: on SPA unmount
  // (in-app route change) AND on pagehide (tab close / refresh / hard nav),
  // where React effect cleanups don't run. keepalive lets the DELETE finish
  // as the page unloads.
  useEffect(() => {
    const onHide = () => {
      const inco = incognitoSidRef.current
      if (inco) fetch(`/api/session/${inco}`, { method: "DELETE", credentials: "same-origin", keepalive: true }).catch(() => { /* best-effort */ })
    }
    window.addEventListener("pagehide", onHide)
    return () => { window.removeEventListener("pagehide", onHide); dropIncognito() }
  }, [dropIncognito])

  const stop = useCallback(async () => {
    if (groupAbortRefs.current.length) {
      groupAbortRefs.current.forEach((ctrl) => ctrl.abort())
      for (const participant of useComposer.getState().groupParticipants) {
        if (participant.sessionId) {
          try { await fetch(`/api/chat/stop/${participant.sessionId}`, { method: "POST", credentials: "same-origin" }) } catch { /* ignore */ }
        }
      }
      groupAbortRefs.current = []
    }
    abortRef.current?.abort()
    const sid = sidRef.current
    if (sid) {
      resumeRef.current = sid
      try { await fetch(`/api/chat/stop/${sid}`, { method: "POST", credentials: "same-origin" }) } catch { /* ignore */ }
    }
    patchAi((m) => ({ ...m, streaming: false, notice: { kind: "stopped", text: m.content ? "Message interrupted." : "Cancelled by user.", continuePrompt: m.content ? "Your previous response was interrupted. Continue exactly where you left off. Do NOT repeat work already completed." : undefined } }))
    setStreaming(false)
  }, [patchAi])

  // Reconnect to a detached run still streaming server-side (e.g. the user
  // navigated away mid-response and came back). Additive: only fires when this
  // hook isn't itself streaming and there's a genuinely active stream.
  // Declared after handleEvent/patchAi so it doesn't reference them in the TDZ.
  useEffect(() => {
    const sid = sessionId
    if (!sid || streaming || resumeRef.current === sid) return
    let cancelled = false
    const ctrl = new AbortController()
    ;(async () => {
      try {
        const s = await fetch(`/api/chat/stream_status/${sid}`, { credentials: "same-origin" })
        if (!s.ok || cancelled || sidRef.current !== sid) return
        resumeRef.current = sid
        setMessages((prev) => prev[prev.length - 1]?.streaming ? prev : [...prev, { role: "assistant", content: "", reasoning: "", tools: [], sources: [], streaming: true }])
        setStreaming(true)
        rawRef.current = ""; artifactRef.current = null
        try {
          await streamResume(sid, (e) => handleEvent(e, sid), ctrl.signal)
        } finally {
          patchAi((m) => ({ ...m, streaming: false }))
          setStreaming(false)
          seededRef.current = null // re-seed from the now-complete saved history
          qc.invalidateQueries({ queryKey: ["history", sid] })
          qc.invalidateQueries({ queryKey: ["sessions"] })
        }
      } catch (err) {
        if (!cancelled && (err as Error)?.name !== "AbortError" && resumeRef.current === sid) {
          const message = err instanceof Error ? err.message : "The running agent could not be resumed."
          const restored = await reloadCanonicalHistory(sid, message)
          if (!restored) patchAi((m) => ({ ...m, streaming: false, notice: { kind: "error", text: message, continuePrompt: "Continue from exactly where you left off. Do not repeat completed work." } }))
          setStreaming(false)
        }
        // A 404 before resumeRef is set simply means there is no active run.
      }
    })()
    return () => { cancelled = true; ctrl.abort() }
  }, [sessionId, streaming, handleEvent, patchAi, qc, reloadCanonicalHistory])

  return { messages, streaming, send, stop, regenerate, editResend, editAssistant, deleteMessage, forkFrom, rewriteMessage, localReply, clearLocalMessages }
}
