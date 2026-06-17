import { apiFetch } from "@/lib/api"

export interface CompareStart {
  id: string
  session_left: string
  session_right: string
  model_left: string | null
  model_right: string | null
  is_blind: boolean
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
  fd.set("is_blind", String(v.is_blind ?? false))
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
export async function voteCompare(compId: string, winner: "left" | "right" | "tie"): Promise<CompareVote> {
  const fd = new FormData()
  fd.set("winner", winner)
  const r = await apiFetch(`/api/compare/${compId}/vote`, { method: "POST", body: fd })
  if (!r.ok) throw new Error(`compare/vote -> ${r.status}`)
  return r.json()
}
