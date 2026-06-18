import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"

// Scoped Claude Agent API tokens (/api/tokens/*, admin-only).
export interface ApiToken {
  id: string
  name: string
  owner?: string | null
  token_prefix: string
  scopes: string[]
  is_active: boolean
  last_used_at?: string | null
  created_at?: string | null
}
export interface TokenProfiles { profiles: Record<string, string[]>; allowed_scopes: string[] }

export function useTokens() {
  return useQuery({
    queryKey: ["api-tokens"],
    retry: false,
    queryFn: async () => { try { return await apiJson<ApiToken[]>("/api/tokens") } catch { return [] } },
  })
}

export function useTokenProfiles() {
  return useQuery({
    queryKey: ["token-profiles"],
    retry: false,
    queryFn: async () => { try { return await apiJson<TokenProfiles>("/api/tokens/profiles") } catch { return { profiles: {}, allowed_scopes: [] } } },
  })
}

export function useTokenMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["api-tokens"] })
  return {
    create: useMutation({
      mutationFn: async (v: { name: string; profile?: string; scopes?: string }) => {
        const fd = new FormData()
        fd.set("name", v.name)
        if (v.profile) fd.set("profile", v.profile)
        if (v.scopes) fd.set("scopes", v.scopes)
        const r = await apiFetch("/api/tokens", { method: "POST", body: fd })
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Create failed")
        return r.json() as Promise<ApiToken & { token: string }>
      },
      onSuccess: inv,
      meta: { silent: true },
    }),
    rename: useMutation({
      mutationFn: async (v: { id: string; name: string }) => {
        const r = await apiFetch(`/api/tokens/${v.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: v.name }) })
        if (!r.ok) throw new Error("Rename failed")
        return r.json()
      },
      onSuccess: inv,
    }),
    remove: useMutation({
      mutationFn: async (id: string) => {
        const r = await apiFetch(`/api/tokens/${id}`, { method: "DELETE" })
        if (!r.ok) throw new Error("Delete failed")
        return r.json()
      },
      onSuccess: inv,
    }),
  }
}
