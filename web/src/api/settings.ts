import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"

// The admin-managed app settings store (src/settings.py DEFAULT_SETTINGS).
// GET returns the full dict for admins (scrubbed for non-admins); POST accepts
// a PARTIAL { key: value } and only writes keys that exist in DEFAULT_SETTINGS.
export type Settings = Record<string, unknown>

export function useSettings() {
  return useQuery({
    queryKey: ["app-settings"],
    queryFn: () => apiJson<Settings>("/api/auth/settings"),
    staleTime: 30_000,
  })
}

export function useSaveSettings() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (patch: Settings): Promise<Settings> => {
      const r = await apiFetch("/api/auth/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      })
      if (!r.ok) {
        const j = await r.json().catch(() => ({}))
        throw new Error(j.detail || (r.status === 403 ? "Admin only" : "Save failed"))
      }
      return r.json()
    },
    // The endpoint returns the full updated settings — seed the cache with it.
    onSuccess: (data) => qc.setQueryData(["app-settings"], data),
  })
}
