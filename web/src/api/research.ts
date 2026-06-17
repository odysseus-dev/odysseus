import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { Source } from "@/types"

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
