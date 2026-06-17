// Streams POST /api/chat_stream (multipart FormData -> text/event-stream).
// EventSource can't POST, so read the body manually (ports static/js/chat.js).
export type SseEvent = { type: string; [k: string]: unknown }

export async function streamChat(
  form: FormData,
  onEvent: (e: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/chat_stream", {
    method: "POST",
    body: form,
    credentials: "same-origin",
    headers: {
      "X-Tz-Offset": String(new Date().getTimezoneOffset()),
      "X-Tz-Name": Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    },
    signal,
  })
  if (res.status === 401) { window.location.assign("/login"); return }
  if (!res.ok || !res.body) throw new Error(`chat_stream -> ${res.status}`)
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ""
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let nl: number
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim()
      buf = buf.slice(nl + 1)
      if (!line.startsWith("data:")) continue
      const payload = line.slice(5).trim()
      if (payload === "[DONE]") return
      try { onEvent(JSON.parse(payload) as SseEvent) } catch { /* ignore keepalives */ }
    }
  }
}
