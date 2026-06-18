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
