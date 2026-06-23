// Streams POST /api/chat_stream (multipart FormData -> text/event-stream).
// EventSource can't POST, so read the body manually (ports static/js/chat.js).
export type SseEvent = { type: string; [k: string]: unknown }

export class StreamInterruptedError extends Error {
  constructor(message = "The response stream closed before completion.") {
    super(message)
    this.name = "StreamInterruptedError"
  }
}

export class SseResponseError extends Error {
  status?: number
  constructor(message: string, status?: number) {
    super(message)
    this.name = "SseResponseError"
    this.status = status
  }
}

export async function streamChat(
  form: FormData,
  onEvent: (e: SseEvent) => void | Promise<void>,
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
  await readSse(res.body, onEvent)
}

// Reconnect to a detached run still streaming server-side (GET, no body).
export async function streamResume(
  sessionId: string,
  onEvent: (e: SseEvent) => void | Promise<void>,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api/chat/resume/${sessionId}`, { credentials: "same-origin", signal })
  if (res.status === 401) { window.location.assign("/login"); return }
  if (!res.ok || !res.body) throw new Error(`chat/resume -> ${res.status}`)
  await readSse(res.body, onEvent)
}

export async function readSse(body: ReadableStream<Uint8Array>, onEvent: (e: SseEvent) => void | Promise<void>) {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buf = ""
  let sawDone = false

  const processRecord = async (record: string) => {
    let eventName = "message"
    const dataLines: string[] = []
    for (const rawLine of record.split("\n")) {
      const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine
      if (!line || line.startsWith(":")) continue
      const colon = line.indexOf(":")
      const field = colon < 0 ? line : line.slice(0, colon)
      let value = colon < 0 ? "" : line.slice(colon + 1)
      if (value.startsWith(" ")) value = value.slice(1)
      if (field === "event") eventName = value
      else if (field === "data") dataLines.push(value)
    }
    if (!dataLines.length) return
    const payload = dataLines.join("\n")
    if (payload === "[DONE]") { sawDone = true; return }

    let parsed: unknown
    try { parsed = JSON.parse(payload) } catch { return }
    if (!parsed || typeof parsed !== "object") return
    const data = parsed as Record<string, unknown>
    const status = typeof data.status === "number" ? data.status : undefined
    const error = typeof data.error === "string" ? data.error : undefined
    if (eventName === "error" || (status != null && status >= 400) || (error && !data.type)) {
      throw new SseResponseError(error || "The agent run failed before completion.", status)
    }
    const ev = data as SseEvent
    if (!ev.type && eventName !== "message") ev.type = eventName
    await onEvent(ev)
  }

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    buf = buf.replace(/\r\n/g, "\n")
    let boundary: number
    while ((boundary = buf.indexOf("\n\n")) >= 0) {
      const record = buf.slice(0, boundary)
      buf = buf.slice(boundary + 2)
      await processRecord(record)
      if (sawDone) { await reader.cancel(); return }
    }
  }
  buf += decoder.decode()
  if (buf.trim()) await processRecord(buf)
  if (!sawDone) throw new StreamInterruptedError()
}
