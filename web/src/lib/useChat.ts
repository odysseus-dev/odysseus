import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { useQueryClient } from "@tanstack/react-query"
import { streamChat, type SseEvent } from "@/lib/sse"
import { useComposer } from "@/stores/composer"
import { createSession, useHistory } from "@/api/sessions"
import type { ChatMessage, HistoryMsg } from "@/types"

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
  const { data: history } = useHistory(sessionId)

  useEffect(() => { sidRef.current = sessionId }, [sessionId])

  // Seed from persisted history, but never clobber an in-flight stream or a
  // session we're already managing optimistically (seededRef).
  useEffect(() => {
    if (streaming) return
    const sid = sessionId || null
    if (seededRef.current === sid) return
    if (!sid) { setMessages([]); seededRef.current = null; return }
    if (history?.history) { setMessages(historyToMessages(history.history)); seededRef.current = sid }
  }, [sessionId, history, streaming])

  const patchAi = (fn: (m: ChatMessage) => ChatMessage) =>
    setMessages((prev) => {
      const c = [...prev]; const i = c.length - 1
      if (i >= 0 && c[i].role === "assistant") c[i] = fn(c[i])
      return c
    })

  const send = useCallback(async (text: string) => {
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
    setMessages((prev) => [...prev, { role: "user", content: text }, { role: "assistant", content: "", tools: [], sources: [], streaming: true }])
    setStreaming(true)

    const fd = new FormData()
    fd.set("message", text)
    fd.set("session", sid)
    fd.set("mode", composer.mode)
    fd.set("allow_bash", String(composer.allowBash))
    if (composer.mode === "chat" && composer.useWeb) fd.set("use_web", "true")
    if (composer.mode === "agent") fd.set("allow_web_search", String(composer.useWeb))
    if (composer.useResearch) fd.set("use_research", "true")
    if (!composer.useRag) fd.set("use_rag", "false")
    if (composer.incognito) fd.set("incognito", "true")
    if (composer.model) fd.set("model", composer.model)
    if (composer.endpointId) fd.set("endpoint_id", composer.endpointId)

    const ctrl = new AbortController(); abortRef.current = ctrl
    try {
      await streamChat(fd, (e: SseEvent) => {
        const ev = e as Record<string, unknown>
        if (typeof ev.delta === "string") { const d = ev.delta as string; patchAi((m) => ({ ...m, content: m.content + d })); return }
        switch (e.type) {
          case "model_info": patchAi((m) => ({ ...m, model: ev.model as string })); break
          case "tool_start": patchAi((m) => ({ ...m, tools: [...(m.tools || []), { name: (ev.tool_name as string) || "tool", input: ev.tool_input }] })); break
          case "tool_output": patchAi((m) => { const t = [...(m.tools || [])]; if (t.length) t[t.length - 1] = { ...t[t.length - 1], output: ev.tool_output as string }; return { ...m, tools: t } }); break
          case "tool_progress": patchAi((m) => { const t = [...(m.tools || [])]; if (t.length) t[t.length - 1] = { ...t[t.length - 1], progress: ev.progress_text as string }; return { ...m, tools: t } }); break
          case "web_sources": case "sources": case "research_sources":
            patchAi((m) => ({ ...m, sources: [...(m.sources || []), ...((ev.data as []) || [])] })); break
        }
      }, ctrl.signal)
    } catch {
      patchAi((m) => ({ ...m, content: m.content || "_(stream interrupted)_" }))
    } finally {
      patchAi((m) => ({ ...m, streaming: false }))
      setStreaming(false); abortRef.current = null
      qc.invalidateQueries({ queryKey: ["sessions"] })
    }
  }, [streaming, composer, navigate, qc])

  const stop = useCallback(async () => {
    abortRef.current?.abort()
    const sid = sidRef.current
    if (sid) { try { await fetch(`/api/chat/stop/${sid}`, { method: "POST", credentials: "same-origin" }) } catch { /* ignore */ } }
    setStreaming(false)
  }, [])

  return { messages, streaming, send, stop }
}
