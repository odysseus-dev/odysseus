import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiFetch, apiJson } from "@/lib/api"

// Importers: src/components/settings/AdminSections.tsx (Integrations section).
// API: GET/POST /api/auth/integrations, GET /api/auth/integrations/presets,
// PUT/DELETE /api/auth/integrations/{id}, POST /api/auth/integrations/{id}/test
// (all admin-gated → 403 for non-admins). Backed by src/integrations.py.

export interface Integration {
  id: string
  name: string
  base_url: string
  preset?: string
  enabled?: boolean
  auth_type?: string
  auth_header?: string
  auth_param?: string
  description?: string
  api_key?: string // masked by the server, e.g. "abcd****"
}
export interface IntegrationPreset {
  name: string
  auth_type?: string
  auth_header?: string
  description?: string
}
export interface IntegrationInput {
  name?: string
  base_url: string
  preset?: string
  api_key?: string
  auth_type?: string
  auth_header?: string
  auth_param?: string
  enabled?: boolean
}

// Admin-gated. A 403 means the caller isn't admin → {items:[], admin:false}.
async function integrationList(): Promise<{ items: Integration[]; admin: boolean }> {
  const r = await apiFetch("/api/auth/integrations")
  if (r.status === 403) return { items: [], admin: false }
  if (!r.ok) return { items: [], admin: true }
  const data = (await r.json()) as { integrations?: Integration[] }
  return { items: data.integrations || [], admin: true }
}

export function useIntegrations() {
  return useQuery({ queryKey: ["integrations"], retry: false, queryFn: integrationList })
}
export function useIntegrationPresets() {
  return useQuery({
    queryKey: ["integration-presets"],
    retry: false,
    queryFn: async () => {
      const data = await apiJson<{ presets?: Record<string, IntegrationPreset> }>("/api/auth/integrations/presets")
      return data.presets || {}
    },
  })
}

async function jsonPost(path: string, body: unknown, method = "POST"): Promise<unknown> {
  const r = await apiFetch(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
  if (!r.ok) { const e = await r.json().catch(() => ({})) as { detail?: string }; throw new Error(e.detail || `HTTP ${r.status}`) }
  return r.json()
}

export function useIntegrationMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["integrations"] })
  return {
    add: useMutation({ mutationFn: (v: IntegrationInput) => jsonPost("/api/auth/integrations", v), onSuccess: inv }),
    update: useMutation({
      mutationFn: (v: { id: string; data: Partial<IntegrationInput> }) => jsonPost(`/api/auth/integrations/${v.id}`, v.data, "PUT"),
      onSuccess: inv,
    }),
    remove: useMutation({
      mutationFn: async (id: string) => { await apiFetch(`/api/auth/integrations/${id}`, { method: "DELETE" }) },
      onSuccess: inv,
    }),
    test: useMutation({
      mutationFn: async (id: string) => {
        const r = await apiFetch(`/api/auth/integrations/${id}/test`, { method: "POST" })
        return r.json().catch(() => ({ ok: false, message: `HTTP ${r.status}` })) as Promise<{ ok?: boolean; message?: string }>
      },
    }),
  }
}
