import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
export interface Preset { id: string; name: string; enabled?: boolean }

// AI-expand a rough character description into a full system prompt.
// Backend: POST /api/presets/expand (preset_routes.py) → { success, prompt?, message? }
export function useExpandPreset() {
  return useMutation({
    mutationFn: async (v: { name?: string; prompt?: string; model?: string }) => {
      const r = await apiFetch("/api/presets/expand", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(v),
      })
      const d = (await r.json().catch(() => ({}))) as { success?: boolean; prompt?: string; message?: string }
      if (!r.ok || d.success === false) throw new Error(d.message || "Expand failed")
      return d
    },
    meta: { silent: true },
  })
}
export interface PresetConfig {
  name?: string
  character_name?: string
  system_prompt?: string
  temperature?: number
  max_tokens?: number
  inject_prefix?: string
  inject_suffix?: string
  enabled?: boolean
}
export type PresetsConfig = Record<string, PresetConfig>
export interface CustomPresetPayload {
  name: string
  system_prompt?: string
  temperature?: number
  max_tokens?: number
  inject_prefix?: string
  inject_suffix?: string
  enabled?: boolean
}

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
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["presets"] }); qc.invalidateQueries({ queryKey: ["preset-config"] }) },
    meta: { silent: true },
  })
}
export function usePresetConfig() {
  return useQuery({ queryKey: ["preset-config"], queryFn: () => apiJson<PresetsConfig>("/api/presets") })
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
        else list = Object.entries(obj).flatMap(([id, v]) => {
          if (!v || typeof v !== "object" || Array.isArray(v)) return []
          const o = (v || {}) as Record<string, unknown>
          return [{ id, name: (o.name as string) || id, enabled: o.enabled as boolean | undefined }]
        })
      }
      return list.filter((p) => p && p.id && p.enabled !== false)
    },
  })
}
export function useCustomPresetMutations() {
  const qc = useQueryClient()
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["presets"] })
    qc.invalidateQueries({ queryKey: ["preset-config"] })
  }
  return {
    save: useMutation({
      mutationFn: async (v: CustomPresetPayload) => {
        const r = await apiFetch("/api/presets/custom", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: v.name,
            system_prompt: v.system_prompt || "",
            temperature: v.temperature ?? 1.0,
            max_tokens: v.max_tokens ?? 0,
            inject_prefix: v.inject_prefix || "",
            inject_suffix: v.inject_suffix || "",
            enabled: v.enabled ?? true,
          }),
        })
        if (!r.ok) throw new Error("save failed")
        return r.json()
      },
      onSuccess: invalidate,
      meta: { silent: true },
    }),
    disable: useMutation({
      mutationFn: async (current?: PresetConfig) => {
        const r = await apiFetch("/api/presets/custom", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: current?.character_name || current?.name || "",
            system_prompt: current?.system_prompt || "",
            temperature: current?.temperature ?? 1.0,
            max_tokens: current?.max_tokens ?? 0,
            inject_prefix: current?.inject_prefix || "",
            inject_suffix: current?.inject_suffix || "",
            enabled: false,
          }),
        })
        if (!r.ok) throw new Error("disable failed")
        return r.json()
      },
      onSuccess: invalidate,
      meta: { silent: true },
    }),
  }
}
