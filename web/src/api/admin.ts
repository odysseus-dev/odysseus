import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiFetch, apiJson } from "@/lib/api"

export interface McpServer {
  id: string; name: string; transport: string; command?: string; url?: string;
  args?: string[]; env?: Record<string, string>;
  is_enabled?: boolean; status?: string; tool_count?: number; error?: string;
  needs_oauth?: boolean; has_oauth?: boolean;
}
export interface Webhook {
  id: string; name: string; url: string; events: string[]; is_active?: boolean;
  has_secret?: boolean; last_status_code?: number; last_error?: string;
}

// Admin-gated lists. A 403 means the caller isn't admin → {admin:false}.
async function adminList<T>(path: string): Promise<{ items: T[]; admin: boolean }> {
  const r = await apiFetch(path)
  if (r.status === 403) return { items: [], admin: false }
  if (!r.ok) return { items: [], admin: true }
  return { items: (await r.json()) as T[], admin: true }
}

export function useFeatures() {
  return useQuery({ queryKey: ["features"], retry: false, queryFn: () => apiJson<Record<string, boolean>>("/api/auth/features") })
}
export function useSetFeature() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (v: { key: string; value: boolean }) => {
      const r = await apiFetch("/api/auth/features", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ [v.key]: v.value }) })
      if (!r.ok) throw new Error("save failed"); return r.json()
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["features"] }),
  })
}

export function useMcpServers() {
  return useQuery({ queryKey: ["mcp-servers"], retry: false, queryFn: () => adminList<McpServer>("/api/mcp/servers") })
}
export function useWebhooks() {
  return useQuery({ queryKey: ["webhooks"], retry: false, queryFn: () => adminList<Webhook>("/api/webhooks") })
}

export function useAdminMutations() {
  const qc = useQueryClient()
  const invMcp = () => qc.invalidateQueries({ queryKey: ["mcp-servers"] })
  const invHook = () => qc.invalidateQueries({ queryKey: ["webhooks"] })
  const form = (path: string, fields: Record<string, string>) => {
    const fd = new FormData(); Object.entries(fields).forEach(([k, v]) => fd.set(k, v))
    return apiFetch(path, { method: "POST", body: fd })
  }
  return {
    addServer: useMutation({
      mutationFn: async (v: { name: string; transport: string; command?: string; url?: string; args?: string; env?: string }) => {
        const r = await form("/api/mcp/servers", { name: v.name, transport: v.transport, command: v.command || "", url: v.url || "", args: v.args || "[]", env: v.env || "{}" })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Add failed") }
        return r.json()
      }, onSuccess: invMcp,
    }),
    reconnectServer: useMutation({ mutationFn: async (id: string) => { const r = await apiFetch(`/api/mcp/servers/${id}/reconnect`, { method: "POST" }); if (!r.ok) throw new Error("Couldn't reconnect the server") }, onSuccess: invMcp }),
    // Enable/disable an MCP server. Backend: PATCH /api/mcp/servers/{id} (form is_enabled).
    toggleServer: useMutation({
      mutationFn: async (v: { id: string; is_enabled: boolean }) => {
        const fd = new FormData(); fd.set("is_enabled", String(v.is_enabled))
        const r = await apiFetch(`/api/mcp/servers/${v.id}`, { method: "PATCH", body: fd })
        if (!r.ok) throw new Error("Couldn't update the server")
      }, onSuccess: invMcp,
    }),
    removeServer: useMutation({ mutationFn: async (id: string) => { const r = await apiFetch(`/api/mcp/servers/${id}`, { method: "DELETE" }); if (!r.ok) throw new Error("Couldn't remove the server") }, onSuccess: invMcp }),
    addWebhook: useMutation({
      mutationFn: async (v: { name: string; url: string; events: string; secret?: string }) => {
        const r = await form("/api/webhooks", { name: v.name, url: v.url, events: v.events, secret: v.secret || "" })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Add failed") }
        return r.json()
      }, onSuccess: invHook,
    }),
    testWebhook: useMutation({ mutationFn: async (id: string) => { const r = await apiFetch(`/api/webhooks/${id}/test`, { method: "POST" }); return r.json().catch(() => ({})) } }),
    toggleWebhook: useMutation({
      mutationFn: async (v: { id: string; is_active: boolean }) => {
        const r = await apiFetch(`/api/webhooks/${v.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ is_active: v.is_active }) })
        if (!r.ok) throw new Error("Couldn't update the webhook")
      }, onSuccess: invHook,
    }),
    removeWebhook: useMutation({ mutationFn: async (id: string) => { const r = await apiFetch(`/api/webhooks/${id}`, { method: "DELETE" }); if (!r.ok) throw new Error("Couldn't remove the webhook") }, onSuccess: invHook }),
  }
}
