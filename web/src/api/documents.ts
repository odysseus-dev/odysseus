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

export interface DocFull { id: string; title?: string; language?: string; current_content?: string; version_count?: number }
export function useDocument(id: string | null) {
  return useQuery({
    queryKey: ["document", id],
    enabled: !!id,
    queryFn: () => apiJson<DocFull>(`/api/document/${id}`),
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
      onSuccess: (_d, v) => { qc.invalidateQueries({ queryKey: ["document", v.id] }); qc.invalidateQueries({ queryKey: ["documents"] }) },
    }),
    remove: useMutation({
      mutationFn: async (id: string) => { const r = await apiFetch(`/api/document/${id}`, { method: "DELETE" }); if (!r.ok) throw new Error("delete failed") },
      onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
    }),
  }
}
