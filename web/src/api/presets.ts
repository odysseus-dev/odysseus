import { useQuery } from "@tanstack/react-query"
import { apiJson } from "@/lib/api"
export interface Preset { id: string; name: string; enabled?: boolean }
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
