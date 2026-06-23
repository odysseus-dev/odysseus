import { useQuery } from "@tanstack/react-query"
import { apiFetch, apiJson } from "@/lib/api"

export interface CompareProbeModel {
  endpoint_id?: string
  model: string
  endpoint?: string
  with_tools?: boolean
}

export interface CompareProbeResult {
  model?: string
  endpoint_id?: string
  status?: string
  error?: string
  latency_ms?: number
}

export interface SearchProviderInfo {
  id: string
  label: string
  available: boolean
}

export interface SearchResultItem {
  title?: string
  url?: string
  snippet?: string
  [key: string]: unknown
}

export interface SearchProviderResponse {
  results?: SearchResultItem[]
  provider?: string
  time?: number
  error?: string
}

export async function probeSelectedModels(models: CompareProbeModel[]): Promise<CompareProbeResult[]> {
  const r = await apiFetch("/api/probe-selected", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ models }),
  })
  if (!r.ok) throw new Error(`probe-selected -> ${r.status}`)
  const data = await r.json().catch(() => ({ results: [] }))
  return (data.results || []) as CompareProbeResult[]
}

export async function listSearchProviders(): Promise<SearchProviderInfo[]> {
  return apiJson<SearchProviderInfo[]>("/api/search/providers")
}

// Abort a single in-flight chat/agent run by its session id. The detached
// backend run keeps generating after the SSE socket closes, so the Stop
// button (global or per-pane) must hit this endpoint to actually cancel it.
export async function stopChatSession(sessionId: string): Promise<{ stopped?: boolean }> {
  const r = await apiFetch(`/api/chat/stop/${encodeURIComponent(sessionId)}`, { method: "POST" })
  if (!r.ok) throw new Error(`chat/stop -> ${r.status}`)
  return r.json().catch(() => ({}))
}

export async function searchWithProvider(
  query: string,
  provider: string,
  count = 10,
  signal?: AbortSignal,
): Promise<SearchProviderResponse> {
  const fd = new FormData()
  fd.set("query", query)
  fd.set("provider", provider)
  fd.set("count", String(count))
  const r = await apiFetch("/api/search/query", { method: "POST", body: fd, signal })
  if (!r.ok) throw new Error(`search/query -> ${r.status}`)
  return r.json()
}

export interface CompareStart {
  id: string
  session_left: string
  session_right: string
  model_left: string | null
  model_right: string | null
  is_blind: boolean
  mapping?: { left: string; right: string } | null
}

export async function startCompare(v: {
  prompt: string
  model_a: string; endpoint_a_id?: string; endpoint_a?: string
  model_b: string; endpoint_b_id?: string; endpoint_b?: string
  is_blind?: boolean
}): Promise<CompareStart> {
  const fd = new FormData()
  fd.set("prompt", v.prompt)
  fd.set("model_a", v.model_a)
  fd.set("model_b", v.model_b)
  if (v.endpoint_a_id) fd.set("endpoint_a_id", v.endpoint_a_id)
  if (v.endpoint_a) fd.set("endpoint_a", v.endpoint_a)
  if (v.endpoint_b_id) fd.set("endpoint_b_id", v.endpoint_b_id)
  if (v.endpoint_b) fd.set("endpoint_b", v.endpoint_b)
  fd.set("is_blind", String(v.is_blind ?? true))
  const r = await apiFetch("/api/compare/start", { method: "POST", body: fd })
  if (!r.ok) throw new Error(`compare/start -> ${r.status}`)
  return r.json()
}

export interface CompareVote {
  winner: string
  model_a: string
  model_b: string
  revealed: { left: string; right: string }
}

export async function recordCompareVote(v: {
  prompt: string
  models: string[]
  winner: string
  is_blind?: boolean
}): Promise<{ status?: string; id?: string }> {
  const r = await apiFetch("/api/compare/record", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      prompt: v.prompt,
      models: v.models,
      winner: v.winner,
      is_blind: v.is_blind ?? true,
    }),
  })
  if (!r.ok) throw new Error(`compare/record -> ${r.status}`)
  return r.json()
}

export interface CompareReveal {
  winner: string | null
  model_a: string
  model_b: string
  revealed: { left: string; right: string }
}

export async function revealCompare(compId: string): Promise<CompareReveal> {
  const r = await apiFetch(`/api/compare/${compId}/reveal`, { method: "POST" })
  if (!r.ok) throw new Error(`compare/reveal -> ${r.status}`)
  return r.json()
}

export async function voteCompare(compId: string, winner: "left" | "right" | "tie"): Promise<CompareVote> {
  const fd = new FormData()
  fd.set("winner", winner)
  const r = await apiFetch(`/api/compare/${compId}/vote`, { method: "POST", body: fd })
  if (!r.ok) throw new Error(`compare/vote -> ${r.status}`)
  return r.json()
}

export interface CompareHistoryItem {
  id: string
  prompt: string
  model_a: string
  model_b: string
  winner: string | null
  is_blind: boolean
  voted_at: string | null
  created_at: string | null
}

export function useCompareHistory() {
  return useQuery({
    queryKey: ["compare-history"],
    retry: false,
    queryFn: () => apiJson<CompareHistoryItem[]>("/api/compare/history"),
  })
}
