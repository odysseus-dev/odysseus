import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { useQueryClient } from "@tanstack/react-query"
import { streamChat, streamResume, type SseEvent } from "@/lib/sse"
import { useComposer } from "@/stores/composer"
import { usePanel } from "@/stores/panel"
import { parseArtifact } from "@/lib/artifact"
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
  const incognitoSidRef = useRef<string | null>(null) // ephemeral incognito session to delete on leave
  const rawRef = useRef<string>("")          // full assistant text this stream (for fence parsing)
  const artifactRef = useRef<string | null>(null) // title of the doc currently open in the panel
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
      patchAi((m) => ({ ...m, content: m.content + d, artifact: artifact || m.artifact }))
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
      // Backend agent loop emits {tool, command, round} / {output, exit_code} / {tail, elapsed_s}.
      case "tool_start": patchAi((m) => ({ ...m, tools: [...(m.tools || []), { name: (ev.tool as string) || (ev.tool_name as string) || "tool", command: (ev.command as string) || (ev.tool_input as string) || undefined, round: ev.round as number | undefined, running: true }] })); break
      case "tool_output": patchAi((m) => { const t = [...(m.tools || [])]; if (t.length) t[t.length - 1] = { ...t[t.length - 1], output: (ev.output as string) ?? (ev.tool_output as string) ?? "", exitCode: ev.exit_code as number | undefined, running: false }; return { ...m, tools: t } }); break
      case "tool_progress": patchAi((m) => { const t = [...(m.tools || [])]; if (t.length) { const tail = (ev.tail as string) || (ev.progress_text as string) || ""; const el = ev.elapsed_s as number | undefined; const lastLine = tail ? tail.split("\n").filter(Boolean).slice(-1)[0] : ""; t[t.length - 1] = { ...t[t.length - 1], progress: [el != null ? `${el}s` : "", lastLine].filter(Boolean).join(" · ") || undefined, running: true } } return { ...m, tools: t } }); break
      case "agent_step": rawRef.current += "\n\n"; patchAi((m) => ({ ...m, content: m.content ? m.content + "\n\n" : m.content })); break // round delimiter — keep each round's text in its own paragraph
      case "web_sources": case "sources": case "research_sources":
        patchAi((m) => ({ ...m, sources: [...(m.sources || []), ...((ev.data as []) || [])] })); break
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
      case "error": patchAi((m) => ({ ...m, content: m.content + (m.content ? "\n\n" : "") + `⚠️ ${(ev.text as string) || (ev.error as string) || "Stream error"}` })); break
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

  // Shared streaming path: appends nothing to the message list (the caller has
  // already placed the streaming assistant message), builds the request, and
  // streams the reply. Used by both send() and regenerate().
  const streamReply = useCallback(async (
    text: string, sid: string,
    opts: { model?: string; endpointId?: string; attachmentIds?: string[]; sendAs?: string } = {},
  ) => {
    setStreaming(true)
    rawRef.current = ""; artifactRef.current = null
    const fd = new FormData()
    fd.set("message", opts.sendAs || text)
    fd.set("session", sid)
    if (opts.attachmentIds && opts.attachmentIds.length) fd.set("attachments", JSON.stringify(opts.attachmentIds))
    fd.set("mode", composer.mode)
    fd.set("allow_bash", String(composer.allowBash))
    if (composer.mode === "chat" && composer.useWeb) fd.set("use_web", "true")
    if (composer.mode === "agent") fd.set("allow_web_search", String(composer.useWeb))
    if (composer.useResearch) fd.set("use_research", "true")
    if (!composer.useRag) fd.set("use_rag", "false")
    if (composer.incognito) fd.set("incognito", "true")
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
        patchAi((m) => ({ ...m, content: m.content || "_(stream interrupted)_" }))
      }
    } finally {
      patchAi((m) => ({ ...m, streaming: false }))
      setStreaming(false); abortRef.current = null
      qc.invalidateQueries({ queryKey: ["sessions"] })
    }
  }, [composer, handleEvent, patchAi, qc])

  const send = useCallback(async (text: string, attachmentIds?: string[], sendAs?: string) => {
    if (!text.trim() || streaming) return
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
    setMessages((prev) => [...prev, { role: "user", content: text }, { role: "assistant", content: "", reasoning: "", tools: [], sources: [], streaming: true }])
    await streamReply(text, sid, { model, endpointId, attachmentIds, sendAs })
  }, [streaming, composer, navigate, qc, dropIncognito, streamReply])

  // Regenerate the assistant reply to the user message at `userIndex`: drop that
  // reply (and anything after) and stream a fresh one — without re-appending a
  // duplicate user turn.
  const regenerate = useCallback(async (text: string, userIndex: number) => {
    if (streaming || !text.trim()) return
    const sid = sidRef.current
    if (!sid) return
    setMessages((prev) => [...prev.slice(0, userIndex + 1), { role: "assistant", content: "", reasoning: "", tools: [], sources: [], streaming: true }])
    await streamReply(text, sid, { model: composer.model || undefined, endpointId: composer.endpointId || undefined })
  }, [streaming, composer.model, composer.endpointId, streamReply])

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
      } catch { /* no active stream (404) — normal case */ }
    })()
    return () => { cancelled = true; ctrl.abort() }
  }, [sessionId, streaming, handleEvent, patchAi, qc])

  return { messages, streaming, send, stop, regenerate }
}
