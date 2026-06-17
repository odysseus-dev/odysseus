import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"

// KB stats — shape from rag_manager.get_stats() (src/rag_vector.py). Either a
// healthy stats object or {error} when RAG is unavailable.
export interface RagStats {
  document_count?: number
  embedding_model?: string
  persist_directory?: string
  collection_name?: string
  embedding_lanes?: { model?: string; url?: string; count?: number }[]
  healthy?: boolean
  error?: string
}
export function useRagStats() {
  return useQuery({
    queryKey: ["rag", "stats"],
    retry: false,
    queryFn: () => apiJson<RagStats>("/api/rag/stats"),
  })
}

// Indexed personal documents — GET /api/personal -> {files, directories}.
export interface RagFile { name: string; size?: number; path?: string }
export function useRagDocuments() {
  return useQuery({
    queryKey: ["rag", "documents"],
    retry: false,
    queryFn: async () => {
      try {
        const r = await apiJson<{ files?: RagFile[]; directories?: string[] }>("/api/personal")
        return { files: r.files || [], directories: r.directories || [], ok: true }
      } catch { return { files: [] as RagFile[], directories: [] as string[], ok: false } }
    },
  })
}

// Embedding models catalog — GET /api/embeddings/models. Admin-gated; a 403
// means the caller isn't admin.
export interface EmbeddingModel {
  model: string
  dim?: number
  size_gb?: number
  description?: string
  downloaded?: boolean
  downloading?: boolean
  active?: boolean
  recommended?: boolean
  cached_size_mb?: number | null
}
export function useEmbeddingModels() {
  return useQuery({
    queryKey: ["rag", "embedding-models"],
    retry: false,
    queryFn: async (): Promise<{ models: EmbeddingModel[]; admin: boolean; available: boolean }> => {
      const r = await apiFetch("/api/embeddings/models")
      if (r.status === 403) return { models: [], admin: false, available: true }
      if (!r.ok) return { models: [], admin: true, available: false }
      return { models: (await r.json()) as EmbeddingModel[], admin: true, available: true }
    },
  })
}

// Custom embedding endpoint — GET /api/embeddings/endpoint.
export interface EmbeddingEndpoint { url?: string; model?: string; active?: boolean }
export function useEmbeddingEndpoint() {
  return useQuery({
    queryKey: ["rag", "embedding-endpoint"],
    retry: false,
    queryFn: async () => {
      try { return await apiJson<EmbeddingEndpoint>("/api/embeddings/endpoint") }
      catch { return {} as EmbeddingEndpoint }
    },
  })
}

export interface UploadResult { success?: boolean; uploaded?: string[]; indexed_count?: number; failed_count?: number }

export function useRagMutations() {
  const qc = useQueryClient()
  const invDocs = () => { qc.invalidateQueries({ queryKey: ["rag", "documents"] }); qc.invalidateQueries({ queryKey: ["rag", "stats"] }) }
  const invModels = () => { qc.invalidateQueries({ queryKey: ["rag", "embedding-models"] }); qc.invalidateQueries({ queryKey: ["rag", "embedding-endpoint"] }) }
  return {
    upload: useMutation({
      mutationFn: async (files: File[]): Promise<UploadResult> => {
        const fd = new FormData()
        for (const f of files) fd.append("files", f)
        const r = await apiFetch("/api/personal/upload", { method: "POST", body: fd })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Upload failed") }
        return r.json()
      },
      onSuccess: invDocs,
    }),
    removeFile: useMutation({
      mutationFn: async (filepath: string) => {
        const r = await apiFetch(`/api/personal/file?filepath=${encodeURIComponent(filepath)}`, { method: "DELETE" })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Delete failed") }
        return r.json()
      },
      onSuccess: invDocs,
    }),
    reload: useMutation({
      mutationFn: async () => { const r = await apiFetch("/api/personal/reload", { method: "POST" }); if (!r.ok) throw new Error("Reload failed"); return r.json() },
      onSuccess: invDocs,
    }),
    downloadModel: useMutation({
      mutationFn: async (model: string) => {
        const r = await apiFetch(`/api/embeddings/models/${model.split("/").map(encodeURIComponent).join("/")}/download`, { method: "POST" })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Download failed") }
        return r.json()
      },
      onSuccess: invModels,
    }),
    deleteModel: useMutation({
      mutationFn: async (model: string) => {
        const r = await apiFetch(`/api/embeddings/models/${model.split("/").map(encodeURIComponent).join("/")}`, { method: "DELETE" })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Delete failed") }
        return r.json()
      },
      onSuccess: invModels,
    }),
    setEndpoint: useMutation({
      mutationFn: async (v: { url: string; model?: string; api_key?: string }) => {
        const fd = new FormData()
        fd.set("url", v.url)
        if (v.model) fd.set("model", v.model)
        if (v.api_key) fd.set("api_key", v.api_key)
        const r = await apiFetch("/api/embeddings/endpoint", { method: "POST", body: fd })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Save failed") }
        return r.json()
      },
      onSuccess: invModels,
    }),
    clearEndpoint: useMutation({
      mutationFn: async () => { const r = await apiFetch("/api/embeddings/endpoint", { method: "DELETE" }); if (!r.ok) throw new Error("Clear failed"); return r.json() },
      onSuccess: invModels,
    }),
  }
}
