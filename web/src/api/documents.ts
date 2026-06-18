import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { DocItem } from "@/types"
export function useDocuments() {
  return useQuery({
    queryKey: ["documents"],
    queryFn: async () => {
      const r = await apiJson<{ documents?: DocItem[]; items?: DocItem[] }>("/api/documents/library?limit=50&sort=recent")
      return r.documents || r.items || []
    },
  })
}

// Documents/artifacts/files belonging to a specific chat thread.
export function useSessionDocuments(sid?: string) {
  return useQuery({
    queryKey: ["session-documents", sid],
    enabled: !!sid,
    queryFn: async () => {
      const r = await apiJson<DocItem[] | { documents?: DocItem[]; items?: DocItem[] }>(`/api/documents/${sid}`)
      return Array.isArray(r) ? r : (r.documents || r.items || [])
    },
  })
}

export interface DocFull { id: string; title?: string; language?: string; current_content?: string; version_count?: number }
export function useDocument(id: string | null) {
  return useQuery({
    queryKey: ["document", id],
    enabled: !!id,
    queryFn: () => apiJson<DocFull>(`/api/document/${id}`),
  })
}

export interface DocVersion { id: string; version_number: number; content: string; summary?: string; source?: string; created_at?: string }
export function useDocVersions(id: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: ["doc-versions", id],
    enabled: !!id && enabled,
    queryFn: () => apiJson<DocVersion[]>(`/api/document/${id}/versions`),
  })
}

export function useDocMutations() {
  const qc = useQueryClient()
  return {
    update: useMutation({
      mutationFn: async (v: { id: string; content: string }) => {
        const r = await apiFetch(`/api/document/${v.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: v.content, summary: "Manual edit (v2)" }),
        })
        if (!r.ok) throw new Error("save failed"); return r.json()
      },
      onSuccess: (_d, v) => {
        qc.invalidateQueries({ queryKey: ["document", v.id] })
        qc.invalidateQueries({ queryKey: ["doc-versions", v.id] })
        qc.invalidateQueries({ queryKey: ["documents"] })
        qc.invalidateQueries({ queryKey: ["session-documents"] })
      },
      meta: { silent: true },
    }),
    restore: useMutation({
      mutationFn: async (v: { id: string; num: number }) => {
        const r = await apiFetch(`/api/document/${v.id}/restore/${v.num}`, { method: "POST" })
        if (!r.ok) throw new Error("restore failed"); return r.json() as Promise<DocFull>
      },
      onSuccess: (_d, v) => {
        qc.invalidateQueries({ queryKey: ["document", v.id] })
        qc.invalidateQueries({ queryKey: ["doc-versions", v.id] })
        qc.invalidateQueries({ queryKey: ["documents"] })
        qc.invalidateQueries({ queryKey: ["session-documents"] })
      },
      meta: { silent: true },
    }),
    remove: useMutation({
      mutationFn: async (id: string) => { const r = await apiFetch(`/api/document/${id}`, { method: "DELETE" }); if (!r.ok) throw new Error("delete failed") },
      onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
    }),
    create: useMutation({
      mutationFn: async (v: { title: string }) => {
        const r = await apiFetch("/api/document", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: v.title, content: "" }) })
        if (!r.ok) throw new Error("create failed"); return r.json() as Promise<DocFull>
      },
      onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
    }),
    rename: useMutation({
      mutationFn: async (v: { id: string; title: string }) => {
        const r = await apiFetch(`/api/document/${v.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: v.title }) })
        if (!r.ok) throw new Error("rename failed"); return r.json()
      },
      onSuccess: (_d, v) => { qc.invalidateQueries({ queryKey: ["document", v.id] }); qc.invalidateQueries({ queryKey: ["documents"] }) },
    }),
  }
}
