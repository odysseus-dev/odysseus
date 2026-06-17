import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { Session, HistoryMsg } from "@/types"

export function useSessions() {
  return useQuery({ queryKey: ["sessions"], queryFn: () => apiJson<Session[]>("/api/sessions") })
}
export function useHistory(sid?: string) {
  return useQuery({
    queryKey: ["history", sid],
    enabled: !!sid,
    queryFn: () => apiJson<{ history: HistoryMsg[] }>(`/api/history/${sid}`),
  })
}
export async function createSession(opts: {
  name?: string; model?: string; endpoint_id?: string; endpoint_url?: string
}): Promise<Session> {
  const fd = new FormData()
  fd.set("name", opts.name ?? "New chat")
  if (opts.model) fd.set("model", opts.model)
  if (opts.endpoint_id) fd.set("endpoint_id", opts.endpoint_id)
  if (opts.endpoint_url) fd.set("endpoint_url", opts.endpoint_url)
  const res = await apiFetch("/api/session", { method: "POST", body: fd })
  if (!res.ok) throw new Error("create session failed")
  return res.json() as Promise<Session>
}
export async function deleteSession(id: string): Promise<void> {
  await apiFetch(`/api/session/${id}`, { method: "DELETE" })
}
export function useSessionMutations() {
  const qc = useQueryClient()
  return {
    remove: useMutation({
      mutationFn: deleteSession,
      onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
    }),
  }
}
