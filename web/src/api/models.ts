import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { ModelsResponse, DefaultChat } from "@/types"

export function useModels() {
  return useQuery({ queryKey: ["models"], queryFn: () => apiJson<ModelsResponse>("/api/models") })
}
export function useDefaultChat() {
  return useQuery({ queryKey: ["default-chat"], queryFn: () => apiJson<DefaultChat>("/api/default-chat") })
}
export function useDeleteEndpoint() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => { await apiFetch(`/api/model-endpoints/${id}`, { method: "DELETE" }) },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["models"] }),
  })
}

export function useSetDefaultModel() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (model: string) => {
      const r = await apiFetch("/api/auth/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ default_model: model }) })
      if (!r.ok) throw new Error("save failed"); return r.json()
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["default-chat"] }),
  })
}

export interface NewEndpoint { name?: string; base_url: string; api_key?: string; model_type?: string }
function endpointForm(v: NewEndpoint) {
  const fd = new FormData()
  if (v.name) fd.set("name", v.name)
  fd.set("base_url", v.base_url)
  if (v.api_key) fd.set("api_key", v.api_key)
  fd.set("model_type", v.model_type || "llm")
  return fd
}
export async function testEndpoint(v: NewEndpoint): Promise<{ reachable?: boolean; models?: string[]; error?: string }> {
  const fd = new FormData(); fd.set("base_url", v.base_url); if (v.api_key) fd.set("api_key", v.api_key)
  const r = await apiFetch("/api/model-endpoints/test", { method: "POST", body: fd })
  return r.json().catch(() => ({ error: `HTTP ${r.status}` }))
}
export function useEndpointMutations() {
  const qc = useQueryClient()
  return {
    create: useMutation({
      mutationFn: async (v: NewEndpoint) => {
        const r = await apiFetch("/api/model-endpoints", { method: "POST", body: endpointForm(v) })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${r.status}`) }
        return r.json()
      },
      onSuccess: () => { qc.invalidateQueries({ queryKey: ["models"] }); qc.invalidateQueries({ queryKey: ["default-chat"] }) },
    }),
  }
}
