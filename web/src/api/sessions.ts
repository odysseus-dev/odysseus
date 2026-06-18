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
  const r = await apiFetch(`/api/session/${id}`, { method: "DELETE" })
  if (!r.ok) throw new Error("Couldn't delete the chat")
}
export function useSessionMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["sessions"] })
  const form = (path: string, fields: Record<string, string>) => {
    const fd = new FormData(); Object.entries(fields).forEach(([k, v]) => fd.set(k, v))
    return apiFetch(path, { method: "POST", body: fd })
  }
  return {
    remove: useMutation({ mutationFn: deleteSession, onSuccess: inv }),
    rename: useMutation({
      mutationFn: async (v: { id: string; name: string }) => {
        const fd = new FormData(); fd.set("name", v.name)
        const r = await apiFetch(`/api/session/${v.id}`, { method: "PATCH", body: fd })
        if (!r.ok) throw new Error("rename failed"); return r.json()
      },
      onSuccess: inv,
    }),
    setImportant: useMutation({
      mutationFn: async (v: { id: string; important: boolean }) => {
        const r = await form(`/api/session/${v.id}/important`, { important: String(v.important) })
        if (!r.ok) throw new Error("Couldn't update the chat")
      },
      onSuccess: inv,
    }),
    archive: useMutation({
      mutationFn: async (id: string) => {
        const r = await apiFetch(`/api/session/${id}/archive`, { method: "POST" })
        if (!r.ok) throw new Error("Couldn't archive the chat")
      },
      onSuccess: inv,
    }),
  }
}
