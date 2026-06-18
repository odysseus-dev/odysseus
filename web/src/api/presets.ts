import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
export interface Preset { id: string; name: string; enabled?: boolean }

export function useCreatePreset() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (v: { name: string; system_prompt?: string; temperature?: number; max_tokens?: number }) => {
      const r = await apiFetch("/api/presets/custom", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: v.name, system_prompt: v.system_prompt || "", temperature: v.temperature ?? 1.0, max_tokens: v.max_tokens ?? 0, enabled: true }),
      })
      if (!r.ok) throw new Error("save failed"); return r.json()
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["presets"] }),
    meta: { silent: true },
  })
}
export function usePresets() {
  return useQuery({
    queryKey: ["presets"],
    queryFn: async () => {
      const raw = await apiJson<unknown>("/api/presets")
      let list: Preset[] = []
      if (Array.isArray(raw)) list = raw as Preset[]
      else if (raw && typeof raw === "object") {
        const obj = raw as Record<string, unknown>
        if (Array.isArray(obj.presets)) list = obj.presets as Preset[]
        else list = Object.entries(obj).map(([id, v]) => {
          const o = (v || {}) as Record<string, unknown>
          return { id, name: (o.name as string) || id, enabled: o.enabled as boolean | undefined }
        })
      }
      return list.filter((p) => p && p.id && p.enabled !== false)
    },
  })
}
