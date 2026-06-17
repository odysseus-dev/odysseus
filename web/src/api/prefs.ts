import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"

export function usePrefs() {
  return useQuery({ queryKey: ["prefs"], queryFn: () => apiJson<Record<string, unknown>>("/api/prefs") })
}
export function useSetPref() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (v: { key: string; value: unknown }) => {
      const r = await apiFetch(`/api/prefs/${v.key}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value: v.value }) })
      if (!r.ok) throw new Error("save failed"); return r.json()
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["prefs"] }),
  })
}
