import { useCallback, useRef, useState } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"

const jx = (method: string, path: string, body?: unknown) =>
  apiFetch(path, { method, headers: { "Content-Type": "application/json" }, body: body !== undefined ? JSON.stringify(body) : undefined })

// ───────────────────────── Device-flow OAuth (Copilot / ChatGPT) ─────────────────────────
// Mirrors the chat composer's `/setup copilot|chatgpt-subscription` flow but as
// a reusable hook for the Settings UI. Endpoints (admin-only, see
// routes/device_flow.py + copilot_routes.py + chatgpt_subscription_routes.py):
//   POST {base}/device/start  → { poll_id, user_code, verification_uri, verification_uri_complete?, interval, expires_in }
//   POST {base}/device/poll   (form poll_id) → { status: pending|authorized|failed, endpoint?, error?, detail? }
//   POST {base}/device/cancel (form poll_id)
export interface DeviceFlowProvider { label: string; base: string }
export const COPILOT_PROVIDER: DeviceFlowProvider = { label: "GitHub Copilot", base: "/api/copilot" }
export const CHATGPT_PROVIDER: DeviceFlowProvider = { label: "ChatGPT Subscription", base: "/api/chatgpt-subscription" }

interface DeviceStart {
  poll_id?: string; user_code?: string; verification_uri?: string
  verification_uri_complete?: string; interval?: number; expires_in?: number; detail?: string
}
interface DevicePoll {
  status?: string; endpoint?: { name?: string; models?: string[] }; error?: string; detail?: string
}
export interface DeviceFlowState {
  active: boolean
  userCode?: string
  verifyUrl?: string
  message?: string
  error?: string
  done?: boolean
}

export function useDeviceFlow(provider: DeviceFlowProvider) {
  const qc = useQueryClient()
  const [state, setState] = useState<DeviceFlowState>({ active: false })
  const cancelRef = useRef(false)
  const pollIdRef = useRef<string | null>(null)

  const reset = useCallback(() => { cancelRef.current = true; pollIdRef.current = null; setState({ active: false }) }, [])

  const cancel = useCallback(async () => {
    cancelRef.current = true
    const pid = pollIdRef.current
    pollIdRef.current = null
    if (pid) { const fd = new FormData(); fd.set("poll_id", pid); apiFetch(`${provider.base}/device/cancel`, { method: "POST", body: fd }).catch(() => {}) }
    setState({ active: false })
  }, [provider.base])

  const start = useCallback(async () => {
    cancelRef.current = false
    setState({ active: true, message: "Requesting device code…" })
    let started: DeviceStart
    try {
      const res = await apiFetch(`${provider.base}/device/start`, { method: "POST", body: new FormData() })
      started = (await res.json().catch(() => ({}))) as DeviceStart
      if (!res.ok || !started.poll_id) {
        const msg = res.status === 403 ? "Connecting provider accounts is admin-only on this instance." : (started.detail || `${provider.label} authorization could not start.`)
        setState({ active: false, error: msg })
        return
      }
    } catch {
      setState({ active: false, error: `${provider.label} authorization could not start.` })
      return
    }
    pollIdRef.current = started.poll_id
    const authUrl = started.verification_uri_complete || started.verification_uri || ""
    if (authUrl) window.open(authUrl, "_blank", "noopener")
    setState({ active: true, userCode: started.user_code, verifyUrl: authUrl, message: "Waiting for you to approve in the opened tab…" })

    const intervalMs = Math.max(2, started.interval || 5) * 1000
    const deadline = Date.now() + Math.max(30, started.expires_in || 900) * 1000
    while (Date.now() < deadline) {
      if (cancelRef.current) return
      await new Promise((r) => window.setTimeout(r, intervalMs))
      if (cancelRef.current) return
      const fd = new FormData()
      fd.set("poll_id", started.poll_id)
      let data: DevicePoll
      try {
        const pollRes = await apiFetch(`${provider.base}/device/poll`, { method: "POST", body: fd })
        data = (await pollRes.json().catch(() => ({}))) as DevicePoll
        if (!pollRes.ok) {
          const msg = pollRes.status === 403 ? "Connecting provider accounts is admin-only on this instance." : (data.detail || `${provider.label} authorization failed.`)
          setState({ active: false, error: msg })
          return
        }
      } catch { continue }
      if (data.status === "authorized") {
        pollIdRef.current = null
        qc.invalidateQueries({ queryKey: ["models"] })
        qc.invalidateQueries({ queryKey: ["default-chat"] })
        const n = data.endpoint?.models?.length
        setState({ active: false, done: true, message: `${provider.label} connected${n ? ` · ${n} models` : ""}.` })
        return
      }
      if (data.status === "failed") {
        pollIdRef.current = null
        setState({ active: false, error: `${provider.label} authorization failed: ${data.error || "denied"}.` })
        return
      }
    }
    pollIdRef.current = null
    setState({ active: false, error: `${provider.label} authorization expired. Try again.` })
  }, [provider.base, provider.label, qc])

  return { state, start, cancel, reset }
}

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
export interface PresetTemplate { id: string; name: string; description?: string; system_prompt?: string; temperature?: number; max_tokens?: number }
export function usePresetTemplates() {
  return useQuery({ queryKey: ["preset-templates"], retry: false, queryFn: async () => { try { const r = await apiJson<{ templates?: PresetTemplate[] } | PresetTemplate[]>("/api/presets/templates"); return Array.isArray(r) ? r : (r.templates || []) } catch { return [] } } })
}
export function usePresetTemplateMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["preset-templates"] })
  return {
    create: useMutation({ mutationFn: async (v: { id?: string; name: string; description?: string; system_prompt?: string; temperature?: number; max_tokens?: number }) => { const r = await jx("POST", "/api/presets/templates", v); if (!r.ok) throw new Error("Create failed"); return r.json() }, onSuccess: inv }),
    remove: useMutation({ mutationFn: async (id: string) => { const r = await jx("DELETE", `/api/presets/templates/${id}`); if (!r.ok) throw new Error("Delete failed"); return r.json() }, onSuccess: inv }),
    expand: useMutation({ mutationFn: async (v: { name?: string; prompt?: string; model?: string }) => { const r = await jx("POST", "/api/presets/expand", v); if (!r.ok) throw new Error("Expand failed"); return r.json() as Promise<{ success?: boolean; prompt?: string; message?: string }> } }),
  }
}

