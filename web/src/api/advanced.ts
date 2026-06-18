import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"

const jx = (method: string, path: string, body?: unknown) =>
  apiFetch(path, { method, headers: { "Content-Type": "application/json" }, body: body !== undefined ? JSON.stringify(body) : undefined })

// ───────────────────────── Model endpoint admin ─────────────────────────
export interface EndpointModel { id: string; display: string; is_hidden: boolean; is_pinned: boolean }
export function useEndpointModels(epId: string | null) {
  return useQuery({ queryKey: ["endpoint-models", epId], enabled: !!epId, retry: false, queryFn: async () => { try { return await apiJson<EndpointModel[]>(`/api/model-endpoints/${epId}/models`) } catch { return [] } } })
}
export function useEndpointAdmin() {
  const qc = useQueryClient()
  return {
    setHidden: useMutation({
      mutationFn: async (v: { epId: string; hidden: string[] }) => { const r = await jx("PATCH", `/api/model-endpoints/${v.epId}/models`, { hidden: v.hidden }); if (!r.ok) throw new Error("Failed"); return r.json() },
      onSuccess: (_d, v) => { qc.invalidateQueries({ queryKey: ["endpoint-models", v.epId] }); qc.invalidateQueries({ queryKey: ["models"] }) },
    }),
    edit: useMutation({
      mutationFn: async (v: { epId: string; name?: string; base_url?: string; api_key?: string }) => { const { epId, ...rest } = v; const r = await jx("PATCH", `/api/model-endpoints/${epId}`, rest); if (!r.ok) throw new Error("Update failed"); return r.json() },
      onSuccess: () => qc.invalidateQueries({ queryKey: ["models"] }),
    }),
  }
}
export async function probeEndpoint(epId: string): Promise<{ ok?: boolean; models?: string[]; error?: string; status?: string }> {
  const r = await apiFetch(`/api/model-endpoints/${epId}/probe`); return r.json().catch(() => ({ ok: r.ok }))
}

// ───────────────────────── Vault (Bitwarden/Vaultwarden) ─────────────────────────
export interface VaultStatus { server_url: string; email: string; unlocked: boolean; unlocked_at?: string; bw_installed: boolean }
export function useVaultConfig() {
  return useQuery({ queryKey: ["vault-config"], retry: false, queryFn: async () => { try { return await apiJson<VaultStatus>("/api/vault/config") } catch { return null } } })
}
export function useVaultMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["vault-config"] })
  const call = async (path: string, body?: unknown) => { const r = await jx("POST", path, body); const d = await r.json().catch(() => ({})); inv(); return { ok: r.ok && d.ok !== false, error: d.error || d.detail } }
  return {
    saveConfig: (server_url: string, email: string) => call("/api/vault/config", { server_url, email }),
    login: (email: string, master_password: string) => call("/api/vault/login", { email, master_password }),
    unlock: (master_password: string) => call("/api/vault/unlock", { master_password }),
    lock: () => call("/api/vault/lock"),
    logout: () => call("/api/vault/logout"),
  }
}

// ───────────────────────── System logs ─────────────────────────
export function useSystemLogs(limit: number, enabled: boolean) {
  return useQuery({
    queryKey: ["sys-logs", limit],
    enabled,
    retry: false,
    refetchInterval: enabled ? 5000 : false,
    queryFn: async () => { try { return (await apiJson<{ logs: string[] }>(`/api/diagnostics/logs?limit=${limit}`)).logs } catch { return [] } },
  })
}

// ───────────────────────── Preset templates ─────────────────────────
export interface PresetTemplate { id: string; name: string; description?: string }
export function usePresetTemplates() {
  return useQuery({ queryKey: ["preset-templates"], retry: false, queryFn: async () => { try { const r = await apiJson<{ templates?: PresetTemplate[] } | PresetTemplate[]>("/api/presets/templates"); return Array.isArray(r) ? r : (r.templates || []) } catch { return [] } } })
}
export function usePresetTemplateMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["preset-templates"] })
  return {
    create: useMutation({ mutationFn: async (v: { name: string; description?: string; system_prompt?: string }) => { const r = await jx("POST", "/api/presets/templates", v); if (!r.ok) throw new Error("Create failed"); return r.json() }, onSuccess: inv }),
    remove: useMutation({ mutationFn: async (id: string) => { const r = await jx("DELETE", `/api/presets/templates/${id}`); if (!r.ok) throw new Error("Delete failed"); return r.json() }, onSuccess: inv }),
  }
}

// ───────────────────────── Admin: toggle user admin ─────────────────────────
export function useSetUserAdmin() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (v: { username: string; is_admin: boolean }) => { const r = await jx("PUT", `/api/auth/users/${encodeURIComponent(v.username)}/admin`, { is_admin: v.is_admin }); if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Failed"); return r.json() },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  })
}
