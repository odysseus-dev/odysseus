import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { Source } from "@/types"

// Shape of a single progress frame emitted by GET /api/research/stream/{id}.
// The backend yields `{...progress, status}` lines plus a terminal
// `{status, final: true, error?}` line (research_routes.py ~470). `progress`
// is a free-form dict, so every field beyond `status`/`final` is optional.
export interface ResearchStreamEvent {
  status?: string
  final?: boolean
  error?: string
  phase?: string
  round?: number
  total_sources?: number
  message?: string
  title?: string
  url?: string
  percent?: number
  [k: string]: unknown
}

// Partial findings returned by POST /api/research/result-peek/{id}
// (research_routes.py ~504). Available mid-run for in-progress jobs.
export interface ResearchPeek {
  result: string
  sources: Source[]
  raw_findings: unknown[]
  category?: string
}

// Self-contained SSE reader for the research progress stream. We can't reuse
// lib/sse.ts (readSse isn't exported) and EventSource can't carry our
// same-origin auth cookie cleanly, so we read the GET body manually — mirroring
// the readSse pattern in lib/sse.ts. Resolves when the stream ends (run done or
// connection closed); call the returned abort to detach early.
export function streamResearch(
  sessionId: string,
  onEvent: (e: ResearchStreamEvent) => void,
  onError?: (err: unknown) => void,
): () => void {
  const controller = new AbortController()
  // Guards the race between abort() and an in-flight reader.read(): once the
  // caller detaches, a buffered chunk can still resolve and fire onEvent for a
  // terminated stream, which would shallow-merge stale fields into live state.
  // The flag short-circuits both the loop and every onEvent after abort.
  let aborted = false
  ;(async () => {
    try {
      const res = await apiFetch(`/api/research/stream/${sessionId}`, { signal: controller.signal })
      if (!res.ok || !res.body) throw new Error(`research/stream -> ${res.status}`)
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ""
      for (;;) {
        const { done, value } = await reader.read()
        if (aborted || done) break
        buf += decoder.decode(value, { stream: true })
        let nl: number
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl).trim()
          buf = buf.slice(nl + 1)
          if (!line.startsWith("data:")) continue
          const payload = line.slice(5).trim()
          if (!payload || payload === "[DONE]") continue
          if (aborted) break
          try { onEvent(JSON.parse(payload) as ResearchStreamEvent) } catch { /* ignore keepalives */ }
        }
      }
    } catch (err) {
      if (aborted || (err as { name?: string })?.name === "AbortError") return
      onError?.(err)
    }
  })()
  return () => { aborted = true; controller.abort() }
}

export function fetchResearchPeek(sessionId: string): Promise<ResearchPeek> {
  return apiJson<ResearchPeek>(`/api/research/result-peek/${sessionId}`, { method: "POST" })
}

export interface ResearchActiveItem {
  session_id: string
  query: string
  status: string
  progress?: { phase?: string; round?: number; total_sources?: number; message?: string; title?: string; url?: string }
  started_at?: number
}
export interface ResearchLibraryItem {
  id: string
  query: string
  category?: string
  source_count: number
  status: string
  duration?: string
  rounds?: string | number
  started_at?: number
  completed_at?: number
  archived: boolean
}
export interface ResearchDetail {
  id?: string
  query?: string
  category?: string
  result?: string
  sources?: Source[]
  status?: string
  archived?: boolean
  started_at?: number
  completed_at?: number
  stats?: Record<string, unknown>
}
export interface ResearchSpinoff {
  session_id: string
  name: string
  source_count: number
}

export interface ResearchStartBody {
  query: string
  category?: string
  max_rounds?: number
  search_provider?: string
  endpoint_id?: string
  model?: string
}
export function useResearchStart() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: ResearchStartBody) =>
      apiJson<{ session_id: string; status: string; query: string }>("/api/research/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["research"] }),
  })
}

export function useResearchActive() {
  return useQuery({
    queryKey: ["research", "active"],
    queryFn: () => apiJson<{ active: ResearchActiveItem[] }>("/api/research/active"),
    refetchInterval: 4000,
  })
}

export function useResearchLibrary(archived = false) {
  return useQuery({
    queryKey: ["research", "library", archived],
    queryFn: () =>
      apiJson<{ research: ResearchLibraryItem[]; total: number }>(
        `/api/research/library?sort=recent&limit=100&archived=${archived}`,
      ),
  })
}

export function useResearchDetail(id?: string) {
  return useQuery({
    queryKey: ["research", "detail", id],
    enabled: !!id,
    queryFn: () => apiJson<ResearchDetail>(`/api/research/detail/${id}`),
  })
}

// The backend-rendered HTML "Visual Report". Fetched as text and rendered via a
// srcDoc iframe — the response sets `frame-ancestors 'none'`, so it can't be
// embedded by URL.
export function useResearchReport(id?: string) {
  return useQuery({
    queryKey: ["research", "report", id],
    enabled: !!id,
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      const r = await apiFetch(`/api/research/report/${id}`)
      if (!r.ok) throw new Error(`report ${r.status}`)
      return r.text()
    },
  })
}

export function useResearchMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["research"] })
  return {
    cancel: useMutation({
      mutationFn: (id: string) => apiJson<{ cancelled: boolean }>(`/api/research/cancel/${id}`, { method: "POST" }),
      onSuccess: inv,
    }),
    archive: useMutation({
      mutationFn: (v: { id: string; archived: boolean }) =>
        apiJson<{ ok: boolean; id: string; archived: boolean }>(
          `/api/research/${v.id}/archive?archived=${v.archived}`,
          { method: "POST" },
        ),
      onSuccess: inv,
    }),
    remove: useMutation({
      mutationFn: async (id: string) => { await apiFetch(`/api/research/${id}`, { method: "DELETE" }) },
      onSuccess: inv,
    }),
    spinoff: useMutation({
      mutationFn: (id: string) => apiJson<ResearchSpinoff>(`/api/research/spinoff/${id}`, { method: "POST" }),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
    }),
  }
}
