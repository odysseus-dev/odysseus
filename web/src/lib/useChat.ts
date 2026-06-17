import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { useQueryClient } from "@tanstack/react-query"
import { streamChat, streamResume, type SseEvent } from "@/lib/sse"
import { useComposer } from "@/stores/composer"
import { usePanel } from "@/stores/panel"
import { createSession, useHistory } from "@/api/sessions"
import type { ChatMessage, HistoryMsg, Source } from "@/types"

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
function historyToMessages(h: HistoryMsg[]): ChatMessage[] {
  return h
    .filter((m) => m.role === "user" || m.role === "assistant")
    .map((m) => ({ role: m.role as "user" | "assistant", content: flatten(m.content), model: m.model }))
}

export function useChat(sessionId?: string) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const composer = useComposer()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const sidRef = useRef<string | undefined>(sessionId)
  const seededRef = useRef<string | null>(null)
  const resumeRef = useRef<string | null>(null)
  const { data: history } = useHistory(sessionId)

  useEffect(() => { sidRef.current = sessionId }, [sessionId])

  useEffect(() => {
    if (streaming) return
    const sid = sessionId || null
    if (seededRef.current === sid) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- seed local message list from loaded server history
    if (!sid) { setMessages([]); seededRef.current = null; return }
    if (history?.history) { setMessages(historyToMessages(history.history)); seededRef.current = sid }
  }, [sessionId, history, streaming])

  const patchAi = useCallback((fn: (m: ChatMessage) => ChatMessage) =>
    setMessages((prev) => {
      const c = [...prev]; const i = c.length - 1
      if (i >= 0 && c[i].role === "assistant") c[i] = fn(c[i])
      return c
    }), [])

  const handleEvent = useCallback(async (e: SseEvent, sid: string) => {
    const ev = e as Record<string, unknown>
    if (typeof ev.delta === "string") {
      const d = ev.delta as string
      if (ev.thinking) patchAi((m) => ({ ...m, reasoning: (m.reasoning || "") + d }))
      else patchAi((m) => ({ ...m, content: m.content + d }))
      return
    }
    switch (e.type) {
      case "model_info": patchAi((m) => ({ ...m, model: ev.model as string })); break
      case "model_actual": patchAi((m) => ({ ...m, modelActual: ev.model as string, model: m.model || (ev.requested_model as string) })); break
      case "doc_stream_open": usePanel.getState().showDoc((ev.title as string) || "Document", ev.language as string); break
      case "doc_stream_delta": usePanel.getState().setDocContent((ev.content as string) || ""); break
      case "doc_update": if (ev.doc_id) usePanel.getState().setDocId(ev.doc_id as string); break
      case "tool_start": patchAi((m) => ({ ...m, tools: [...(m.tools || []), { name: (ev.tool_name as string) || "tool", input: ev.tool_input }] })); break
      case "tool_output": patchAi((m) => { const t = [...(m.tools || [])]; if (t.length) t[t.length - 1] = { ...t[t.length - 1], output: ev.tool_output as string }; return { ...m, tools: t } }); break
      case "tool_progress": patchAi((m) => { const t = [...(m.tools || [])]; if (t.length) t[t.length - 1] = { ...t[t.length - 1], progress: ev.progress_text as string }; return { ...m, tools: t } }); break
      case "web_sources": case "sources": case "research_sources":
        patchAi((m) => ({ ...m, sources: [...(m.sources || []), ...((ev.data as []) || [])] })); break
      case "metrics":
        patchAi((m) => ({ ...m, metrics: { tokens_in: ev.tokens_in as number, tokens_out: ev.tokens_out as number, cost: ev.cost as number, tok_per_sec: ev.tok_per_sec as number } })); break
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
  }, [patchAi])

  const send = useCallback(async (text: string, attachmentIds?: string[], sendAs?: string) => {
    if (!text.trim() || streaming) return
    let sid = sidRef.current
    if (!sid) {
      const s = await createSession({
        name: text.slice(0, 48) || "New chat",
        model: composer.model, endpoint_id: composer.endpointId, endpoint_url: composer.endpointUrl,
      })
      sid = s.id; sidRef.current = sid; seededRef.current = sid
      qc.invalidateQueries({ queryKey: ["sessions"] })
      navigate(`/chat/${sid}`, { replace: true })
    } else {
      seededRef.current = sid
    }
    setMessages((prev) => [...prev, { role: "user", content: text }, { role: "assistant", content: "", reasoning: "", tools: [], sources: [], streaming: true }])
    setStreaming(true)

    const fd = new FormData()
    fd.set("message", sendAs || text)
    fd.set("session", sid)
    if (attachmentIds && attachmentIds.length) fd.set("attachments", JSON.stringify(attachmentIds))
    fd.set("mode", composer.mode)
    fd.set("allow_bash", String(composer.allowBash))
    if (composer.mode === "chat" && composer.useWeb) fd.set("use_web", "true")
    if (composer.mode === "agent") fd.set("allow_web_search", String(composer.useWeb))
    if (composer.useResearch) fd.set("use_research", "true")
    if (!composer.useRag) fd.set("use_rag", "false")
    if (composer.incognito) fd.set("incognito", "true")
    if (composer.model) fd.set("model", composer.model)
    if (composer.endpointId) fd.set("endpoint_id", composer.endpointId)
    if (composer.presetId) fd.set("preset_id", composer.presetId)

    const ctrl = new AbortController(); abortRef.current = ctrl
    try {
      await streamChat(fd, (e: SseEvent) => handleEvent(e, sid!), ctrl.signal)
    } catch {
      patchAi((m) => ({ ...m, content: m.content || "_(stream interrupted)_" }))
    } finally {
      patchAi((m) => ({ ...m, streaming: false }))
      setStreaming(false); abortRef.current = null
      qc.invalidateQueries({ queryKey: ["sessions"] })
    }
  }, [streaming, composer, navigate, qc, handleEvent, patchAi])

  const stop = useCallback(async () => {
    abortRef.current?.abort()
    const sid = sidRef.current
    if (sid) { try { await fetch(`/api/chat/stop/${sid}`, { method: "POST", credentials: "same-origin" }) } catch { /* ignore */ } }
    setStreaming(false)
  }, [])

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
        try {
          await streamResume(sid, (e) => handleEvent(e, sid), ctrl.signal)
        } finally {
          patchAi((m) => ({ ...m, streaming: false }))
          setStreaming(false)
          seededRef.current = null // re-seed from the now-complete saved history
          qc.invalidateQueries({ queryKey: ["history", sid] })
          qc.invalidateQueries({ queryKey: ["sessions"] })
        }
      } catch { /* no active stream (404) — normal case */ }
    })()
    return () => { cancelled = true; ctrl.abort() }
  }, [sessionId, streaming, handleEvent, patchAi, qc])

  return { messages, streaming, send, stop }
}
