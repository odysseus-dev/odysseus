import { useQuery } from "@tanstack/react-query"
import { apiJson } from "@/lib/api"

export interface CachedModel {
  repo_id: string; size?: string; nb_files?: number; status?: string; path?: string;
  backend?: string; is_gguf?: boolean; is_ollama?: boolean; is_diffusion?: boolean; is_local_dir?: boolean;
}
export interface Gpu {
  index: number; name?: string; free_mb?: number; total_mb?: number; used_mb?: number;
  util_pct?: number; busy?: boolean; processes?: { pid: number; name: string; used_mb: number }[];
}

export function useCachedModels() {
  return useQuery({
    queryKey: ["cookbook", "cached"],
    retry: false,
    staleTime: 30_000,
    queryFn: async () => {
      try {
        const r = await apiJson<{ models?: CachedModel[]; host?: string }>("/api/model/cached")
        return { models: r.models || [], host: r.host || "local", ok: true }
      } catch { return { models: [] as CachedModel[], host: "local", ok: false } }
    },
  })
}

export function useGpus() {
  return useQuery({
    queryKey: ["cookbook", "gpus"],
    retry: false,
    staleTime: 15_000,
    queryFn: async () => {
      try {
        return await apiJson<{ ok: boolean; gpus?: Gpu[]; error?: string; backend?: string; source?: string }>("/api/cookbook/gpus")
      } catch { return { ok: false, gpus: [] as Gpu[], error: "unavailable" } }
    },
  })
}
