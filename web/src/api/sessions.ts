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

export interface SessionSearchContext {
  message_id: string
  role: string
  content: string
  timestamp: string | null
}

export interface SessionSearchResult {
  message_id: string
  session_id: string
  session_name: string
  role: string
  content_snippet: string
  timestamp: string | null
  context_before?: SessionSearchContext[]
  context_after?: SessionSearchContext[]
}

export function searchMessages(query: string, limit = 20) {
  const params = new URLSearchParams({ q: query, limit: String(limit) })
  return apiJson<SessionSearchResult[]>(`/api/search?${params.toString()}`)
}

export async function createSession(opts: {
  name?: string; model?: string; endpoint_id?: string; endpoint_url?: string; skip_validation?: boolean
}): Promise<Session> {
  const fd = new FormData()
  fd.set("name", opts.name ?? "New chat")
  if (opts.model) fd.set("model", opts.model)
  if (opts.endpoint_id) fd.set("endpoint_id", opts.endpoint_id)
  if (opts.endpoint_url) fd.set("endpoint_url", opts.endpoint_url)
  if (opts.skip_validation) fd.set("skip_validation", "true")
  const res = await apiFetch("/api/session", { method: "POST", body: fd })
  if (!res.ok) throw new Error("create session failed")
  return res.json() as Promise<Session>
}
export async function deleteSession(id: string): Promise<void> {
  const r = await apiFetch(`/api/session/${id}`, { method: "DELETE" })
  if (!r.ok) throw new Error("Couldn't delete the chat")
}
export async function setSessionImportant(id: string, important: boolean): Promise<void> {
  const fd = new FormData()
  fd.set("important", String(important))
  const r = await apiFetch(`/api/session/${id}/important`, { method: "POST", body: fd })
  if (!r.ok) throw new Error("Couldn't update the chat")
}
export async function archiveSession(id: string): Promise<void> {
  const r = await apiFetch(`/api/session/${id}/archive`, { method: "POST" })
  if (!r.ok) throw new Error("Couldn't archive the chat")
}
export async function unarchiveSession(id: string): Promise<void> {
  const r = await apiFetch(`/api/session/${id}/unarchive`, { method: "POST" })
  if (!r.ok) throw new Error("Couldn't restore the chat")
}
export async function setSessionFolder(id: string, folder: string | null): Promise<void> {
  const fd = new FormData(); fd.set("folder", folder || "")
  const r = await apiFetch(`/api/session/${id}`, { method: "PATCH", body: fd })
  if (!r.ok) throw new Error("Couldn't move the chat")
}
// The backend exposes a single bulk-delete endpoint (it silently skips
// pinned/important sessions) but no bulk-archive, so archiving in bulk fans
// out to the per-session archive route.
export async function bulkDeleteSessions(ids: string[]): Promise<{ deleted: number }> {
  const r = await apiFetch("/api/sessions/bulk-delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids }),
  })
  if (!r.ok) throw new Error("Couldn't delete the chats")
  return r.json() as Promise<{ deleted: number }>
}
export async function bulkArchiveSessions(ids: string[]): Promise<void> {
  await Promise.all(ids.map((id) => archiveSession(id)))
}

// Archived sessions come from a dedicated paginated endpoint with a slimmer
// shape than the active-session list, so it gets its own local type.
export interface ArchivedSession {
  id: string
  name: string
  model: string
  message_count: number
  created_at: string | null
  updated_at: string | null
  is_important: boolean
}
export function useArchivedSessions(enabled = true) {
  return useQuery({
    queryKey: ["sessions", "archived"],
    enabled,
    queryFn: () =>
      apiJson<{ sessions: ArchivedSession[]; total: number }>(
        "/api/sessions/archived?limit=200",
      ),
  })
}
export function useSessionMutations() {
  const qc = useQueryClient()
  // Invalidate both the active list and the archived browser so a session
  // moving between the two views is reflected everywhere immediately.
  const inv = () => qc.invalidateQueries({ queryKey: ["sessions"] })
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
      mutationFn: (v: { id: string; important: boolean }) => setSessionImportant(v.id, v.important),
      onSuccess: inv,
    }),
    archive: useMutation({ mutationFn: archiveSession, onSuccess: inv }),
    unarchive: useMutation({ mutationFn: unarchiveSession, onSuccess: inv }),
    setFolder: useMutation({ mutationFn: (v: { id: string; folder: string | null }) => setSessionFolder(v.id, v.folder), onSuccess: inv }),
    bulkDelete: useMutation({ mutationFn: bulkDeleteSessions, onSuccess: inv }),
    bulkArchive: useMutation({ mutationFn: bulkArchiveSessions, onSuccess: inv }),
  }
}