// ───────────────────────── Provider presets (add-endpoint) ─────────────────────────
export interface Provider { provider: string; items?: { url: string; models?: string[] }[] }
export function useProviders() {
  return useQuery({ queryKey: ["providers"], retry: false, queryFn: async () => { try { return (await apiJson<{ providers: Provider[] }>("/api/providers")).providers } catch { return [] } } })
}

// ───────────────────────── Preset groups ─────────────────────────
export interface PresetGroupParticipant {
  modelId?: string
  modelDisplay?: string
  characterId?: string | null
  characterName?: string | null
  characterPrompt?: string | null
}
export interface PresetGroup {
  id?: string
  name?: string
  mode?: "parallel" | "round-robin" | string
  participants?: PresetGroupParticipant[]
  [k: string]: unknown
}
export function usePresetGroups() {
  return useQuery({ queryKey: ["preset-groups"], retry: false, queryFn: async () => { try { return (await apiJson<{ groups: PresetGroup[] }>("/api/presets/groups")).groups || [] } catch { return [] } } })
}
export function useSavePresetGroups() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: async (groups: PresetGroup[]) => { const r = await jx("POST", "/api/presets/groups", { groups }); if (!r.ok) throw new Error("Save failed"); return r.json() }, onSuccess: () => qc.invalidateQueries({ queryKey: ["preset-groups"] }) })
}

// ───────────────────────── Admin: toggle user admin ─────────────────────────
export function useSetUserAdmin() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (v: { username: string; is_admin: boolean }) => { const r = await jx("PUT", `/api/auth/users/${encodeURIComponent(v.username)}/admin`, { is_admin: v.is_admin }); if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Failed"); return r.json() },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  })
}
